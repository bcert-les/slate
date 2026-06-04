"""
Download parsed evidence data from the Investigation Hub.

Streams evidence rows to SQLite with deduplication, checkpointing, and resume.
Also supports CSV/JSON output for smaller datasets.

Run from repository root:
  python workflows/investigation_hub/download_evidence.py <investigation_id> --list
  python workflows/investigation_hub/download_evidence.py <investigation_id> processes
  python workflows/investigation_hub/download_evidence.py <investigation_id> processes --format csv
  python workflows/investigation_hub/download_evidence.py <investigation_id> tcp_table --format all
"""
import csv
import json
import logging
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY = 0.1
SQLITE_MAX_INT = 2**63 - 1

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

_LOG = logging.getLogger("updraft")


def _setup_log(path) -> None:
    """Attach a file handler to the 'updraft' logger.

    path=None  → no-op
    path=True  → auto-generate timestamped file under output/logs/
    path=str   → write to that path
    """
    if not path:
        return
    if path is True:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        script = os.path.splitext(os.path.basename(__file__))[0]
        path = os.path.join(_root, "output", "logs", f"{script}_{ts}.log")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    _handler = logging.FileHandler(path, encoding="utf-8")
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _LOG.setLevel(logging.DEBUG)
    _LOG.addHandler(_handler)
    _LOG.info("Log started  script=%s  args=%s", os.path.basename(__file__), sys.argv[1:])
    print(f"  Logging to: {path}", flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def load_config():
    load_dotenv()
    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")
    if not air_host or not api_token:
        print("Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env", file=sys.stderr)
        sys.exit(1)
    return air_host.rstrip("/"), api_token


def _headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, retries=_MAX_RETRIES, **kwargs):
    backoff = _INITIAL_BACKOFF
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                if not resp.ok:
                    _LOG.warning("HTTP %s  url=%s  body=%s", resp.status_code, url, resp.text[:500])
                return resp
            if attempt == retries:
                _LOG.error("HTTP %s after %d attempts  url=%s  body=%s",
                           resp.status_code, retries + 1, url, resp.text[:500])
                return resp
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            _LOG.warning("HTTP %s retrying in %.1fs (attempt %d/%d)  url=%s",
                         resp.status_code, wait, attempt + 1, retries + 1, url)
            print(f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(wait)
            backoff *= _BACKOFF_FACTOR
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                _LOG.error("Connection failed after %d attempts  url=%s",
                           retries + 1, url, exc_info=True)
                raise
            _LOG.warning("Connection error (attempt %d/%d)  url=%s  error=%s",
                         attempt + 1, retries + 1, url, exc)
            print(f"\n  Connection error, retrying in {backoff:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(backoff)
            backoff *= _BACKOFF_FACTOR
    raise last_exc


def api_get(air_host, api_token, path, params=None, timeout=_DEFAULT_TIMEOUT,
            retries=_MAX_RETRIES, extra_headers=None):
    url = f"{air_host}{path}"
    headers = dict(_headers(api_token))
    if extra_headers:
        headers.update(extra_headers)
    return _request_with_retry(
        requests.get, url,
        headers=headers, params=params, timeout=timeout,
        retries=retries,
    )


def api_post(air_host, api_token, path, body=None, params=None,
             timeout=_DEFAULT_TIMEOUT, retries=_MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.post, url,
        headers=_headers(api_token), json=body or {}, params=params, timeout=timeout,
        retries=retries,
    )


# ---------------------------------------------------------------------------
# Investigation Hub helpers
# ---------------------------------------------------------------------------

def get_investigation_assets(air_host: str, api_token: str, investigation_id: str) -> list:
    """GET /investigation-hub/investigations/{id}/assets"""
    resp = api_get(
        air_host, api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/assets",
    )
    if not resp.ok:
        return []
    return resp.json().get("result", [])


def build_endpoint_name_map(assets_data: list) -> Dict[str, str]:
    """Build a mapping from assignment/endpoint IDs to human-readable hostnames."""
    id_to_name: Dict[str, str] = {}
    for platform_group in assets_data:
        for asset in platform_group.get("assets", []):
            eid = asset.get("_id")
            ename = asset.get("name", "Unknown")
            if eid:
                id_to_name[eid] = ename
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid:
                    id_to_name[aid] = ename
    return id_to_name


def list_available_sections(sections_data: list) -> List[Tuple[str, str, int]]:
    """Return (platform, name, count) tuples for sections that have data."""
    available: List[Tuple[str, str, int]] = []
    for pg in sections_data:
        platform = pg.get("platform", "?")
        for tg in pg.get("types", []):
            for s in tg.get("sections", []):
                count = s.get("count", 0)
                if count > 0:
                    available.append((platform, s.get("name"), count))
    return sorted(available, key=lambda x: (-x[2], x[0], x[1]))


# ---------------------------------------------------------------------------
# SQLite streaming writer
# ---------------------------------------------------------------------------

class SqliteEvidenceWriter:
    """
    Streams evidence rows into SQLite with deduplication, checkpointing,
    and an ingested_at timestamp.
    """

    def __init__(self, db_path: str, table_name: str) -> None:
        self.db_path = db_path
        self.table_name = table_name
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.cur = self.conn.cursor()
        self.columns: Optional[List[str]] = None
        self.col_types: Optional[Dict[str, str]] = None
        self.insert_sql: Optional[str] = None
        self.rows_written = 0
        self._ensure_checkpoint_table()

    def _ensure_checkpoint_table(self) -> None:
        self.cur.execute(
            'CREATE TABLE IF NOT EXISTS _checkpoints ('
            '  table_name TEXT PRIMARY KEY,'
            '  investigation_id TEXT,'
            '  last_skip INTEGER,'
            '  total_count INTEGER,'
            '  updated_at TEXT'
            ')'
        )
        self.conn.commit()

    def get_checkpoint(self, investigation_id: str) -> int:
        row = self.cur.execute(
            'SELECT last_skip FROM _checkpoints WHERE table_name = ? AND investigation_id = ?',
            (self.table_name, investigation_id),
        ).fetchone()
        return row[0] if row else 0

    def save_checkpoint(self, investigation_id: str, skip: int, total_count: int) -> None:
        self.cur.execute(
            'INSERT OR REPLACE INTO _checkpoints '
            '(table_name, investigation_id, last_skip, total_count, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (self.table_name, investigation_id, skip, total_count,
             datetime.now(timezone.utc).isoformat()),
        )

    def _infer_col_type(self, values: list) -> str:
        for val in values:
            if val is None or isinstance(val, (dict, list)):
                continue
            if isinstance(val, bool):
                return "INTEGER"
            if isinstance(val, int):
                return "TEXT" if abs(val) > SQLITE_MAX_INT else "INTEGER"
            if isinstance(val, float):
                return "REAL"
            return "TEXT"
        return "TEXT"

    def _ensure_table(self, sample_rows: list) -> None:
        all_columns: List[str] = list(sample_rows[0].keys())
        seen = set(all_columns)
        for row in sample_rows[1:]:
            for k in row:
                if k not in seen:
                    seen.add(k)
                    all_columns.append(k)

        for extra in ("air_endpoint_name", "ingested_at"):
            if extra not in seen:
                seen.add(extra)
                all_columns.append(extra)

        col_types: Dict[str, str] = {}
        for col in all_columns:
            if col == "ingested_at":
                col_types[col] = "TEXT"
            else:
                sample = [row.get(col) for row in sample_rows[:100]]
                col_types[col] = self._infer_col_type(sample)

        col_defs = ", ".join(f'"{c}" {col_types[c]}' for c in all_columns)
        self.cur.execute(f'CREATE TABLE IF NOT EXISTS "{self.table_name}" ({col_defs})')

        self.cur.execute(f'PRAGMA table_info("{self.table_name}")')
        existing = {r[1] for r in self.cur.fetchall()}
        for col in all_columns:
            if col not in existing:
                self.cur.execute(
                    f'ALTER TABLE "{self.table_name}" ADD COLUMN "{col}" {col_types[col]}'
                )

        if "air_id" in seen and "air_task_assignment_id" in seen:
            self.cur.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS '
                f'"uq_{self.table_name}_dedup" '
                f'ON "{self.table_name}" ("air_id", "air_task_assignment_id")'
            )

        for idx_col in ("air_endpoint_name", "name", "air_endpoint_id", "ingested_at"):
            if idx_col in seen:
                self.cur.execute(
                    f'CREATE INDEX IF NOT EXISTS '
                    f'"idx_{self.table_name}_{idx_col}" '
                    f'ON "{self.table_name}" ("{idx_col}")'
                )

        self.conn.commit()
        self.columns = all_columns
        self.col_types = col_types

        placeholders = ", ".join("?" for _ in all_columns)
        col_names = ", ".join(f'"{c}"' for c in all_columns)
        self.insert_sql = (
            f'INSERT OR IGNORE INTO "{self.table_name}" ({col_names}) VALUES ({placeholders})'
        )

    def write_batch(
        self,
        rows: list,
        endpoint_name_map: Dict[str, str],
        investigation_id: str,
        total_count: int,
    ) -> int:
        if not rows:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            aid = row.get("air_task_assignment_id", "")
            eid = row.get("air_endpoint_id", "")
            row["air_endpoint_name"] = (
                endpoint_name_map.get(aid) or endpoint_name_map.get(eid) or "Unknown"
            )
            row["ingested_at"] = now

        if self.columns is None:
            self._ensure_table(rows)

        new_cols = set()
        for row in rows:
            for k in row:
                if k not in set(self.columns):  # type: ignore[arg-type]
                    new_cols.add(k)
        if new_cols:
            self._ensure_table(rows)

        batch = []
        for row in rows:
            values = []
            for col in self.columns:  # type: ignore[union-attr]
                val = row.get(col)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val)
                elif isinstance(val, int) and abs(val) > SQLITE_MAX_INT:
                    val = str(val)
                values.append(val)
            batch.append(values)

        self.cur.executemany(self.insert_sql, batch)
        self.conn.commit()

        inserted = self.cur.execute("SELECT changes()").fetchone()[0]
        self.rows_written += inserted

        skip = rows[-1].get("_page_skip", 0) if rows else 0
        self.save_checkpoint(investigation_id, skip + len(rows), total_count)
        self.conn.commit()

        return inserted

    def total_rows(self) -> int:
        return self.cur.execute(
            f'SELECT COUNT(*) FROM "{self.table_name}"'
        ).fetchone()[0]

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Streaming download
# ---------------------------------------------------------------------------

def stream_evidence_data(
    air_host: str,
    api_token: str,
    investigation_id: str,
    platform: str,
    evidence_category: str,
    assignment_ids: List[str],
    endpoint_name_map: Dict[str, str],
    db_path: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    limit: Optional[int] = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    resume_skip: int = 0,
    table_name: Optional[str] = None,
):
    """
    Download evidence data page-by-page, writing each page directly to SQLite.
    Memory usage is O(page_size). Supports resuming from a checkpoint offset.
    Returns (writer, total_downloaded, sample_rows).
    """
    if table_name is None:
        table_name = evidence_category.replace("/", "_").replace("\\", "_")
    writer = SqliteEvidenceWriter(db_path, table_name)

    skip = resume_skip
    total = None
    downloaded = 0
    sample_rows: list = []

    while True:
        take = page_size
        if limit is not None:
            remaining = limit - downloaded
            if remaining <= 0:
                break
            take = min(take, remaining)

        body = {
            "globalFilter": {"assignmentIds": assignment_ids},
            "filter": [],
            "skip": skip,
            "take": take,
        }

        resp = api_post(
            air_host, api_token,
            f"/api/public/investigation-hub/investigations/{investigation_id}"
            f"/platform/{platform}/evidence-category/{evidence_category}",
            body=body,
            timeout=60,
        )

        if not resp.ok:
            print(f"\n  API error at skip={skip}: {resp.status_code} - {resp.text[:200]}")
            break

        result = resp.json().get("result", {})
        entities = result.get("entities", [])

        if total is None:
            total = result.get("totalCount", 0)
            effective = min(total, limit) if limit else total
            suffix = f" (downloading {limit})" if limit and limit < total else ""
            print(f"  Total records available: {total}{suffix}")
            if resume_skip > 0:
                print(f"  Resuming from checkpoint at offset {resume_skip}")

        if not entities:
            break

        for row in entities:
            row["_page_skip"] = skip

        if not sample_rows:
            sample_rows = entities[:5]

        inserted = writer.write_batch(entities, endpoint_name_map, investigation_id, total)
        downloaded += len(entities)
        skip += len(entities)

        dup_note = ""
        if inserted < len(entities):
            dup_note = f" ({len(entities) - inserted} duplicates skipped)"

        effective = min(total, limit) if limit else total
        print(f"  {downloaded}/{effective} rows...{dup_note}    ", end="\r", flush=True)

        if skip >= total:
            break

        if request_delay > 0:
            time.sleep(request_delay)

    print(f"  {downloaded}/{total or 0} rows downloaded, "
          f"{writer.rows_written} new rows written.    ")

    for row in sample_rows:
        row.pop("_page_skip", None)

    return writer, downloaded, sample_rows


# ---------------------------------------------------------------------------
# In-memory download (for CSV/JSON output)
# ---------------------------------------------------------------------------

def get_evidence_data_inmemory(
    air_host: str,
    api_token: str,
    investigation_id: str,
    platform: str,
    evidence_category: str,
    assignment_ids: List[str],
    endpoint_name_map: Dict[str, str],
    page_size: int = DEFAULT_PAGE_SIZE,
    limit: Optional[int] = None,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> list:
    """In-memory download for CSV/JSON output. Enriches rows before returning."""
    all_rows: list = []
    skip = 0
    total = None

    while True:
        take = page_size
        if limit is not None:
            remaining = limit - len(all_rows)
            if remaining <= 0:
                break
            take = min(take, remaining)

        body = {
            "globalFilter": {"assignmentIds": assignment_ids},
            "filter": [],
            "skip": skip,
            "take": take,
        }

        resp = api_post(
            air_host, api_token,
            f"/api/public/investigation-hub/investigations/{investigation_id}"
            f"/platform/{platform}/evidence-category/{evidence_category}",
            body=body,
            timeout=60,
        )

        if not resp.ok:
            print(f"\n  API error at skip={skip}: {resp.status_code} - {resp.text[:200]}")
            break

        result = resp.json().get("result", {})
        entities = result.get("entities", [])
        if total is None:
            total = result.get("totalCount", 0)
            effective = min(total, limit) if limit else total
            suffix = f" (downloading {limit})" if limit and limit < total else ""
            print(f"  Total records available: {total}{suffix}")

        if not entities:
            break

        for row in entities:
            aid = row.get("air_task_assignment_id", "")
            eid = row.get("air_endpoint_id", "")
            row["air_endpoint_name"] = (
                endpoint_name_map.get(aid) or endpoint_name_map.get(eid) or "Unknown"
            )

        all_rows.extend(entities)
        skip += len(entities)
        print(f"  {len(all_rows)}/{effective} rows...", end="\r", flush=True)

        if skip >= total:
            break
        if request_delay > 0:
            time.sleep(request_delay)

    print(f"  Downloaded {len(all_rows)}/{total or 0} rows.    ")
    return all_rows


# ---------------------------------------------------------------------------
# CSV / JSON writers
# ---------------------------------------------------------------------------

def save_json(rows: list, filename: str) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"  Saved JSON: {filename} ({len(rows)} rows)")


def save_csv(rows: list, filename: str) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: json.dumps(v) if isinstance(v, (dict, list)) else v
                for k, v in row.items()
            })
    print(f"  Saved CSV:  {filename} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Script logic
# ---------------------------------------------------------------------------

def get_sections(air_host, api_token, investigation_id):
    resp = api_post(
        air_host, api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/sections",
        body={},
    )
    if not resp.ok:
        print(f"  Failed to fetch sections: {resp.status_code}", file=sys.stderr)
        return []
    return resp.json().get("result", [])


def display_summary(evidence_category, investigation_id, platform, rows, sample_rows=None):
    sample = sample_rows or rows[:5]
    print(f"\n{'='*70}")
    print(f"EVIDENCE DATA: {evidence_category}")
    print(f"{'='*70}\n")
    print(f"  Investigation ID: {investigation_id}")
    print(f"  Platform:         {platform}")
    print(f"  Category:         {evidence_category}")

    if rows:
        print(f"  Total rows:       {len(rows)}")
        print(f"  Columns:          {len(rows[0].keys())}")
        print(f"  Column names:     {', '.join(rows[0].keys())}")

    if sample:
        print(f"\n  --- Sample ({len(sample)} rows) ---\n")
        label_fields = ["name", "process_path", "command_line", "source", "destination",
                        "local_address", "remote_address", "path", "key", "value"]
        for i, row in enumerate(sample):
            display = {}
            for field in label_fields:
                if field in row and row[field]:
                    display[field] = row[field]
            if not display:
                display = {k: v for k, v in list(row.items())[:5] if v is not None}
            print(f"  [{i+1}] {json.dumps(display, default=str)}")


def print_usage():
    print("Usage: python workflows/investigation_hub/download_evidence.py <investigation_id> <evidence_category> [options]")
    print()
    print("Arguments:")
    print("  investigation_id     Investigation UUID")
    print("  evidence_category    Evidence section name (e.g. processes, tcp_table)")
    print()
    print("Options:")
    print("  --platform PLATFORM  Platform filter (default: windows)")
    print("  --format FORMAT      Output format: json, csv, sqlite, both, all (default: sqlite)")
    print("  --db PATH            SQLite database path (default: output/evidence.db)")
    print("  --list               List all available evidence sections and exit")
    print("  --limit N            Max rows to download (default: all)")
    print("  --delay SECONDS      Delay between API requests (default: 0.1)")
    print("  --no-resume          Ignore checkpoint, download from scratch")


def parse_args(argv):
    args = {
        "investigation_id": None,
        "evidence_category": None,
        "platform": "windows",
        "format": "sqlite",
        "db_path": None,
        "list_sections": False,
        "limit": None,
        "delay": DEFAULT_REQUEST_DELAY,
        "no_resume": False,
        "log": None,
    }

    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--platform" and i + 1 < len(argv):
            args["platform"] = argv[i + 1]; i += 2
        elif argv[i] == "--format" and i + 1 < len(argv):
            args["format"] = argv[i + 1]; i += 2
        elif argv[i] == "--db" and i + 1 < len(argv):
            args["db_path"] = argv[i + 1]; i += 2
        elif argv[i] == "--limit" and i + 1 < len(argv):
            args["limit"] = int(argv[i + 1]); i += 2
        elif argv[i] == "--delay" and i + 1 < len(argv):
            args["delay"] = float(argv[i + 1]); i += 2
        elif argv[i] == "--no-resume":
            args["no_resume"] = True; i += 1
        elif argv[i] == "--list":
            args["list_sections"] = True; i += 1
        elif argv[i] == "--log" and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            args["log"] = argv[i + 1]; i += 2
        elif argv[i] == "--log":
            args["log"] = True; i += 1
        elif argv[i] in ("--help", "-h"):
            print_usage(); sys.exit(0)
        else:
            positional.append(argv[i]); i += 1

    if len(positional) >= 1:
        args["investigation_id"] = positional[0]
    if len(positional) >= 2:
        args["evidence_category"] = positional[1]

    return args


def main():
    air_host, api_token = load_config()
    args = parse_args(sys.argv[1:])
    _setup_log(args["log"])
    _LOG.info("=== download_evidence started  host=%s", air_host)

    if not args["investigation_id"]:
        print_usage()
        sys.exit(1)

    investigation_id = args["investigation_id"]
    platform = args["platform"]
    fmt = args["format"]

    print("Fetching investigation assets...", flush=True)
    assets_data = get_investigation_assets(air_host, api_token, investigation_id)
    if not assets_data:
        print("Error: Could not retrieve investigation assets.", file=sys.stderr)
        sys.exit(1)

    assignment_ids = []
    asset_names = {}
    for pg in assets_data:
        if pg.get("platform") != platform:
            continue
        for asset in pg.get("assets", []):
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid:
                    assignment_ids.append(aid)
                    asset_names[aid] = asset.get("name", "Unknown")

    if not assignment_ids:
        all_platforms = [pg.get("platform") for pg in assets_data]
        print(f"Error: No assets found for platform '{platform}'.", file=sys.stderr)
        print(f"Available platforms: {', '.join(all_platforms)}", file=sys.stderr)
        sys.exit(1)

    print(f"  Platform: {platform}  |  Assets: {len(assignment_ids)}")
    endpoint_name_map = build_endpoint_name_map(assets_data)

    if args["list_sections"]:
        print("\nFetching available evidence sections...", flush=True)
        sections_data = get_sections(air_host, api_token, investigation_id)
        available = list_available_sections(sections_data)
        if not available:
            print("  No evidence sections with data found.")
            sys.exit(0)
        print(f"\n{'='*70}\nAVAILABLE EVIDENCE SECTIONS\n{'='*70}\n")
        current_platform = None
        for plat, name, count in available:
            if plat != current_platform:
                current_platform = plat
                print(f"  [{plat}]")
            print(f"    {name:<50} {count:>8} rows")
        print()
        sys.exit(0)

    evidence_category = args["evidence_category"]
    if not evidence_category:
        print("\nError: evidence_category is required (or use --list).", file=sys.stderr)
        print_usage()
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"\nDownloading evidence: {evidence_category} ({platform})...", flush=True)

    if fmt in ("sqlite", "all"):
        db_path = args["db_path"] or os.path.join(OUTPUT_DIR, "evidence.db")
        table_name = evidence_category.replace("/", "_").replace("\\", "_")

        resume_skip = 0
        if not args["no_resume"]:
            tmp_writer = SqliteEvidenceWriter(db_path, table_name)
            resume_skip = tmp_writer.get_checkpoint(investigation_id)
            tmp_writer.close()
            if resume_skip > 0:
                print(f"  Found checkpoint at offset {resume_skip}")

        writer, downloaded, sample_rows = stream_evidence_data(
            air_host, api_token, investigation_id, platform,
            evidence_category, assignment_ids, endpoint_name_map,
            db_path,
            limit=args["limit"],
            request_delay=args["delay"],
            resume_skip=resume_skip,
        )

        if downloaded == 0 and resume_skip == 0:
            writer.close()
            print("\n  No data returned for this evidence category.")
            print("  Use --list to see available sections with data.")
            sys.exit(0)

        total_in_table = writer.total_rows()
        writer.close()

        display_summary(evidence_category, investigation_id, platform, [], sample_rows)
        print(f"\n  SQLite: {db_path} -> table '{table_name}' ({total_in_table} total rows)")

    if fmt in ("json", "csv", "both", "all"):
        if fmt == "all":
            print("\n  Downloading again for CSV/JSON export...", flush=True)

        rows = get_evidence_data_inmemory(
            air_host, api_token, investigation_id, platform,
            evidence_category, assignment_ids, endpoint_name_map,
            limit=args["limit"],
            request_delay=args["delay"],
        )

        if not rows:
            if fmt != "all":
                print("\n  No data returned for this evidence category.")
            sys.exit(0)

        if fmt != "all":
            display_summary(evidence_category, investigation_id, platform, rows)

        safe_category = evidence_category.replace("/", "_").replace("\\", "_")
        rows_by_endpoint = {}
        for row in rows:
            ep = row.get("air_endpoint_name", "Unknown")
            rows_by_endpoint.setdefault(ep, []).append(row)

        print(f"\n  Saving files...", flush=True)
        for ep_name, ep_rows in sorted(rows_by_endpoint.items()):
            safe_name = ep_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
            base = os.path.join(OUTPUT_DIR, f"evidence_{safe_category}_{safe_name}")
            if fmt in ("json", "both", "all"):
                save_json(ep_rows, f"{base}.json")
            if fmt in ("csv", "both", "all"):
                save_csv(ep_rows, f"{base}.csv")

    print(f"\nDone.\n")


if __name__ == "__main__":
    main()

"""
Workflow: Windows Process Analysis

Interactive workflow that:
  1. Loads org from .env (BINALYZE_ORG_ID)
  2. Lists open cases and lets you pick one
  3. Downloads all Windows process data to SQLite (streaming)
  4. Prints summary, top-10 and bottom-10 processes by frequency
"""
import json
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ITERATIONS = 1000

DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY = 0.1
SQLITE_MAX_INT = 2**63 - 1

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
PLATFORM = "windows"
EVIDENCE_CATEGORY = "processes"


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
                return resp
            if attempt == retries:
                return resp
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            print(f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(wait)
            backoff *= _BACKOFF_FACTOR
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise
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


def _entity_ids_fingerprint(entities):
    if not entities:
        return ()
    ids = []
    for row in entities:
        if isinstance(row, dict):
            oid = row.get("_id") or row.get("id") or row.get("endpointId")
            if oid is not None:
                ids.append(str(oid))
    return tuple(sorted(ids))


def paginate_get(air_host, api_token, path, params=None, page_size=100, verbose=True):
    base_params = dict(params or {})
    all_entities = []
    page = 1
    seen_pages = set()
    seen_fingerprints = set()

    while len(seen_pages) < _MAX_ITERATIONS:
        if page in seen_pages:
            if verbose:
                print(f"\nDetected loop at page {page}, stopping.")
            break
        seen_pages.add(page)

        request_params = {**base_params, "page": page, "pageSize": page_size}
        if verbose:
            print(f"Fetching page {page}...", end=" ", flush=True)

        resp = api_get(air_host, api_token, path, params=request_params)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        if verbose:
            print("OK")

        data = resp.json()

        result = data.get("result") if isinstance(data, dict) else None
        if result and isinstance(result, dict) and "entities" in result:
            entities = result.get("entities") or []
            if not entities:
                break

            fp = _entity_ids_fingerprint(entities)
            if fp and fp in seen_fingerprints:
                if verbose:
                    print(
                        f"\nDetected repeated entity set on page {page} "
                        f"(API may ignore page cursor); stopping.",
                        file=sys.stderr,
                    )
                break
            if fp:
                seen_fingerprints.add(fp)

            all_entities.extend(entities)

            total_pages = result.get("totalPageCount")
            current_page = result.get("currentPage", page)

            if total_pages and current_page >= total_pages:
                break

            next_page = result.get("nextPage")
            if next_page and next_page != page:
                page = next_page
                continue
            elif total_pages and page < total_pages:
                page += 1
                continue
            else:
                break

        elif isinstance(data, list):
            all_entities.extend(data)
            break
        elif isinstance(data, dict) and "entities" in data:
            all_entities.extend(data["entities"])
            break
        else:
            raise ValueError(
                f"Unexpected response format: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

    return all_entities


# ---------------------------------------------------------------------------
# Investigation Hub / evidence helpers
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
# Workflow
# ---------------------------------------------------------------------------

def load_org_id():
    org_id = os.getenv("BINALYZE_ORG_ID")
    if not org_id:
        print("Set BINALYZE_ORG_ID in .env", file=sys.stderr)
        sys.exit(1)
    return org_id


def fetch_open_cases(air_host, api_token, org_id):
    params = {"filter[organizationIds]": org_id, "filter[status]": "open"}
    return paginate_get(air_host, api_token, "/api/public/cases", params=params, verbose=False)


def select_case(cases):
    if not cases:
        print("No open cases found for this organization.")
        sys.exit(0)

    print(f"\n{'='*70}\nOPEN CASES\n{'='*70}\n")

    for i, case in enumerate(cases, 1):
        name = case.get("name") or case.get("title") or "(untitled)"
        created = (case.get("createdAt") or "")[:10]
        owner = case.get("owner") or "?"
        inv_id = (case.get("metadata") or {}).get("investigationId") or "none"
        print(f"  [{i:>3}]  {name}")
        print(f"         Owner: {owner}  |  Created: {created}  |  Investigation: {inv_id}")

    print()
    while True:
        try:
            choice = input(f"Select case [1-{len(cases)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(cases):
                return cases[idx]
            print(f"  Enter a number between 1 and {len(cases)}.")
        except (ValueError, EOFError):
            print(f"  Enter a number between 1 and {len(cases)}.")


def get_assignment_ids(assets_data, platform):
    ids = []
    for pg in assets_data:
        if pg.get("platform") != platform:
            continue
        for asset in pg.get("assets", []):
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid:
                    ids.append(aid)
    return ids


def print_analysis(db_path, table_name):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total = cur.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    endpoints = cur.execute(
        f'SELECT COUNT(DISTINCT air_endpoint_name) FROM "{table_name}"'
    ).fetchone()[0]
    columns = [row[1] for row in cur.execute(f'PRAGMA table_info("{table_name}")').fetchall()]

    print(f"\n{'='*70}\nPROCESS ANALYSIS SUMMARY\n{'='*70}\n")
    print(f"  Table:      {table_name}")
    print(f"  Database:   {db_path}")
    print(f"  Total rows: {total:,}")
    print(f"  Endpoints:  {endpoints}")
    print(f"  Columns:    {len(columns)}")

    print(f"\n  {'─'*66}")
    print(f"  TOP 10 PROCESSES (highest frequency)")
    print(f"  {'─'*66}\n")
    print(f"  {'#':<5} {'Process Name':<45} {'Count':>8} {'%':>7}")
    print(f"  {'─'*5} {'─'*45} {'─'*8} {'─'*7}")

    top10 = cur.execute(
        f'SELECT name, COUNT(*) as cnt FROM "{table_name}" GROUP BY name ORDER BY cnt DESC LIMIT 10'
    ).fetchall()
    for i, (name, count) in enumerate(top10, 1):
        pct = (count / total * 100) if total else 0
        print(f"  {i:<5} {(name or '(empty)')[:45]:<45} {count:>8,} {pct:>6.1f}%")

    print(f"\n  {'─'*66}")
    print(f"  BOTTOM 10 PROCESSES (lowest frequency — hunting gold)")
    print(f"  {'─'*66}\n")
    print(f"  {'#':<5} {'Process Name':<45} {'Count':>8} {'%':>7}")
    print(f"  {'─'*5} {'─'*45} {'─'*8} {'─'*7}")

    bottom10 = cur.execute(
        f'SELECT name, COUNT(*) as cnt FROM "{table_name}" GROUP BY name ORDER BY cnt ASC LIMIT 10'
    ).fetchall()
    for i, (name, count) in enumerate(bottom10, 1):
        pct = (count / total * 100) if total else 0
        print(f"  {i:<5} {(name or '(empty)')[:45]:<45} {count:>8,} {pct:>6.1f}%")

    unique = cur.execute(f'SELECT COUNT(DISTINCT name) FROM "{table_name}"').fetchone()[0]
    print(f"\n  Unique process names: {unique:,}")
    conn.close()


def main():
    air_host, api_token = load_config()
    org_id = load_org_id()

    print(f"Binalyze AIR Process Analysis Workflow")
    print(f"  Host: {air_host}")
    print(f"  Org:  {org_id}")

    print(f"\nFetching open cases...", flush=True)
    cases = fetch_open_cases(air_host, api_token, org_id)
    selected = select_case(cases)

    case_name = selected.get("name") or selected.get("title") or "unknown"
    investigation_id = (selected.get("metadata") or {}).get("investigationId")

    if not investigation_id:
        print(f"\nError: Selected case has no investigationId.", file=sys.stderr)
        print("This case may not have completed acquisition yet.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Selected: {case_name}")
    print(f"  Investigation ID: {investigation_id}")

    print(f"\nFetching investigation assets...", flush=True)
    assets_data = get_investigation_assets(air_host, api_token, investigation_id)
    if not assets_data:
        print("Error: Could not retrieve investigation assets.", file=sys.stderr)
        sys.exit(1)

    assignment_ids = get_assignment_ids(assets_data, PLATFORM)
    if not assignment_ids:
        all_platforms = [pg.get("platform") for pg in assets_data]
        print(f"Error: No Windows assets found.", file=sys.stderr)
        print(f"Available platforms: {', '.join(all_platforms)}", file=sys.stderr)
        sys.exit(1)

    endpoint_name_map = build_endpoint_name_map(assets_data)
    print(f"  Found {len(assignment_ids)} Windows endpoint(s)")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_case = case_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:30]
    table_name = f"processes_{safe_case}_{timestamp}"
    db_path = os.path.join(OUTPUT_DIR, "evidence.db")

    print(f"\nDownloading Windows processes...", flush=True)
    print(f"  Table: {table_name}")

    writer, downloaded, sample_rows = stream_evidence_data(
        air_host, api_token, investigation_id, PLATFORM,
        EVIDENCE_CATEGORY, assignment_ids, endpoint_name_map,
        db_path,
        page_size=500,
        request_delay=0.1,
        table_name=table_name,
    )

    if downloaded == 0:
        writer.close()
        print("\n  No process data found for this case.")
        sys.exit(0)

    writer.close()
    print_analysis(db_path, table_name)
    print(f"\nDone.\n")


if __name__ == "__main__":
    main()

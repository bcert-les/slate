import os
import sys
import json
import csv
import sqlite3
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from lib.api_client import load_config, api_get, api_post

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY = 0.1

SQLITE_MAX_INT = 2**63 - 1


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_assets(air_host, api_token, investigation_id):
    resp = api_get(
        air_host, api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/assets",
    )
    if not resp.ok:
        print(f"  Failed to fetch assets: {resp.status_code}", file=sys.stderr)
        return []
    return resp.json().get("result", [])


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


def build_endpoint_name_map(assets_data):
    """Build a mapping from assignment/endpoint IDs to human-readable names."""
    id_to_name = {}
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


# ---------------------------------------------------------------------------
# SQLite streaming writer
# ---------------------------------------------------------------------------

class SqliteEvidenceWriter:
    """
    Streams evidence rows into SQLite with deduplication, checkpointing,
    and an ingested_at timestamp.
    """

    def __init__(self, db_path, table_name):
        self.db_path = db_path
        self.table_name = table_name
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.cur = self.conn.cursor()
        self.columns = None
        self.col_types = None
        self.insert_sql = None
        self.rows_written = 0
        self._ensure_checkpoint_table()

    def _ensure_checkpoint_table(self):
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

    def get_checkpoint(self, investigation_id):
        """Return the last successful skip offset, or 0 if none."""
        row = self.cur.execute(
            'SELECT last_skip FROM _checkpoints WHERE table_name = ? AND investigation_id = ?',
            (self.table_name, investigation_id),
        ).fetchone()
        return row[0] if row else 0

    def save_checkpoint(self, investigation_id, skip, total_count):
        self.cur.execute(
            'INSERT OR REPLACE INTO _checkpoints (table_name, investigation_id, last_skip, total_count, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (self.table_name, investigation_id, skip, total_count,
             datetime.now(timezone.utc).isoformat()),
        )

    def _infer_col_type(self, values):
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

    def _ensure_table(self, sample_rows):
        all_columns = list(sample_rows[0].keys())
        seen = set(all_columns)
        for row in sample_rows[1:]:
            for k in row:
                if k not in seen:
                    seen.add(k)
                    all_columns.append(k)

        # Always include enrichment columns
        for extra in ("air_endpoint_name", "ingested_at"):
            if extra not in seen:
                seen.add(extra)
                all_columns.append(extra)

        col_types = {}
        for col in all_columns:
            if col == "ingested_at":
                col_types[col] = "TEXT"
            else:
                sample = [row.get(col) for row in sample_rows[:100]]
                col_types[col] = self._infer_col_type(sample)

        col_defs = ", ".join(f'"{c}" {col_types[c]}' for c in all_columns)
        self.cur.execute(f'CREATE TABLE IF NOT EXISTS "{self.table_name}" ({col_defs})')

        # Add missing columns if table already existed with fewer columns
        self.cur.execute(f'PRAGMA table_info("{self.table_name}")')
        existing = {r[1] for r in self.cur.fetchall()}
        for col in all_columns:
            if col not in existing:
                self.cur.execute(
                    f'ALTER TABLE "{self.table_name}" ADD COLUMN "{col}" {col_types[col]}'
                )

        # Unique index for deduplication
        if "air_id" in seen and "air_task_assignment_id" in seen:
            self.cur.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS '
                f'"uq_{self.table_name}_dedup" '
                f'ON "{self.table_name}" ("air_id", "air_task_assignment_id")'
            )

        # Query indexes
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

    def write_batch(self, rows, endpoint_name_map, investigation_id, total_count):
        """Write a batch of rows to SQLite. Called once per API page."""
        if not rows:
            return 0

        now = datetime.now(timezone.utc).isoformat()

        # Enrich rows
        for row in rows:
            aid = row.get("air_task_assignment_id", "")
            eid = row.get("air_endpoint_id", "")
            row["air_endpoint_name"] = (
                endpoint_name_map.get(aid) or endpoint_name_map.get(eid) or "Unknown"
            )
            row["ingested_at"] = now

        if self.columns is None:
            self._ensure_table(rows)

        # Handle new columns from this batch
        new_cols = set()
        for row in rows:
            for k in row:
                if k not in set(self.columns):
                    new_cols.add(k)
        if new_cols:
            self._ensure_table(rows)

        batch = []
        for row in rows:
            values = []
            for col in self.columns:
                val = row.get(col)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val)
                elif isinstance(val, int) and abs(val) > SQLITE_MAX_INT:
                    val = str(val)
                values.append(val)
            batch.append(values)

        self.cur.executemany(self.insert_sql, batch)
        self.conn.commit()

        inserted = self.cur.execute(
            "SELECT changes()"
        ).fetchone()[0]
        self.rows_written += inserted

        # Checkpoint after each page
        skip = row.get("_page_skip", 0) if rows else 0
        self.save_checkpoint(investigation_id, skip + len(rows), total_count)
        self.conn.commit()

        return inserted

    def total_rows(self):
        return self.cur.execute(
            f'SELECT COUNT(*) FROM "{self.table_name}"'
        ).fetchone()[0]

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Streaming download
# ---------------------------------------------------------------------------

def stream_evidence_data(air_host, api_token, investigation_id, platform,
                         evidence_category, assignment_ids, endpoint_name_map,
                         db_path, page_size=DEFAULT_PAGE_SIZE, limit=None,
                         request_delay=DEFAULT_REQUEST_DELAY, resume_skip=0,
                         table_name=None):
    """
    Download evidence data page-by-page, writing each page directly to SQLite.

    Memory usage is O(page_size) instead of O(total_rows).
    Supports resuming from a checkpoint offset.
    Returns (writer, total_downloaded, sample_rows).
    """
    if table_name is None:
        table_name = evidence_category.replace("/", "_").replace("\\", "_")
    writer = SqliteEvidenceWriter(db_path, table_name)

    skip = resume_skip
    total = None
    downloaded = 0
    sample_rows = []

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
            print(f"\n  API error at skip={skip}: {resp.status_code} - {resp.text[:200]}",
                  file=sys.stderr)
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

        # Tag rows with skip for checkpoint tracking
        for row in entities:
            row["_page_skip"] = skip

        # Keep a small sample for display (only from first page)
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

    # Clean up _page_skip from sample rows
    for row in sample_rows:
        row.pop("_page_skip", None)

    return writer, downloaded, sample_rows


# ---------------------------------------------------------------------------
# CSV / JSON writers (for non-sqlite formats)
# ---------------------------------------------------------------------------

def save_json(rows, filename):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"  Saved JSON: {filename} ({len(rows)} rows)")


def save_csv(rows, filename):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {}
            for k, v in row.items():
                flat[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
            writer.writerow(flat)
    print(f"  Saved CSV:  {filename} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Sections listing
# ---------------------------------------------------------------------------

def list_available_sections(sections_data):
    available = []
    for pg in sections_data:
        platform = pg.get("platform", "?")
        for tg in pg.get("types", []):
            for s in tg.get("sections", []):
                count = s.get("count", 0)
                if count > 0:
                    available.append((platform, s.get("name"), count))
    return sorted(available, key=lambda x: (-x[2], x[0], x[1]))


# ---------------------------------------------------------------------------
# Legacy in-memory download (for CSV/JSON formats without SQLite)
# ---------------------------------------------------------------------------

def get_evidence_data_inmemory(air_host, api_token, investigation_id, platform,
                               evidence_category, assignment_ids, endpoint_name_map,
                               page_size=DEFAULT_PAGE_SIZE, limit=None,
                               request_delay=DEFAULT_REQUEST_DELAY):
    """In-memory download for CSV/JSON output. Enriches rows before returning."""
    all_rows = []
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
            print(f"\n  API error at skip={skip}: {resp.status_code} - {resp.text[:200]}",
                  file=sys.stderr)
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
# CLI
# ---------------------------------------------------------------------------

def print_usage():
    print("Usage: python3 scripts/api_scripts/case_download_evidence.py <investigation_id> <evidence_category> [options]")
    print()
    print("Arguments:")
    print("  investigation_id     Investigation UUID (from enumerate_cases.py)")
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
    print()
    print("Examples:")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> --list")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> processes")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> processes --format csv")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> tcp_table --format all")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> processes --db output/my_case.db")
    print("  python3 scripts/api_scripts/case_download_evidence.py <inv_id> processes --delay 0.5")


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
    }

    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--platform" and i + 1 < len(argv):
            args["platform"] = argv[i + 1]
            i += 2
        elif argv[i] == "--format" and i + 1 < len(argv):
            args["format"] = argv[i + 1]
            i += 2
        elif argv[i] == "--db" and i + 1 < len(argv):
            args["db_path"] = argv[i + 1]
            i += 2
        elif argv[i] == "--limit" and i + 1 < len(argv):
            args["limit"] = int(argv[i + 1])
            i += 2
        elif argv[i] == "--delay" and i + 1 < len(argv):
            args["delay"] = float(argv[i + 1])
            i += 2
        elif argv[i] == "--no-resume":
            args["no_resume"] = True
            i += 1
        elif argv[i] == "--list":
            args["list_sections"] = True
            i += 1
        elif argv[i] in ("--help", "-h"):
            print_usage()
            sys.exit(0)
        else:
            positional.append(argv[i])
            i += 1

    if len(positional) >= 1:
        args["investigation_id"] = positional[0]
    if len(positional) >= 2:
        args["evidence_category"] = positional[1]

    return args


def display_summary(evidence_category, investigation_id, platform, rows, sample_rows=None):
    """Print summary and sample rows."""
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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    air_host, api_token = load_config()
    args = parse_args(sys.argv[1:])

    if not args["investigation_id"]:
        print_usage()
        sys.exit(1)

    investigation_id = args["investigation_id"]
    platform = args["platform"]
    fmt = args["format"]

    # --- Fetch assets ---
    print("Fetching investigation assets...", flush=True)
    assets_data = get_assets(air_host, api_token, investigation_id)
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

    print(f"  Platform: {platform}")
    print(f"  Assets ({len(assignment_ids)}):")
    for aid in assignment_ids:
        print(f"    - {asset_names.get(aid, '?')} ({aid})")

    endpoint_name_map = build_endpoint_name_map(assets_data)

    # --- List sections mode ---
    if args["list_sections"]:
        print("\nFetching available evidence sections...", flush=True)
        sections_data = get_sections(air_host, api_token, investigation_id)
        available = list_available_sections(sections_data)

        if not available:
            print("  No evidence sections with data found.")
            sys.exit(0)

        print(f"\n{'='*70}")
        print("AVAILABLE EVIDENCE SECTIONS")
        print(f"{'='*70}\n")
        current_platform = None
        for plat, name, count in available:
            if plat != current_platform:
                current_platform = plat
                print(f"  [{plat}]")
            print(f"    {name:<50} {count:>8} rows")
        print()
        sys.exit(0)

    # --- Download ---
    evidence_category = args["evidence_category"]
    if not evidence_category:
        print("\nError: evidence_category is required (or use --list to see options).", file=sys.stderr)
        print_usage()
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"\nDownloading evidence: {evidence_category} ({platform})...", flush=True)

    if fmt in ("sqlite", "all"):
        db_path = args["db_path"] or os.path.join(OUTPUT_DIR, "evidence.db")
        table_name = evidence_category.replace("/", "_").replace("\\", "_")

        # Check for resume checkpoint
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
            # Already downloaded via streaming above, re-download for flat files
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

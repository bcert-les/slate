"""
Shared utilities for Investigation Hub evidence downloads.

Provides streaming SQLite writes, deduplication, checkpoint/resume, and in-memory
download helpers. Used by workflows/investigation_hub/ and workflows/process_analysis/.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .api_client import api_get, api_post

DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY = 0.1

SQLITE_MAX_INT = 2**63 - 1


# ---------------------------------------------------------------------------
# Investigation Hub API helpers
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

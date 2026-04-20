"""
Workflow: Windows Process Analysis

Interactive workflow that:
  1. Loads org from .env (BINALYZE_ORG_ID)
  2. Lists open cases and lets you pick one
  3. Downloads all Windows process data to SQLite (streaming)
  4. Prints summary, top-10 and bottom-10 processes by frequency
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from lib.api_client import load_config, api_get
from lib.pagination import paginate_get
from api_scripts.case_download_evidence import (
    get_assets,
    build_endpoint_name_map,
    stream_evidence_data,
    SqliteEvidenceWriter,
    OUTPUT_DIR,
)

PLATFORM = "windows"
EVIDENCE_CATEGORY = "processes"


def load_org_id():
    org_id = os.getenv("BINALYZE_ORG_ID")
    if not org_id:
        print("Set BINALYZE_ORG_ID in .env", file=sys.stderr)
        sys.exit(1)
    return org_id


def fetch_open_cases(air_host, api_token, org_id):
    params = {
        "filter[organizationIds]": org_id,
        "filter[status]": "open",
    }
    return paginate_get(air_host, api_token, "/api/public/cases", params=params,
                        verbose=False)


def select_case(cases):
    """Display interactive menu and return the selected case dict."""
    if not cases:
        print("No open cases found for this organization.")
        sys.exit(0)

    print(f"\n{'='*70}")
    print("OPEN CASES")
    print(f"{'='*70}\n")

    for i, case in enumerate(cases, 1):
        name = case.get("name") or case.get("title") or "(untitled)"
        status = case.get("status", "?")
        created = (case.get("createdAt") or "")[:10]
        owner = case.get("owner") or "?"
        metadata = case.get("metadata") or {}
        inv_id = metadata.get("investigationId") or "none"

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
    """Extract assignment IDs for a given platform from assets data."""
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
    """Query SQLite and print summary + frequency analysis."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total = cur.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    endpoints = cur.execute(
        f'SELECT COUNT(DISTINCT air_endpoint_name) FROM "{table_name}"'
    ).fetchone()[0]
    columns = [row[1] for row in cur.execute(f'PRAGMA table_info("{table_name}")').fetchall()]

    print(f"\n{'='*70}")
    print(f"PROCESS ANALYSIS SUMMARY")
    print(f"{'='*70}\n")
    print(f"  Table:      {table_name}")
    print(f"  Database:   {db_path}")
    print(f"  Total rows: {total:,}")
    print(f"  Endpoints:  {endpoints}")
    print(f"  Columns:    {len(columns)}")

    # Top 10 most frequent processes
    print(f"\n  {'─'*66}")
    print(f"  TOP 10 PROCESSES (highest frequency)")
    print(f"  {'─'*66}\n")
    print(f"  {'#':<5} {'Process Name':<45} {'Count':>8} {'%':>7}")
    print(f"  {'─'*5} {'─'*45} {'─'*8} {'─'*7}")

    top10 = cur.execute(
        f'SELECT name, COUNT(*) as cnt FROM "{table_name}" '
        f'GROUP BY name ORDER BY cnt DESC LIMIT 10'
    ).fetchall()

    for i, (name, count) in enumerate(top10, 1):
        pct = (count / total * 100) if total else 0
        display_name = (name or "(empty)")[:45]
        print(f"  {i:<5} {display_name:<45} {count:>8,} {pct:>6.1f}%")

    # Bottom 10 least frequent processes
    print(f"\n  {'─'*66}")
    print(f"  BOTTOM 10 PROCESSES (lowest frequency)")
    print(f"  {'─'*66}\n")
    print(f"  {'#':<5} {'Process Name':<45} {'Count':>8} {'%':>7}")
    print(f"  {'─'*5} {'─'*45} {'─'*8} {'─'*7}")

    bottom10 = cur.execute(
        f'SELECT name, COUNT(*) as cnt FROM "{table_name}" '
        f'GROUP BY name ORDER BY cnt ASC LIMIT 10'
    ).fetchall()

    for i, (name, count) in enumerate(bottom10, 1):
        pct = (count / total * 100) if total else 0
        display_name = (name or "(empty)")[:45]
        print(f"  {i:<5} {display_name:<45} {count:>8,} {pct:>6.1f}%")

    # Unique process count
    unique = cur.execute(
        f'SELECT COUNT(DISTINCT name) FROM "{table_name}"'
    ).fetchone()[0]
    print(f"\n  Unique process names: {unique:,}")

    conn.close()


def main():
    air_host, api_token = load_config()
    org_id = load_org_id()

    print(f"Binalyze AIR Process Analysis Workflow")
    print(f"  Host: {air_host}")
    print(f"  Org:  {org_id}")

    # Step 1: Fetch and select case
    print(f"\nFetching open cases...", flush=True)
    cases = fetch_open_cases(air_host, api_token, org_id)
    selected = select_case(cases)

    case_name = selected.get("name") or selected.get("title") or "unknown"
    metadata = selected.get("metadata") or {}
    investigation_id = metadata.get("investigationId")

    if not investigation_id:
        print(f"\nError: Selected case has no investigationId.", file=sys.stderr)
        print("This case may not have completed acquisition yet.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Selected: {case_name}")
    print(f"  Investigation ID: {investigation_id}")

    # Step 2: Fetch assets
    print(f"\nFetching investigation assets...", flush=True)
    assets_data = get_assets(air_host, api_token, investigation_id)
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

    # Step 3: Download processes (one table per execution)
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

    # Step 4: Analyze
    print_analysis(db_path, table_name)
    print(f"\nDone.\n")


if __name__ == "__main__":
    main()

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
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_post, load_config
from lib.binalyze_evidence import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_REQUEST_DELAY,
    SqliteEvidenceWriter,
    build_endpoint_name_map,
    get_evidence_data_inmemory,
    get_investigation_assets,
    list_available_sections,
    save_csv,
    save_json,
    stream_evidence_data,
)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


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

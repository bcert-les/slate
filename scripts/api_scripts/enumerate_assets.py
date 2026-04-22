"""
List all assets (endpoints) for an organization via GET /api/public/assets.

Writes JSON (full API objects) and CSV (union of top-level keys; nested
values as JSON strings in cells).
"""
import argparse
import csv
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))

from lib.api_client import load_config
from lib.pagination import paginate_get


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


def _cell_csv(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_json(path, org_id, assets):
    payload = {
        "organizationId": org_id,
        "count": len(assets),
        "assets": assets,
    }
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def write_csv(path, assets):
    _ensure_parent_dir(path)
    if not assets:
        with open(path, "w", encoding="utf-8", newline="") as f:
            pass
        return

    keys = set()
    for row in assets:
        if isinstance(row, dict):
            keys.update(row.keys())
    fieldnames = sorted(keys)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in assets:
            if not isinstance(row, dict):
                continue
            writer.writerow({k: _cell_csv(row.get(k)) for k in fieldnames})


def main():
    parser = argparse.ArgumentParser(
        description="Enumerate all assets for an organization (JSON + CSV).",
    )
    parser.add_argument("org_id", help="Organization ID (from enumerate_orgs.py)")
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="Output JSON path (default: output/assets_org_<org_id>.json)",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        metavar="PATH",
        help="Output CSV path (default: output/assets_org_<org_id>.csv)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Less console output during pagination",
    )
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    slug = _safe_org_slug(org_id)
    out_dir = os.path.join(_PROJECT_ROOT, "output")
    default_json = os.path.join(out_dir, f"assets_org_{slug}.json")
    default_csv = os.path.join(out_dir, f"assets_org_{slug}.csv")
    json_path = args.json_path or default_json
    csv_path = args.csv_path or default_csv

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        if verbose:
            print(f"Connecting to {air_host}/api/public/assets...")
            print(f"Fetching assets for organization ID: {org_id}")

        params = {"filter[organizationIds]": org_id}
        assets = paginate_get(
            air_host,
            api_token,
            "/api/public/assets",
            params=params,
            verbose=verbose,
        )

        if verbose:
            print(f"\nRetrieved {len(assets)} asset(s).")

        write_json(json_path, org_id, assets)
        write_csv(csv_path, assets)

        if verbose:
            print(f"Wrote JSON: {json_path}")
            print(f"Wrote CSV:  {csv_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

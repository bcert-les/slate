"""
List all assets (endpoints) for an organization.

Endpoint: GET /api/public/assets

Writes full API objects to JSON and a CSV whose columns are the union of all
top-level asset keys (nested dicts/lists are JSON-encoded in cells).

Run from repository root:
  python api/list_assets.py <org_id>
  python api/list_assets.py <org_id> --json output/my_assets.json --csv output/my_assets.csv --quiet
"""
import argparse
import csv
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

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
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"organizationId": org_id, "count": len(assets), "assets": assets},
                  f, indent=2, ensure_ascii=False)


def write_csv(path, assets):
    _ensure_parent_dir(path)
    if not assets:
        open(path, "w").close()
        return
    keys: set = set()
    for row in assets:
        if isinstance(row, dict):
            keys.update(row.keys())
    fieldnames = sorted(keys)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in assets:
            if isinstance(row, dict):
                writer.writerow({k: _cell_csv(row.get(k)) for k in fieldnames})


def main():
    parser = argparse.ArgumentParser(
        description="GET /api/public/assets — list all assets for an organization (JSON + CSV).",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--json", dest="json_path", metavar="PATH",
                        help="Output JSON path (default: output/assets_org_<org_id>.json)")
    parser.add_argument("--csv", dest="csv_path", metavar="PATH",
                        help="Output CSV path (default: output/assets_org_<org_id>.csv)")
    parser.add_argument("--quiet", action="store_true", help="Less console output")
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        id_slug = _safe_org_slug(org_id)
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        json_path = args.json_path or os.path.join(out_dir, f"assets_org_{id_slug}.json")
        csv_path = args.csv_path or os.path.join(out_dir, f"assets_org_{id_slug}.csv")

        if verbose:
            print(f"Organization ID: {org_id}")
            print(f"GET {air_host}/api/public/assets")

        assets = paginate_get(
            air_host, api_token, "/api/public/assets",
            params={"filter[organizationIds]": org_id},
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

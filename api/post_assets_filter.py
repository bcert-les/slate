"""
Filter assets using a server-side POST body.

Endpoint: POST /api/public/assets/filter

Default body when --body-file and --preset are omitted:
  {"filter": {"organizationIds": ["<org_id>"]}}

Use --preset isolated for isolated-only inventory, or --body-file for a
fully custom filter body.

Run from repository root:
  python api/post_assets_filter.py <org_id>
  python api/post_assets_filter.py <org_id> --preset isolated
  python api/post_assets_filter.py <org_id> --body-file my_filter.json
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
from lib.binalyze_asset_filter import (
    filter_assets_client_isolated_only,
    isolated_assets_filter_body,
)
from lib.pagination import paginate_post


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
        description="POST /api/public/assets/filter — server-side asset filter.",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--preset", choices=("isolated",), default=None,
                        help="Built-in filter: 'isolated' returns isolated assets.")
    parser.add_argument("--body-file", metavar="PATH",
                        help="JSON file to use as the POST body (overrides --preset).")
    parser.add_argument("--page-size", type=int, default=100, metavar="N")
    parser.add_argument("--json", dest="json_path", metavar="PATH")
    parser.add_argument("--csv", dest="csv_path", metavar="PATH")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)
    if args.body_file and args.preset:
        print("Use either --body-file or --preset, not both.", file=sys.stderr)
        sys.exit(1)

    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = json.load(f)
    elif args.preset == "isolated":
        body = isolated_assets_filter_body(org_id)
    else:
        body = {"filter": {"organizationIds": [org_id]}}

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        id_slug = _safe_org_slug(org_id)
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        suffix = f"_preset_{args.preset}" if args.preset else ""
        json_path = args.json_path or os.path.join(out_dir, f"assets_filter_org_{id_slug}{suffix}.json")
        csv_path = args.csv_path or os.path.join(out_dir, f"assets_filter_org_{id_slug}{suffix}.csv")

        if verbose:
            print(f"Organization ID: {org_id}")
            print(f"POST {air_host}/api/public/assets/filter")
            print(json.dumps(body, indent=2))

        assets = paginate_post(
            air_host, api_token, "/api/public/assets/filter",
            body=body, page_size=args.page_size, verbose=verbose,
        )

        if args.preset == "isolated":
            raw_n = len(assets)
            assets = filter_assets_client_isolated_only(assets)
            if verbose and raw_n != len(assets):
                print(f"\nClient-side filter: kept {len(assets)} of {raw_n} with isolationStatus "
                      f"isolated or isolating.")

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

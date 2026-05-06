"""
List assets via POST /api/public/assets/filter (server-side filter body).

Uses the same paginated result shape as GET /api/public/assets when the API
supports page/pageSize query parameters on this route. Writes JSON and CSV
in the same layout as enumerate_assets.py.

Default POST body (when --body-file is omitted):

    {"filter": {"organizationIds": ["<org_id>"]}}

Override the body with --body-file (full JSON object). For custom filters,
keep organizationIds inside filter if your tenant expects that shape.
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

from lib.api_client import api_get, load_config
from lib.pagination import paginate_post


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


def _safe_org_name_for_file(name: str, max_len: int = 50) -> str:
    if not name or not str(name).strip():
        return ""
    s = str(name).strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z._\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or ""


def fetch_organization(air_host, api_token, org_id):
    resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
    if not resp.ok:
        raise RuntimeError(
            f"Could not fetch organization {org_id}: HTTP {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    return data.get("result", data) if isinstance(data, dict) else {}


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


def default_filter_body(org_id: str) -> dict:
    return {"filter": {"organizationIds": [org_id]}}


def main():
    parser = argparse.ArgumentParser(
        description="Filter assets via POST /api/public/assets/filter (JSON + CSV).",
    )
    parser.add_argument("org_id", help="Organization ID (used in default body and output names)")
    parser.add_argument(
        "--body-file",
        metavar="PATH",
        help="JSON file to send as the POST body (replaces default filter body)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        metavar="N",
        help="Page size for pagination query (default: 100)",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="Output JSON path (default: output/assets_filter_org_<name>_<org_id>.json)",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        metavar="PATH",
        help="Output CSV path (default: output/assets_filter_org_<name>_<org_id>.csv)",
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

    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = json.load(f)
        if not isinstance(body, dict):
            print("--body-file must contain a JSON object", file=sys.stderr)
            sys.exit(1)
    else:
        body = default_filter_body(org_id)

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        org = fetch_organization(air_host, api_token, org_id)
        org_name = org.get("name") or ""
        name_slug = _safe_org_name_for_file(org_name)
        id_slug = _safe_org_slug(org_id)
        if name_slug:
            file_stem = f"assets_filter_org_{name_slug}_{id_slug}"
        else:
            file_stem = f"assets_filter_org_{id_slug}"

        out_dir = os.path.join(_PROJECT_ROOT, "output")
        default_json = os.path.join(out_dir, f"{file_stem}.json")
        default_csv = os.path.join(out_dir, f"{file_stem}.csv")
        json_path = args.json_path or default_json
        csv_path = args.csv_path or default_csv

        if verbose:
            label = f"{org_name} ({org_id})" if org_name else org_id
            print(f"Organization: {label}")
            print(f"POST {air_host}/api/public/assets/filter")
            if args.body_file:
                print(f"Body: {args.body_file}")
            else:
                print("Body: default filter.organizationIds")

        assets = paginate_post(
            air_host,
            api_token,
            "/api/public/assets/filter",
            body=body,
            page_size=args.page_size,
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

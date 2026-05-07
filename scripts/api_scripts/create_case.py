"""
Create a Binalyze AIR case (POST /api/public/cases).

Validates the organization, then creates a case with the given name and org ID.
Optional fields can be merged into the JSON body for tenants that require them
(e.g. category, or visibility) — use --extra-json with a small JSON file or string.
Default visibility is public-to-organization; override via --extra-json if needed.

Run from repository root:
  python scripts/api_scripts/create_case.py <org_id> --name "Investigation title"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_post, load_config
from lib.binalyze_cases import validate_org


def _default_case_name() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"API case - {ts}"


def _parse_extra_json(raw: str | None) -> dict:
    if not raw or not str(raw).strip():
        return {}
    path = os.path.expanduser(str(raw).strip())
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--extra-json must be a JSON object or a path to a JSON file containing an object.")
    return data


def main() -> None:
    p = argparse.ArgumentParser(description="Create a Binalyze AIR case.")
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument(
        "--name",
        dest="case_name",
        default=None,
        help='Case title (default: "API case - <UTC timestamp>")',
    )
    p.add_argument(
        "--extra-json",
        metavar="STR_OR_PATH",
        default=None,
        help="Merge extra fields into the POST body (JSON string or path to .json object).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request body only; do not POST.",
    )
    p.add_argument(
        "--print-json",
        action="store_true",
        help="Print full JSON response to stdout.",
    )
    args = p.parse_args()

    air_host, api_token = load_config()
    org_id = args.org_id.strip()
    case_name = (args.case_name or "").strip() or _default_case_name()

    print(f"Host: {air_host}")
    print(f"Organization ID: {org_id}")
    print(f"Case name: {case_name}")

    print("\nValidating organization...", flush=True)
    org = validate_org(air_host, api_token, org_id)
    print(f"  {org.get('name', org_id)}")

    body: dict = {
        "name": case_name,
        "organizationId": org_id,
        "visibility": "public-to-organization",
    }
    extra = _parse_extra_json(args.extra_json)
    overlap = set(body.keys()) & set(extra.keys())
    if overlap:
        print(f"Warning: --extra-json overwrites keys: {sorted(overlap)}", file=sys.stderr)
    body.update(extra)

    print(f"\nPOST /api/public/cases")
    print(json.dumps(body, indent=2))

    if args.dry_run:
        print("\n[DRY RUN] No request sent.")
        return

    resp = api_post(air_host, api_token, "/api/public/cases", body=body)
    if not resp.ok:
        print(f"\nError: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:2000], file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    case = data.get("result", data)
    cid = case.get("_id") or case.get("id") or case.get("caseId")
    print(f"\nCreated case ID: {cid}")
    print(f"Status: {case.get('status', 'N/A')}")
    if args.print_json:
        print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

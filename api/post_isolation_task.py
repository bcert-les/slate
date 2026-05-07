"""
Assign an isolation task (enable or disable) to one or more endpoints.

Endpoint: POST /api/public/assets/tasks/isolation

Run from repository root:
  python api/post_isolation_task.py <org_id> <endpoint_id> [<endpoint_id> ...] --enable
  python api/post_isolation_task.py <org_id> <endpoint_id> [<endpoint_id> ...] --disable
  python api/post_isolation_task.py <org_id> <endpoint_id> --enable --case-id <case_id>
"""
import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_post, load_config
from lib.binalyze_isolation import build_isolation_request_body


def main():
    p = argparse.ArgumentParser(
        description="POST /api/public/assets/tasks/isolation — enable or disable network isolation.",
    )
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument("endpoint_ids", nargs="+", metavar="ENDPOINT_ID",
                   help="One or more asset IDs to isolate/unisolate")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--enable", action="store_true", dest="enable", help="Isolate the endpoints.")
    action.add_argument("--disable", action="store_false", dest="enable", help="Remove isolation.")
    p.add_argument("--case-id", default=None, help="Optional case ID to attach the task to.")
    p.add_argument("--dry-run", action="store_true", help="Print body only; do not POST.")
    args = p.parse_args()

    air_host, api_token = load_config()

    body = build_isolation_request_body(
        organization_id=args.org_id,
        endpoint_ids=args.endpoint_ids,
        enable=args.enable,
        case_id=args.case_id,
    )

    action_label = "ENABLE (isolate)" if args.enable else "DISABLE (unisolate)"
    print(f"POST {air_host}/api/public/assets/tasks/isolation  [{action_label}]")
    print(f"\nRequest body:")
    print(json.dumps(body, indent=2))

    if args.dry_run:
        print("\n[DRY RUN] No request sent.")
        return

    try:
        resp = api_post(air_host, api_token, "/api/public/assets/tasks/isolation", body=body)
        print(f"\nHTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str))
        except Exception:
            print(resp.text[:2000])
        if not resp.ok:
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

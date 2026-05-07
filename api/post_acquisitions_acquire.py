"""
Assign an acquisition task to one or more endpoints.

Endpoint: POST /api/public/acquisitions/acquire

Run from repository root:
  python api/post_acquisitions_acquire.py --help

Body shape reference:
  {
    "caseId": "<case_id>",
    "acquisitionProfileId": "<profile_id_or_preset>",
    "droneConfig": {"autoPilot": false, "enabled": false},
    "taskConfig": {"choice": "use-policy"},
    "filter": {"endpointIds": ["<id>"], "organizationIds": [<org_int>]}
  }
"""
import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_post, load_config


def main():
    p = argparse.ArgumentParser(
        description="POST /api/public/acquisitions/acquire — assign acquisition task.",
    )
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument("case_id", help="Case ID to attach the task to")
    p.add_argument("profile_id", help="Acquisition profile ID (or preset slug like 'quick')")
    p.add_argument("endpoint_ids", nargs="+", metavar="ENDPOINT_ID",
                   help="One or more endpoint asset IDs")
    p.add_argument("--dry-run", action="store_true", help="Print body only; do not POST.")
    args = p.parse_args()

    air_host, api_token = load_config()

    body = {
        "caseId": args.case_id,
        "droneConfig": {"autoPilot": False, "enabled": False},
        "taskConfig": {"choice": "use-policy"},
        "acquisitionProfileId": args.profile_id,
        "filter": {
            "endpointIds": list(args.endpoint_ids),
            "organizationIds": [int(args.org_id)],
        },
    }

    print(f"POST {air_host}/api/public/acquisitions/acquire")
    print(f"\nRequest body:")
    print(json.dumps(body, indent=2))

    if args.dry_run:
        print("\n[DRY RUN] No request sent.")
        return

    try:
        resp = api_post(air_host, api_token, "/api/public/acquisitions/acquire", body=body)
        print(f"\nHTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str)[:3000])
        except Exception:
            print(resp.text[:2000])
        if not resp.ok:
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

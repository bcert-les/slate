"""
List cases for an organization.

Endpoint: GET /api/public/cases

Run from repository root:
  python api/list_cases.py <org_id> [status]

  org_id: Organization ID (required)
  status: Case status filter (optional, default: open)
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.pagination import paginate_get


def main():
    if len(sys.argv) < 2:
        print("Usage: python api/list_cases.py <org_id> [status]", file=sys.stderr)
        print("  status: open (default) | closed | all", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()
    org_id = sys.argv[1].strip()
    status_filter = sys.argv[2] if len(sys.argv) > 2 else "open"

    try:
        print(f"GET {air_host}/api/public/cases  (org={org_id}, status={status_filter})")
        params = {
            "filter[organizationIds]": org_id,
            "filter[status]": status_filter,
        }
        cases = paginate_get(air_host, api_token, "/api/public/cases", params=params)

        print(f"\nFound {len(cases)} {status_filter} case(s) in organization {org_id}:")
        if not cases:
            print("  (No cases found)")
        else:
            for case in cases:
                case_id = case.get("_id") or case.get("id") or case.get("caseId")
                name = case.get("name") or case.get("title")
                status = case.get("status")
                created = case.get("createdAt")
                owner = case.get("owner")
                investigation_id = (case.get("metadata") or {}).get("investigationId")

                print(f"\n  Case ID:          {case_id}")
                print(f"  Name:             {name}")
                print(f"  Status:           {status}")
                print(f"  Owner:            {owner}")
                print(f"  Created:          {created}")
                print(f"  Investigation ID: {investigation_id}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

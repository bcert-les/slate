import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.api_client import load_config
from lib.pagination import paginate_get


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 2:
        print("Usage: python3 scripts/enumerate_cases.py <org_id> [status]", file=sys.stderr)
        print("  org_id: Organization ID (required)", file=sys.stderr)
        print("  status: Case status filter (optional, default: 'open')", file=sys.stderr)
        print("\nGet org IDs with: python3 scripts/enumerate_orgs.py", file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1]
    status_filter = sys.argv[2] if len(sys.argv) > 2 else "open"

    print(f"Connecting to {air_host}/api/public/cases...")
    print(f"Fetching cases for organization ID: {org_id}, status: {status_filter}")

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
            metadata = case.get("metadata") or {}
            investigation_id = metadata.get("investigationId")

            print(f"\n- Case ID: {case_id}")
            print(f"  Name: {name}")
            print(f"  Status: {status}")
            print(f"  Owner: {owner}")
            print(f"  Created: {created}")
            print(f"  Investigation ID: {investigation_id}")


if __name__ == "__main__":
    main()

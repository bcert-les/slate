"""
List all organizations in the Binalyze AIR tenant.

Endpoint: GET /api/public/organizations

Run from repository root:
  python api/list_organizations.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.pagination import paginate_get


def main():
    air_host, api_token = load_config()

    try:
        print(f"GET {air_host}/api/public/organizations")
        orgs = paginate_get(air_host, api_token, "/api/public/organizations")

        print(f"\nFound {len(orgs)} organization(s):")
        for org in orgs:
            oid = org.get("_id") or org.get("id") or org.get("organizationId")
            name = org.get("name")
            print(f"  - ID: {oid}  Name: {name}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

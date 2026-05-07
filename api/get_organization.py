"""
Fetch a single organization by ID.

Endpoint: GET /api/public/organizations/{id}

Run from repository root:
  python api/get_organization.py <org_id>
"""
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config


def main():
    if len(sys.argv) < 2:
        print("Usage: python api/get_organization.py <org_id>", file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1].strip()
    air_host, api_token = load_config()

    try:
        print(f"GET {air_host}/api/public/organizations/{org_id}")
        resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
        if not resp.ok:
            print(f"Error: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()
        org = data.get("result", data)
        print(f"\nOrganization ID:   {org.get('_id') or org.get('id')}")
        print(f"Name:              {org.get('name')}")
        print(f"Slug:              {org.get('slug')}")
        print()
        print(json.dumps(org, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

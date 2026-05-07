"""
Fetch a single case by ID.

Endpoint: GET /api/public/cases/{id}

Run from repository root:
  python api/get_case.py <case_id>
"""
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config


def main():
    if len(sys.argv) < 2:
        print("Usage: python api/get_case.py <case_id>", file=sys.stderr)
        sys.exit(1)

    case_id = sys.argv[1].strip()
    air_host, api_token = load_config()

    try:
        print(f"GET {air_host}/api/public/cases/{case_id}")
        resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
        if not resp.ok:
            print(f"Error: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            sys.exit(1)
        data = resp.json()
        case = data.get("result", data)
        print(f"\nCase ID:          {case.get('_id') or case.get('id')}")
        print(f"Name:             {case.get('name')}")
        print(f"Status:           {case.get('status')}")
        print(f"Org ID:           {case.get('organizationId')}")
        print(f"Investigation ID: {(case.get('metadata') or {}).get('investigationId')}")
        print()
        print(json.dumps(case, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

"""
List acquisition profiles available for an organization.

Endpoint: GET /api/public/acquisitions/profiles

Run from repository root:
  python api/list_acquisition_profiles.py <org_id>
"""
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.pagination import paginate_get


def main():
    if len(sys.argv) < 2:
        print("Usage: python api/list_acquisition_profiles.py <org_id>", file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1].strip()
    air_host, api_token = load_config()

    try:
        print(f"GET {air_host}/api/public/acquisitions/profiles  (org={org_id})")
        profiles = paginate_get(
            air_host, api_token, "/api/public/acquisitions/profiles",
            params={"filter[organizationIds]": org_id},
            verbose=False,
        )

        print(f"\nFound {len(profiles)} acquisition profile(s):\n")
        for i, p in enumerate(profiles, 1):
            pid = p.get("_id") or p.get("id") or "?"
            name = p.get("name", "Unnamed")
            print(f"  [{i:>3}]  {name}  (ID: {pid})")

        print()
        print(json.dumps(profiles, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

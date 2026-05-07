"""
List all tasks for a specific asset (endpoint).

Endpoint: GET /api/public/assets/{id}/tasks

Run from repository root:
  python api/list_asset_tasks.py <asset_id_or_hostname> <org_id>
"""
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config
from lib.binalyze_cases import find_asset_strict, AssetResolveError


def main():
    if len(sys.argv) < 3:
        print("Usage: python api/list_asset_tasks.py <asset_id_or_hostname> <org_id>", file=sys.stderr)
        sys.exit(1)

    identifier = sys.argv[1].strip()
    org_id = sys.argv[2].strip()
    air_host, api_token = load_config()

    try:
        # Resolve identifier to asset id if needed
        try:
            asset = find_asset_strict(air_host, api_token, identifier, org_id)
            asset_id = str(asset.get("_id") or asset.get("id"))
            hostname = asset.get("name", identifier)
        except AssetResolveError as e:
            print(f"Error resolving asset: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Endpoint: {hostname} ({asset_id})")
        print(f"GET {air_host}/api/public/assets/{asset_id}/tasks")

        resp = api_get(air_host, api_token, f"/api/public/assets/{asset_id}/tasks")
        if not resp.ok:
            print(f"Error: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        inner = data.get("result", data)
        tasks = inner if isinstance(inner, list) else inner.get("entities", [])

        print(f"\nFound {len(tasks)} task(s):\n")
        for i, task in enumerate(tasks, 1):
            print(f"  [{i}] {task.get('name', 'Unnamed')}")
            print(f"      Type:   {task.get('type', 'N/A')}")
            print(f"      Status: {task.get('status', 'N/A')}")
            print(f"      Created: {task.get('createdAt', 'N/A')}")

        print()
        print(json.dumps(tasks, indent=2, default=str))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

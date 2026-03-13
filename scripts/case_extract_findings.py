import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.api_client import load_config, api_get

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def try_endpoint(air_host, api_token, endpoint_path, params=None):
    """Try a specific endpoint and return data if successful."""
    resp = api_get(air_host, api_token, endpoint_path, params=params)
    if resp.ok:
        return resp.json(), resp.status_code
    return None, resp.status_code


def extract_findings(air_host, api_token, org_id, case_id, page_size=100):
    """Attempt to extract findings from a case by probing multiple endpoints."""
    endpoint_patterns = [
        f"/api/public/cases/{case_id}",
        f"/api/public/cases/{case_id}/endpoints",
        f"/api/public/cases/{case_id}/tasks",
        f"/api/public/cases/{case_id}/acquisitions",
        f"/api/public/acquisitions",
        f"/api/public/tasks",
        f"/api/public/cases/{case_id}/findings",
        f"/api/public/cases/{case_id}/evidence",
        f"/api/public/investigations",
    ]

    print(f"Attempting to extract findings from case {case_id} in organization {org_id}...")
    print(f"Testing various endpoint patterns...\n")

    successful_endpoints = []

    for endpoint in endpoint_patterns:
        params = {"page": 1, "pageSize": page_size}

        if "/acquisitions" in endpoint and case_id not in endpoint:
            params["filter[caseIds]"] = case_id
        elif "/tasks" in endpoint and case_id not in endpoint:
            params["filter[caseIds]"] = case_id
        elif "/investigations" in endpoint:
            params["filter[caseIds]"] = case_id

        print(f"Trying: {air_host}{endpoint}", flush=True)
        data, status = try_endpoint(air_host, api_token, endpoint, params)

        if data is not None:
            print(f"  SUCCESS (HTTP {status})\n")
            successful_endpoints.append({
                "endpoint": endpoint,
                "data": data,
                "status": status,
                "params": params,
            })
        else:
            print(f"  Failed (HTTP {status})\n")

    return successful_endpoints


def display_findings(endpoints_data):
    """Display findings from successful endpoints."""
    if not endpoints_data:
        print("\nNo successful endpoints found.")
        print("\nPossible reasons:")
        print("  1. The findings API endpoint may have a different path")
        print("  2. Your API token may not have permissions to view findings")
        print("  3. The case may not have any findings yet")
        return

    print(f"\n{'='*80}")
    print(f"RESULTS: Found {len(endpoints_data)} successful endpoint(s)")
    print(f"{'='*80}\n")

    for idx, endpoint_info in enumerate(endpoints_data, 1):
        endpoint = endpoint_info["endpoint"]
        data = endpoint_info["data"]

        print(f"\n[{idx}] Endpoint: {endpoint}")
        print(f"{'─'*80}")

        if isinstance(data, dict):
            if "result" in data and isinstance(data["result"], dict):
                result = data["result"]
                if "entities" in result:
                    entities = result["entities"]
                    print(f"Found {len(entities)} item(s)\n")
                    if isinstance(entities, list) and len(entities) > 0:
                        for i, entity in enumerate(entities[:5], 1):
                            print(f"  Item {i}:")
                            if isinstance(entity, dict):
                                for key, value in entity.items():
                                    if not key.startswith('_') and value is not None:
                                        str_value = str(value)
                                        if len(str_value) > 100:
                                            str_value = str_value[:100] + "..."
                                        print(f"    {key}: {str_value}")
                            print()
                        if len(entities) > 5:
                            print(f"  ... and {len(entities) - 5} more items\n")
                else:
                    print("Result data:")
                    for key, value in result.items():
                        if not key.startswith('_'):
                            print(f"  {key}: {value}")
            else:
                print("Response structure:")
                for key in data.keys():
                    print(f"  - {key}")

        print()


def save_to_file(endpoints_data, org_id, case_id):
    """Save findings to a JSON file."""
    if not endpoints_data:
        return
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(OUTPUT_DIR, f"findings_org{org_id}_case{case_id}.json")
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(endpoints_data, f, indent=2, ensure_ascii=False)
    print(f"Data saved to: {filename}")


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 3:
        print("Usage: python3 scripts/case_extract_findings.py <org_id> <case_id>", file=sys.stderr)
        print("\nExample: python3 scripts/case_extract_findings.py 362 C-2026-00001", file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1]
    case_id = sys.argv[2]

    try:
        endpoints_data = extract_findings(air_host, api_token, org_id, case_id)
        display_findings(endpoints_data)
        if endpoints_data:
            save_to_file(endpoints_data, org_id, case_id)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

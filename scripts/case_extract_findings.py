import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.api_client import load_config, api_get, api_post

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def try_get(air_host, api_token, path, params=None):
    resp = api_get(air_host, api_token, path, params=params)
    if resp.ok:
        return resp.json(), resp.status_code
    return None, resp.status_code


def try_post(air_host, api_token, path, body=None):
    resp = api_post(air_host, api_token, path, body=body or {})
    if resp.ok:
        return resp.json(), resp.status_code
    return None, resp.status_code


def get_investigation_id(air_host, api_token, case_id):
    """Fetch a case and return its investigation ID."""
    data, status = try_get(air_host, api_token, f"/api/public/cases/{case_id}")
    if data is None:
        return None
    result = data.get("result", data)
    return (result.get("metadata") or {}).get("investigationId")


def probe_endpoints(air_host, api_token, org_id, case_id, investigation_id):
    """Probe case and Investigation Hub endpoints, returning those that respond."""

    endpoints = [
        ("GET",  f"/api/public/cases/{case_id}",
         {"filter[organizationIds]": org_id}),
        ("GET",  f"/api/public/cases/{case_id}/endpoints",
         {"filter[organizationIds]": org_id, "page": 1, "pageSize": 100}),
        ("GET",  f"/api/public/cases/{case_id}/tasks",
         {"page": 1, "pageSize": 100}),
    ]

    if investigation_id:
        hub = f"/api/public/investigation-hub/investigations/{investigation_id}"
        endpoints += [
            ("GET",  f"{hub}/assets", None),
            ("GET",  f"{hub}/evidence/data-structure", None),
            ("GET",  f"{hub}/evidence/counts", None),
            ("GET",  f"{hub}/findings/data-structure", None),
            ("POST", f"{hub}/findings/summary", None),
            ("GET",  f"{hub}/sections", None),
        ]

    print(f"Probing {len(endpoints)} endpoints...\n")
    results = []

    for method, path, params in endpoints:
        print(f"  {method} {path}", end="", flush=True)
        if method == "POST":
            data, status = try_post(air_host, api_token, path)
        else:
            data, status = try_get(air_host, api_token, path, params)

        if data is not None:
            print(f"  -> {status} OK")
            results.append({"method": method, "endpoint": path, "data": data, "status": status})
        else:
            print(f"  -> {status} Failed")

    return results


def display_findings(endpoints_data):
    if not endpoints_data:
        print("\nNo successful endpoints found.")
        print("\nPossible reasons:")
        print("  1. Your API token may not have permissions")
        print("  2. The case may not have completed acquisition yet")
        return

    print(f"\n{'='*80}")
    print(f"RESULTS: {len(endpoints_data)} endpoint(s) returned data")
    print(f"{'='*80}")

    for idx, info in enumerate(endpoints_data, 1):
        data = info["data"]
        print(f"\n[{idx}] {info['method']} {info['endpoint']}")
        print(f"{'─'*80}")

        if not isinstance(data, dict):
            print(f"  (non-dict response: {type(data).__name__})")
            continue

        result = data.get("result", data)

        if isinstance(result, dict) and "entities" in result:
            entities = result["entities"]
            print(f"  {len(entities)} item(s)")
            for i, entity in enumerate(entities[:3], 1):
                if isinstance(entity, dict):
                    preview = {k: v for k, v in list(entity.items())[:6]
                               if v is not None and not str(k).startswith("_")}
                    print(f"    [{i}] {json.dumps(preview, default=str)[:200]}")
            if len(entities) > 3:
                print(f"    ... and {len(entities) - 3} more")
        elif isinstance(result, list):
            print(f"  {len(result)} item(s)")
            for i, item in enumerate(result[:3], 1):
                print(f"    [{i}] {json.dumps(item, default=str)[:200]}")
            if len(result) > 3:
                print(f"    ... and {len(result) - 3} more")
        elif isinstance(result, dict):
            for key in list(result.keys())[:10]:
                val = result[key]
                val_str = json.dumps(val, default=str) if isinstance(val, (dict, list)) else str(val)
                if len(val_str) > 120:
                    val_str = val_str[:120] + "..."
                print(f"    {key}: {val_str}")

    print()


def save_to_file(endpoints_data, org_id, case_id):
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
        print(f"Looking up investigation ID for case {case_id}...", flush=True)
        investigation_id = get_investigation_id(air_host, api_token, case_id)
        if investigation_id:
            print(f"  Investigation ID: {investigation_id}\n")
        else:
            print("  No investigation ID found -- skipping Investigation Hub endpoints.\n")

        endpoints_data = probe_endpoints(air_host, api_token, org_id, case_id, investigation_id)
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

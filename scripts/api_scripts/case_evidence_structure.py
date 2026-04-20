import os
import sys
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from lib.api_client import load_config, api_get, api_post
from lib.pagination import paginate_get

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def try_investigation_hub(air_host, api_token, investigation_id):
    """Try Investigation Hub API endpoints (available on some tenant tiers)."""
    results = {}

    base = f"/api/public/investigation-hub/investigations/{investigation_id}"

    resp = api_get(air_host, api_token, f"{base}/evidence/data-structure")
    if resp.ok:
        results["dataStructure"] = resp.json()

    resp = api_get(air_host, api_token, f"{base}/assets")
    if resp.ok:
        results["assets"] = resp.json()

    resp = api_get(air_host, api_token, f"{base}/evidence/counts")
    if resp.ok:
        results["evidenceCounts"] = resp.json()

    resp = api_get(air_host, api_token, f"{base}/findings/data-structure")
    if resp.ok:
        results["findingsStructure"] = resp.json()

    resp = api_post(air_host, api_token, f"{base}/findings/summary", {})
    if resp.ok:
        results["findingsSummary"] = resp.json()

    return results if results else None


def get_case_by_investigation_id(air_host, api_token, investigation_id, org_id=None):
    """Find the case associated with this investigation ID.

    If org_id is given, searches that org only. Otherwise fetches all orgs
    and searches each one (the cases endpoint requires an organizationId filter).
    """
    if org_id:
        org_ids_to_search = [org_id]
    else:
        orgs = paginate_get(air_host, api_token, "/api/public/organizations", verbose=False)
        org_ids_to_search = [
            o.get("_id") or o.get("id") or o.get("organizationId") for o in orgs
        ]
        org_ids_to_search = [oid for oid in org_ids_to_search if oid]

    for oid in org_ids_to_search:
        params = {"filter[organizationIds]": oid}
        cases = paginate_get(air_host, api_token, "/api/public/cases", params=params, verbose=False)
        for case in cases:
            meta = case.get("metadata") or {}
            if meta.get("investigationId") == investigation_id:
                return case

    return None


def _format_size(size):
    if size > 1024 * 1024:
        return f"{size / (1024*1024):.1f} MB"
    elif size > 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def get_case_endpoints(air_host, api_token, case_id, org_id):
    params = {
        "filter[organizationIds]": org_id,
        "pageNumber": 1,
        "pageSize": 100,
    }
    resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}/endpoints", params)
    if not resp.ok:
        return []
    return resp.json().get("result", {}).get("entities", [])


def display_results(case, tasks, endpoints, hub_results):
    print(f"\n{'='*80}")
    print("EVIDENCE STRUCTURE REPORT")
    print(f"{'='*80}\n")

    case_id = case.get("_id", "N/A")
    name = case.get("name", "N/A")
    status = case.get("status", "N/A")
    org_id = case.get("organizationId", "N/A")
    meta = case.get("metadata", {})
    investigation_id = meta.get("investigationId", "N/A")
    disk_usage = meta.get("diskUsageInBytes", 0)

    print(f"Case: {name} ({case_id})")
    print(f"Status: {status}")
    print(f"Organization ID: {org_id}")
    print(f"Investigation ID: {investigation_id}")
    if disk_usage:
        print(f"Disk Usage: {disk_usage / (1024*1024):.1f} MB")

    category = case.get("category", {})
    if category:
        print(f"Category: {category.get('name', 'N/A')}")

    if hub_results:
        print(f"\n{'─'*80}")
        print("INVESTIGATION HUB DATA (from API)")
        print(f"{'─'*80}\n")

        for key, label in [("dataStructure", "Evidence Data Structure"),
                           ("findingsStructure", "Findings Data Structure"),
                           ("findingsSummary", "Findings Summary"),
                           ("evidenceCounts", "Evidence Counts"),
                           ("assets", "Investigation Assets")]:
            if key in hub_results:
                result = hub_results[key].get("result", hub_results[key])
                print(f"  {label}:")
                print(f"  {json.dumps(result, indent=4)[:2000]}")
                print()

    print(f"\n{'─'*80}")
    print(f"ENDPOINTS ({len(endpoints)})")
    print(f"{'─'*80}\n")

    for ep in endpoints:
        print(f"  {ep.get('name', 'Unknown')}")
        print(f"    ID: {ep.get('_id', 'N/A')}")
        print(f"    OS: {ep.get('os', 'N/A')} ({ep.get('platform', 'N/A')})")
        print(f"    IP: {ep.get('ipAddress', 'N/A')}")
        print()

    acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
    triages = [t for t in tasks if t.get("type") == "triage"]
    others = [t for t in tasks if t.get("type") not in ["acquisition", "triage"]]

    print(f"{'─'*80}")
    print(f"EVIDENCE COLLECTED ({len(tasks)} task(s))")
    print(f"{'─'*80}\n")

    for task_group, label in [(acquisitions, "Acquisitions"), (triages, "Triage"), (others, "Other")]:
        if not task_group:
            continue

        print(f"  {label} ({len(task_group)}):")
        print()

        for t in task_group:
            task_name = t.get("name", "Unnamed")
            task_type = t.get("displayType") or t.get("type", "N/A")
            endpoint_name = t.get("endpointName", "N/A")
            status = t.get("status", "N/A")
            task_id = t.get("taskId", "N/A")
            meta = t.get("metadata", {})

            print(f"    [{task_name}]")
            print(f"      Task ID: {task_id}")
            print(f"      Type: {task_type}")
            print(f"      Endpoint: {endpoint_name}")
            print(f"      Status: {status}")

            has_case_db = meta.get("hasCaseDb", False)
            has_drone = meta.get("hasDroneData", False)
            case_ppc_entries = meta.get("casePpcEntries", [])
            drone_entries = meta.get("droneZipEntries", [])
            investigation_info = meta.get("investigation", {})
            acq_profile = meta.get("acquisitionProfile", {})

            if acq_profile:
                print(f"      Profile: {acq_profile.get('name', acq_profile.get('id', 'N/A'))}")

            print(f"      Has Case.db: {has_case_db}")
            print(f"      Has DRONE Data: {has_drone}")

            if investigation_info:
                inv_status = investigation_info.get("status", "N/A")
                inv_disk = investigation_info.get("diskUsageInBytes", 0)
                print(f"      Investigation Status: {inv_status}")
                if inv_disk:
                    print(f"      Investigation Disk Usage: {inv_disk / (1024*1024):.1f} MB")

            if case_ppc_entries:
                print(f"      Evidence Files:")
                for entry in case_ppc_entries:
                    print(f"        - {entry.get('name', '?')} ({_format_size(entry.get('size', 0))})")

            if drone_entries:
                print(f"      DRONE Files:")
                for entry in drone_entries:
                    print(f"        - {entry.get('name', '?')} ({_format_size(entry.get('size', 0))})")

            response = t.get("response", {})
            if response:
                match_count = response.get("matchCount")
                if match_count is not None:
                    print(f"      Match Count: {match_count}")

            print()


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 2:
        print("Usage: python3 scripts/api_scripts/case_evidence_structure.py <investigation_id> [org_id]", file=sys.stderr)
        print("\nExample: python3 scripts/api_scripts/case_evidence_structure.py 557a6170-... 362", file=sys.stderr)
        sys.exit(1)

    investigation_id = sys.argv[1]
    org_id = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        print("Trying Investigation Hub API endpoints...", flush=True)
        hub_results = try_investigation_hub(air_host, api_token, investigation_id)
        if hub_results:
            print(f"  Investigation Hub API available ({len(hub_results)} endpoint(s) returned data)")
        else:
            print("  Investigation Hub API not available on this tenant, using fallback.")

        print("Looking up case for this investigation...", flush=True)
        case = get_case_by_investigation_id(air_host, api_token, investigation_id, org_id)

        if not case:
            print(f"\nError: Could not find a case with investigation ID: {investigation_id}", file=sys.stderr)
            sys.exit(1)

        case_id = case.get("_id")
        case_org_id = case.get("organizationId")
        print(f"  Found case: {case.get('name')} ({case_id})")

        print("Fetching case tasks...", flush=True)
        tasks = paginate_get(
            air_host, api_token, f"/api/public/cases/{case_id}/tasks", verbose=False,
        )
        print(f"  Found {len(tasks)} task(s)")

        print("Fetching case endpoints...", flush=True)
        endpoints = get_case_endpoints(air_host, api_token, case_id, case_org_id)
        print(f"  Found {len(endpoints)} endpoint(s)")

        display_results(case, tasks, endpoints, hub_results)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        filename = os.path.join(OUTPUT_DIR, f"evidence_structure_{investigation_id[:8]}.json")
        output = {
            "investigationId": investigation_id,
            "case": case,
            "tasks": tasks,
            "endpoints": endpoints,
        }
        if hub_results:
            output["investigationHub"] = hub_results

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"\nRaw data saved to: {filename}\n")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

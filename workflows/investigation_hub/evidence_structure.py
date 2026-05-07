"""
Show the evidence structure for an investigation via the Investigation Hub API.

Looks up the case associated with the investigation ID, fetches tasks and
endpoints, and displays a structured summary.

Run from repository root:
  python workflows/investigation_hub/evidence_structure.py <investigation_id> [org_id]
"""
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, api_post, load_config
from lib.pagination import paginate_get

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


def try_investigation_hub(air_host, api_token, investigation_id):
    results = {}
    base = f"/api/public/investigation-hub/investigations/{investigation_id}"

    for path in [
        f"{base}/evidence/data-structure",
        f"{base}/assets",
        f"{base}/evidence/counts",
        f"{base}/findings/data-structure",
    ]:
        resp = api_get(air_host, api_token, path)
        if resp.ok:
            results[path.split("/")[-1].replace("-", "_")] = resp.json()

    resp = api_post(air_host, api_token, f"{base}/findings/summary", {})
    if resp.ok:
        results["findingsSummary"] = resp.json()

    return results if results else None


def get_case_by_investigation_id(air_host, api_token, investigation_id, org_id=None):
    if org_id:
        org_ids_to_search = [org_id]
    else:
        orgs = paginate_get(air_host, api_token, "/api/public/organizations", verbose=False)
        org_ids_to_search = [
            o.get("_id") or o.get("id") or o.get("organizationId") for o in orgs
        ]
        org_ids_to_search = [oid for oid in org_ids_to_search if oid]

    for oid in org_ids_to_search:
        cases = paginate_get(
            air_host, api_token, "/api/public/cases",
            params={"filter[organizationIds]": oid}, verbose=False,
        )
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
    params = {"filter[organizationIds]": org_id, "pageNumber": 1, "pageSize": 100}
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
    org_id = case.get("organizationId", "N/A")
    meta = case.get("metadata", {})
    investigation_id = meta.get("investigationId", "N/A")
    disk_usage = meta.get("diskUsageInBytes", 0)

    print(f"Case: {name} ({case_id})")
    print(f"Status: {case.get('status', 'N/A')}")
    print(f"Organization ID: {org_id}")
    print(f"Investigation ID: {investigation_id}")
    if disk_usage:
        print(f"Disk Usage: {disk_usage / (1024*1024):.1f} MB")

    category = case.get("category", {})
    if category:
        print(f"Category: {category.get('name', 'N/A')}")

    if hub_results:
        print(f"\n{'─'*80}\nINVESTIGATION HUB DATA\n{'─'*80}\n")
        for key, label in [
            ("data_structure", "Evidence Data Structure"),
            ("findings_data_structure", "Findings Data Structure"),
            ("findingsSummary", "Findings Summary"),
            ("counts", "Evidence Counts"),
            ("assets", "Investigation Assets"),
        ]:
            if key in hub_results:
                result = hub_results[key].get("result", hub_results[key])
                print(f"  {label}:")
                print(f"  {json.dumps(result, indent=4)[:2000]}")
                print()

    print(f"\n{'─'*80}\nENDPOINTS ({len(endpoints)})\n{'─'*80}\n")
    for ep in endpoints:
        print(f"  {ep.get('name', 'Unknown')}")
        print(f"    ID: {ep.get('_id', 'N/A')}  OS: {ep.get('os', 'N/A')}  IP: {ep.get('ipAddress', 'N/A')}")
        print()

    acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
    triages = [t for t in tasks if t.get("type") == "triage"]
    others = [t for t in tasks if t.get("type") not in ["acquisition", "triage"]]

    print(f"{'─'*80}\nEVIDENCE COLLECTED ({len(tasks)} task(s))\n{'─'*80}\n")
    for task_group, label in [(acquisitions, "Acquisitions"), (triages, "Triage"), (others, "Other")]:
        if not task_group:
            continue
        print(f"  {label} ({len(task_group)}):\n")
        for t in task_group:
            m = t.get("metadata", {})
            print(f"    [{t.get('name', 'Unnamed')}]")
            print(f"      Task ID: {t.get('taskId', 'N/A')}  Status: {t.get('status', 'N/A')}")
            print(f"      Endpoint: {t.get('endpointName', 'N/A')}")
            acq = m.get("acquisitionProfile", {})
            if acq:
                print(f"      Profile: {acq.get('name', acq.get('id', 'N/A'))}")
            case_entries = m.get("casePpcEntries", [])
            if case_entries:
                print("      Evidence Files:")
                for entry in case_entries:
                    print(f"        - {entry.get('name', '?')} ({_format_size(entry.get('size', 0))})")
            print()


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 2:
        print("Usage: python workflows/investigation_hub/evidence_structure.py <investigation_id> [org_id]",
              file=sys.stderr)
        sys.exit(1)

    investigation_id = sys.argv[1]
    org_id = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        print("Trying Investigation Hub API...", flush=True)
        hub_results = try_investigation_hub(air_host, api_token, investigation_id)
        if hub_results:
            print(f"  Hub API available ({len(hub_results)} endpoint(s) returned data)")
        else:
            print("  Hub API not available; using case-based fallback.")

        print("Looking up case for investigation...", flush=True)
        case = get_case_by_investigation_id(air_host, api_token, investigation_id, org_id)
        if not case:
            print(f"\nError: No case found with investigation ID: {investigation_id}", file=sys.stderr)
            sys.exit(1)

        case_id = case.get("_id")
        case_org_id = case.get("organizationId")
        print(f"  Case: {case.get('name')} ({case_id})")

        print("Fetching case tasks...", flush=True)
        tasks = paginate_get(air_host, api_token, f"/api/public/cases/{case_id}/tasks", verbose=False)
        print(f"  {len(tasks)} task(s)")

        print("Fetching case endpoints...", flush=True)
        endpoints = get_case_endpoints(air_host, api_token, case_id, case_org_id)
        print(f"  {len(endpoints)} endpoint(s)")

        display_results(case, tasks, endpoints, hub_results)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_file = os.path.join(OUTPUT_DIR, f"evidence_structure_{investigation_id[:8]}.json")
        output = {"investigationId": investigation_id, "case": case, "tasks": tasks, "endpoints": endpoints}
        if hub_results:
            output["investigationHub"] = hub_results
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\nRaw data saved to: {out_file}\n")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

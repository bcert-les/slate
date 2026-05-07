"""
Fetch and display all findings (acquisitions, triage tasks) for a case.

Run from repository root:
  python workflows/investigation_hub/case_findings.py <org_id> <case_id>
"""
import json
import os
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config
from lib.pagination import paginate_get

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


def get_case_details(air_host, api_token, case_id):
    resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
    if not resp.ok:
        raise RuntimeError(f"Failed to get case: HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def _format_duration(milliseconds):
    if not milliseconds:
        return "N/A"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def display_findings(case_details, tasks):
    print(f"\n{'='*80}\nCASE FINDINGS REPORT\n{'='*80}\n")

    result = case_details.get("result", case_details)
    print(f"Case ID:          {result.get('_id') or result.get('id')}")
    print(f"Case Name:        {result.get('name', 'N/A')}")
    print(f"Organization ID:  {result.get('organizationId', 'N/A')}")
    status = result.get("status", "N/A")
    print(f"Status:           {status.upper() if status != 'N/A' else status}")
    print(f"Created:          {result.get('createdAt', 'N/A')}")
    investigation_id = (result.get("metadata") or {}).get("investigationId")
    if investigation_id:
        print(f"Investigation ID: {investigation_id}")

    print(f"\n{'─'*80}\nFINDINGS & EVIDENCE (Total Tasks: {len(tasks)})\n{'─'*80}\n")

    if not tasks:
        print("  No tasks/findings found for this case.\n")
        return

    acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
    triages = [t for t in tasks if t.get("type") == "triage"]
    others = [t for t in tasks if t.get("type") not in ["acquisition", "triage"]]

    for group, label, note in [
        (acquisitions, "ACQUISITIONS", "(Forensic evidence collected from endpoints)"),
        (triages, "TRIAGE TASKS", "(Analysis and hunting tasks performed)"),
        (others, "OTHER TASKS", ""),
    ]:
        if not group:
            continue
        print(f"{label} ({len(group)})")
        if note:
            print(f"   {note}\n")
        for i, task in enumerate(group, 1):
            print(f"   [{i}] {task.get('name', 'Unnamed')}")
            print(f"       Task ID:  {task.get('taskId')}")
            print(f"       Endpoint: {task.get('endpointName', 'N/A')}")
            print(f"       Status:   {task.get('status', 'N/A').upper()}")
            print(f"       Progress: {task.get('progress', 0)}%")
            print(f"       Duration: {_format_duration(task.get('duration'))}")
            print()


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 3:
        print("Usage: python workflows/investigation_hub/case_findings.py <org_id> <case_id>",
              file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1]
    case_id = sys.argv[2]

    try:
        print("Fetching case details...")
        case_details = get_case_details(air_host, api_token, case_id)

        print("Fetching tasks/findings...")
        tasks = paginate_get(
            air_host, api_token, f"/api/public/cases/{case_id}/tasks", verbose=False,
        )

        display_findings(case_details, tasks)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_file = os.path.join(OUTPUT_DIR, f"case_findings_{org_id}_{case_id}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({
                "case": case_details,
                "tasks": tasks,
                "summary": {
                    "total_tasks": len(tasks),
                    "acquisitions": len([t for t in tasks if t.get("type") == "acquisition"]),
                    "triages": len([t for t in tasks if t.get("type") == "triage"]),
                    "exported_at": datetime.now().isoformat(),
                },
            }, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}\nComplete findings saved to: {out_file}\n{'='*80}\n")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

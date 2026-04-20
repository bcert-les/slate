import os
import sys
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from lib.api_client import load_config, api_get
from lib.pagination import paginate_get

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def get_case_details(air_host, api_token, case_id):
    resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
    if not resp.ok:
        raise RuntimeError(f"Failed to get case details: HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def format_duration(milliseconds):
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
    print(f"\n{'='*80}")
    print(f"CASE FINDINGS REPORT")
    print(f"{'='*80}\n")

    result = case_details.get('result', case_details)

    case_id_value = result.get('_id') or result.get('id') or result.get('caseId', 'N/A')
    print(f"Case ID: {case_id_value}")
    print(f"Case Name: {result.get('name', 'N/A')}")
    print(f"Organization ID: {result.get('organizationId', 'N/A')}")

    status = result.get('status', 'N/A')
    print(f"Status: {status.upper() if status != 'N/A' else status}")
    print(f"Created: {result.get('createdAt', 'N/A')}")
    print(f"Total Endpoints: {result.get('totalEndpoints', 0)}")

    category = result.get('category', {})
    if category:
        print(f"Category: {category.get('name', 'N/A')}")

    metadata = result.get('metadata', {})
    if metadata:
        investigation_id = metadata.get('investigationId')
        if investigation_id:
            print(f"Investigation ID: {investigation_id}")

    print(f"\n{'-'*80}")
    print(f"FINDINGS & EVIDENCE (Total Tasks: {len(tasks)})")
    print(f"{'-'*80}\n")

    if not tasks:
        print("  No tasks/findings found for this case.\n")
        return

    acquisitions = [t for t in tasks if t.get('type') == 'acquisition']
    triages = [t for t in tasks if t.get('type') == 'triage']
    others = [t for t in tasks if t.get('type') not in ['acquisition', 'triage']]

    if acquisitions:
        print(f"ACQUISITIONS ({len(acquisitions)})")
        print("   (Forensic evidence collected from endpoints)\n")
        for i, task in enumerate(acquisitions, 1):
            print(f"   [{i}] {task.get('name', 'Unnamed')}")
            print(f"       Task ID: {task.get('taskId')}")
            print(f"       Endpoint: {task.get('endpointName', 'N/A')}")
            print(f"       Type: {task.get('displayType', task.get('type', 'N/A'))}")
            print(f"       Status: {task.get('status', 'N/A').upper()}")
            print(f"       Progress: {task.get('progress', 0)}%")
            print(f"       Duration: {format_duration(task.get('duration'))}")
            print(f"       Created: {task.get('createdAt', 'N/A')}")
            if task.get('createdBy'):
                print(f"       Created By: {task.get('createdBy')}")
            task_metadata = task.get('metadata', {})
            if task_metadata:
                print(f"       Has Case DB: {task_metadata.get('hasCaseDb', False)}")
                print(f"       Has Drone Data: {task_metadata.get('hasDroneData', False)}")
            if task.get('reportUrl'):
                print(f"       Report: {task.get('reportUrl')}")
            print()

    if triages:
        print(f"TRIAGE TASKS ({len(triages)})")
        print("   (Analysis and hunting tasks performed)\n")
        for i, task in enumerate(triages, 1):
            print(f"   [{i}] {task.get('name', 'Unnamed')}")
            print(f"       Task ID: {task.get('taskId')}")
            print(f"       Endpoint: {task.get('endpointName', 'N/A')}")
            print(f"       Status: {task.get('status', 'N/A').upper()}")
            print(f"       Progress: {task.get('progress', 0)}%")
            print(f"       Duration: {format_duration(task.get('duration'))}")
            print(f"       Created: {task.get('createdAt', 'N/A')}")
            if task.get('createdBy'):
                print(f"       Created By: {task.get('createdBy')}")
            if task.get('reportUrl'):
                print(f"       Report: {task.get('reportUrl')}")
            print()

    if others:
        print(f"OTHER TASKS ({len(others)})\n")
        for i, task in enumerate(others, 1):
            print(f"   [{i}] {task.get('name', 'Unnamed')}")
            print(f"       Type: {task.get('type', 'N/A')}")
            print(f"       Status: {task.get('status', 'N/A').upper()}")
            print(f"       Created: {task.get('createdAt', 'N/A')}")
            print()


def save_findings_json(case_details, tasks, org_id, case_id):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(OUTPUT_DIR, f"case_findings_{org_id}_{case_id}.json")

    data = {
        "case": case_details,
        "tasks": tasks,
        "summary": {
            "total_tasks": len(tasks),
            "acquisitions": len([t for t in tasks if t.get('type') == 'acquisition']),
            "triages": len([t for t in tasks if t.get('type') == 'triage']),
            "exported_at": datetime.now().isoformat(),
        },
    }

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*80}")
    print(f"Complete findings saved to: {filename}")
    print(f"{'='*80}\n")


def main():
    air_host, api_token = load_config()

    if len(sys.argv) < 3:
        print("Usage: python3 scripts/api_scripts/case_findings.py <org_id> <case_id>", file=sys.stderr)
        print("\nExample: python3 scripts/api_scripts/case_findings.py 362 C-2026-00001", file=sys.stderr)
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
        save_findings_json(case_details, tasks, org_id, case_id)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

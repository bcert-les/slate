"""
List all tasks for a case.

Endpoint: GET /api/public/cases/{id}/tasks

Run from repository root:
  python api/list_case_tasks.py <case_id>
"""
import json
import os
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.pagination import paginate_get

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


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


def main():
    if len(sys.argv) < 2:
        print("Usage: python api/list_case_tasks.py <case_id> [org_id]", file=sys.stderr)
        sys.exit(1)

    case_id = sys.argv[1].strip()
    org_id = sys.argv[2].strip() if len(sys.argv) > 2 else None

    air_host, api_token = load_config()

    try:
        print(f"GET {air_host}/api/public/cases/{case_id}/tasks")
        tasks = paginate_get(air_host, api_token, f"/api/public/cases/{case_id}/tasks", verbose=False)

        print(f"\nFound {len(tasks)} task(s) in case {case_id}:\n")
        print(f"{'─'*80}")

        acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
        triages = [t for t in tasks if t.get("type") == "triage"]
        others = [t for t in tasks if t.get("type") not in ["acquisition", "triage"]]

        for group, label in [(acquisitions, "ACQUISITIONS"), (triages, "TRIAGE"), (others, "OTHER")]:
            if not group:
                continue
            print(f"\n{label} ({len(group)}):")
            for i, task in enumerate(group, 1):
                print(f"  [{i}] {task.get('name', 'Unnamed')}")
                print(f"      Task ID:  {task.get('taskId')}")
                print(f"      Endpoint: {task.get('endpointName', 'N/A')}")
                print(f"      Status:   {task.get('status', 'N/A')}")
                print(f"      Progress: {task.get('progress', 0)}%")
                print(f"      Duration: {_format_duration(task.get('duration'))}")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_file = os.path.join(OUTPUT_DIR, f"case_tasks_{case_id}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"case_id": case_id, "tasks": tasks}, f, indent=2, ensure_ascii=False)
        print(f"\nSaved to: {out_file}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

"""
Fetch the current status of a task.

Endpoint: GET /api/public/tasks/{id}

Optionally poll until the task reaches a terminal state (completed, failed,
cancelled, error).

Run from repository root:
  python api/get_task.py <task_id>
  python api/get_task.py <task_id> --poll
  python api/get_task.py <task_id> --poll --interval 5 --timeout 600
"""
import argparse
import json
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}


def main():
    p = argparse.ArgumentParser(description="GET /api/public/tasks/{id} — fetch task status.")
    p.add_argument("task_id", help="Task ID")
    p.add_argument("--poll", action="store_true", help="Poll until terminal status.")
    p.add_argument("--interval", type=float, default=10.0, metavar="SECS",
                   help="Seconds between polls (default: 10).")
    p.add_argument("--timeout", type=float, default=3600.0, metavar="SECS",
                   help="Max poll duration in seconds (default: 3600).")
    args = p.parse_args()

    air_host, api_token = load_config()

    try:
        start = time.time()
        while True:
            resp = api_get(air_host, api_token, f"/api/public/tasks/{args.task_id}")
            if not resp.ok:
                print(f"Error: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
                sys.exit(1)

            data = resp.json()
            task = data.get("result", data)
            status = (task.get("status") or "unknown").lower()
            progress = task.get("progress", 0)
            elapsed = time.time() - start

            if args.poll:
                print(f"  [{elapsed:>6.0f}s]  status={status}  progress={progress}%", flush=True)
            else:
                print(f"Task ID:  {args.task_id}")
                print(f"Status:   {status}")
                print(f"Progress: {progress}%")
                print()
                print(json.dumps(task, indent=2, default=str))
                break

            if status in TERMINAL_STATUSES:
                print(f"\nTerminal status: {status}  (elapsed: {elapsed:.0f}s)")
                duration = task.get("duration")
                if duration:
                    print(f"Server duration: {duration / 1000:.1f}s")
                print()
                print(json.dumps(task, indent=2, default=str))
                break

            if elapsed >= args.timeout:
                print(f"\nTimeout ({args.timeout}s) reached without terminal status.", file=sys.stderr)
                sys.exit(1)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

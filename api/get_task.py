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
import warnings

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}


def load_config():
    load_dotenv()
    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")
    if not air_host or not api_token:
        print("Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env", file=sys.stderr)
        sys.exit(1)
    return air_host.rstrip("/"), api_token


def _headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, retries=_MAX_RETRIES, **kwargs):
    backoff = _INITIAL_BACKOFF
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                return resp
            if attempt == retries:
                return resp
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            print(f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(wait)
            backoff *= _BACKOFF_FACTOR
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise
            print(f"\n  Connection error, retrying in {backoff:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(backoff)
            backoff *= _BACKOFF_FACTOR
    raise last_exc


def api_get(air_host, api_token, path, params=None, timeout=_DEFAULT_TIMEOUT,
            retries=_MAX_RETRIES, extra_headers=None):
    url = f"{air_host}{path}"
    headers = dict(_headers(api_token))
    if extra_headers:
        headers.update(extra_headers)
    return _request_with_retry(
        requests.get, url,
        headers=headers, params=params, timeout=timeout,
        retries=retries,
    )


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

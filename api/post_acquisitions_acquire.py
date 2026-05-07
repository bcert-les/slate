"""
Assign an acquisition task to one or more endpoints.

Endpoint: POST /api/public/acquisitions/acquire

Run from repository root:
  python api/post_acquisitions_acquire.py --help

Body shape reference:
  {
    "caseId": "<case_id>",
    "acquisitionProfileId": "<profile_id_or_preset>",
    "droneConfig": {"autoPilot": false, "enabled": false},
    "taskConfig": {"choice": "use-policy"},
    "filter": {"endpointIds": ["<id>"], "organizationIds": [<org_int>]}
  }
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


def api_post(air_host, api_token, path, body=None, params=None,
             timeout=_DEFAULT_TIMEOUT, retries=_MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.post, url,
        headers=_headers(api_token), json=body or {}, params=params, timeout=timeout,
        retries=retries,
    )


def main():
    p = argparse.ArgumentParser(
        description="POST /api/public/acquisitions/acquire — assign acquisition task.",
    )
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument("case_id", help="Case ID to attach the task to")
    p.add_argument("profile_id", help="Acquisition profile ID (or preset slug like 'quick')")
    p.add_argument("endpoint_ids", nargs="+", metavar="ENDPOINT_ID",
                   help="One or more endpoint asset IDs")
    p.add_argument("--dry-run", action="store_true", help="Print body only; do not POST.")
    args = p.parse_args()

    air_host, api_token = load_config()

    body = {
        "caseId": args.case_id,
        "droneConfig": {"autoPilot": False, "enabled": False},
        "taskConfig": {"choice": "use-policy"},
        "acquisitionProfileId": args.profile_id,
        "filter": {
            "endpointIds": list(args.endpoint_ids),
            "organizationIds": [int(args.org_id)],
        },
    }

    print(f"POST {air_host}/api/public/acquisitions/acquire")
    print(f"\nRequest body:")
    print(json.dumps(body, indent=2))

    if args.dry_run:
        print("\n[DRY RUN] No request sent.")
        return

    try:
        resp = api_post(air_host, api_token, "/api/public/acquisitions/acquire", body=body)
        print(f"\nHTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str)[:3000])
        except Exception:
            print(resp.text[:2000])
        if not resp.ok:
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

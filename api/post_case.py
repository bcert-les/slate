"""
Create a Binalyze AIR case.

Endpoint: POST /api/public/cases

Optional --extra-json merges additional fields into the POST body (e.g. category,
visibility override). Default visibility is public-to-organization.

Run from repository root:
  python api/post_case.py <org_id> --name "Investigation title"
  python api/post_case.py <org_id> --name "Investigation title" --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone

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


def _first_id(d: dict, *keys: str, default=None):
    """Return the first not-None value for *keys* in *d*.

    Using ``or`` to chain .get() calls silently drops 0 because Python treats
    0 as falsy.  This helper only skips None, so numeric ID 0 is preserved.
    """
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _default_case_name() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"API case - {ts}"


def _parse_extra_json(raw: str | None) -> dict:
    if not raw or not str(raw).strip():
        return {}
    path = os.path.expanduser(str(raw).strip())
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("--extra-json must be a JSON object or path to a JSON file containing an object.")
    return data


def main() -> None:
    p = argparse.ArgumentParser(description="Create a Binalyze AIR case (POST /api/public/cases).")
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument(
        "--name",
        dest="case_name",
        default=None,
        help='Case title (default: "API case - <UTC timestamp>")',
    )
    p.add_argument(
        "--extra-json",
        metavar="STR_OR_PATH",
        default=None,
        help="Merge extra fields into the POST body (JSON string or path to .json object).",
    )
    p.add_argument("--dry-run", action="store_true", help="Print request body only; do not POST.")
    p.add_argument("--print-json", action="store_true", help="Print full JSON response to stdout.")
    args = p.parse_args()

    air_host, api_token = load_config()
    org_id = args.org_id.strip()
    case_name = (args.case_name or "").strip() or _default_case_name()

    print(f"Host:             {air_host}")
    print(f"Organization ID:  {org_id}")
    print(f"Case name:        {case_name}")

    body: dict = {
        "name": case_name,
        "organizationId": org_id,
        "visibility": "public-to-organization",
    }
    extra = _parse_extra_json(args.extra_json)
    overlap = set(body.keys()) & set(extra.keys())
    if overlap:
        print(f"Warning: --extra-json overwrites keys: {sorted(overlap)}", file=sys.stderr)
    body.update(extra)

    print(f"\nPOST /api/public/cases")
    print(json.dumps(body, indent=2))

    if args.dry_run:
        print("\n[DRY RUN] No request sent.")
        return

    resp = api_post(air_host, api_token, "/api/public/cases", body=body)
    if not resp.ok:
        print(f"\nError: HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text[:2000], file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    case = data.get("result", data)
    cid = _first_id(case, "_id", "id", "caseId")
    print(f"\nCreated case ID: {cid}")
    print(f"Status:          {case.get('status', 'N/A')}")
    if args.print_json:
        print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

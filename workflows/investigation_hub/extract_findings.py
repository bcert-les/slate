"""
Probe case and Investigation Hub API endpoints to discover what data is available.

Automatically looks up the investigation ID from a case, tests each endpoint,
and reports which ones return data. Useful for exploring what a case contains
before running a more targeted download.

Run from repository root:
  python workflows/investigation_hub/extract_findings.py <org_id> <case_id>
"""
import argparse
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

_LOG = logging.getLogger("updraft")


def _setup_log(path) -> None:
    """Attach a file handler to the 'updraft' logger.

    path=None  → no-op
    path=True  → auto-generate timestamped file under output/logs/
    path=str   → write to that path
    """
    if not path:
        return
    if path is True:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        script = os.path.splitext(os.path.basename(__file__))[0]
        path = os.path.join(_root, "output", "logs", f"{script}_{ts}.log")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    _handler = logging.FileHandler(path, encoding="utf-8")
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _LOG.setLevel(logging.DEBUG)
    _LOG.addHandler(_handler)
    _LOG.info("Log started  script=%s  args=%s", os.path.basename(__file__), sys.argv[1:])
    print(f"  Logging to: {path}", flush=True)


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
                if not resp.ok:
                    _LOG.warning("HTTP %s  url=%s  body=%s", resp.status_code, url, resp.text[:500])
                return resp
            if attempt == retries:
                _LOG.error("HTTP %s after %d attempts  url=%s  body=%s",
                           resp.status_code, retries + 1, url, resp.text[:500])
                return resp
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff
            _LOG.warning("HTTP %s retrying in %.1fs (attempt %d/%d)  url=%s",
                         resp.status_code, wait, attempt + 1, retries + 1, url)
            print(f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(wait)
            backoff *= _BACKOFF_FACTOR
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                _LOG.error("Connection failed after %d attempts  url=%s",
                           retries + 1, url, exc_info=True)
                raise
            _LOG.warning("Connection error (attempt %d/%d)  url=%s  error=%s",
                         attempt + 1, retries + 1, url, exc)
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


def api_post(air_host, api_token, path, body=None, params=None,
             timeout=_DEFAULT_TIMEOUT, retries=_MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.post, url,
        headers=_headers(api_token), json=body or {}, params=params, timeout=timeout,
        retries=retries,
    )


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
    data, _ = try_get(air_host, api_token, f"/api/public/cases/{case_id}")
    if data is None:
        return None
    result = data.get("result", data)
    return (result.get("metadata") or {}).get("investigationId")


def probe_endpoints(air_host, api_token, org_id, case_id, investigation_id):
    endpoints = [
        ("GET",  f"/api/public/cases/{case_id}", {"filter[organizationIds]": org_id}),
        ("GET",  f"/api/public/cases/{case_id}/endpoints",
         {"filter[organizationIds]": org_id, "page": 1, "pageSize": 100}),
        ("GET",  f"/api/public/cases/{case_id}/tasks", {"page": 1, "pageSize": 100}),
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


def main():
    parser = argparse.ArgumentParser(
        description="Probe case and Investigation Hub endpoints to discover available data."
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("case_id", help="Case ID")
    parser.add_argument(
        "--log", metavar="PATH", nargs="?", const=True,
        help="Write a debug log to PATH (omit PATH to auto-generate under output/logs/).",
    )
    args = parser.parse_args()

    air_host, api_token = load_config()
    _setup_log(args.log)
    _LOG.info("=== extract_findings started  host=%s  org=%s  case=%s",
              air_host, args.org_id, args.case_id)

    org_id = args.org_id
    case_id = args.case_id

    try:
        print(f"Looking up investigation ID for case {case_id}...", flush=True)
        investigation_id = get_investigation_id(air_host, api_token, case_id)
        if investigation_id:
            print(f"  Investigation ID: {investigation_id}\n")
        else:
            print("  No investigation ID — skipping Investigation Hub endpoints.\n")

        endpoints_data = probe_endpoints(air_host, api_token, org_id, case_id, investigation_id)
        display_findings(endpoints_data)

        if endpoints_data:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_file = os.path.join(OUTPUT_DIR, f"findings_org{org_id}_case{case_id}.json")
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(endpoints_data, f, indent=2, ensure_ascii=False)
            print(f"Data saved to: {out_file}")
    except Exception as e:
        _LOG.exception("Unhandled error: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

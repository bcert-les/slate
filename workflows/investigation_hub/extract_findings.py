"""
Probe case and Investigation Hub API endpoints to discover what data is available.

Automatically looks up the investigation ID from a case, tests each endpoint,
and reports which ones return data. Useful for exploring what a case contains
before running a more targeted download.

Run from repository root:
  python workflows/investigation_hub/extract_findings.py <org_id> <case_id>
"""
import json
import os
import sys
import time
import warnings

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
    air_host, api_token = load_config()

    if len(sys.argv) < 3:
        print("Usage: python workflows/investigation_hub/extract_findings.py <org_id> <case_id>",
              file=sys.stderr)
        sys.exit(1)

    org_id = sys.argv[1]
    case_id = sys.argv[2]

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
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

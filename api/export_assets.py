"""
Download the bulk asset export for an organization.

Endpoint: GET /api/public/assets/export

The response may be JSON, CSV text, or binary; output encoding is inferred
from Content-Type unless --out is specified explicitly.

Run from repository root:
  python api/export_assets.py <org_id>
  python api/export_assets.py <org_id> --out output/my_export.csv
  python api/export_assets.py <org_id> --param format=csv
"""
import argparse
import json
import os
import re
import sys
import time
import warnings

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


def _suffix_from_content_type(ct: str) -> str:
    if not ct:
        return ".bin"
    ct_lower = ct.split(";")[0].strip().lower()
    if "json" in ct_lower:
        return ".json"
    if "csv" in ct_lower or ct_lower == "text/plain":
        return ".csv"
    if "zip" in ct_lower:
        return ".zip"
    return ".bin"


def _parse_param_pairs(pairs):
    params = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --param (expected key=value): {raw!r}")
        key, value = raw.split("=", 1)
        params[key.strip()] = value
    return params


def main():
    parser = argparse.ArgumentParser(
        description="GET /api/public/assets/export — bulk asset export for an organization.",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--out", metavar="PATH",
                        help="Output file path (default: output/assets_export_org_<org_id>.<ext>)")
    parser.add_argument("--param", dest="params_kv", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="Extra query parameter (repeatable).")
    parser.add_argument("--accept", default="*/*", metavar="VALUE",
                        help='Accept header value (default: "*/*")')
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()

    try:
        extra = _parse_param_pairs(args.params_kv)
        params = {"filter[organizationIds]": org_id, **extra}
        id_slug = _safe_org_slug(org_id)

        print(f"GET {air_host}/api/public/assets/export  (org={org_id})")
        resp = api_get(air_host, api_token, "/api/public/assets/export",
                       params=params,
                       extra_headers={"Accept": args.accept})
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        ct = resp.headers.get("Content-Type", "")
        suffix = _suffix_from_content_type(ct)

        out_path = args.out or os.path.join(
            _PROJECT_ROOT, "output", f"assets_export_org_{id_slug}{suffix}")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        if "json" in ct.lower():
            try:
                data = resp.json()
            except ValueError:
                data = None
            if data is not None:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                with open(out_path, "wb") as f:
                    f.write(resp.content)
        elif "csv" in ct.lower() or ct.lower().startswith("text/"):
            text = resp.content.decode(resp.encoding or "utf-8", errors="replace")
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        else:
            with open(out_path, "wb") as f:
                f.write(resp.content)

        print(f"Wrote: {out_path}  ({ct or 'unknown content-type'})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

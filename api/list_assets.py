"""
List all assets (endpoints) for an organization.

Endpoint: GET /api/public/assets

Writes full API objects to JSON and a CSV whose columns are the union of all
top-level asset keys (nested dicts/lists are JSON-encoded in cells).

Run from repository root:
  python api/list_assets.py <org_id>
  python api/list_assets.py <org_id> --json output/my_assets.json --csv output/my_assets.csv --quiet
"""
import argparse
import csv
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
_MAX_ITERATIONS = 1000


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


def _entity_ids_fingerprint(entities):
    if not entities:
        return ()
    ids = []
    for row in entities:
        if isinstance(row, dict):
            oid = _first_id(row, "_id", "id", "endpointId")
            if oid is not None:
                ids.append(str(oid))
    return tuple(sorted(ids))


def paginate_get(air_host, api_token, path, params=None, page_size=100, verbose=True):
    base_params = dict(params or {})
    all_entities = []
    page = 1
    seen_pages = set()
    seen_fingerprints = set()

    while len(seen_pages) < _MAX_ITERATIONS:
        if page in seen_pages:
            if verbose:
                print(f"\nDetected loop at page {page}, stopping.")
            break
        seen_pages.add(page)

        request_params = {**base_params, "page": page, "pageSize": page_size}
        if verbose:
            print(f"Fetching page {page}...", end=" ", flush=True)

        resp = api_get(air_host, api_token, path, params=request_params)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        if verbose:
            print("OK")

        data = resp.json()

        result = data.get("result") if isinstance(data, dict) else None
        if result and isinstance(result, dict) and "entities" in result:
            entities = result.get("entities") or []
            if not entities:
                break

            fp = _entity_ids_fingerprint(entities)
            if fp and fp in seen_fingerprints:
                if verbose:
                    print(
                        f"\nDetected repeated entity set on page {page} "
                        f"(API may ignore page cursor); stopping.",
                        file=sys.stderr,
                    )
                break
            if fp:
                seen_fingerprints.add(fp)

            all_entities.extend(entities)

            total_pages = result.get("totalPageCount")
            current_page = result.get("currentPage", page)

            if total_pages and current_page >= total_pages:
                break

            next_page = result.get("nextPage")
            if next_page and next_page != page:
                page = next_page
                continue
            elif total_pages and page < total_pages:
                page += 1
                continue
            else:
                break

        elif isinstance(data, list):
            all_entities.extend(data)
            break
        elif isinstance(data, dict) and "entities" in data:
            all_entities.extend(data["entities"])
            break
        else:
            raise ValueError(
                f"Unexpected response format: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

    return all_entities


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


def _cell_csv(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def write_json(path, org_id, assets):
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"organizationId": org_id, "count": len(assets), "assets": assets},
                  f, indent=2, ensure_ascii=False)


def write_csv(path, assets):
    _ensure_parent_dir(path)
    if not assets:
        open(path, "w").close()
        return
    keys: set = set()
    for row in assets:
        if isinstance(row, dict):
            keys.update(row.keys())
    fieldnames = sorted(keys)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in assets:
            if isinstance(row, dict):
                writer.writerow({k: _cell_csv(row.get(k)) for k in fieldnames})


def main():
    parser = argparse.ArgumentParser(
        description="GET /api/public/assets — list all assets for an organization (JSON + CSV).",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--json", dest="json_path", metavar="PATH",
                        help="Output JSON path (default: output/assets_org_<org_id>.json)")
    parser.add_argument("--csv", dest="csv_path", metavar="PATH",
                        help="Output CSV path (default: output/assets_org_<org_id>.csv)")
    parser.add_argument("--quiet", action="store_true", help="Less console output")
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        id_slug = _safe_org_slug(org_id)
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        json_path = args.json_path or os.path.join(out_dir, f"assets_org_{id_slug}.json")
        csv_path = args.csv_path or os.path.join(out_dir, f"assets_org_{id_slug}.csv")

        if verbose:
            print(f"Organization ID: {org_id}")
            print(f"GET {air_host}/api/public/assets")

        assets = paginate_get(
            air_host, api_token, "/api/public/assets",
            params={"filter[organizationIds]": org_id},
            verbose=verbose,
        )

        if verbose:
            print(f"\nRetrieved {len(assets)} asset(s).")

        write_json(json_path, org_id, assets)
        write_csv(csv_path, assets)

        if verbose:
            print(f"Wrote JSON: {json_path}")
            print(f"Wrote CSV:  {csv_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

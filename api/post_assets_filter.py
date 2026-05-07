"""
Filter assets using a server-side POST body.

Endpoint: POST /api/public/assets/filter

Default body when --body-file and --preset are omitted:
  {"filter": {"organizationIds": ["<org_id>"]}}

Use --preset isolated for isolated-only inventory, or --body-file for a
fully custom filter body.

Run from repository root:
  python api/post_assets_filter.py <org_id>
  python api/post_assets_filter.py <org_id> --preset isolated
  python api/post_assets_filter.py <org_id> --body-file my_filter.json
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import warnings
from typing import Any, Dict, List, Union

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

_ISOLATION_STATUS_ACTIVE = frozenset({"isolated", "isolating"})


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


def _entity_ids_fingerprint(entities):
    if not entities:
        return ()
    ids = []
    for row in entities:
        if isinstance(row, dict):
            oid = row.get("_id") or row.get("id") or row.get("endpointId")
            if oid is not None:
                ids.append(str(oid))
    return tuple(sorted(ids))


def paginate_post(air_host, api_token, path, body, params=None, page_size=100, verbose=True):
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

        resp = api_post(air_host, api_token, path, body=body, params=request_params)
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


def _parse_filter_value(raw: str) -> Union[str, bool]:
    low = raw.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return raw.strip()


def isolated_assets_filter_body(org_id: Union[str, int]) -> dict:
    """Preset: server-side filter for isolated assets. Keys/values configurable via env."""
    key = (os.getenv("BINALYZE_ISOLATED_FILTER_KEY") or "isolationStatus").strip() or "isolationStatus"
    val_raw = os.getenv("BINALYZE_ISOLATED_FILTER_VALUE", "isolated")
    value = _parse_filter_value(str(val_raw))
    flt: Dict[str, Any] = {"organizationIds": [str(org_id)], key: value}
    return {"filter": flt}


def filter_assets_client_isolated_only(assets: List[dict]) -> List[dict]:
    """Keep rows that are isolated or mid-isolation (client-side pass after server filter)."""
    out: List[dict] = []
    for a in assets:
        st = str(a.get("isolationStatus") or "").strip().lower()
        if st in _ISOLATION_STATUS_ACTIVE:
            out.append(a)
            continue
        if a.get("isolated") is True:
            out.append(a)
            continue
        iso = a.get("isolation")
        if isinstance(iso, dict) and iso.get("enabled") is True:
            out.append(a)
            continue
    return out


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
        description="POST /api/public/assets/filter — server-side asset filter.",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--preset", choices=("isolated",), default=None,
                        help="Built-in filter: 'isolated' returns isolated assets.")
    parser.add_argument("--body-file", metavar="PATH",
                        help="JSON file to use as the POST body (overrides --preset).")
    parser.add_argument("--page-size", type=int, default=100, metavar="N")
    parser.add_argument("--json", dest="json_path", metavar="PATH")
    parser.add_argument("--csv", dest="csv_path", metavar="PATH")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)
    if args.body_file and args.preset:
        print("Use either --body-file or --preset, not both.", file=sys.stderr)
        sys.exit(1)

    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            body = json.load(f)
    elif args.preset == "isolated":
        body = isolated_assets_filter_body(org_id)
    else:
        body = {"filter": {"organizationIds": [org_id]}}

    air_host, api_token = load_config()
    verbose = not args.quiet

    try:
        id_slug = _safe_org_slug(org_id)
        out_dir = os.path.join(_PROJECT_ROOT, "output")
        suffix = f"_preset_{args.preset}" if args.preset else ""
        json_path = args.json_path or os.path.join(out_dir, f"assets_filter_org_{id_slug}{suffix}.json")
        csv_path = args.csv_path or os.path.join(out_dir, f"assets_filter_org_{id_slug}{suffix}.csv")

        if verbose:
            print(f"Organization ID: {org_id}")
            print(f"POST {air_host}/api/public/assets/filter")
            print(json.dumps(body, indent=2))

        assets = paginate_post(
            air_host, api_token, "/api/public/assets/filter",
            body=body, page_size=args.page_size, verbose=verbose,
        )

        if args.preset == "isolated":
            raw_n = len(assets)
            assets = filter_assets_client_isolated_only(assets)
            if verbose and raw_n != len(assets):
                print(f"\nClient-side filter: kept {len(assets)} of {raw_n} with isolationStatus "
                      f"isolated or isolating.")

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

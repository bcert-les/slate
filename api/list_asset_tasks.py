"""
List all tasks for a specific asset (endpoint).

Endpoint: GET /api/public/assets/{id}/tasks

Run from repository root:
  python api/list_asset_tasks.py <asset_id_or_hostname> <org_id>
"""
import json
import os
import sys
import time
import warnings
from typing import List

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

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


class AssetResolveError(Exception):
    """Could not resolve exactly one asset for an identifier."""


def _primary_host_label(name):
    if name is None:
        return ""
    s = str(name).strip().lower()
    if not s:
        return ""
    return s.split(".", 1)[0]


def find_asset_strict(air_host: str, api_token: str, identifier: str, org_id: str) -> dict:
    """Resolve a single asset by ID or hostname search. Raises AssetResolveError on ambiguity."""
    resp = api_get(air_host, api_token, f"/api/public/assets/{identifier}")
    if resp.ok:
        asset = resp.json().get("result", resp.json())
        if asset.get("_id"):
            return asset

    params = {"filter[organizationIds]": org_id, "search": identifier}
    assets: List[dict] = paginate_get(
        air_host, api_token, "/api/public/assets", params=params, verbose=False
    )
    if not assets:
        raise AssetResolveError(f"No endpoint found matching '{identifier}'")

    ident_norm = identifier.strip().lower()
    exact = [a for a in assets if (a.get("name") or "").strip().lower() == ident_norm]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = [a.get("name") for a in exact[:10]]
        raise AssetResolveError(
            f"Multiple endpoints with hostname '{identifier}': {names!r}"
        )

    by_label = [a for a in assets if _primary_host_label(a.get("name")) == ident_norm]
    if len(by_label) == 1:
        return by_label[0]
    if len(by_label) > 1:
        names = [a.get("name") for a in by_label[:10]]
        raise AssetResolveError(
            f"Multiple endpoints share hostname label {identifier!r}: {names!r}"
        )

    if len(assets) == 1:
        return assets[0]

    lines = [
        f"Ambiguous search for '{identifier}' ({len(assets)} matches). "
        "Use asset _id or a unique hostname."
    ]
    for a in assets[:15]:
        lines.append(
            f"  - {a.get('name', '?')}  _id={a.get('_id')}  {a.get('ipAddress', '')}"
        )
    if len(assets) > 15:
        lines.append(f"  ... and {len(assets) - 15} more")
    raise AssetResolveError("\n".join(lines))


def main():
    if len(sys.argv) < 3:
        print("Usage: python api/list_asset_tasks.py <asset_id_or_hostname> <org_id>", file=sys.stderr)
        sys.exit(1)

    identifier = sys.argv[1].strip()
    org_id = sys.argv[2].strip()
    air_host, api_token = load_config()

    try:
        try:
            asset = find_asset_strict(air_host, api_token, identifier, org_id)
            asset_id = str(asset.get("_id") or asset.get("id"))
            hostname = asset.get("name", identifier)
        except AssetResolveError as e:
            print(f"Error resolving asset: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Endpoint: {hostname} ({asset_id})")
        print(f"GET {air_host}/api/public/assets/{asset_id}/tasks")

        resp = api_get(air_host, api_token, f"/api/public/assets/{asset_id}/tasks")
        if not resp.ok:
            print(f"Error: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        inner = data.get("result", data)
        tasks = inner if isinstance(inner, list) else inner.get("entities", [])

        print(f"\nFound {len(tasks)} task(s):\n")
        for i, task in enumerate(tasks, 1):
            print(f"  [{i}] {task.get('name', 'Unnamed')}")
            print(f"      Type:   {task.get('type', 'N/A')}")
            print(f"      Status: {task.get('status', 'N/A')}")
            print(f"      Created: {task.get('createdAt', 'N/A')}")

        print()
        print(json.dumps(tasks, indent=2, default=str))

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

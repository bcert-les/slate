#!/usr/bin/env python3

import argparse
import os
import sys
import time
import requests
from dotenv import load_dotenv

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ITERATIONS = 1000


def api_url(host, path):
    return host.rstrip("/") + path


def request(session, method, host, path, **kwargs):
    resp = session.request(method, api_url(host, path), timeout=60, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed: {resp.status_code} {resp.text}")
    if resp.text:
        return resp.json()
    return None


def _headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, retries=_MAX_RETRIES, **kwargs):
    backoff = _INITIAL_BACKOFF

    for attempt in range(retries + 1):
        resp = method(url, **kwargs)

        if resp.status_code not in _RETRYABLE_STATUS_CODES:
            return resp

        if attempt == retries:
            return resp

        wait = backoff
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                pass

        print(
            f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
            f"(attempt {attempt + 1}/{retries})...",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(wait)
        backoff *= _BACKOFF_FACTOR


def api_get(host, token, path, params=None, timeout=_DEFAULT_TIMEOUT):
    url = api_url(host, path)
    return _request_with_retry(
        requests.get,
        url,
        headers=_headers(token),
        params=params,
        timeout=timeout,
    )


def _entity_ids_fingerprint(entities):
    ids = []

    for row in entities or []:
        if isinstance(row, dict):
            oid = row.get("_id") or row.get("id") or row.get("caseId")
            if oid is not None:
                ids.append(str(oid))

    return tuple(sorted(ids))


def paginate_get(host, token, path, params=None, page_size=100, verbose=True):
    base_params = dict(params or {})
    all_entities = []
    page = 1
    seen_pages = set()
    seen_fingerprints = set()

    while len(seen_pages) < _MAX_ITERATIONS:
        if page in seen_pages:
            break

        seen_pages.add(page)

        request_params = {
            **base_params,
            "page": page,
            "pageSize": page_size,
        }

        if verbose:
            print(f"Fetching page {page}...", end=" ", flush=True)

        resp = api_get(host, token, path, params=request_params)

        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        if verbose:
            print("OK")

        data = resp.json()
        result = data.get("result") if isinstance(data, dict) else None

        if isinstance(result, dict) and isinstance(result.get("entities"), list):
            entities = result.get("entities") or []

            if not entities:
                break

            fp = _entity_ids_fingerprint(entities)
            if fp and fp in seen_fingerprints:
                break

            if fp:
                seen_fingerprints.add(fp)

            all_entities.extend(entities)

            total_pages = result.get("totalPageCount")
            current_page = result.get("currentPage", page)
            next_page = result.get("nextPage")

            if total_pages and current_page >= total_pages:
                break

            if next_page and next_page != page:
                page = next_page
            elif total_pages and page < total_pages:
                page += 1
            else:
                break

        elif isinstance(data, list):
            all_entities.extend(data)
            break

        elif isinstance(data, dict) and isinstance(data.get("entities"), list):
            all_entities.extend(data["entities"])
            break

        else:
            raise ValueError(
                f"Unexpected response format: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

    return all_entities


def is_closed(case):
    status = str(case.get("status") or case.get("state") or "").lower()
    return status in {"closed", "resolved", "archived"}


def main():
    parser = argparse.ArgumentParser(
        description="Safely reset a Binalyze AIR training org by closing open cases."
    )
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--yes", action="store_true", help="Actually perform changes.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. Default behavior.")
    args = parser.parse_args()

    load_dotenv()

    host = os.getenv("BINALYZE_AIR_HOST")
    token = os.getenv("BINALYZE_API_TOKEN")

    if not host or not token:
        print("Missing BINALYZE_AIR_HOST or BINALYZE_API_TOKEN in .env", file=sys.stderr)
        sys.exit(1)

    dry_run = not args.yes

    session = requests.Session()
    session.headers.update(_headers(token))

    print(f"Org reset target: {args.org_id}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE CHANGES'}")

    params = {
        "filter[organizationIds]": args.org_id,
        "filter[status]": "open",
    }

    cases = paginate_get(
        host,
        token,
        "/api/public/cases",
        params=params,
        page_size=100,
    )

    open_cases = [case for case in cases if not is_closed(case)]

    print(f"\nCases found: {len(cases)}")
    print(f"Open cases to close: {len(open_cases)}")

    for case in open_cases:
        case_id = case.get("_id") or case.get("id") or case.get("caseId")
        name = case.get("name") or case.get("title") or "<unnamed>"

        print(f"  - close case: {name} ({case_id})")

        if not dry_run:
            request(
                session,
                "POST",
                host,
                f"/api/public/cases/{case_id}/close",
                json={},
            )

    print("\nDone.")


if __name__ == "__main__":
    main()
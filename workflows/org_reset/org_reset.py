#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

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
            if not resp.ok:
                _LOG.warning("HTTP %s  url=%s  body=%s", resp.status_code, url, resp.text[:500])
            return resp

        if attempt == retries:
            _LOG.error("HTTP %s after %d attempts  url=%s", resp.status_code, retries + 1, url)
            return resp

        wait = backoff
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                pass

        _LOG.warning("HTTP %s retrying in %.1fs (attempt %d/%d)  url=%s",
                     resp.status_code, wait, attempt + 1, retries + 1, url)
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


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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


def _entity_ids_fingerprint(entities):
    ids = []

    for row in entities or []:
        if isinstance(row, dict):
            oid = _first_id(row, "_id", "id", "caseId")
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
    parser.add_argument(
        "--log", metavar="PATH", nargs="?", const=True,
        help="Write a debug log to PATH (omit PATH to auto-generate under output/logs/).",
    )
    args = parser.parse_args()

    load_dotenv()
    _setup_log(args.log)

    host = os.getenv("BINALYZE_AIR_HOST")
    token = os.getenv("BINALYZE_API_TOKEN")

    if not host or not token:
        print("Missing BINALYZE_AIR_HOST or BINALYZE_API_TOKEN in .env", file=sys.stderr)
        sys.exit(1)

    _LOG.info("=== org_reset started  host=%s  org=%s", host, args.org_id)
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
        case_id = _first_id(case, "_id", "id", "caseId")
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
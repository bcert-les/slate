"""
Fetch and display all findings (acquisitions, triage tasks) for a case.

Run from repository root:
  python workflows/investigation_hub/case_findings.py <org_id> <case_id>
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
_MAX_ITERATIONS = 1000

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


def get_case_details(air_host, api_token, case_id):
    resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
    if not resp.ok:
        raise RuntimeError(f"Failed to get case: HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def _format_duration(milliseconds):
    if not milliseconds:
        return "N/A"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def display_findings(case_details, tasks):
    print(f"\n{'='*80}\nCASE FINDINGS REPORT\n{'='*80}\n")

    result = case_details.get("result", case_details)
    print(f"Case ID:          {_first_id(result, '_id', 'id')}")
    print(f"Case Name:        {result.get('name', 'N/A')}")
    print(f"Organization ID:  {result.get('organizationId', 'N/A')}")
    status = result.get("status", "N/A")
    print(f"Status:           {status.upper() if status != 'N/A' else status}")
    print(f"Created:          {result.get('createdAt', 'N/A')}")
    investigation_id = (result.get("metadata") or {}).get("investigationId")
    if investigation_id:
        print(f"Investigation ID: {investigation_id}")

    print(f"\n{'─'*80}\nFINDINGS & EVIDENCE (Total Tasks: {len(tasks)})\n{'─'*80}\n")

    if not tasks:
        print("  No tasks/findings found for this case.\n")
        return

    acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
    triages = [t for t in tasks if t.get("type") == "triage"]
    others = [t for t in tasks if t.get("type") not in ["acquisition", "triage"]]

    for group, label, note in [
        (acquisitions, "ACQUISITIONS", "(Forensic evidence collected from endpoints)"),
        (triages, "TRIAGE TASKS", "(Analysis and hunting tasks performed)"),
        (others, "OTHER TASKS", ""),
    ]:
        if not group:
            continue
        print(f"{label} ({len(group)})")
        if note:
            print(f"   {note}\n")
        for i, task in enumerate(group, 1):
            print(f"   [{i}] {task.get('name', 'Unnamed')}")
            print(f"       Task ID:  {task.get('taskId')}")
            print(f"       Endpoint: {task.get('endpointName', 'N/A')}")
            print(f"       Status:   {task.get('status', 'N/A').upper()}")
            print(f"       Progress: {task.get('progress', 0)}%")
            print(f"       Duration: {_format_duration(task.get('duration'))}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display all findings for a case."
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
    _LOG.info("=== case_findings started  host=%s  org=%s  case=%s",
              air_host, args.org_id, args.case_id)

    org_id = args.org_id
    case_id = args.case_id

    try:
        print("Fetching case details...")
        case_details = get_case_details(air_host, api_token, case_id)

        print("Fetching tasks/findings...")
        tasks = paginate_get(
            air_host, api_token, f"/api/public/cases/{case_id}/tasks", verbose=False,
        )

        display_findings(case_details, tasks)

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_file = os.path.join(OUTPUT_DIR, f"case_findings_{org_id}_{case_id}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({
                "case": case_details,
                "tasks": tasks,
                "summary": {
                    "total_tasks": len(tasks),
                    "acquisitions": len([t for t in tasks if t.get("type") == "acquisition"]),
                    "triages": len([t for t in tasks if t.get("type") == "triage"]),
                    "exported_at": datetime.now().isoformat(),
                },
            }, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*80}\nComplete findings saved to: {out_file}\n{'='*80}\n")

    except Exception as e:
        _LOG.exception("Unhandled error: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

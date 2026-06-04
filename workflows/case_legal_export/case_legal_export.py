"""
Workflow: Legal case export

Interactive export of DRONE findings and all Investigation Hub evidence tables
for legal handoff: per-table CSVs, supplemental data, chain-of-custody report,
and ZIP archive.

Run from repository root:
  python workflows/case_legal_export/case_legal_export.py
  python workflows/case_legal_export/case_legal_export.py --operator "Jane Doe" --yes
  python workflows/case_legal_export/case_legal_export.py --output-dir output/C-2024-001_20260518T120000Z --no-resume
"""
from __future__ import annotations

import argparse
import base64
import csv
import getpass
import hashlib
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import time
import warnings
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

_SCRIPT_VERSION = "1.0.0"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ITERATIONS = 1000

DEFAULT_PAGE_SIZE = 500
DEFAULT_REQUEST_DELAY = 0.1


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


FINDING_TYPES = ["dangerous", "suspicious", "relevant", "matched", "rare"]
FINDINGS_DEFAULT_FILTER = [
    {"column": "section", "operator": "!=", "value": "__never__"},
]

EXPORT_POLL_INTERVAL = 2.0
EXPORT_POLL_MAX_ATTEMPTS = 60

CHECKPOINT_FILE = ".checkpoint.json"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def load_config() -> Tuple[str, str]:
    load_dotenv()
    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")
    if not air_host or not api_token:
        print("Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env", file=sys.stderr)
        sys.exit(1)
    return air_host.rstrip("/"), api_token


def _headers(api_token: str) -> Dict[str, str]:
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
            print(
                f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                f"(attempt {attempt + 1}/{retries})...",
                file=sys.stderr,
                flush=True,
            )
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
            print(
                f"\n  Connection error, retrying in {backoff:.1f}s "
                f"(attempt {attempt + 1}/{retries})...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(backoff)
            backoff *= _BACKOFF_FACTOR
    raise last_exc


def api_get(air_host, api_token, path, params=None, timeout=_DEFAULT_TIMEOUT, retries=_MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.get,
        url,
        headers=_headers(api_token),
        params=params,
        timeout=timeout,
        retries=retries,
    )


def api_post(air_host, api_token, path, body=None, params=None, timeout=_DEFAULT_TIMEOUT, retries=_MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.post,
        url,
        headers=_headers(api_token),
        json=body or {},
        params=params,
        timeout=timeout,
        retries=retries,
    )


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


def paginate_get(air_host, api_token, path, params=None, page_size=100, verbose=False):
    base_params = dict(params or {})
    all_entities = []
    page = 1
    seen_pages = set()
    seen_fingerprints = set()

    while len(seen_pages) < _MAX_ITERATIONS:
        if page in seen_pages:
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
            if total_pages and page < total_pages:
                page += 1
                continue
            break

        if isinstance(data, list):
            all_entities.extend(data)
            break
        if isinstance(data, dict) and "entities" in data:
            all_entities.extend(data["entities"])
            break
        raise ValueError(f"Unexpected response format from {path}")

    return all_entities


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class Checkpoint:
    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {
            "version": 1,
            "supplemental_done": False,
            "findings_done": False,
            "findings_skip": 0,
            "findings_row_count": 0,
            "evidence_completed": [],
            "evidence_progress": {},
        }
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
                self.data.update(loaded)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def evidence_key(self, platform: str, section: str) -> str:
        return f"{platform}/{section}"

    def is_evidence_done(self, platform: str, section: str) -> bool:
        key = self.evidence_key(platform, section)
        return key in self.data.get("evidence_completed", [])

    def mark_evidence_done(self, platform: str, section: str, row_count: int) -> None:
        key = self.evidence_key(platform, section)
        completed = self.data.setdefault("evidence_completed", [])
        if key not in completed:
            completed.append(key)
        self.data.setdefault("evidence_progress", {}).pop(key, None)
        inv = self.data.setdefault("export_inventory", {})
        inv.setdefault("evidence_tables", {})[key] = row_count
        self.save()

    def get_evidence_skip(self, platform: str, section: str) -> int:
        key = self.evidence_key(platform, section)
        prog = self.data.get("evidence_progress", {}).get(key, {})
        return int(prog.get("skip", 0))

    def set_evidence_progress(self, platform: str, section: str, skip: int, row_count: int) -> None:
        key = self.evidence_key(platform, section)
        self.data.setdefault("evidence_progress", {})[key] = {
            "skip": skip,
            "row_count": row_count,
        }
        self.save()


# ---------------------------------------------------------------------------
# Investigation Hub helpers
# ---------------------------------------------------------------------------

def get_investigation_assets(air_host: str, api_token: str, investigation_id: str) -> Tuple[list, Optional[str]]:
    """
    Returns (assets_data, error_message).
    An empty list with no error means the API succeeded but the investigation has no assets.
    """
    resp = api_get(
        air_host,
        api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/assets",
    )
    if not resp.ok:
        return [], f"HTTP {resp.status_code}: {resp.text[:500]}"
    result = resp.json().get("result")
    if result is None:
        return [], None
    if not isinstance(result, list):
        return [], f"Unexpected assets response type: {type(result).__name__}"
    return result, None


def collect_all_assignment_ids(assets_data: list) -> List[str]:
    ids: List[str] = []
    seen = set()
    for pg in assets_data:
        for asset in pg.get("assets", []):
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid and aid not in seen:
                    seen.add(aid)
                    ids.append(aid)
    return ids


def get_assignment_ids_for_platform(assets_data: list, platform: str) -> List[str]:
    ids: List[str] = []
    for pg in assets_data:
        if pg.get("platform") != platform:
            continue
        for asset in pg.get("assets", []):
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid:
                    ids.append(aid)
    return ids


def build_endpoint_name_map(assets_data: list) -> Dict[str, str]:
    id_to_name: Dict[str, str] = {}
    for platform_group in assets_data:
        for asset in platform_group.get("assets", []):
            eid = asset.get("_id")
            ename = asset.get("name", "Unknown")
            if eid:
                id_to_name[eid] = ename
            for task in asset.get("tasks", []):
                aid = task.get("_id")
                if aid:
                    id_to_name[aid] = ename
    return id_to_name


def get_sections(air_host, api_token, investigation_id: str) -> list:
    resp = api_post(
        air_host,
        api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/sections",
        body={},
    )
    if not resp.ok:
        print(f"  Failed to fetch sections: {resp.status_code}", file=sys.stderr)
        return []
    return resp.json().get("result", [])


def list_all_sections(sections_data: list) -> List[Tuple[str, str, int]]:
    """Return (platform, section_name, count) for every section including empty."""
    out: List[Tuple[str, str, int]] = []
    for pg in sections_data:
        platform = pg.get("platform", "unknown")
        for tg in pg.get("types", []):
            for s in tg.get("sections", []):
                name = s.get("name")
                if name:
                    out.append((platform, name, s.get("count", 0)))
    return sorted(out, key=lambda x: (x[0], x[1]))


def get_evidence_data_structure(air_host, api_token, investigation_id: str) -> Any:
    resp = api_get(
        air_host,
        api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/evidence/data-structure",
    )
    if not resp.ok:
        return None
    return resp.json().get("result")


def extract_columns_from_data_structure(data_structure: Any, platform: str, section: str) -> List[str]:
    """Best-effort column names for an evidence section from data-structure API."""
    if not data_structure:
        return []

    def walk(node, plat=None):
        if isinstance(node, dict):
            if node.get("platform") == platform or plat == platform:
                for tg in node.get("types", []) or []:
                    for sec in tg.get("sections", []) or []:
                        if sec.get("name") == section:
                            cols = sec.get("columns") or sec.get("fields") or []
                            if cols:
                                if isinstance(cols[0], dict):
                                    return [c.get("name") or c.get("field") for c in cols if c]
                                return list(cols)
            for v in node.values():
                found = walk(v, plat or node.get("platform"))
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item, plat)
                if found:
                    return found
        return []

    if isinstance(data_structure, list):
        for item in data_structure:
            found = walk(item)
            if found:
                return [c for c in found if c]
    else:
        found = walk(data_structure)
        if found:
            return [c for c in found if c]
    return []


def sanitize_filename_component(value: str) -> str:
    safe = re.sub(r"[^\w\-.]+", "_", value.strip())
    return safe.strip("_") or "unknown"


def evidence_csv_filename(platform: str, section: str) -> str:
    return f"evidence_{sanitize_filename_component(platform)}_{sanitize_filename_component(section)}.csv"


# ---------------------------------------------------------------------------
# CSV utilities
# ---------------------------------------------------------------------------

def _serialize_row(row: dict) -> dict:
    return {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}


def save_csv_rows(rows: list, filename: str) -> int:
    if not rows:
        return 0
    fieldnames = list(rows[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_serialize_row(row))
    return len(rows)


def save_csv_header_only(filename: str, fieldnames: List[str]) -> None:
    if not fieldnames:
        fieldnames = ["note"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()


def append_csv_rows(rows: list, filename: str, fieldnames: Optional[List[str]] = None) -> List[str]:
    if not rows:
        return fieldnames or []
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    file_exists = os.path.isfile(filename) and os.path.getsize(filename) > 0
    mode = "a" if file_exists else "w"
    with open(filename, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(_serialize_row(row))
    return fieldnames


def save_json_data(data: Any, filename: str) -> int:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if isinstance(data, list):
        return len(data)
    return 1


# ---------------------------------------------------------------------------
# Streaming downloads
# ---------------------------------------------------------------------------

def build_findings_global_filter(assignment_ids: List[str]) -> Dict[str, Any]:
    return {
        "assignmentIds": assignment_ids,
        "findingTypes": FINDING_TYPES,
        "flagIds": [],
        "mitreTechniqueIds": [],
        "mitreTacticIds": [],
        "dateTimeRange": None,
    }


def build_findings_filter_body(assignment_ids: List[str], skip: int, take: int) -> Dict[str, Any]:
    return {
        "globalFilter": build_findings_global_filter(assignment_ids),
        "filter": list(FINDINGS_DEFAULT_FILTER),
        "onlyExcludedFindings": False,
        "skip": skip,
        "take": take,
        "sort": None,
    }


def count_csv_rows(csv_path: str) -> int:
    if not os.path.isfile(csv_path):
        return 0
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        lines = [ln for ln in f if ln.strip()]
    return max(0, len(lines) - 1) if lines else 0


def get_findings_column_headers(air_host: str, api_token: str, investigation_id: str) -> List[str]:
    resp = api_get(
        air_host,
        api_token,
        f"/api/public/investigation-hub/investigations/{investigation_id}/findings/data-structure",
    )
    if not resp.ok:
        return ["note"]
    result = resp.json().get("result")
    cols = extract_columns_from_data_structure(result, "", "")
    if cols:
        return cols
    if isinstance(result, dict):
        for key in ("columns", "fields"):
            if key in result and isinstance(result[key], list):
                items = result[key]
                if items and isinstance(items[0], dict):
                    return [c.get("name") or c.get("field") for c in items if c]
                return list(items)
    return ["note"]

def export_findings_via_server_export(
    air_host: str,
    api_token: str,
    investigation_id: str,
    assignment_ids: List[str],
    csv_path: str,
) -> int:
    """
    Download findings CSV via POST/GET .../findings/export.
    Used when findings/filter is not available on the tenant (HTTP 404).
    """
    hub = f"/api/public/investigation-hub/investigations/{investigation_id}"
    bodies = [
        {
            "globalFilter": build_findings_global_filter(assignment_ids),
            "filter": list(FINDINGS_DEFAULT_FILTER),
        },
        {"filter": list(FINDINGS_DEFAULT_FILTER)},
    ]

    export_url = None
    for body in bodies:
        resp = api_post(air_host, api_token, f"{hub}/findings/export", body=body, timeout=120)
        if resp.ok:
            export_url = (resp.json().get("result") or {}).get("exportUrl")
            if export_url:
                break
        elif resp.status_code not in (400, 404):
            print(
                f"\n  Findings export request failed: {resp.status_code} - {resp.text[:300]}",
                file=sys.stderr,
            )
            return 0

    if not export_url:
        print("\n  Findings export request failed (no exportUrl returned).", file=sys.stderr)
        return 0

    full_url = f"{air_host}{export_url}" if export_url.startswith("/") else export_url
    download_headers = dict(_headers(api_token))
    download_headers["Accept"] = "*/*"

    for attempt in range(EXPORT_POLL_MAX_ATTEMPTS):
        get_resp = _request_with_retry(
            requests.get,
            full_url,
            headers=download_headers,
            timeout=120,
        )
        if get_resp.status_code in (404, 202) or not get_resp.content:
            time.sleep(EXPORT_POLL_INTERVAL)
            continue

        content_type = (get_resp.headers.get("content-type") or "").lower()
        if "json" in content_type:
            try:
                payload = get_resp.json()
            except ValueError:
                payload = {}
            if payload.get("success") is False or payload.get("statusCode", 200) >= 400:
                time.sleep(EXPORT_POLL_INTERVAL)
                continue

        with open(csv_path, "wb") as f:
            f.write(get_resp.content)

        row_count = count_csv_rows(csv_path)
        print(f"  Findings export downloaded ({row_count} rows).    ")
        return row_count

    print("\n  Findings export download timed out waiting for file.", file=sys.stderr)
    return 0


def stream_findings_to_csv(
    air_host: str,
    api_token: str,
    investigation_id: str,
    assignment_ids: List[str],
    csv_path: str,
    checkpoint: Checkpoint,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> int:
    if checkpoint.data.get("findings_done"):
        if os.path.isfile(csv_path):
            return count_csv_rows(csv_path)
        return checkpoint.data.get("findings_row_count", 0)

    skip = checkpoint.data.get("findings_skip", 0)
    total = None
    row_count = checkpoint.data.get("findings_row_count", 0)
    fieldnames: Optional[List[str]] = None
    hub = f"/api/public/investigation-hub/investigations/{investigation_id}"
    use_server_export = checkpoint.data.get("findings_use_server_export")

    print("  Exporting findings...", flush=True)

    if use_server_export:
        row_count = export_findings_via_server_export(
            air_host, api_token, investigation_id, assignment_ids, csv_path,
        )
        checkpoint.data["findings_row_count"] = row_count
        checkpoint.data["findings_export_method"] = "server_export"
        checkpoint.data["findings_done"] = True
        checkpoint.data.setdefault("export_inventory", {})["findings_row_count"] = row_count
        checkpoint.save()
        return row_count

    while True:
        body = build_findings_filter_body(assignment_ids, skip, DEFAULT_PAGE_SIZE)
        resp = api_post(air_host, api_token, f"{hub}/findings/filter", body=body, timeout=120)

        if resp.status_code == 404:
            print(
                "  findings/filter not available on this tenant; using findings/export...",
                flush=True,
            )
            checkpoint.data["findings_use_server_export"] = True
            checkpoint.save()
            return stream_findings_to_csv(
                air_host, api_token, investigation_id, assignment_ids, csv_path, checkpoint,
                request_delay,
            )

        if not resp.ok:
            print(
                f"\n  findings/filter error at skip={skip}: {resp.status_code} - {resp.text[:300]}",
                file=sys.stderr,
            )
            print("  Trying findings/export fallback...", flush=True)
            checkpoint.data["findings_use_server_export"] = True
            checkpoint.save()
            return stream_findings_to_csv(
                air_host, api_token, investigation_id, assignment_ids, csv_path, checkpoint,
                request_delay,
            )

        result = resp.json().get("result", {})
        entities = result.get("entities", [])
        if total is None:
            total = result.get("totalCount", 0)
            print(f"  Total findings available: {total}")
            checkpoint.data["findings_export_method"] = "filter"
            if skip > 0:
                print(f"  Resuming findings from offset {skip}")

        if not entities:
            break

        fieldnames = append_csv_rows(entities, csv_path, fieldnames)
        batch = len(entities)
        skip += batch
        row_count += batch
        checkpoint.data["findings_skip"] = skip
        checkpoint.data["findings_row_count"] = row_count
        checkpoint.save()

        effective = total or 0
        print(f"  Findings {row_count}/{effective}...", end="\r", flush=True)

        if skip >= (total or 0):
            break
        if request_delay > 0:
            time.sleep(request_delay)

    print(f"  Findings {row_count}/{total or 0} exported.    ")
    if row_count == 0 and not os.path.isfile(csv_path):
        headers = get_findings_column_headers(air_host, api_token, investigation_id)
        save_csv_header_only(csv_path, headers)
    checkpoint.data["findings_done"] = True
    checkpoint.data.setdefault("export_inventory", {})["findings_row_count"] = row_count
    checkpoint.save()
    return row_count


def stream_evidence_to_csv(
    air_host: str,
    api_token: str,
    investigation_id: str,
    platform: str,
    evidence_category: str,
    assignment_ids: List[str],
    endpoint_name_map: Dict[str, str],
    csv_path: str,
    checkpoint: Checkpoint,
    request_delay: float = DEFAULT_REQUEST_DELAY,
) -> int:
    key = checkpoint.evidence_key(platform, evidence_category)
    if checkpoint.is_evidence_done(platform, evidence_category):
        inv = checkpoint.data.get("export_inventory", {}).get("evidence_tables", {})
        return int(inv.get(key, 0))

    skip = checkpoint.get_evidence_skip(platform, evidence_category) if os.path.isfile(csv_path) else 0
    if skip == 0 and os.path.isfile(csv_path):
        os.remove(csv_path)

    total = None
    row_count = 0
    if skip > 0 and os.path.isfile(csv_path):
        with open(csv_path, encoding="utf-8") as f:
            row_count = max(0, sum(1 for _ in f) - 1)

    fieldnames: Optional[List[str]] = None
    hub = f"/api/public/investigation-hub/investigations/{investigation_id}"

    while True:
        body = {
            "globalFilter": {"assignmentIds": assignment_ids},
            "filter": [],
            "skip": skip,
            "take": DEFAULT_PAGE_SIZE,
        }
        resp = api_post(
            air_host,
            api_token,
            f"{hub}/platform/{platform}/evidence-category/{evidence_category}",
            body=body,
            timeout=120,
        )
        if not resp.ok:
            print(
                f"\n  Evidence API error ({platform}/{evidence_category}) "
                f"skip={skip}: {resp.status_code} - {resp.text[:200]}"
            )
            break

        result = resp.json().get("result", {})
        entities = result.get("entities", [])
        if total is None:
            total = result.get("totalCount", 0)

        if not entities:
            break

        for row in entities:
            aid = row.get("air_task_assignment_id", "")
            eid = row.get("air_endpoint_id", "")
            row["air_endpoint_name"] = (
                endpoint_name_map.get(aid) or endpoint_name_map.get(eid) or "Unknown"
            )
            row["ingested_at"] = datetime.now(timezone.utc).isoformat()

        fieldnames = append_csv_rows(entities, csv_path, fieldnames)
        batch = len(entities)
        skip += batch
        row_count += batch
        checkpoint.set_evidence_progress(platform, evidence_category, skip, row_count)

        effective = total or 0
        print(f"    {row_count}/{effective} rows...", end="\r", flush=True)

        if skip >= (total or 0):
            break
        if request_delay > 0:
            time.sleep(request_delay)

    print(f"    {row_count}/{total or 0} rows exported.    ")
    checkpoint.mark_evidence_done(platform, evidence_category, row_count)
    return row_count


# ---------------------------------------------------------------------------
# Supplemental exports
# ---------------------------------------------------------------------------

def export_supplemental(
    air_host: str,
    api_token: str,
    org_id: str,
    case_id: str,
    investigation_id: str,
    supplemental_dir: str,
    checkpoint: Checkpoint,
    assignment_ids: Optional[List[str]] = None,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if checkpoint.data.get("supplemental_done"):
        return checkpoint.data.get("supplemental_counts", {})

    os.makedirs(supplemental_dir, exist_ok=True)
    hub = f"/api/public/investigation-hub/investigations/{investigation_id}"

    print("  Exporting supplemental data...", flush=True)

    tasks = paginate_get(
        air_host,
        api_token,
        f"/api/public/cases/{case_id}/tasks",
        params={"filter[organizationIds]": org_id},
        verbose=False,
    )
    tasks_path = os.path.join(supplemental_dir, "case_tasks.json")
    save_json_data(tasks, tasks_path)
    save_csv_rows(tasks, os.path.join(supplemental_dir, "case_tasks.csv")) if tasks else save_csv_header_only(
        os.path.join(supplemental_dir, "case_tasks.csv"), ["taskId", "name", "type", "status"]
    )
    counts["case_tasks"] = len(tasks)

    ep_resp = api_get(
        air_host,
        api_token,
        f"/api/public/cases/{case_id}/endpoints",
        params={"filter[organizationIds]": org_id, "pageNumber": 1, "pageSize": 500},
    )
    endpoints = []
    if ep_resp.ok:
        endpoints = ep_resp.json().get("result", {}).get("entities", [])
    save_json_data(endpoints, os.path.join(supplemental_dir, "case_endpoints.json"))
    if endpoints:
        save_csv_rows(endpoints, os.path.join(supplemental_dir, "case_endpoints.csv"))
    else:
        save_csv_header_only(
            os.path.join(supplemental_dir, "case_endpoints.csv"),
            ["_id", "name", "os", "platform", "ipAddress"],
        )
    counts["case_endpoints"] = len(endpoints)

    summary_body = {"globalFilter": build_findings_global_filter(assignment_ids or [])}
    summary_resp = api_post(air_host, api_token, f"{hub}/findings/summary", body=summary_body)
    summary = summary_resp.json() if summary_resp.ok else {"error": summary_resp.text[:500]}
    save_json_data(summary, os.path.join(supplemental_dir, "findings_summary.json"))
    counts["findings_summary"] = 1

    flags_resp = api_get(air_host, api_token, f"{hub}/flags")
    flags = flags_resp.json() if flags_resp.ok else {}
    save_json_data(flags, os.path.join(supplemental_dir, "flags.json"))
    flag_list = flags.get("result", flags) if isinstance(flags, dict) else flags
    counts["flags"] = len(flag_list) if isinstance(flag_list, list) else 1

    comments_resp = api_get(air_host, api_token, f"{hub}/comments")
    comments = comments_resp.json() if comments_resp.ok else {}
    save_json_data(comments, os.path.join(supplemental_dir, "comments.json"))
    comment_list = comments.get("result", comments) if isinstance(comments, dict) else comments
    counts["comments"] = len(comment_list) if isinstance(comment_list, list) else 1

    checkpoint.data["supplemental_done"] = True
    checkpoint.data["supplemental_counts"] = counts
    checkpoint.save()
    return counts


# ---------------------------------------------------------------------------
# Integrity / custody
# ---------------------------------------------------------------------------

def hash_file(path: str) -> Dict[str, Any]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return {
        "path": path,
        "size_bytes": size,
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
    }


def collect_package_files(export_dir: str) -> List[str]:
    files: List[str] = []
    for root, _dirs, names in os.walk(export_dir):
        for name in names:
            if name == CHECKPOINT_FILE or name.endswith(".zip"):
                continue
            full = os.path.join(root, name)
            if name in ("chain_of_custody.txt", "manifest.json"):
                continue
            files.append(full)
    return sorted(files)


def decode_token_identity(api_token: str) -> str:
    try:
        parts = api_token.split(".")
        if len(parts) != 3:
            return "API token (subject not decodable — opaque token)"
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(data)
        for key in ("email", "name", "sub", "preferred_username", "unique_name"):
            if claims.get(key):
                return f"{key}={claims[key]}"
        return f"JWT claims: {json.dumps(claims)[:200]}"
    except Exception:
        return "API token (subject not decodable — service account or opaque token)"


def script_build_id() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return f"file-mtime-{int(os.path.getmtime(__file__))}"


def get_package_versions() -> Dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "script_version": _SCRIPT_VERSION,
        "script_build": script_build_id(),
    }
    try:
        versions["requests"] = requests.__version__
    except Exception:
        pass
    try:
        import dotenv

        versions["python-dotenv"] = dotenv.__version__
    except Exception:
        pass
    return versions


def write_chain_of_custody(
    export_dir: str,
    manifest: Dict[str, Any],
) -> str:
    path = os.path.join(export_dir, "chain_of_custody.txt")
    lines: List[str] = []
    meta = manifest.get("execution", {})
    case = manifest.get("case", {})
    org = manifest.get("organization", {})
    source = manifest.get("source_system", {})

    lines.append("CHAIN OF CUSTODY REPORT")
    lines.append("=" * 72)
    lines.append("")
    lines.append("1. EXECUTION")
    lines.append(f"   Started (UTC):  {meta.get('started_at')}")
    lines.append(f"   Completed (UTC): {meta.get('completed_at')}")
    lines.append(f"   Script:         {meta.get('script_path')}")
    lines.append(f"   Version:        {meta.get('script_version')} (build {meta.get('script_build')})")
    for k, v in (meta.get("dependencies") or {}).items():
        lines.append(f"   {k}: {v}")
    lines.append("")
    lines.append("2. ENVIRONMENT")
    lines.append(f"   Hostname:       {meta.get('hostname')}")
    lines.append(f"   OS:             {meta.get('platform')}")
    lines.append(f"   OS user:        {meta.get('os_user')}")
    lines.append("")
    lines.append("3. OPERATOR")
    lines.append(f"   Name:           {meta.get('operator')}")
    lines.append("")
    lines.append("4. SOURCE SYSTEM")
    lines.append(f"   AIR host:       {source.get('air_host')}")
    lines.append(f"   Organization: {org.get('name')} (ID: {org.get('id')})")
    lines.append("")
    lines.append("5. AUTHENTICATION")
    lines.append(f"   API identity:   {meta.get('api_identity')}")
    lines.append("")
    lines.append("6. CASE RECORD")
    lines.append(f"   Case ID:        {case.get('id')}")
    lines.append(f"   Name:           {case.get('name')}")
    lines.append(f"   Status:         {case.get('status')}")
    lines.append(f"   Owner:          {case.get('owner')}")
    lines.append(f"   Created:        {case.get('createdAt')}")
    lines.append(f"   Updated:        {case.get('updatedAt')}")
    lines.append(f"   Closed:         {case.get('closedAt')}")
    lines.append(f"   Investigation:  {case.get('investigationId')}")
    lines.append("")
    lines.append("7. EXPORT INVENTORY")
    inv = manifest.get("export_inventory", {})
    lines.append(f"   Findings rows:  {inv.get('findings_row_count', 0)}")
    lines.append("   Evidence tables:")
    for key, count in sorted((inv.get("evidence_tables") or {}).items()):
        lines.append(f"     - {key}: {count} rows")
    lines.append("")
    lines.append("8. SUPPLEMENTAL FILES")
    for name, count in sorted((inv.get("supplemental") or {}).items()):
        lines.append(f"     - {name}: {count} records")
    lines.append("")
    lines.append("9. FILE INTEGRITY")
    for entry in manifest.get("files", []):
        rel = entry.get("relative_path", entry.get("path"))
        lines.append(f"   {rel}")
        lines.append(f"     Size: {entry.get('size_bytes')} bytes")
        lines.append(f"     MD5:    {entry.get('md5')}")
        lines.append(f"     SHA1:   {entry.get('sha1')}")
        lines.append(f"     SHA256: {entry.get('sha256')}")
    lines.append("")
    lines.append("10. DISCLAIMER")
    lines.append(
        "   This package was generated by the Updraft/Slate legal case export workflow."
    )
    lines.append(
        "   It is not a substitute for platform-native audit logs. Protect API credentials"
    )
    lines.append("   and verify hashes before legal proceedings.")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def build_manifest_and_custody(
    export_dir: str,
    *,
    started_at: str,
    completed_at: str,
    air_host: str,
    org: dict,
    case: dict,
    operator: Optional[str],
    api_token: str,
    export_inventory: dict,
    supplemental_counts: dict,
) -> Tuple[str, str]:
    export_inventory = dict(export_inventory)
    export_inventory["supplemental"] = supplemental_counts

    package_files = collect_package_files(export_dir)
    file_entries = []
    for full_path in package_files:
        rel = os.path.relpath(full_path, export_dir)
        entry = hash_file(full_path)
        entry["relative_path"] = rel.replace("\\", "/")
        file_entries.append(entry)

    manifest: Dict[str, Any] = {
        "execution": {
            "started_at": started_at,
            "completed_at": completed_at,
            "script_path": os.path.abspath(__file__),
            "script_version": _SCRIPT_VERSION,
            "script_build": script_build_id(),
            "dependencies": get_package_versions(),
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "os_user": getpass.getuser(),
            "operator": operator or "not provided",
            "api_identity": decode_token_identity(api_token),
        },
        "source_system": {"air_host": air_host},
        "organization": {
            "id": _first_id(org, "_id", "id"),
            "name": org.get("name"),
        },
        "case": {
            "id": case.get("_id"),
            "name": case.get("name"),
            "status": case.get("status"),
            "owner": case.get("owner"),
            "createdAt": case.get("createdAt"),
            "updatedAt": case.get("updatedAt"),
            "closedAt": case.get("closedAt"),
            "investigationId": (case.get("metadata") or {}).get("investigationId"),
        },
        "export_inventory": export_inventory,
        "files": [],
    }

    custody_path = write_chain_of_custody(export_dir, manifest)
    custody_hash = hash_file(custody_path)
    custody_hash["relative_path"] = "chain_of_custody.txt"
    file_entries.append(custody_hash)

    manifest["files"] = file_entries
    manifest_path = os.path.join(export_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    manifest_hash = hash_file(manifest_path)
    manifest_hash["relative_path"] = "manifest.json"
    manifest["files"].append(manifest_hash)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    write_chain_of_custody(export_dir, manifest)
    return custody_path, manifest_path


def create_zip(export_dir: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(export_dir):
            for name in files:
                if name == CHECKPOINT_FILE:
                    continue
                full = os.path.join(root, name)
                arcname = os.path.relpath(full, export_dir)
                zf.write(full, arcname)


# ---------------------------------------------------------------------------
# Interactive selection
# ---------------------------------------------------------------------------

def pick_from_list(prompt: str, items: list, formatter) -> Any:
    if not items:
        print(f"No items available for {prompt}.")
        sys.exit(1)
    print()
    for i, item in enumerate(items, 1):
        print(formatter(i, item))
    print()
    while True:
        try:
            choice = input(f"Select {prompt} [1-{len(items)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx]
            print(f"  Enter a number between 1 and {len(items)}.")
        except (ValueError, EOFError):
            print(f"  Enter a number between 1 and {len(items)}.")


def select_organization(air_host: str, api_token: str) -> dict:
    print("Fetching organizations...", flush=True)
    orgs = paginate_get(air_host, api_token, "/api/public/organizations", verbose=False)
    if not orgs:
        print("No organizations found.", file=sys.stderr)
        sys.exit(1)

    def fmt(i, org):
        oid = _first_id(org, "_id", "id")
        return f"  [{i:>3}]  {org.get('name', '?')}  (ID: {oid})"

    print(f"\n{'='*70}\nORGANIZATIONS\n{'='*70}")
    return pick_from_list("organization", orgs, fmt)


def fetch_cases(air_host: str, api_token: str, org_id: str) -> list:
    return paginate_get(
        air_host,
        api_token,
        "/api/public/cases",
        params={"filter[organizationIds]": org_id},
        verbose=False,
    )


def select_case(cases: list) -> dict:
    def fmt(i, case):
        name = case.get("name") or case.get("title") or "(untitled)"
        status = case.get("status", "?")
        cid = _first_id(case, "_id", "id")
        endpoints = case.get("totalEndpoints", "?")
        inv = (case.get("metadata") or {}).get("investigationId") or "none"
        return (
            f"  [{i:>3}]  {name}\n"
            f"         ID: {cid}  |  Status: {status}  |  Endpoints: {endpoints}  |  Investigation: {inv}"
        )

    print(f"\n{'='*70}\nCASES\n{'='*70}")
    return pick_from_list("case", cases, fmt)


def get_case_details(air_host: str, api_token: str, case_id: str, org_id: str) -> dict:
    resp = api_get(
        air_host,
        api_token,
        f"/api/public/cases/{case_id}",
        params={"filter[organizationIds]": org_id},
    )
    if not resp.ok:
        print(f"Failed to fetch case {case_id}: {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    return data.get("result", data)


def confirm_export(summary: str, auto_yes: bool) -> None:
    print(f"\n{'='*70}\nEXPORT SUMMARY\n{'='*70}\n")
    print(summary)
    if auto_yes:
        return
    answer = input("\nProceed with export? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("Export cancelled.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Main export orchestration
# ---------------------------------------------------------------------------

def run_export(
    air_host: str,
    api_token: str,
    org: dict,
    case: dict,
    export_dir: str,
    operator: Optional[str],
    resume: bool,
) -> str:
    org_id = _first_id(org, "_id", "id")
    case_id = case.get("_id")
    investigation_id = (case.get("metadata") or {}).get("investigationId")
    if not investigation_id:
        print("\nError: Case has no investigationId in metadata.", file=sys.stderr)
        print("Investigation Hub data is not available for this case.", file=sys.stderr)
        sys.exit(1)

    csv_dir = os.path.join(export_dir, "csv")
    supplemental_dir = os.path.join(export_dir, "supplemental")
    os.makedirs(csv_dir, exist_ok=True)
    os.makedirs(supplemental_dir, exist_ok=True)

    checkpoint_path = os.path.join(export_dir, CHECKPOINT_FILE)
    checkpoint = Checkpoint(checkpoint_path)
    if not resume:
        checkpoint.data = {
            "version": 1,
            "case_id": case_id,
            "investigation_id": investigation_id,
            "supplemental_done": False,
            "findings_done": False,
            "findings_skip": 0,
            "findings_row_count": 0,
            "evidence_completed": [],
            "evidence_progress": {},
            "export_inventory": {},
        }
        checkpoint.save()

    checkpoint.data.setdefault("case_id", case_id)
    checkpoint.data.setdefault("investigation_id", investigation_id)
    checkpoint.save()

    started_at = checkpoint.data.get("started_at") or datetime.now(timezone.utc).isoformat()
    checkpoint.data["started_at"] = started_at
    checkpoint.save()

    print("\nFetching investigation assets...", flush=True)
    assets_data, assets_error = get_investigation_assets(air_host, api_token, investigation_id)
    if assets_error:
        print("Error: Could not retrieve investigation assets.", file=sys.stderr)
        print(f"  {assets_error}", file=sys.stderr)
        sys.exit(1)
    if not assets_data:
        print(
            "  Warning: Investigation Hub returned no assets for this case "
            "(common for closed cases or before acquisition is ingested).",
            flush=True,
        )
        print(
            "  Export will continue with supplemental data; findings/evidence may be empty.",
            flush=True,
        )

    all_assignment_ids = collect_all_assignment_ids(assets_data)
    endpoint_name_map = build_endpoint_name_map(assets_data)
    print(f"  Assignment IDs: {len(all_assignment_ids)}")

    supplemental_counts = export_supplemental(
        air_host,
        api_token,
        org_id,
        case_id,
        investigation_id,
        supplemental_dir,
        checkpoint,
        assignment_ids=all_assignment_ids,
    )

    findings_path = os.path.join(csv_dir, "findings.csv")
    findings_count = stream_findings_to_csv(
        air_host,
        api_token,
        investigation_id,
        all_assignment_ids,
        findings_path,
        checkpoint,
    )

    print("\nFetching evidence sections...", flush=True)
    sections_data = get_sections(air_host, api_token, investigation_id)
    all_sections = list_all_sections(sections_data)
    data_structure = get_evidence_data_structure(air_host, api_token, investigation_id)

    total_sections = len(all_sections)
    for idx, (platform, section, expected_count) in enumerate(all_sections, 1):
        label = f"[{idx}/{total_sections}] evidence {platform}/{section}"
        print(f"\n{label} (API count: {expected_count})", flush=True)

        csv_name = evidence_csv_filename(platform, section)
        csv_path = os.path.join(csv_dir, csv_name)

        if checkpoint.is_evidence_done(platform, section):
            print("    (skipped — already exported)")
            continue

        assignment_ids = get_assignment_ids_for_platform(assets_data, platform)
        if not assignment_ids:
            cols = extract_columns_from_data_structure(data_structure, platform, section)
            if not cols:
                cols = ["note"]
            save_csv_header_only(csv_path, cols)
            checkpoint.mark_evidence_done(platform, section, 0)
            print("    (no assets for platform — header-only CSV)")
            continue

        if expected_count == 0 and checkpoint.get_evidence_skip(platform, section) == 0:
            cols = extract_columns_from_data_structure(data_structure, platform, section)
            if not cols:
                cols = ["note"]
            save_csv_header_only(csv_path, cols)
            checkpoint.mark_evidence_done(platform, section, 0)
            print("    (empty section — header-only CSV)")
            continue

        stream_evidence_to_csv(
            air_host,
            api_token,
            investigation_id,
            platform,
            section,
            assignment_ids,
            endpoint_name_map,
            csv_path,
            checkpoint,
        )

    completed_at = datetime.now(timezone.utc).isoformat()
    export_inventory = checkpoint.data.get("export_inventory", {})
    export_inventory["findings_row_count"] = findings_count

    print("\nGenerating chain-of-custody report and manifest...", flush=True)
    custody_path, manifest_path = build_manifest_and_custody(
        export_dir,
        started_at=started_at,
        completed_at=completed_at,
        air_host=air_host,
        org=org,
        case=case,
        operator=operator,
        api_token=api_token,
        export_inventory=export_inventory,
        supplemental_counts=supplemental_counts,
    )
    print(f"  {custody_path}")
    print(f"  {manifest_path}")

    zip_name = os.path.basename(export_dir.rstrip(os.sep)) + ".zip"
    zip_path = os.path.join(os.path.dirname(export_dir), zip_name)
    print(f"\nCreating ZIP archive...", flush=True)
    create_zip(export_dir, zip_path)
    print(f"  {zip_path}")

    return zip_path


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export case findings and evidence for legal handoff.",
    )
    parser.add_argument(
        "--operator",
        default=None,
        help="Name of person performing export (optional; recorded as 'not provided' if omitted)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Export directory (for resume, point to an existing incomplete export folder)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoint and re-export into the output directory",
    )
    parser.add_argument(
        "--log", metavar="PATH", nargs="?", const=True,
        help="Write a debug log to PATH (omit PATH to auto-generate under output/logs/).",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    _setup_log(args.log)
    air_host, api_token = load_config()
    _LOG.info("=== case_legal_export started  host=%s", air_host)

    print("Binalyze AIR — Legal Case Export")
    print(f"  Host: {air_host}")

    org = select_organization(air_host, api_token)
    org_id = _first_id(org, "_id", "id")

    print(f"\nFetching cases for organization {org_id}...", flush=True)
    cases = fetch_cases(air_host, api_token, org_id)
    if not cases:
        print("No cases found for this organization.")
        sys.exit(0)

    selected = select_case(cases)
    case_id = _first_id(selected, "_id", "id")
    case = get_case_details(air_host, api_token, case_id, org_id)

    investigation_id = (case.get("metadata") or {}).get("investigationId")
    operator_display = args.operator or "not provided"

    summary = (
        f"  Organization:    {org.get('name')} ({org_id})\n"
        f"  Case:            {case.get('name')} ({case_id})\n"
        f"  Status:          {case.get('status')}\n"
        f"  Investigation:   {investigation_id or 'MISSING'}\n"
        f"  Operator:        {operator_display}\n"
        f"  Export includes: findings, all evidence tables (incl. empty), supplemental data, custody report, ZIP"
    )
    confirm_export(summary, args.yes)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if args.output_dir:
        export_dir = os.path.abspath(args.output_dir)
        os.makedirs(export_dir, exist_ok=True)
        resume = not args.no_resume and os.path.isfile(os.path.join(export_dir, CHECKPOINT_FILE))
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_case = sanitize_filename_component(str(case_id))
        export_dir = os.path.join(OUTPUT_DIR, f"{safe_case}_{timestamp}")
        os.makedirs(export_dir, exist_ok=True)
        resume = False

    if resume:
        print(f"\nResuming export in: {export_dir}")

    zip_path = run_export(
        air_host,
        api_token,
        org,
        case,
        export_dir,
        args.operator,
        resume=resume,
    )

    print(f"\n{'='*70}\nEXPORT COMPLETE\n{'='*70}")
    print(f"  Folder: {export_dir}")
    print(f"  ZIP:    {zip_path}")
    print("\nDeliver the ZIP and chain_of_custody.txt to counsel.\n")


if __name__ == "__main__":
    main()

"""
Batch CSV acquisition for Binalyze AIR (single self-contained script; no lib/ imports).

- Validates org, reads host identifiers from CSV, resolves every asset in AIR before
  creating a case or assigning acquisitions.
- Acquisition profile chosen by name (case-insensitive match against listed profiles);
  resolves the database profile id (GET detail when list row uses a preset slug like \"quick\").
- Batches of N (default 5): after each batch completes, prompts before the next batch.
- Assets whose tags contain "server" (case-insensitive substring) require per-host approval.
- Prints isolation-related asset fields and latest isolation-like task per asset at the end.

Run from repository root:
  python workflows/batch_acquisition_csv/batch_acquisition_csv.py \
    --csv hosts.csv --profile-name "Quick triage" --case-name "Investigation X"

Requires .env: BINALYZE_AIR_HOST, BINALYZE_API_TOKEN (or AIR_* aliases).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import warnings
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

# ---------------------------------------------------------------------------
# HTTP / config (from lib/api_client.py)
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
BACKOFF_FACTOR = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_PAGINATION_ITERATIONS = 1000
SCRIPT_VERSION = "1.0.0"

CASE_VISIBILITY_VALUES = frozenset(
    ("public-to-organization", "private-to-users")
)

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_MAGENTA = "\033[35m"
ANSI_CYAN = "\033[36m"
ANSI_WHITE = "\033[97m"
ANSI_DARK_GRAY = "\033[90m"
ANSI_BOLD = "\033[1m"

_ANSI_ENABLED = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(text: str, color: str) -> str:
    if not _ANSI_ENABLED:
        return text
    return f"{color}{text}{ANSI_RESET}"


def _startup_banner() -> None:
    banner = r"""
 ____  _  ____ _____ ____ _____
| __ )(_)/ ___| ____|  _ \_   _|
|  _ \| | |   |  _| | |_) || |
| |_) | | |___| |___|  _ < | |
|____/|_|\____|_____|_| \_\|_|
"""
    print(_c(banner, ANSI_CYAN))
    print(_c(f"B!CERT Acquisition Utility v{SCRIPT_VERSION}", ANSI_BOLD + ANSI_MAGENTA))
    print(_c("Starting in 3 seconds...", ANSI_BLUE))
    time.sleep(3)


def _normalize_case_visibility(case_visibility: str) -> str:
    v = (case_visibility or "public-to-organization").strip()
    if v not in CASE_VISIBILITY_VALUES:
        raise ValueError(
            "case_visibility must be 'public-to-organization' or 'private-to-users', "
            f"not {case_visibility!r}"
        )
    return v


def load_config() -> Tuple[str, str]:
    load_dotenv()
    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")
    if not air_host or not api_token:
        print(
            "Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return air_host.rstrip("/"), api_token


def _headers(api_token: str) -> dict:
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url: str, retries: int = MAX_RETRIES, **kwargs):
    backoff = INITIAL_BACKOFF
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = method(url, **kwargs)
            if resp.status_code not in RETRYABLE_STATUS_CODES:
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
            print(
                f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                f"(attempt {attempt + 1}/{retries})...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(wait)
            backoff *= BACKOFF_FACTOR
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise
            print(
                f"\n  Connection error, retrying in {backoff:.1f}s "
                f"(attempt {attempt + 1}/{retries})...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(backoff)
            backoff *= BACKOFF_FACTOR
    raise last_exc  # pragma: no cover


def api_get(
    air_host: str,
    api_token: str,
    path: str,
    params=None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    extra_headers=None,
):
    url = f"{air_host}{path}"
    headers = dict(_headers(api_token))
    if extra_headers:
        headers.update(extra_headers)
    return _request_with_retry(
        requests.get,
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        retries=retries,
    )


def api_post(
    air_host: str,
    api_token: str,
    path: str,
    body=None,
    params=None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
):
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


# ---------------------------------------------------------------------------
# Pagination (from lib/pagination.py)
# ---------------------------------------------------------------------------


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


def _entity_ids_fingerprint(entities: List[dict]) -> Tuple[str, ...]:
    if not entities:
        return ()
    ids = []
    for row in entities:
        if isinstance(row, dict):
            oid = _first_id(row, "_id", "id", "endpointId")
            if oid is not None:
                ids.append(str(oid))
    return tuple(sorted(ids))


def paginate_get(
    air_host: str,
    api_token: str,
    path: str,
    params=None,
    page_size: int = 100,
    verbose: bool = True,
) -> List[dict]:
    base_params = dict(params or {})
    all_entities: List[dict] = []
    page = 1
    seen_pages = set()
    seen_fingerprints = set()

    while len(seen_pages) < MAX_PAGINATION_ITERATIONS:
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
        raise ValueError(
            f"Unexpected response format: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )
    return all_entities


# ---------------------------------------------------------------------------
# AIR: org, assets, cases, profiles, POST /acquisitions/acquire
# ---------------------------------------------------------------------------


class AssetResolveError(Exception):
    pass


def _primary_host_label(name: Any) -> str:
    """First DNS label, lowercased (short name vs FQDN in asset `name`)."""
    if name is None:
        return ""
    s = str(name).strip().lower()
    if not s:
        return ""
    return s.split(".", 1)[0]


def validate_org(air_host: str, api_token: str, org_id: str) -> dict:
    resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
    if not resp.ok:
        raise RuntimeError(
            f"Could not fetch organization {org_id}: HTTP {resp.status_code} {resp.text[:300]}"
        )
    return resp.json().get("result", resp.json())


def find_asset_strict(
    air_host: str, api_token: str, identifier: str, org_id: str
) -> dict:
    resp = api_get(air_host, api_token, f"/api/public/assets/{identifier}")
    if resp.ok:
        asset = resp.json().get("result", resp.json())
        if asset.get("_id"):
            return asset
    params = {"filter[organizationIds]": org_id, "search": identifier}
    assets = paginate_get(
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


def resolve_case_new(
    air_host: str,
    api_token: str,
    org_id: str,
    case_name: str,
    case_visibility: str = "public-to-organization",
) -> dict:
    visibility = _normalize_case_visibility(case_visibility)
    body = {
        "name": case_name,
        "organizationId": org_id,
        "visibility": visibility,
    }
    resp = api_post(air_host, api_token, "/api/public/cases", body=body)
    if not resp.ok:
        raise RuntimeError(
            f"Failed to create case: HTTP {resp.status_code} {resp.text[:500]}"
        )
    return resp.json().get("result", resp.json())


def list_acquisition_profiles(air_host: str, api_token: str, org_id: str) -> List[dict]:
    params = {"filter[organizationIds]": org_id}
    return paginate_get(
        air_host,
        api_token,
        "/api/public/acquisitions/profiles",
        params=params,
        verbose=False,
    )


def resolve_acquisition_profile(
    air_host: str, api_token: str, org_id: str, profile_name: str
) -> dict:
    """Match profile by exact name after case-folding (same as case_acquire / list UI)."""
    pn = (profile_name or "").strip().lower()
    if not pn:
        raise RuntimeError("Acquisition profile name is empty.")
    profiles = list_acquisition_profiles(air_host, api_token, org_id)
    if not profiles:
        raise RuntimeError("No acquisition profiles returned by API.")
    for p in profiles:
        if (p.get("name") or "").lower() == pn:
            return p
    raise RuntimeError(f"No acquisition profile found with name {profile_name!r}")


PRESET_PROFILES = frozenset(
    (
        "browsing-history",
        "compromise-assessment",
        "event-logs",
        "full",
        "memory-ram-pagefile",
        "quick",
    )
)


def acquisition_profile_id_for_acquire(
    profile_from_list: Dict[str, Any],
    profile_arg: str,
) -> str:
    """
    ID for JSON acquisitionProfileId on POST /acquisitions/acquire.
    Duplicated from lib/binalyze_acquisitions.py for this self-contained script.
    """
    arg_norm = (profile_arg or "").strip().lower()
    if arg_norm in PRESET_PROFILES:
        return arg_norm
    ref = _first_id(profile_from_list, "_id", "id")
    ref_s = str(ref) if ref is not None else ""
    if not ref_s:
        raise RuntimeError("Acquisition profile row has no _id/id; cannot derive profile id.")
    return ref_s


def assign_acquisition_task(
    air_host: str,
    api_token: str,
    case_id: str,
    endpoint_ids: List[str],
    profile_id: str,
    org_id: str,
) -> dict:
    body = {
        "caseId": case_id,
        "droneConfig": {"autoPilot": False, "enabled": False},
        "taskConfig": {"choice": "use-policy"},
        "acquisitionProfileId": profile_id,
        "filter": {"endpointIds": endpoint_ids, "organizationIds": [int(org_id)]},
    }
    resp = api_post(air_host, api_token, "/api/public/acquisitions/acquire", body=body)
    if not resp.ok:
        raise RuntimeError(
            f"POST /acquisitions/acquire failed HTTP {resp.status_code}: {resp.text[:2000]}"
        )
    return resp.json()


def assign_unisolation_task(
    air_host: str,
    api_token: str,
    endpoint_ids: List[str],
    org_id: str,
    case_id: Optional[str] = None,
) -> dict:
    # AIR expects org scope under filter (same as per-host disable with filter.name).
    body: Dict[str, Any] = {
        "enabled": False,
        "filter": {
            "organizationIds": [int(org_id)],
            "endpointIds": list(endpoint_ids),
        },
    }
    if case_id:
        body["caseId"] = case_id
    resp = api_post(air_host, api_token, "/api/public/assets/tasks/isolation", body=body)
    if not resp.ok:
        raise RuntimeError(
            f"POST /assets/tasks/isolation (bulk disable) failed HTTP {resp.status_code}: {resp.text[:2000]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Tags / Server gate
# ---------------------------------------------------------------------------


def _normalize_tags(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    return [raw]


def _tag_to_searchable(tag: Any) -> str:
    if tag is None:
        return ""
    if isinstance(tag, str):
        return tag
    if isinstance(tag, dict):
        return str(tag.get("name") or tag.get("label") or tag.get("value") or "")
    return str(tag)


def asset_tags_contain_server(asset: dict) -> bool:
    for t in _normalize_tags(asset.get("tags")):
        if "server" in _tag_to_searchable(t).lower():
            return True
    return False


def asset_is_endpoint(asset: dict) -> bool:
    kind = str(asset.get("assetType") or asset.get("type") or "").strip().lower()
    return kind == "endpoint"


def _boolish(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    return None


def asset_is_server(asset: dict) -> bool:
    # Prefer API's explicit isServer field when present.
    is_server = _boolish(asset.get("isServer"))
    if is_server is not None:
        return is_server
    # Fallback for tenants/rows that do not expose isServer.
    return asset_tags_contain_server(asset)


def asset_is_actively_isolated(asset: dict) -> bool:
    status = str(asset.get("isolationStatus") or "").strip().lower()
    return status in ("isolated", "isolating")


# ---------------------------------------------------------------------------
# Isolation summary (from isolation_status / binalyze_isolation)
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset(
    s.lower() for s in ("completed", "failed", "cancelled", "error", "canceled")
)


def _task_is_isolation(task: dict) -> bool:
    t = (task.get("type") or "").lower()
    name = (task.get("name") or "").lower()
    display = (task.get("displayType") or "").lower()
    return "isolat" in t or "isolat" in name or "isolat" in display


def get_asset_tasks(air_host: str, api_token: str, endpoint_id: str) -> List[dict]:
    resp = api_get(air_host, api_token, f"/api/public/assets/{endpoint_id}/tasks")
    if not resp.ok:
        raise RuntimeError(
            f"Could not list asset tasks: HTTP {resp.status_code} {resp.text[:500]}"
        )
    data = resp.json()
    inner = data.get("result", data)
    if isinstance(inner, list):
        return inner
    if isinstance(inner, dict):
        entities = inner.get("entities")
        if isinstance(entities, list):
            return entities
    return []


def latest_isolation_task(tasks: List[dict]) -> Optional[dict]:
    candidates = [t for t in tasks if _task_is_isolation(t)]
    if not candidates:
        return None

    def sort_key(t: dict) -> str:
        return t.get("createdAt") or t.get("updatedAt") or ""

    return sorted(candidates, key=sort_key)[-1]


def asset_isolation_flags(asset: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in (
        "isolation",
        "isolating",
        "isolated",
        "isolationStatus",
        "networkIsolation",
        "networkIsolationStatus",
    ):
        if key in asset and asset.get(key) is not None:
            out[key] = asset.get(key)
    return out


def summarize_isolation_for_asset(
    air_host: str, api_token: str, asset: dict
) -> Dict[str, Any]:
    eid = _first_id(asset, "_id", "id")
    tasks = get_asset_tasks(air_host, api_token, str(eid))
    latest = latest_isolation_task(tasks)
    row: Dict[str, Any] = {
        "hostname": asset.get("name"),
        "asset_id": str(eid),
        "asset_type": asset.get("assetType") or asset.get("type") or "",
        "asset_tags": asset.get("tags") or [],
        "is_server": asset_is_server(asset),
        "asset_isolation_fields": asset_isolation_flags(asset),
        "latest_isolation_task": None,
    }
    if latest:
        st = (latest.get("status") or "").lower()
        row["latest_isolation_task"] = {
            "name": latest.get("name"),
            "type": latest.get("type"),
            "status": latest.get("status"),
            "taskId": _first_id(latest, "taskId", "_id", "id"),
            "is_terminal": st in TERMINAL_STATUSES,
        }
    return row


# ---------------------------------------------------------------------------
# CSV + prompts + batches
# ---------------------------------------------------------------------------


def _prompt_yes(question: str) -> bool:
    while True:
        try:
            a = input(f"{question} [y/N]: ").strip().lower()
        except EOFError:
            return False
        if a in ("y", "yes"):
            return True
        if a in ("n", "no", ""):
            return False
        print("  Please enter y or n.")


def read_csv_identifiers(
    path: str, hostname_column: str
) -> Tuple[List[str], List[str]]:
    """
    Returns (valid_identifiers, rejected_row_messages).
    Header must include hostname_column.
    """
    if not os.path.isfile(path):
        print(f"Error: CSV file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("Error: CSV has no header row.", file=sys.stderr)
            sys.exit(1)
        if hostname_column not in reader.fieldnames:
            print(
                f"Error: column {hostname_column!r} not in CSV headers: {reader.fieldnames!r}",
                file=sys.stderr,
            )
            sys.exit(1)
        idents: List[str] = []
        errors: List[str] = []
        # First data row is typically line 2 (line 1 = header); use reader.line_num after read
        for row_idx, row in enumerate(reader, start=1):
            line_approx = reader.line_num
            raw = row.get(hostname_column)
            if raw is None or not str(raw).strip():
                errors.append(
                    f"CSV row {row_idx} (line {line_approx}): "
                    f"empty or null value in column {hostname_column!r}"
                )
                continue
            idents.append(str(raw).strip())
    if not idents:
        if errors:
            print("Error: all CSV hostnames were null/blank.", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
        else:
            print("Error: no host identifiers after reading CSV.", file=sys.stderr)
        sys.exit(1)
    seen: set = set()
    dupes = []
    for i in idents:
        k = i.lower()
        if k in seen:
            dupes.append(i)
        seen.add(k)
    if dupes:
        print(
            f"Warning: duplicate identifier(s) in CSV (processing all): {dupes!r}",
            file=sys.stderr,
        )
    return idents, errors


def batches(items: List[Any], size: int) -> Iterator[List[Any]]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for i in range(0, len(items), size):
        yield items[i : i + size]


def guard_non_null_hostnames(resolved: List[Tuple[str, dict]]) -> None:
    bad = []
    for ident, asset in resolved:
        name = asset.get("name")
        if name is None or not str(name).strip():
            aid = _first_id(asset, "_id", "id")
            bad.append(f"identifier={ident!r} asset_id={aid!r} name={name!r}")
    if bad:
        print(
            "Error: resolved asset(s) have null/blank hostname; refusing to continue.",
            file=sys.stderr,
        )
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        sys.exit(1)


def print_isolation_table(rows: List[Dict[str, Any]]) -> None:
    print(
        f"\n{'Hostname':<26} {'Type':<12} {'Is server':<10} "
        f"{'Isolation status':<20} {'Latest iso task':<30}"
    )
    print("-" * 110)
    for row in rows:
        flags = row.get("asset_isolation_fields") or {}
        isolation_status = str(flags.get("isolationStatus") or "-")[:18]
        atype = str(row.get("asset_type") or "")[:10]
        is_server_s = "server" if row.get("is_server") else "-"
        latest = row.get("latest_isolation_task") or {}
        if row.get("error"):
            lt = f"ERR: {str(row.get('error'))[:24]}"
        elif latest:
            lt = f"{latest.get('status')} (terminal={latest.get('is_terminal')})"
        else:
            lt = "(no isolation task)"
        hn = (row.get("hostname") or "")[:26]
        hn_col = _c(hn.ljust(26), ANSI_WHITE)
        atype_col = atype.ljust(12)
        if atype.lower() != "endpoint":
            atype_col = _c(atype_col, ANSI_DARK_GRAY)
        is_server_col = is_server_s.ljust(10)
        iso_col = isolation_status.ljust(20)
        if isolation_status.strip().lower() == "isolated":
            iso_col = _c(iso_col, ANSI_RED)
        print(
            f"{hn_col} {atype_col} {is_server_col} "
            f"{iso_col} {lt[:28]:<30}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch acquisition from CSV (flat script, no lib imports)."
    )
    p.add_argument(
        "--org-id",
        default="0",
        help="Binalyze organization ID (default: 0)",
    )
    p.add_argument(
        "--case-name",
        required=True,
        help="Title for the new AIR case",
    )
    p.add_argument("--csv", required=True, help="Path to CSV with host identifiers")
    p.add_argument(
        "--profile-name",
        required=True,
        help="Acquisition profile name in AIR (matched case-insensitively)",
    )
    p.add_argument(
        "--hostname-column",
        default="name",
        help="CSV column header for hostname or asset _id (default: name)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Hosts per batch before prompting for the next batch (default: 5)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve assets and profile only; do not create case or POST acquire",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print isolation summary after each successful POST /acquisitions/acquire",
    )
    p.add_argument(
        "--case-visibility",
        dest="case_visibility",
        default="public-to-organization",
        choices=sorted(CASE_VISIBILITY_VALUES),
        help="POST /cases visibility (default: public-to-organization)",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    org_id = str(args.org_id).strip()
    case_name = (args.case_name or "").strip()
    if not case_name:
        print("Error: --case-name must be non-empty.", file=sys.stderr)
        sys.exit(1)
    if args.batch_size < 1:
        print("Error: --batch-size must be >= 1.", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()
    profile_name_arg = (args.profile_name or "").strip()
    if not profile_name_arg:
        print("Error: --profile-name must be non-empty.", file=sys.stderr)
        sys.exit(1)

    _startup_banner()
    print(f"AIR host: {air_host}")
    print(f"Organization ID: {org_id}")
    print(f"Case name: {case_name}")
    print(f"Case visibility: {args.case_visibility}")
    print(f"Acquisition profile name: {_c(repr(profile_name_arg), ANSI_MAGENTA)}")
    print(f"CSV: {args.csv}  column: {args.hostname_column!r}")

    print("\nValidating organization...", flush=True)
    org = validate_org(air_host, api_token, org_id)
    print(f"  {_c(str(org.get('name', org_id)), ANSI_GREEN)}")

    identifiers, csv_rejections = read_csv_identifiers(args.csv, args.hostname_column)
    print(f"\nLoaded {len(identifiers)} identifier(s) from CSV.")
    if csv_rejections:
        print(_c("\nCSV hostname validation errors (rows skipped):", ANSI_RED), file=sys.stderr)
        for e in csv_rejections:
            print(_c(f"  {e}", ANSI_RED), file=sys.stderr)

    print("\nResolving all endpoints in AIR (preflight)...", flush=True)
    resolved: List[Tuple[str, dict]] = []
    unresolved_identifiers: List[str] = []
    for ident in identifiers:
        try:
            asset = find_asset_strict(air_host, api_token, ident, org_id)
            resolved.append((ident, asset))
            print(
                f"  {_c('OK', ANSI_GREEN)} {ident} -> "
                f"{_c(str(asset.get('name')), ANSI_CYAN)} ({asset.get('_id')})"
            )
        except AssetResolveError:
            unresolved_identifiers.append(ident)
            print(
                f"  {_c('FAILED', ANSI_RED)} {ident} {_c('(Not Resolved)', ANSI_RED)}",
                flush=True,
            )
    if not resolved:
        print(
            _c("Error: no endpoints could be resolved from the CSV.", ANSI_RED),
            file=sys.stderr,
        )
        sys.exit(1)
    if unresolved_identifiers:
        print(
            f"\nContinuing with {_c(str(len(resolved)), ANSI_GREEN)} resolved host(s); "
            f"{_c(str(len(unresolved_identifiers)), ANSI_RED)} identifier(s) skipped.",
            flush=True,
        )

    guard_non_null_hostnames(resolved)

    print("\nResolving acquisition profile by name...", flush=True)
    try:
        profile = resolve_acquisition_profile(air_host, api_token, org_id, profile_name_arg)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        prof_id = acquisition_profile_id_for_acquire(profile, profile_name_arg)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"  Profile: {_c(str(profile.get('name')), ANSI_MAGENTA)} "
        f"(acquire profileId={prof_id!r})"
    )

    if args.dry_run:
        print("\n[DRY RUN] Skipping case creation and POST /acquisitions/acquire.")
        print("[DRY RUN] Skipping POST /assets/tasks/isolation (disable).")
        print("\nIsolation status (read-only preview):", flush=True)
        iso_rows = []
        for ident, asset in resolved:
            row = summarize_isolation_for_asset(air_host, api_token, asset)
            row["identifier"] = ident
            iso_rows.append(row)
        print_isolation_table(iso_rows)
        return

    print("\nCreating case...", flush=True)
    case = resolve_case_new(
        air_host, api_token, org_id, case_name, case_visibility=args.case_visibility
    )
    case_id = _first_id(case, "_id", "id")
    if case_id is None:
        print("Error: case response missing _id/id.", file=sys.stderr)
        sys.exit(1)
    print(f"  Case: {case.get('name')} ({case_id})")

    skipped_non_endpoints: List[Tuple[str, str, str]] = []
    eligible_endpoints: List[Tuple[str, dict]] = []
    for ident, asset in resolved:
        if asset_is_endpoint(asset):
            eligible_endpoints.append((ident, asset))
        else:
            skipped_non_endpoints.append(
                (
                    ident,
                    str(asset.get("name") or ""),
                    str(asset.get("assetType") or asset.get("type") or ""),
                )
            )

    if skipped_non_endpoints:
        print(
            f"\nSkipping {len(skipped_non_endpoints)} non-endpoint asset(s) for acquisition:"
        )
        for ident, host, atype in skipped_non_endpoints[:20]:
            print(f"  - {ident} -> {host or '?'} (type={atype or '?'})")
        if len(skipped_non_endpoints) > 20:
            print(f"  ... and {len(skipped_non_endpoints) - 20} more")

    non_server_assets: List[Tuple[str, dict]] = []
    server_assets: List[Tuple[str, dict]] = []
    for row in eligible_endpoints:
        if asset_is_server(row[1]):
            server_assets.append(row)
        else:
            non_server_assets.append(row)

    if not eligible_endpoints:
        print("\nNo endpoint assets eligible for acquisition assignment.")

    workstation_batches = list(batches(non_server_assets, args.batch_size))
    total_workstation_batches = len(workstation_batches)
    skipped_server: List[str] = []
    assign_errors: List[str] = []
    selected_non_server_count = 0
    selected_server_count = 0
    selected_endpoint_ids: List[str] = []
    selected_endpoint_names: List[str] = []

    for bi, batch in enumerate(workstation_batches):
        print(
            f"\n{'=' * 70}\nWorkstation Batch {bi + 1}/{total_workstation_batches} "
            f"({len(batch)} host(s))\n{'=' * 70}"
        )

        for ident, asset in batch:
            eid = _first_id(asset, "_id", "id")
            name = asset.get("name")

            print(f"\nSelect for acquisition: {_c(str(name), ANSI_CYAN)} ({eid})...")
            selected_endpoint_ids.append(str(eid))
            selected_endpoint_names.append(str(name))
            print(f"  {_c('OK', ANSI_GREEN)} (queued for bulk acquire)")
            selected_non_server_count += 1

            if args.verbose:
                summ = summarize_isolation_for_asset(air_host, api_token, asset)
                print("  Isolation snapshot:", json.dumps(summ, indent=2, default=str)[:2500])

        if bi < total_workstation_batches - 1:
            remaining = sum(len(b) for b in workstation_batches[bi + 1 :])
            if not _prompt_yes(
                f"Proceed with the next workstation batch ({remaining} host(s) remaining)?"
            ):
                print("Stopped by operator before next workstation batch.")
                break

    if server_assets:
        print("\n" + _c("=" * 70, ANSI_YELLOW))
        print(_c("SERVER PHASE: server-class endpoint acquisitions", ANSI_BOLD + ANSI_YELLOW))
        print(_c("=" * 70, ANSI_YELLOW))
        for ident, asset in server_assets:
            eid = _first_id(asset, "_id", "id")
            name = asset.get("name")
            print(
                f"\n{_c('*** Server-related tag on asset', ANSI_YELLOW)} "
                f"{_c(repr(name), ANSI_WHITE)} {_c(f'({ident}) ***', ANSI_YELLOW)}"
            )
            if not _prompt_yes(f"Approve acquisition for this host ({name})?"):
                print(f"  Skipped by operator: {ident}")
                skipped_server.append(ident)
                continue
            print(f"\nSelect for acquisition: {_c(str(name), ANSI_CYAN)} ({eid})...")
            selected_endpoint_ids.append(str(eid))
            selected_endpoint_names.append(str(name))
            selected_server_count += 1
            print(f"  {_c('OK', ANSI_GREEN)} (queued for bulk acquire)")

            if args.verbose:
                summ = summarize_isolation_for_asset(air_host, api_token, asset)
                print("  Isolation snapshot:", json.dumps(summ, indent=2, default=str)[:2500])

    if selected_endpoint_ids:
        print("\n" + "=" * 70)
        print(
            f"Submitting single bulk acquisition task for "
            f"{_c(str(len(selected_endpoint_ids)), ANSI_CYAN)} selected endpoint(s)..."
        )
        print("=" * 70)
        try:
            assign_acquisition_task(
                air_host,
                api_token,
                str(case_id),
                selected_endpoint_ids,
                str(prof_id),
                org_id,
            )
            print(f"POST /acquisitions/acquire {_c('OK', ANSI_GREEN)}.")
        except RuntimeError as e:
            print(_c(f"Error: {e}", ANSI_RED), file=sys.stderr)
            assign_errors.append(f"bulk-acquire: {e}")
    else:
        print("\nNo endpoints were selected for bulk acquisition.")

    print("\n" + "=" * 70)
    print("Isolation status for all resolved assets")
    print("=" * 70)
    iso_rows: List[Dict[str, Any]] = []
    for ident, asset in resolved:
        try:
            eid = _first_id(asset, "_id", "id")
            fresh = api_get(air_host, api_token, f"/api/public/assets/{eid}")
            if fresh.ok:
                asset = fresh.json().get("result", fresh.json())
            row = summarize_isolation_for_asset(air_host, api_token, asset)
            row["identifier"] = ident
            iso_rows.append(row)
        except RuntimeError as e:
            iso_rows.append(
                {
                    "identifier": ident,
                    "hostname": asset.get("name"),
                    "asset_type": asset.get("assetType") or asset.get("type") or "",
                    "asset_tags": asset.get("tags") or [],
                    "is_server": asset_is_server(asset),
                    "asset_isolation_fields": {},
                    "latest_isolation_task": None,
                    "error": str(e),
                }
            )
    print_isolation_table(iso_rows)
    print("\nRun summary:")
    print(f"  Resolved assets: {_c(str(len(resolved)), ANSI_CYAN)}")
    unresolved_n = len(unresolved_identifiers)
    print(
        f"  Unresolved CSV identifiers: "
        f"{_c(str(unresolved_n), ANSI_RED if unresolved_n else ANSI_GREEN)}"
    )
    if unresolved_identifiers:
        print(f"    {_c(', '.join(unresolved_identifiers), ANSI_RED)}")
    print(f"  Eligible endpoints: {_c(str(len(eligible_endpoints)), ANSI_GREEN)}")
    skipped_non_endpoints_n = len(skipped_non_endpoints)
    print(
        f"  Skipped non-endpoints: "
        f"{_c(str(skipped_non_endpoints_n), ANSI_YELLOW if skipped_non_endpoints_n else ANSI_GREEN)}"
    )
    print(f"  Non-server endpoints selected: {_c(str(selected_non_server_count), ANSI_GREEN)}")
    print(
        f"  Server endpoints selected: "
        f"{_c(str(selected_server_count), ANSI_GREEN if selected_server_count else ANSI_YELLOW)}"
    )
    skipped_server_n = len(skipped_server)
    print(
        f"  Server approvals skipped: "
        f"{_c(str(skipped_server_n), ANSI_RED if skipped_server_n else ANSI_GREEN)}"
    )
    if skipped_server:
        print(f"\nSkipped (server approval declined): {skipped_server}")
    if assign_errors:
        print(_c("\nPOST /acquisitions/acquire errors:", ANSI_RED), file=sys.stderr)
        for e in assign_errors:
            print(_c(f"  {e}", ANSI_RED), file=sys.stderr)
        # Do not exit yet; allow optional un-isolation flow after table + summary.
    had_runtime_errors = bool(assign_errors) or bool(unresolved_identifiers)

    if eligible_endpoints:
        # Refresh endpoint docs once before un-isolation selection, so isolationStatus
        # reflects current tenant state rather than preflight snapshots.
        refreshed_eligible: List[Tuple[str, dict]] = []
        refresh_errors: List[str] = []
        for ident, asset in eligible_endpoints:
            eid = _first_id(asset, "_id", "id")
            if eid is None:
                refresh_errors.append(f"{ident}: missing asset id")
                continue
            try:
                fresh = api_get(air_host, api_token, f"/api/public/assets/{eid}")
                if fresh.ok:
                    asset = fresh.json().get("result", fresh.json())
                refreshed_eligible.append((ident, asset))
            except Exception as e:
                refreshed_eligible.append((ident, asset))
                refresh_errors.append(f"{ident}: refresh error ({e})")

        uniso_candidates: List[Tuple[str, dict]] = []
        uniso_skipped_not_isolated: List[Tuple[str, str, str, str]] = []
        for ident, asset in refreshed_eligible:
            status = str(asset.get("isolationStatus") or "").strip().lower() or "-"
            if asset_is_actively_isolated(asset):
                uniso_candidates.append((ident, asset))
            else:
                uniso_skipped_not_isolated.append(
                    (
                        ident,
                        str(asset.get("name") or ""),
                        str(asset.get("assetType") or asset.get("type") or ""),
                        status,
                    )
                )

        print("\nUn-isolation candidate selection:")
        print(f"  Eligible endpoints: {_c(str(len(refreshed_eligible)), ANSI_CYAN)}")
        print(
            f"  Active isolation candidates (isolated/isolating): "
            f"{_c(str(len(uniso_candidates)), ANSI_GREEN if uniso_candidates else ANSI_YELLOW)}"
        )
        print(
            f"  Skipped (not isolated/isolating): "
            f"{_c(str(len(uniso_skipped_not_isolated)), ANSI_YELLOW if uniso_skipped_not_isolated else ANSI_GREEN)}"
        )
        if refresh_errors:
            print(_c("  Asset refresh warnings:", ANSI_YELLOW))
            for line in refresh_errors[:10]:
                print(_c(f"    - {line}", ANSI_YELLOW))
            if len(refresh_errors) > 10:
                print(_c(f"    ... and {len(refresh_errors) - 10} more", ANSI_YELLOW))
        if uniso_skipped_not_isolated:
            print("  Not-isolated preview:")
            for ident, host, atype, st in uniso_skipped_not_isolated[:10]:
                print(f"    - {ident} -> {host or '?'} (type={atype or '?'}, isolationStatus={st})")
            if len(uniso_skipped_not_isolated) > 10:
                print(f"    ... and {len(uniso_skipped_not_isolated) - 10} more")

        print()
        if _prompt_yes("Run un-isolation task?"):
            if not uniso_candidates:
                print("No isolated/isolating endpoints found; skipping un-isolation API calls.")
            else:
                print(_c("\nSubmitting single bulk un-isolation task...", ANSI_CYAN))
                uniso_errors: List[str] = []
                uniso_endpoint_ids: List[str] = []
                uniso_hosts: List[str] = []
                for ident, asset in uniso_candidates:
                    eid = _first_id(asset, "_id", "id")
                    name = asset.get("name")
                    if eid is None:
                        uniso_errors.append(
                            f"{ident} (host={name}, asset_id={eid}): missing endpoint id"
                        )
                        continue
                    uniso_endpoint_ids.append(str(eid))
                    uniso_hosts.append(str(name or ident))

                if uniso_endpoint_ids:
                    print(f"  Hosts in task: {_c(str(len(uniso_endpoint_ids)), ANSI_CYAN)}")
                    preview = ", ".join(uniso_hosts[:10])
                    if preview:
                        suffix = " ..." if len(uniso_hosts) > 10 else ""
                        print(f"  Host preview: {preview}{suffix}")
                    try:
                        assign_unisolation_task(
                            air_host,
                            api_token,
                            uniso_endpoint_ids,
                            org_id,
                            case_id=str(case_id) if case_id else None,
                        )
                        print(f"  POST /assets/tasks/isolation disable {_c('OK', ANSI_GREEN)}.")
                    except RuntimeError as e:
                        print(_c(f"  Error: {e}", ANSI_RED), file=sys.stderr)
                        uniso_errors.append(str(e))
                else:
                    print("No valid endpoint IDs for un-isolation; skipping API call.")

                print("\nUn-isolation summary:")
                uniso_ok = 1 if uniso_endpoint_ids and not uniso_errors else 0
                print(f"  Successful un-isolation tasks: {_c(str(uniso_ok), ANSI_GREEN)}")
                print(
                    f"  Un-isolation errors: "
                    f"{_c(str(len(uniso_errors)), ANSI_RED if uniso_errors else ANSI_GREEN)}"
                )
                if uniso_errors:
                    print(_c("\nPOST /assets/tasks/isolation disable errors:", ANSI_RED), file=sys.stderr)
                    for e in uniso_errors:
                        print(_c(f"  {e}", ANSI_RED), file=sys.stderr)
                    had_runtime_errors = True

    print("\nDone.\n")
    if had_runtime_errors:
        sys.exit(2)


if __name__ == "__main__":
    main()

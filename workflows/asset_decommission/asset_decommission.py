"""
Workflow: Asset decommission

Compares a customer-supplied "decommissioned hosts" CSV (A-list) against the
full asset inventory of a Binalyze AIR organization (B-list), reports matches
and gaps, prompts for confirmation, then uninstalls matched endpoints via the
AIR API to release license seats.

Run from repository root:
  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv
  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --org-id 362 --yes
  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --dry-run
  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --purge --yes
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

_SCRIPT_VERSION = "1.0.0"

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ITERATIONS = 1000

_CANDIDATE_HOSTNAME_COLUMNS = [
    "hostname", "host_name", "host", "name", "computer_name",
    "computername", "device_name", "devicename", "endpoint", "asset",
]

# Directories searched for .env, in priority order:
#   1. This workflow's own directory  (workflows/asset_decommission/.env)
#   2. Repository root                (.env)
_WORKFLOW_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_WORKFLOW_DIR))


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
# Config
# ---------------------------------------------------------------------------

def load_config() -> Tuple[str, str]:
    # Workflow-local .env takes precedence over the repo-root .env so the
    # script works both standalone and as part of the monorepo.
    load_dotenv(os.path.join(_WORKFLOW_DIR, ".env"), override=False)
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=False)

    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")
    if not air_host or not api_token:
        print(
            "Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env\n"
            f"  Workflow-local : {os.path.join(_WORKFLOW_DIR, '.env')}\n"
            f"  Repository root: {os.path.join(_PROJECT_ROOT, '.env')}",
            file=sys.stderr,
        )
        sys.exit(1)
    return air_host.rstrip("/"), api_token


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

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
            backoff *= _BACKOFF_FACTOR
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
            backoff *= _BACKOFF_FACTOR
    raise last_exc


def api_get(
    air_host: str,
    api_token: str,
    path: str,
    params=None,
    timeout=_DEFAULT_TIMEOUT,
    retries=_MAX_RETRIES,
):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.get,
        url,
        headers=_headers(api_token),
        params=params,
        timeout=timeout,
        retries=retries,
    )


def api_delete(
    air_host: str,
    api_token: str,
    path: str,
    body=None,
    timeout=_DEFAULT_TIMEOUT,
    retries=_MAX_RETRIES,
):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.delete,
        url,
        headers=_headers(api_token),
        json=body or {},
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


def paginate_get(
    air_host: str,
    api_token: str,
    path: str,
    params=None,
    page_size: int = 10_000,
    verbose: bool = False,
) -> list:
    # Strip caller-supplied pagination keys — the loop controls these.
    # Also strips any stale pageNumber=1 that callers sometimes pass as a
    # base param (which would otherwise freeze the page cursor at 1 forever).
    _pagination_keys = {"page", "pageNumber", "pageSize"}
    base_params = {k: v for k, v in (params or {}).items() if k not in _pagination_keys}

    all_entities: list = []
    page = 1
    seen_pages: set = set()
    seen_fingerprints: set = set()
    total_entity_count: Optional[int] = None  # populated from first response

    while len(seen_pages) < _MAX_ITERATIONS:
        if page in seen_pages:
            break
        seen_pages.add(page)

        # Send both 'page' and 'pageNumber': the Binalyze API uses 'pageNumber'
        # on the assets endpoint.  Sending both keeps compatibility with
        # endpoints that use 'page'.
        request_params = {
            **base_params,
            "page": page,
            "pageNumber": page,
            "pageSize": page_size,
        }
        if verbose:
            fetched = len(all_entities)
            of_total = f" of ~{total_entity_count:,}" if total_entity_count else ""
            print(f"  Fetching page {page} ({fetched:,}{of_total} fetched)...", end=" ", flush=True)

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

            # Capture total entity count from the first page so we can
            # compute totalPageCount when the server omits it.
            if total_entity_count is None:
                for _key in ("totalEntityCount", "totalCount", "totalItems"):
                    _v = result.get(_key)
                    if _v is not None:
                        try:
                            total_entity_count = int(_v)
                        except (ValueError, TypeError):
                            pass
                        break

            fp = _entity_ids_fingerprint(entities)
            if fp and fp in seen_fingerprints:
                # API returned a page we already have — the page cursor is
                # not advancing (likely the server requires 'pageNumber' but
                # only received 'page', or the server has a hard cap).
                print(
                    f"\n  Warning: page {page} returned duplicate entities — "
                    f"stopping at {len(all_entities):,} of "
                    f"~{total_entity_count:,} expected.  "
                    f"The server may not be honouring the page cursor.",
                    file=sys.stderr,
                )
                break
            if fp:
                seen_fingerprints.add(fp)

            all_entities.extend(entities)

            total_pages = result.get("totalPageCount")
            # Fall back: compute totalPageCount from totalEntityCount when
            # the server omits it.
            if not total_pages and total_entity_count and page_size:
                total_pages = -(-total_entity_count // page_size)  # ceil division

            current_page = (
                result.get("currentPage")
                or result.get("pageNumber")
                or page
            )

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
# Hostname normalisation
# ---------------------------------------------------------------------------

def _primary_host_label(name: Any) -> str:
    """First DNS label, lowercased — strips FQDN suffix for comparison."""
    if name is None:
        return ""
    s = str(name).strip().lower()
    if not s:
        return ""
    return s.split(".", 1)[0]


# ---------------------------------------------------------------------------
# Organization helpers
# ---------------------------------------------------------------------------

def pick_from_list(label: str, items: list, fmt_fn) -> dict:
    for i, item in enumerate(items, 1):
        print(fmt_fn(i, item))
    while True:
        try:
            raw = input(f"\nSelect {label} (1–{len(items)}): ").strip()
            idx = int(raw) - 1
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


def get_organization(air_host: str, api_token: str, org_id: str) -> dict:
    resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
    if not resp.ok:
        print(
            f"Could not fetch organization {org_id}: HTTP {resp.status_code}",
            file=sys.stderr,
        )
        sys.exit(1)
    data = resp.json()
    return data.get("result", data)


# ---------------------------------------------------------------------------
# A-list CSV helpers
# ---------------------------------------------------------------------------

def _detect_hostname_column(headers: List[str]) -> Optional[str]:
    lower_map = {h.strip().lower(): h for h in headers}
    for candidate in _CANDIDATE_HOSTNAME_COLUMNS:
        if candidate in lower_map:
            return lower_map[candidate]
    return None


def load_a_list(csv_path: str, hostname_column: Optional[str] = None) -> Tuple[List[str], str]:
    """
    Returns (hostnames, detected_column_name).
    Raises SystemExit on unrecoverable errors.
    """
    if not os.path.isfile(csv_path):
        print(f"A-list file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            print(f"A-list CSV appears empty: {csv_path}", file=sys.stderr)
            sys.exit(1)

        col = hostname_column
        if col is None:
            col = _detect_hostname_column(list(reader.fieldnames))
        if col is None:
            cols = ", ".join(reader.fieldnames)
            print(
                f"Cannot detect hostname column in A-list CSV.\n"
                f"  Available columns: {cols}\n"
                f"  Use --hostname-column to specify one.",
                file=sys.stderr,
            )
            sys.exit(1)

        if col not in reader.fieldnames:
            print(
                f"Column '{col}' not found in A-list CSV.\n"
                f"  Available columns: {', '.join(reader.fieldnames)}",
                file=sys.stderr,
            )
            sys.exit(1)

        hostnames = [
            row[col].strip()
            for row in reader
            if row.get(col) and row[col].strip()
        ]

    return hostnames, col


# ---------------------------------------------------------------------------
# B-list: fetch all AIR assets for the org
# ---------------------------------------------------------------------------

def fetch_b_list(air_host: str, api_token: str, org_id: str, label: str = "B-list") -> List[dict]:
    if label:
        expected = count_assets(air_host, api_token, org_id)
        if expected >= 0:
            print(
                f"Fetching AIR asset inventory ({label})..."
                f"  Expected: {expected:,} endpoints",
                flush=True,
            )
        else:
            print(f"Fetching AIR asset inventory ({label})...", flush=True)
            expected = 0

    assets = paginate_get(
        air_host,
        api_token,
        "/api/public/assets",
        params={"filter[organizationIds]": org_id},
        verbose=True,
    )

    if label and expected > 0 and len(assets) < expected:
        print(
            f"\n  Warning: fetched {len(assets):,} of {expected:,} expected endpoints "
            f"({expected - len(assets):,} missing).  "
            f"Results may be incomplete — check API pagination limits.",
            file=sys.stderr,
        )

    return assets


def count_assets(air_host: str, api_token: str, org_id: str) -> int:
    """Fast asset count — single page request, reads totalEntityCount from pagination."""
    resp = api_get(
        air_host,
        api_token,
        "/api/public/assets",
        params={"filter[organizationIds]": org_id, "page": 1, "pageSize": 1},
    )
    if not resp.ok:
        return -1
    data = resp.json()
    result = data.get("result") if isinstance(data, dict) else None
    if result and isinstance(result, dict):
        count = (
            result.get("totalEntityCount")
            or result.get("totalCount")
            or result.get("totalItems")
        )
        if count is not None:
            return int(count)
    return -1


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _fmt_last_seen(asset: dict) -> str:
    raw = asset.get("lastSeenAt") or asset.get("lastSeen") or asset.get("updatedAt")
    if not raw:
        return "never"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return str(raw)[:10]


def compare_lists(
    a_hostnames: List[str],
    b_assets: List[dict],
) -> Tuple[List[dict], List[str]]:
    """
    Returns:
      matches   — list of B-list asset dicts whose normalized name is in A-list
      not_found — A-list hostnames with no corresponding B-list entry
    """
    b_index: Dict[str, dict] = {}
    for asset in b_assets:
        label = _primary_host_label(asset.get("name"))
        if label:
            b_index[label] = asset
        full = str(asset.get("name") or "").strip().lower()
        if full and full not in b_index:
            b_index[full] = asset

    matches: List[dict] = []
    not_found: List[str] = []
    seen_ids: set = set()

    for hostname in a_hostnames:
        label = _primary_host_label(hostname)
        full = hostname.strip().lower()

        asset = b_index.get(label) or b_index.get(full)
        if asset:
            aid = _first_id(asset, "_id", "id")
            if aid not in seen_ids:
                seen_ids.add(aid)
                matches.append(asset)
        else:
            not_found.append(hostname)

    return matches, not_found


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

def _call_uninstall_api(
    air_host: str,
    api_token: str,
    org_id: str,
    endpoint_ids: List[str],
    purge: bool,
) -> Tuple[bool, str]:
    """
    Single DELETE call for the given endpoint IDs.
    Returns (ok, summary_line).
    """
    path = (
        "/api/public/assets/purge-and-uninstall"
        if purge
        else "/api/public/assets/uninstall-without-purge"
    )

    # AIR expects organizationIds as integers (consistent with other task endpoints).
    try:
        org_id_val: Any = int(org_id)
    except (ValueError, TypeError):
        org_id_val = org_id

    body = {
        "filter": {
            "organizationIds": [org_id_val],
            "endpointIds": endpoint_ids,
        }
    }

    resp = api_delete(air_host, api_token, path, body=body)

    try:
        resp_data = resp.json()
        api_success = resp_data.get("success")
    except Exception:
        resp_data = {}
        api_success = None

    summary = f"HTTP {resp.status_code}"
    if api_success is not None:
        summary += f"  success={api_success}"

    return resp.ok and api_success is not False, summary


def uninstall_assets(
    air_host: str,
    api_token: str,
    org_id: str,
    assets: List[dict],
    purge: bool,
) -> Tuple[int, List[str]]:
    """
    Calls the uninstall API for the given assets.
    Returns (accepted_count, error_messages).

    Note: a 200 / success=True response means AIR *accepted* the request.
    For uninstall-without-purge, offline endpoints will only be removed once
    the agent next connects and processes the command. Use purge=True to
    force-remove offline/stale endpoints immediately.
    """
    endpoint_ids = [_first_id(a, "_id", "id") for a in assets]
    endpoint_ids = [eid for eid in endpoint_ids if eid is not None]

    if not endpoint_ids:
        return 0, ["No valid endpoint IDs to uninstall."]

    ok, summary = _call_uninstall_api(air_host, api_token, org_id, endpoint_ids, purge)

    print(f"  [{summary}]", flush=True)

    if ok:
        return len(endpoint_ids), []

    return 0, [summary]


def check_still_present(
    air_host: str,
    api_token: str,
    org_id: str,
    matched_assets: List[dict],
) -> List[dict]:
    """Re-fetches the asset list and returns any matched assets still present."""
    time.sleep(5)
    current = fetch_b_list(air_host, api_token, org_id, label="")
    current_ids = {_first_id(a, "_id", "id") for a in current}
    still_present = [
        a for a in matched_assets
        if _first_id(a, "_id", "id") in current_ids
    ]
    return still_present


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _sep(char="─", width=70) -> str:
    return char * width


def print_matches(matches: List[dict]) -> None:
    if not matches:
        print("  (none)")
        return
    for asset in matches:
        aid = _first_id(asset, "_id", "id", default="?")
        name = asset.get("name") or "?"
        last_seen = _fmt_last_seen(asset)
        online = asset.get("onlineStatus") or asset.get("status") or "?"
        print(f"  {name:<30}  id={aid}  last seen={last_seen}  status={online}")


def print_not_found(not_found: List[str]) -> None:
    if not not_found:
        print("  (none)")
        return
    for h in not_found:
        print(f"  {h}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compare a decommissioned-host CSV against AIR assets and uninstall matches.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv\n"
            "  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --org-id 362 --yes\n"
            "  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --dry-run\n"
            "  python workflows/asset_decommission/asset_decommission.py --a-list hosts.csv --purge --yes\n"
        ),
    )
    p.add_argument("--a-list", metavar="PATH", help="Path to A-list CSV of decommissioned hostnames.")
    p.add_argument("--hostname-column", metavar="COL", help="Column header for hostnames in A-list (default: auto-detect).")
    p.add_argument("--org-id", metavar="ID", help="AIR organization ID (skips interactive selection).")
    p.add_argument(
        "--purge",
        action="store_true",
        help=(
            "Use purge-and-uninstall instead of uninstall-without-purge. "
            "WARNING: permanently deletes all previously collected evidence for these endpoints."
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="Preview matches but do not call the uninstall API.")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    p.add_argument("--version", action="version", version=f"%(prog)s {_SCRIPT_VERSION}")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  Binalyze AIR — Asset Decommission  (v{_SCRIPT_VERSION})")
    print(f"{'='*70}\n")

    # ------------------------------------------------------------------
    # 1. Load config
    # ------------------------------------------------------------------
    air_host, api_token = load_config()

    # ------------------------------------------------------------------
    # 2. Resolve organization
    # ------------------------------------------------------------------
    if args.org_id:
        org = get_organization(air_host, api_token, args.org_id)
    else:
        org = select_organization(air_host, api_token)

    org_id = str(_first_id(org, "_id", "id", default=""))
    org_name = org.get("name") or org_id
    if not org_id:
        print("Could not determine organization ID.", file=sys.stderr)
        sys.exit(1)
    print(f"\nOrganization : {org_name}  (ID: {org_id})")

    # ------------------------------------------------------------------
    # 3. Load A-list CSV
    # ------------------------------------------------------------------
    a_list_path = args.a_list
    if not a_list_path:
        a_list_path = input("\nPath to A-list CSV: ").strip()
        if not a_list_path:
            print("No A-list path provided.", file=sys.stderr)
            sys.exit(1)

    a_hostnames, detected_col = load_a_list(a_list_path, args.hostname_column)
    print(f"\n[A-list]  Loaded {len(a_hostnames)} hostnames from '{os.path.basename(a_list_path)}'  (column: '{detected_col}')")

    if not a_hostnames:
        print("A-list contains no hostnames. Nothing to do.", file=sys.stderr)
        sys.exit(0)

    # ------------------------------------------------------------------
    # 4. Fetch B-list (all AIR assets for this org)
    # ------------------------------------------------------------------
    b_assets = fetch_b_list(air_host, api_token, org_id)
    print(f"[B-list]  Fetched {len(b_assets)} assets from org '{org_name}'")

    if not b_assets:
        print("\nNo assets found in AIR for this organization. Nothing to compare.", file=sys.stderr)
        sys.exit(0)

    # ------------------------------------------------------------------
    # 5. Compare
    # ------------------------------------------------------------------
    matches, not_found = compare_lists(a_hostnames, b_assets)

    print(f"\n{_sep()}")
    print(f"COMPARISON RESULTS")
    print(_sep())

    print(f"\nMatches — found in AIR and will be uninstalled ({len(matches)}):")
    print_matches(matches)

    print(f"\nNot found in AIR — already removed or never registered ({len(not_found)}):")
    print_not_found(not_found)

    print(f"\n{_sep()}")
    print(f"SUMMARY")
    print(_sep())
    assets_before = count_assets(air_host, api_token, org_id)
    assets_before_display = str(assets_before) if assets_before >= 0 else str(len(b_assets)) + " (estimated)"
    print(f"  Assets before removal : {assets_before_display}")
    print(f"  Matches to uninstall  : {len(matches)}")
    print(f"  Not found in AIR      : {len(not_found)}")

    # ------------------------------------------------------------------
    # 6. Dry run / no matches guard
    # ------------------------------------------------------------------
    if not matches:
        print("\nNo matching assets found. Nothing to uninstall.")
        sys.exit(0)

    if args.dry_run:
        print(f"\n[DRY RUN] Would uninstall {len(matches)} endpoint(s). No changes made.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # 7. Confirmation prompt
    # ------------------------------------------------------------------
    action_label = "purge-and-uninstall" if args.purge else "uninstall-without-purge"

    print()
    if args.purge:
        print(
            "  WARNING: --purge will permanently delete all previously collected\n"
            "           evidence for these endpoints. This cannot be undone.\n"
        )

    if not args.yes:
        answer = input(
            f"Uninstall {len(matches)} endpoint(s) [{action_label}]? [y/N]: "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. No changes made.")
            sys.exit(0)

    # ------------------------------------------------------------------
    # 8. Uninstall
    # ------------------------------------------------------------------
    print(f"\nUninstalling {len(matches)} endpoint(s)...", end=" ", flush=True)
    success_count, errors = uninstall_assets(air_host, api_token, org_id, matches, purge=args.purge)

    if errors:
        print("FAILED")
        for err in errors:
            print(f"  Error: {err}", file=sys.stderr)
        sys.exit(1)

    print(f"done ({success_count} / {len(matches)})")

    # ------------------------------------------------------------------
    # 9. Verify removal and final summary
    # ------------------------------------------------------------------
    print("\nVerifying removal (re-fetching asset inventory)...", flush=True)
    still_present = check_still_present(air_host, api_token, org_id, matches)
    confirmed_removed = len(matches) - len(still_present)

    assets_after = count_assets(air_host, api_token, org_id)
    assets_after_display = str(assets_after) if assets_after >= 0 else "unknown"

    print(f"\n{_sep()}")
    print(f"REMOVAL COMPLETE")
    print(_sep())
    print(f"  Assets before removal     : {assets_before_display}")
    print(f"  Assets after removal      : {assets_after_display}")
    print(f"  Confirmed removed         : {confirmed_removed}")
    print(f"  Still present in AIR      : {len(still_present)}")
    print(f"  Uninstall mode            : {action_label}")

    if still_present and not args.purge:
        print()
        print("  NOTE: The following endpoints are still visible in AIR.")
        print("  uninstall-without-purge queues a command for the agent to process")
        print("  on next check-in. Offline/decommissioned endpoints that will never")
        print("  reconnect must be force-removed with --purge.")
        print()
        print("  Still present:")
        for asset in still_present:
            name = asset.get("name") or "?"
            aid = _first_id(asset, "_id", "id", default="?")
            last_seen = _fmt_last_seen(asset)
            print(f"    {name:<30}  id={aid}  last seen={last_seen}")
        print()
        if not args.yes:
            answer = input(
                f"Force-remove {len(still_present)} offline endpoint(s) with --purge? [y/N]: "
            ).strip().lower()
        else:
            answer = "y"
            print(f"  Auto-confirming force-remove (--yes).")

        if answer in ("y", "yes"):
            print(f"\nForce-removing {len(still_present)} endpoint(s) (purge-and-uninstall)...", end=" ", flush=True)
            purge_count, purge_errors = uninstall_assets(
                air_host, api_token, org_id, still_present, purge=True
            )
            if purge_errors:
                print("FAILED")
                for err in purge_errors:
                    print(f"  Error: {err}", file=sys.stderr)
            else:
                print(f"done ({purge_count} / {len(still_present)})")
                print("\nVerifying force-removal...", flush=True)
                still_after_purge = check_still_present(air_host, api_token, org_id, still_present)
                assets_final = count_assets(air_host, api_token, org_id)
                assets_final_display = str(assets_final) if assets_final >= 0 else "unknown"
                print(f"\n  Assets after force-remove : {assets_final_display}")
                print(f"  Confirmed removed         : {purge_count - len(still_after_purge)}")
                if still_after_purge:
                    print(f"  Still present             : {len(still_after_purge)}")
                    for asset in still_after_purge:
                        print(f"    {asset.get('name') or '?'}")
        else:
            print("  Force-remove skipped. Endpoints remain in AIR pending agent check-in.")

    print()


if __name__ == "__main__":
    main()

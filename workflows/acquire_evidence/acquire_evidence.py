"""
Acquire evidence from an endpoint via the Binalyze AIR API.

Replicates the console acquisition workflow programmatically:
  1. Validate organization
  2. Find endpoint (asset) by name or ID
  3. Select an acquisition profile
  4. Create or reuse a case
  5. Start acquisition (POST /api/public/acquisitions/acquire)
  6. Optionally poll until task completes

NOTE: The POST /api/public/acquisitions/acquire request body schema is inferred from
SDK patterns and may need adjustment. The script prints the full request and
response bodies to aid debugging. Use --dry-run to preview without sending.
"""

import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List

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

DEFAULT_POLL_INTERVAL = 10
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}
CASE_VISIBILITY_VALUES = frozenset(
    ("public-to-organization", "private-to-users")
)

# Built-in AIR acquisition profile slugs (sent as-is rather than using _id).
_PRESET_PROFILES = frozenset((
    "browsing-history",
    "compromise-assessment",
    "event-logs",
    "full",
    "memory-ram-pagefile",
    "quick",
))


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


def acquisition_profile_id_for_acquire(profile_from_list: Dict[str, Any], profile_arg: str) -> str:
    """
    Return the value to send as `acquisitionProfileId`.
    For built-ins, AIR expects the preset slug. For custom profiles, use the row _id.
    """
    arg_norm = (profile_arg or "").strip().lower()
    if arg_norm in _PRESET_PROFILES:
        return arg_norm
    ref = _first_id(profile_from_list, "_id", "id")
    ref_s = str(ref) if ref is not None else ""
    if not ref_s:
        raise RuntimeError("Acquisition profile row has no _id/id; cannot derive profile id.")
    return ref_s


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------

def _normalize_case_visibility(case_visibility):
    v = (case_visibility or "public-to-organization").strip()
    if v not in CASE_VISIBILITY_VALUES:
        print(
            "Error: case visibility must be 'public-to-organization' or 'private-to-users'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


def validate_org(air_host, api_token, org_id):
    resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
    if not resp.ok:
        print(f"Error: Could not fetch organization {org_id}: HTTP {resp.status_code}",
              file=sys.stderr)
        sys.exit(1)
    org = resp.json().get("result", resp.json())
    name = org.get("name", "Unknown")
    print(f"  Organization: {name} ({org_id})")
    return org


def find_endpoint(air_host, api_token, identifier, org_id):
    """Find an endpoint by name or ID. Tries search first, falls back to direct get."""
    resp = api_get(air_host, api_token, f"/api/public/assets/{identifier}")
    if resp.ok:
        asset = resp.json().get("result", resp.json())
        if asset.get("_id"):
            return asset

    params = {
        "filter[organizationIds]": org_id,
        "search": identifier,
    }
    assets = paginate_get(
        air_host, api_token, "/api/public/assets", params=params, verbose=False,
    )
    if not assets:
        print(f"Error: No endpoint found matching '{identifier}'", file=sys.stderr)
        sys.exit(1)

    ident_norm = identifier.strip().lower()
    for asset in assets:
        if (asset.get("name") or "").strip().lower() == ident_norm:
            return asset

    def _label(n):
        s = (n or "").strip().lower()
        return s.split(".", 1)[0] if s else ""

    by_label = [a for a in assets if _label(a.get("name")) == ident_norm]
    if len(by_label) == 1:
        return by_label[0]

    if len(assets) == 1:
        return assets[0]

    print(f"\n  Multiple endpoints match '{identifier}':\n")
    for i, a in enumerate(assets, 1):
        name = a.get("name", "Unknown")
        platform = a.get("platform", "?")
        ip = a.get("ipAddress", "?")
        print(f"  [{i:>3}]  {name}  ({platform}, {ip})")

    print()
    while True:
        try:
            choice = input(f"Select endpoint [1-{len(assets)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(assets):
                return assets[idx]
            print(f"  Enter a number between 1 and {len(assets)}.")
        except (ValueError, EOFError):
            print(f"  Enter a number between 1 and {len(assets)}.")


def list_profiles(air_host, api_token, org_id):
    params = {"filter[organizationIds]": org_id}
    return paginate_get(
        air_host,
        api_token,
        "/api/public/acquisitions/profiles",
        params=params,
        verbose=False,
    )


def resolve_profile(air_host, api_token, org_id, profile_id=None, profile_name=None):
    """Find a profile by ID, by name, or let the user pick interactively."""
    profiles = list_profiles(air_host, api_token, org_id)
    if not profiles:
        print("Error: No acquisition profiles found.", file=sys.stderr)
        sys.exit(1)

    if profile_id:
        for p in profiles:
            if str(p.get("_id")) == str(profile_id) or str(p.get("id")) == str(profile_id):
                return p
        print(f"Error: No profile found with ID '{profile_id}'", file=sys.stderr)
        sys.exit(1)

    if profile_name:
        for p in profiles:
            if (p.get("name") or "").lower() == profile_name.lower():
                return p
        print(f"Error: No profile found with name '{profile_name}'", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*70}")
    print("ACQUISITION PROFILES")
    print(f"{'='*70}\n")

    for i, p in enumerate(profiles, 1):
        name = p.get("name", "Unnamed")
        pid = _first_id(p, "_id", "id", default="?")
        print(f"  [{i:>3}]  {name}  (ID: {pid})")

    print()
    while True:
        try:
            choice = input(f"Select profile [1-{len(profiles)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(profiles):
                return profiles[idx]
            print(f"  Enter a number between 1 and {len(profiles)}.")
        except (ValueError, EOFError):
            print(f"  Enter a number between 1 and {len(profiles)}.")


def resolve_case(
    air_host,
    api_token,
    org_id,
    case_id=None,
    case_name=None,
    endpoint_name="unknown",
    case_visibility=None,
):
    """Fetch an existing case or create a new one."""
    if case_id:
        resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
        if not resp.ok:
            print(f"Error: Could not fetch case {case_id}: HTTP {resp.status_code}",
                  file=sys.stderr)
            sys.exit(1)
        case = resp.json().get("result", resp.json())
        status = case.get("status", "unknown")
        if status not in ("open",):
            print(f"Warning: Case '{case.get('name')}' has status '{status}' (not open).",
                  file=sys.stderr)
        return case

    if not case_name:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        case_name = f"Acquisition - {endpoint_name} - {date_str}"

    visibility = _normalize_case_visibility(case_visibility)
    body = {
        "name": case_name,
        "organizationId": org_id,
        "visibility": visibility,
    }
    print(f"  Creating case: {case_name}")
    resp = api_post(air_host, api_token, "/api/public/cases", body=body)
    if not resp.ok:
        print(f"Error: Failed to create case: HTTP {resp.status_code}", file=sys.stderr)
        print(f"  Response: {resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    case = resp.json().get("result", resp.json())
    print(f"  Case created: {_first_id(case, '_id', 'id')}")
    return case


def assign_acquisition(air_host, api_token, case_id, endpoint_name, profile_id,
                       org_id, dry_run=False):
    """Call POST /api/public/acquisitions/acquire. Prints request/response for debugging."""
    body = {
        "caseId": case_id,
        "droneConfig": {"autoPilot": False, "enabled": False},
        "taskConfig": {"choice": "use-policy"},
        "acquisitionProfileId": profile_id,
        "filter": {"name": endpoint_name, "organizationIds": [int(org_id)]},
    }

    print(f"\n{'─'*70}")
    print("ACQUIRE EVIDENCE (POST /acquisitions/acquire)")
    print(f"{'─'*70}\n")
    print(f"  POST /api/public/acquisitions/acquire")
    print(f"  Request body:")
    print(f"  {json.dumps(body, indent=4)}")

    if dry_run:
        print(f"\n  [DRY RUN] Stopping before API call.")
        return None

    resp = api_post(air_host, api_token, "/api/public/acquisitions/acquire", body=body)

    print(f"\n  Response: HTTP {resp.status_code}")
    try:
        resp_body = resp.json()
        print(f"  {json.dumps(resp_body, indent=4)[:2000]}")
    except Exception:
        print(f"  {resp.text[:2000]}")

    if not resp.ok:
        print(f"\nError: POST /acquisitions/acquire failed with HTTP {resp.status_code}.", file=sys.stderr)
        print("The request body schema is a best guess and may need adjustment.",
              file=sys.stderr)
        print("Check the response above for clues on the expected format.",
              file=sys.stderr)
        sys.exit(1)

    return resp_body


def poll_task(air_host, api_token, task_id, interval=DEFAULT_POLL_INTERVAL):
    """Poll GET /tasks/{id} until the task reaches a terminal state."""
    print(f"\n{'─'*70}")
    print(f"POLLING TASK: {task_id}")
    print(f"{'─'*70}\n")

    start = time.time()
    while True:
        resp = api_get(air_host, api_token, f"/api/public/tasks/{task_id}")
        if not resp.ok:
            print(f"  Poll error: HTTP {resp.status_code}", file=sys.stderr)
            break

        task = resp.json().get("result", resp.json())
        status = task.get("status", "unknown")
        progress = task.get("progress", 0)
        elapsed = time.time() - start

        print(f"  [{elapsed:>6.0f}s]  status={status}  progress={progress}%", flush=True)

        if status.lower() in TERMINAL_STATUSES:
            print(f"\n  Task finished: {status} (elapsed: {elapsed:.0f}s)")
            duration = task.get("duration")
            if duration:
                print(f"  Server-reported duration: {duration / 1000:.1f}s")
            return task

        time.sleep(interval)

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_usage():
    print("Usage: python workflows/acquire_evidence/acquire_evidence.py <org_id> <endpoint_name_or_id> [options]")
    print()
    print("Arguments:")
    print("  org_id                Organization ID")
    print("  endpoint_name_or_id   Endpoint hostname or asset ID")
    print()
    print("Options:")
    print("  --case-id ID          Use an existing case (skip creation)")
    print("  --case-name NAME      Create a new case with this name")
    print("  --profile-id ID       Acquisition profile ID (skip interactive selection)")
    print("  --profile-name NAME   Find acquisition profile by name")
    print("  --poll                Poll for task completion after assignment")
    print("  --poll-interval SECS  Seconds between status checks (default: 10)")
    print("  --dry-run             Show what would be sent without calling POST /acquisitions/acquire")
    print("  --case-visibility V   public-to-organization | private-to-users (default: public)")
    print()
    print("Examples:")
    print("  python workflows/acquire_evidence/acquire_evidence.py 362 WORKSTATION-01")
    print("  python workflows/acquire_evidence/acquire_evidence.py 362 WORKSTATION-01 --profile-name 'Full' --poll")
    print("  python workflows/acquire_evidence/acquire_evidence.py 362 WORKSTATION-01 --case-id C-2026-00001 --dry-run")


def parse_args(argv):
    args = {
        "org_id": None,
        "endpoint": None,
        "case_id": None,
        "case_name": None,
        "profile_id": None,
        "profile_name": None,
        "poll": False,
        "poll_interval": DEFAULT_POLL_INTERVAL,
        "dry_run": False,
        "case_visibility": "public-to-organization",
    }

    positional = []
    i = 0
    while i < len(argv):
        if argv[i] == "--case-id" and i + 1 < len(argv):
            args["case_id"] = argv[i + 1]
            i += 2
        elif argv[i] == "--case-name" and i + 1 < len(argv):
            args["case_name"] = argv[i + 1]
            i += 2
        elif argv[i] == "--profile-id" and i + 1 < len(argv):
            args["profile_id"] = argv[i + 1]
            i += 2
        elif argv[i] == "--profile-name" and i + 1 < len(argv):
            args["profile_name"] = argv[i + 1]
            i += 2
        elif argv[i] == "--poll-interval" and i + 1 < len(argv):
            args["poll_interval"] = int(argv[i + 1])
            i += 2
        elif argv[i] == "--poll":
            args["poll"] = True
            i += 1
        elif argv[i] == "--dry-run":
            args["dry_run"] = True
            i += 1
        elif argv[i] == "--case-visibility" and i + 1 < len(argv):
            args["case_visibility"] = argv[i + 1]
            i += 2
        elif argv[i] in ("--help", "-h"):
            print_usage()
            sys.exit(0)
        else:
            positional.append(argv[i])
            i += 1

    if len(positional) >= 1:
        args["org_id"] = positional[0]
    if len(positional) >= 2:
        args["endpoint"] = positional[1]

    return args


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    air_host, api_token = load_config()
    args = parse_args(sys.argv[1:])

    if not args["org_id"] or not args["endpoint"]:
        print_usage()
        sys.exit(1)

    org_id = args["org_id"]
    endpoint_identifier = args["endpoint"]

    try:
        print("Validating organization...", flush=True)
        validate_org(air_host, api_token, org_id)

        print(f"\nFinding endpoint '{endpoint_identifier}'...", flush=True)
        asset = find_endpoint(air_host, api_token, endpoint_identifier, org_id)
        endpoint_id = _first_id(asset, "_id", "id")
        endpoint_name = asset.get("name", "Unknown")
        print(f"  Endpoint: {endpoint_name}")
        print(f"  ID:       {endpoint_id}")
        print(f"  OS:       {asset.get('os', 'N/A')} ({asset.get('platform', 'N/A')})")
        print(f"  IP:       {asset.get('ipAddress', 'N/A')}")

        print(f"\nResolving acquisition profile...", flush=True)
        profile = resolve_profile(
            air_host, api_token, org_id,
            profile_id=args["profile_id"],
            profile_name=args["profile_name"],
        )
        profile_name = profile.get("name", "Unknown")
        profile_id = acquisition_profile_id_for_acquire(profile, profile_name)
        print(f"  Profile: {profile_name} (acquire profileId={profile_id!r})")

        print(f"\nResolving case...", flush=True)
        case = resolve_case(
            air_host, api_token, org_id,
            case_id=args["case_id"],
            case_name=args["case_name"],
            endpoint_name=endpoint_name,
            case_visibility=args["case_visibility"],
        )
        case_id = _first_id(case, "_id", "id")
        print(f"  Case: {case.get('name', 'Unknown')} ({case_id})")
        print(f"  Status: {case.get('status', 'N/A')}")

        result = assign_acquisition(
            air_host, api_token, case_id, endpoint_name, profile_id, org_id,
            dry_run=args["dry_run"],
        )

        if args["dry_run"] or result is None:
            print("\nDone (dry run).\n")
            sys.exit(0)

        task_id = None
        r = result.get("result", result)
        if isinstance(r, dict):
            task_id = _first_id(r, "taskId", "_id", "id")
        elif isinstance(r, list) and r:
            task_id = _first_id(r[0], "taskId", "_id", "id")

        if task_id:
            print(f"\n  Task ID: {task_id}")

        if args["poll"] and task_id:
            poll_task(air_host, api_token, task_id, interval=args["poll_interval"])
        elif args["poll"] and not task_id:
            print("\n  Warning: --poll requested but could not extract task ID from response.",
                  file=sys.stderr)

        print("\nDone.\n")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()

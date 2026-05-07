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

import os
import sys
import json
import time
from datetime import datetime, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))

from lib.api_client import load_config, api_get, api_post
from lib.binalyze_acquisitions import acquisition_profile_id_for_acquire
from lib.pagination import paginate_get


DEFAULT_POLL_INTERVAL = 10
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}
CASE_VISIBILITY_VALUES = frozenset(
    ("public-to-organization", "private-to-users")
)


def _normalize_case_visibility(case_visibility):
    v = (case_visibility or "public-to-organization").strip()
    if v not in CASE_VISIBILITY_VALUES:
        print(
            "Error: case visibility must be 'public-to-organization' or 'private-to-users'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

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
    # Try direct ID lookup first
    resp = api_get(air_host, api_token, f"/api/public/assets/{identifier}")
    if resp.ok:
        asset = resp.json().get("result", resp.json())
        if asset.get("_id"):
            return asset

    # Search by name
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
    # Exact name match preferred
    for asset in assets:
        if (asset.get("name") or "").strip().lower() == ident_norm:
            return asset

    # Substring/fuzzy search: disambiguate FQDN vs short hostname (first DNS label).
    def _label(n):
        s = (n or "").strip().lower()
        return s.split(".", 1)[0] if s else ""

    by_label = [a for a in assets if _label(a.get("name")) == ident_norm]
    if len(by_label) == 1:
        return by_label[0]

    if len(assets) == 1:
        return assets[0]

    # Multiple matches -- interactive selection
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

    # Interactive selection
    print(f"\n{'='*70}")
    print("ACQUISITION PROFILES")
    print(f"{'='*70}\n")

    for i, p in enumerate(profiles, 1):
        name = p.get("name", "Unnamed")
        pid = p.get("_id") or p.get("id") or "?"
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
    print(f"  Case created: {case.get('_id') or case.get('id')}")
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
    print("Usage: python3 scripts/api_scripts/case_acquire.py <org_id> <endpoint_name_or_id> [options]")
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
    print("  python3 scripts/api_scripts/case_acquire.py 362 WORKSTATION-01")
    print("  python3 scripts/api_scripts/case_acquire.py 362 WORKSTATION-01 --profile-name 'Full' --poll")
    print("  python3 scripts/api_scripts/case_acquire.py 362 WORKSTATION-01 --case-id C-2026-00001 --dry-run")


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
        # Step 1: Validate organization
        print("Validating organization...", flush=True)
        validate_org(air_host, api_token, org_id)

        # Step 2: Find endpoint
        print(f"\nFinding endpoint '{endpoint_identifier}'...", flush=True)
        asset = find_endpoint(air_host, api_token, endpoint_identifier, org_id)
        endpoint_id = asset.get("_id") or asset.get("id")
        endpoint_name = asset.get("name", "Unknown")
        print(f"  Endpoint: {endpoint_name}")
        print(f"  ID:       {endpoint_id}")
        print(f"  OS:       {asset.get('os', 'N/A')} ({asset.get('platform', 'N/A')})")
        print(f"  IP:       {asset.get('ipAddress', 'N/A')}")

        # Step 3: Resolve acquisition profile
        print(f"\nResolving acquisition profile...", flush=True)
        profile = resolve_profile(
            air_host, api_token, org_id,
            profile_id=args["profile_id"],
            profile_name=args["profile_name"],
        )
        profile_name = profile.get("name", "Unknown")
        profile_id = acquisition_profile_id_for_acquire(profile, profile_name)
        print(f"  Profile: {profile_name} (acquire profileId={profile_id!r})")

        # Step 4: Resolve case
        print(f"\nResolving case...", flush=True)
        case = resolve_case(
            air_host, api_token, org_id,
            case_id=args["case_id"],
            case_name=args["case_name"],
            endpoint_name=endpoint_name,
            case_visibility=args["case_visibility"],
        )
        case_id = case.get("_id") or case.get("id")
        print(f"  Case: {case.get('name', 'Unknown')} ({case_id})")
        print(f"  Status: {case.get('status', 'N/A')}")

        # Step 5: POST /acquisitions/acquire
        result = assign_acquisition(
            air_host, api_token, case_id, endpoint_name, profile_id, org_id,
            dry_run=args["dry_run"],
        )

        if args["dry_run"] or result is None:
            print("\nDone (dry run).\n")
            sys.exit(0)

        # Step 6: Poll for completion (optional)
        task_id = None
        r = result.get("result", result)
        if isinstance(r, dict):
            task_id = r.get("taskId") or r.get("_id") or r.get("id")
        elif isinstance(r, list) and r:
            task_id = r[0].get("taskId") or r[0].get("_id") or r[0].get("id")

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

"""
Check isolation status for Binalyze AIR endpoints.

Two modes:

1) Per endpoint (default): resolve hostnames or asset IDs, read asset tasks, and
   summarize the latest isolation-like task (same heuristics as workflow_isolation).

2) Inventory via POST /api/public/assets/filter: use --list-isolated to return all
   assets the server considers isolated (preset body in lib/binalyze_asset_filter.py).
   If your tenant rejects the default JSON shape, use --filter-body-file with a
   body captured from the Binalyze UI.

Run from repository root:
  python scripts/api_scripts/isolation_status.py <org_id> HOST1 [HOST2 ...]
  python scripts/api_scripts/isolation_status.py <org_id> --list-isolated
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.binalyze_asset_filter import (
    filter_assets_client_isolated_only,
    isolated_assets_filter_body,
)
from lib.binalyze_cases import AssetResolveError, find_asset_strict, validate_org
from lib.binalyze_isolation import (
    TERMINAL_STATUSES,
    _task_is_isolation,
    get_asset_tasks,
    latest_isolation_task,
)
from lib.pagination import paginate_post


def _asset_isolation_flags(asset: dict) -> Dict[str, Any]:
    """Best-effort: some tenants expose isolation on the asset document."""
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


def _summarize_endpoint(
    air_host: str,
    api_token: str,
    org_id: str,
    identifier: str,
    *,
    include_asset_flags: bool,
    list_all_isolation: bool,
) -> dict:
    asset = find_asset_strict(air_host, api_token, identifier, org_id)
    eid = asset.get("_id") or asset.get("id")
    hostname = asset.get("name")
    tasks = get_asset_tasks(air_host, api_token, str(eid))
    iso_tasks = [t for t in tasks if _task_is_isolation(t)]
    latest = latest_isolation_task(tasks)

    row: Dict[str, Any] = {
        "identifier": identifier,
        "hostname": hostname,
        "asset_id": str(eid),
        "total_tasks": len(tasks),
        "isolation_task_count": len(iso_tasks),
        "latest_isolation_task": None,
    }

    if include_asset_flags:
        row["asset_isolation_fields"] = _asset_isolation_flags(asset)

    if latest:
        st = (latest.get("status") or "").lower()
        row["latest_isolation_task"] = {
            "name": latest.get("name"),
            "type": latest.get("type"),
            "status": latest.get("status"),
            "progress": latest.get("progress"),
            "createdAt": latest.get("createdAt"),
            "updatedAt": latest.get("updatedAt"),
            "taskId": latest.get("taskId") or latest.get("_id") or latest.get("id"),
            "is_terminal": st in TERMINAL_STATUSES,
        }

    if list_all_isolation and iso_tasks:
        row["all_isolation_tasks"] = [
            {
                "name": t.get("name"),
                "type": t.get("type"),
                "status": t.get("status"),
                "createdAt": t.get("createdAt"),
                "taskId": t.get("taskId") or t.get("_id") or t.get("id"),
            }
            for t in sorted(
                iso_tasks,
                key=lambda x: x.get("createdAt") or x.get("updatedAt") or "",
            )
        ]

    return row


def _print_task_table(rows: List[dict]) -> None:
    if not rows:
        return
    print(f"\n{'='*100}")
    print(f"{'Identifier':<24} {'Hostname':<28} {'Latest isolation status':<22} {'Terminal':<10} {'Task ID':<18}")
    print("=" * 100)
    for r in rows:
        latest = r.get("latest_isolation_task") or {}
        status = latest.get("status") if latest else "(no isolation task found)"
        term = str(latest.get("is_terminal")) if latest else "N/A"
        tid = str(latest.get("taskId") or "") if latest else ""
        hn = (r.get("hostname") or "")[:26]
        ident = (r.get("identifier") or "")[:22]
        print(f"{ident:<24} {hn:<28} {str(status):<22} {str(term):<10} {tid:<18}")
    print("=" * 100)
    print(
        "\nNote: Status reflects the latest isolation-like task on the asset. "
        "If none appears, the endpoint may never have had an isolation task, or "
        "task naming differs on your tenant (use --verbose)."
    )


def _print_filter_inventory(assets: List[dict]) -> None:
    print(f"\n{'='*110}")
    print(f"{'Hostname':<34} {'Asset _id':<26} {'IP':<16} {'Isolation-related fields'}")
    print("=" * 110)
    for a in assets:
        hn = (a.get("name") or "")[:32]
        aid = str(a.get("_id") or a.get("id") or "")[:24]
        ip = (a.get("ipAddress") or "")[:14]
        flags = _asset_isolation_flags(a)
        extra = json.dumps(flags, ensure_ascii=False) if flags else "(none in JSON)"
        if len(extra) > 52:
            extra = extra[:49] + "..."
        print(f"{hn:<34} {aid:<26} {ip:<16} {extra}")
    print("=" * 110)
    print(f"\nTotal assets from filter: {len(assets)}")


def _write_json_payload(path: str | None, payload: dict) -> None:
    text = json.dumps(payload, indent=2, default=str)
    if path is None or path in ("-", ""):
        print("\n" + text)
        return
    out_path = os.path.expanduser(path)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\nWrote JSON to {out_path}")


def _run_list_isolated(
    air_host: str,
    api_token: str,
    org_id: str,
    *,
    filter_body_file: str | None,
    page_size: int,
    quiet: bool,
    json_path: str | None,
    no_client_filter: bool,
) -> None:
    if filter_body_file:
        with open(os.path.expanduser(filter_body_file), encoding="utf-8") as f:
            body = json.load(f)
        if not isinstance(body, dict):
            raise ValueError("--filter-body-file must contain a JSON object")
    else:
        body = isolated_assets_filter_body(org_id)

    print(f"Host: {air_host}")
    print(f"Organization ID: {org_id}")
    print("\nValidating organization...", flush=True)
    org = validate_org(air_host, api_token, org_id)
    print(f"  {org.get('name', org_id)}")

    print("\nPOST /api/public/assets/filter", flush=True)
    print(json.dumps(body, indent=2))

    assets = paginate_post(
        air_host,
        api_token,
        "/api/public/assets/filter",
        body=body,
        page_size=page_size,
        verbose=not quiet,
    )

    raw_count = len(assets)
    if not no_client_filter:
        assets = filter_assets_client_isolated_only(assets)
        if not quiet and raw_count != len(assets):
            print(
                f"\nNote: API returned {raw_count} asset(s); kept {len(assets)} with "
                f"isolationStatus isolated or isolating (or equivalent) after client-side filter.",
                flush=True,
            )

    _print_filter_inventory(assets)

    if json_path is not None:
        payload = {
            "mode": "assets_filter",
            "organization_id": org_id,
            "filter_body": body,
            "count": len(assets),
            "count_from_api": raw_count,
            "assets": assets,
        }
        _write_json_payload(json_path, payload)


def _run_per_endpoint(
    air_host: str,
    api_token: str,
    org_id: str,
    identifiers: List[str],
    *,
    verbose: bool,
    json_path: str | None,
) -> None:
    print(f"Host: {air_host}")
    print(f"Organization ID: {org_id}")
    print(f"Endpoints: {len(identifiers)}")

    print("\nValidating organization...", flush=True)
    org = validate_org(air_host, api_token, org_id)
    print(f"  {org.get('name', org_id)}")

    rows: List[dict] = []
    errors: List[str] = []

    for ident in identifiers:
        try:
            row = _summarize_endpoint(
                air_host,
                api_token,
                org_id,
                ident,
                include_asset_flags=verbose,
                list_all_isolation=verbose,
            )
            rows.append(row)
        except AssetResolveError as e:
            errors.append(f"{ident}: {e}")
        except RuntimeError as e:
            errors.append(f"{ident}: {e}")

    if errors:
        print("\nErrors:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        if not rows:
            sys.exit(1)

    _print_task_table(rows)

    if verbose:
        for r in rows:
            print(f"\n--- {r.get('identifier')} ({r.get('asset_id')}) ---")
            if r.get("asset_isolation_fields"):
                print("Asset document fields:", json.dumps(r["asset_isolation_fields"], indent=2))
            if r.get("all_isolation_tasks"):
                print("Isolation tasks (chronological):")
                for t in r["all_isolation_tasks"]:
                    print(f"  {t}")

    if json_path is not None:
        payload = {"mode": "per_endpoint_tasks", "organization_id": org_id, "results": rows, "errors": errors}
        _write_json_payload(json_path, payload)

    if errors:
        sys.exit(2)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Isolation status: per-endpoint tasks or inventory via POST /assets/filter.",
    )
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument(
        "endpoints",
        nargs="*",
        metavar="HOST_OR_ID",
        default=[],
        help="Hostname or asset _id (space-separated). Omit when using --list-isolated.",
    )
    p.add_argument(
        "--list-isolated",
        action="store_true",
        help="List isolated assets via POST /api/public/assets/filter (server-side inventory).",
    )
    p.add_argument(
        "--filter-body-file",
        metavar="PATH",
        help="Full JSON POST body for --list-isolated (overrides default isolated preset).",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=100,
        metavar="N",
        help="Page size for --list-isolated pagination (default: 100).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Less pagination logging for --list-isolated.",
    )
    p.add_argument(
        "--no-client-isolated-filter",
        action="store_true",
        help="For --list-isolated: show every row the API returned (skip client-side isolated-only filter).",
    )
    p.add_argument(
        "--json",
        metavar="PATH",
        nargs="?",
        const="-",
        help='Write JSON summary to PATH, or stdout if PATH is "-" or --json alone.',
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Per-endpoint mode: include asset fields and all isolation tasks.",
    )
    args = p.parse_args()

    air_host, api_token = load_config()
    org_id = args.org_id.strip()
    endpoints = [str(x).strip() for x in args.endpoints if str(x).strip()]

    if args.list_isolated and endpoints:
        p.error("Do not pass hostnames with --list-isolated (inventory is org-wide).")
    if not args.list_isolated and not endpoints:
        p.error("Provide at least one HOST_OR_ID, or use --list-isolated.")

    json_path: str | None = args.json

    try:
        if args.list_isolated:
            _run_list_isolated(
                air_host,
                api_token,
                org_id,
                filter_body_file=args.filter_body_file,
                page_size=args.page_size,
                quiet=args.quiet,
                json_path=json_path,
                no_client_filter=args.no_client_isolated_filter,
            )
        else:
            _run_per_endpoint(
                air_host,
                api_token,
                org_id,
                endpoints,
                verbose=args.verbose,
                json_path=json_path,
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

"""
Interactive workflow: validate endpoints, create Binalyze case, open XSOAR incident,
assign isolation tasks, poll status, prompt operator before/after server-class hosts.

Run from repository root. See workflows/isolation_xsoar/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import load_config
from lib.binalyze_cases import (
    AssetResolveError,
    fetch_case_tasks,
    find_asset_strict,
    resolve_case,
    summarize_findings_for_xsoar,
    validate_org,
)
from lib.binalyze_isolation import assign_isolation_task, poll_isolation_task
from lib.workflow_policy import (
    WorkflowPolicy,
    batches,
    is_null_hostname,
    load_policy,
    matches_server_regex,
)
from lib.xsoar_adapter import (
    XsoarAdapterError,
    build_workflow_raw_json,
    create_isolation_incident,
)


def _audit(path: Optional[str], event: str, **fields: Any) -> None:
    if not path:
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    log_dir = os.path.dirname(os.path.abspath(path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def _prompt_continue_batches(remaining: int) -> bool:
    return _prompt_yes(
        f"There are {remaining} more endpoint(s) after this batch. Continue with the next batch?"
    )


def _prompt_server_gate(hostnames: List[str], phrase: str) -> bool:
    print("\n*** SERVER-CLASS HOSTNAME(S) DETECTED (naming policy) ***")
    for h in hostnames:
        print(f"  - {h}")
    print(f"\nType exactly {phrase!r} to approve isolation for these hosts, or press Enter to abort.")
    try:
        typed = input("> ").strip()
    except EOFError:
        return False
    return typed == phrase


def _prompt_unisolate(endpoints: List[Dict[str, str]]) -> None:
    print("\n" + "=" * 70)
    print("UNISOLATE REMINDER")
    print("=" * 70)
    print(
        "When remediation is complete, release isolation in Binalyze AIR for each host.\n"
        "Use the console or API (same endpoint with isolation disable) — see docs/WORKFLOW_ISOLATION.md.\n"
    )
    for e in endpoints:
        print(f"  - {e['name']}  (asset _id={e['_id']})")
    _prompt_yes("Confirm you have reviewed isolation status / unisolate steps for these assets")


def _resolve_all_assets(
    air_host: str,
    api_token: str,
    org_id: str,
    identifiers: List[str],
) -> List[Tuple[str, dict]]:
    resolved: List[Tuple[str, dict]] = []
    errors: List[str] = []
    for ident in identifiers:
        try:
            asset = find_asset_strict(air_host, api_token, ident, org_id)
            resolved.append((ident, asset))
        except AssetResolveError as e:
            errors.append(f"{ident}: {e}")
    if errors:
        print("Asset resolution failed:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        raise SystemExit(1)
    return resolved


def _guard_hostnames(resolved: List[Tuple[str, dict]]) -> None:
    bad = []
    for ident, asset in resolved:
        name = asset.get("name")
        if is_null_hostname(name):
            aid = asset.get("_id") or asset.get("id")
            bad.append(f"identifier={ident!r} asset_id={aid!r} name={name!r}")
    if bad:
        print(
            "Refusing to run: null/blank hostname is a critical security issue for this workflow.",
            file=sys.stderr,
        )
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        raise SystemExit(1)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Binalyze + XSOAR isolation workflow (interactive CLI).",
    )
    p.add_argument("org_id", help="Binalyze organization ID")
    p.add_argument("endpoints", nargs="+", help="Endpoint hostname or asset _id (multiple allowed)")
    p.add_argument("--case-id", help="Use existing case ID")
    p.add_argument("--case-name", help="New case name (if not using --case-id)")
    p.add_argument("--policy", help="Path to workflow policy JSON (overrides WORKFLOW_POLICY_PATH)")
    p.add_argument("--dry-run", action="store_true", help="Do not POST isolation or XSOAR mutations")
    p.add_argument("--skip-xsoar", action="store_true", help="Skip XSOAR incident step")
    p.add_argument("--audit-log", metavar="PATH", help="Append JSONL audit events to this file")
    p.add_argument("--poll-interval", type=float, default=10.0, help="Seconds between isolation polls")
    p.add_argument("--poll-timeout", type=float, default=3600.0, help="Max seconds to poll per endpoint")
    p.add_argument("--no-poll", action="store_true", help="Do not poll for isolation task completion")
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of prompting (for smoke tests; not for production batches)",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    air_host, api_token = load_config()
    policy = load_policy(args.policy)
    audit_path = args.audit_log

    org_id = args.org_id
    identifiers = list(args.endpoints)
    run_id = str(uuid.uuid4())

    print(f"Run ID: {run_id}")
    print(f"Host: {air_host}  Org: {org_id}")
    if args.dry_run:
        print("DRY RUN: isolation and XSOAR writes will be skipped (reads still run).")

    _audit(audit_path, "start", run_id=run_id, org_id=org_id, endpoints=identifiers)

    print("\nValidating organization...")
    org = validate_org(air_host, api_token, org_id)
    print(f"  {org.get('name', org_id)}")

    print("\nResolving endpoints (strict, no prompts)...")
    resolved = _resolve_all_assets(air_host, api_token, org_id, identifiers)
    for ident, asset in resolved:
        print(f"  {ident} -> {asset.get('name')} ({asset.get('_id')})")

    _guard_hostnames(resolved)

    case_holder: Dict[str, Any] = {}
    all_endpoint_refs = [
        {"identifier": ident, "name": a.get("name"), "_id": a.get("_id") or a.get("id")}
        for ident, a in resolved
    ]

    batch_list = list(batches([x for x in resolved], policy.max_batch_size))
    total_batches = len(batch_list)

    for bi, batch in enumerate(batch_list):
        print(f"\n{'=' * 70}\nBatch {bi + 1}/{total_batches} ({len(batch)} endpoint(s))\n{'=' * 70}")

        hostnames = [b[1].get("name") or "" for b in batch]
        if policy.server_hostname_regex:
            server_hits = [h for h in hostnames if matches_server_regex(h, policy.server_hostname_regex)]
            if server_hits:
                if args.non_interactive:
                    print("Abort: server-class hosts require interactive approval.", file=sys.stderr)
                    raise SystemExit(1)
                if not _prompt_server_gate(server_hits, policy.server_confirmation_phrase):
                    print("Aborted by operator (server gate).")
                    _audit(audit_path, "aborted_server_gate", batch_index=bi, hosts=server_hits)
                    raise SystemExit(1)
                _audit(audit_path, "server_gate_approved", batch_index=bi, hosts=server_hits)

        if not args.non_interactive:
            if not _prompt_yes(f"Proceed with batch {bi + 1} ({len(batch)} host(s))? "):
                print("Stopped by operator.")
                _audit(audit_path, "aborted_batch_prompt", batch_index=bi)
                raise SystemExit(0)
        _audit(audit_path, "batch_approved", batch_index=bi, count=len(batch))

        first_name = (batch[0][1].get("name") or "host")[:80]
        if not case_holder:
            print("\nResolving Binalyze case...")
            if args.dry_run:
                cid = args.case_id or "DRY-RUN-CASE"
                print(f"  [DRY RUN] Would use case: {cid}")
                case_holder["id"] = cid
                case_holder["obj"] = {"_id": cid, "name": args.case_name or "(dry-run)"}
            else:
                case_obj = resolve_case(
                    air_host,
                    api_token,
                    org_id,
                    case_id=args.case_id,
                    case_name=args.case_name,
                    endpoint_name=first_name,
                )
                cid = case_obj.get("_id") or case_obj.get("id")
                case_holder["id"] = cid
                case_holder["obj"] = case_obj
                print(f"  Case: {case_obj.get('name')} ({cid})")
            _audit(audit_path, "case_ready", case_id=case_holder.get("id"))

        case_id = case_holder["id"]
        tasks = []
        if not args.dry_run:
            tasks = fetch_case_tasks(air_host, api_token, str(case_id))
        elif args.case_id:
            try:
                tasks = fetch_case_tasks(air_host, api_token, str(case_id))
            except RuntimeError:
                tasks = []
        findings_text = summarize_findings_for_xsoar(tasks)

        batch_refs = [
            {"identifier": ident, "name": a.get("name"), "_id": a.get("_id") or a.get("id")}
            for ident, a in batch
        ]

        if not args.skip_xsoar:
            raw = build_workflow_raw_json(
                binalyze_org_id=org_id,
                binalyze_case_id=str(case_id),
                endpoints=batch_refs,
                run_id=run_id,
            )
            incident_name = f"Binalyze isolation {case_id} batch{bi + 1}"
            details = (
                f"Binalyze AIR isolation workflow.\n"
                f"Case: {case_id}\n"
                f"Batch endpoints: {', '.join(hostnames)}\n\n"
                f"Case task summary:\n{findings_text}"
            )
            try:
                result = create_isolation_incident(
                    name=incident_name,
                    details=details,
                    incident_type=policy.xsoar_incident_type,
                    severity=policy.xsoar_severity,
                    raw_json=raw,
                    custom_fields=policy.xsoar_custom_fields or None,
                    dry_run=args.dry_run,
                )
                print("\nXSOAR step:")
                print(f"  {json.dumps(result, indent=2, default=str)[:3000]}")
                _audit(audit_path, "xsoar_incident", batch_index=bi, result_summary=str(result)[:2000])
            except XsoarAdapterError as e:
                print(f"XSOAR error: {e}", file=sys.stderr)
                if e.body:
                    print(e.body[:1500], file=sys.stderr)
                _audit(
                    audit_path,
                    "xsoar_error",
                    batch_index=bi,
                    error=str(e),
                    status_code=e.status_code,
                )
                raise SystemExit(1)
        else:
            print("\nSkipping XSOAR (--skip-xsoar).")
            _audit(audit_path, "xsoar_skipped", batch_index=bi)

        print("\nAssigning isolation tasks in Binalyze AIR...")
        for ident, asset in batch:
            eid = asset.get("_id") or asset.get("id")
            name = asset.get("name")
            print(f"  {name} ({eid})...")
            try:
                iso_case_id: Optional[str] = str(case_id) if case_id != "DRY-RUN-CASE" else None
                if args.dry_run and args.case_id:
                    iso_case_id = args.case_id
                out = assign_isolation_task(
                    air_host,
                    api_token,
                    org_id,
                    str(eid),
                    case_id=iso_case_id,
                    enable=True,
                    dry_run=args.dry_run,
                )
                if args.dry_run:
                    print(f"    [DRY RUN] would POST: {out}")
                else:
                    print(f"    OK: {json.dumps(out, default=str)[:800]}")
                _audit(
                    audit_path,
                    "isolation_assigned",
                    endpoint_id=str(eid),
                    hostname=name,
                    dry_run=args.dry_run,
                )
            except RuntimeError as e:
                print(f"    FAILED: {e}", file=sys.stderr)
                _audit(
                    audit_path,
                    "isolation_error",
                    endpoint_id=str(eid),
                    error=str(e),
                )
                raise SystemExit(1)

        if not args.no_poll and not args.dry_run:

            def vprint(msg: str) -> None:
                print(msg, flush=True)

            for ident, asset in batch:
                eid = asset.get("_id") or asset.get("id")
                name = asset.get("name")
                print(f"\nPolling isolation task for {name}...")
                last, _ = poll_isolation_task(
                    air_host,
                    api_token,
                    str(eid),
                    interval_sec=args.poll_interval,
                    timeout_sec=args.poll_timeout,
                    verbose_print=vprint,
                )
                if last:
                    print(f"  Terminal status: {last.get('status')}")
                    _audit(
                        audit_path,
                        "isolation_poll_done",
                        endpoint_id=str(eid),
                        status=last.get("status"),
                    )
                else:
                    print("  Timeout or no isolation task found on asset.", file=sys.stderr)
                    _audit(
                        audit_path,
                        "isolation_poll_timeout",
                        endpoint_id=str(eid),
                    )

        if bi < total_batches - 1:
            if args.non_interactive:
                print("Abort: further batches need interactive approval.", file=sys.stderr)
                raise SystemExit(1)
            processed = sum(len(batch_list[j]) for j in range(bi + 1))
            remaining = len(resolved) - processed
            if not _prompt_continue_batches(remaining):
                print("Stopping before remaining batches.")
                _audit(audit_path, "stopped_after_batch", batch_index=bi)
                break

    if not args.non_interactive:
        _prompt_unisolate(all_endpoint_refs)

    _audit(audit_path, "complete", run_id=run_id)
    print("\nDone.\n")


if __name__ == "__main__":
    main()

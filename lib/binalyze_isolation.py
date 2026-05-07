"""
Binalyze AIR asset isolation tasks.

Request shape aligns with the Cortex XSOAR Binalyze AIR integration parameters
(hostname, organization_id, case_id, isolation enable/disable) mapped to the
public REST style used by task-style endpoints (enabled, filter.organizationIds,
filter.endpointIds, optional caseId).

If your tenant returns 4xx, capture the request/response and adjust
build_isolation_request_body() accordingly (see docs/WORKFLOW_ISOLATION.md).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .api_client import api_get, api_post


def build_isolation_request_body(
    organization_id: str,
    endpoint_ids: List[str],
    case_id: Optional[str] = None,
    enable: bool = True,
) -> dict:
    try:
        org_ids = [int(organization_id)]
    except (TypeError, ValueError):
        org_ids = [organization_id]
    body: Dict[str, Any] = {
        "enabled": enable,
        "filter": {
            "organizationIds": org_ids,
            "endpointIds": list(endpoint_ids),
        },
    }
    if case_id:
        body["caseId"] = case_id
    return body


def assign_isolation_task(
    air_host: str,
    api_token: str,
    organization_id: str,
    endpoint_id: str,
    case_id: Optional[str] = None,
    enable: bool = True,
    dry_run: bool = False,
) -> Optional[dict]:
    body = build_isolation_request_body(
        organization_id, [endpoint_id], case_id=case_id, enable=enable
    )
    path = "/api/public/assets/tasks/isolation"
    if dry_run:
        return {"dry_run": True, "path": path, "body": body}

    resp = api_post(air_host, api_token, path, body=body)
    if not resp.ok:
        raise RuntimeError(
            f"Isolation task failed: HTTP {resp.status_code}\n"
            f"Request: {json.dumps(body)}\n"
            f"Response: {resp.text[:2000]}"
        )
    return resp.json()


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


def _task_is_isolation(task: dict) -> bool:
    t = (task.get("type") or "").lower()
    name = (task.get("name") or "").lower()
    display = (task.get("displayType") or "").lower()
    return "isolat" in t or "isolat" in name or "isolat" in display


def latest_isolation_task(tasks: List[dict]) -> Optional[dict]:
    candidates = [t for t in tasks if _task_is_isolation(t)]
    if not candidates:
        return None

    def sort_key(t: dict) -> str:
        return t.get("createdAt") or t.get("updatedAt") or ""

    return sorted(candidates, key=sort_key)[-1]


TERMINAL_STATUSES = frozenset(
    s.lower()
    for s in ("completed", "failed", "cancelled", "error", "canceled")
)


def poll_isolation_task(
    air_host: str,
    api_token: str,
    endpoint_id: str,
    *,
    interval_sec: float = 10.0,
    timeout_sec: float = 3600.0,
    verbose_print=None,
) -> Tuple[Optional[dict], List[dict]]:
    """
    Poll asset tasks until the latest isolation-like task reaches a terminal status
    or timeout. Returns (last_isolation_task_or_none, final_task_list).
    """
    start = time.time()
    last_iso = None
    final_list: List[dict] = []
    while time.time() - start < timeout_sec:
        final_list = get_asset_tasks(air_host, api_token, endpoint_id)
        last_iso = latest_isolation_task(final_list)
        if last_iso:
            st = (last_iso.get("status") or "").lower()
            if st in TERMINAL_STATUSES:
                return last_iso, final_list
            if verbose_print:
                verbose_print(
                    f"  Isolation task status={last_iso.get('status')} "
                    f"progress={last_iso.get('progress', 0)}%"
                )
        elif verbose_print:
            verbose_print("  (No isolation task found on asset yet; waiting...)")
        time.sleep(interval_sec)
    return last_iso, final_list

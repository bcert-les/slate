"""
Cortex XSOAR (Demisto) incident creation for isolation workflow.

Modes (env XSOAR_MODE):
  rest   — POST JSON to XSOAR_BASE_URL + XSOAR_INCIDENT_PATH (default /v1/incident)
  webhook — POST JSON to XSOAR_WEBHOOK_URL (generic receiver / incoming webhook)

REST auth: Authorization header = XSOAR_API_KEY (standard server API key).
Override header name with XSOAR_AUTH_HEADER (default "Authorization").

Errors are raised as XsoarAdapterError with .status_code when applicable.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

DEFAULT_INCIDENT_PATH = "/v1/incident"
DEFAULT_TIMEOUT = 60


class XsoarAdapterError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_xsoar_mode() -> str:
    return (os.getenv("XSOAR_MODE") or "rest").strip().lower()


def create_isolation_incident(
    *,
    name: str,
    details: str,
    incident_type: str,
    severity: int,
    raw_json: Optional[dict] = None,
    custom_fields: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """
    Create an incident or fire a webhook payload.

    Returns a dict with keys: mode, dry_run, response_summary (if not dry_run).
    """
    mode = load_xsoar_mode()
    payload = _build_payload(
        name=name,
        details=details,
        incident_type=incident_type,
        severity=severity,
        raw_json=raw_json,
        custom_fields=custom_fields,
    )

    if dry_run:
        return {"mode": mode, "dry_run": True, "payload": payload}

    if mode == "webhook":
        return _post_webhook(payload)
    if mode == "rest":
        return _post_rest_incident(payload)
    raise XsoarAdapterError(f"Unknown XSOAR_MODE: {mode!r} (use 'rest' or 'webhook')")


def _build_payload(
    *,
    name: str,
    details: str,
    incident_type: str,
    severity: int,
    raw_json: Optional[dict],
    custom_fields: Optional[dict],
) -> dict:
    occurred = _utc_now_iso()
    raw = raw_json if raw_json is not None else {}
    body: Dict[str, Any] = {
        "name": name[:500],
        "type": incident_type,
        "severity": int(severity),
        "occurred": occurred,
        "details": details[:32000] if details else "",
        "rawJSON": json.dumps(raw) if isinstance(raw, dict) else str(raw),
    }
    if custom_fields:
        body["customFields"] = custom_fields
    return body


def _post_webhook(payload: dict) -> dict:
    url = os.getenv("XSOAR_WEBHOOK_URL", "").strip()
    if not url:
        raise XsoarAdapterError(
            "XSOAR_WEBHOOK_URL is required when XSOAR_MODE=webhook",
        )
    headers = {"Content-Type": "application/json"}
    extra = os.getenv("XSOAR_WEBHOOK_HEADERS_JSON")
    if extra:
        try:
            headers.update(json.loads(extra))
        except json.JSONDecodeError as e:
            raise XsoarAdapterError(f"Invalid XSOAR_WEBHOOK_HEADERS_JSON: {e}") from e

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise XsoarAdapterError(f"Webhook request failed: {e}") from e

    if not r.ok:
        raise XsoarAdapterError(
            f"Webhook HTTP {r.status_code}",
            status_code=r.status_code,
            body=r.text[:2000],
        )
    summary = _safe_response_summary(r)
    return {"mode": "webhook", "dry_run": False, "response_summary": summary}


def _post_rest_incident(payload: dict) -> dict:
    base = (os.getenv("XSOAR_BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("XSOAR_API_KEY") or "").strip()
    if not base or not key:
        raise XsoarAdapterError(
            "XSOAR_BASE_URL and XSOAR_API_KEY are required when XSOAR_MODE=rest",
        )
    path = (os.getenv("XSOAR_INCIDENT_PATH") or DEFAULT_INCIDENT_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"

    auth_header = (os.getenv("XSOAR_AUTH_HEADER") or "Authorization").strip()
    headers = {
        auth_header: key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Some deployments expect a second key id header — optional
    key_id = os.getenv("XSOAR_API_KEY_ID")
    if key_id:
        headers["x-xdr-auth-id"] = key_id.strip()

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise XsoarAdapterError(f"REST incident request failed: {e}") from e

    if not r.ok:
        raise XsoarAdapterError(
            f"XSOAR incident create failed: HTTP {r.status_code} {r.text[:500]}",
            status_code=r.status_code,
            body=r.text[:2000],
        )
    summary = _safe_response_summary(r)
    return {"mode": "rest", "dry_run": False, "response_summary": summary}


def _safe_response_summary(r: requests.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"text": r.text[:2000]}


def build_workflow_raw_json(
    *,
    binalyze_org_id: str,
    binalyze_case_id: str,
    endpoints: list,
    run_id: Optional[str] = None,
) -> dict:
    return {
        "source": "slate_workflow_isolation",
        "run_id": run_id or str(uuid.uuid4()),
        "binalyze_organization_id": binalyze_org_id,
        "binalyze_case_id": binalyze_case_id,
        "endpoints": endpoints,
    }

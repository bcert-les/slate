"""
Interactive workflow: validate endpoints, create Binalyze case, open XSOAR incident,
assign isolation tasks, poll status, prompt operator before/after server-class hosts.

Run from repository root. See workflows/isolation_xsoar/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple, TypeVar

import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 5
_INITIAL_BACKOFF = 1.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_ITERATIONS = 1000

T = TypeVar("T")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Binalyze cases helpers
# ---------------------------------------------------------------------------

class AssetResolveError(Exception):
    """Could not resolve exactly one asset for an identifier."""


CASE_VISIBILITY_VALUES = frozenset(
    ("public-to-organization", "private-to-users")
)


def _normalize_case_visibility(case_visibility: Optional[str]) -> str:
    v = (case_visibility or "public-to-organization").strip()
    if v not in CASE_VISIBILITY_VALUES:
        raise ValueError(
            "case_visibility must be 'public-to-organization' or 'private-to-users', "
            f"not {case_visibility!r}"
        )
    return v


def _primary_host_label(name: Optional[str]) -> str:
    """First DNS label, lowercased (matches short name vs FQDN in asset `name`)."""
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


def resolve_case(
    air_host: str,
    api_token: str,
    org_id: str,
    case_id: Optional[str] = None,
    case_name: Optional[str] = None,
    endpoint_name: str = "unknown",
    case_visibility: Optional[str] = None,
) -> dict:
    if case_id:
        resp = api_get(air_host, api_token, f"/api/public/cases/{case_id}")
        if not resp.ok:
            raise RuntimeError(
                f"Could not fetch case {case_id}: HTTP {resp.status_code} {resp.text[:300]}"
            )
        return resp.json().get("result", resp.json())

    if not case_name:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        case_name = f"Isolation workflow - {endpoint_name} - {date_str}"

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


def find_asset_strict(
    air_host: str, api_token: str, identifier: str, org_id: str
) -> dict:
    """Resolve a single asset by ID or hostname. Raises AssetResolveError on ambiguity."""
    resp = api_get(air_host, api_token, f"/api/public/assets/{identifier}")
    if resp.ok:
        asset = resp.json().get("result", resp.json())
        if asset.get("_id"):
            return asset

    params = {"filter[organizationIds]": org_id, "search": identifier}
    assets: List[dict] = paginate_get(
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


def fetch_case_tasks(air_host: str, api_token: str, case_id: str) -> List[dict]:
    return paginate_get(
        air_host,
        api_token,
        f"/api/public/cases/{case_id}/tasks",
        verbose=False,
    )


def summarize_findings_for_xsoar(tasks: List[dict], max_lines: int = 40) -> str:
    """Short text summary of case tasks for incident details."""
    if not tasks:
        return "No case tasks yet."
    lines: List[str] = []
    acquisitions = [t for t in tasks if t.get("type") == "acquisition"]
    triages = [t for t in tasks if t.get("type") == "triage"]
    lines.append(
        f"Total tasks: {len(tasks)} (acquisitions={len(acquisitions)}, triages={len(triages)})"
    )
    for label, full in (("Acquisition", acquisitions), ("Triage", triages)):
        if not full:
            continue
        subset = full[:8]
        lines.append(f"{label}:")
        for t in subset:
            lines.append(
                f"  - {t.get('name', '?')}: status={t.get('status')} "
                f"endpoint={t.get('endpointName', 'N/A')}"
            )
        if len(full) > 8:
            lines.append(f"  ... ({len(full) - 8} more)")
    out = "\n".join(lines)
    if len(out.splitlines()) > max_lines:
        return "\n".join(out.splitlines()[:max_lines]) + "\n..."
    return out


# ---------------------------------------------------------------------------
# Isolation helpers
# ---------------------------------------------------------------------------

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


def _get_asset_tasks(air_host: str, api_token: str, endpoint_id: str) -> List[dict]:
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


def _latest_isolation_task(tasks: List[dict]) -> Optional[dict]:
    candidates = [t for t in tasks if _task_is_isolation(t)]
    if not candidates:
        return None

    def sort_key(t: dict) -> str:
        return t.get("createdAt") or t.get("updatedAt") or ""

    return sorted(candidates, key=sort_key)[-1]


_TERMINAL_STATUSES = frozenset(
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
        final_list = _get_asset_tasks(air_host, api_token, endpoint_id)
        last_iso = _latest_isolation_task(final_list)
        if last_iso:
            st = (last_iso.get("status") or "").lower()
            if st in _TERMINAL_STATUSES:
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


# ---------------------------------------------------------------------------
# Workflow policy
# ---------------------------------------------------------------------------

_DEFAULT_POLICY_PATH = "config/workflow_isolation.json"


@dataclass
class WorkflowPolicy:
    max_batch_size: int = 5
    server_hostname_regex: Optional[str] = None
    server_confirmation_phrase: str = "YES-ISOLATE-SERVER"
    xsoar_incident_type: str = "Unclassified"
    xsoar_severity: int = 2
    xsoar_custom_fields: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowPolicy":
        if not data:
            return cls()
        regex = data.get("server_hostname_regex")
        if regex is not None and not isinstance(regex, str):
            raise ValueError("server_hostname_regex must be a string or null")
        sev = data.get("xsoar_severity", 2)
        if not isinstance(sev, int) or sev < 0 or sev > 4:
            raise ValueError("xsoar_severity must be an integer 0–4")
        mbs = int(data.get("max_batch_size", 5))
        if mbs < 1:
            raise ValueError("max_batch_size must be >= 1")
        cf = data.get("xsoar_custom_fields") or {}
        if not isinstance(cf, dict):
            raise ValueError("xsoar_custom_fields must be an object")
        return cls(
            max_batch_size=mbs,
            server_hostname_regex=regex,
            server_confirmation_phrase=str(
                data.get("server_confirmation_phrase") or "YES-ISOLATE-SERVER"
            ),
            xsoar_incident_type=str(data.get("xsoar_incident_type") or "Unclassified"),
            xsoar_severity=sev,
            xsoar_custom_fields=dict(cf),
        )


def load_policy(path: Optional[str] = None) -> WorkflowPolicy:
    p = path or os.getenv("WORKFLOW_POLICY_PATH") or _DEFAULT_POLICY_PATH
    if not os.path.isfile(p):
        return WorkflowPolicy()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Policy file must contain a JSON object: {p}")
    return WorkflowPolicy.from_dict(data)


def is_null_hostname(name: Optional[str]) -> bool:
    if name is None:
        return True
    if not str(name).strip():
        return True
    return False


def matches_server_regex(hostname: str, pattern: Optional[str]) -> bool:
    if not pattern:
        return False
    try:
        return re.search(pattern, hostname) is not None
    except re.error as e:
        raise ValueError(f"Invalid server_hostname_regex: {e}") from e


def batches(items: List[T], size: int) -> Iterator[List[T]]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# XSOAR adapter
# ---------------------------------------------------------------------------

_XSOAR_DEFAULT_INCIDENT_PATH = "/v1/incident"
_XSOAR_DEFAULT_TIMEOUT = 60


class XsoarAdapterError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_xsoar_mode() -> str:
    return (os.getenv("XSOAR_MODE") or "rest").strip().lower()


def _build_xsoar_payload(
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


def _post_xsoar_webhook(payload: dict) -> dict:
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
        r = requests.post(url, json=payload, headers=headers, timeout=_XSOAR_DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise XsoarAdapterError(f"Webhook request failed: {e}") from e

    if not r.ok:
        raise XsoarAdapterError(
            f"Webhook HTTP {r.status_code}",
            status_code=r.status_code,
            body=r.text[:2000],
        )
    try:
        summary: Any = r.json()
    except Exception:
        summary = {"text": r.text[:2000]}
    return {"mode": "webhook", "dry_run": False, "response_summary": summary}


def _post_xsoar_rest_incident(payload: dict) -> dict:
    base = (os.getenv("XSOAR_BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("XSOAR_API_KEY") or "").strip()
    if not base or not key:
        raise XsoarAdapterError(
            "XSOAR_BASE_URL and XSOAR_API_KEY are required when XSOAR_MODE=rest",
        )
    path = (os.getenv("XSOAR_INCIDENT_PATH") or _XSOAR_DEFAULT_INCIDENT_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"

    auth_header = (os.getenv("XSOAR_AUTH_HEADER") or "Authorization").strip()
    headers = {
        auth_header: key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    key_id = os.getenv("XSOAR_API_KEY_ID")
    if key_id:
        headers["x-xdr-auth-id"] = key_id.strip()

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=_XSOAR_DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        raise XsoarAdapterError(f"REST incident request failed: {e}") from e

    if not r.ok:
        raise XsoarAdapterError(
            f"XSOAR incident create failed: HTTP {r.status_code} {r.text[:500]}",
            status_code=r.status_code,
            body=r.text[:2000],
        )
    try:
        summary = r.json()
    except Exception:
        summary = {"text": r.text[:2000]}
    return {"mode": "rest", "dry_run": False, "response_summary": summary}


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
    """Create an incident or fire a webhook payload."""
    mode = _load_xsoar_mode()
    payload = _build_xsoar_payload(
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
        return _post_xsoar_webhook(payload)
    if mode == "rest":
        return _post_xsoar_rest_incident(payload)
    raise XsoarAdapterError(f"Unknown XSOAR_MODE: {mode!r} (use 'rest' or 'webhook')")


def build_workflow_raw_json(
    *,
    binalyze_org_id: str,
    binalyze_case_id: str,
    endpoints: list,
    run_id: Optional[str] = None,
) -> dict:
    return {
        "source": "updraft_workflow_isolation",
        "run_id": run_id or str(uuid.uuid4()),
        "binalyze_organization_id": binalyze_org_id,
        "binalyze_case_id": binalyze_case_id,
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------

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
            aid = _first_id(asset, "_id", "id")
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
        {"identifier": ident, "name": a.get("name"), "_id": _first_id(a, "_id", "id")}
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
                cid = _first_id(case_obj, "_id", "id")
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
            {"identifier": ident, "name": a.get("name"), "_id": _first_id(a, "_id", "id")}
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
            eid = _first_id(asset, "_id", "id")
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
                eid = _first_id(asset, "_id", "id")
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

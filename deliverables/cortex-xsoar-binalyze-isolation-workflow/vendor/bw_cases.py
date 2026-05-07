"""
Binalyze AIR: organization validation, case create/fetch, strict asset resolution (no stdin).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from bw_api_client import api_get, api_post
from bw_pagination import paginate_get


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
    """
    Resolve a single asset by ID or hostname search. No interactive prompts.
    Raises AssetResolveError if zero or ambiguous matches.
    """
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

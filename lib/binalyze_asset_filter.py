"""
POST /api/public/assets/filter request bodies (shared presets).

AIR commonly exposes ``isolationStatus`` on each asset (e.g. ``"isolated"``,
``"isolating"``, ``"unisolated"``). The default preset filters with
``filter.isolationStatus`` set to ``"isolated"`` (override via env). Rows are
then narrowed with ``filter_assets_client_isolated_only()`` to **isolated**
or **isolating**. If the server still returns extra rows, that client pass
drops ``unisolated`` (and unknown) rows.

For unusual tenants, capture the JSON body from the Binalyze UI (browser
devtools) when you apply an “Isolated only” view and pass it via ``--body-file``
on ``filter_assets.py`` or ``--filter-body-file`` on ``isolation_status.py``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Union

# Treat as "actively isolated or in progress" for client-side filtering.
_ISOLATION_STATUS_ACTIVE = frozenset({"isolated", "isolating"})


def asset_filter_body(org_id: Union[str, int], extra: Dict[str, Any] | None = None) -> dict:
    """Base POST body: ``{"filter": {"organizationIds": [...], ...}}``."""
    flt: Dict[str, Any] = {"organizationIds": [str(org_id)]}
    if extra:
        for k, v in extra.items():
            if v is not None:
                flt[k] = v
    return {"filter": flt}


def _parse_filter_value(raw: str) -> Union[str, bool]:
    low = raw.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return raw.strip()


def isolated_assets_filter_body(org_id: Union[str, int]) -> dict:
    """
    Preset: server-side filter for isolated assets.

    Env:

    - ``BINALYZE_ISOLATED_FILTER_KEY`` — key inside ``filter`` (default
      ``isolationStatus``).
    - ``BINALYZE_ISOLATED_FILTER_VALUE`` — value sent for that key (default
      ``isolated``). Use ``true`` / ``false`` for boolean-style keys.
    """
    key = (os.getenv("BINALYZE_ISOLATED_FILTER_KEY") or "isolationStatus").strip() or "isolationStatus"
    val_raw = os.getenv("BINALYZE_ISOLATED_FILTER_VALUE", "isolated")
    value = _parse_filter_value(str(val_raw))
    return asset_filter_body(org_id, {key: value})


def filter_assets_client_isolated_only(assets: List[dict]) -> List[dict]:
    """
    Keep rows that are isolated or mid-isolation (``isolationStatus``) in the
    asset JSON.

    Use after ``POST /assets/filter`` when the API returns a broader set than
    expected (e.g. ignores an unknown filter key).
    """
    out: List[dict] = []
    for a in assets:
        st = str(a.get("isolationStatus") or "").strip().lower()
        if st in _ISOLATION_STATUS_ACTIVE:
            out.append(a)
            continue
        if a.get("isolated") is True:
            out.append(a)
            continue
        iso = a.get("isolation")
        if isinstance(iso, dict) and iso.get("enabled") is True:
            out.append(a)
            continue
    return out

"""
Binalyze AIR acquisition profiles: resolve profile id for POST /acquisitions/acquire.
"""

from __future__ import annotations

from typing import Any, Dict


PRESET_PROFILES = frozenset(
    (
        "browsing-history",
        "compromise-assessment",
        "event-logs",
        "full",
        "memory-ram-pagefile",
        "quick",
    )
)


def acquisition_profile_id_for_acquire(profile_from_list: Dict[str, Any], profile_arg: str) -> str:
    """
    Return the value to send as `acquisitionProfileId` for POST /api/public/acquisitions/acquire.

    For built-ins, AIR expects the preset slug (`quick`, `full`, etc.).
    For custom profiles, use profile row `_id` (or fallback `id`) from GET /acquisitions/profiles.
    """
    arg_norm = (profile_arg or "").strip().lower()
    if arg_norm in PRESET_PROFILES:
        return arg_norm

    ref = profile_from_list.get("_id") or profile_from_list.get("id")
    ref_s = str(ref or "").strip()
    if not ref_s:
        raise RuntimeError("Acquisition profile row has no _id/id; cannot derive profile id.")
    return ref_s

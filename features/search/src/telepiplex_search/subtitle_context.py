"""Small non-naming enrichment for external subtitle selection."""
from __future__ import annotations

import math


def _seconds(minutes):
    if isinstance(minutes, bool):
        return None
    try:
        value = float(minutes) * 60
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and 0 < value <= 12 * 3600 else None


def build_subtitle_context(private_contract: dict, selected: dict) -> dict:
    identity = private_contract.get("identity") or {}
    snapshot = selected.get("entity_snapshot") or {}
    result = {
        "original_language": str(identity.get("original_language") or snapshot.get("original_language") or "").strip(),
        "imdb_id": str((snapshot.get("external_ids") or {}).get("imdb") or "").strip(),
    }
    if identity.get("content_kind") == "movie":
        seconds = _seconds(identity.get("runtime_minutes"))
        if seconds:
            result["expected_duration_seconds"] = seconds
    else:
        durations = {}
        # Only explicitly mapped episodes qualify. A show's usual runtime or
        # a season total must never be applied to every episode or special.
        for item in private_contract.get("items") or []:
            if not isinstance(item, dict):
                continue
            season, episode = item.get("season_number"), item.get("episode_number")
            if (type(season) is not int or type(episode) is not int or season < 0 or episode < 1):
                continue
            seconds = _seconds(item.get("runtime_minutes"))
            if seconds:
                durations[f"S{season:02d}E{episode:02d}"] = seconds
        if durations:
            result["episode_durations"] = durations
    return result

from __future__ import annotations

from copy import deepcopy


def extract_confirmed_download_plan(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    plan = metadata.get("download_plan")
    if not isinstance(plan, dict):
        return None
    if plan.get("schema_version") != 1 or plan.get("confirmed") is not True:
        return None

    placement = plan.get("placement")
    if not isinstance(placement, dict):
        return None
    if placement.get("library_type") not in {"movie", "series"}:
        return None

    if placement.get("mapping_kind") == "temporary_related_special":
        source_entry = plan.get("source_entry")
        if not isinstance(source_entry, dict):
            return None
        if not source_entry.get("title") or not (
            source_entry.get("url") or source_entry.get("external_id")
        ):
            return None
    return deepcopy(plan)


def locked_episode(plan: dict) -> tuple[int, int] | None:
    placement = plan.get("placement") if isinstance(plan, dict) else None
    if not isinstance(placement, dict) or placement.get("library_type") != "series":
        return None
    try:
        season = int(placement.get("season_number"))
        episode = int(placement.get("episode_number"))
    except (TypeError, ValueError):
        return None
    return (season, episode) if season >= 0 and episode > 0 else None

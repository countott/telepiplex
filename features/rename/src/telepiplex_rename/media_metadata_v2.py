"""Pure Rename helpers for the minimal immutable media_metadata v2 contract."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath

from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2


_EPISODE = re.compile(
    r"(?i)(?<![A-Z0-9])S(?P<season>\d{1,3})E(?P<episode>\d{1,4})(?!\d)"
)
_VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv"}


def naming_identity_from_v2(contract: dict) -> dict:
    value = validate_media_metadata_v2(contract)
    if value is None:
        raise ValueError("invalid media_metadata v2")
    identity = value["identity"]
    return {
        "media_type": identity["media_type"],
        "chinese_title": identity["title_zh"],
        "english_title": identity["title_en"],
        "original_title": identity["title_original"],
        "year": identity["year"],
        "category_kind": value["placement"]["category_kind"],
    }


def scope_allows_coordinate(
    contract: dict,
    season: int,
    episode: int,
) -> bool:
    value = validate_media_metadata_v2(contract)
    if value is None or value["identity"]["media_type"] != "series":
        return False
    try:
        season = int(season)
        episode = int(episode)
    except (TypeError, ValueError):
        return False
    if season < 1 or episode < 1:
        return False
    scope = value["scope"]
    if scope["kind"] == "whole_series":
        return True
    if scope["kind"] == "season":
        return season == scope["season_number"]
    return bool(
        scope["kind"] == "episode"
        and season == scope["season_number"]
        and episode == scope["episode_number"]
    )


def observed_episode_plan(contract: dict, file_tree: list[dict]) -> dict:
    if validate_media_metadata_v2(contract) is None:
        raise ValueError("invalid media_metadata v2")
    coordinates: dict[tuple[int, int], list[dict]] = {}
    unresolved = []
    for raw in file_tree or ():
        if not isinstance(raw, dict) or raw.get("is_dir"):
            continue
        path = str(raw.get("relative_path") or raw.get("path") or raw.get("name") or "")
        if PurePosixPath(path).suffix.casefold() not in _VIDEO_SUFFIXES:
            continue
        match = _EPISODE.search(PurePosixPath(path).name)
        if match is None:
            unresolved.append({
                "relative_path": path,
                "reason_code": "coordinate_unparseable",
            })
            continue
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        if not scope_allows_coordinate(contract, season, episode):
            unresolved.append({
                "relative_path": path,
                "season_number": season,
                "episode_number": episode,
                "reason_code": "outside_frozen_scope",
            })
            continue
        coordinates.setdefault((season, episode), []).append(deepcopy(raw))
    episode_map = []
    for (season, episode), files in sorted(coordinates.items()):
        if len(files) != 1:
            unresolved.extend({
                "relative_path": str(item.get("relative_path") or item.get("path") or item.get("name") or ""),
                "season_number": season,
                "episode_number": episode,
                "reason_code": "duplicate_coordinate",
            } for item in files)
            continue
        episode_map.append({
            "season_number": season,
            "episode_number": episode,
            "source": files[0],
        })
    return {"episode_map": episode_map, "unresolved": unresolved}

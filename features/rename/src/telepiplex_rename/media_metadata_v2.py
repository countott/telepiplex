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
        "english_title": identity["title_original"],
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


def private_v1_adapter_from_v2(
    contract: dict,
    file_tree: list[dict] | None,
) -> dict:
    """Build temporary processor input; callers must never publish this value."""

    value = validate_media_metadata_v2(contract)
    if value is None:
        raise ValueError("invalid media_metadata v2")
    naming = naming_identity_from_v2(value)
    scope = value["scope"]
    items = []
    unresolved = []
    if naming["media_type"] == "series":
        observed = observed_episode_plan(value, file_tree or [])
        unresolved = observed["unresolved"]
        items = [{
            "item_id": f"observed:s{item['season_number']}e{item['episode_number']}",
            "content_role": "main_episode",
            "season_number": item["season_number"],
            "episode_number": item["episode_number"],
        } for item in observed["episode_map"]]
    external_ids = {}
    for key, provider_id in value["identity"]["provider_refs"].items():
        if key == "wikidata":
            external_ids["wikidata"] = provider_id
        elif key == "douban_subject":
            external_ids["douban_subject"] = provider_id
        elif key.startswith("tmdb_"):
            external_ids["tmdb"] = provider_id
        elif key.startswith("tvdb_"):
            external_ids["tvdb"] = provider_id
        elif key == "anilist":
            external_ids["anilist"] = provider_id
    return {
        "schema_version": 1,
        "metadata_id": value["metadata_id"],
        "confirmed": True,
        "identity": {
            "chinese_title": naming["chinese_title"],
            "english_title": naming["english_title"],
            "year": str(naming["year"] or ""),
            "content_kind": "movie" if naming["media_type"] == "movie" else "series",
            "external_ids": external_ids,
        },
        "retrieval": {
            "media_type": naming["media_type"],
            "scope": scope["kind"] if naming["media_type"] == "series" else "work",
            "query": "",
        },
        "relation": {"target_series": None, "source": "media_metadata_v2"},
        "placement": {
            "category_kind": naming["category_kind"],
            "library_type": naming["media_type"],
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {"decision": {
            "scope": scope["kind"],
            "season_number": scope["season_number"],
            "episode_number": scope["episode_number"],
            "scope_source": "media_metadata_v2",
        }},
        "warnings": (["warning:observed_files_unresolved"] if unresolved else []),
        "items": items,
    }


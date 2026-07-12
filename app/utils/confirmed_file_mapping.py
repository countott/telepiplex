from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath

from app.core.media_metadata import (
    SERIES_EPISODE_MAPPINGS,
    validate_media_metadata,
)


SXXEYY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])s(?P<season>\d{1,2})[ ._-]*e(?P<episode>\d{1,3})(?:[^0-9]|$)"
)
NXEE_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?:[^0-9]|$)"
)


def _clean_path(value) -> str:
    return "/".join(
        part
        for part in str(value or "").replace("\\", "/").split("/")
        if part and part not in {".", ".."}
    )


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_key(item: dict):
    season = _safe_int(item.get("season_number"))
    episode = _safe_int(item.get("episode_number"))
    if season is None or episode is None:
        return None
    return season, episode


def _target_id(item: dict) -> str:
    target = _target_key(item)
    if target is None:
        return ""
    season, episode = target
    return str(item.get("item_id") or f"S{season:02d}E{episode:03d}")


def _expected_items(media_metadata: dict) -> list[dict]:
    contract = validate_media_metadata(media_metadata, require_confirmed=True)
    if contract is None:
        raise ValueError("invalid confirmed media_metadata")
    items = [
        deepcopy(item)
        for item in contract.get("items") or []
        if isinstance(item, dict) and _target_key(item) is not None
    ]
    placement = contract.get("placement") or {}
    if not items and placement.get("mapping_kind") in SERIES_EPISODE_MAPPINGS:
        season = _safe_int(placement.get("season_number"))
        episode = _safe_int(placement.get("episode_number"))
        if season is not None and episode is not None:
            items.append({
                "item_id": f"S{season:02d}E{episode:03d}",
                "content_role": (contract.get("identity") or {}).get("content_kind") or "special",
                "season_number": season,
                "episode_number": episode,
            })
    return items


def _file_size(node: dict) -> int:
    for key in ("size", "fs", "size_byte"):
        value = _safe_int(node.get(key))
        if value is not None:
            return max(0, value)
    return 0


def _source_nodes(
    file_tree: list[dict],
    minimum_video_size: int = 0,
) -> list[dict]:
    nodes = []
    for raw in file_tree or []:
        if not isinstance(raw, dict) or raw.get("is_dir"):
            continue
        if raw.get("is_video") is False:
            continue
        size = _file_size(raw)
        if minimum_video_size > 0 and 0 < size < minimum_video_size:
            continue
        relative_path = _clean_path(raw.get("relative_path") or raw.get("name"))
        if not relative_path:
            continue
        node = deepcopy(raw)
        node["relative_path"] = relative_path
        node["name"] = str(raw.get("name") or PurePosixPath(relative_path).name)
        nodes.append(node)
    return sorted(nodes, key=lambda item: item["relative_path"].casefold())


def _source_aliases(nodes: list[dict]):
    by_path = {node["relative_path"]: node for node in nodes}
    basename_counts = {}
    for node in nodes:
        basename_counts[node["name"]] = basename_counts.get(node["name"], 0) + 1
    aliases = dict(by_path)
    for node in nodes:
        if basename_counts[node["name"]] == 1:
            aliases[node["name"]] = node
    return aliases


def _episode_marker(value: str):
    for pattern in (SXXEYY_PATTERN, NXEE_PATTERN):
        match = pattern.search(str(value or ""))
        if match:
            return int(match.group("season")), int(match.group("episode"))
    return None


def _mapping(source: dict, target: dict, mapping_source: str) -> dict:
    season, episode = _target_key(target)
    return {
        "source_file": source["relative_path"],
        "item_id": _target_id(target),
        "content_role": target.get("content_role") or "main_episode",
        "season_number": season,
        "episode_number": episode,
        "mapping_source": mapping_source,
    }


def map_confirmed_files(
    media_metadata: dict,
    file_tree: list[dict],
    ai_episode_map: list[dict] | None = None,
    minimum_video_size: int = 0,
) -> dict:
    expected_items = _expected_items(media_metadata)
    targets = {_target_key(item): item for item in expected_items}
    nodes = _source_nodes(file_tree, minimum_video_size)
    eligible_paths = {node["relative_path"] for node in nodes}
    all_video_nodes = _source_nodes(file_tree)
    ineligible_sources = [
        node["relative_path"]
        for node in all_video_nodes
        if node["relative_path"] not in eligible_paths
    ]
    aliases = _source_aliases(nodes)
    mapped_sources = set()
    mapped_targets = set()
    mappings = []
    rejected = []

    for target in expected_items:
        hint = _clean_path(target.get("source_hint"))
        if not hint:
            continue
        source = aliases.get(hint)
        target_key = _target_key(target)
        if not source or source["relative_path"] in mapped_sources or target_key in mapped_targets:
            continue
        mappings.append(_mapping(source, target, "rule"))
        mapped_sources.add(source["relative_path"])
        mapped_targets.add(target_key)

    for source in nodes:
        source_path = source["relative_path"]
        if source_path in mapped_sources:
            continue
        target_key = _episode_marker(source_path)
        target = targets.get(target_key)
        if not target or target_key in mapped_targets:
            continue
        mappings.append(_mapping(source, target, "rule"))
        mapped_sources.add(source_path)
        mapped_targets.add(target_key)

    for raw in ai_episode_map or []:
        if not isinstance(raw, dict):
            rejected.append({"reason": "invalid_mapping", "mapping": deepcopy(raw)})
            continue
        requested_source = _clean_path(raw.get("source_file"))
        source = aliases.get(requested_source)
        if source is None:
            rejected.append({"reason": "source_not_unresolved", "mapping": deepcopy(raw)})
            continue
        source_path = source["relative_path"]
        if source_path in mapped_sources:
            rejected.append({"reason": "source_already_mapped", "mapping": deepcopy(raw)})
            continue
        target_key = (
            _safe_int(raw.get("season_number")),
            _safe_int(raw.get("episode_number")),
        )
        target = targets.get(target_key)
        if target is None:
            rejected.append({"reason": "target_not_unresolved", "mapping": deepcopy(raw)})
            continue
        if target_key in mapped_targets:
            rejected.append({"reason": "target_already_mapped", "mapping": deepcopy(raw)})
            continue
        mappings.append(_mapping(source, target, "ai"))
        mapped_sources.add(source_path)
        mapped_targets.add(target_key)

    missing_items = [
        deepcopy(item)
        for item in expected_items
        if _target_key(item) not in mapped_targets
    ]
    unexpected_sources = [
        node["relative_path"]
        for node in nodes
        if node["relative_path"] not in mapped_sources
    ]
    if not mappings:
        state = "failed"
    elif missing_items or unexpected_sources:
        state = "partial"
    else:
        state = "completed"
    return {
        "state": state,
        "mappings": mappings,
        "missing_items": missing_items,
        "unexpected_sources": unexpected_sources,
        "ineligible_sources": ineligible_sources,
        "rejected": rejected,
    }


def unresolved_mapping_context(
    media_metadata: dict,
    file_tree: list[dict],
    coverage: dict,
) -> dict:
    unresolved_paths = set(coverage.get("unexpected_sources") or [])
    nodes = [
        node
        for node in _source_nodes(file_tree)
        if node["relative_path"] in unresolved_paths
    ]
    return {
        "confirmed_items": deepcopy(coverage.get("missing_items") or []),
        "file_tree": nodes,
        "metadata_id": str(media_metadata.get("metadata_id") or ""),
    }

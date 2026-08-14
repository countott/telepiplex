# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import PurePosixPath

from telepiplex_plugin_sdk.media_metadata import (
    merge_resolved_items,
    series_titles,
)
from .media_naming import sanitize_path_name, sanitize_target_name
from .subtitles import build_series_subtitle_plan


VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".m4v",
    ".ts",
    ".m2ts",
    ".wmv",
    ".flv",
    ".webm",
}


def _clean_path(value: str) -> str:
    parts = []
    for raw_part in str(value or "").replace("\\", "/").split("/"):
        part = sanitize_path_name(raw_part)
        if part and part not in {".", ".."}:
            parts.append(part)
    return "/".join(parts)


def _join_path(*parts: str) -> str:
    cleaned = []
    leading_slash = str(parts[0] or "").startswith("/") if parts else False
    for part in parts:
        for item in str(part or "").strip("/").split("/"):
            if item:
                cleaned.append(item)
    result = "/".join(cleaned)
    return f"/{result}" if leading_slash else result


def _video_file_nodes(file_tree: list[dict]) -> list[dict]:
    nodes = []
    for item in file_tree or []:
        if not isinstance(item, dict) or item.get("is_dir"):
            continue
        relative_path = _clean_path(
            item.get("relative_path") or item.get("name") or ""
        )
        name = str(item.get("name") or PurePosixPath(relative_path).name).strip()
        if not name or not relative_path:
            continue
        suffix = PurePosixPath(relative_path).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            continue
        node = dict(item)
        node["name"] = name
        node["relative_path"] = relative_path
        nodes.append(node)
    return nodes


def _source_index(file_tree: list[dict]) -> dict[str, dict]:
    nodes = _video_file_nodes(file_tree)
    index = {node["relative_path"]: node for node in nodes}
    basename_counts = {}
    for node in nodes:
        basename_counts[node["name"]] = basename_counts.get(node["name"], 0) + 1
    for node in nodes:
        if basename_counts.get(node["name"]) == 1:
            index[node["name"]] = node
    return index


def _candidate_ids(tvdb_candidates: list[dict]) -> set[str]:
    return {
        str(item.get("tvdb_series_id") or item.get("id") or "").strip()
        for item in tvdb_candidates or []
        if isinstance(item, dict)
    }


def _episode_ids(tvdb_episodes: list[dict]) -> set[str]:
    return {
        str(item.get("tvdb_episode_id") or item.get("id") or "").strip()
        for item in tvdb_episodes or []
        if isinstance(item, dict) and (item.get("tvdb_episode_id") or item.get("id"))
    }


def _safe_int(value) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed


def _safe_season_int(value) -> int | None:
    parsed = _safe_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _safe_episode_int(value) -> int | None:
    parsed = _safe_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _episode_marker_text(season: int, episode: int) -> str:
    episode_width = 3 if episode >= 100 else 2
    return f"S{season:02d}E{episode:0{episode_width}d}"


def _display_folder(chinese_title: str, english_title: str) -> str:
    chinese_title = sanitize_target_name(chinese_title)
    english_title = sanitize_target_name(english_title)
    if chinese_title and english_title and chinese_title != english_title:
        return f"{chinese_title} ({english_title})"
    return chinese_title or english_title


def _target_root(selected_path: str, metadata: dict, ai_plan: dict) -> str:
    series_name = sanitize_target_name(ai_plan.get("series_name") or metadata.get("english_title") or metadata.get("query"))
    chinese_title = sanitize_target_name(metadata.get("chinese_title"))
    if not series_name:
        return ""
    return _join_path(selected_path, _display_folder(chinese_title, series_name))


def _target_relative_path(item: dict, source_relative_path: str, series_name: str) -> str:
    series_name = sanitize_target_name(series_name)
    season = _safe_season_int(item.get("season_number"))
    episode = _safe_episode_int(item.get("episode_number"))
    if not series_name or season is None or episode is None:
        return ""

    suffix = PurePosixPath(source_relative_path).suffix
    marker = _episode_marker_text(season, episode)
    return _join_path(f"{series_name} Season {season:02d}", f"{series_name} {marker}{suffix}")


def build_tvdb_rename_plan(
    final_path: str,
    selected_path: str,
    metadata: dict | None,
    ai_plan: dict | None,
    file_tree: list[dict],
    tvdb_candidates: list[dict],
    tvdb_episodes: list[dict],
) -> dict | None:
    metadata = metadata or {}
    ai_plan = ai_plan or {}
    if not isinstance(ai_plan.get("episode_map"), list) or not ai_plan.get("episode_map"):
        return None

    tvdb_series_id = str(ai_plan.get("tvdb_series_id") or "").strip()
    if tvdb_series_id and tvdb_series_id not in _candidate_ids(tvdb_candidates):
        return None

    source_lookup = _source_index(file_tree)
    source_video_paths = {node["relative_path"] for node in _video_file_nodes(file_tree)}
    known_episode_ids = _episode_ids(tvdb_episodes)
    target_root = _target_root(selected_path, metadata, ai_plan)
    if not target_root:
        return None
    series_name = sanitize_target_name(ai_plan.get("series_name") or metadata.get("english_title") or metadata.get("query"))

    operations = []
    seen_sources = set()
    seen_targets = set()
    for item in ai_plan["episode_map"]:
        if not isinstance(item, dict):
            return None

        source_file = _clean_path(item.get("source_file") or "")
        source_node = source_lookup.get(source_file)
        if not source_node:
            return None
        source_relative_path = source_node["relative_path"]
        if source_relative_path in seen_sources:
            return None
        seen_sources.add(source_relative_path)

        tvdb_episode_id = str(item.get("tvdb_episode_id") or "").strip()
        if tvdb_episode_id and known_episode_ids and tvdb_episode_id not in known_episode_ids:
            return None

        target_relative_path = _target_relative_path(item, source_node["relative_path"], series_name)
        if not target_relative_path:
            return None
        if target_relative_path in seen_targets:
            return None
        seen_targets.add(target_relative_path)

        target_parts = target_relative_path.split("/")
        rename_to = target_parts[-1]
        target_dir = _join_path(target_root, *target_parts[:-1])
        source_path = str(source_node.get("path") or "") or _join_path(
            final_path, source_node["relative_path"]
        )
        source_parent = source_path.rsplit("/", 1)[0]
        renamed_source_path = _join_path(source_parent, rename_to)
        operations.append(
            {
                "source_relative_path": source_node["relative_path"],
                "source_path": source_path,
                "rename_to": rename_to,
                "renamed_source_path": renamed_source_path,
                "target_dir": target_dir,
                "target_relative_path": target_relative_path,
            }
        )

    unmatched_sources = source_video_paths - seen_sources

    return {
        "target_root": target_root,
        "tvdb_series_id": tvdb_series_id,
        "series_name": sanitize_target_name(ai_plan.get("series_name") or ""),
        "operations": operations,
        "unmatched_sources": sorted(unmatched_sources),
        "warnings": [str(item) for item in ai_plan.get("warnings") or [] if str(item).strip()],
    }
def build_confirmed_rename_plan(
    final_path: str,
    selected_path: str,
    metadata: dict,
    media_metadata: dict,
    ai_plan: dict,
    file_tree: list[dict],
) -> dict | None:
    placement = media_metadata.get("placement") or {}
    identity = media_metadata.get("identity") or {}
    if (
        media_metadata.get("confirmed") is not True
        or placement.get("library_type") != "series"
    ):
        return None

    allowed_targets = set()
    for item in media_metadata.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("season_number") is None or str(item.get("season_number")).strip() == "":
            continue
        season = _safe_season_int(item.get("season_number"))
        episode = _safe_episode_int(item.get("episode_number"))
        if season is not None and episode is not None:
            allowed_targets.add((season, episode))
    bounded_season = None
    if (
        not allowed_targets
        and "warning:episode_inventory_unavailable"
        in (media_metadata.get("warnings") or ())
        and str(
            (media_metadata.get("retrieval") or {}).get("scope") or ""
        ) == "season"
    ):
        decision = (
            (media_metadata.get("evidence") or {}).get("decision") or {}
        )
        bounded_season = _safe_season_int(decision.get("season_number"))
        if bounded_season is None or bounded_season < 1:
            return None
    if not allowed_targets:
        if bounded_season is not None:
            pass
        elif (
            placement.get("season_number") is None
            or str(placement.get("season_number")).strip() == ""
        ):
            return None
        else:
            season = _safe_season_int(placement.get("season_number"))
            episode = _safe_episode_int(placement.get("episode_number"))
            if season is None or episode is None:
                return None
            allowed_targets.add((season, episode))

    source_lookup = _source_index(file_tree)
    source_video_paths = {node["relative_path"] for node in _video_file_nodes(file_tree)}
    chinese_title, english_title = (
        sanitize_target_name(title)
        for title in series_titles(media_metadata)
    )
    series_name = english_title or chinese_title
    if not series_name:
        return None

    target_root = _join_path(
        selected_path,
        _display_folder(chinese_title, english_title),
    )
    operations = []
    seen_sources = set()
    seen_targets = set()
    for item in ai_plan.get("episode_map") or []:
        if not isinstance(item, dict):
            continue
        if item.get("season_number") is None or str(item.get("season_number")).strip() == "":
            continue
        source_node = source_lookup.get(_clean_path(item.get("source_file") or ""))
        season = _safe_season_int(item.get("season_number"))
        episode = _safe_episode_int(item.get("episode_number"))
        allowed = (
            (season, episode) in allowed_targets
            if bounded_season is None
            else season == bounded_season and episode is not None
        )
        if not source_node or not allowed:
            continue

        source_relative_path = source_node["relative_path"]
        marker = _episode_marker_text(season, episode)
        suffix = PurePosixPath(source_relative_path).suffix
        rename_to = f"{series_name} {marker}{suffix}"
        target_dir = _join_path(target_root, f"{series_name} Season {season:02d}")
        target_relative_path = _join_path(
            f"{series_name} Season {season:02d}", rename_to
        )
        resolved_path = _join_path(target_dir, rename_to)
        if source_relative_path in seen_sources or resolved_path in seen_targets:
            continue
        seen_sources.add(source_relative_path)
        seen_targets.add(resolved_path)
        source_path = str(source_node.get("path") or "") or _join_path(
            final_path, source_relative_path
        )
        source_parent = source_path.rsplit("/", 1)[0]
        operations.append({
            "media_kind": "video",
            "content_role": item.get("content_role") or identity.get("content_kind"),
            "season_number": season,
            "episode_number": episode,
            "source_relative_path": source_relative_path,
            "source_path": source_path,
            "rename_to": rename_to,
            "renamed_source_path": _join_path(source_parent, rename_to),
            "target_dir": target_dir,
            "target_relative_path": target_relative_path,
            "final_path": resolved_path,
        })

    mapped_video_targets = {
        (operation["season_number"], operation["episode_number"])
        for operation in operations
    }
    if source_video_paths and (
        not operations
        or (bounded_season is None and mapped_video_targets != allowed_targets)
    ):
        return None

    subtitle_assignments = {}
    for item in ai_plan.get("subtitle_map") or []:
        if not isinstance(item, dict):
            continue
        source_file = str(item.get("source_file") or "").replace(
            "\\", "/"
        ).strip("/")
        season = _safe_season_int(item.get("season_number"))
        episode = _safe_episode_int(item.get("episode_number"))
        if source_file and season is not None and episode is not None:
            subtitle_assignments[source_file] = (season, episode)
    subtitle_plan = build_series_subtitle_plan(
        final_path=final_path,
        target_root=target_root,
        series_name=series_name,
        file_tree=file_tree,
        allowed_targets=allowed_targets or None,
        episode_assignments=subtitle_assignments,
    )
    if bounded_season is not None:
        invalid_bounded_sources = [
            operation["source_relative_path"]
            for operation in subtitle_plan["operations"]
            if operation.get("season_number") != bounded_season
        ]
        if invalid_bounded_sources:
            subtitle_plan = {
                "operations": [],
                "discard_sources": [],
                "kept_sources": sorted(set(
                    (subtitle_plan.get("kept_sources") or [])
                    + invalid_bounded_sources
                )),
                "unresolved_sources": invalid_bounded_sources,
            }
    operations.extend(subtitle_plan["operations"])
    unmatched_video_sources = sorted(source_video_paths - seen_sources)
    discard_sources = sorted(set(subtitle_plan["discard_sources"]))
    kept_sources = sorted(set(subtitle_plan.get("kept_sources") or []))
    if (
        not operations
        and not discard_sources
        and not kept_sources
        and not subtitle_plan["unresolved_sources"]
    ):
        return None
    return {
        "target_root": target_root,
        "series_name": series_name,
        "operations": operations,
        "unmatched_sources": unmatched_video_sources,
        "discard_sources": discard_sources,
        "kept_sources": kept_sources,
        "unresolved_sources": subtitle_plan["unresolved_sources"],
        "warnings": [
            str(item)
            for item in media_metadata.get("warnings") or []
            if str(item).strip()
        ],
    }


def enrich_media_metadata_with_rename_plan(
    media_metadata: dict,
    rename_plan: dict,
) -> dict:
    resolved = [{
        "content_role": operation.get("content_role"),
        "season_number": operation["season_number"],
        "episode_number": operation["episode_number"],
        "source_relative_path": operation["source_relative_path"],
        "final_path": operation["final_path"],
    } for operation in rename_plan.get("operations") or []
        if operation.get("media_kind") != "subtitle"
    ]
    return (
        merge_resolved_items(media_metadata, resolved)
        if resolved
        else media_metadata
    )

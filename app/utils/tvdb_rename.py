# -*- coding: utf-8 -*-

from pathlib import PurePosixPath

from app.utils.plex_naming import sanitize_path_name


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
        name = str(item.get("name") or "").strip()
        relative_path = _clean_path(item.get("relative_path") or name)
        if not name or not relative_path:
            continue
        suffix = PurePosixPath(relative_path).suffix.lower()
        if suffix and suffix not in VIDEO_EXTENSIONS:
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


def _safe_positive_int(value) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _target_root(selected_path: str, metadata: dict, ai_plan: dict) -> str:
    series_name = sanitize_path_name(ai_plan.get("series_name") or metadata.get("english_title") or metadata.get("query"))
    chinese_title = sanitize_path_name(metadata.get("chinese_title"))
    if not series_name:
        return ""
    if chinese_title and chinese_title != series_name:
        return _join_path(selected_path, chinese_title, series_name)
    return _join_path(selected_path, series_name)


def _target_relative_path(item: dict, source_relative_path: str) -> str:
    target_path = _clean_path(item.get("target_relative_path") or "")
    if target_path:
        return target_path

    target_name = sanitize_path_name(item.get("target_name") or "")
    if not target_name:
        source_suffix = PurePosixPath(source_relative_path).suffix
        season = _safe_positive_int(item.get("season_number"))
        episode = _safe_positive_int(item.get("episode_number"))
        target_name = f"S{season:02d}E{episode:02d}{source_suffix}" if season and episode else ""
    if not target_name:
        return ""

    season = _safe_positive_int(item.get("season_number"))
    if item.get("season_number") and not season:
        return ""
    if season:
        return _join_path(f"Season {season:02d}", target_name)
    return target_name


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
    known_episode_ids = _episode_ids(tvdb_episodes)
    target_root = _target_root(selected_path, metadata, ai_plan)
    if not target_root:
        return None

    operations = []
    seen_targets = set()
    for item in ai_plan["episode_map"]:
        if not isinstance(item, dict):
            return None

        source_file = _clean_path(item.get("source_file") or "")
        source_node = source_lookup.get(source_file)
        if not source_node:
            return None

        tvdb_episode_id = str(item.get("tvdb_episode_id") or "").strip()
        if tvdb_episode_id and known_episode_ids and tvdb_episode_id not in known_episode_ids:
            return None

        target_relative_path = _target_relative_path(item, source_node["relative_path"])
        if not target_relative_path:
            return None
        if target_relative_path in seen_targets:
            return None
        seen_targets.add(target_relative_path)

        target_parts = target_relative_path.split("/")
        rename_to = target_parts[-1]
        target_dir = _join_path(target_root, *target_parts[:-1])
        source_path = _join_path(final_path, source_node["relative_path"])
        source_parent = "/".join(source_node["relative_path"].split("/")[:-1])
        renamed_source_path = _join_path(final_path, source_parent, rename_to)
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

    return {
        "target_root": target_root,
        "tvdb_series_id": tvdb_series_id,
        "series_name": sanitize_path_name(ai_plan.get("series_name") or ""),
        "operations": operations,
        "warnings": [str(item) for item in ai_plan.get("warnings") or [] if str(item).strip()],
    }

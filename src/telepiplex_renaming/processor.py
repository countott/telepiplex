# -*- coding: utf-8 -*-

"""Renaming pipeline: ordinary naming first, canonical series patch second."""

from __future__ import annotations

from pathlib import Path
import re

from .context import runtime_context
from .tvdb import TvdbConfigError, TvdbRequestError, get_tvdb_series_episodes, search_tvdb_series
from telepiplex_plugin_sdk.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
)
from .models import DownloadCompletedEvent, PostDownloadResult
from .ai import (
    infer_movie_cleanup_plan_with_ai,
    infer_tvdb_episode_plan_with_ai,
)
from .media_naming import (
    build_media_naming_plan,
    infer_english_title_from_release,
    parse_episode_marker,
)
from .tvdb_rename import (
    VIDEO_EXTENSIONS,
    build_confirmed_rename_plan,
    build_tvdb_rename_plan,
    enrich_media_metadata_with_rename_plan,
)


def _storage(event: DownloadCompletedEvent):
    storage = event.storage
    if storage is None:
        raise RuntimeError("renaming processor requires a storage provider")
    return storage


def _cleanup_source_directory(storage, path):
    try:
        result = storage.delete_single_file(path)
    except Exception as exc:
        runtime_context.logger.warning(f"自动整理已完成，但源目录清理失败 path={path}: {exc}")
        return False
    if result is not True:
        runtime_context.logger.warning(f"自动整理已完成，但源目录未能清理 path={path}")
        return False
    return True


def _move_file_with_outcome(storage, source_path, target_dir):
    detailed = getattr(storage, "move_file_detailed", None)
    if callable(detailed):
        result = detailed(source_path, target_dir)
        if isinstance(result, dict) and "copied" in result:
            return result
    moved = storage.move_file(source_path, target_dir)
    return {
        "state": "moved" if moved is True else "move_failed",
        "copied": moved is True,
        "source_deleted": moved is True,
    }


def _list_response_items(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            return data["list"]
        if isinstance(data, list):
            return data
        if isinstance(response.get("list"), list):
            return response["list"]
    return []


def _file_name_from_115_item(item):
    return str(item.get("fn") or item.get("n") or item.get("file_name") or item.get("name") or "").strip()


def _file_id_from_115_item(item):
    return str(item.get("fid") or item.get("cid") or item.get("file_id") or item.get("id") or "").strip()


def _is_dir_115_item(item):
    if "is_dir" in item:
        return bool(item.get("is_dir"))
    if "file_category" in item:
        return str(item.get("file_category")) == "0"
    if "fc" in item:
        return str(item.get("fc")) != "1"
    return False


def collect_storage_file_tree(storage, root_path, max_depth=4, limit=1000):
    root_info = storage.get_file_info(root_path)
    if not root_info:
        runtime_context.logger.warn(f"TVDB整理跳过：无法读取目录 {root_path}")
        return []

    root_id = str(root_info.get("file_id") or root_info.get("cid") or root_info.get("fid") or "").strip()
    if not root_id:
        runtime_context.logger.warn(f"TVDB整理跳过：目录缺少ID {root_path}")
        return []

    tree = []

    def walk(parent_id, prefix="", depth=0):
        if depth > max_depth:
            return
        items = _list_response_items(storage.get_file_list({"cid": parent_id, "limit": limit, "show_dir": 1}))
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _file_name_from_115_item(item)
            if not name:
                continue
            relative_path = f"{prefix}/{name}".strip("/")
            is_dir = _is_dir_115_item(item)
            node = {
                "name": name,
                "relative_path": relative_path,
                "is_dir": is_dir,
                "file_id": _file_id_from_115_item(item),
                "size": item.get("fs") or item.get("size") or item.get("size_byte") or 0,
            }
            if is_dir:
                tree.append(node)
                child_id = node["file_id"]
                if child_id:
                    walk(child_id, relative_path, depth + 1)
            elif Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                tree.append(node)

    walk(root_id)
    return tree


def _event_file_tree(event: DownloadCompletedEvent):
    if isinstance(event.file_tree, list) and event.file_tree:
        return [dict(item) for item in event.file_tree if isinstance(item, dict)]
    return collect_storage_file_tree(_storage(event), event.final_path)


def _source_path(event: DownloadCompletedEvent, node: dict) -> str:
    absolute = str(node.get("path") or "").strip()
    if absolute:
        return absolute
    relative = str(node.get("relative_path") or node.get("name") or "").strip("/")
    return f"{str(event.final_path).rstrip('/')}/{relative}"


def _selection_key(value):
    name = Path(str(value or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix in VIDEO_EXTENSIONS else name
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", " ", stem.casefold()).strip()


def _video_nodes(file_tree):
    return [
        item for item in file_tree
        if not item.get("is_dir")
        and Path(str(item.get("name") or "")).suffix.lower() in VIDEO_EXTENSIONS
    ]


def _movie_plan_hints(event, media_metadata):
    strong = []
    for item in (media_metadata or {}).get("items") or []:
        if isinstance(item, dict) and item.get("source_hint"):
            strong.append(item["source_hint"])
    release = event.release if isinstance(event.release, dict) else {}
    ordinary = [
        release.get("title"),
        (event.naming_metadata or {}).get("release_title"),
    ]
    return strong, ordinary


def _find_unique_hint(video_nodes, hints):
    for hint in hints:
        hint_path = str(hint or "").strip("/")
        hint_key = _selection_key(hint_path)
        matches = [
            node for node in video_nodes
            if hint_path in {
                str(node.get("relative_path") or "").strip("/"),
                str(node.get("name") or ""),
            }
            or (hint_key and _selection_key(node.get("name")) == hint_key)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _lookup_ai_movie_selection(video_nodes, plan):
    if not isinstance(plan, dict):
        return None
    index = {}
    basename_counts = {}
    for node in video_nodes:
        relative = str(node.get("relative_path") or node.get("name") or "").strip("/")
        index[relative] = node
        name = str(node.get("name") or "")
        basename_counts[name] = basename_counts.get(name, 0) + 1
    for node in video_nodes:
        name = str(node.get("name") or "")
        if basename_counts.get(name) == 1:
            index[name] = node
    main = index.get(str(plan.get("main_video") or "").strip("/"))
    if main is None:
        return None
    expected_discard = {
        str(node.get("relative_path") or node.get("name") or "").strip("/")
        for node in video_nodes if node is not main
    }
    actual_discard = set()
    for value in plan.get("discard_files") or []:
        node = index.get(str(value or "").strip("/"))
        if node is not None and node is not main:
            actual_discard.add(
                str(node.get("relative_path") or node.get("name") or "").strip("/")
            )
    if actual_discard != expected_discard:
        return None
    return main


def _choose_movie_main_video(event, naming_metadata, file_tree):
    video_nodes = _video_nodes(file_tree)
    if not video_nodes:
        return None, ""
    media_metadata, _present = _media_metadata_state(event)
    strong, ordinary = _movie_plan_hints(event, media_metadata)
    main = _find_unique_hint(video_nodes, strong)
    if main:
        return main, "confirmed_source_hint"
    if len(video_nodes) == 1:
        return video_nodes[0], "unique_video"
    main = _find_unique_hint(video_nodes, ordinary)
    if main:
        return main, "release_filename"

    context = {
        "confirmed_media_metadata": media_metadata,
        "naming_metadata": naming_metadata,
        "release": event.release or {},
        "resource_name": event.resource_name,
        "download_root": event.final_path,
        "file_tree": file_tree,
    }
    ai_plan = (
        infer_movie_cleanup_plan_with_ai(context)
        if _has_ai_episode_inference_config()
        else None
    )
    main = _lookup_ai_movie_selection(video_nodes, ai_plan)
    if main:
        return main, "ai_evidence"

    ranked = sorted(
        video_nodes,
        key=lambda item: int(item.get("size") or 0),
        reverse=True,
    )
    largest = int(ranked[0].get("size") or 0)
    second = int(ranked[1].get("size") or 0)
    ratio = float(
        ((runtime_context.config or {}).get("selection") or {}).get(
            "movie_size_fallback_ratio", 1.5
        )
    )
    if largest > 0 and (second == 0 or largest / second >= ratio):
        return ranked[0], f"size_fallback_ratio_{largest / max(second, 1):.2f}"
    return None, ""


def _tvdb_title_from_metadata(metadata):
    metadata = metadata or {}
    title = metadata.get("english_title") or metadata.get("query") or ""
    year = str(metadata.get("year") or "").strip()
    if title and year and title.endswith(f" {year}"):
        title = title[: -len(year)].strip()
    return " ".join(str(title or "").split())


def _get_tvdb_candidates_and_episodes(metadata):
    title = _tvdb_title_from_metadata(metadata)
    if not title:
        runtime_context.logger.warn(f"TVDB整理跳过：元数据缺少英文标题 {metadata}")
        return [], []

    try:
        candidates = search_tvdb_series(title, year=str((metadata or {}).get("year") or "").strip())[:3]
    except TvdbConfigError as e:
        runtime_context.logger.info(f"TVDB整理跳过：{e}")
        return [], []
    except TvdbRequestError as e:
        runtime_context.logger.warn(f"TVDB搜索失败，跳过TVDB整理: {e}")
        return [], []

    episodes = []
    for candidate in candidates:
        series_id = str(candidate.get("tvdb_series_id") or "").strip()
        if not series_id:
            continue
        try:
            series_episodes = get_tvdb_series_episodes(series_id, season_type="default")
        except TvdbRequestError as e:
            runtime_context.logger.warn(f"TVDB剧集列表获取失败 series_id={series_id}: {e}")
            continue
        for episode in series_episodes:
            item = dict(episode)
            item["tvdb_series_id"] = series_id
            episodes.append(item)
    return candidates, episodes


def _has_ai_episode_inference_config():
    ai_config = runtime_context.config.get("ai") or {}
    return bool(
        ai_config.get("enable", True)
        and
        str(ai_config.get("api_url") or ai_config.get("base_url") or "").strip()
        and str(ai_config.get("api_key") or "").strip()
        and str(ai_config.get("model") or "").strip()
    )


def _has_metadata_value(value):
    return value is not None and value != "" and value != [] and value != {}


def _filename_metadata_from_resource(resource_name):
    inferred_title = infer_english_title_from_release(resource_name)
    if not inferred_title:
        return None
    return {
        "source": "filename",
        "chinese_title": inferred_title,
        "english_title": inferred_title,
        "query": inferred_title,
        "release_title": resource_name,
    }


def _merge_tvdb_metadata(naming_metadata=None, metadata=None, filename_metadata=None):
    merged = {}
    for source in (naming_metadata, metadata):
        if not source:
            continue
        for key, value in source.items():
            if _has_metadata_value(value) or key not in merged:
                if key in {"external_ids", "evidence"} and isinstance(value, (dict, list)):
                    merged[key] = value.copy()
                elif _has_metadata_value(value):
                    merged[key] = value
    if filename_metadata:
        for key, value in filename_metadata.items():
            if key not in merged and _has_metadata_value(value):
                merged[key] = value
    return merged or None


def _attempt_legacy_tvdb_ai_episode_rename(event: DownloadCompletedEvent, metadata):
    if not metadata or not _has_ai_episode_inference_config():
        return None

    storage = _storage(event)
    tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
    if not tvdb_candidates or not tvdb_episodes:
        return None

    file_tree = _event_file_tree(event)
    video_count = len([item for item in file_tree if not item.get("is_dir")])
    if not video_count:
        runtime_context.logger.warn(f"TVDB整理跳过：目录中未找到视频文件 {event.final_path}")
        return None

    context = {
        "metadata": metadata,
        "release_title": metadata.get("release_title") or event.resource_name,
        "resource_name": event.resource_name,
        "download_path": event.final_path,
        "file_tree": file_tree,
        "tvdb_candidates": tvdb_candidates,
        "tvdb_episodes": tvdb_episodes,
        "naming_rules": {
            "target_root": "selected_path / chinese_title (tvdb series_name)",
            "target_relative_path": "Series Name Season XX / Series Name SXXEXX.ext",
            "source_file": "must exactly match one file_tree relative_path or a unique file name",
        },
    }
    ai_plan = infer_tvdb_episode_plan_with_ai(context)
    rename_plan = build_tvdb_rename_plan(
        final_path=event.final_path,
        selected_path=event.selected_path,
        metadata=metadata,
        ai_plan=ai_plan,
        file_tree=file_tree,
        tvdb_candidates=tvdb_candidates,
        tvdb_episodes=tvdb_episodes,
    )
    if not rename_plan:
        runtime_context.logger.warn(f"TVDB整理跳过：AI映射未通过交叉校验 path={event.final_path}")
        return None

    _assert_no_target_conflicts(storage, rename_plan)

    root_source_deleted = None
    for operation in rename_plan["operations"]:
        storage.create_dir_recursive(operation["target_dir"])
        current_source_path = operation["source_path"]
        if Path(operation["source_path"]).name != operation["rename_to"]:
            if not storage.rename(operation["source_path"], operation["rename_to"]):
                raise RuntimeError(f"TVDB整理失败：重命名失败 {operation['source_path']}")
            current_source_path = operation["renamed_source_path"]
        outcome = _move_file_with_outcome(
            storage, current_source_path, operation["target_dir"]
        )
        if not outcome.get("copied"):
            raise RuntimeError(f"TVDB整理失败：移动失败 {current_source_path}")
        if (
            len(rename_plan["operations"]) == 1
            and str(operation["source_path"]).rstrip("/")
            == str(event.final_path).rstrip("/")
        ):
            root_source_deleted = bool(outcome.get("source_deleted"))

    if root_source_deleted is False:
        rename_plan["cleanup_complete"] = False
    elif root_source_deleted is None and event.final_path != rename_plan["target_root"]:
        _cleanup_source_directory(storage, event.final_path)

    return rename_plan


def _media_metadata_state(event: DownloadCompletedEvent):
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    present = MEDIA_METADATA_KEY in metadata
    return extract_confirmed_media_metadata(metadata), present


def _confirmed_series_metadata(event: DownloadCompletedEvent):
    contract = extract_confirmed_media_metadata(event.metadata)
    placement = contract.get("placement") if isinstance(contract, dict) else None
    if not isinstance(placement, dict) or placement.get("library_type") != "series":
        return None
    return contract


def _unorganized_root() -> str:
    return str(
        ((runtime_context.config or {}).get("media") or {}).get("unorganized_path") or ""
    ).rstrip("/")


def _move_unmatched_to_unorganized(event, unmatched_sources):
    if not unmatched_sources:
        return ""
    storage = _storage(event)
    for relative_path in unmatched_sources:
        source_path = (
            f"{str(event.final_path).rstrip('/')}/"
            f"{str(relative_path).strip('/')}"
        )
        if storage.delete_single_file(source_path) is not True:
            raise RuntimeError(f"无法删除未匹配视频 {source_path}")
    return ""


def _move_confirmed_failure_to_unorganized(event):
    unorganized_root = _unorganized_root()
    if not unorganized_root:
        raise RuntimeError("确认方案映射失败，但 media.unorganized_path 未配置")
    storage = _storage(event)
    source_path = str(event.final_path or "").rstrip("/")
    source_leaf = source_path.rsplit("/", 1)[-1]
    if not storage.create_dir_recursive(unorganized_root):
        raise RuntimeError(f"无法创建未整理目录 {unorganized_root}")
    if storage.move_file(source_path, unorganized_root) is not True:
        raise RuntimeError(f"无法移动确认方案失败目录 {source_path}")
    return f"{unorganized_root}/{source_leaf}"


class ConfirmedPlanConflict(RuntimeError):
    pass


class BatchRenameInterrupted(RuntimeError):
    def __init__(self, *, completed, total, target_root, failed_path, cause):
        super().__init__(str(cause))
        self.completed = int(completed)
        self.total = int(total)
        self.target_root = str(target_root)
        self.failed_path = str(failed_path)


def _deterministic_episode_plan(media_metadata: dict, file_tree: list[dict]):
    placement = media_metadata.get("placement") or {}
    allowed = {
        (int(item["season_number"]), int(item["episode_number"]))
        for item in media_metadata.get("items") or []
        if item.get("season_number") is not None
        and item.get("episode_number") is not None
    }
    if not allowed and placement.get("season_number") is not None and placement.get("episode_number") is not None:
        allowed.add((int(placement["season_number"]), int(placement["episode_number"])))
    mapped = {}
    nodes_by_path = {
        str(node.get("relative_path") or "").strip("/"): node
        for node in file_tree if not node.get("is_dir")
    }
    for item in media_metadata.get("items") or []:
        hint = str(item.get("source_hint") or "").strip("/")
        if not hint:
            continue
        marker = (int(item["season_number"]), int(item["episode_number"]))
        node = nodes_by_path.get(hint)
        if marker in allowed and node is not None and marker not in mapped:
            mapped[marker] = node
    for node in file_tree:
        if node.get("is_dir"):
            continue
        marker = parse_episode_marker(node.get("relative_path") or node.get("name"))
        if marker in allowed and marker not in mapped:
            mapped[marker] = node
    if not allowed or set(mapped) != allowed:
        return None
    return {
        "episode_map": [{
            "source_file": node["relative_path"],
            "season_number": season,
            "episode_number": episode,
            "content_role": media_metadata.get("identity", {}).get("content_kind"),
        } for (season, episode), node in sorted(mapped.items())],
        "warnings": [],
    }


def _assert_no_target_conflicts(storage, rename_plan):
    for operation in rename_plan.get("operations") or []:
        target_path = (
            f"{str(operation['target_dir']).rstrip('/')}/"
            f"{operation['rename_to']}"
        )
        if storage.get_file_info(target_path):
            raise ConfirmedPlanConflict(
                f"已确认目标编号发生冲突：{operation['rename_to']}"
            )


def _attempt_confirmed_series_rename(
    event: DownloadCompletedEvent,
    metadata: dict,
    media_metadata: dict,
):
    if not metadata:
        return None

    storage = _storage(event)
    file_tree = _event_file_tree(event)
    if not [item for item in file_tree if not item.get("is_dir")]:
        runtime_context.logger.warn(
            f"确认方案整理跳过：目录中未找到视频文件 {event.final_path}"
        )
        return None

    ai_plan = _deterministic_episode_plan(media_metadata, file_tree)
    ai_was_used = False
    if ai_plan is None and _has_ai_episode_inference_config():
        tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
        context = {
            "metadata": metadata,
            "confirmed_media_metadata": media_metadata,
            "release_title": metadata.get("release_title") or event.resource_name,
            "resource_name": event.resource_name,
            "download_path": event.final_path,
            "file_tree": file_tree,
            "tvdb_candidates": tvdb_candidates,
            "tvdb_episodes": tvdb_episodes,
        }
        ai_plan = infer_tvdb_episode_plan_with_ai(context)
        ai_was_used = True
    rename_plan = build_confirmed_rename_plan(
        final_path=event.final_path,
        selected_path=event.selected_path,
        metadata=metadata,
        media_metadata=media_metadata,
        ai_plan=ai_plan or {},
        file_tree=file_tree,
    )
    if not rename_plan:
        runtime_context.logger.warn(
            f"确认方案整理跳过：AI文件映射未通过锁定校验 path={event.final_path}"
        )
        return None

    unmatched_sources = rename_plan.get("unmatched_sources") or []
    if unmatched_sources:
        nodes_by_path = {
            str(node.get("relative_path") or node.get("name") or "").strip("/"): node
            for node in file_tree if not node.get("is_dir")
        }
        mapped_sizes = [
            int(nodes_by_path.get(operation["source_relative_path"], {}).get("size") or 0)
            for operation in rename_plan.get("operations") or []
        ]
        largest_mapped = max(mapped_sizes or [0])
        selection = (runtime_context.config or {}).get("selection") or {}
        relative_threshold = largest_mapped * float(
            selection.get("unmatched_large_ratio", 0.25)
        )
        absolute_threshold = int(
            selection.get("unmatched_large_min_bytes", 314572800)
        )
        large_unmatched = {
            path for path in unmatched_sources
            if int(nodes_by_path.get(path, {}).get("size") or 0)
            >= max(relative_threshold, absolute_threshold)
        }
        if large_unmatched and not ai_was_used:
            tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
            context = {
                "metadata": metadata,
                "confirmed_media_metadata": media_metadata,
                "release": event.release or {},
                "release_title": metadata.get("release_title") or event.resource_name,
                "resource_name": event.resource_name,
                "download_path": event.final_path,
                "file_tree": file_tree,
                "tvdb_candidates": tvdb_candidates,
                "tvdb_episodes": tvdb_episodes,
            }
            ai_plan = infer_tvdb_episode_plan_with_ai(context)
            ai_was_used = True
            rename_plan = build_confirmed_rename_plan(
                final_path=event.final_path,
                selected_path=event.selected_path,
                metadata=metadata,
                media_metadata=media_metadata,
                ai_plan=ai_plan or {},
                file_tree=file_tree,
            )
            if not rename_plan:
                return None
            unmatched_sources = rename_plan.get("unmatched_sources") or []
            large_unmatched = large_unmatched.intersection(unmatched_sources)
        if large_unmatched:
            discard = {
                str(value or "").strip("/")
                for value in (ai_plan or {}).get("discard_files") or []
            }
            if not large_unmatched.issubset(discard):
                runtime_context.logger.warning(
                    "确认方案存在未经 AI 明确处理的大视频，整批转入未整理"
                )
                return None

    _assert_no_target_conflicts(storage, rename_plan)
    operations = rename_plan["operations"]
    completed = 0
    root_source_deleted = None
    for operation in operations:
        current_source_path = operation["source_path"]
        try:
            if not storage.create_dir_recursive(operation["target_dir"]):
                raise RuntimeError(f"无法创建 {operation['target_dir']}")
            if Path(operation["source_path"]).name != operation["rename_to"]:
                if storage.rename(operation["source_path"], operation["rename_to"]) is not True:
                    raise RuntimeError(f"重命名失败 {operation['source_path']}")
                current_source_path = operation["renamed_source_path"]
            outcome = _move_file_with_outcome(
                storage, current_source_path, operation["target_dir"]
            )
            if not outcome.get("copied"):
                raise RuntimeError(f"移动失败 {current_source_path}")
            if (
                len(operations) == 1
                and str(operation["source_path"]).rstrip("/")
                == str(event.final_path).rstrip("/")
            ):
                root_source_deleted = bool(outcome.get("source_deleted"))
        except Exception as exc:
            raise BatchRenameInterrupted(
                completed=completed,
                total=len(operations),
                target_root=rename_plan["target_root"],
                failed_path=current_source_path,
                cause=exc,
            ) from exc
        completed += 1

    unmatched_sources = rename_plan.get("unmatched_sources") or []
    try:
        unmatched_dir = _move_unmatched_to_unorganized(event, unmatched_sources)
    except Exception as exc:
        raise BatchRenameInterrupted(
            completed=len(operations),
            total=len(operations) + len(unmatched_sources),
            target_root=rename_plan["target_root"],
            failed_path=str(event.final_path),
            cause=exc,
        ) from exc
    rename_plan["unmatched_target"] = unmatched_dir
    rename_plan["cleanup_complete"] = True
    if root_source_deleted is False:
        rename_plan["cleanup_complete"] = False
    elif root_source_deleted is None and event.final_path != rename_plan["target_root"]:
        rename_plan["cleanup_complete"] = _cleanup_source_directory(
            storage, event.final_path
        )
    rename_plan["media_metadata"] = enrich_media_metadata_with_rename_plan(
        media_metadata,
        rename_plan,
    )
    return rename_plan


def _attempt_tvdb_ai_episode_rename(event: DownloadCompletedEvent, metadata):
    media_metadata, contract_present = _media_metadata_state(event)
    confirmed_series = _confirmed_series_metadata(event)
    if contract_present:
        if confirmed_series:
            return _attempt_confirmed_series_rename(
                event,
                metadata,
                confirmed_series,
            )
        return None
    return _attempt_legacy_tvdb_ai_episode_rename(event, metadata)


def process_tvdb_episode(event: DownloadCompletedEvent) -> PostDownloadResult:
    media_metadata, contract_present = _media_metadata_state(event)
    if contract_present and media_metadata is None:
        return PostDownloadResult(
            True,
            final_path=event.final_path,
            message="⚠️ media_metadata 无效或版本不受支持；文件保持原位。",
            should_stop=True,
            metadata=event.metadata,
        )
    filename_metadata = _filename_metadata_from_resource(event.resource_name)
    metadata = _merge_tvdb_metadata(
        naming_metadata=event.naming_metadata,
        metadata=event.metadata,
        filename_metadata=filename_metadata,
    )
    confirmed_series = _confirmed_series_metadata(event)
    try:
        rename_plan = _attempt_tvdb_ai_episode_rename(event, metadata)
    except ConfirmedPlanConflict as exc:
        unorganized_target = _move_confirmed_failure_to_unorganized(event)
        return PostDownloadResult(
            True,
            final_path=unorganized_target,
            message=(
                f"⚠️ {exc}\n映射规则存在冲突，整批已移入未整理。\n"
                f"保存目录：`{unorganized_target}`"
            ),
            should_stop=True,
        )
    except BatchRenameInterrupted as exc:
        return PostDownloadResult(
            True,
            final_path=exc.target_root,
            message=(
                f"⚠️ 批量整理部分完成（{exc.completed}/{exc.total}），"
                "已停止自动重试，请人工检查。\n"
                f"失败位置：`{exc.failed_path}`\n"
                f"目标目录：`{exc.target_root}`"
            ),
            should_stop=True,
            metadata=event.metadata,
        )
    if not rename_plan:
        if confirmed_series:
            unorganized_target = _move_confirmed_failure_to_unorganized(event)
            return PostDownloadResult(
                True,
                final_path=unorganized_target,
                message=(
                    "⚠️ 下载后 AI 文件映射失败，已移入未整理目录。\n\n"
                    f"保存目录：`{unorganized_target}`"
                ),
                should_stop=True,
            )
        return PostDownloadResult(False, final_path=event.final_path)

    message = (
        f"✅ TVDB 自动整理完成：`{rename_plan['series_name'] or rename_plan['target_root'].split('/')[-1]}`\n"
        f"文件数：{len(rename_plan['operations'])} 个文件\n\n"
        f"保存目录：`{rename_plan['target_root']}`"
    )
    if rename_plan.get("tvdb_series_id"):
        message += f"\nTVDB：`{rename_plan['tvdb_series_id']}`"
    if rename_plan.get("warnings"):
        message += f"\n提示：{'; '.join(rename_plan['warnings'][:2])}"
    if not rename_plan.get("cleanup_complete", True):
        message = (
            "⚠️ 视频已完成整理，但源目录清理未完成，请人工检查。\n\n"
            f"保存目录：`{rename_plan['target_root']}`"
        )
    result_metadata = event.metadata
    if rename_plan.get("media_metadata"):
        result_metadata = attach_media_metadata(
            event.metadata,
            rename_plan["media_metadata"],
        )
    return PostDownloadResult(
        True,
        final_path=rename_plan["target_root"],
        message=message,
        should_stop=True,
        metadata=result_metadata,
    )


def _attempt_media_auto_rename(event: DownloadCompletedEvent, naming_metadata):
    if not naming_metadata:
        return None

    storage = _storage(event)
    file_tree = _event_file_tree(event)
    main_video, selection_reason = _choose_movie_main_video(
        event,
        naming_metadata,
        file_tree,
    )
    if not main_video:
        runtime_context.logger.warn(f"自动整理跳过：目录中未找到视频文件 {event.final_path}")
        return None

    original_file_name = main_video["name"]
    release_title = naming_metadata.get("release_title") or event.resource_name
    plan = build_media_naming_plan(naming_metadata, release_title, original_file_name)
    if not plan:
        runtime_context.logger.warn(f"自动整理跳过：元数据不足 {naming_metadata}")
        return None

    target_path = f"{event.selected_path}/{plan.target_relative_dir}"
    target_file = f"{target_path.rstrip('/')}/{plan.file_name}"
    if storage.get_file_info(target_file):
        raise ConfirmedPlanConflict(f"目标文件发生冲突：{target_file}")
    if not storage.create_dir_recursive(target_path):
        raise RuntimeError(f"自动整理失败：无法创建目标目录 {target_path}")

    original_file_path = _source_path(event, main_video)
    source_root = str(original_file_path).rsplit("/", 1)[0]
    renamed_file_path = f"{source_root}/{plan.file_name}"
    if original_file_name != plan.file_name:
        if storage.rename(original_file_path, plan.file_name) is not True:
            raise RuntimeError(f"自动整理失败：重命名失败 {original_file_path}")

    outcome = _move_file_with_outcome(storage, renamed_file_path, target_path)
    if not outcome.get("copied"):
        raise RuntimeError(f"自动整理失败：移动失败 {renamed_file_path}")
    cleanup_complete = True
    if str(event.final_path).rstrip("/") == str(original_file_path).rstrip("/"):
        cleanup_complete = bool(outcome.get("source_deleted"))
    elif event.final_path != target_path:
        cleanup_complete = _cleanup_source_directory(storage, event.final_path)

    return target_path, plan, cleanup_complete, selection_reason


def _standalone_contract_naming_metadata(event: DownloadCompletedEvent):
    media_metadata = extract_confirmed_media_metadata(event.metadata)
    placement = (
        media_metadata.get("placement")
        if isinstance(media_metadata, dict)
        else None
    )
    if not isinstance(placement, dict) or placement.get("mapping_kind") != "standalone":
        return None
    identity = media_metadata.get("identity")
    if not isinstance(identity, dict):
        return None
    result = dict(identity)
    result["source"] = "media_metadata"
    return result


def process_generic_media(event: DownloadCompletedEvent) -> PostDownloadResult:
    naming_auto_metadata = (
        _standalone_contract_naming_metadata(event)
        or event.naming_metadata
    )
    try:
        result = _attempt_media_auto_rename(event, naming_auto_metadata)
    except ConfirmedPlanConflict as exc:
        unorganized_target = _move_confirmed_failure_to_unorganized(event)
        return PostDownloadResult(
            True,
            final_path=unorganized_target,
            message=(
                f"⚠️ {exc}\n映射规则存在冲突，整批已移入未整理。\n"
                f"保存目录：`{unorganized_target}`"
            ),
            should_stop=True,
            metadata=event.metadata,
        )
    if not result:
        return PostDownloadResult(False, final_path=event.final_path)
    target_path, plan, cleanup_complete, selection_reason = result
    message = (
        f"✅ 自动整理完成：`{plan.file_name}`\n"
        f"主视频依据：{selection_reason}\n\n保存目录：`{target_path}`"
        if cleanup_complete
        else (
            "⚠️ 视频已完成整理，但源目录清理未完成，请人工检查。\n\n"
            f"保存目录：`{target_path}`"
        )
    )
    return PostDownloadResult(
        True,
        final_path=target_path,
        message=message,
        should_stop=True,
        metadata=event.metadata,
    )

# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import init
from app.adapters.tvdb import TvdbConfigError, TvdbRequestError, get_tvdb_series_episodes, search_tvdb_series
from app.core.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
    series_folder_name,
    series_titles,
)
from app.core.module_registry import DownloadCompletedEvent, PostDownloadResult
from app.utils.ai import infer_tvdb_episode_plan_with_ai
from app.utils.confirmed_file_mapping import (
    map_confirmed_files,
    unresolved_mapping_context,
)
from app.utils.media_naming import build_media_naming_plan, infer_english_title_from_release
from app.utils.tvdb_rename import (
    VIDEO_EXTENSIONS,
    build_confirmed_rename_plan,
    build_tvdb_rename_plan,
    enrich_media_metadata_with_rename_plan,
)


def _storage(event: DownloadCompletedEvent):
    storage = event.storage or getattr(init, "openapi_115", None)
    if storage is None:
        raise RuntimeError("renaming processor requires a storage provider")
    return storage


def _cleanup_source_directory(storage, path):
    try:
        result = storage.delete_single_file(path)
    except Exception as exc:
        init.logger.warn(f"自动整理已完成，但源目录清理失败 path={path}: {exc}")
        return False
    if result is not True:
        init.logger.warn(f"自动整理已完成，但源目录未能清理 path={path}")
        return False
    return True


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


def collect_storage_file_tree(
    storage,
    root_path,
    max_depth=4,
    limit=1000,
    include_non_video=False,
):
    root_info = storage.get_file_info(root_path)
    if not root_info:
        init.logger.warn(f"TVDB整理跳过：无法读取目录 {root_path}")
        return []

    root_id = str(root_info.get("file_id") or root_info.get("cid") or root_info.get("fid") or "").strip()
    if not root_id:
        init.logger.warn(f"TVDB整理跳过：目录缺少ID {root_path}")
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
            else:
                node["is_video"] = Path(name).suffix.lower() in VIDEO_EXTENSIONS
            if not is_dir and (node["is_video"] or include_non_video):
                tree.append(node)

    walk(root_id)
    return tree


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
        init.logger.warn(f"TVDB整理跳过：元数据缺少英文标题 {metadata}")
        return [], []

    try:
        candidates = search_tvdb_series(title, year=str((metadata or {}).get("year") or "").strip())[:3]
    except TvdbConfigError as e:
        init.logger.info(f"TVDB整理跳过：{e}")
        return [], []
    except TvdbRequestError as e:
        init.logger.warn(f"TVDB搜索失败，跳过TVDB整理: {e}")
        return [], []

    episodes = []
    for candidate in candidates:
        series_id = str(candidate.get("tvdb_series_id") or "").strip()
        if not series_id:
            continue
        try:
            series_episodes = get_tvdb_series_episodes(series_id, season_type="default")
        except TvdbRequestError as e:
            init.logger.warn(f"TVDB剧集列表获取失败 series_id={series_id}: {e}")
            continue
        for episode in series_episodes:
            item = dict(episode)
            item["tvdb_series_id"] = series_id
            episodes.append(item)
    return candidates, episodes


def _has_ai_episode_inference_config():
    ai_config = init.bot_config.get("ai") or {}
    return bool(
        str(ai_config.get("api_url") or ai_config.get("base_url") or "").strip()
        and str(ai_config.get("api_key") or "").strip()
        and str(ai_config.get("model") or "").strip()
    )


def _minimum_video_size_bytes() -> int:
    policy = (init.bot_config or {}).get("clean_policy") or {}
    if str(policy.get("switch") or "off").lower() == "off":
        return 0
    raw = str(policy.get("less_than") or "").strip().upper()
    multipliers = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    if not raw:
        return 0
    suffix = raw[-1]
    try:
        return int(raw[:-1]) * multipliers[suffix] if suffix in multipliers else int(raw)
    except (TypeError, ValueError):
        return 0


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

    file_tree = collect_storage_file_tree(storage, event.final_path)
    video_count = len([item for item in file_tree if not item.get("is_dir")])
    if not video_count:
        init.logger.warn(f"TVDB整理跳过：目录中未找到视频文件 {event.final_path}")
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
        init.logger.warn(f"TVDB整理跳过：AI映射未通过交叉校验 path={event.final_path}")
        return None

    for operation in rename_plan["operations"]:
        storage.create_dir_recursive(operation["target_dir"])
        current_source_path = operation["source_path"]
        if Path(operation["source_path"]).name != operation["rename_to"]:
            if not storage.rename(operation["source_path"], operation["rename_to"]):
                raise RuntimeError(f"TVDB整理失败：重命名失败 {operation['source_path']}")
            current_source_path = operation["renamed_source_path"]
        if not storage.move_file(current_source_path, operation["target_dir"]):
            raise RuntimeError(f"TVDB整理失败：移动失败 {current_source_path}")

    if event.final_path != rename_plan["target_root"]:
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
        ((init.bot_config or {}).get("media") or {}).get("unorganized_path") or ""
    ).rstrip("/")


def _move_unmatched_to_unorganized(event, unmatched_sources):
    if not unmatched_sources:
        return ""
    unorganized_root = _unorganized_root()
    if not unorganized_root:
        raise RuntimeError("未匹配文件存在，但 media.unorganized_path 未配置")
    source_leaf = str(event.final_path or "").rstrip("/").rsplit("/", 1)[-1]
    target_dir = f"{unorganized_root}/{source_leaf}"
    storage = _storage(event)
    if not storage.create_dir_recursive(target_dir):
        raise RuntimeError(f"无法创建未整理目录 {target_dir}")
    for relative_path in unmatched_sources:
        source_path = (
            f"{str(event.final_path).rstrip('/')}/"
            f"{str(relative_path).strip('/')}"
        )
        if storage.move_file(source_path, target_dir) is not True:
            raise RuntimeError(f"无法移动未匹配文件 {source_path}")
    return target_dir


def _is_video_node(item):
    if not isinstance(item, dict) or item.get("is_dir"):
        return False
    if "is_video" in item:
        return bool(item.get("is_video"))
    return Path(str(item.get("name") or item.get("relative_path") or "")).suffix.lower() in VIDEO_EXTENSIONS


def _cleanup_discarded_files(event, file_tree, ineligible_sources=None):
    storage = _storage(event)
    deleted = []
    failed = []
    ineligible = {
        str(value or "").strip("/")
        for value in ineligible_sources or []
        if str(value or "").strip("/")
    }
    for item in file_tree or []:
        if not isinstance(item, dict) or item.get("is_dir"):
            continue
        relative_path = str(item.get("relative_path") or item.get("name") or "").strip("/")
        if not relative_path:
            continue
        is_video = _is_video_node(item)
        if is_video and relative_path not in ineligible:
            continue
        reason = "below_minimum_size" if is_video else "non_video"
        source_path = f"{str(event.final_path).rstrip('/')}/{relative_path}"
        try:
            removed = storage.delete_single_file(source_path)
        except Exception as exc:
            failed.append({"source": relative_path, "reason": reason, "error": str(exc)})
            continue
        if removed is True:
            deleted.append(relative_path)
        else:
            failed.append({"source": relative_path, "reason": reason, "error": "删除失败"})
    return {"deleted": deleted, "failed": failed}


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
        "source_path": source_path,
        "target_path": (
            f"{str(target_dir).rstrip('/')}/{Path(source_path).name}"
        ),
    }


def _move_sources_to_unorganized(event, relative_paths):
    sources = []
    seen = set()
    for value in relative_paths or []:
        relative_path = str(value or "").strip("/")
        if relative_path and relative_path not in seen:
            seen.add(relative_path)
            sources.append(relative_path)
    if not sources:
        return {"target_dir": "", "moved": [], "failed": []}

    unorganized_root = _unorganized_root()
    source_leaf = str(event.final_path or "").rstrip("/").rsplit("/", 1)[-1]
    target_dir = f"{unorganized_root}/{source_leaf}" if unorganized_root else ""
    if not target_dir:
        return {
            "target_dir": "",
            "moved": [],
            "failed": [{"source": source, "error": "media.unorganized_path 未配置"} for source in sources],
        }
    storage = _storage(event)
    if storage.create_dir_recursive(target_dir) is not True:
        return {
            "target_dir": target_dir,
            "moved": [],
            "failed": [{"source": source, "error": f"无法创建 {target_dir}"} for source in sources],
        }

    moved = []
    failed = []
    for relative_path in sources:
        source_path = f"{str(event.final_path).rstrip('/')}/{relative_path}"
        try:
            outcome = _move_file_with_outcome(storage, source_path, target_dir)
        except Exception as exc:
            failed.append({"source": relative_path, "error": str(exc)})
            continue
        if outcome.get("state") == "moved":
            moved.append(relative_path)
        elif outcome.get("copied"):
            moved.append(relative_path)
            failed.append({
                "source": relative_path,
                "error": "已复制到未整理，但源文件删除失败",
            })
        else:
            failed.append({"source": relative_path, "error": "移动到未整理失败"})
    return {"target_dir": target_dir, "moved": moved, "failed": failed}


def _relative_to_source_root(event, path):
    root = str(event.final_path or "").rstrip("/") + "/"
    value = str(path or "")
    return value[len(root):] if value.startswith(root) else value.rsplit("/", 1)[-1]


def _execute_confirmed_rename_plan(event, rename_plan, file_tree):
    storage = _storage(event)
    planned = list(rename_plan.get("operations") or [])
    _assert_no_target_conflicts(storage, rename_plan)
    coverage = rename_plan.get("mapping_coverage") or {}
    cleanup = _cleanup_discarded_files(
        event,
        file_tree,
        coverage.get("ineligible_sources"),
    )
    successful = []
    failed_operation = None
    retained_sources = []
    remaining_sources = list(rename_plan.get("unmatched_sources") or [])

    for index, operation in enumerate(planned):
        current_source_path = operation["source_path"]
        try:
            if storage.create_dir_recursive(operation["target_dir"]) is not True:
                raise RuntimeError(f"无法创建 {operation['target_dir']}")
            if Path(operation["source_path"]).name != operation["rename_to"]:
                if storage.rename(operation["source_path"], operation["rename_to"]) is not True:
                    raise RuntimeError(f"重命名失败 {operation['source_path']}")
                current_source_path = operation["renamed_source_path"]
            outcome = _move_file_with_outcome(
                storage,
                current_source_path,
                operation["target_dir"],
            )
            if outcome.get("copied") and not outcome.get("source_deleted"):
                successful.append(operation)
                retained_sources.append({
                    "source": _relative_to_source_root(event, current_source_path),
                    "target": outcome.get("target_path"),
                    "error": "正式文件已复制，但源文件删除失败",
                })
                continue
            if outcome.get("state") != "moved":
                raise RuntimeError(f"移动失败 {current_source_path}")
        except Exception as exc:
            failed_operation = {
                "source": operation.get("source_relative_path") or operation["source_path"],
                "current_source": _relative_to_source_root(event, current_source_path),
                "error": str(exc),
            }
            remaining_sources.append(failed_operation["current_source"])
            remaining_sources.extend(
                item.get("source_relative_path") or _relative_to_source_root(event, item["source_path"])
                for item in planned[index + 1:]
            )
            break
        successful.append(operation)

    unorganized = _move_sources_to_unorganized(event, remaining_sources)
    has_problem = bool(
        failed_operation
        or retained_sources
        or coverage.get("missing_items")
        or coverage.get("unexpected_sources")
        or coverage.get("rejected")
        or cleanup["failed"]
        or unorganized["failed"]
    )
    if not successful:
        state = "failed"
    elif has_problem:
        state = "partial"
    else:
        state = "completed"

    rename_plan["planned_operations"] = planned
    rename_plan["operations"] = successful
    rename_plan["execution"] = {
        "state": state,
        "cleanup": cleanup,
        "unorganized": unorganized,
        "failed_operation": failed_operation,
        "retained_sources": retained_sources,
    }
    if (
        state == "completed"
        and event.final_path != rename_plan["target_root"]
    ):
        _cleanup_source_directory(storage, event.final_path)
    rename_plan["media_metadata"] = enrich_media_metadata_with_rename_plan(
        rename_plan["media_metadata_source"],
        rename_plan,
    )
    rename_plan.pop("media_metadata_source", None)
    return rename_plan


def _format_confirmed_execution_message(event, rename_plan):
    execution = rename_plan.get("execution") or {}
    state = execution.get("state") or "completed"
    labels = {"completed": "完成", "partial": "部分完成", "failed": "失败"}
    successful = rename_plan.get("operations") or []
    coverage = rename_plan.get("mapping_coverage") or {}
    unorganized = execution.get("unorganized") or {}
    cleanup = execution.get("cleanup") or {}
    download_cleanup = (
        (event.metadata or {}).get("download_cleanup")
        if isinstance(event.metadata, dict)
        else {}
    ) or {}
    cleaned_count = int(download_cleanup.get("count") or 0) + len(cleanup.get("deleted") or [])
    lines = [
        f"{'✅' if state == 'completed' else '⚠️'} 自动整理{labels.get(state, state)}",
        f"正式目录：{len(successful)}",
        f"未整理：{len(unorganized.get('moved') or [])}",
        f"清理：{cleaned_count}",
    ]

    def append_bounded(label, values, limit=5):
        values = [
            (str(value)[:237] + "…") if len(str(value)) > 238 else str(value)
            for value in values
            if str(value or "").strip()
        ]
        if not values:
            return
        shown = values[:limit]
        suffix = f"（另有 {len(values) - limit} 项）" if len(values) > limit else ""
        lines.append(f"{label}：" + "、".join(shown) + suffix)

    append_bounded(
        "正式文件",
        [
            f"{str(item.get('target_dir') or '').rstrip('/')}/{item.get('rename_to')}"
            for item in successful
        ],
    )
    missing = coverage.get("missing_items") or []
    if missing:
        markers = [
            f"S{int(item['season_number']):02d}E{int(item['episode_number']):02d}"
            for item in missing
        ]
        lines.append("计划缺失：" + "、".join(markers))
    failure = execution.get("failed_operation")
    if failure:
        lines.append(f"失败文件：{failure.get('source')}")
        lines.append(f"失败原因：{failure.get('error')}")
    retained = execution.get("retained_sources") or []
    append_bounded("源文件保留", [item.get("source") for item in retained])
    cleanup_failures = list(download_cleanup.get("failed") or []) + list(cleanup.get("failed") or [])
    append_bounded(
        "清理失败",
        [item.get("source") if isinstance(item, dict) else item for item in cleanup_failures],
    )
    if unorganized.get("failed"):
        lines.append(f"未整理失败：{len(unorganized['failed'])}")
    if unorganized.get("target_dir"):
        lines.append(f"未整理目录：`{unorganized['target_dir']}`")
    append_bounded("未整理文件", unorganized.get("moved") or [])
    if successful:
        lines.append(f"保存目录：`{rename_plan['target_root']}`")
    elif unorganized.get("target_dir"):
        lines.append(f"保存目录：`{unorganized['target_dir']}`")
    message = "\n".join(lines)
    if len(message) > 3900:
        message = message[:3875].rstrip() + "\n…内容已截断"
    return message


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
    tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
    file_tree = collect_storage_file_tree(
        storage,
        event.final_path,
        include_non_video=True,
    )
    if not [item for item in file_tree if _is_video_node(item)]:
        init.logger.warn(
            f"确认方案整理跳过：目录中未找到视频文件 {event.final_path}"
        )
        return None

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
    minimum_video_size = _minimum_video_size_bytes()
    coverage = map_confirmed_files(
        media_metadata,
        file_tree,
        minimum_video_size=minimum_video_size,
    )
    if (
        coverage["missing_items"]
        and coverage["unexpected_sources"]
        and _has_ai_episode_inference_config()
    ):
        context.update(
            unresolved_mapping_context(media_metadata, file_tree, coverage)
        )
        ai_plan = infer_tvdb_episode_plan_with_ai(context) or {}
        coverage = map_confirmed_files(
            media_metadata,
            file_tree,
            ai_plan.get("episode_map") or [],
            minimum_video_size=minimum_video_size,
        )
    rename_plan = build_confirmed_rename_plan(
        final_path=event.final_path,
        selected_path=event.selected_path,
        metadata=metadata,
        media_metadata=media_metadata,
        ai_plan={"episode_map": coverage["mappings"]},
        file_tree=file_tree,
        mapping_coverage=coverage,
    )
    if not rename_plan:
        chinese_title, english_title = series_titles(media_metadata)
        series_name = english_title or chinese_title
        rename_plan = {
            "target_root": (
                f"{str(event.selected_path).rstrip('/')}/"
                f"{series_folder_name(media_metadata)}"
            ),
            "series_name": series_name,
            "operations": [],
            "unmatched_sources": list(coverage.get("unexpected_sources") or []),
            "mapping_coverage": coverage,
            "warnings": [],
        }

    rename_plan["media_metadata_source"] = media_metadata
    return _execute_confirmed_rename_plan(event, rename_plan, file_tree)


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
        return PostDownloadResult(
            True,
            final_path=event.final_path,
            message=f"⚠️ {exc}\n文件保持原位，请重新确认下载方案。",
            should_stop=True,
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

    if rename_plan.get("execution"):
        message = _format_confirmed_execution_message(event, rename_plan)
    else:
        message = (
            f"✅ TVDB 自动整理完成：`{rename_plan['series_name'] or rename_plan['target_root'].split('/')[-1]}`\n"
            f"文件数：{len(rename_plan['operations'])} 个文件\n\n"
            f"保存目录：`{rename_plan['target_root']}`"
        )
        if rename_plan.get("tvdb_series_id"):
            message += f"\nTVDB：`{rename_plan['tvdb_series_id']}`"
        if rename_plan.get("warnings"):
            message += f"\n提示：{'; '.join(rename_plan['warnings'][:2])}"
    result_metadata = event.metadata
    if rename_plan.get("media_metadata"):
        result_metadata = attach_media_metadata(
            event.metadata,
            rename_plan["media_metadata"],
        )
    execution = rename_plan.get("execution") or {}
    unorganized = execution.get("unorganized") or {}
    final_path = (
        rename_plan["target_root"]
        if rename_plan.get("operations")
        else unorganized.get("target_dir") or event.final_path
    )
    return PostDownloadResult(
        True,
        final_path=final_path,
        message=message,
        should_stop=True,
        metadata=result_metadata,
    )


def _attempt_media_auto_rename(event: DownloadCompletedEvent, naming_metadata):
    if not naming_metadata:
        return None

    storage = _storage(event)
    file_list = storage.get_files_from_dir(event.final_path)
    if not file_list:
        init.logger.warn(f"自动整理跳过：目录中未找到视频文件 {event.final_path}")
        return None
    if len(file_list) != 1:
        init.logger.warn(
            f"自动整理跳过：通用重命名仅支持单视频目录 "
            f"path={event.final_path} video_count={len(file_list)}"
        )
        return None

    original_file_name = file_list[0]
    release_title = naming_metadata.get("release_title") or event.resource_name
    plan = build_media_naming_plan(naming_metadata, release_title, original_file_name)
    if not plan:
        init.logger.warn(f"自动整理跳过：元数据不足 {naming_metadata}")
        return None

    target_path = f"{event.selected_path}/{plan.target_relative_dir}"
    if not storage.create_dir_recursive(target_path):
        raise RuntimeError(f"自动整理失败：无法创建目标目录 {target_path}")

    original_file_path = f"{event.final_path}/{original_file_name}"
    renamed_file_path = f"{event.final_path}/{plan.file_name}"
    if original_file_name != plan.file_name:
        if storage.rename(original_file_path, plan.file_name) is not True:
            raise RuntimeError(f"自动整理失败：重命名失败 {original_file_path}")

    if storage.move_file(renamed_file_path, target_path) is not True:
        raise RuntimeError(f"自动整理失败：移动失败 {renamed_file_path}")
    if event.final_path != target_path:
        _cleanup_source_directory(storage, event.final_path)

    return target_path, plan


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
    filename_metadata = _filename_metadata_from_resource(event.resource_name)
    naming_auto_metadata = _standalone_contract_naming_metadata(event) or event.naming_metadata or (
        filename_metadata if not event.metadata and not event.naming_metadata else None
    )
    result = _attempt_media_auto_rename(event, naming_auto_metadata)
    if not result:
        return PostDownloadResult(False, final_path=event.final_path)
    target_path, plan = result
    message = f"✅ 自动整理完成：`{plan.file_name}`\n\n保存目录：`{target_path}`"
    return PostDownloadResult(
        True,
        final_path=target_path,
        message=message,
        should_stop=True,
        metadata=event.metadata,
    )


def register_module(registry):
    registry.add_config_sections(["media", "metadata.tvdb", "ai"])
    registry.add_post_download_processor(process_tvdb_episode, priority=100, name="renaming.tvdb_episode")
    registry.add_post_download_processor(process_generic_media, priority=110, name="renaming.generic_media")

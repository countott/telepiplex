# -*- coding: utf-8 -*-

"""Renaming pipeline: ordinary naming first, canonical series patch second."""

from __future__ import annotations

from pathlib import Path
import re

from telepiplex_plugin_sdk import FeatureError
from .context import runtime_context
from telepiplex_plugin_sdk.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
)
from .models import DownloadCompletedEvent, PostDownloadResult
from .ai import (
    explain_unresolved_episode_files_with_ai,
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
    enrich_media_metadata_with_rename_plan,
)
from .subtitles import (
    SUBTITLE_EXTENSIONS,
    build_movie_subtitle_plan,
    collect_subtitle_evidence,
)
from .file_executor import (
    cleanup_source_directories,
    execute_file_resolutions,
    prefetch_file_info,
)
from .file_facts import build_file_facts, parse_file_evidence
from .file_plan import normalize_storage_path, plan_file_resolutions


def _storage(event: DownloadCompletedEvent):
    storage = event.storage
    if storage is None:
        raise RuntimeError("rename processor requires a storage provider")
    return storage


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
            elif Path(name).suffix.lower() in (
                VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS
            ):
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


def process_file_first_media(
    event: DownloadCompletedEvent,
    *,
    operations: list[dict],
    work_identity: dict,
) -> dict:
    """Plan and execute one independent resolution for every scanned file."""

    if event.snapshot_complete is False:
        raise FeatureError(
            "inventory_tree_incomplete",
            "file-first rename requires a complete tree snapshot",
        )
    storage = _storage(event)
    file_tree = _event_file_tree(event)
    root_path = event.download_root or event.final_path
    missing_paths = []
    for node in file_tree:
        if not node.get("is_dir") and not (
            node.get("source_id") or node.get("file_id") or node.get("fid")
        ):
            missing_paths.append(
                str(node.get("path") or "").strip() or (
                    f"{str(root_path).rstrip('/')}/"
                    f"{str(node.get('relative_path') or node.get('name') or '').strip('/')}"
                )
            )
    missing_info = prefetch_file_info(storage, missing_paths)
    enriched_tree = []
    for node in file_tree:
        enriched = dict(node)
        if not enriched.get("is_dir") and not (
            enriched.get("source_id")
            or enriched.get("file_id")
            or enriched.get("fid")
        ):
            absolute_path = str(enriched.get("path") or "").strip() or (
                f"{str(root_path).rstrip('/')}/"
                f"{str(enriched.get('relative_path') or enriched.get('name') or '').strip('/')}"
            )
            info = missing_info.get(normalize_storage_path(absolute_path))
            if isinstance(info, dict):
                enriched["file_id"] = str(
                    info.get("file_id") or info.get("fid") or ""
                )
                enriched["sha1"] = str(
                    info.get("sha1") or info.get("sha") or ""
                )
        enriched_tree.append(enriched)
    file_tree = enriched_tree
    facts = build_file_facts(
        file_tree,
        root_path=root_path,
        provider=event.provider,
        snapshot_id=str(event.snapshot_id or "") or "completed-download",
    )
    evidence = {
        fact.source_id: parse_file_evidence(fact)
        for fact in facts
    }
    facts_by_path = {
        normalize_storage_path(fact.absolute_path): fact
        for fact in facts
    }
    targets = {}
    identities = {}
    operation_by_source = {}
    existing_targets = {}
    prepared_operations = []
    for operation in operations or []:
        source_path = normalize_storage_path(operation.get("source_path"))
        fact = facts_by_path.get(source_path)
        if fact is None:
            continue
        target_path = normalize_storage_path(
            operation.get("final_path")
            or (
                f"{str(operation.get('target_dir') or '').rstrip('/')}/"
                f"{operation.get('rename_to') or ''}"
            )
        )
        if not target_path:
            continue
        operation["source_id"] = fact.source_id
        operation["final_path"] = target_path
        targets[fact.source_id] = target_path
        identities[fact.source_id] = dict(work_identity or {})
        operation_by_source[fact.source_id] = operation
        prepared_operations.append((target_path, operation))
    target_info_by_path = prefetch_file_info(
        storage,
        [target_path for target_path, _operation in prepared_operations],
    )
    for target_path, _operation in prepared_operations:
        target_info = target_info_by_path.get(target_path)
        if isinstance(target_info, dict):
            existing_targets[target_path] = target_info

    resolutions = plan_file_resolutions(
        facts,
        evidence,
        targets,
        identities,
        existing_targets=existing_targets,
    )
    execution = execute_file_resolutions(
        storage,
        resolutions,
        selected_root=event.download_root or event.final_path,
        journal=getattr(storage, "journal", None),
        move_batch_size=int(
            (runtime_context.config or {}).get("storage_move_batch_size") or 32
        ),
    )
    source_root = event.download_root or event.final_path
    protected_category_root = normalize_storage_path(event.selected_path)
    cleanup = cleanup_source_directories(
        storage,
        resolutions,
        selected_root=source_root,
        include_selected_root=True,
        protected_roots=tuple(filter(None, (protected_category_root,))),
    )
    outcome_by_source = {
        outcome.source_id: outcome
        for outcome in execution.outcomes
    }
    successful_operations = [
        operation
        for source_id, operation in operation_by_source.items()
        if outcome_by_source.get(source_id)
        and outcome_by_source[source_id].state in {"organized", "no_op"}
    ]
    media_resolutions = [
        resolution
        for resolution in resolutions
        if evidence[resolution.source_id].content_role != "unknown"
        or resolution.source_id in operation_by_source
    ]
    result = {
        "pipeline_version": "file-first-v1",
        "resolutions": resolutions,
        "outcomes": list(execution.outcomes),
        "successful_operations": successful_operations,
        "media_files_total": len(media_resolutions),
        "organized_files": execution.organized_files,
        "canonical_no_ops": execution.canonical_no_ops,
        "kept_unresolved": sum(
            resolution.action == "keep_original"
            for resolution in media_resolutions
        ),
        "target_conflicts": sum(
            "target_conflict" in resolution.reason_codes
            or "planned_target_collision" in resolution.reason_codes
            for resolution in media_resolutions
        ),
        "failed_files": execution.failed_files,
        "cleanup": cleanup.to_dict(),
        "verified_work_groups": int(bool(
            execution.organized_files or execution.canonical_no_ops
        )),
    }
    verified_files = result["organized_files"] + result["canonical_no_ops"]
    if (
        verified_files > 0
        and result["kept_unresolved"] > 0
        and result["target_conflicts"] == 0
        and result["failed_files"] == 0
    ):
        result["completion_kind"] = "partial_completed"
    return result


def _partial_completion_explanation(
    file_first: dict,
    media_metadata: dict,
) -> dict | None:
    if file_first.get("completion_kind") != "partial_completed":
        return None
    unresolved_files = []
    for resolution in file_first.get("resolutions") or []:
        if resolution.action != "keep_original":
            continue
        unresolved_files.append({
            "source_id": resolution.source_id,
            "source_path": resolution.source_path,
            "source_name": Path(resolution.source_path).name,
            "reason_codes": list(resolution.reason_codes),
        })
    file_first["unresolved_files"] = unresolved_files
    context = {
        "confirmed_work": dict(media_metadata.get("identity") or {}),
        "placement": dict(media_metadata.get("placement") or {}),
        "inventory_reconciliation": dict(
            ((media_metadata.get("evidence") or {}).get(
                "inventory_reconciliation"
            ) or {})
        ),
        "unresolved_files": unresolved_files,
    }
    explanation = None
    if unresolved_files and _has_ai_episode_inference_config():
        try:
            explanation = explain_unresolved_episode_files_with_ai(context)
        except Exception as exc:
            runtime_context.logger.warning(
                f"AI分集歧义解释失败 error={type(exc).__name__}"
            )
    if not isinstance(explanation, dict):
        explanation = {
            "source": "rules",
            "summary": "这些文件坐标无法与已确认的官方分集唯一对应，已保持原位。",
            "possible_causes": [
                "资源可能采用 DVD、absolute、alternate 或平台自定义分集顺序。",
            ],
            "user_checks": [
                "核对资源发行说明和元数据平台的可选分集顺序后再手动处理。",
            ],
        }
    file_first["ambiguity_explanation"] = explanation
    return explanation


def _public_file_results(file_first: dict) -> dict:
    successful_files = []
    files = []
    warnings = []
    for outcome in file_first.get("outcomes") or []:
        item = {
            "source_id": outcome.source_id,
            "state": outcome.state,
            "source_path": outcome.source_path,
            "target_path": outcome.target_path,
            "observed_path": outcome.observed_path,
            "reason_codes": list(outcome.reason_codes),
        }
        files.append(item)
        if outcome.state not in {"organized", "no_op"}:
            warnings.append({
                "source_id": outcome.source_id,
                "state": outcome.state,
                "observed_path": outcome.observed_path,
                "reason_codes": list(outcome.reason_codes),
            })
            continue
        successful_files.append({
            "source_id": outcome.source_id,
            "state": outcome.state,
            "final_path": outcome.observed_path,
        })
    result = {
        "pipeline_version": "file-first-v1",
        "media_files_total": int(
            file_first.get("media_files_total") or 0
        ),
        "organized_files": int(file_first.get("organized_files") or 0),
        "canonical_no_ops": int(
            file_first.get("canonical_no_ops") or 0
        ),
        "kept_unresolved": int(file_first.get("kept_unresolved") or 0),
        "target_conflicts": int(file_first.get("target_conflicts") or 0),
        "failed_files": int(file_first.get("failed_files") or 0),
        "verified_work_groups": int(
            file_first.get("verified_work_groups") or 0
        ),
        "successful_files": successful_files,
        "files": files,
        "warnings": warnings,
        "cleanup": dict(file_first.get("cleanup") or {
            "candidate_directories": 0,
            "deleted_directories": 0,
            "retained_directories": 0,
            "failed_directories": 0,
            "complete": True,
            "deleted_paths": [],
            "failures": [],
        }),
    }
    if file_first.get("completion_kind"):
        result["completion_kind"] = file_first["completion_kind"]
        result["unresolved_files"] = list(
            file_first.get("unresolved_files") or []
        )
        result["ambiguity_explanation"] = dict(
            file_first.get("ambiguity_explanation") or {}
        )
    return result


def _selection_key(value):
    name = Path(str(value or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix in VIDEO_EXTENSIONS else name
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", " ", stem.casefold()).strip()


def _video_nodes(file_tree):
    return [
        item for item in file_tree
        if not item.get("is_dir")
        and Path(str(
            item.get("relative_path") or item.get("name") or ""
        )).suffix.lower() in VIDEO_EXTENSIONS
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
    bounded_season = None
    if (
        not allowed
        and "warning:episode_inventory_unavailable"
        in (media_metadata.get("warnings") or ())
        and str((media_metadata.get("retrieval") or {}).get("scope") or "")
        == "season"
    ):
        decision = ((media_metadata.get("evidence") or {}).get("decision") or {})
        try:
            bounded_season = int(decision.get("season_number"))
        except (TypeError, ValueError):
            return None
        if bounded_season < 1:
            return None
    mapped = {}
    video_nodes = _video_nodes(file_tree)
    nodes_by_path = {
        str(node.get("relative_path") or "").strip("/"): node
        for node in video_nodes
    }
    for item in media_metadata.get("items") or []:
        hint = str(item.get("source_hint") or "").strip("/")
        if not hint:
            continue
        marker = (int(item["season_number"]), int(item["episode_number"]))
        node = nodes_by_path.get(hint)
        if marker in allowed and node is not None and marker not in mapped:
            mapped[marker] = node
    for node in video_nodes:
        marker = parse_episode_marker(node.get("relative_path") or node.get("name"))
        if (
            marker is not None
            and (
                marker in allowed
                or (
                    bounded_season is not None
                    and marker[0] == bounded_season
                    and marker[1] > 0
                )
            )
            and marker not in mapped
        ):
            mapped[marker] = node
    if bounded_season is not None and video_nodes:
        allowed = set(mapped)
    if video_nodes and (not allowed or set(mapped) != allowed):
        return None
    subtitle_evidence = collect_subtitle_evidence(file_tree)
    subtitle_map = []
    subtitle_mapping_incomplete = False
    for item in subtitle_evidence:
        marker = item.get("episode_key")
        if marker is None:
            subtitle_mapping_incomplete = True
            continue
        if allowed and marker not in allowed:
            subtitle_mapping_incomplete = True
            continue
        if bounded_season is not None and marker[0] != bounded_season:
            subtitle_mapping_incomplete = True
            continue
        subtitle_map.append({
            "source_file": item["relative_path"],
            "season_number": marker[0],
            "episode_number": marker[1],
        })
    if not video_nodes and not subtitle_evidence:
        return None
    return {
        "episode_map": [{
            "source_file": node["relative_path"],
            "season_number": season,
            "episode_number": episode,
            "content_role": media_metadata.get("identity", {}).get("content_kind"),
        } for (season, episode), node in sorted(mapped.items())],
        "subtitle_map": subtitle_map,
        "subtitle_mapping_incomplete": subtitle_mapping_incomplete,
        "warnings": [],
    }


def _locked_ai_context(media_metadata: dict) -> dict:
    identity = media_metadata.get("identity") or {}
    relation = media_metadata.get("relation") or {}
    target = relation.get("target_series") if isinstance(relation.get("target_series"), dict) else {}
    target_ids = target.get("external_ids") if isinstance(target.get("external_ids"), dict) else {}
    identity_ids = identity.get("external_ids") if isinstance(identity.get("external_ids"), dict) else {}
    series_id = str(target_ids.get("tvdb") or identity_ids.get("tvdb") or "").strip()
    locked = []
    episodes = []
    for item in media_metadata.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            season = int(item["season_number"])
            episode = int(item["episode_number"])
        except (KeyError, TypeError, ValueError):
            continue
        locked.append([season, episode])
        episodes.append({
            "tvdb_series_id": series_id,
            "tvdb_episode_id": str(item.get("tvdb_episode_id") or item.get("item_id") or ""),
            "season_number": season,
            "episode_number": episode,
        })
    if not locked:
        placement = media_metadata.get("placement") or {}
        try:
            season = int(placement["season_number"])
            episode = int(placement["episode_number"])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            locked.append([season, episode])
            episodes.append({
                "tvdb_series_id": series_id,
                "tvdb_episode_id": str(placement.get("tvdb_episode_id") or ""),
                "season_number": season,
                "episode_number": episode,
            })
    canonical_title = str(
        target.get("english_title")
        or identity.get("english_title")
        or ""
    ).strip()
    return {
        "locked_identity": {
            "tvdb_series_id": series_id,
            "canonical_latin_title": canonical_title,
            "content_kind": identity.get("content_kind") or "",
        },
        "locked_episode_keys": locked,
        "tvdb_candidates": ([{
            "tvdb_series_id": series_id,
            "name": canonical_title,
            "year": target.get("year") or identity.get("year") or "",
        }] if series_id else []),
        "tvdb_episodes": episodes,
    }


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

    deterministic_plan = _deterministic_episode_plan(
        media_metadata, file_tree
    )
    ai_plan = deterministic_plan
    ai_was_used = False
    if (
        ai_plan is None
        or ai_plan.get("subtitle_mapping_incomplete") is True
    ) and _has_ai_episode_inference_config():
        context = {
            "metadata": metadata,
            "confirmed_media_metadata": media_metadata,
            "release_title": metadata.get("release_title") or event.resource_name,
            "resource_name": event.resource_name,
            "download_path": event.final_path,
            "file_tree": file_tree,
            **_locked_ai_context(media_metadata),
        }
        inferred_plan = infer_tvdb_episode_plan_with_ai(context)
        if deterministic_plan is not None:
            ai_plan = dict(inferred_plan or deterministic_plan)
            ai_plan["episode_map"] = list(
                deterministic_plan.get("episode_map") or []
            )
            ai_plan["subtitle_map"] = list(
                (inferred_plan or {}).get("subtitle_map") or []
            )
        else:
            ai_plan = inferred_plan
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
        runtime_context.logger.warning(
            f"确认方案整理跳过：AI文件映射未通过锁定校验 path={event.final_path}"
        )
        return None
    planned_operations = list(rename_plan.get("operations") or [])
    file_first = process_file_first_media(
        event,
        operations=planned_operations,
        work_identity={
            "metadata_id": media_metadata.get("metadata_id") or "",
            **dict(media_metadata.get("identity") or {}),
        },
    )
    rename_plan["planned_operations"] = planned_operations
    rename_plan["operations"] = file_first["successful_operations"]
    rename_plan["file_first"] = file_first
    _partial_completion_explanation(file_first, media_metadata)
    rename_plan["kept_sources"] = sorted(set(
        (rename_plan.get("kept_sources") or [])
        + (rename_plan.get("unmatched_sources") or [])
        + (rename_plan.get("discard_sources") or [])
    ))
    rename_plan["discard_sources"] = []
    rename_plan["cleanup_complete"] = bool(
        file_first["failed_files"] == 0
        and (file_first.get("cleanup") or {}).get("complete") is True
    )
    rename_plan["media_metadata"] = enrich_media_metadata_with_rename_plan(
        media_metadata,
        rename_plan,
    )
    return rename_plan


def _attempt_tvdb_ai_episode_rename(event: DownloadCompletedEvent, metadata):
    _media_metadata, contract_present = _media_metadata_state(event)
    confirmed_series = _confirmed_series_metadata(event)
    if contract_present and confirmed_series:
        return _attempt_confirmed_series_rename(
            event,
            metadata,
            confirmed_series,
        )
    return None


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
    rename_plan = _attempt_tvdb_ai_episode_rename(event, metadata)
    if not rename_plan:
        if confirmed_series:
            return PostDownloadResult(
                True,
                final_path=event.final_path,
                message=(
                    "⚠️ 下载后文件映射未能确认；相关文件保持原位，"
                    "未移动整个目录。"
                ),
                should_stop=True,
                metadata=event.metadata,
            )
        return PostDownloadResult(False, final_path=event.final_path)

    file_first = rename_plan.get("file_first") or {}
    successful = int(file_first.get("organized_files") or 0) + int(
        file_first.get("canonical_no_ops") or 0
    )
    conflicts = int(file_first.get("target_conflicts") or 0)
    failed = int(file_first.get("failed_files") or 0)
    kept = int(file_first.get("kept_unresolved") or 0)
    cleanup = file_first.get("cleanup") or {}
    cleanup_failed = int(cleanup.get("failed_directories") or 0)
    cleanup_incomplete = cleanup.get("complete") is False
    final_path = rename_plan["target_root"] if successful else event.final_path
    prefix = (
        "📂"
        if not kept and not conflicts and not failed and not cleanup_incomplete
        else "⚠️"
    )
    message = (
        f"{prefix} 媒体整理结果：`{rename_plan['series_name'] or rename_plan['target_root'].split('/')[-1]}`\n"
        f"已整理 {file_first.get('organized_files', 0)}，"
        f"已规范 {file_first.get('canonical_no_ops', 0)}，"
        f"保留 {kept}，目标冲突 {conflicts}，失败 {failed}\n"
        f"源目录删除 {cleanup.get('deleted_directories', 0)}，"
        f"保留 {cleanup.get('retained_directories', 0)}，"
        f"清理失败 {cleanup_failed}\n\n"
        f"保存目录：`{final_path}`"
    )
    if rename_plan.get("tvdb_series_id"):
        message += f"\nTVDB：`{rename_plan['tvdb_series_id']}`"
    if rename_plan.get("warnings"):
        message += f"\n提示：{'; '.join(rename_plan['warnings'][:2])}"
    if file_first.get("completion_kind") == "partial_completed":
        unresolved_names = [
            item.get("source_name")
            for item in file_first.get("unresolved_files") or []
            if item.get("source_name")
        ]
        if unresolved_names:
            message += "\n待确认（保持原位）：" + "、".join(unresolved_names[:8])
            if len(unresolved_names) > 8:
                message += f" 等 {len(unresolved_names)} 个文件"
        explanation = file_first.get("ambiguity_explanation") or {}
        if explanation.get("summary"):
            message += f"\n歧义说明：{explanation['summary']}"
    result_metadata = event.metadata
    if rename_plan.get("media_metadata"):
        result_metadata = attach_media_metadata(
            event.metadata,
            rename_plan["media_metadata"],
        )
    return PostDownloadResult(
        True,
        final_path=final_path,
        message=message,
        should_stop=True,
        metadata=result_metadata,
        file_results=_public_file_results(file_first),
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
    subtitle_evidence = collect_subtitle_evidence(file_tree)
    if not main_video and not subtitle_evidence:
        runtime_context.logger.warning(
            f"自动整理跳过：目录中未找到媒体文件 {event.final_path}"
        )
        return None

    original_file_name = main_video["name"] if main_video else "placeholder.mkv"
    release_title = naming_metadata.get("release_title") or event.resource_name
    plan = build_media_naming_plan(naming_metadata, release_title, original_file_name)
    if not plan:
        runtime_context.logger.warn(f"自动整理跳过：元数据不足 {naming_metadata}")
        return None

    target_path = f"{event.selected_path}/{plan.target_relative_dir}"
    operations = []
    if main_video:
        original_file_path = _source_path(event, main_video)
        source_root = str(original_file_path).rsplit("/", 1)[0]
        operations.append({
            "media_kind": "video",
            "source_path": original_file_path,
            "rename_to": plan.file_name,
            "renamed_source_path": f"{source_root}/{plan.file_name}",
            "target_dir": target_path,
        })
    subtitle_plan = build_movie_subtitle_plan(
        final_path=event.final_path,
        target_dir=target_path,
        target_stem=Path(plan.file_name).stem,
        file_tree=file_tree,
    )
    operations.extend(subtitle_plan["operations"])
    file_first = process_file_first_media(
        event,
        operations=operations,
        work_identity=dict(naming_metadata or {}),
    )

    return (
        target_path,
        plan,
        file_first,
        selection_reason or "subtitle_only",
    )


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
    result = _attempt_media_auto_rename(event, naming_auto_metadata)
    if not result:
        return PostDownloadResult(False, final_path=event.final_path)
    target_path, plan, file_first, selection_reason = result
    successful = int(file_first.get("organized_files") or 0) + int(
        file_first.get("canonical_no_ops") or 0
    )
    conflicts = int(file_first.get("target_conflicts") or 0)
    failed = int(file_first.get("failed_files") or 0)
    kept = int(file_first.get("kept_unresolved") or 0)
    cleanup = file_first.get("cleanup") or {}
    cleanup_failed = int(cleanup.get("failed_directories") or 0)
    cleanup_incomplete = cleanup.get("complete") is False
    final_path = target_path if successful else event.final_path
    prefix = (
        "📂"
        if not kept and not conflicts and not failed and not cleanup_incomplete
        else "⚠️"
    )
    message = (
        f"{prefix} 电影整理结果：`{plan.file_name}`\n"
        f"主视频依据：{selection_reason}\n"
        f"已整理 {file_first.get('organized_files', 0)}，"
        f"已规范 {file_first.get('canonical_no_ops', 0)}，"
        f"保留 {kept}，目标冲突 {conflicts}，失败 {failed}\n"
        f"源目录删除 {cleanup.get('deleted_directories', 0)}，"
        f"保留 {cleanup.get('retained_directories', 0)}，"
        f"清理失败 {cleanup_failed}\n\n"
        f"保存目录：`{final_path}`"
    )
    return PostDownloadResult(
        True,
        final_path=final_path,
        message=message,
        should_stop=True,
        metadata=event.metadata,
        file_results=_public_file_results(file_first),
    )

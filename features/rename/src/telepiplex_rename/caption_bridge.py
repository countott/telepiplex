"""Public subtitle naming, bounded library inventory, and cloud placement."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import PurePosixPath
import time
import uuid

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2

from .inventory import VIDEO_EXTENSIONS
from .file_facts import build_file_facts, parse_file_evidence
from .media_metadata_v2 import naming_identity_from_v2, scope_allows_coordinate
from .media_naming import build_media_naming_plan, parse_episode_marker


def absolute_path(value):
    value = str(value or "")
    path = PurePosixPath(value)
    if not value.startswith("/") or ".." in path.parts or "\\" in value or any(ord(c) < 32 for c in value):
        raise FeatureError("invalid_request", "an absolute storage path is required")
    return str(path)


def name_subtitle(payload):
    language = str(payload.get("language") or "").lower()
    extension = str(payload.get("extension") or "").lower().lstrip(".")
    if language not in {"chi", "cht"} or extension not in {"srt", "ass", "vtt", "ssa"}:
        raise FeatureError("invalid_request", "subtitle language must be chi/cht and format must be external text subtitles")
    video = payload.get("video_path")
    if video:
        path = PurePosixPath(absolute_path(video))
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise FeatureError("invalid_request", "subtitle video path is not a supported media file")
        directory, stem = str(path.parent), path.stem
    else:
        metadata = validate_media_metadata_v2(payload.get("media_metadata"))
        if metadata is None:
            raise FeatureError("invalid_media_metadata_v2", "subtitle-only naming requires confirmed media_metadata v2")
        identity = naming_identity_from_v2(metadata)
        release = ""
        if identity["media_type"] == "series":
            scope = metadata["scope"]
            try:
                season = int(payload.get("season") or payload.get("season_number") or scope.get("season_number") or 0)
                episode = int(payload.get("episode") or payload.get("episode_number") or scope.get("episode_number") or 0)
            except (TypeError, ValueError) as exc:
                raise FeatureError("invalid_request", "subtitle episode coordinates are invalid") from exc
            if not scope_allows_coordinate(metadata, season, episode):
                raise FeatureError("invalid_request", "subtitle episode must be inside the confirmed scope")
            release = f"S{season:02d}E{episode:02d}"
        plan = build_media_naming_plan(identity, release, "subtitle." + extension)
        if plan is None:
            raise FeatureError("naming_unresolved", "confirmed metadata cannot produce a subtitle filename")
        directory = str(PurePosixPath(absolute_path(payload.get("target_dir"))) / plan.target_relative_dir)
        stem = PurePosixPath(plan.file_name).stem
    filename = f"{stem}.{language}.{extension}"
    return {"filename": filename, "target_dir": directory, "path": str(PurePosixPath(directory) / filename)}


async def capability(feature, request):
    method = str(request.get("method") or "")
    payload = request.get("payload") or {}
    if not isinstance(payload, dict):
        raise FeatureError("invalid_request", "subtitle request must be an object")
    if method == "scan_library":
        return await scan_library(feature, payload)
    if method not in {"name_subtitle", "place_subtitle"}:
        raise FeatureError("method_not_allowed", "media.rename method is not allowed")
    named = name_subtitle(payload)
    if method == "name_subtitle":
        return named
    if payload.get("video_path"):
        info = await feature._storage_value("get_file_info", absolute_path(payload["video_path"]))
        if not info or feature._inventory_item_is_dir(info):
            raise FeatureError("media_not_found", "subtitle source video no longer exists")
    upload = {key: payload[key] for key in ("transfer_id", "chunk_base64", "chunk_index", "chunk_count", "content_sha1", "size_bytes") if key in payload}
    if "content_base64" in payload:
        encoded = payload["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > 262144:
            raise FeatureError("invalid_request", "large subtitles must use chunk transfer")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise FeatureError("invalid_request", "invalid subtitle content") from exc
        sha1 = hashlib.sha1(content).hexdigest()
        upload = {"transfer_id": hashlib.sha256((named["path"] + sha1).encode()).hexdigest(), "chunk_base64": encoded, "chunk_index": 0, "chunk_count": 1, "content_sha1": sha1, "size_bytes": len(content)}
    upload["path"] = named["path"]
    response = await feature.host.call_capability("storage.provider", "upload_subtitle_chunk", upload, deadline=float(feature.config.get("storage_timeout") or 120))
    result = response.get("value") if isinstance(response, dict) else None
    if not isinstance(result, dict) or result.get("status") not in {"uploading", "placed", "exists"}:
        raise FeatureError("upload_unverified", "storage did not confirm subtitle placement")
    return {**named, **result}


async def scan_library(feature, payload):
    root = str(payload.get("root_path") or "")
    if not root:
        roots = []
        for item in feature.config.get("category_folder") or []:
            if isinstance(item, dict) and item.get("path"):
                roots.append({"name": str(item.get("name") or item.get("category_kind") or item["path"]), "path": item["path"]})
        if feature.config.get("unorganized_path"):
            roots.append({"name": "未整理", "path": feature.config["unorganized_path"]})
        return {"roots": roots}
    root = absolute_path(root)
    try:
        limit = max(1, min(200, int(payload.get("limit") or 100)))
    except (TypeError, ValueError) as exc:
        raise FeatureError("invalid_request", "inventory page size is invalid") from exc
    scans = getattr(feature, "_caption_scans", {})
    feature._caption_scans = scans
    for key in list(scans):
        if scans[key]["expires"] < time.monotonic():
            scans.pop(key)
    cursor = str(payload.get("cursor") or "")
    if cursor:
        try:
            token, raw_offset = cursor.split(":", 1)
            offset = int(raw_offset)
            cached = scans[token]
            if cached["root"] != root or not 0 <= offset <= len(cached["media"]):
                raise ValueError("cursor mismatch")
        except (ValueError, KeyError) as exc:
            raise FeatureError("invalid_cursor", "library scan cursor expired or belongs to another root") from exc
    else:
        info = await feature._storage_value("get_file_info", root)
        if not info or not feature._inventory_item_is_dir(info):
            raise FeatureError("media_not_found", "library root must be an existing directory")
        tree = await feature._inventory_file_tree(info, root, max_nodes=50000)
        main_paths = {fact.absolute_path for fact in build_file_facts(tree, root_path=root, provider="download", snapshot_id="") if fact.media_kind == "video" and parse_file_evidence(fact).content_role == "main"}
        media = []
        for item in tree:
            if item.get("is_dir") or item["path"] not in main_paths:
                continue
            marker = parse_episode_marker(item["relative_path"])
            media.append({key: item[key] for key in ("path", "file_id", "name", "relative_path", "size")})
            if marker:
                media[-1].update(season=marker[0], episode=marker[1])
        media.sort(key=lambda item: item["path"])
        token, offset = uuid.uuid4().hex, 0
        if len(scans) >= 8:
            scans.pop(min(scans, key=lambda key: scans[key]["expires"]))
        cached = {"root": root, "media": media, "expires": time.monotonic() + 1800}
        scans[token] = cached
    page = []
    page_bytes = 0
    for item in cached["media"][offset:offset + limit]:
        size = len(json.dumps(item, ensure_ascii=False).encode())
        if page and page_bytes + size > 128 * 1024:
            break
        page.append(item)
        page_bytes += size
    next_offset = offset + len(page)
    return {"root_path": root, "snapshot_complete": True, "media": page, "total": len(cached["media"]), "next_cursor": f"{token}:{next_offset}" if next_offset < len(cached["media"]) else ""}

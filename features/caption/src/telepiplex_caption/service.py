"""External subtitles for download handoff, library inventory and standalone queries."""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import re
import time
import uuid

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2

from .engine import CaptionEngine
from .interaction import metadata_choice_view
from .metadata import make_query, metadata_query, same_work, video_items
from .store import CaptionStore

logger = logging.getLogger("telepiplex.caption")
TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
SESSION_TTL = 1800
CHUNK_BYTES = 192 * 1024
_API_SETTINGS = {"assrt": "token", "opensubtitles": "api_key", "subdl": "api_key", "subsource": "api_key"}


def _owner(request):
    return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)


def _args(request):
    value = request.get("args") or []
    return " ".join(str(item) for item in value) if isinstance(value, list) else str(value).strip()


def _response(text, *, keyboard=None, session=None):
    action = {"kind": "send_message", "text": text}
    if keyboard:
        action["data"] = {"keyboard": keyboard}
    result = {"actions": [action]}
    if session:
        result["session"] = {"state": session}
    return result


def _path(value):
    value = str(value or "").strip()
    if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("请输入 115 内的绝对路径，例如 /字幕。")
    return str(PurePosixPath(value))


class CaptionFeature:
    def __init__(self, *, config, host, state_path, providers=None, engine=None):
        self.config = dict(config)
        self.host = host
        self.store = CaptionStore(Path(state_path) / "caption.db")
        self.engine = engine or CaptionEngine(self.config, providers=providers)
        self.runtime = None
        self.operations = {}
        self.sessions = {}
        self._auto_lock = asyncio.Lock()

    def bind_runtime(self, runtime):
        self.runtime = runtime

    async def validate_config(self, request):
        config = request.get("config") or request.get("payload") or request
        if not isinstance(config, dict):
            raise FeatureError("invalid_config", "caption config must be an object")
        if "output_path" in config:
            try:
                _path(config["output_path"])
            except ValueError as exc:
                raise FeatureError("invalid_config", str(exc)) from None
        return {"valid": True}

    async def capability(self, request):
        method = str(request.get("method") or "")
        payload = request.get("payload") or {}
        if method == "status":
            return {"enabled": self.config.get("enabled", True), "external_only": True,
                    "providers": [getattr(p, "name", type(p).__name__) for p in self.engine.providers]}
        if method != "prepare_download":
            raise FeatureError("method_not_allowed", "subtitle.caption method is not allowed")
        if not self.config.get("enabled", True) or not self.config.get("automatic", True):
            return {"status": "disabled", "added_count": 0, "results": [], "warnings": []}
        async with self._auto_lock:
            return await self.prepare_download(payload)

    async def prepare_download(self, payload):
        total_budget = float(self.config.get("automatic_timeout_seconds", 240))
        stop_at = time.monotonic() + total_budget
        metadata = payload.get("media_metadata") or (payload.get("metadata") or {}).get("media_metadata")
        metadata = validate_media_metadata_v2(metadata)
        if metadata is None:
            return {"status": "metadata_required", "added_count": 0, "results": [], "warnings": ["confirmed_metadata_required"]}
        context = payload.get("subtitle_context") or (payload.get("metadata") or {}).get("subtitle_context") or {}
        if not context.get("original_language"):
            try:
                resolved = await asyncio.wait_for(self._resolve(metadata_query(metadata)), min(60, total_budget * 0.25))
                if resolved.get("status") == "resolved" and same_work(metadata, resolved.get("media_metadata") or {}):
                    context = resolved.get("subtitle_context") or {}
            except (FeatureError, TimeoutError):
                context = {}
        items = video_items(payload.get("file_tree") or [])
        if not items:
            return {"status": "no_media", "added_count": 0, "results": [], "warnings": ["verified_video_tree_required"]}
        results = []
        try:
            async with asyncio.timeout(max(0, stop_at - time.monotonic())):
                for item in items:
                    remaining = stop_at - time.monotonic()
                    if remaining <= min(30, total_budget * 0.2):
                        return self._batch_result(results, warnings=["automatic_timeout"], incomplete=True)
                    # Leave time to hand an accepted subtitle to Rename.
                    retrieval_budget = min(float(self.config.get("retrieval_timeout_seconds", 180)),
                                           remaining - min(30, total_budget * 0.2))
                    results.append(await self._process(metadata, context, item=item, timeout_seconds=retrieval_budget))
        except TimeoutError:
            return self._batch_result(results, warnings=["automatic_timeout"], incomplete=True)
        return self._batch_result(results)

    async def command(self, request):
        if str(request.get("command") or "") == "caption_config":
            self.sessions[_owner(request)] = {"kind": "config", "stage": "choose", "expires": time.time() + SESSION_TTL}
            return _response("字幕设置：使用免费来源；个人免费 API Key 可在申请后补充。", keyboard=[
                [{"text": "字幕保存目录", "callback_data": "caption:config:output_path"}],
                [{"text": "ASSRT Token", "callback_data": "caption:config:assrt"}],
                [{"text": "OpenSubtitles API Key", "callback_data": "caption:config:opensubtitles"}],
                [{"text": "SubDL 免费 API Key", "callback_data": "caption:config:subdl"}],
                [{"text": "SubSource 免费 API Key", "callback_data": "caption:config:subsource"}],
                [{"text": "下载后补字幕：" + ("开启" if self.config.get("automatic", True) else "关闭"), "callback_data": "caption:config:automatic"}],
                [{"text": "退出", "callback_data": "caption:config:cancel"}],
            ], session="open")
        args = _args(request)
        if not args or args == "scan":
            return self._start(request, "menu", "")
        if args.startswith("scan") and args[4:5].isspace():
            try:
                root = _path(args[4:].strip())
            except ValueError as exc:
                return _response(str(exc))
            return self._start(request, "scan", root)
        try:
            query, source_url = self._query_input(args)
        except ValueError as exc:
            return _response(str(exc))
        return self._start(request, "query", query, source_url=source_url)

    def _query_input(self, value):
        value = str(value).strip()
        if not value or len(value) > 2000:
            raise ValueError("请输入片名和年份；剧集可加 S01E02。")
        source_url = ""
        if "--source" in value:
            match = re.fullmatch(r"(.+?)\s+--source\s+(https://\S+)", value)
            if not match or "--source" in match[1] or not any(
                    getattr(p, "supports_detail_url", lambda _: False)(match[2]) for p in self.engine.providers):
                raise ValueError("请使用 片名 年份 --source 字幕详情链接；支持 SubHD、字幕库、Subf2m、R3SUB 等已接入来源的正规详情页。")
            value, source_url = match[1].strip(), match[2]
        return value, source_url

    def _active_operation(self, request):
        return next((op for op in self.operations.values()
                     if (op["chat_id"], op["user_id"]) == _owner(request)
                     and op["state"] not in TERMINAL), None)

    def _operation_response(self, operation):
        return {"actions": [], "session": {"state": "close" if operation["state"] in TERMINAL else "open"},
                "operation": self._view(operation)}

    def _start(self, request, kind, value, *, source_url=""):
        if not self.config.get("enabled", True):
            return _response("字幕模块已停用，请先启用 caption。")
        active = self._active_operation(request)
        if active:
            return self._operation_response(active)
        self.sessions.pop(_owner(request), None)
        chat_id, user_id = _owner(request)
        text = {"menu": "正在读取媒体目录。", "scan": "正在扫描媒体文件。", "query": "正在确认作品。"}[kind]
        operation = {"operation_id": uuid.uuid4().hex, "chat_id": chat_id, "user_id": user_id,
                     "state": "running", "stage": "metadata" if kind == "query" else "inventory",
                     "status_text": text, "control": "cancel", "revision": 1, "details": {}, "kind": kind,
                     "input": value, "source_url": source_url, "cancel_event": asyncio.Event()}
        self.operations[operation["operation_id"]] = operation
        self.store.save(operation)
        self._spawn(operation, self._run(operation, kind, value))
        return self._operation_response(operation)

    def _menu_view(self, operation, page=0):
        roots = operation.get("menu_roots") or []
        last = max(0, (len(roots) - 1) // 8)
        page = min(max(0, page), last)
        token = operation["operation_id"][:12]
        def button(label, action):
            return {"text": label, "callback_data": f"caption:menu:{token}:{action}"}
        keyboard = [[button(str(root.get("name") or root["path"])[:80], f"root-{i}")]
                    for i, root in enumerate(roots) if page * 8 <= i < (page + 1) * 8]
        navigation = []
        if page:
            navigation.append(button("上一页", f"page-{page - 1}"))
        if page < last:
            navigation.append(button("下一页", f"page-{page + 1}"))
        if navigation:
            keyboard.append(navigation)
        keyboard.append([button("输入其他目录", "path"), button("单独查找字幕", "query")])
        text = "选择要补齐外挂字幕的目录：" if roots else "没有已配置媒体目录，可输入 115 目录或单独查找字幕。"
        if last:
            text += f"\n第 {page + 1}/{last + 1} 页"
        operation["session"] = {"kind": "menu", "page": page, "expires": time.time() + SESSION_TTL}
        return text, {"keyboard": keyboard}

    async def _load_menu(self, operation):
        response = await self.host.call_capability("media.rename", "scan_library", {}, deadline=30)
        self._check_cancel(operation)
        operation["menu_roots"] = [dict(root, path=_path("/" + str(root["path"]).strip("/"))) for root in response.get("roots") or []
                                   if isinstance(root, dict) and root.get("path")]
        text, details = self._menu_view(operation)
        await self._report(operation, state="awaiting_input", stage="inventory_root_selection", control="exit",
                           status_text=text, details=details)

    def _spawn(self, operation, awaitable):
        if self.runtime is None:
            awaitable.close()
            raise FeatureError("runtime_unavailable", "caption runtime is not bound")
        task_id = "caption:" + operation["operation_id"] + ":" + uuid.uuid4().hex[:8]
        operation["task"] = self.runtime.spawn(awaitable, task_id=task_id)

    async def _resolve(self, query, *, confirmation=None):
        method = "confirm_metadata" if confirmation else "resolve_metadata"
        payload = dict(confirmation) if confirmation else {"query": query}
        return await self.host.call_capability("media.search", method, payload, deadline=180)

    async def _run(self, operation, kind, value, *, resolved=None):
        try:
            # Command results register ownership in Host after dispatch returns.
            await self._confirm_ownership(operation)
            if kind == "menu":
                await self._load_menu(operation)
                return
            if kind == "scan":
                if (operation.get("inventory") or {}).get("confirmed"):
                    await self._run_scan_batch(operation, resolved=resolved)
                else:
                    await self._scan(operation, value)
                return
            resolved = resolved or await self._resolve(value)
            self._check_cancel(operation)
            if await self._await_resolution(operation, resolved):
                return
            if resolved.get("status") != "resolved":
                await self._report(operation, state="failed", stage="metadata_unresolved", control="",
                    status_text="未能确认作品或季集范围，请补充年份、作品链接或 S01E02。", details={"reason": resolved.get("reason_code", "metadata_unresolved")})
                return
            context = resolved.get("subtitle_context") or {}
            await self._report(operation, state="running", stage="caption", control="cancel", status_text="正在检索、下载并检查外挂字幕。", details={})
            result = await self._process(resolved["media_metadata"], context, operation=operation)
            await self._finish(operation, self._batch_result([result]))
        except asyncio.CancelledError:
            await self._cancelled(operation)
        except Exception as exc:
            logger.warning("字幕任务失败 (%s)", type(exc).__name__)
            await self._report(operation, state="failed", stage="failed", control="", status_text="字幕任务未完成；已写入的文件保留。",
                               details={"reason": str(getattr(exc, "code", type(exc).__name__))})

    async def _await_resolution(self, operation, resolved, *, item=None):
        self._check_cancel(operation)
        video_path = str((item or {}).get("path") or "")
        if resolved.get("status") == "confirmation_required" and resolved.get("candidates"):
            candidates = list(resolved["candidates"])[:5]
            operation["session"] = {"kind": "metadata", "resolved": resolved, "candidates": candidates,
                                    "expires": time.time() + SESSION_TTL}
            text, details = metadata_choice_view(candidates, operation["operation_id"], video_path=video_path)
            await self._report(operation, state="awaiting_input", stage="metadata_choice", control="exit",
                               status_text=text, details=details)
            return True
        if resolved.get("status") == "resolved" and not (resolved.get("subtitle_context") or {}).get("original_language"):
            operation["session"] = {"kind": "language", "resolved": resolved, "expires": time.time() + SESSION_TTL}
            text = "元数据没有原始语言。请选择原始语言，以应用正确的字幕优先级。"
            if video_path:
                text += "\n当前文件：" + video_path[:240]
            await self._report(operation, state="awaiting_input", stage="original_language", control="exit",
                status_text=text, details={"keyboard": [[{
                    "text": label, "callback_data": f"caption:language:{operation['operation_id'][:12]}:{code}"
                }] for label, code in (("英语", "en"), ("中文／粤语", "zh"), ("其他语言", "und-other"))]})
            return True
        operation["session"] = {}
        return False

    async def _scan(self, operation, root):
        cursor, items, seen_cursors = "", [], set()
        while True:
            self._check_cancel(operation)
            inventory = await self.host.call_capability("media.rename", "scan_library",
                {"root_path": root, "cursor": cursor, "limit": 100}, deadline=180)
            self._check_cancel(operation)
            if not inventory.get("snapshot_complete"):
                raise FeatureError("inventory_incomplete", "library scan was not complete")
            items.extend(inventory.get("media") or [])
            if len(items) > 50000:
                raise FeatureError("inventory_too_large", "library inventory exceeds 50000 media files")
            cursor = str(inventory.get("next_cursor") or "")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise FeatureError("inventory_incomplete", "library pagination did not advance")
            seen_cursors.add(cursor)
        # Read all pages before asking for confirmation; no subtitle retrieval or
        # storage writes occur until the user starts this fixed batch.
        operation["inventory"] = {"items": items, "results": [], "index": 0, "confirmed": False}
        operation["session"] = {"kind": "scan_confirmation", "expires": time.time() + SESSION_TTL}
        token = operation["operation_id"][:12]
        keyboard = []
        if items:
            keyboard.append([{"text": f"开始补字幕（{len(items)}）", "callback_data": f"caption:menu:{token}:confirm"}])
        keyboard.append([{"text": "返回目录", "callback_data": f"caption:menu:{token}:back"}])
        text = f"扫描完成：{root[:240]}\n发现 {len(items)} 个视频文件。"
        text += "\n确认后逐个查找外挂字幕，保存到对应视频旁。" if items else "\n没有可补字幕的视频文件。"
        await self._report(operation, state="awaiting_input", stage="inventory_confirmation", control="exit",
                           status_text=text, details={"total": len(items), "keyboard": keyboard})

    async def _run_scan_batch(self, operation, *, resolved=None):
        inventory = operation["inventory"]
        items, results = inventory["items"], inventory["results"]
        while inventory["index"] < len(items):
            self._check_cancel(operation)
            processed = inventory["index"]
            item = items[processed]
            await self._report(operation, state="running", stage="caption", control="cancel",
                status_text=f"正在补齐外挂字幕：{processed + 1}/{len(items)}\n{str(item.get('name') or '')[:240]}",
                details={"processed": processed, "total": len(items)})
            try:
                resolved = resolved or await self._resolve(self._query_for_file(item))
                self._check_cancel(operation)
                if await self._await_resolution(operation, resolved, item=item):
                    return
                if resolved.get("status") != "resolved":
                    results.append({"status": "metadata_required", "video_path": item.get("path"), "reason": resolved.get("reason_code") or "ambiguous_metadata", "placements": []})
                else:
                    results.append(await self._process(resolved["media_metadata"], resolved.get("subtitle_context") or {}, item=item, operation=operation))
            except FeatureError as exc:
                self._check_cancel(operation)
                results.append({"status": "failed", "video_path": item.get("path"), "reason": exc.code, "placements": []})
            inventory["index"] += 1
            resolved = None
        await self._finish(operation, self._batch_result(results))

    @staticmethod
    def _query_for_file(item):
        path = PurePosixPath(str(item.get("path") or item.get("name") or ""))
        stem = path.stem
        # Already organized TV filenames may contain only SxxExx. Parent folder
        # supplies the work title while the basename retains the episode.
        if re.fullmatch(r"(?i)s\d+e\d+(?:[ ._-].*)?", stem):
            parent = path.parent
            if re.fullmatch(r"(?i)(?:season|s)[ ._-]*\d+", parent.name):
                parent = parent.parent
            return f"{parent.name} {stem}"
        return stem.replace(".", " ").replace("_", " ")

    async def _process(self, metadata, context, *, item=None, operation=None, timeout_seconds=None):
        try:
            query = make_query(metadata, context, item)
        except ValueError as exc:
            return {"status": "metadata_required", "reason": str(exc), "video_path": (item or {}).get("path", ""), "placements": []}
        source_url = (operation or {}).get("source_url", "")
        options = {}
        if source_url:
            options["source_url"] = source_url
        if timeout_seconds is not None:
            options["timeout_seconds"] = timeout_seconds
        selections, report = await self.engine.retrieve(query, **options)
        if query.media_type == "series" and query.episode is None:
            report.setdefault("warnings", []).append("series_coverage_unverified")
        placements = []
        for selection in selections:
            self._check_cancel(operation)
            try:
                placed = await self._place(metadata, selection, operation=operation)
                placements.append({**selection.summary(), **placed})
            except FeatureError as exc:
                placements.append({**selection.summary(), "status": "failed", "reason": exc.code})
        success = sum(p.get("status") in {"placed", "exists"} for p in placements)
        status = "completed" if placements and success == len(placements) else "partial" if success else report["status"]
        if placements and not success:
            status = "failed"
        return {"status": status, "video_path": query.video_path, "placements": placements, "search": report}

    async def _place(self, metadata, selection, *, operation=None):
        content = selection.quality.normalized_text.encode("utf-8")
        if not content or len(content) > 8 * 1024 * 1024:
            raise FeatureError("subtitle_size_invalid", "subtitle file exceeds 8 MiB or is empty")
        sha1 = hashlib.sha1(content).hexdigest()
        query = selection.query
        fields = {"media_metadata": metadata, "video_path": query.video_path,
                  "target_dir": self.config.get("output_path", "/字幕"),
                  "language": selection.quality.language, "extension": selection.document.format,
                  "season": query.season, "episode": query.episode}
        transfer_identity = {key: value for key, value in fields.items() if key != "media_metadata"}
        transfer_identity.update(metadata_id=metadata.get("metadata_id"), content_sha1=sha1)
        if not query.video_path:
            # Metadata IDs identify the work, not its editable display titles
            # or placement category. Those fields determine standalone paths.
            transfer_identity["naming_metadata"] = metadata
        transfer_id = hashlib.sha256(json.dumps(transfer_identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        count = (len(content) + CHUNK_BYTES - 1) // CHUNK_BYTES
        response = {}
        index = 0
        while index < count:
            self._check_cancel(operation)
            response = await self.host.call_capability("media.rename", "place_subtitle", {
                **fields, "transfer_id": transfer_id, "chunk_index": index, "chunk_count": count,
                "content_sha1": sha1, "size_bytes": len(content),
                "chunk_base64": base64.b64encode(content[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]).decode("ascii"),
            }, deadline=150, idempotency_key=f"caption:{transfer_id}:{index}")
            if response.get("status") in {"placed", "exists"}:
                return response
            next_index = response.get("next_chunk_index")
            if (response.get("status") != "uploading" or type(next_index) is not int
                    or not index < next_index < count):
                raise FeatureError("upload_unverified", "subtitle transfer was not acknowledged")
            # A durable receiver may already have several chunks from an
            # interrupted attempt. Resume at its first missing chunk.
            index = next_index
        raise FeatureError("upload_unverified", "storage did not confirm subtitle placement")

    @staticmethod
    def _batch_result(results, *, warnings=None, incomplete=False):
        placements = [p for result in results for p in result.get("placements", [])]
        successes = sum(p.get("status") in {"placed", "exists"} for p in placements)
        attribution = []
        seen_sources = set()
        for placement in placements:
            provider = str(placement.get("provider") or "")
            if placement.get("status") not in {"placed", "exists"} or provider in seen_sources:
                continue
            seen_sources.add(provider)
            attribution.append({"provider": provider, "source_page": str(placement.get("source_page") or ""),
                                "text": "字幕服务由 assrt.net 提供" if provider == "assrt" else f"字幕来源：{provider}"})
        failed = sum(result.get("status") != "completed" for result in results)
        failures = {r.get("status") for r in results}
        status = "completed" if results and not failed and not incomplete else "partial" if successes else "no_match"
        if not successes:
            if "failed" in failures:
                status = "failed"
            elif "metadata_required" in failures:
                status = "metadata_required"
            elif "unavailable" in failures:
                status = "unavailable"
        return {"status": status,
                "added_count": sum(p.get("status") == "placed" for p in placements),
                "existing_count": sum(p.get("status") == "exists" for p in placements),
                "processed_count": len(results), "unmatched_count": failed, "results": results[:100],
                "results_truncated": len(results) > 100,
                "attribution": attribution[:5],
                "warnings": list(warnings or []), "incomplete": incomplete, "needs_rescan": incomplete}

    async def _finish(self, operation, result):
        written, existing = result["added_count"], result["existing_count"]
        text = f"外挂字幕处理完成：新增 {written}，已存在 {existing}，未完成 {result['unmatched_count']}。"
        if not written and not existing:
            text += "\n" + {"metadata_required": "部分作品身份、季集或原始语言尚未确认。",
                              "unavailable": "字幕来源暂时不可用。", "failed": "字幕下载或写入失败。"}.get(result["status"], "未找到通过匹配和内容检查的字幕。")
        provider_issues = {}
        issue_labels = {"auth_required": "待补充授权", "user_action_required": "需在来源网站完成操作",
            "unavailable": "暂时不可用", "timeout": "请求超时", "rate_limited": "访问额度受限",
            "access_denied": "来源拒绝访问", "invalid_response": "来源返回格式异常",
            "catalog_incomplete": "目录未完整读取", "unsafe_path": "归档路径无法安全读取",
            "invalid_config": "来源配置需要调整"}
        warnings = set()
        for row in result["results"]:
            search = row.get("search") or {}
            warnings.update(search.get("warnings") or [])
            if search.get("candidate_limit_reached"):
                warnings.add("candidate_limit_reached")
            for provider in search.get("providers") or []:
                if provider.get("status") in issue_labels:
                    provider_issues[provider.get("provider", "来源")] = provider
        explicit_sources = {p.name for p in self.engine.providers if operation.get("source_url") and
            getattr(p, "supports_detail_url", lambda _: False)(operation["source_url"])}
        visible_issues = sorted(provider_issues.values(), key=lambda p: (
            p.get("provider") not in explicit_sources, p.get("status") == "auth_required"))
        for provider in visible_issues[:4]:
            label = issue_labels[provider["status"]]
            text += f"\n{str(provider['provider'])}：{label}。"
        if len(provider_issues) > 4:
            text += f"\n另有 {len(provider_issues) - 4} 个来源受限，详细状态已保存在本次任务结果中。"
        if "series_coverage_unverified" in warnings:
            text += "\n仅保存能确认季集的字幕；未核验整季／全剧是否齐全。"
        if "candidate_limit_reached" in warnings:
            text += "\n已达到本次候选检查上限，结果是已检查候选中的最优项。"
        if "retrieval_budget_reached" in warnings:
            text += "\n已达到本次查找时限，保留已完成内容检查的最优字幕；其余候选未检查完。"
        paths = [p.get("path") for r in result["results"] for p in r.get("placements", []) if p.get("status") in {"placed", "exists"}]
        if paths:
            text += "\n" + "\n".join(str(path) for path in paths[:5])
        if paths:
            text += "\n已检查字幕内容与格式；未验证逐句音画同步。"
        for source in result.get("attribution") or []:
            text += "\n" + source["text"]
            if source["source_page"].startswith("https://"):
                text += "\n" + source["source_page"]
        # Keep the durable audit in the Feature store and a bounded Host summary.
        operation["result"] = result
        operation["session"] = {}
        operation.pop("inventory", None)
        final_state = "failed" if result["status"] in {"failed", "unavailable", "metadata_required"} else "completed"
        await self._report(operation, state=final_state, stage="completed" if final_state == "completed" else result["status"], control="", status_text=text,
            details={k: result[k] for k in ("status", "added_count", "existing_count", "processed_count", "unmatched_count", "warnings")})

    async def callback(self, request):
        payload = str(request.get("payload") or "")
        if payload.startswith("config:"):
            return self._config_callback(request, payload.split(":", 1)[1])
        if payload.startswith("menu:"):
            return self._menu_callback(request, payload)
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] not in {"choose", "language"}:
            raise FeatureError("invalid_callback", "unknown caption action")
        operation = next((op for op in self.operations.values() if op["operation_id"][:12] == parts[1] and (op["chat_id"], op["user_id"]) == _owner(request)), None)
        session = (operation or {}).get("session") or {}
        if not operation or operation["state"] != "awaiting_input":
            return _response("此选择已失效，请使用当前字幕面板。")
        if session.get("expires", 0) < time.time():
            return self._expired(operation)
        if parts[0] == "language" and session.get("kind") == "language":
            if parts[2] not in {"en", "zh", "und-other"}:
                raise FeatureError("invalid_selection", "invalid original language")
            resolved = deepcopy(session["resolved"])
            resolved.setdefault("subtitle_context", {})["original_language"] = parts[2]
        elif parts[0] == "choose" and session.get("kind") == "metadata":
            try:
                index = int(parts[2])
                if index < 0:
                    raise ValueError
                candidate = session["candidates"][index]
            except (ValueError, IndexError):
                raise FeatureError("invalid_selection", "invalid metadata candidate") from None
            # Return immediately; external metadata calls belong to background work.
            operation["session"] = {}
            self._change(operation, state="running", stage="metadata", control="cancel", status_text="正在确认作品。", details={})
            self._spawn(operation, self._confirm_choice(operation, session["resolved"]["resolution_id"], candidate["ref"]))
            return self._operation_response(operation)
        else:
            raise FeatureError("invalid_selection", "caption action does not match current step")
        operation["session"] = {}
        self._change(operation, state="running", stage="caption", control="cancel", status_text="正在查找字幕。", details={})
        self._spawn(operation, self._run(operation, operation["kind"], operation["input"], resolved=resolved))
        return self._operation_response(operation)

    def _menu_callback(self, request, payload):
        parts = payload.split(":")
        operation = self._active_operation(request)
        if len(parts) != 3 or not operation or parts[1] != operation["operation_id"][:12]:
            return _response("此选择已失效，请重新发送 /caption。")
        session = operation.get("session") or {}
        if operation["state"] != "awaiting_input":
            return self._operation_response(operation)
        if session.get("expires", 0) < time.time():
            return self._expired(operation)
        action, kind = parts[2], session.get("kind")
        if action.startswith("page-") and kind == "menu":
            try:
                page = int(action[5:])
            except ValueError:
                raise FeatureError("invalid_selection", "invalid caption directory page") from None
            text, details = self._menu_view(operation, page)
            self._change(operation, status_text=text, details=details)
        elif action in {"query", "path"} and kind == "menu":
            operation["session"] = {"kind": action + "_input", "expires": time.time() + SESSION_TTL}
            text = ("请输入片名和年份，剧集可加 S01E02。\n例如：星际穿越 2014\n无视频时保存到：" + str(self.config.get("output_path", "/字幕"))
                    if action == "query" else "请输入 115 中要扫描的目录，例如：真人电影 或 /真人电影。")
            self._change(operation, stage=action + "_input", status_text=text,
                details={"keyboard": [[{"text": "返回目录", "callback_data": f"caption:menu:{parts[1]}:back"}]]})
        elif action == "back" and kind in {"query_input", "path_input", "scan_confirmation"}:
            operation.pop("inventory", None)
            operation.update(kind="menu", input="", source_url="", session={})
            if "menu_roots" in operation:
                text, details = self._menu_view(operation)
                self._change(operation, stage="inventory_root_selection", status_text=text, details=details)
            else:
                self._change(operation, state="running", stage="inventory", control="cancel", status_text="正在读取媒体目录。", details={})
                self._spawn(operation, self._run(operation, "menu", ""))
        elif action.startswith("root-") and kind == "menu":
            try:
                index = int(action[5:])
                if index < 0:
                    raise ValueError
                root = operation["menu_roots"][index]["path"]
            except (ValueError, KeyError, IndexError):
                raise FeatureError("invalid_selection", "invalid caption directory") from None
            self._continue_operation(operation, "scan", root)
        elif action == "confirm" and kind == "scan_confirmation" and (operation.get("inventory") or {}).get("items"):
            operation["inventory"]["confirmed"] = True
            self._continue_operation(operation, "scan", operation["input"])
        else:
            raise FeatureError("invalid_selection", "caption action does not match current step")
        return self._operation_response(operation)

    def _continue_operation(self, operation, kind, value, *, source_url=""):
        operation.update(kind=kind, input=value, source_url=source_url, session={})
        self._change(operation, state="running", stage="metadata" if kind == "query" else "inventory",
                     control="cancel", status_text="正在确认作品。" if kind == "query" else "正在处理媒体目录。", details={})
        self._spawn(operation, self._run(operation, kind, value))

    def _expired(self, operation):
        operation["cancel_event"].set()
        operation.pop("inventory", None)
        operation["session"] = {}
        self._change(operation, state="cancelled", stage="expired", control="", details={},
                     status_text="此选择已过期，请重新发送 /caption；已写入的字幕保留。")
        return self._operation_response(operation)

    async def _confirm_choice(self, operation, resolution_id, candidate_ref):
        try:
            resolved = await self._resolve("", confirmation={"resolution_id": resolution_id, "candidate_ref": candidate_ref})
            await self._run(operation, operation["kind"], operation["input"], resolved=resolved)
        except asyncio.CancelledError:
            await self._cancelled(operation)
        except Exception as exc:
            await self._report(operation, state="failed", stage="failed", control="", status_text="作品确认失败，请重新发起。", details={"reason": getattr(exc, "code", type(exc).__name__)})

    def _config_callback(self, request, action):
        session = self.sessions.get(_owner(request)) or {}
        if session.get("kind") != "config" or session.get("expires", 0) < time.time():
            return _response("字幕配置已过期，请重新打开 /caption_config。", session="close")
        if action == "cancel":
            self.sessions.pop(_owner(request), None)
            return _response("已退出字幕配置。", session="close")
        if action == "automatic":
            self.sessions.pop(_owner(request), None)
            return {"actions": [], "session": {"state": "close"}, "config_patch": {"automatic": not self.config.get("automatic", True)}}
        if action != "output_path" and action not in _API_SETTINGS:
            raise FeatureError("invalid_selection", "invalid caption config option")
        session["stage"] = action
        return _response("请输入 115 内的绝对保存路径。" if action == "output_path" else "请发送授权值；发送 clear 清空。授权值不会在回复中显示。", session="open")

    async def message(self, request):
        operation = self._active_operation(request)
        if operation:
            session = operation.get("session") or {}
            if operation["state"] != "awaiting_input":
                return self._operation_response(operation)
            if session.get("expires", 0) < time.time():
                return self._expired(operation)
            value = str(request.get("text") or "").strip()
            kind = session.get("kind")
            if kind == "metadata" and value.isascii() and value.isdigit() and 1 <= int(value) <= len(session["candidates"]):
                return await self.callback({**request, "payload": f"choose:{operation['operation_id'][:12]}:{int(value) - 1}"})
            if kind in {"query_input", "path_input"}:
                try:
                    if kind == "query_input":
                        query, source_url = self._query_input(value)
                        self._continue_operation(operation, "query", query, source_url=source_url)
                    else:
                        path = _path(value if value.startswith("/") else "/" + value) if value else _path("")
                        self._continue_operation(operation, "scan", path)
                except ValueError as exc:
                    self._change(operation, status_text=str(exc))
                return self._operation_response(operation)
            # Keep the same selection panel and its owned controls for invalid input.
            return self._operation_response(operation)
        session = self.sessions.get(_owner(request)) or {}
        if session.get("kind") != "config" or session.get("expires", 0) < time.time():
            return _response("请发送 /caption 片名，或 /caption_config 配置字幕。", session="close")
        stage, value = session.get("stage"), str(request.get("text") or "").strip()
        if stage == "output_path":
            try:
                patch = {"output_path": _path(value)}
            except ValueError as exc:
                return _response(str(exc), session="open")
        elif stage in _API_SETTINGS:
            if not value or len(value) > 1024 or any(c.isspace() for c in value):
                return _response("请输入单行授权值，或发送 clear 清空。", session="open")
            patch = {"providers": {stage: {_API_SETTINGS[stage]: "" if value == "clear" else value}}}
        else:
            return _response("请先选择配置项目。", session="open")
        self.sessions.pop(_owner(request), None)
        return {"actions": [], "session": {"state": "close"}, "config_patch": patch}

    async def operation_control(self, request):
        operation = self.operations.get(str(request.get("operation_id") or ""))
        if not operation:
            raise FeatureError("operation_not_found", "caption task not found")
        if any(key in request for key in ("chat_id", "user_id")) and _owner(request) != (operation["chat_id"], operation["user_id"]):
            raise FeatureError("ownership_rejected", "caption operation belongs to another user")
        if operation["state"] in TERMINAL:
            return self._operation_response(operation)
        action = str(request.get("action") or "")
        if action not in {"exit", "cancel"}:
            raise FeatureError("invalid_control", "caption control is invalid")
        if action != operation.get("control"):
            raise FeatureError("stale_control", "caption control has changed")
        try:
            operation["revision"] = max(operation["revision"], int(request.get("revision") or 0))
        except (TypeError, ValueError):
            pass
        operation["cancel_event"].set()
        operation["session"] = {}
        operation.pop("inventory", None)
        self.sessions.pop((operation["chat_id"], operation["user_id"]), None)
        task = operation.get("task")
        if task and not task.done():
            task.cancel()
        text = "已退出字幕任务；已写入的字幕保留。" if action == "exit" else "字幕任务已取消；已写入的字幕保留。"
        self._change(operation, state="cancelled", stage="cancelled", control="", status_text=text, details={})
        return self._operation_response(operation)

    async def operation_snapshot(self, request):
        operation_id = str(request.get("operation_id") or "")
        operation = self.operations.get(operation_id) or self.store.get(operation_id)
        if not operation:
            raise FeatureError("operation_not_found", "caption task not found")
        return {"operation": self._view(operation)}

    async def _confirm_ownership(self, operation):
        for attempt in range(20):
            self._check_cancel(operation)
            try:
                response = await self.host.report_operation(self._view(operation))
            except FeatureError as exc:
                if exc.code not in {"operation_not_found", "operation_owner_mismatch", "ownership_rejected"}:
                    raise
                response = {}
            if response.get("accepted") is True:
                return
            await asyncio.sleep(0.1)
        raise FeatureError("ownership_rejected", "Host did not accept caption operation")

    def _change(self, operation, **changes):
        operation.update(changes)
        operation["revision"] += 1
        self.store.save(operation)

    async def _report(self, operation, **changes):
        if operation["state"] in TERMINAL:
            return
        if changes.get("state") != "cancelled":
            self._check_cancel(operation)
        self._change(operation, **changes)
        response = await self.host.report_operation(self._view(operation))
        if response.get("accepted") is not True:
            raise FeatureError("ownership_rejected", "Host rejected caption operation update")

    async def _cancelled(self, operation):
        if operation["state"] != "cancelled":
            await self._report(operation, state="cancelled", stage="cancelled", control="", status_text="字幕任务已取消；已写入的字幕保留。", details={})

    @staticmethod
    def _check_cancel(operation):
        if operation and operation["cancel_event"].is_set():
            raise asyncio.CancelledError

    @staticmethod
    def _view(operation):
        fields = ("operation_id", "chat_id", "user_id", "state", "stage", "status_text", "control", "revision", "details")
        return {**{k: deepcopy(operation.get(k)) for k in fields}, "segment": {"role": "caption", "presentation_kind": "text"}}

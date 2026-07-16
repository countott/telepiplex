from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path

from telepiplex_plugin_sdk import FeatureError

from .adapters.fanart import FanartAdapter
from .adapters.plex import PlexAdapter
from .adapters.tmdb import TmdbAdapter
from .config_wizard import PlexConfigWizard
from .jobs import PlexJobRepository
from .management import PlexManagementService, PlexOperationCancelled


_PLEX_STAGE_TEXT = {
    "scan_preparing": "正在准备 Plex 扫描任务。",
    "scanning": "Plex 已接受媒体库扫描，等待当前调用完成。",
    "artwork": "正在处理 Plex 海报。",
    "audio": "正在处理 Plex 音轨选择。",
    "subtitle": "正在处理 Plex 字幕选择。",
}


def _ambiguous_core_report_error(exc: Exception) -> bool:
    return not isinstance(exc, FeatureError) or exc.code in {
        "core_unavailable", "deadline_exceeded", "invalid_response",
    }


class PlexFeature:
    def __init__(
        self,
        *,
        config: dict,
        core,
        state_path: Path,
        repository=None,
        service_factory=None,
    ):
        self.config = config
        self.core = core
        self.state_path = Path(state_path)
        self.state_path.mkdir(parents=True, exist_ok=True)
        self.jobs = repository or PlexJobRepository(self.state_path / "plex_jobs.db")
        self.service_factory = service_factory or self._build_service
        self.service = None
        self.service_error = ""
        self.mcp_handle = None
        self.runtime = None
        self.loop = None
        self._service_lock = asyncio.Lock()
        self.operations = {}
        self.owner_operations = {}
        self.job_operation_ids = {}
        self.interrupted_job_ids = self.jobs.mark_incomplete_interrupted()
        self.config_wizard = PlexConfigWizard(config)

    def bind_runtime(self, runtime):
        self.runtime = runtime
        self.loop = asyncio.get_running_loop()
        if self.interrupted_job_ids:
            # Establish terminal coordinated ownership synchronously before the
            # route can receive a replayed Core event.  Reporting the snapshot
            # may wait for Core, but local replay suppression must not.
            for job_id in self.interrupted_job_ids:
                job = self.jobs.get(job_id)
                payload = (job or {}).get("payload") or {}
                if payload.get("operation_id"):
                    self._restore_interrupted_operation(job)
            runtime.spawn(self._resume_interrupted(), task_id="plex-resume")

    async def media_organized(self, request: dict) -> dict:
        payload = request.get("payload") or {}
        operation = await self._accept_event_operation(payload)
        operation_id = operation["operation_id"] if operation else ""
        if operation and operation.get("state") == "interrupted":
            job_id = next((
                job_id for job_id, owner in self.job_operation_ids.items()
                if owner == operation_id
            ), 0)
            return {
                "accepted": True,
                "job_id": int(job_id),
                "state": "interrupted",
                "duplicate": True,
                "operation_id": operation_id,
                "operation": self._operation_view(
                    self.operations[operation_id]
                ),
            }
        try:
            service = await self._ensure_service()
            job = await asyncio.to_thread(
                service.enqueue_organized_event, payload
            )
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="scan_preparing",
                status_text=(
                    "Plex 管理初始化失败，任务未进入扫描："
                    f"{type(exc).__name__}"
                ),
                control="",
            )
            raise
        if not job:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="scan_preparing",
                status_text="Plex 管理拒绝了不完整的媒体元数据。",
                control="",
            )
            if payload.get("user_id"):
                await self.core.notify_user(
                    int(payload["user_id"]),
                    "⚠️ Plex 管理拒绝了不完整的 canonical metadata；请人工检查。",
                )
            result = {"accepted": True, "state": "rejected"}
            if operation:
                result["operation"] = self._operation_view(
                    self.operations[operation_id]
                )
            return result
        if operation and operation.get("state") in {
            "completed", "cancelled", "failed", "interrupted"
        }:
            return {
                "accepted": True,
                "job_id": job["id"],
                "state": operation["state"],
                "duplicate": True,
                "operation_id": operation_id,
                "operation": self._operation_view(
                    self.operations[operation_id]
                ),
            }
        started = False
        state = str(job.get("state") or "")
        if state != "completed" and await asyncio.to_thread(
            self.jobs.claim,
            job["id"],
        ):
            started = True
            state = "running"
            if operation_id:
                self.job_operation_ids[int(job["id"])] = operation_id
        elif state != "completed":
            state = str((self.jobs.get(job["id"]) or job).get("state") or "")
        if started:
            task_identity = str(
                request.get("event_id")
                or payload.get("job_id")
                or job["id"]
            )
            try:
                task = self.runtime.spawn(
                    self._run_job(job["id"], operation_id),
                    task_id=f"plex-job-{task_identity}",
                )
                if operation_id:
                    self.operations[operation_id]["task"] = task
            except Exception:
                self.jobs.update(
                    job["id"],
                    state="interrupted",
                    error="failed to start Plex job task",
                )
                raise
        result = {
            "accepted": True,
            "job_id": job["id"],
            "state": "running" if started else state,
            "duplicate": not started,
        }
        if operation:
            if not started:
                current = self.operations[operation_id]
                if (
                    current.get("state") in {
                        "running", "awaiting_input", "cancelling"
                    }
                    and state not in {"completed", "failed", "cancelled"}
                ):
                    operation = self._operation_view(current)
                else:
                    terminal_state = (
                        "completed" if state == "completed" else "failed"
                    )
                    operation = await self._report_if_active(
                        operation_id,
                        state=terminal_state,
                        stage=terminal_state,
                        status_text=(
                            "Plex 管理任务已完成。"
                            if terminal_state == "completed"
                            else "Plex 管理任务未能启动。"
                        ),
                        control="",
                    )
            result["operation_id"] = operation_id
            result["operation"] = operation
        return result

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command == "plex_config":
            result = self.config_wizard.start(request)
            result["operation"] = self._new_operation(
                request,
                state="awaiting_input",
                stage="config_section",
                status_text="等待选择 plex-management 配置项。",
                control="exit",
                kind="config",
            )
            return result
        self.config_wizard.clear(request)
        try:
            service = await self._ensure_service()
        except Exception:
            return self._message(f"⚠️ Plex Feature 暂不可用：{self.service_error or 'configuration error'}")
        if command == "scan":
            if request.get("args"):
                return self._message("用法：/scan")
            return await self._scan_menu(service)
        text = " ".join(str(item) for item in request.get("args") or []).strip()
        if not text:
            jobs = await asyncio.to_thread(service.list_jobs, 5)
            if not jobs:
                return self._message("当前没有 Plex 管理任务。")
            lines = ["最近 Plex 任务："]
            lines.extend(
                f"#{job['id']} {job['state']} {job['payload'].get('resource_name') or ''}"
                for job in jobs
            )
            return self._message("\n".join(lines))
        if not text.isdecimal():
            return self._message("用法：/plex [Job ID]")
        job = await asyncio.to_thread(service.get_job, int(text))
        if not job:
            return self._message(f"⚠️ Plex 任务不存在：#{text}")
        waiting = await asyncio.to_thread(
            service.pending_selection, job["id"]
        )
        if not waiting:
            return self._message(PlexManagementService.format_job_summary(job))
        operation = self._selection_operation(request, job, waiting)
        return {
            "actions": [self._selection_action(job, waiting)],
            "operation": operation,
        }

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        if payload.startswith("config:"):
            return self._decorate_config_result(
                request, self.config_wizard.callback(request)
            )
        if payload in {"exit", "cancel"}:
            return await self._cancel_owner_interaction(request, payload)
        service = await self._ensure_service()
        if payload.startswith("scan:"):
            return await self._scan_callback(request, service, payload)
        if payload.startswith("choice:"):
            return await self._choice_callback(request, service, payload)
        return self._message("⚠️ Plex callback 无效。")

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        return self._message("⚠️ Plex 配置会话已失效。")

    async def _scan_menu(self, service, *, page=0, edit=False):
        libraries = await asyncio.to_thread(service.list_libraries)
        libraries = [
            dict(library)
            for library in libraries
            if str((library or {}).get("id") or "").strip()
        ]
        page_count = max((len(libraries) + 7) // 8, 1)
        page = min(max(int(page), 0), page_count - 1)
        visible = libraries[page * 8:(page + 1) * 8]
        keyboard = []
        if libraries:
            keyboard.append([{
                "text": "扫描全部媒体库",
                "callback_data": "plex:scan:all",
            }])
        for library in visible:
            library_id = str(library["id"])
            callback_data = f"plex:scan:{library_id}"
            if len(callback_data.encode("utf-8")) > 64:
                continue
            keyboard.append([{
                "text": str(library.get("title") or library_id),
                "callback_data": callback_data,
            }])
        navigation = []
        if page > 0:
            navigation.append({
                "text": "上一页",
                "callback_data": f"plex:scan:page:{page - 1}",
            })
        if page + 1 < page_count:
            navigation.append({
                "text": "下一页",
                "callback_data": f"plex:scan:page:{page + 1}",
            })
        navigation.append({
            "text": "取消",
            "callback_data": "plex:scan:cancel",
        })
        keyboard.append(navigation)
        text = (
            f"请选择要扫描的 Plex 媒体库（{page + 1}/{page_count}）："
            if libraries
            else "当前没有可扫描的 Plex 媒体库。"
        )
        action = {
            "kind": "edit_message" if edit else "send_message",
            "text": text,
        }
        if keyboard:
            action["data"] = {"keyboard": keyboard}
        return {"actions": [action]}

    async def _scan_callback(self, request, service, payload):
        if payload == "scan:cancel":
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": "已取消 Plex 扫描选择。",
                }]
            }
        if payload.startswith("scan:page:"):
            try:
                page = int(payload.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                return self._message("⚠️ Plex 扫描页码无效。")
            return await self._scan_menu(service, page=page, edit=True)

        selected = payload.split(":", 1)[1]
        libraries = await asyncio.to_thread(service.list_libraries)
        libraries = [
            dict(library)
            for library in libraries
            if str((library or {}).get("id") or "").strip()
        ]
        by_id = {
            str(library["id"]): library
            for library in libraries
        }
        if selected == "all":
            if not libraries:
                return {
                    "actions": [{
                        "kind": "edit_message",
                        "text": "当前没有可扫描的 Plex 媒体库。",
                    }]
                }
            library_ids = None
            target_text = "全部媒体库"
        else:
            library = by_id.get(selected)
            if not library:
                return {
                    "actions": [{
                        "kind": "edit_message",
                        "text": "⚠️ Plex 媒体库列表已变化，请重新执行 /scan。",
                    }]
                }
            library_ids = [selected]
            target_text = str(library.get("title") or selected)

        operation = self._new_operation(
            request,
            state="running",
            stage="scanning",
            status_text=f"正在提交 Plex 扫描：{target_text}。",
            control="cancel",
            kind="manual_scan",
        )
        operation_id = operation["operation_id"]
        task_id = f"plex-scan-{operation_id}"
        task = self.runtime.spawn(
            self._run_manual_scan(operation_id, library_ids),
            task_id=task_id,
        )
        self.operations[operation_id].update({
            "task": task,
            "task_id": task_id,
        })
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"⏳ 正在提交 Plex 扫描：{target_text}...",
            }],
            "operation": operation,
        }

    async def _run_manual_scan(self, operation_id, library_ids):
        try:
            service = await self._ensure_service()
            result = await asyncio.to_thread(
                service.scan_libraries,
                library_ids,
                should_cancel=lambda: self._is_cancelled(operation_id),
            )
            self._raise_if_cancelled(operation_id)
            failed = list(result.get("failed") or [])
            succeeded = list(result.get("succeeded") or [])
            state = "failed" if failed and not succeeded else "completed"
            await self._report_operation(
                operation_id,
                state=state,
                stage=state,
                status_text=self._scan_summary(result),
                control="",
                details={
                    "succeeded": succeeded,
                    "failed": failed,
                },
            )
        except PlexOperationCancelled:
            await self._finish_cancelled(operation_id)
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="scanning",
                status_text=f"Plex 手动扫描失败：{type(exc).__name__}",
                control="",
            )

    @staticmethod
    def _scan_summary(result):
        succeeded = list((result or {}).get("succeeded") or [])
        failed = list((result or {}).get("failed") or [])
        lines = ["Plex 媒体库扫描提交完成。"]
        if succeeded:
            lines.append(
                "成功：" + "、".join(
                    str(library.get("title") or library.get("id") or "")
                    for library in succeeded
                )
            )
        if failed:
            lines.append(
                "失败：" + "；".join(
                    (
                        str(library.get("title") or library.get("id") or "")
                        + (
                            f"（{library.get('error')}）"
                            if library.get("error")
                            else ""
                        )
                    )
                    for library in failed
                )
            )
        if not succeeded and not failed:
            lines.append("没有可扫描的媒体库。")
        return "\n".join(lines)

    async def _choice_callback(self, request, service, payload):
        parts = payload.split(":")
        if len(parts) < 3:
            return self._message("⚠️ Plex 选择已失效。")
        try:
            job_id = int(parts[1])
        except (TypeError, ValueError):
            return self._message("⚠️ Plex 选择已失效。")
        job = await asyncio.to_thread(service.get_job, job_id)
        if not job:
            return self._message("⚠️ Plex 选择已失效。")
        waiting = await asyncio.to_thread(service.pending_selection, job_id)
        if not waiting:
            return self._message("⚠️ Plex 选择已失效。")
        operation = self._selection_operation(request, job, waiting)
        operation_id = operation["operation_id"]
        action = parts[2]
        candidates = list(waiting.get("candidates") or [])
        if not candidates:
            return self._message("⚠️ Plex 当前没有可选候选。")

        if action in {"prev", "next"} and len(parts) == 3:
            current = int(waiting.get("candidate_index") or 0)
            offset = -1 if action == "prev" else 1
            if str(waiting.get("kind") or "") == "artwork":
                index = (current + offset) % len(candidates)
            else:
                page_count = max((len(candidates) + 7) // 8, 1)
                current_page = min(current // 8, page_count - 1)
                page = min(max(current_page + offset, 0), page_count - 1)
                index = page * 8
            waiting = await asyncio.to_thread(
                service.set_selection_index, job_id, index
            )
            view = self._advance_operation(
                operation_id,
                state="awaiting_input",
                stage=str(waiting["kind"]),
                status_text=self._selection_text(job, waiting),
                control="cancel",
                details=self._selection_details(job, waiting),
            )
            return {
                "actions": [self._selection_action(job, waiting, edit=True)],
                "operation": view,
            }

        if action != "pick" or len(parts) != 4:
            return self._message("⚠️ Plex 选择已失效。")
        try:
            index = int(parts[3])
        except (TypeError, ValueError):
            return self._message("⚠️ Plex 选择已失效。")
        if index < 0 or index >= len(candidates):
            return self._message("⚠️ Plex 选择已失效。")

        view = self._advance_operation(
            operation_id,
            state="running",
            stage=str(waiting["kind"]),
            status_text="已确认候选，继续执行 Plex 管理任务。",
            control="cancel",
            details={"job_id": job_id},
        )
        task_id = f"plex-choice-{job_id}"
        task = self.runtime.spawn(
            self._run_selection(operation_id, job_id, index),
            task_id=task_id,
        )
        self.operations[operation_id].update({
            "task": task,
            "task_id": task_id,
        })
        progress = {
            "kind": (
                "edit_photo"
                if str(waiting.get("kind") or "") == "artwork"
                else "edit_message"
            ),
            "text": "⏳ 已确认候选，继续执行 Plex 管理任务...",
        }
        if progress["kind"] == "edit_photo":
            progress["data"] = {
                "photo_url": str(candidates[index].get("url") or ""),
            }
        return {"actions": [progress], "operation": view}

    def _selection_operation(self, request, job, waiting):
        job_id = int(job["id"])
        operation_id = self.job_operation_ids.get(job_id)
        operation = self.operations.get(operation_id) if operation_id else None
        if operation is None or operation.get("state") in {
            "completed", "cancelled", "failed", "interrupted",
        }:
            view = self._new_operation(
                request,
                state="awaiting_input",
                stage=str(waiting["kind"]),
                status_text=self._selection_text(job, waiting),
                control="cancel",
                kind="selection",
            )
            operation_id = view["operation_id"]
            self.job_operation_ids[job_id] = operation_id
            operation = self.operations[operation_id]
        operation.update({
            "state": "awaiting_input",
            "stage": str(waiting["kind"]),
            "status_text": self._selection_text(job, waiting),
            "control": "cancel",
            "details": self._selection_details(job, waiting),
        })
        return self._operation_view(operation)

    @staticmethod
    def _selection_text(job, waiting):
        kind_labels = {
            "artwork": "海报",
            "audio": "音轨",
            "subtitle": "字幕",
        }
        kind = str(waiting.get("kind") or "")
        candidates = list(waiting.get("candidates") or [])
        index = min(
            max(int(waiting.get("candidate_index") or 0), 0),
            max(len(candidates) - 1, 0),
        )
        name = (
            (job.get("payload") or {}).get("resource_name")
            or f"Job {job.get('id')}"
        )
        if candidates and kind == "artwork":
            position = f"（{index + 1}/{len(candidates)}）"
        elif candidates:
            page_count = max((len(candidates) + 7) // 8, 1)
            page = min(index // 8, page_count - 1)
            position = f"（第 {page + 1}/{page_count} 页）"
        else:
            position = ""
        return (
            f"Plex 任务 #{job['id']}：{name}\n"
            f"请选择{kind_labels.get(kind, '候选')}{position}。"
        )

    def _selection_details(self, job, waiting):
        action = self._selection_action(job, waiting)
        data = dict(action.get("data") or {})
        details = {
            "job_id": int(job["id"]),
            "candidate_index": int(waiting.get("candidate_index") or 0),
            "keyboard": list(data.get("keyboard") or []),
        }
        if data.get("photo_url"):
            details["photo_url"] = str(data["photo_url"])
        return details

    def _selection_action(self, job, waiting, *, edit=False):
        kind = str(waiting.get("kind") or "")
        candidates = list(waiting.get("candidates") or [])
        index = min(
            max(int(waiting.get("candidate_index") or 0), 0),
            max(len(candidates) - 1, 0),
        )
        text = self._selection_text(job, waiting)
        if kind == "artwork":
            keyboard = [[
                {
                    "text": "上一张",
                    "callback_data": f"plex:choice:{job['id']}:prev",
                },
                {
                    "text": "选择此海报",
                    "callback_data": (
                        f"plex:choice:{job['id']}:pick:{index}"
                    ),
                },
                {
                    "text": "下一张",
                    "callback_data": f"plex:choice:{job['id']}:next",
                },
            ]]
            return {
                "kind": "edit_photo" if edit else "send_photo",
                "text": text,
                "data": {
                    "photo_url": str(candidates[index].get("url") or ""),
                    "keyboard": keyboard + [[{
                        "text": "取消",
                        "callback_data": "plex:cancel",
                    }]],
                },
            }

        page_count = max((len(candidates) + 7) // 8, 1)
        page = min(index // 8, page_count - 1)
        start = page * 8
        keyboard = [
            [{
                "text": self._candidate_label(kind, candidate),
                "callback_data": (
                    f"plex:choice:{job['id']}:pick:{candidate_index}"
                ),
            }]
            for candidate_index, candidate in enumerate(
                candidates[start:start + 8],
                start=start,
            )
        ]
        controls = []
        if page > 0:
            controls.append({
                "text": "上一页",
                "callback_data": f"plex:choice:{job['id']}:prev",
            })
        if page + 1 < page_count:
            controls.append({
                "text": "下一页",
                "callback_data": f"plex:choice:{job['id']}:next",
            })
        controls.append({
            "text": "取消",
            "callback_data": "plex:cancel",
        })
        return {
            "kind": "edit_message" if edit else "send_message",
            "text": text,
            "data": {"keyboard": keyboard + [controls]},
        }

    @staticmethod
    def _candidate_label(kind, candidate):
        candidate_id = str(candidate.get("id") or "?")
        name = str(
            candidate.get("display_title")
            or candidate.get("title")
            or candidate.get("language")
            or candidate.get("language_code")
            or "未知语言"
        )
        if kind == "audio":
            codec = str(candidate.get("codec") or "未知格式").upper()
            channels = int(candidate.get("channels") or 0)
            bitrate = int(candidate.get("bitrate") or 0)
            return (
                f"#{candidate_id} · {name} · {codec} · "
                f"{channels}ch · {bitrate}kbps"
            )
        location = "外挂" if candidate.get("external") else "内嵌"
        return f"#{candidate_id} · {name} · {location}"

    async def _run_selection(self, operation_id, job_id, index):
        try:
            service = await self._ensure_service()
            result = await asyncio.to_thread(
                service.confirm_selection,
                job_id,
                index,
                should_cancel=lambda: self._is_cancelled(operation_id),
                on_stage=lambda stage, job: self._stage_sync(
                    operation_id, stage, job
                ),
            )
            await self._complete_batch_operation(operation_id, [result])
        except PlexOperationCancelled:
            await self._finish_cancelled(operation_id)
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage=(
                    (self.operations.get(operation_id) or {}).get("stage")
                    or "selection"
                ),
                status_text=f"Plex 候选确认失败：{type(exc).__name__}",
                control="",
            )

    async def management_capability(self, request: dict) -> dict:
        """Expose stable read-only job inspection to other Features."""
        service = await self._ensure_service()
        method = str(request.get("method") or "")
        params = request.get("payload") or {}
        if method == "get_job":
            return {
                "job": await asyncio.to_thread(
                    service.get_job, int(params.get("job_id") or 0)
                )
            }
        if method == "list_jobs":
            limit = min(max(int(params.get("limit") or 20), 1), 100)
            return {"jobs": await asyncio.to_thread(service.list_jobs, limit)}
        raise ValueError(f"unsupported plex.management method: {method}")

    async def _ensure_service(self):
        if self.service is not None:
            return self.service
        async with self._service_lock:
            if self.service is not None:
                return self.service
            try:
                self.service = await asyncio.to_thread(self.service_factory)
                self.service_error = ""
            except Exception as exc:
                self.service_error = PlexManagementService._safe_error(exc)
                raise
            return self.service

    def _build_service(self):
        plex_config = self.config.get("plex") or {}
        base_url = str(plex_config.get("base_url") or "").strip()
        token = str(plex_config.get("token") or "").strip()
        if not base_url or not token:
            raise ValueError("plex.base_url and plex.token are required")
        tmdb_config = self.config.get("tmdb") or {}
        fanart_config = self.config.get("fanart") or {}
        service = PlexManagementService(
            self.jobs,
            PlexAdapter(base_url, token, plex_config.get("timeout", 15)),
            tmdb=(TmdbAdapter(tmdb_config["api_key"], tmdb_config.get("timeout", 15)) if tmdb_config.get("api_key") else None),
            fanart=(FanartAdapter(fanart_config["api_key"], fanart_config.get("timeout", 15)) if fanart_config.get("api_key") else None),
            notifier=self._notify_sync,
            category_folders=self.config.get("category_folder") or [],
            scan_poll_interval=plex_config.get("scan_poll_interval", 5),
            scan_timeout=plex_config.get("scan_timeout", 300),
        )
        mcp_config = self.config.get("mcp") or {}
        if mcp_config.get("enabled"):
            try:
                from .mcp_server import start_plex_mcp_server
                self.mcp_handle = start_plex_mcp_server(service, mcp_config)
            except Exception as exc:
                self.service_error = f"MCP isolated: {PlexManagementService._safe_error(exc)}"
        return service

    async def _run_job(self, job_id: int, operation_id=""):
        try:
            service = await self._ensure_service()
            result = await asyncio.to_thread(
                service.run_job,
                job_id,
                should_cancel=(
                    lambda: self._is_cancelled(operation_id)
                ) if operation_id else None,
                on_stage=(
                    lambda stage, job: self._stage_sync(
                        operation_id,
                        stage,
                        job,
                    )
                ) if operation_id else None,
            )
            await self._complete_batch_operation(operation_id, [result])
        except PlexOperationCancelled:
            job = self.jobs.get(job_id)
            if job and job["state"] not in {"completed", "failed"}:
                self.jobs.update(
                    job_id,
                    state="cancelled",
                    error="cancelled after current Plex step",
                )
            await self._finish_cancelled(operation_id)
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage=(
                    (self.operations.get(operation_id) or {}).get("stage")
                    or "scan_preparing"
                ),
                status_text=f"Plex 管理任务失败：{type(exc).__name__}",
                control="",
            )
        finally:
            job = self.jobs.get(job_id)
            if job and job["state"] in {
                "running", "scanning", "artwork", "audio", "subtitle",
            }:
                self.jobs.update(
                    job_id,
                    state="interrupted",
                    error="interrupted before completion",
                )

    async def _accept_event_operation(self, payload):
        operation_id = str(payload.get("operation_id") or "")
        if not operation_id:
            return None
        user_id = int(payload.get("user_id") or 0)
        chat_id = int(payload.get("chat_id") or user_id or 0)
        if len(operation_id) > 40 or user_id <= 0 or chat_id == 0:
            raise FeatureError("invalid_operation", "Plex operation identity is invalid")
        existing = self.operations.get(operation_id)
        if existing is not None:
            if (
                int(existing.get("chat_id") or 0) != chat_id
                or int(existing.get("user_id") or 0) != user_id
            ):
                raise FeatureError(
                    "operation_conflict", "Plex operation owner changed"
                )
            if existing.get("ownership_pending"):
                await self._confirm_event_ownership(existing)
            return self._operation_view(existing)
        try:
            revision = max(0, int(payload.get("operation_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        operation = {
            "operation_id": operation_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "state": "running",
            "stage": "accepted",
            "status_text": "plex-management 已接受任务。",
            "control": "cancel",
            "revision": revision,
            "details": {"completed_effects": []},
            "kind": "management",
            "cancel_event": threading.Event(),
        }
        self.operations[operation_id] = operation
        self.owner_operations[(chat_id, user_id)] = operation_id
        try:
            return await self._report_operation(
                operation_id,
                state="running",
                stage="scan_preparing",
                status_text=_PLEX_STAGE_TEXT["scan_preparing"],
                control="cancel",
                details={"completed_effects": []},
            )
        except Exception as exc:
            if _ambiguous_core_report_error(exc):
                operation["ownership_pending"] = True
                operation["ownership_report"] = self._operation_view(operation)
            raise

    async def _confirm_event_ownership(self, operation):
        report = dict(
            operation.get("ownership_report")
            or self._operation_view(operation)
        )
        try:
            response = await self.core.report_operation(report)
        except Exception as exc:
            if _ambiguous_core_report_error(exc):
                raise
            operation.update({
                "state": "interrupted",
                "status_text": "Core 已结束协调任务，未开始 Plex 操作。",
                "control": "",
            })
            operation["ownership_pending"] = False
            operation.pop("ownership_report", None)
            return
        if not isinstance(response, dict) or response.get("accepted") is not True:
            operation.update({
                "state": "interrupted",
                "status_text": "Core 已结束协调任务，未开始 Plex 操作。",
                "control": "",
            })
        operation["ownership_pending"] = False
        operation.pop("ownership_report", None)

    def _stage_sync(self, operation_id, stage, job):
        if not operation_id or self.loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._report_stage(operation_id, stage, job),
            self.loop,
        )
        future.result(timeout=30)

    async def _report_stage(self, operation_id, stage, job):
        operation = self.operations.get(operation_id)
        if operation is None or operation.get("state") in {
            "cancelling", "cancelled", "completed", "failed",
        }:
            return
        details = dict(operation.get("details") or {})
        effects = list(details.get("completed_effects") or [])
        previous = str(operation.get("stage") or "")
        if previous in _PLEX_STAGE_TEXT and previous not in effects:
            effects.append(previous)
        details.update({
            "completed_effects": effects,
            "job_id": int((job or {}).get("id") or 0),
        })
        await self._report_operation(
            operation_id,
            state="running",
            stage=stage,
            status_text=_PLEX_STAGE_TEXT.get(stage, "Plex 管理任务执行中。"),
            control="cancel",
            details=details,
        )

    async def _complete_batch_operation(self, operation_id, results):
        if not operation_id or operation_id not in self.operations:
            return None
        results = [job for job in (results or []) if isinstance(job, dict)]
        waiting = next((
            job for job in results
            if job.get("state") == "awaiting_selection"
        ), None)
        if waiting:
            service = await self._ensure_service()
            selection = await asyncio.to_thread(
                service.pending_selection, waiting["id"]
            )
            if selection:
                return await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage=str(selection["kind"]),
                    status_text=self._selection_text(waiting, selection),
                    control="cancel",
                    details=self._selection_details(waiting, selection),
                )
        failed = [job for job in results if job.get("state") == "failed"]
        if failed:
            return await self._report_operation(
                operation_id,
                state="failed",
                stage=str(failed[0].get("state") or "failed"),
                status_text=PlexManagementService.format_job_summary(failed[0]),
                control="",
            )
        return await self._report_operation(
            operation_id,
            state="completed",
            stage="completed",
            status_text="Plex 管理任务已完成。",
            control="",
            details={"completed_effects": list(_PLEX_STAGE_TEXT)},
        )

    async def _finish_cancelled(self, operation_id):
        operation = self.operations.get(operation_id)
        if operation is None or operation.get("state") in {
            "cancelled", "completed", "failed",
        }:
            return self._operation_view(operation) if operation else None
        effects = list(
            (operation.get("details") or {}).get("completed_effects") or []
        )
        return await self._report_operation(
            operation_id,
            state="cancelled",
            stage=operation.get("stage") or "cancelled",
            status_text=self._cancelled_status(effects),
            control="",
            details={"completed_effects": effects},
        )

    @staticmethod
    def _cancelled_status(effects=()):
        return (
            "已取消 Plex 任务，后续步骤不会继续。"
            + (f" 已完成步骤：{'、'.join(effects)}。" if effects else "")
            + " 已由 Plex 接受的扫描、海报和音轨/字幕写入不会自动回滚。"
        )

    def _is_cancelled(self, operation_id):
        operation = self.operations.get(operation_id)
        event = operation.get("cancel_event") if operation else None
        return bool(event and event.is_set())

    def _raise_if_cancelled(self, operation_id):
        if self._is_cancelled(operation_id):
            raise PlexOperationCancelled("Plex operation cancelled")

    async def operation_control(self, request: dict) -> dict:
        operation_id = str(request.get("operation_id") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise FeatureError("not_found", "Plex operation was not found")
        if operation.get("state") in {
            "completed", "cancelled", "failed", "interrupted"
        }:
            return {"actions": [], "operation": self._operation_view(operation)}
        try:
            operation["revision"] = max(
                int(operation.get("revision") or 0),
                int(request.get("revision") or 0),
            )
        except (TypeError, ValueError):
            pass
        action = str(request.get("action") or "")
        if action != operation.get("control") or action not in {"exit", "cancel"}:
            raise FeatureError("stale_control", "Plex operation control changed")
        owner = (operation["chat_id"], operation["user_id"])
        self.config_wizard.sessions.pop(owner, None)
        if action == "exit":
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "interaction",
                status_text="已退出 Plex 交互。",
                control="",
            )
            return {"actions": [], "operation": terminal}

        cancel_event = operation.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        task = operation.get("task")
        if operation.get("state") == "awaiting_input":
            job_id = int(
                (operation.get("details") or {}).get("job_id") or 0
            )
            if job_id:
                service = await self._ensure_service()
                job = await asyncio.to_thread(
                    service.cancel_pending_selection, job_id
                )
                terminal = self._advance_operation(
                    operation_id,
                    state="cancelled",
                    stage=operation.get("stage") or "selection",
                    status_text=self._cancelled_status(),
                    control="",
                    details={
                        "job_id": job_id,
                        "job_state": str(job.get("state") or ""),
                    },
                )
                return {"actions": [], "operation": terminal}
            terminal = await self._finish_cancelled(operation_id)
            return {"actions": [], "operation": terminal}
        if task is None:
            terminal = await self._finish_cancelled(operation_id)
            return {"actions": [], "operation": terminal}
        cancelling = self._advance_operation(
            operation_id,
            state="cancelling",
            stage=operation.get("stage") or "running",
            status_text="取消请求已接受，将在当前 Plex 调用结束后停止。",
            control="cancel",
            details=dict(operation.get("details") or {}),
        )
        return {"actions": [], "operation": cancelling}

    async def operation_snapshot(self, request: dict) -> dict:
        requested = str(request.get("operation_id") or "")
        return {"operations": [
            self._operation_view(operation)
            for operation_id, operation in self.operations.items()
            if operation.get("state") not in {
                "completed", "cancelled", "failed", "interrupted"
            }
            and (not requested or requested == operation_id)
        ]}

    async def _cancel_owner_interaction(self, request, action):
        operation = self._operation_for_owner(self._owner_key(request))
        if operation is None:
            return self._message("⚠️ Plex 交互已失效。")
        photo_url = str(
            (operation.get("details") or {}).get("photo_url") or ""
        )
        result = await self.operation_control({
            "operation_id": operation["operation_id"],
            "action": action,
            "revision": operation["revision"],
        })
        feedback = {
            "kind": "edit_photo" if photo_url else "edit_message",
            "text": result["operation"]["status_text"],
        }
        if photo_url:
            feedback["data"] = {"photo_url": photo_url}
        return {
            "actions": [feedback],
            "session": {"state": "close"},
            "operation": result["operation"],
        }

    def _decorate_config_result(self, request, result):
        owner = self._owner_key(request)
        operation = self._operation_for_owner(owner)
        if operation is None:
            return result
        session = result.get("session") if isinstance(result, dict) else None
        if "config_patch" in result:
            view = self._advance_operation(
                operation["operation_id"],
                state="running",
                stage="config_apply",
                status_text="正在保存并重新加载 plex-management 配置。",
                control="cancel",
            )
        elif isinstance(session, dict) and session.get("state") == "open":
            wizard_session = self.config_wizard.sessions.get(owner) or {}
            view = self._advance_operation(
                operation["operation_id"],
                state="awaiting_input",
                stage=f"config_{wizard_session.get('stage') or 'input'}",
                status_text="等待 plex-management 配置输入。",
                control="exit",
            )
        else:
            view = self._advance_operation(
                operation["operation_id"],
                state="cancelled",
                stage="config_cancelled",
                status_text="已退出 plex-management 配置。",
                control="",
            )
        result["operation"] = view
        return result

    def _new_operation(
        self, request, *, state, stage, status_text, control, kind
    ):
        operation_id = uuid.uuid4().hex
        owner = self._owner_key(request)
        operation = {
            "operation_id": operation_id,
            "chat_id": owner[0],
            "user_id": owner[1],
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": 1,
            "details": {},
            "kind": kind,
            "cancel_event": threading.Event(),
        }
        self.operations[operation_id] = operation
        self.owner_operations[owner] = operation_id
        return self._operation_view(operation)

    def _operation_for_owner(self, owner):
        operation_id = self.owner_operations.get(owner)
        return self.operations.get(operation_id) if operation_id else None

    def _advance_operation(
        self, operation_id, *, state, stage, status_text, control, details=None
    ):
        operation = self.operations[operation_id]
        operation.update({
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": int(operation.get("revision") or 0) + 1,
        })
        if details is not None:
            operation["details"] = dict(details)
        return self._operation_view(operation)

    async def _report_operation(self, operation_id, **changes):
        view = self._advance_operation(operation_id, **changes)
        response = await self.core.report_operation(view)
        if not isinstance(response, dict) or response.get("accepted") is not True:
            operation = self.operations[operation_id]
            operation.update({
                "state": "interrupted",
                "status_text": "Core 未接受当前 Feature 的任务所有权。",
                "control": "",
            })
            raise FeatureError(
                "operation_rejected",
                "Core rejected plex-management operation ownership",
            )
        return view

    async def _report_if_active(self, operation_id, **changes):
        if not operation_id or operation_id not in self.operations:
            return None
        operation = self.operations[operation_id]
        if operation.get("state") in {"completed", "cancelled", "failed"}:
            return self._operation_view(operation)
        return await self._report_operation(operation_id, **changes)

    @staticmethod
    def _operation_view(operation):
        return {
            "operation_id": str(operation["operation_id"]),
            "chat_id": int(operation.get("chat_id") or 0),
            "user_id": int(operation.get("user_id") or 0),
            "state": str(operation.get("state") or ""),
            "stage": str(operation.get("stage") or ""),
            "status_text": str(operation.get("status_text") or ""),
            "control": str(operation.get("control") or ""),
            "revision": int(operation.get("revision") or 0),
            "details": dict(operation.get("details") or {}),
        }

    @staticmethod
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    async def _resume_interrupted(self):
        legacy_job_ids = []
        for job_id in self.interrupted_job_ids:
            job = self.jobs.get(job_id)
            payload = (job or {}).get("payload") or {}
            operation_id = str(payload.get("operation_id") or "")
            if operation_id:
                existing = self.operations.get(operation_id)
                operation = (
                    self._operation_view(existing)
                    if existing is not None
                    else self._restore_interrupted_operation(job)
                )
                await self.core.report_operation(operation)
                continue
            legacy_job_ids.append(job_id)
        if not legacy_job_ids:
            self.interrupted_job_ids = []
            return
        try:
            await self._ensure_service()
        except Exception:
            return
        claimed = []
        for job_id in legacy_job_ids:
            if await asyncio.to_thread(self.jobs.claim, job_id):
                claimed.append(job_id)
        for job_id in claimed:
            self.runtime.spawn(
                self._run_job(job_id),
                task_id=f"plex-resume-{job_id}",
            )
        self.interrupted_job_ids = []

    def _restore_interrupted_operation(self, job):
        payload = (job or {}).get("payload") or {}
        operation_id = str(payload.get("operation_id") or "")
        user_id = int(payload.get("user_id") or 0)
        chat_id = int(payload.get("chat_id") or user_id or 0)
        try:
            revision = int(payload.get("operation_revision") or 0) + 1
        except (TypeError, ValueError):
            revision = 1
        operation = {
            "operation_id": operation_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "state": "interrupted",
            "stage": str((job or {}).get("state") or "interrupted"),
            "status_text": (
                "Plex Feature 进程停止，协调任务已中断；"
                "已完成的 Plex 远端操作不会自动回滚。"
            ),
            "control": "",
            "revision": revision,
            "details": {
                "job_id": int((job or {}).get("id") or 0),
                "stopped_at": str((job or {}).get("state") or "interrupted"),
            },
            "kind": "management",
            "cancel_event": threading.Event(),
        }
        self.operations[operation_id] = operation
        self.owner_operations[(chat_id, user_id)] = operation_id
        self.job_operation_ids[int(job["id"])] = operation_id
        return self._operation_view(operation)

    def _notify_sync(self, user_id, message, confirmation=None):
        if not user_id or self.loop is None:
            return False
        if confirmation:
            message += f"\n请使用 /plex 查看任务 #{confirmation.get('job_id')} 并人工确认。"
        future = asyncio.run_coroutine_threadsafe(
            self.core.notify_user(int(user_id), str(message)),
            self.loop,
        )
        return bool(future.result(timeout=30).get("accepted"))

    @staticmethod
    def _message(text):
        return {"actions": [{"kind": "send_message", "text": str(text)}]}

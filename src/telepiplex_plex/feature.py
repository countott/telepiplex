from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path

from telepiplex_plugin_sdk import FeatureError

from .adapters.fanart import FanartAdapter
from .adapters.plex import PlexAdapter
from .adapters.tmdb import TmdbAdapter
from .ai import PlexAIOrchestrator
from .config_wizard import PlexConfigWizard
from .jobs import PlexJobRepository
from .management import PlexManagementService, PlexOperationCancelled


_PLEX_STAGE_TEXT = {
    "scan_preparing": "正在准备 Plex 扫描任务。",
    "scanning": "Plex 已接受媒体库扫描，等待当前调用完成。",
    "locating": "正在定位新入库的 Plex 条目。",
    "matching": "正在验证或修正 Plex 匹配。",
    "localizing": "正在处理 Plex 中文元数据。",
    "artwork": "正在处理 Plex 海报。",
    "streams": "正在处理 Plex 音轨和字幕选择。",
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
        self.ai = None
        self.ai_error = ""
        self.mcp_handle = None
        self.runtime = None
        self.loop = None
        self._service_lock = asyncio.Lock()
        self.pending_writes = {}
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
            job_ids = sorted(
                job_id for job_id, owner in self.job_operation_ids.items()
                if owner == operation_id
            )
            return {
                "accepted": True,
                "job_ids": job_ids,
                "job_id": job_ids[0] if job_ids else 0,
                "state": "interrupted",
                "duplicate": True,
                "operation_id": operation_id,
                "operation": self._operation_view(
                    self.operations[operation_id]
                ),
            }
        try:
            service = await self._ensure_service()
            jobs = await asyncio.to_thread(
                service.enqueue_organized_event_jobs, payload
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
        if not jobs:
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
                "job_ids": [job["id"] for job in jobs],
                "job_id": jobs[0]["id"],
                "state": operation["state"],
                "duplicate": True,
                "operation_id": operation_id,
                "operation": self._operation_view(
                    self.operations[operation_id]
                ),
            }
        started = []
        states = []
        for job in jobs:
            if job["state"] == "completed":
                states.append("completed")
                continue
            if not await asyncio.to_thread(self.jobs.claim, job["id"]):
                states.append((self.jobs.get(job["id"]) or job)["state"])
                continue
            started.append(job["id"])
            if operation_id:
                self.job_operation_ids[int(job["id"])] = operation_id
            states.append("running")
        if started:
            batch_id = str(
                request.get("event_id")
                or payload.get("job_id")
                or started[0]
            )
            try:
                task = self.runtime.spawn(
                    self._run_batch(started, operation_id),
                    task_id=f"plex-batch-{batch_id}",
                )
                if operation_id:
                    self.operations[operation_id]["task"] = task
            except Exception:
                for job_id in started:
                    self.jobs.update(
                        job_id,
                        state="interrupted",
                        error="failed to start Plex batch task",
                    )
                raise
        result = {
            "accepted": True,
            "job_ids": [job["id"] for job in jobs],
            "job_id": jobs[0]["id"],
            "state": "running" if started else states[0],
            "duplicate": not started,
        }
        if operation:
            if not started:
                current = self.operations[operation_id]
                if (
                    current.get("state") in {
                        "running", "awaiting_input", "cancelling"
                    }
                    and any(state not in {"completed", "failed", "cancelled"}
                            for state in states)
                ):
                    operation = self._operation_view(current)
                else:
                    terminal_state = "completed" if all(
                        state == "completed" for state in states
                    ) else "failed"
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
            if self.ai_error:
                lines.append(f"AI 已隔离：{self.ai_error}")
            return self._message("\n".join(lines))
        if self.ai is None:
            return self._message(f"Plex AI 未启用或已隔离：{self.ai_error or 'missing configuration'}")
        operation = self._new_operation(
            request,
            state="running",
            stage="ai_planning",
            status_text="Plex AI 正在规划只读操作。",
            control="cancel",
            kind="ai",
        )
        operation_id = operation["operation_id"]
        task_id = f"plex-ai-{operation_id}"
        task = self.runtime.spawn(
            self._run_ai(operation_id, text),
            task_id=task_id,
        )
        self.operations[operation_id].update({"task": task, "task_id": task_id})
        return {
            "actions": [{"kind": "send_message", "text": "⏳ Plex AI 正在规划..."}],
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
        if payload.startswith("write:"):
            token = payload.split(":", 1)[1]
            pending = self.pending_writes.pop(token, None)
            if not pending:
                return self._message("⚠️ Plex 确认已失效。")
            operation_id = str(pending.get("operation_id") or "")
            operation = self._advance_operation(
                operation_id,
                state="running",
                stage="applying_write",
                status_text="正在执行已确认的 Plex 写操作。",
                control="cancel",
            )
            task_id = f"plex-write-{operation_id}"
            task = self.runtime.spawn(
                self._apply_write(operation_id, token, pending, service),
                task_id=task_id,
            )
            self.operations[operation_id].update({"task": task, "task_id": task_id})
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": "⏳ 正在执行已确认的 Plex 操作...",
                }],
                "operation": operation,
            }
        if payload.startswith("match:"):
            _, job_id, raw_index = payload.split(":", 2)
            job = service.get_job(int(job_id))
            waiting = next((
                value for value in (job or {}).get("step_results", {}).values()
                if isinstance(value, dict) and value.get("status") == "waiting"
            ), None)
            candidates = (waiting or {}).get("candidates") or []
            try:
                candidate = candidates[int(raw_index)]
            except (IndexError, ValueError):
                return self._message("⚠️ Plex 候选已失效。")
            selection = (
                candidate.get("rating_key")
                if waiting.get("kind") == "location"
                else candidate.get("guid")
            )
            operation_id = self.job_operation_ids.get(int(job_id), "")
            if not operation_id:
                operation = self._new_operation(
                    request,
                    state="running",
                    stage="matching",
                    status_text="正在应用 Plex 人工匹配。",
                    control="cancel",
                    kind="manual_match",
                )
                operation_id = operation["operation_id"]
                self.job_operation_ids[int(job_id)] = operation_id
            else:
                operation = self._advance_operation(
                    operation_id,
                    state="running",
                    stage="matching",
                    status_text="正在应用 Plex 人工匹配。",
                    control="cancel",
                )
            task_id = f"plex-match-{operation_id}"
            task = self.runtime.spawn(
                self._confirm_match(operation_id, int(job_id), selection, service),
                task_id=task_id,
            )
            self.operations[operation_id].update({"task": task, "task_id": task_id})
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": "⏳ 正在应用 Plex 人工匹配并继续任务...",
                }],
                "operation": operation,
            }
        return self._message("⚠️ Plex callback 无效。")

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        return self._message("⚠️ Plex 配置会话已失效。")

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

    async def _run_ai(self, operation_id, text):
        try:
            result = await asyncio.to_thread(self.ai.run, text)
            self._raise_if_cancelled(operation_id)
            message = str(result.get("message") or "Plex AI 未返回内容。")
            confirmation = result.get("confirmation") or {}
            token = str(confirmation.get("confirmation_token") or "")
            if token:
                self.pending_writes[token] = {
                    "action": confirmation.get("action") or "",
                    "payload": confirmation.get("payload") or {},
                    "operation_id": operation_id,
                }
                await self._report_operation(
                    operation_id,
                    state="awaiting_input",
                    stage="ai_confirmation",
                    status_text=message,
                    control="exit",
                    details={"keyboard": [[
                        {
                            "text": "确认执行",
                            "callback_data": f"plex:write:{token}",
                        },
                        {"text": "退出", "callback_data": "plex:exit"},
                    ]]},
                )
            else:
                await self._report_operation(
                    operation_id,
                    state="completed",
                    stage="completed",
                    status_text=message,
                    control="",
                )
        except PlexOperationCancelled:
            await self._finish_cancelled(operation_id)
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="ai_planning",
                status_text=f"Plex AI 请求失败：{type(exc).__name__}",
                control="",
            )

    async def _apply_write(self, operation_id, token, pending, service):
        try:
            result = await asyncio.to_thread(
                service.apply_operation,
                pending["action"], pending["payload"], token,
            )
            operation = self.operations[operation_id]
            if operation["cancel_event"].is_set():
                await self._report_if_active(
                    operation_id,
                    state="cancelled",
                    stage="applying_write",
                    status_text=(
                        "取消请求到达时 Plex 调用已完成；已停止后续管线，"
                        "本次 Plex 变更不自动回滚。"
                    ),
                    control="",
                    details={"completed_action": result.get("action") or ""},
                )
                return
            await self._report_operation(
                operation_id,
                state="completed",
                stage="completed",
                status_text=f"Plex 操作已执行：{result['action']}",
                control="",
            )
        except Exception as exc:
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="applying_write",
                status_text=f"Plex 写操作失败：{type(exc).__name__}",
                control="",
            )

    async def _confirm_match(self, operation_id, job_id, selection, service):
        try:
            result = await asyncio.to_thread(
                service.confirm_match,
                job_id,
                selection,
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
                stage="matching",
                status_text=f"Plex 人工匹配失败：{type(exc).__name__}",
                control="",
            )

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
        ai_config = self.config.get("ai") or {}
        if ai_config.get("enabled"):
            if all(str(ai_config.get(key) or "").strip() for key in ("api_url", "api_key", "model")):
                try:
                    from .mcp_server import PlexToolDispatcher
                    self.ai = PlexAIOrchestrator(
                        ai_config,
                        PlexToolDispatcher(service),
                        max_tool_rounds=ai_config.get("max_tool_rounds", 3),
                    )
                except Exception as exc:
                    self.ai_error = PlexManagementService._safe_error(exc)
            else:
                self.ai_error = "AI credentials are incomplete"
        mcp_config = self.config.get("mcp") or {}
        if mcp_config.get("enabled"):
            try:
                from .mcp_server import start_plex_mcp_server
                self.mcp_handle = start_plex_mcp_server(service, mcp_config)
            except Exception as exc:
                self.service_error = f"MCP isolated: {PlexManagementService._safe_error(exc)}"
        return service

    async def _run_job(self, job_id: int):
        try:
            service = await self._ensure_service()
            await asyncio.to_thread(service.run_job, job_id)
        finally:
            job = self.jobs.get(job_id)
            if job and job["state"] in {
                "running", "scanning", "locating", "matching",
                "localizing", "artwork", "streams",
            }:
                self.jobs.update(
                    job_id,
                    state="interrupted",
                    error="interrupted before completion",
                )

    async def _run_batch(self, job_ids, operation_id=""):
        try:
            service = await self._ensure_service()
            results = await asyncio.to_thread(
                service.run_batch,
                list(job_ids),
                should_cancel=(
                    lambda: self._is_cancelled(operation_id)
                ) if operation_id else None,
                on_stage=(
                    lambda stage, job: self._stage_sync(
                        operation_id, stage, job
                    )
                ) if operation_id else None,
            )
            await self._complete_batch_operation(operation_id, results)
        except PlexOperationCancelled:
            for job_id in job_ids:
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
            for job_id in job_ids:
                job = self.jobs.get(job_id)
                if job and job["state"] in {
                    "running", "scanning", "locating", "matching",
                    "localizing", "artwork", "streams",
                }:
                    self.jobs.update(
                        job_id,
                        state="interrupted",
                        error="interrupted before batch completion",
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
        waiting = [
            job for job in results
            if job.get("state") == "waiting_match_confirmation"
        ]
        if waiting:
            keyboard = []
            for job in waiting:
                self.job_operation_ids[int(job["id"])] = operation_id
                step = next((
                    value
                    for value in (job.get("step_results") or {}).values()
                    if isinstance(value, dict) and value.get("status") == "waiting"
                ), {})
                for index, candidate in enumerate(step.get("candidates") or []):
                    title = str(
                        candidate.get("title")
                        or candidate.get("name")
                        or f"候选 {index + 1}"
                    )
                    year = str(candidate.get("year") or "")
                    keyboard.append([{
                        "text": f"{title} {year}".strip()[:48],
                        "callback_data": f"plex:match:{job['id']}:{index}",
                    }])
            keyboard.append([{
                "text": "取消任务",
                "callback_data": "plex:cancel",
            }])
            return await self._report_operation(
                operation_id,
                state="awaiting_input",
                stage="manual_match",
                status_text="Plex 需要人工选择匹配候选后才能继续。",
                control="cancel",
                details={"keyboard": keyboard},
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
            status_text=(
                "Plex 任务已停止，不再执行后续步骤。"
                + (f" 已完成步骤：{'、'.join(effects)}。" if effects else "")
                + " Plex 已接受的操作不自动回滚。"
            ),
            control="",
            details={"completed_effects": effects},
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
        for token, pending in list(self.pending_writes.items()):
            if pending.get("operation_id") == operation_id:
                self.pending_writes.pop(token, None)
        if action == "exit":
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "interaction",
                status_text="已退出 Plex 交互，未执行确认写操作。",
                control="",
            )
            return {"actions": [], "operation": terminal}

        cancel_event = operation.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        task = operation.get("task")
        if operation.get("state") == "awaiting_input" or task is None:
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
        result = await self.operation_control({
            "operation_id": operation["operation_id"],
            "action": action,
            "revision": operation["revision"],
        })
        return {
            "actions": [{
                "kind": "edit_message",
                "text": result["operation"]["status_text"],
            }],
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
        if claimed:
            self.runtime.spawn(
                self._run_batch(claimed),
                task_id="plex-resume-batch",
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

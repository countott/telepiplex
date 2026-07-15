from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import PurePosixPath

from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.media_metadata import (
    MEDIA_METADATA_KEY,
    attach_media_metadata,
    extract_confirmed_media_metadata,
)

from .config_wizard import RenamingConfigWizard
from .models import DownloadCompletedEvent, PostDownloadResult
from .operations import OperationCancelled, RenameOperationJournal
from .processor import process_generic_media, process_tvdb_episode


_STORAGE_METHODS = {
    "get_file_info", "get_file_info_by_id", "get_file_list",
    "create_directory", "create_dir_recursive", "rename", "copy_file",
    "delete_single_file", "move_file", "is_directory", "get_files_from_dir",
    "move_file_detailed",
}


_STORAGE_STAGES = {
    "get_file_info": ("conflict_validation", "正在验证目标文件冲突。"),
    "get_file_info_by_id": ("planning", "正在读取文件身份。"),
    "get_file_list": ("planning", "正在构建整理计划。"),
    "get_files_from_dir": ("planning", "正在构建整理计划。"),
    "is_directory": ("planning", "正在验证目录结构。"),
    "create_directory": ("directory_preparation", "正在准备目标目录。"),
    "create_dir_recursive": ("directory_preparation", "正在准备目标目录。"),
    "rename": ("renaming", "正在重命名媒体文件。"),
    "copy_file": ("moving", "正在复制媒体文件。"),
    "move_file": ("moving", "正在移动媒体文件。"),
    "move_file_detailed": ("moving", "正在移动媒体文件。"),
    "delete_single_file": ("cleanup", "正在清理已处理的源文件。"),
}
_IRREVERSIBLE_METHODS = {
    "copy_file", "move_file", "move_file_detailed", "delete_single_file",
}


class StorageProxy:
    def __init__(
        self,
        core,
        loop,
        *,
        timeout=120,
        cancel_event=None,
        on_stage=None,
        journal=None,
    ):
        self.core = core
        self.loop = loop
        self.timeout = float(timeout)
        self.cancel_event = cancel_event
        self.on_stage = on_stage
        self.journal = journal

    def __getattr__(self, method):
        if method not in _STORAGE_METHODS:
            raise AttributeError(method)

        def call(*args, **kwargs):
            self._raise_if_cancelled()
            control = "cancel"
            if method in _IRREVERSIBLE_METHODS and self.journal is not None:
                self.journal.mark_irreversible(method)
            self._report_stage(method, control)
            self._raise_if_cancelled()

            if method in {"create_directory", "create_dir_recursive"}:
                existing = self._storage_call("get_file_info", [args[0]], {})
                value = self._storage_call(method, list(args), kwargs)
                if not existing and value and self.journal is not None:
                    self.journal.mark_irreversible("directory_created")
                self._raise_if_cancelled()
                return value

            if method == "rename":
                source_path = str(args[0])
                source_info = self._storage_call(
                    "get_file_info", [source_path], {}
                )
                value = self._storage_call(method, list(args), kwargs)
                if value is True and self.journal is not None:
                    target_path = (
                        str(PurePosixPath(source_path).parent)
                        + "/"
                        + str(args[1])
                    )
                    target_info = self._storage_call(
                        "get_file_info", [target_path], {}
                    )
                    verified = self.journal.record_rename(
                        source_path=source_path,
                        target_path=target_path,
                        source_id=self._file_id(source_info),
                        target_id=self._file_id(target_info),
                    )
                    if verified and self.journal.can_rollback:
                        self._report_stage(method, "rollback")
                self._raise_if_cancelled()
                return value

            value = self._storage_call(method, list(args), kwargs)
            self._raise_if_cancelled()
            return value

        return call

    def _storage_call(self, method, args, kwargs):
        future = asyncio.run_coroutine_threadsafe(
            self.core.call_capability(
                "storage.provider",
                method,
                {"args": args, "kwargs": kwargs},
                deadline=self.timeout,
            ),
            self.loop,
        )
        return future.result(timeout=self.timeout + 1).get("value")

    def _report_stage(self, method, control):
        if self.on_stage is None:
            return
        stage, status_text = _STORAGE_STAGES.get(
            method, ("organizing", "正在整理媒体文件。")
        )
        future = asyncio.run_coroutine_threadsafe(
            self.on_stage(stage, status_text, control, method),
            self.loop,
        )
        future.result(timeout=self.timeout + 1)

    def _raise_if_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OperationCancelled("renaming operation cancelled")

    @staticmethod
    def _file_id(value):
        if not isinstance(value, dict):
            return ""
        return str(value.get("file_id") or value.get("fid") or "")


class RenamingFeature:
    def __init__(self, *, config: dict, core, jobs=None):
        self.config = config
        self.core = core
        self.jobs = jobs
        self.config_wizard = RenamingConfigWizard(config)
        self.runtime = None
        self.operations = {}
        self.owner_operations = {}

    def bind_runtime(self, runtime):
        self.runtime = runtime

    async def command(self, request: dict) -> dict:
        if str(request.get("command") or "") != "renaming_config":
            raise FeatureError("not_found", "unknown renaming command")
        result = self.config_wizard.start(request)
        result["operation"] = self._new_operation(
            request,
            state="awaiting_input",
            stage="config_section",
            status_text="等待选择 renaming 配置项。",
            control="exit",
            kind="config",
        )
        return result

    async def callback(self, request: dict) -> dict:
        return self._decorate_config_result(
            request, self.config_wizard.callback(request)
        )

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        return {
            "actions": [{"kind": "send_message", "text": "⚠️ renaming 配置会话已失效。"}],
            "session": {"state": "close"},
        }

    async def download_completed(self, request: dict) -> dict:
        payload = request.get("payload") or {}
        job_id = str(payload.get("job_id") or request.get("event_id") or "")
        if not job_id:
            raise FeatureError("invalid_event", "renaming job identity is required")
        if self.runtime is None:
            raise FeatureError("not_ready", "renaming runtime is not ready")
        if self.jobs:
            existing = self.jobs.get(job_id)
            if existing and existing["state"] in {
                "processed", "completed", "failed", "cancelled"
            }:
                return await self._finish_operation(
                    job_id, existing["result"], None
                )
            if not self.jobs.claim(job_id):
                return {
                    "accepted": True,
                    "duplicate": True,
                    "state": (existing or {}).get("state", "processing"),
                }
            self.jobs.update(job_id, "processing", {
                "organized": False,
                "final_path": str(
                    payload.get("download_root") or payload.get("final_path") or ""
                ),
                "message": (
                    "⚠️ 整理进程在完成前中断，已停止自动重放，请人工检查。"
                ),
                "user_id": int(payload.get("user_id") or 0),
                "job_id": job_id,
            })

        operation = await self._accept_event_operation(payload, job_id)
        operation_id = operation["operation_id"] if operation else ""
        task_id = f"renaming-{job_id}"
        task = self.runtime.spawn(
            self._run_organization(job_id, dict(payload), operation_id),
            task_id=task_id,
        )
        if operation_id:
            self.operations[operation_id].update({
                "task": task,
                "task_id": task_id,
                "job_id": job_id,
            })
        result = {"accepted": True, "job_id": job_id, "state": "running"}
        if operation:
            result.update({
                "operation_id": operation_id,
                "operation": operation,
            })
        return result

    async def _run_organization(self, job_id, payload, operation_id):
        user_id = int(payload.get("user_id") or 0)
        event = None
        processing_complete = False
        try:
            self._raise_if_cancelled(operation_id)
            metadata = {}
            if isinstance(payload.get("media_metadata"), dict):
                try:
                    metadata = attach_media_metadata({}, payload["media_metadata"])
                except ValueError:
                    metadata = {MEDIA_METADATA_KEY: payload["media_metadata"]}
            naming_metadata = (
                payload.get("naming_metadata")
                if isinstance(payload.get("naming_metadata"), dict)
                else None
            )
            if not metadata:
                await self._report_if_active(
                    operation_id,
                    state="running",
                    stage="metadata_resolution",
                    status_text="正在解析媒体元数据。",
                    control="cancel",
                )
                try:
                    resolved = await self.core.call_capability(
                        "media.search",
                        "resolve_metadata",
                        {"query": self._metadata_query(payload)},
                        deadline=float(self.config.get("metadata_timeout") or 120),
                        idempotency_key=f"{job_id}:metadata",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    resolved = {}
                if isinstance(resolved.get("media_metadata"), dict):
                    try:
                        metadata = attach_media_metadata(
                            {}, resolved["media_metadata"]
                        )
                    except ValueError:
                        metadata = {}
                    if isinstance(resolved.get("naming_metadata"), dict):
                        naming_metadata = resolved["naming_metadata"]
            self._raise_if_cancelled(operation_id)
            await self._report_if_active(
                operation_id,
                state="running",
                stage="organizing",
                status_text="正在构建并执行媒体整理计划。",
                control="cancel",
            )
            loop = asyncio.get_running_loop()
            operation_state = self.operations.get(operation_id) or {}
            journal = operation_state.get("journal") or RenameOperationJournal()
            operation_state["journal"] = journal
            storage = StorageProxy(
                self.core,
                loop,
                timeout=float(self.config.get("storage_timeout") or 120),
                cancel_event=operation_state.get("cancel_event"),
                on_stage=(
                    lambda stage, text, control, method: self._storage_stage(
                        operation_id, stage, text, control, method
                    )
                ) if operation_id else None,
                journal=journal,
            )
            event = DownloadCompletedEvent(
                link=str(payload.get("link") or ""),
                selected_path=str(payload.get("selected_path") or ""),
                user_id=user_id,
                final_path=str(
                    payload.get("download_root") or payload.get("final_path") or ""
                ),
                resource_name=str(payload.get("resource_name") or ""),
                naming_metadata=naming_metadata,
                metadata=metadata,
                file_tree=(
                    payload.get("file_tree")
                    if isinstance(payload.get("file_tree"), list)
                    else None
                ),
                release=(
                    payload.get("release")
                    if isinstance(payload.get("release"), dict)
                    else None
                ),
                download_root=str(payload.get("download_root") or ""),
                provider=str(payload.get("provider") or "open115"),
                storage=storage,
            )
            if operation_id:
                operation_state["thread_started"] = True
            result = await asyncio.to_thread(self._process, event)
            self._raise_if_cancelled(operation_id)
            organized = bool(
                result.handled
                and result.final_path
                and str(result.message or "").startswith("✅")
            )
            contract = extract_confirmed_media_metadata(
                result.metadata or event.metadata
            )
            outcome = {
                "organized": organized,
                "final_path": result.final_path or event.final_path,
                "message": result.message or "",
                "user_id": user_id,
                "job_id": job_id,
                "event_payload": {
                    "job_id": job_id,
                    "user_id": user_id,
                    "chat_id": int(payload.get("chat_id") or user_id or 0),
                    "provider": event.provider,
                    "source_path": payload.get("final_path"),
                    "final_path": result.final_path,
                    "media_metadata": contract,
                },
            }
            if self.jobs:
                self.jobs.update(job_id, "processed", outcome)
            processing_complete = True
            await self._finish_operation(job_id, outcome, operation_id)
        except (asyncio.CancelledError, OperationCancelled):
            stopped_at = (
                (self.operations.get(operation_id) or {}).get("stage")
                or "organizing"
            )
            outcome = {
                "organized": False,
                "final_path": event.final_path if event else str(
                    payload.get("final_path") or ""
                ),
                "message": (
                    f"整理任务已停止；停止位置：{stopped_at}。"
                    "已完成的远端文件变更未自动回滚。"
                ),
                "user_id": user_id,
                "job_id": job_id,
            }
            if self.jobs:
                self.jobs.update(job_id, "cancelled", outcome)
            await self._report_if_active(
                operation_id,
                state="cancelled",
                stage=stopped_at,
                status_text=outcome["message"],
                control="",
                details={"stopped_at": stopped_at},
            )
        except Exception as exc:
            if processing_complete:
                raise
            stopped_at = (
                (self.operations.get(operation_id) or {}).get("stage")
                or "organizing"
            )
            outcome = {
                "organized": False,
                "final_path": event.final_path if event else str(
                    payload.get("final_path") or ""
                ),
                "message": (
                    "⚠️ 整理执行异常，已停止自动重试，请人工检查："
                    f"{type(exc).__name__}"
                ),
                "user_id": user_id,
                "job_id": job_id,
            }
            if self.jobs:
                self.jobs.update(job_id, "failed", outcome)
            await self._report_if_active(
                operation_id,
                state="failed",
                stage=stopped_at,
                status_text=outcome["message"],
                control="",
                details={"stopped_at": stopped_at},
            )
            if user_id:
                try:
                    await self.core.notify_user(
                        user_id,
                        outcome["message"],
                        idempotency_key=f"{job_id}:renaming-notice",
                    )
                except Exception:
                    pass

    async def _finish_operation(self, job_id, outcome, operation_id):
        if outcome.get("organized"):
            event_payload = outcome["event_payload"]
            if operation_id:
                handoff = await self._report_if_active(
                    operation_id,
                    state="handed_off",
                    stage="handoff_plex",
                    status_text="媒体整理完成，已交给 Plex 管理任务。",
                    control="cancel",
                    next_plugin_id="plex-management",
                )
                if handoff:
                    event_payload["operation_id"] = operation_id
                    event_payload["operation_revision"] = handoff["revision"]
            try:
                await self.core.publish_event(
                    "media.organized",
                    event_payload,
                    idempotency_key=f"{job_id}:organized",
                )
            except Exception as exc:
                await self._report_if_active(
                    operation_id,
                    state="failed",
                    stage="event_publication",
                    status_text=(
                        "媒体已整理，但 Plex 事件发布失败："
                        f"{type(exc).__name__}。"
                    ),
                    control="",
                    details={"manual_check_required": True},
                )
                raise
        else:
            await self._report_if_active(
                operation_id,
                state="completed",
                stage="completed",
                status_text=(
                    outcome.get("message")
                    or "媒体整理任务已完成，未发布 Plex 任务。"
                ),
                control="",
            )
        if outcome.get("user_id") and outcome.get("message"):
            await self.core.notify_user(
                int(outcome["user_id"]),
                outcome["message"],
                idempotency_key=f"{job_id}:renaming-notice",
            )
        if self.jobs:
            self.jobs.update(job_id, "completed", outcome)
        return {
            "accepted": True,
            "organized": bool(outcome.get("organized")),
            "final_path": outcome.get("final_path"),
            "replayed": True,
        }

    async def _accept_event_operation(self, payload, job_id):
        operation_id = str(payload.get("operation_id") or "")
        if not operation_id:
            return None
        user_id = int(payload.get("user_id") or 0)
        chat_id = int(payload.get("chat_id") or user_id or 0)
        if user_id <= 0 or chat_id == 0:
            return None
        if len(operation_id) > 40:
            raise FeatureError("invalid_operation", "operation identity is invalid")
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
            "status_text": "renaming 已接受媒体整理任务。",
            "control": "cancel",
            "revision": revision,
            "details": {},
            "kind": "organization",
            "job_id": job_id,
            "cancel_event": threading.Event(),
            "journal": RenameOperationJournal(),
        }
        self.operations[operation_id] = operation
        self.owner_operations[(chat_id, user_id)] = operation_id
        return await self._report_operation(
            operation_id,
            state="running",
            stage="metadata_resolution",
            status_text="renaming 已接受任务，正在检查媒体元数据。",
            control="cancel",
        )

    async def _storage_stage(
        self, operation_id, stage, status_text, control, method
    ):
        operation = self.operations.get(operation_id)
        if operation is None or operation.get("state") in {
            "cancelling", "rolling_back", "cancelled", "rolled_back",
            "partially_rolled_back", "failed", "completed", "handed_off",
        }:
            return
        operation.setdefault("details", {})["last_storage_method"] = method
        if (
            operation.get("stage") == stage
            and operation.get("control") == control
        ):
            return
        await self._report_operation(
            operation_id,
            state="running",
            stage=stage,
            status_text=status_text,
            control=control,
            details={"last_storage_method": method},
        )

    async def operation_control(self, request: dict) -> dict:
        operation_id = str(request.get("operation_id") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise FeatureError("not_found", "renaming operation was not found")
        if operation.get("state") in {
            "completed", "cancelled", "rolled_back",
            "partially_rolled_back", "failed",
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
        if action not in {"exit", "cancel", "rollback"}:
            raise FeatureError("invalid_control", "renaming control is invalid")
        if action != operation.get("control"):
            raise FeatureError("stale_control", "renaming control has changed")

        owner = (operation["chat_id"], operation["user_id"])
        if action == "exit" and operation.get("state") == "awaiting_input":
            self.config_wizard.sessions.pop(owner, None)
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "interaction",
                status_text="已退出 renaming 交互。",
                control="",
            )
            return {"actions": [], "operation": terminal}

        cancel_event = operation.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        if action == "rollback":
            journal = operation.get("journal")
            if journal is None or not journal.can_rollback:
                raise FeatureError(
                    "rollback_unavailable", "verified rollback is no longer available"
                )
            rolling = self._advance_operation(
                operation_id,
                state="rolling_back",
                stage=operation.get("stage") or "renaming",
                status_text="取消请求已接受，正在验证并回滚重命名。",
                control="rollback",
            )
            outcome = await journal.rollback(
                self.core,
                deadline=float(self.config.get("storage_timeout") or 120),
            )
            terminal = self._advance_operation(
                operation_id,
                state=outcome["state"],
                stage="rollback",
                status_text=(
                    "已取消并回滚全部可验证的重命名。"
                    if outcome["state"] == "rolled_back"
                    else "回滚未能完整完成，请按剩余路径人工检查。"
                ),
                control="",
                details=outcome,
            )
            return {
                "actions": [],
                "operation": terminal,
                "previous_operation": rolling,
            }

        cancelling = self._advance_operation(
            operation_id,
            state="cancelling",
            stage=operation.get("stage") or "organizing",
            status_text="取消请求已接受，将在当前存储调用结束后停止。",
            control="cancel",
            details={
                "stopped_at": operation.get("stage") or "organizing",
                "last_storage_method": (
                    (operation.get("details") or {}).get("last_storage_method") or ""
                ),
            },
        )
        task = operation.get("task")
        if (
            task is not None
            and hasattr(task, "cancel")
            and not task.done()
            and not operation.get("thread_started")
        ):
            task.cancel()
        return {"actions": [], "operation": cancelling}

    async def operation_snapshot(self, request: dict) -> dict:
        requested = str(request.get("operation_id") or "")
        terminal = {
            "completed", "cancelled", "rolled_back",
            "partially_rolled_back", "failed", "handed_off",
        }
        return {"operations": [
            self._operation_view(operation)
            for operation_id, operation in self.operations.items()
            if operation.get("state") not in terminal
            and (not requested or requested == operation_id)
        ]}

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
                status_text="正在保存并重新加载 renaming 配置。",
                control="cancel",
            )
        elif isinstance(session, dict) and session.get("state") == "open":
            wizard_session = self.config_wizard.sessions.get(owner) or {}
            view = self._advance_operation(
                operation["operation_id"],
                state="awaiting_input",
                stage=f"config_{wizard_session.get('stage') or 'input'}",
                status_text="等待 renaming 配置输入。",
                control="exit",
            )
        else:
            view = self._advance_operation(
                operation["operation_id"],
                state="cancelled",
                stage="config_cancelled",
                status_text="已退出 renaming 配置。",
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
        }
        self.operations[operation_id] = operation
        self.owner_operations[owner] = operation_id
        return self._operation_view(operation)

    def _operation_for_owner(self, owner):
        operation_id = self.owner_operations.get(owner)
        return self.operations.get(operation_id) if operation_id else None

    def _advance_operation(
        self,
        operation_id,
        *,
        state,
        stage,
        status_text,
        control,
        details=None,
        next_plugin_id="",
    ):
        operation = self.operations[operation_id]
        operation.update({
            "state": state,
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": int(operation.get("revision") or 0) + 1,
            "next_plugin_id": next_plugin_id if state == "handed_off" else "",
        })
        if details is not None:
            operation["details"] = dict(details)
        return self._operation_view(operation)

    async def _report_operation(self, operation_id, **changes):
        view = self._advance_operation(operation_id, **changes)
        await self.core.report_operation(view)
        return view

    async def _report_if_active(self, operation_id, **changes):
        if not operation_id or operation_id not in self.operations:
            return None
        current = self.operations[operation_id]
        if current.get("state") in {
            "completed", "cancelled", "rolled_back",
            "partially_rolled_back", "failed",
        }:
            return self._operation_view(current)
        return await self._report_operation(operation_id, **changes)

    def _raise_if_cancelled(self, operation_id):
        operation = self.operations.get(operation_id)
        cancel_event = operation.get("cancel_event") if operation else None
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("renaming operation cancelled")

    @staticmethod
    def _operation_view(operation):
        view = {
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
        if operation.get("next_plugin_id"):
            view["next_plugin_id"] = str(operation["next_plugin_id"])
        return view

    @staticmethod
    def _owner_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _metadata_query(payload):
        values = []
        release = payload.get("release")
        if isinstance(release, dict):
            values.append(release.get("title"))
        values.append(payload.get("resource_name"))
        for node in payload.get("file_tree") or []:
            if isinstance(node, dict) and not node.get("is_dir"):
                values.append(node.get("relative_path") or node.get("name"))
        cleaned = []
        seen = set()
        for value in values:
            value = " ".join(str(value or "").split())
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return " | ".join(cleaned)

    def _process(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        result = process_tvdb_episode(event)
        if result.handled or result.should_stop:
            return result
        result = process_generic_media(event)
        if result.handled or result.should_stop:
            return result
        return self._fallback_unorganized(event)

    def _fallback_unorganized(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        root = str(self.config.get("unorganized_path") or "").rstrip("/")
        if not root:
            return PostDownloadResult(
                True,
                final_path=event.final_path,
                message="⚠️ 无法确定整理规则，文件保持原位。",
                should_stop=True,
                metadata=event.metadata,
            )
        leaf = str(event.final_path).rstrip("/").rsplit("/", 1)[-1]
        if not event.storage.create_dir_recursive(root):
            raise RuntimeError(f"cannot create unorganized path: {root}")
        if event.storage.move_file(event.final_path, root) is not True:
            raise RuntimeError(f"cannot move release to unorganized path: {event.final_path}")
        target = f"{root}/{leaf}"
        return PostDownloadResult(
            True,
            final_path=target,
            message=f"⚠️ 无法确定整理规则，已移入未整理。\n保存目录：{target}",
            should_stop=True,
            metadata=event.metadata,
        )

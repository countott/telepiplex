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
    resolve_category_route,
)

from .config_wizard import RenameConfigWizard
from .content_probe import build_metadata_probe
from .context import runtime_context
from .inventory import (
    inventory_job_id,
    looks_organized_release,
)
from .models import DownloadCompletedEvent, PostDownloadResult
from .operations import OperationCancelled, RenameOperationJournal
from .processor import process_generic_media, process_tvdb_episode
from .query_recovery import recover_metadata_probe


_STORAGE_METHODS = {
    "get_file_info", "get_file_info_by_id", "get_file_list",
    "get_file_tree",
    "create_directory", "create_dir_recursive", "rename", "copy_file",
    "delete_single_file", "move_file", "is_directory", "get_files_from_dir",
    "move_file_detailed",
}


_STORAGE_STAGES = {
    "get_file_info": ("conflict_validation", "正在验证目标文件冲突。"),
    "get_file_info_by_id": ("planning", "正在读取文件身份。"),
    "get_file_list": ("planning", "正在构建整理计划。"),
    "get_file_tree": ("planning", "正在读取媒体文件树。"),
    "get_files_from_dir": ("planning", "正在构建整理计划。"),
    "is_directory": ("planning", "正在验证目录结构。"),
    "create_directory": ("directory_preparation", "正在准备目标目录。"),
    "create_dir_recursive": ("directory_preparation", "正在准备目标目录。"),
    "rename": ("rename", "正在重命名媒体文件。"),
    "copy_file": ("moving", "正在复制媒体文件。"),
    "move_file": ("moving", "正在移动媒体文件。"),
    "move_file_detailed": ("moving", "正在移动媒体文件。"),
    "delete_single_file": ("cleanup", "正在清理已处理的源文件。"),
}
_IRREVERSIBLE_METHODS = {
    "copy_file", "move_file", "move_file_detailed", "delete_single_file",
}


def _ambiguous_host_report_error(exc: Exception) -> bool:
    return not isinstance(exc, FeatureError) or exc.code in {
        "host_unavailable", "deadline_exceeded", "invalid_response",
    }


def _plain_notification(value) -> str:
    return str(value or "").replace("`", "")


def _safe_error_detail(exc: Exception) -> str:
    detail = str(getattr(exc, "message", "") or str(exc) or type(exc).__name__)
    return _plain_notification(detail).replace("\n", " ")[:300]


def _retryable_error(code: str) -> bool:
    code = str(code or "")
    return (
        code in {
            "busy",
            "capability_unavailable",
            "deadline_exceeded",
            "dependent_capability_lost",
            "host_unavailable",
            "metadata_source_unavailable",
            "notification_unavailable",
            "unavailable",
        }
        or code.endswith("_unavailable")
        or code.endswith("_timeout")
    )


class StorageProxy:
    def __init__(
        self,
        host,
        loop,
        *,
        timeout=120,
        cancel_event=None,
        on_stage=None,
        journal=None,
    ):
        self.host = host
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
            self.host.call_capability(
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
            raise OperationCancelled("rename operation cancelled")

    @staticmethod
    def _file_id(value):
        if not isinstance(value, dict):
            return ""
        return str(value.get("file_id") or value.get("fid") or "")


class RenameFeature:
    def __init__(self, *, config: dict, host, jobs=None):
        self.config = config
        self.host = host
        self.jobs = jobs
        self.config_wizard = RenameConfigWizard(config)
        self.runtime = None
        self.operations = {}
        self.owner_operations = {}
        self.inventory_sessions = {}

    def bind_runtime(self, runtime):
        self.runtime = runtime
        if self.jobs:
            for job in self.jobs.resumable():
                runtime.spawn(
                    self._resume_durable_job(job),
                    task_id=f"rename-resume-{job['job_id']}",
                )

    async def _resume_durable_job(self, job):
        try:
            outcome = job.get("result") or {}
            event_payload = outcome.get("event_payload") or {}
            if (
                outcome.get("inventory_batch_id")
                or event_payload.get("_inventory_batch_id")
            ):
                outcome["message"] = (
                    "存量整理批次在 Feature 重启时中断；"
                    "请重新执行 /rename 扫描，当前项可安全重试。"
                )
                if self.jobs:
                    self.jobs.update(job["job_id"], "failed", outcome)
                return
            if job.get("state") == "awaiting_metadata":
                await self._restore_metadata_confirmation(
                    job["job_id"],
                    outcome,
                )
                return
            if job.get("state") == "ready_metadata":
                payload = outcome.get("event_payload") or {}
                operation = await self._accept_event_operation(
                    payload,
                    job["job_id"],
                )
                self._spawn_organization(
                    job["job_id"],
                    payload,
                    operation["operation_id"] if operation else "",
                )
                return
            operation_id = str(event_payload.get("operation_id") or "")
            if job.get("state") == "published":
                await self._complete_published_job(job["job_id"], outcome)
                return
            if (
                operation_id
                and not outcome.get("handoff_operation")
                and operation_id not in self.operations
            ):
                await self._accept_event_operation(
                    event_payload, job["job_id"]
                )
            await self._finish_operation(
                job["job_id"], outcome, operation_id
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger = runtime_context.logger
            if logger:
                logger.warning(
                    "durable_job_resume_deferred "
                    f"job_id={job.get('job_id') or ''} "
                    f"error={type(exc).__name__}"
                )

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command == "rename":
            return self._inventory_menu(request)
        if command != "rename_config":
            raise FeatureError("not_found", "unknown rename command")
        result = self.config_wizard.start(request)
        result["operation"] = self._new_operation(
            request,
            state="awaiting_input",
            stage="config_section",
            status_text="等待选择 rename 配置项。",
            control="exit",
            kind="config",
        )
        return result

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        if payload.startswith("inventory:"):
            return await self._inventory_callback(request, payload)
        if payload.startswith("metadata:"):
            return await self._metadata_callback(request, payload)
        return self._decorate_config_result(
            request, self.config_wizard.callback(request)
        )

    def _inventory_roots(self) -> list[dict]:
        roots = [{
            "kind": str(item.get("kind") or ""),
            "name": str(item.get("name") or item.get("path") or ""),
            "path": "/" + str(item.get("path") or "").strip("/"),
            "source_kind": "category",
        } for item in self.config.get("category_folder") or [] if (
            isinstance(item, dict) and str(item.get("path") or "").strip("/")
        )]
        unorganized_path = "/" + str(
            self.config.get("unorganized_path") or ""
        ).strip("/")
        if unorganized_path != "/":
            roots.append({
                "kind": "unorganized",
                "name": "未整理",
                "path": unorganized_path,
                "source_kind": "unorganized",
            })
        return roots

    def _inventory_menu(self, request: dict) -> dict:
        if request.get("args"):
            return {
                "actions": [{"kind": "send_message", "text": "用法：/rename"}],
            }
        roots = self._inventory_roots()
        if not roots:
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "⚠️ rename 没有可扫描的分类目录或未整理目录。",
                }],
            }
        operation = self._new_operation(
            request,
            state="awaiting_input",
            stage="inventory_root_selection",
            status_text="等待选择要扫描的 115 目录。",
            control="exit",
            kind="inventory",
        )
        owner = self._owner_key(request)
        self.inventory_sessions[owner] = {
            "stage": "root_selection",
            "roots": roots,
            "operation_id": operation["operation_id"],
        }
        keyboard = [[{
            "text": root["name"],
            "callback_data": f"rename:inventory:root:{index}",
        }] for index, root in enumerate(roots)]
        keyboard.append([{
            "text": "退出",
            "callback_data": "rename:inventory:cancel",
        }])
        return {
            "actions": [{
                "kind": "send_message",
                "text": "请选择要扫描的 115 目录：",
                "data": {"keyboard": keyboard},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    async def _inventory_callback(
        self, request: dict, payload: str
    ) -> dict:
        owner = self._owner_key(request)
        session = self.inventory_sessions.get(owner)
        if not session:
            raise FeatureError(
                "invalid_state", "rename inventory session is no longer active"
            )
        operation_id = str(session.get("operation_id") or "")
        if payload == "inventory:cancel":
            operation = self.operations.get(operation_id) or {}
            cancel_event = operation.get("cancel_event")
            if cancel_event is not None:
                cancel_event.set()
            task = operation.get("task")
            if (
                task is not None
                and hasattr(task, "cancel")
                and not task.done()
                and not operation.get("thread_started")
            ):
                task.cancel()
            current_job_id = str(session.get("current_job_id") or "")
            current_job = (
                self.jobs.get(current_job_id)
                if self.jobs and current_job_id
                else None
            )
            if current_job and current_job.get("state") in {
                "awaiting_metadata", "ready_metadata", "processing"
            }:
                outcome = dict(current_job.get("result") or {})
                outcome["message"] = "用户已退出存量媒体补整理。"
                self.jobs.update(current_job_id, "cancelled", outcome)
            self.inventory_sessions.pop(owner, None)
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage="inventory_cancelled",
                status_text="已退出存量媒体扫描。",
                control="",
            )
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": "已退出存量媒体扫描。",
                }],
                "session": {"state": "close"},
                "operation": terminal,
            }
        if payload.startswith("inventory:root:"):
            if session.get("stage") != "root_selection":
                raise FeatureError(
                    "invalid_state", "rename inventory root is already selected"
                )
            try:
                root = session["roots"][int(payload.rsplit(":", 1)[1])]
            except (ValueError, IndexError):
                raise FeatureError(
                    "invalid_callback", "rename inventory root is invalid"
                ) from None
            session.update({
                "stage": "scanning",
                "root": dict(root),
            })
            operation = self.operations[operation_id]
            operation["cancel_event"] = threading.Event()
            view = self._advance_operation(
                operation_id,
                state="running",
                stage="inventory_scan",
                status_text=f"正在扫描 {root['name']} 的直接子项。",
                control="cancel",
            )
            task = self.runtime.spawn(
                self._scan_inventory(owner, operation_id),
                task_id=f"rename-inventory-scan-{operation_id}",
            )
            operation["task"] = task
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": view["status_text"],
                }],
                "session": {"state": "open"},
                "operation": view,
            }
        if payload == "inventory:confirm":
            if session.get("stage") != "confirmation":
                raise FeatureError(
                    "invalid_state", "rename inventory is not ready to start"
                )
            pending = list(session.get("pending") or [])
            if not pending:
                raise FeatureError(
                    "invalid_state", "rename inventory has no pending media"
                )
            session.update({
                "stage": "batch",
                "index": 0,
                "success": 0,
                "failed": 0,
            })
            view = self._advance_operation(
                operation_id,
                state="running",
                stage="inventory_batch",
                status_text=f"开始串行补整理，共 {len(pending)} 项。",
                control="cancel",
                details={
                    "total": len(pending),
                    "completed": 0,
                    "success": 0,
                    "failed": 0,
                },
            )
            task = self.runtime.spawn(
                self._run_inventory_batch(owner, operation_id),
                task_id=f"rename-inventory-batch-{operation_id}",
            )
            self.operations[operation_id]["task"] = task
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": view["status_text"],
                }],
                "session": {"state": "open"},
                "operation": view,
            }
        raise FeatureError(
            "invalid_callback", "rename inventory action is invalid"
        )

    async def _storage_value(self, method: str, *args, **kwargs):
        response = await self.host.call_capability(
            "storage.provider",
            method,
            {"args": list(args), "kwargs": kwargs},
            deadline=float(self.config.get("storage_timeout") or 120),
        )
        return response.get("value") if isinstance(response, dict) else None

    @staticmethod
    def _storage_items(value) -> list[dict]:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for candidate in (
                value.get("data"), value.get("list"), value.get("items")
            ):
                if isinstance(candidate, list):
                    return [
                        dict(item) for item in candidate
                        if isinstance(item, dict)
                    ]
                if isinstance(candidate, dict) and isinstance(
                    candidate.get("list"), list
                ):
                    return [
                        dict(item) for item in candidate["list"]
                        if isinstance(item, dict)
                    ]
        return []

    @staticmethod
    def _inventory_item_name(item: dict) -> str:
        return str(
            item.get("name") or item.get("file_name")
            or item.get("fn") or item.get("n") or ""
        ).strip()

    @staticmethod
    def _inventory_item_is_dir(item: dict) -> bool:
        if "is_dir" in item:
            return bool(item.get("is_dir"))
        if "file_category" in item:
            return str(item.get("file_category")) == "0"
        if "fc" in item:
            return str(item.get("fc")) != "1"
        return False

    @staticmethod
    def _inventory_item_id(item: dict) -> str:
        return str(
            item.get("file_id") or item.get("fid")
            or item.get("cid") or item.get("id") or ""
        ).strip()

    @staticmethod
    def _inventory_item_size(item: dict):
        return (
            item.get("size") or item.get("fs")
            or item.get("size_byte") or 0
        )

    async def _inventory_directory_items(self, parent_id: str) -> list[dict]:
        page_size = 1000
        offset = 0
        items = []
        seen_page_items = set()
        while True:
            page = self._storage_items(await self._storage_value(
                "get_file_list",
                {
                    "cid": str(parent_id),
                    "offset": offset,
                    "limit": page_size,
                    "show_dir": 1,
                },
            ))
            if not page:
                return items
            new_items = []
            for item in page:
                identity = (
                    self._inventory_item_id(item),
                    self._inventory_item_name(item),
                    self._inventory_item_is_dir(item),
                )
                if identity in seen_page_items:
                    continue
                seen_page_items.add(identity)
                new_items.append(item)
            if not new_items:
                raise FeatureError(
                    "inventory_tree_incomplete",
                    "storage pagination did not advance",
                )
            items.extend(new_items)
            if len(page) < page_size:
                return items
            offset += len(page)

    async def _inventory_file_tree(
        self,
        child: dict,
        source_path: str,
    ) -> list[dict]:
        root_id = self._inventory_item_id(child)
        if not root_id:
            raise FeatureError(
                "inventory_tree_incomplete",
                f"inventory folder has no file identity: {source_path}",
            )
        tree = []
        stack = [(root_id, "")]
        visited_directories = set()
        while stack:
            parent_id, prefix = stack.pop()
            if parent_id in visited_directories:
                raise FeatureError(
                    "inventory_tree_incomplete",
                    f"inventory folder cycle detected: {source_path}",
                )
            visited_directories.add(parent_id)
            descendants = await self._inventory_directory_items(parent_id)
            nested_directories = []
            for item in descendants:
                name = self._inventory_item_name(item)
                if not name:
                    continue
                relative_path = f"{prefix}/{name}".strip("/")
                is_dir = self._inventory_item_is_dir(item)
                file_id = self._inventory_item_id(item)
                tree.append({
                    **item,
                    "name": name,
                    "relative_path": relative_path,
                    "path": f"{source_path.rstrip('/')}/{relative_path}",
                    "is_dir": is_dir,
                    "file_id": file_id,
                    "size": self._inventory_item_size(item),
                })
                if is_dir:
                    if not file_id:
                        raise FeatureError(
                            "inventory_tree_incomplete",
                            f"inventory subfolder has no file identity: "
                            f"{source_path}/{relative_path}",
                        )
                    nested_directories.append((file_id, relative_path))
            stack.extend(reversed(nested_directories))
        return tree

    async def _scan_inventory(self, owner, operation_id):
        session = self.inventory_sessions.get(owner) or {}
        root = session.get("root") or {}
        counts = {"pending": 0, "completed": 0}
        pending = []
        try:
            root_info = await self._storage_value(
                "get_file_info", root.get("path")
            )
            root_id = str(
                (root_info or {}).get("file_id")
                or (root_info or {}).get("cid")
                or (root_info or {}).get("fid")
                or ""
            ).strip()
            if not root_id:
                raise FeatureError(
                    "storage_unavailable", "inventory root has no file identity"
                )
            children = await self._inventory_directory_items(root_id)
            for child in children:
                self._raise_if_cancelled(operation_id)
                name = self._inventory_item_name(child)
                if not name:
                    continue
                source_path = (
                    f"{str(root.get('path') or '').rstrip('/')}/{name}"
                )
                child_is_directory = self._inventory_item_is_dir(child)
                if child_is_directory:
                    file_tree = await self._inventory_file_tree(
                        child,
                        source_path,
                    )
                else:
                    file_tree = [{
                        **child,
                        "name": name,
                        "relative_path": name,
                        "path": source_path,
                        "is_dir": False,
                    }]
                job_id = inventory_job_id(child, source_path)
                if (
                    child_is_directory
                    and looks_organized_release(name, file_tree)
                ):
                    counts["completed"] += 1
                    continue
                pending.append({
                    "job_id": job_id,
                    "source_path": source_path,
                    "resource_name": name,
                    "file_tree": file_tree,
                    "source_file_id": str(
                        child.get("file_id") or child.get("fid")
                        or child.get("cid") or ""
                    ),
                })
                counts["pending"] += 1
            session.update({
                "stage": "confirmation",
                "pending": pending,
                "counts": counts,
            })
            keyboard = []
            if pending:
                keyboard.append([{
                    "text": f"开始补整理（{len(pending)}）",
                    "callback_data": "rename:inventory:confirm",
                }])
            keyboard.append([{
                "text": "取消",
                "callback_data": "rename:inventory:cancel",
            }])
            text = (
                f"{root.get('name') or '所选目录'}扫描完成：\n"
                f"未完成：{counts['pending']}\n"
                f"已完成：{counts['completed']}"
            )
            await self._report_if_active(
                operation_id,
                state="awaiting_input",
                stage="inventory_confirmation",
                status_text=text,
                control="exit",
                details={"keyboard": keyboard, "counts": counts},
            )
        except (asyncio.CancelledError, OperationCancelled):
            self.inventory_sessions.pop(owner, None)
            await self._report_if_active(
                operation_id,
                state="cancelled",
                stage="inventory_scan",
                status_text="存量媒体扫描已取消。",
                control="",
                details={"counts": counts},
            )
        except Exception as exc:
            self.inventory_sessions.pop(owner, None)
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="inventory_scan",
                status_text=(
                    "存量媒体扫描失败："
                    f"{getattr(exc, 'code', type(exc).__name__)}。"
                ),
                control="",
                details={"counts": counts},
            )

    def _inventory_item_payload(
        self, owner, operation_id: str, session: dict, item: dict
    ) -> dict:
        root = session.get("root") or {}
        return {
            "job_id": str(item.get("job_id") or ""),
            "chat_id": int(owner[0]),
            "user_id": int(owner[1]),
            "provider": "inventory",
            "source_path": str(item.get("source_path") or ""),
            "selected_path": (
                str(root.get("path") or "")
                if root.get("source_kind") == "category"
                else ""
            ),
            "download_root": str(item.get("source_path") or ""),
            "final_path": str(item.get("source_path") or ""),
            "resource_name": str(item.get("resource_name") or ""),
            "file_tree": list(item.get("file_tree") or []),
            "operation_id": operation_id,
            "operation_revision": int(
                (self.operations.get(operation_id) or {}).get("revision") or 0
            ),
            "_inventory_batch_id": operation_id,
            "_inventory_index": int(session.get("index") or 0),
            "_inventory_source_kind": str(root.get("source_kind") or ""),
        }

    async def _run_inventory_batch(self, owner, operation_id: str) -> None:
        session = self.inventory_sessions.get(owner)
        if not session or session.get("operation_id") != operation_id:
            return
        pending = list(session.get("pending") or [])
        try:
            while int(session.get("index") or 0) < len(pending):
                self._raise_if_cancelled(operation_id)
                index = int(session.get("index") or 0)
                item = pending[index]
                job_id = str(item.get("job_id") or "")
                job = self.jobs.get(job_id) if self.jobs else None
                if job and job.get("state") == "awaiting_metadata":
                    session["stage"] = "awaiting_metadata"
                    return
                if job and job.get("state") == "ready_metadata":
                    payload = dict(
                        (job.get("result") or {}).get("event_payload") or {}
                    )
                else:
                    if self.jobs and not self.jobs.claim_retryable(
                        job_id,
                        reopen_completed=True,
                    ):
                        session["failed"] = int(session.get("failed") or 0) + 1
                        session["index"] = index + 1
                        continue
                    payload = self._inventory_item_payload(
                        owner, operation_id, session, item
                    )
                session["stage"] = "batch"
                session["current_job_id"] = job_id
                await self._report_if_active(
                    operation_id,
                    state="running",
                    stage="inventory_batch",
                    status_text=(
                        f"正在补整理 {index + 1}/{len(pending)}："
                        f"{item.get('resource_name') or item.get('source_path')}"
                    ),
                    control="cancel",
                    details={
                        "total": len(pending),
                        "completed": index,
                        "success": int(session.get("success") or 0),
                        "failed": int(session.get("failed") or 0),
                    },
                )
                await self._run_organization(job_id, payload, operation_id)
                current = self.jobs.get(job_id) if self.jobs else None
                if current and current.get("state") == "awaiting_metadata":
                    session["stage"] = "awaiting_metadata"
                    return
                result = (current or {}).get("result") or {}
                if current and current.get("state") == "completed" and result.get(
                    "organized"
                ):
                    session["success"] = int(session.get("success") or 0) + 1
                else:
                    session["failed"] = int(session.get("failed") or 0) + 1
                session["index"] = index + 1
            session["stage"] = "completed"
            total = len(pending)
            success = int(session.get("success") or 0)
            failed = int(session.get("failed") or 0)
            text = (
                "存量媒体补整理完成。\n"
                f"成功：{success}\n失败：{failed}\n"
                f"总计：{total}"
            )
            await self._report_if_active(
                operation_id,
                state="completed",
                stage="completed",
                status_text=text,
                control="",
                details={
                    "total": total,
                    "completed": total,
                    "success": success,
                    "failed": failed,
                },
            )
            self.inventory_sessions.pop(owner, None)
        except (asyncio.CancelledError, OperationCancelled):
            self.inventory_sessions.pop(owner, None)
            await self._report_if_active(
                operation_id,
                state="cancelled",
                stage="inventory_batch",
                status_text="存量媒体补整理已停止；已完成的文件变更保持不变。",
                control="",
            )
        except Exception as exc:
            self.inventory_sessions.pop(owner, None)
            await self._report_if_active(
                operation_id,
                state="failed",
                stage="inventory_batch",
                status_text=(
                    "存量媒体补整理异常终止："
                    f"{getattr(exc, 'code', type(exc).__name__)}。"
                ),
                control="",
                details={"manual_check_required": True},
            )

    async def _metadata_callback(self, request: dict, payload: str) -> dict:
        try:
            job_id, raw_index = payload.removeprefix("metadata:").rsplit(
                ":", 1
            )
            if not job_id:
                raise ValueError
            index = int(raw_index)
        except (TypeError, ValueError):
            raise FeatureError(
                "invalid_callback",
                "rename metadata candidate is invalid",
            ) from None
        job = self.jobs.get(job_id) if self.jobs else None
        if not job or job.get("state") != "awaiting_metadata":
            raise FeatureError(
                "invalid_state",
                "rename metadata confirmation is no longer active",
            )
        outcome = job.get("result") or {}
        candidates = outcome.get("candidates") or []
        try:
            candidate = candidates[index]
        except (TypeError, IndexError):
            raise FeatureError(
                "invalid_callback",
                "rename metadata candidate is invalid",
            ) from None
        event_payload = dict(outcome.get("event_payload") or {})
        operation_id = str(event_payload.get("operation_id") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            operation = await self._accept_event_operation(
                event_payload,
                job_id,
            )
        owner = self._owner_key(request)
        if operation and owner != (
            operation["chat_id"],
            operation["user_id"],
        ):
            raise FeatureError(
                "forbidden",
                "rename metadata confirmation belongs to another user",
            )
        is_inventory = bool(
            event_payload.get("_inventory_batch_id") == operation_id
        )
        if is_inventory:
            session = self.inventory_sessions.get(owner)
            if (
                not session
                or session.get("operation_id") != operation_id
                or session.get("current_job_id") != job_id
            ):
                raise FeatureError(
                    "invalid_state", "rename inventory batch is no longer active"
                )
        resolved = await self.host.call_capability(
            "media.search",
            "confirm_metadata",
            {
                "query": outcome.get("query") or "",
                "probe": outcome.get("probe") or {},
                "candidate_ref": candidate.get("ref") or "",
            },
            deadline=float(self.config.get("metadata_timeout") or 120),
            idempotency_key=f"{job_id}:metadata:{candidate.get('ref') or index}",
        )
        if (
            not isinstance(resolved, dict)
            or resolved.get("status") not in {None, "resolved"}
            or not isinstance(resolved.get("media_metadata"), dict)
        ):
            raise FeatureError(
                "metadata_unresolved",
                "confirmed metadata candidate could not be resolved",
            )
        event_payload["media_metadata"] = resolved["media_metadata"]
        if isinstance(resolved.get("naming_metadata"), dict):
            event_payload["naming_metadata"] = resolved["naming_metadata"]
        if isinstance(resolved.get("presentation"), dict):
            event_payload["_metadata_presentation"] = resolved[
                "presentation"
            ]
        await self._publish_metadata_identity(event_payload, operation_id)
        ready = {
            **outcome,
            "event_payload": event_payload,
            "selected_candidate_ref": candidate.get("ref") or "",
        }
        if self.jobs:
            self.jobs.update(job_id, "ready_metadata", ready)
        running = await self._report_if_active(
            operation_id,
            state="running",
            stage="inventory_batch" if is_inventory else "organizing",
            status_text=(
                "正在恢复存量媒体整理任务。"
                if is_inventory
                else "正在规划媒体目录。"
            ),
            control="cancel",
            details={},
        )
        if is_inventory:
            session["stage"] = "batch"
            task = self.runtime.spawn(
                self._run_inventory_batch(owner, operation_id),
                task_id=f"rename-inventory-batch-{operation_id}",
            )
            self.operations[operation_id]["task"] = task
        else:
            self._spawn_organization(job_id, event_payload, operation_id)
        return {
            "actions": [],
            "session": {"state": "close"},
            "operation": running,
        }

    @staticmethod
    def _metadata_confirmation_view(job_id: str, outcome: dict) -> tuple[str, dict]:
        lines = ["请选择文件树对应的作品："]
        keyboard = []
        poster_items = []
        for index, candidate in enumerate(
            (outcome.get("candidates") or [])[:5]
        ):
            title = str(candidate.get("title") or "未知作品").strip()
            original = str(
                candidate.get("original_title") or ""
            ).strip()
            display_title = (
                f"{title} ({original})"
                if original and original.casefold() != title.casefold()
                else title
            )
            countries = "、".join(
                str(item).strip()
                for item in candidate.get("countries") or []
                if str(item).strip()
            ) or "地区未知"
            media_type = str(
                candidate.get("media_type_label") or ""
            ).strip()
            if media_type not in {
                "电影", "剧集", "动画电影", "动画剧集",
            }:
                media_type = (
                    "电影"
                    if candidate.get("media_type") == "movie"
                    else "剧集"
                )
            lines.append(
                f"{index + 1}. {display_title}\n"
                f"   {candidate.get('year') or '年份未知'}"
                f"｜{countries}｜{media_type}"
            )
            keyboard.append([{
                "text": f"{index + 1}. {title[:24]}",
                "callback_data": f"rename:metadata:{job_id}:{index}",
            }])
            poster_items.append({
                "number": index + 1,
                "title": title,
                "poster_url": str(
                    candidate.get("poster_url") or ""
                ),
            })
        details = {"keyboard": keyboard}
        if any(
            item["poster_url"].startswith("https://")
            for item in poster_items
        ):
            details["poster_items"] = poster_items
        return "\n".join(lines), details

    async def _restore_metadata_confirmation(
        self,
        job_id: str,
        outcome: dict,
    ) -> None:
        payload = outcome.get("event_payload") or {}
        operation_id = str(payload.get("operation_id") or "")
        if operation_id not in self.operations:
            await self._accept_event_operation(payload, job_id)
        text, details = self._metadata_confirmation_view(
            job_id,
            outcome,
        )
        await self._report_if_active(
            operation_id,
            state="awaiting_input",
            stage="metadata_confirmation",
            status_text=text,
            control="exit",
            details=details,
        )

    async def _await_metadata_confirmation(
        self,
        job_id: str,
        payload: dict,
        operation_id: str,
        resolved: dict,
    ) -> None:
        outcome = {
            "event_payload": dict(payload),
            "query": str(resolved.get("query") or ""),
            "probe": dict(resolved.get("probe") or {}),
            "candidates": list(resolved.get("candidates") or [])[:5],
            "organized": False,
            "final_path": str(
                payload.get("download_root")
                or payload.get("final_path")
                or ""
            ),
            "user_id": int(payload.get("user_id") or 0),
            "job_id": job_id,
        }
        if self.jobs:
            self.jobs.update(job_id, "awaiting_metadata", outcome)
        text, details = self._metadata_confirmation_view(
            job_id,
            outcome,
        )
        await self._report_if_active(
            operation_id,
            state="awaiting_input",
            stage="metadata_confirmation",
            status_text=text,
            control="exit",
            details=details,
        )

    async def message(self, request: dict) -> dict:
        if self.config_wizard.has_session(request):
            return self._decorate_config_result(
                request, self.config_wizard.message(request)
            )
        if self._owner_key(request) in self.inventory_sessions:
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "请使用当前 rename 存量整理面板中的按钮。",
                }],
                "session": {"state": "open"},
            }
        return {
            "actions": [{"kind": "send_message", "text": "⚠️ rename 配置会话已失效。"}],
            "session": {"state": "close"},
        }

    async def download_completed(self, request: dict) -> dict:
        payload = request.get("payload") or {}
        job_id = str(payload.get("job_id") or request.get("event_id") or "")
        if not job_id:
            raise FeatureError("invalid_event", "rename job identity is required")
        if self.runtime is None:
            raise FeatureError("not_ready", "rename runtime is not ready")
        if self.jobs:
            existing = self.jobs.get(job_id)
            if existing and existing["state"] in {
                "processed", "published", "completed", "failed", "cancelled"
            }:
                stored_payload = (
                    (existing.get("result") or {}).get("event_payload") or {}
                )
                requested_operation_id = str(payload.get("operation_id") or "")
                stored_operation_id = str(
                    stored_payload.get("operation_id") or ""
                )
                if (
                    requested_operation_id
                    and stored_operation_id != requested_operation_id
                ):
                    accepted = await self._accept_event_operation(
                        payload, job_id
                    )
                    if accepted:
                        await self._report_if_active(
                            requested_operation_id,
                            state="interrupted",
                            stage="replay_identity_check",
                            status_text=(
                                "持久化结果缺少匹配的协调任务身份；"
                                "已停止自动发布后续 Plex 任务。"
                            ),
                            control="",
                            details={"manual_check_required": True},
                        )
                    return {
                        "accepted": True,
                        "duplicate": True,
                        "state": "interrupted",
                        "message": (
                            "协调任务身份未在处理结果中完整持久化；"
                            "已停止自动发布后续 Plex 任务。"
                        ),
                    }
                outcome = existing.get("result") or {}
                if existing["state"] in {"completed", "failed", "cancelled"}:
                    return {
                        "accepted": True,
                        "duplicate": True,
                        "state": existing["state"],
                        "organized": bool(outcome.get("organized")),
                        "final_path": outcome.get("final_path"),
                    }
                if existing["state"] == "published":
                    return await self._complete_published_job(job_id, outcome)
                operation_id = stored_operation_id
                if (
                    operation_id
                    and operation_id not in self.operations
                    and not outcome.get("handoff_reported")
                    and not outcome.get("handoff_operation")
                ):
                    restored = await self._accept_event_operation(
                        stored_payload, job_id
                    )
                    operation_id = (
                        restored["operation_id"] if restored else ""
                    )
                return await self._finish_operation(job_id, outcome, operation_id)
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

        try:
            operation = await self._accept_event_operation(payload, job_id)
        except Exception as exc:
            operation_id = str(payload.get("operation_id") or "")
            operation = self.operations.get(operation_id)
            if operation is not None and _ambiguous_host_report_error(exc):
                operation["ownership_pending"] = True
                operation["ownership_report"] = self._operation_view(operation)
                self._spawn_organization(job_id, payload, operation_id)
                return {
                    "accepted": True,
                    "job_id": job_id,
                    "state": "running",
                    "report_pending": True,
                    "operation_id": operation_id,
                    "operation": self._operation_view(operation),
                }
            if (
                operation is not None
                and isinstance(exc, FeatureError)
                and exc.code == "operation_rejected"
            ):
                if self.jobs:
                    self.jobs.update(job_id, "cancelled", {
                        "organized": False,
                        "final_path": str(
                            payload.get("download_root")
                            or payload.get("final_path") or ""
                        ),
                        "message": "Host 已结束协调任务，未开始媒体文件变更。",
                        "user_id": int(payload.get("user_id") or 0),
                        "job_id": job_id,
                    })
                return {
                    "accepted": True,
                    "duplicate": True,
                    "state": "interrupted",
                    "operation_id": operation_id,
                    "operation": self._operation_view(operation),
                }
            raise
        operation_id = operation["operation_id"] if operation else ""
        task = self._spawn_organization(job_id, payload, operation_id)
        result = {"accepted": True, "job_id": job_id, "state": "running"}
        if operation:
            result.update({
                "operation_id": operation_id,
                "operation": operation,
            })
        return result

    def _spawn_organization(self, job_id, payload, operation_id):
        task_id = f"rename-{job_id}"
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
        return task

    async def _run_organization(self, job_id, payload, operation_id):
        user_id = int(payload.get("user_id") or 0)
        is_inventory = bool(
            payload.get("_inventory_batch_id") == operation_id
        )
        event = None
        processing_complete = False
        try:
            self._raise_if_cancelled(operation_id)
            await self._confirm_operation_ownership(operation_id)
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
                    probe = await asyncio.to_thread(
                        recover_metadata_probe,
                        build_metadata_probe(payload),
                    )
                    if (
                        not str(probe.get("identity_query") or "").strip()
                        or probe.get("requires_recovery") is True
                    ):
                        raise FeatureError(
                            "metadata_query_unresolved",
                            "file tree did not provide an evidence-bound "
                            "metadata query",
                        )
                    resolved = await self.host.call_capability(
                        "media.search",
                        "resolve_metadata",
                        {
                            "query": probe["identity_query"],
                            "probe": probe,
                        },
                        deadline=float(self.config.get("metadata_timeout") or 120),
                        idempotency_key=f"{job_id}:metadata",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise
                if resolved.get("status") == "confirmation_required":
                    await self._await_metadata_confirmation(
                        job_id,
                        payload,
                        operation_id,
                        resolved,
                    )
                    return
                if resolved.get("status") == "unresolved":
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
                    presentation = resolved.get("presentation")
                    if isinstance(presentation, dict):
                        payload["_metadata_presentation"] = presentation
            if (
                is_inventory
                and payload.get("_inventory_source_kind") == "unorganized"
                and metadata
            ):
                contract = extract_confirmed_media_metadata(metadata)
                placement = (
                    contract.get("placement")
                    if isinstance(contract, dict)
                    else None
                )
                route = resolve_category_route(
                    {"category_folder": self.config.get("category_folder") or []},
                    str((placement or {}).get("category_kind") or ""),
                )
                if not route:
                    raise FeatureError(
                        "category_route_missing",
                        "confirmed metadata has no configured category route",
                    )
                payload["selected_path"] = route["path"]
            if metadata:
                await self._publish_metadata_identity(payload, operation_id)
            self._raise_if_cancelled(operation_id)
            await self._report_if_active(
                operation_id,
                state="running",
                stage="organizing",
                status_text="正在规划媒体目录。",
                control="cancel",
            )
            loop = asyncio.get_running_loop()
            operation_state = self.operations.get(operation_id) or {}
            journal = operation_state.get("journal") or RenameOperationJournal()
            operation_state["journal"] = journal
            storage = StorageProxy(
                self.host,
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
                provider=str(payload.get("provider") or "download"),
                storage=storage,
            )
            if operation_id:
                operation_state["thread_started"] = True
            if metadata:
                processor = self._process
            elif is_inventory:
                processor = self._inventory_unresolved
            else:
                processor = self._fallback_unorganized
            result = await asyncio.to_thread(processor, event)
            self._raise_if_cancelled(operation_id)
            organized = bool(
                result.handled
                and result.final_path
                and str(result.message or "").startswith("✅")
            )
            contract = extract_confirmed_media_metadata(
                result.metadata or event.metadata
            )
            event_payload = {
                "job_id": job_id,
                "user_id": user_id,
                "chat_id": int(payload.get("chat_id") or user_id or 0),
                "provider": event.provider,
                "source_path": (
                    payload.get("source_path") or payload.get("final_path")
                ),
                "final_path": result.final_path,
                "media_metadata": contract,
            }
            if not is_inventory:
                event_payload.update({
                    "operation_id": operation_id,
                    "operation_revision": int(
                        operation_state.get("revision") or 0
                    ),
                })
            outcome = {
                "organized": organized,
                "final_path": result.final_path or event.final_path,
                "message": result.message or "",
                "user_id": user_id,
                "job_id": job_id,
                "event_payload": event_payload,
            }
            if is_inventory:
                outcome["inventory_batch_id"] = operation_id
            if self.jobs:
                self.jobs.update(job_id, "processed", outcome)
            processing_complete = True
            if is_inventory:
                await self._finish_inventory_item(job_id, outcome)
            else:
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
            if (self.operations.get(operation_id) or {}).get("state") != "rolling_back":
                await self._report_if_active(
                    operation_id,
                    state="cancelled",
                    stage=stopped_at,
                    status_text=outcome["message"],
                    control="",
                    details={"stopped_at": stopped_at},
                )
        except Exception as exc:
            if (
                isinstance(exc, FeatureError)
                and exc.code == "ownership_rejected"
            ):
                outcome = {
                    "organized": False,
                    "final_path": str(payload.get("final_path") or ""),
                    "message": "Host 已结束协调任务，未开始媒体文件变更。",
                    "user_id": user_id,
                    "job_id": job_id,
                }
                if self.jobs:
                    self.jobs.update(job_id, "cancelled", outcome)
                operation = self.operations.get(operation_id)
                if operation is not None:
                    operation.update({
                        "state": "interrupted",
                        "status_text": outcome["message"],
                        "control": "",
                    })
                return
            if processing_complete:
                raise
            stopped_at = (
                (self.operations.get(operation_id) or {}).get("stage")
                or "organizing"
            )
            error_code = str(
                getattr(exc, "code", "") or type(exc).__name__
            )
            error_detail = _safe_error_detail(exc)
            retryable = _retryable_error(error_code)
            error_details = {
                "error_code": error_code,
                "error_stage": stopped_at,
                "error_detail": error_detail,
                "retryable": retryable,
                "stopped_at": stopped_at,
            }
            if stopped_at == "metadata_resolution":
                error_message = (
                    "⚠️ 元数据解析失败，未移动媒体文件："
                    f"{error_code}（{error_detail}）"
                )
            else:
                error_message = (
                    "⚠️ 整理执行异常，已停止自动重试，请人工检查："
                    f"{error_code}（{error_detail}）"
                )
            outcome = {
                "organized": False,
                "final_path": event.final_path if event else str(
                    payload.get("final_path") or ""
                ),
                "message": error_message,
                "user_id": user_id,
                "job_id": job_id,
                "error": error_details,
            }
            if self.jobs:
                self.jobs.update(job_id, "failed", outcome)
            if is_inventory:
                return
            await self._report_if_active(
                operation_id,
                state="failed",
                stage=stopped_at,
                status_text=outcome["message"],
                control="",
                details=error_details,
            )
            if user_id:
                try:
                    await self.host.notify_user(
                        user_id,
                        outcome["message"],
                        idempotency_key=f"{job_id}:rename-notice",
                    )
                except Exception:
                    pass

    async def _finish_inventory_item(self, job_id: str, outcome: dict) -> None:
        if outcome.get("organized"):
            try:
                await self.host.publish_event(
                    "media.organized",
                    outcome["event_payload"],
                    idempotency_key=(
                        f"{job_id}:organized:"
                        f"{outcome.get('inventory_batch_id') or 'inventory'}"
                    ),
                )
            except Exception as exc:
                outcome["downstream_error"] = type(exc).__name__
                outcome["message"] = (
                    str(outcome.get("message") or "").rstrip()
                    + "\nPlex 事件发布失败，请人工检查。"
                ).lstrip()
        if self.jobs:
            self.jobs.update(
                job_id,
                "completed" if outcome.get("organized") else "failed",
                outcome,
            )

    async def _finish_operation(self, job_id, outcome, operation_id):
        if outcome.get("organized"):
            event_payload = outcome["event_payload"]
            if operation_id:
                handoff = outcome.get("handoff_operation")
                if not isinstance(handoff, dict):
                    handoff = self._advance_operation(
                        operation_id,
                        state="handed_off",
                        stage="handoff_plex",
                        status_text="媒体整理完成，已交给 Plex 管理任务。",
                        control="cancel",
                        next_plugin_id="sync",
                    )
                    event_payload["operation_id"] = operation_id
                    event_payload["operation_revision"] = handoff["revision"]
                    outcome["handoff_operation"] = dict(handoff)
                    if self.jobs:
                        self.jobs.update(job_id, "processed", outcome)
                if not outcome.get("handoff_reported"):
                    response = await self.host.report_operation(handoff)
                    if (
                        not isinstance(response, dict)
                        or response.get("accepted") is not True
                    ):
                        if (
                            isinstance(response, dict)
                            and response.get("error_code")
                            == "handoff_target_unavailable"
                            and response.get("target_plugin_id") == "sync"
                        ):
                            outcome.pop("handoff_operation", None)
                            outcome["downstream_skipped"] = "sync"
                            outcome["message"] = (
                                str(outcome.get("message") or "").rstrip()
                                + "\nPlex 管理未安装，已跳过后续处理。"
                            ).lstrip()
                            await self._report_operation(
                                operation_id,
                                state="completed",
                                stage="completed",
                                status_text=(
                                    "媒体整理完成；Plex 管理未安装，"
                                    "已跳过后续处理。"
                                ),
                                control="",
                                details={"downstream_skipped": "sync"},
                            )
                            if self.jobs:
                                self.jobs.update(
                                    job_id, "published", outcome
                                )
                            return await self._complete_published_job(
                                job_id, outcome
                            )
                        raise FeatureError(
                            "operation_rejected",
                            "Host rejected rename handoff ownership",
                        )
                seal_response = await self.host.seal_operation_stage(
                    operation_id,
                    f"rename-stage-complete:{job_id}",
                    (
                        "✅ 媒体整理已完成。\n"
                        f"目标目录：{outcome.get('final_path') or ''}"
                    ),
                    deadline=45,
                )
                if not isinstance(seal_response, dict) or not (
                    seal_response.get("accepted") is True
                    or seal_response.get("duplicate") is True
                ):
                    raise FeatureError(
                        "stage_seal_failed",
                        "Host did not seal the completed rename stage",
                    )
                outcome["handoff_reported"] = True
                if self.jobs:
                    self.jobs.update(job_id, "processed", outcome)
            try:
                await self.host.publish_event(
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
        if self.jobs:
            self.jobs.update(job_id, "published", outcome)
        return await self._complete_published_job(job_id, outcome)

    async def _complete_published_job(self, job_id, outcome):
        if outcome.get("user_id") and outcome.get("message"):
            await self.host.notify_user(
                int(outcome["user_id"]),
                _plain_notification(outcome["message"]),
                idempotency_key=f"{job_id}:rename-notice",
            )
        if self.jobs:
            self.jobs.update(job_id, "completed", outcome)
        return {
            "accepted": True,
            "duplicate": True,
            "organized": bool(outcome.get("organized")),
            "final_path": outcome.get("final_path"),
            "replayed": True,
        }

    async def _publish_metadata_identity(
        self,
        payload: dict,
        operation_id: str,
    ) -> bool:
        if not operation_id or payload.get("_metadata_identity_published"):
            return False
        presentation = payload.get("_metadata_presentation")
        if not isinstance(presentation, dict):
            return False
        milestone_id = str(presentation.get("milestone_id") or "").strip()
        text = str(presentation.get("text") or "").strip()
        if not milestone_id or not text:
            return False
        response = await self.host.publish_operation_milestone(
            operation_id,
            milestone_id,
            text,
            mode="identity",
            photo_url=str(presentation.get("photo_url") or ""),
            deadline=45,
        )
        if not isinstance(response, dict) or not (
            response.get("accepted") is True
            or response.get("duplicate") is True
        ):
            raise FeatureError(
                "identity_delivery_failed",
                "Host did not deliver the confirmed media identity",
            )
        payload["_metadata_identity_published"] = True
        return True

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
            "status_text": "rename 已接受媒体整理任务。",
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
        has_metadata = isinstance(payload.get("media_metadata"), dict)
        return await self._report_operation(
            operation_id,
            state="running",
            stage="organizing" if has_metadata else "metadata_resolution",
            status_text=(
                "正在规划媒体目录。"
                if has_metadata
                else "rename 已接受任务，正在解析媒体元数据。"
            ),
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
            raise FeatureError("not_found", "rename operation was not found")
        if operation.get("state") in {
            "completed", "cancelled", "rolled_back",
            "partially_rolled_back", "failed",
        }:
            return {"actions": [], "operation": self._operation_view(operation)}
        if operation.get("state") in {"cancelling", "rolling_back"}:
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
            raise FeatureError("invalid_control", "rename control is invalid")
        if action != operation.get("control"):
            raise FeatureError("stale_control", "rename control has changed")

        owner = (operation["chat_id"], operation["user_id"])
        if action == "exit" and operation.get("state") == "awaiting_input":
            self.config_wizard.sessions.pop(owner, None)
            inventory_session = self.inventory_sessions.pop(owner, None)
            if inventory_session:
                cancel_event = operation.get("cancel_event")
                if cancel_event is not None:
                    cancel_event.set()
                current_job_id = str(
                    inventory_session.get("current_job_id") or ""
                )
                current_job = (
                    self.jobs.get(current_job_id)
                    if self.jobs and current_job_id
                    else None
                )
                if current_job and current_job.get("state") in {
                    "awaiting_metadata", "ready_metadata", "processing"
                }:
                    outcome = dict(current_job.get("result") or {})
                    outcome["message"] = "用户已退出存量媒体补整理。"
                    self.jobs.update(current_job_id, "cancelled", outcome)
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "interaction",
                status_text="已退出 rename 交互。",
                control="",
            )
            return {"actions": [], "operation": terminal}

        cancel_event = operation.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        if operation.get("state") == "handed_off":
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "handoff_plex",
                status_text=(
                    "已取消尚未被下游接受的后续 Plex 任务；"
                    "已完成的媒体文件变更保持不变。"
                ),
                control="",
                details={
                    "stopped_at": operation.get("stage") or "handoff_plex",
                    "completed_media_changes": "preserved",
                },
            )
            return {"actions": [], "operation": terminal}
        if action == "rollback":
            journal = operation.get("journal")
            if journal is None or not journal.can_rollback:
                raise FeatureError(
                    "rollback_unavailable", "verified rollback is no longer available"
                )
            rolling = await self._report_operation(
                operation_id,
                state="rolling_back",
                stage=operation.get("stage") or "rename",
                status_text="取消请求已接受，正在验证并回滚重命名。",
                control="",
            )
            forward_task = operation.get("task")
            rollback_task = self.runtime.spawn(
                self._rollback_after_forward_stop(
                    operation_id, journal, forward_task
                ),
                task_id=f"rename-rollback-{operation_id}",
            )
            operation["rollback_task"] = rollback_task
            return {"actions": [], "operation": rolling}

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

    async def _rollback_after_forward_stop(
        self, operation_id, journal, forward_task
    ):
        if forward_task is not None and not forward_task.done():
            try:
                await asyncio.shield(forward_task)
            except asyncio.CancelledError:
                raise
            except (OperationCancelled, Exception):
                pass
        try:
            outcome = await journal.rollback(
                self.host,
                deadline=float(self.config.get("storage_timeout") or 120),
            )
        except Exception as exc:
            outcome = {
                "state": "partially_rolled_back",
                "restored": [],
                "remaining": [
                    inverse.target_path
                    for inverse in getattr(journal, "inverses", ())
                ],
                "error": type(exc).__name__,
            }
        await self._report_operation(
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
                status_text="正在保存并重新加载 rename 配置。",
                control="cancel",
            )
        elif isinstance(session, dict) and session.get("state") == "open":
            wizard_session = self.config_wizard.sessions.get(owner) or {}
            view = self._advance_operation(
                operation["operation_id"],
                state="awaiting_input",
                stage=f"config_{wizard_session.get('stage') or 'input'}",
                status_text="等待 rename 配置输入。",
                control="exit",
            )
        else:
            view = self._advance_operation(
                operation["operation_id"],
                state="cancelled",
                stage="config_cancelled",
                status_text="已退出 rename 配置。",
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
        response = await self.host.report_operation(view)
        if not isinstance(response, dict) or response.get("accepted") is not True:
            operation = self.operations[operation_id]
            operation.update({
                "state": "interrupted",
                "status_text": "Host 未接受当前 Feature 的任务所有权。",
                "control": "",
                "next_plugin_id": "",
            })
            raise FeatureError(
                "operation_rejected",
                "Host rejected rename operation ownership",
            )
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

    async def _confirm_operation_ownership(self, operation_id):
        operation = self.operations.get(operation_id)
        if operation is None or not operation.get("ownership_pending"):
            return
        report = dict(
            operation.get("ownership_report")
            or self._operation_view(operation)
        )
        retries = max(1, int(
            self.config.get("operation_confirmation_retries") or 3
        ))
        response = None
        for attempt in range(retries):
            self._raise_if_cancelled(operation_id)
            try:
                response = await self.host.report_operation(report)
                break
            except Exception as exc:
                if not _ambiguous_host_report_error(exc):
                    raise
                if attempt + 1 >= retries:
                    raise
                await asyncio.sleep(float(
                    self.config.get("operation_confirmation_interval") or 0.1
                ))
        if not isinstance(response, dict) or response.get("accepted") is not True:
            raise FeatureError(
                "ownership_rejected",
                "Host did not confirm rename operation ownership",
            )
        operation["ownership_pending"] = False
        operation.pop("ownership_report", None)

    def _raise_if_cancelled(self, operation_id):
        operation = self.operations.get(operation_id)
        cancel_event = operation.get("cancel_event") if operation else None
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("rename operation cancelled")

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

    def _process(self, event: DownloadCompletedEvent) -> PostDownloadResult:
        result = process_tvdb_episode(event)
        if result.handled or result.should_stop:
            return result
        result = process_generic_media(event)
        if result.handled or result.should_stop:
            return result
        return self._fallback_unorganized(event)

    @staticmethod
    def _inventory_unresolved(
        event: DownloadCompletedEvent,
    ) -> PostDownloadResult:
        return PostDownloadResult(
            True,
            final_path=event.final_path,
            message="⚠️ 元数据反查未确认媒体身份，文件保持原位。",
            should_stop=True,
            metadata=event.metadata,
        )

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
        move = event.storage.move_file_detailed(event.final_path, root)
        state = str(
            move.get("state") if isinstance(move, dict) else ""
        )
        if state not in {"moved", "copied_source_retained"}:
            raise RuntimeError(
                "cannot move release to unorganized path: "
                f"{event.final_path} ({state or 'invalid_result'})"
            )
        target = str(
            move.get("target_path") if isinstance(move, dict) else ""
        ) or f"{root}/{leaf}"
        if state == "copied_source_retained":
            message = (
                "⚠️ 无法确定整理规则，已复制到未整理；"
                "源文件仍保留，请人工检查后清理。"
                f"\n保存目录：{target}"
            )
        else:
            message = f"⚠️ 无法确定整理规则，已移入未整理。\n保存目录：{target}"
        return PostDownloadResult(
            True,
            final_path=target,
            message=message,
            should_stop=True,
            metadata=event.metadata,
        )

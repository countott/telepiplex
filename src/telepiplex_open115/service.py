from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import re
import threading
import uuid

from telepiplex_plugin_sdk import FeatureError

from .context import logger
from .directories import (
    normalize_save_directories,
    normalize_save_directory_path,
)


_MAGNET = re.compile(r"^magnet:\?xt=urn:btih:(?:[A-Fa-f0-9]{40}|[A-Za-z2-7]{32})(?:&.*)?$")
SESSION_TTL_SECONDS = 30 * 60
DIRECTORY_PAGE_SIZE = 5
OPERATION_TERMINAL_STATES = {
    "completed", "cancelled", "failed", "rolled_back",
    "partially_rolled_back", "interrupted",
}
_STORAGE_METHODS = {
    "get_file_info",
    "get_file_info_by_id",
    "get_file_list",
    "create_directory",
    "create_dir_recursive",
    "rename",
    "copy_file",
    "delete_single_file",
    "move_file",
    "move_file_detailed",
    "is_directory",
    "get_files_from_dir",
    "get_file_tree",
}


def _link_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _single_secret(value: str) -> str:
    value = str(value or "").strip().strip("`").strip('"').strip("'")
    if (
        not value
        or "\n" in value
        or "\r" in value
        or value.lower().startswith("your_")
    ):
        raise ValueError("invalid secret")
    return value


def _ambiguous_core_report_error(exc: Exception) -> bool:
    return not isinstance(exc, FeatureError) or exc.code in {
        "core_unavailable", "deadline_exceeded", "invalid_response",
    }


class TokenPersistenceCancelled(RuntimeError):
    def __init__(self, rollback_verified: bool):
        super().__init__("token persistence cancelled")
        self.rollback_verified = rollback_verified


class TokenPersistenceFailed(RuntimeError):
    def __init__(self, rollback_verified: bool):
        super().__init__("token persistence failed")
        self.rollback_verified = rollback_verified


class Open115Feature:
    def __init__(self, *, config: dict, core, client, jobs=None, config_store=None):
        self.config = config
        self.core = core
        self.client = client
        self.runtime = None
        self.sessions = {}
        self.session_expiry_handles = {}
        self.active_job_ids = set()
        self.operations = {}
        self.jobs = jobs
        self.config_store = config_store

    def bind_runtime(self, runtime):
        self.runtime = runtime
        if self.jobs:
            for job in self.jobs.resumable():
                self._restore_downloaded_operation(job)
                runtime.spawn(self._publish_downloaded(job), task_id=job["job_id"])

    async def download_capability(self, request: dict) -> dict:
        if request.get("method") != "submit":
            raise FeatureError("method_not_allowed", "download provider method is not allowed")
        return await self._start_download(
            request.get("payload") or {}, request.get("context") or {}
        )

    async def storage_capability(self, request: dict) -> dict:
        method = str(request.get("method") or "")
        if method not in _STORAGE_METHODS:
            raise FeatureError("method_not_allowed", f"storage method is not allowed: {method}")
        payload = request.get("payload") or {}
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise FeatureError("invalid_request", "storage args/kwargs are invalid")
        value = await asyncio.to_thread(getattr(self.client, method), *args, **kwargs)
        return {"value": value}

    async def command(self, request: dict) -> dict:
        command = str(request.get("command") or "")
        if command in {"magnet", "m"}:
            link = " ".join(str(item) for item in request.get("args") or []).strip()
            if not _MAGNET.fullmatch(link):
                return self._message("用法：/magnet <magnet链接>")
            directories = self.config.get("save_directories") or []
            if not directories:
                return self._message("⚠️ open115 配置中没有 save_directories。")
            key = self._session_key(request)
            self._clear_auth_session(key)
            operation = self._new_operation(
                request,
                stage="destination_selection",
                status_text="等待选择 115 保存目录。",
                control="exit",
            )
            self.sessions[key] = {
                "link": link,
                "stage": "path",
                "operation_id": operation["operation_id"],
            }
            keyboard = [[{
                "text": f"📁 {item['name']}",
                "callback_data": f"open115:path:{index}",
            }] for index, item in enumerate(directories)]
            keyboard.append(self._exit_row())
            return {
                "actions": [{
                    "kind": "send_message",
                    "text": "请选择保存目录：",
                    "data": {"keyboard": keyboard},
                }],
                "session": {"state": "open"},
                "operation": operation,
            }
        if command == "q":
            key = self._session_key(request)
            operation = self._close_interaction(key, "已退出当前交互。")
            result = {
                "actions": [{"kind": "send_message", "text": "已取消。"}],
                "session": {"state": "close"},
            }
            if operation is not None:
                result["operation"] = operation
            return result
        if command == "config":
            return self._start_config_session(request)
        if command == "auth":
            return self._start_auth_session(request)
        raise FeatureError("not_found", f"unknown open115 command: {command}")

    async def callback(self, request: dict) -> dict:
        payload = str(request.get("payload") or "")
        key = self._session_key(request)
        if payload == "exit":
            operation = self._close_interaction(key, "已退出当前交互。")
            result = self._message_with_session("已退出。", "close")
            if operation is not None:
                result["operation"] = operation
            return result
        if payload.startswith("config:"):
            return await self._config_callback(request, payload, key)
        if payload == "auth:direct":
            session = self.sessions.get(key)
            if not session or session.get("stage") != "choose_mode":
                return self._message_with_session("⚠️ 会话已失效。", "close")
            operation_id = session["operation_id"]
            self.sessions[key] = {
                "stage": "access_token",
                "operation_id": operation_id,
            }
            operation = self._advance_operation(
                operation_id,
                state="awaiting_input",
                stage="access_token",
                status_text="等待 Access token。",
                control="exit",
            )
            return self._interaction_message(
                key, "请发送 Access token。", kind="edit_message", operation=operation
            )
        if payload == "auth:scan":
            session = self.sessions.get(key)
            if not session or session.get("stage") != "choose_mode":
                return self._message_with_session("⚠️ 会话已失效。", "close")
            return await self._start_scan_auth(request)
        session = self.sessions.get(key)
        if (
            not session
            or session.get("stage") != "path"
            or not payload.startswith("path:")
        ):
            return {"actions": [{"kind": "send_message", "text": "⚠️ 会话已失效。"}], "session": {"state": "close"}}
        try:
            directories = self.config.get("save_directories") or []
            index = self._bounded_index(
                directories, payload.split(":", 1)[1]
            )
            directory = directories[index]
        except (IndexError, TypeError, ValueError):
            operation = self._close_interaction(key, "保存目录不可用。")
            result = self._message_with_session("⚠️ 保存目录不可用。", "close")
            if operation is not None:
                result["operation"] = operation
            return result
        self.sessions.pop(key, None)
        operation_id = session.get("operation_id")
        operation = self.operations.get(operation_id)
        result = await self._start_download({
            "link": session["link"],
            "selected_path": directory["path"],
            "user_id": request.get("user_id"),
            "chat_id": request.get("chat_id"),
            "operation_id": operation_id,
            "operation_revision": (
                int(operation.get("revision") or 0) if operation else 0
            ),
        }, {
            "idempotency_key": (
                f"telegram:{request.get('update_id') or uuid.uuid4().hex}"
            ),
        })
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"✅ 已加入 115 下载队列：{result['job_id']}",
            }],
            "session": {"state": "close"},
            "operation": result["operation"],
        }

    async def message(self, request: dict) -> dict:
        key = self._session_key(request)
        session = self.sessions.get(key)
        if not session:
            return self._message_with_session("⚠️ 会话已失效。", "close")

        stage = session.get("stage")
        if stage.startswith("directory_"):
            return self._directory_message(request, key, session)
        if stage == "access_token":
            try:
                access_token = _single_secret(request.get("text"))
            except ValueError:
                operation = self._advance_operation(
                    session["operation_id"],
                    state="awaiting_input",
                    stage="access_token",
                    status_text="Access token 无效，等待重新输入。",
                    control="exit",
                )
                return self._interaction_message(
                    key,
                    "⚠️ Access token 无效，请重新发送单行 Token。",
                    operation=operation,
                )
            self.sessions[key] = {
                "stage": "refresh_token",
                "access_token": access_token,
                "operation_id": session["operation_id"],
            }
            self._schedule_sensitive_expiry(key)
            operation = self._advance_operation(
                session["operation_id"],
                state="awaiting_input",
                stage="refresh_token",
                status_text="等待 Refresh token。",
                control="exit",
            )
            return self._interaction_message(
                key,
                "已收到 Access token。\n请发送 Refresh token。",
                operation=operation,
            )

        if stage == "refresh_token":
            try:
                refresh_token = _single_secret(request.get("text"))
            except ValueError:
                operation = self._advance_operation(
                    session["operation_id"],
                    state="awaiting_input",
                    stage="refresh_token",
                    status_text="Refresh token 无效，等待重新输入。",
                    control="exit",
                )
                return self._interaction_message(
                    key,
                    "⚠️ Refresh token 无效，请重新发送单行 Token。",
                    operation=operation,
                )
            access_token = session["access_token"]
            operation_state = self.operations[session["operation_id"]]
            operation_state.update({
                "kind": "direct_auth",
                "cancel_event": threading.Event(),
                "token_decision_lock": asyncio.Lock(),
            })
            await self._report_operation(
                session["operation_id"],
                state="running",
                stage="token_persistence",
                status_text="正在验证并写入 115 Token。",
                control="rollback",
            )
            try:
                updated = await self._persist_tokens(
                    session["operation_id"], access_token, refresh_token,
                    auth_mode="direct",
                )
                operation = await self._commit_token_persistence(
                    session["operation_id"],
                    status_text="115 Token 已写入并立即生效。",
                )
            except TokenPersistenceCancelled as exc:
                self._clear_auth_session(key)
                operation = await self._report_operation(
                    session["operation_id"],
                    state=(
                        "rolled_back" if exc.rollback_verified
                        else "partially_rolled_back"
                    ),
                    stage="token_persistence",
                    status_text=(
                        "Token 写入已取消，原私有配置和运行中 Token 已恢复。"
                        if exc.rollback_verified else
                        "Token 写入已停止，但原配置恢复未能完整验证，请人工检查。"
                    ),
                    control="",
                )
                result = self._message_with_session(
                    operation["status_text"], "close"
                )
                result["operation"] = operation
                return result
            except TokenPersistenceFailed as exc:
                logger.error("open115_direct_auth_write_failed")
                if not exc.rollback_verified:
                    self._clear_auth_session(key)
                    operation = await self._report_operation(
                        session["operation_id"],
                        state="partially_rolled_back",
                        stage="token_persistence",
                        status_text=(
                            "Token 写入失败，原配置恢复未能完整验证，请人工检查。"
                        ),
                        control="",
                    )
                    result = self._message_with_session(
                        operation["status_text"], "close"
                    )
                    result["operation"] = operation
                    return result
                operation = await self._report_operation(
                    session["operation_id"],
                    state="awaiting_input",
                    stage="refresh_token",
                    status_text="Token 写入失败，原配置已恢复，等待重新输入。",
                    control="exit",
                )
                return self._interaction_message(
                    key,
                    "⚠️ 115 Token 写入失败，原配置已恢复；"
                    "请重新发送 Refresh token 或使用 /q 取消。",
                    operation=operation,
                )
            except Exception:
                logger.error("open115_direct_auth_write_failed")
                operation = await self._report_operation(
                    session["operation_id"],
                    state="awaiting_input",
                    stage="refresh_token",
                    status_text="Token 写入失败，等待重新输入 Refresh token。",
                    control="exit",
                )
                return self._interaction_message(
                    key,
                    "⚠️ 115 Token 写入失败，请重新发送 Refresh token 或使用 /q 取消。",
                    operation=operation,
                )
            self.config.update(updated)
            self._clear_auth_session(key)
            logger.info("open115_direct_auth_updated auth_mode=direct")
            result = self._message_with_session(
                "✅ 115 Token 已写入并立即生效。", "close"
            )
            result["operation"] = operation
            return result

        return self._message_with_session("⚠️ 会话已失效。", "close")

    def _start_config_session(self, request: dict) -> dict:
        key = self._session_key(request)
        self._clear_auth_session(key)
        operation = self._new_operation(
            request,
            stage="config_home",
            status_text="等待选择 open115 配置项。",
            control="exit",
        )
        self.sessions[key] = {
            "stage": "config_home",
            "operation_id": operation["operation_id"],
        }
        self._schedule_sensitive_expiry(key)
        return self._config_home_message(key, operation=operation)

    def _config_home_message(
        self,
        key,
        *,
        kind="send_message",
        operation=None,
        text_prefix="",
    ):
        current = (
            self.config_store.read()
            if self.config_store else dict(self.config)
        )
        configured = bool(
            current.get("access_token") and current.get("refresh_token")
        )
        directory_count = len(current.get("save_directories") or [])
        if operation is None:
            session = self.sessions[key]
            operation = self._operation_view(
                self.operations[session["operation_id"]]
            )
        return {
            "actions": [{
                "kind": kind,
                "text": (
                    (f"{text_prefix}\n\n" if text_prefix else "") +
                    "open115 配置\n\n"
                    f"授权：{'已配置' if configured else '未配置'}\n"
                    f"保存目录：{directory_count} 个\n\n"
                    "请选择要修改的配置。"
                ),
                "data": {"keyboard": [
                    [{
                        "text": "授权配置",
                        "callback_data": "open115:config:auth",
                    }],
                    [{
                        "text": "保存目录",
                        "callback_data": "open115:config:directories",
                    }],
                    self._exit_row(),
                ]},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    async def _config_callback(self, request, payload, key):
        session = self.sessions.get(key)
        if not session:
            return self._message_with_session("⚠️ 配置会话已失效。", "close")
        stage = session.get("stage")
        if payload == "config:auth" and stage == "config_home":
            return self._start_auth_session(
                request,
                operation_id=session["operation_id"],
                kind="edit_message",
            )
        if payload == "config:directories" and stage == "config_home":
            current = (
                self.config_store.read()
                if self.config_store else dict(self.config)
            )
            session.update({
                "stage": "directory_list",
                "working_directories": normalize_save_directories(
                    current.get("save_directories") or []
                ),
                "page": 0,
            })
            self._schedule_sensitive_expiry(key)
            operation = self._advance_operation(
                session["operation_id"],
                state="awaiting_input",
                stage="directory_list",
                status_text="正在管理 115 保存目录。",
                control="exit",
            )
            return self._directory_list_message(
                key, kind="edit_message", operation=operation
            )
        if payload == "config:back" and str(stage).startswith("directory_"):
            return self._return_to_directory_list(
                key, "已返回保存目录列表。", kind="edit_message"
            )
        if (
            payload.startswith("config:page:")
            and stage == "directory_list"
        ):
            try:
                session["page"] = int(payload.rsplit(":", 1)[1])
            except ValueError:
                return self._directory_prompt(
                    key, "⚠️ 保存目录页码无效。", stage="directory_list"
                )
            self._schedule_sensitive_expiry(key)
            return self._directory_list_message(key, kind="edit_message")
        if payload == "config:add" and stage == "directory_list":
            return self._directory_prompt(
                key,
                "请发送保存目录的显示名称。",
                stage="directory_add_name",
                status_text="等待输入保存目录名称。",
                kind="edit_message",
            )
        if payload.startswith("config:item:") and stage == "directory_list":
            try:
                index = self._bounded_index(
                    session["working_directories"],
                    payload.rsplit(":", 1)[1],
                )
                directory = session["working_directories"][index]
            except (IndexError, TypeError, ValueError):
                return self._directory_list_message(
                    key,
                    kind="edit_message",
                    text_prefix="⚠️ 保存目录条目不可用。",
                )
            session.update({"stage": "directory_item", "selected_index": index})
            self._schedule_sensitive_expiry(key)
            operation = self._advance_operation(
                session["operation_id"],
                state="awaiting_input",
                stage="directory_item",
                status_text="等待选择保存目录编辑操作。",
                control="exit",
            )
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": (
                        f"目录名称：{directory['name']}\n"
                        f"115 路径：{directory['path']}\n\n"
                        "请选择操作。"
                    ),
                    "data": {"keyboard": [
                        [{
                            "text": "编辑名称",
                            "callback_data": "open115:config:edit:name",
                        }],
                        [{
                            "text": "编辑路径",
                            "callback_data": "open115:config:edit:path",
                        }],
                        [{
                            "text": "删除目录",
                            "callback_data": "open115:config:delete",
                        }],
                        [{
                            "text": "返回目录列表",
                            "callback_data": "open115:config:back",
                        }],
                        self._exit_row(),
                    ]},
                }],
                "session": {"state": "open"},
                "operation": operation,
            }
        if payload == "config:edit:name" and stage == "directory_item":
            return self._directory_prompt(
                key,
                "请发送新的目录名称。",
                stage="directory_edit_name",
                status_text="等待输入新的保存目录名称。",
                kind="edit_message",
            )
        if payload == "config:edit:path" and stage == "directory_item":
            return self._directory_prompt(
                key,
                (
                    "请从 115 根文件夹开始填写保存路径，例如 "
                    "series/live action（末尾 / 可省略）。"
                ),
                stage="directory_edit_path",
                status_text="等待输入新的 115 保存路径。",
                kind="edit_message",
            )
        if payload == "config:delete" and stage == "directory_item":
            index = session["selected_index"]
            directory = session["working_directories"][index]
            session["stage"] = "directory_delete_confirm"
            self._schedule_sensitive_expiry(key)
            operation = self._advance_operation(
                session["operation_id"],
                state="awaiting_input",
                stage="directory_delete_confirm",
                status_text="等待确认删除保存目录。",
                control="exit",
            )
            return {
                "actions": [{
                    "kind": "edit_message",
                    "text": (
                        f"确认删除目录“{directory['name']}”"
                        f"（{directory['path']}）？"
                    ),
                    "data": {"keyboard": [
                        [{
                            "text": "确认删除",
                            "callback_data": "open115:config:delete:confirm",
                        }],
                        [{
                            "text": "返回目录列表",
                            "callback_data": "open115:config:back",
                        }],
                        self._exit_row(),
                    ]},
                }],
                "session": {"state": "open"},
                "operation": operation,
            }
        if (
            payload == "config:delete:confirm"
            and stage == "directory_delete_confirm"
        ):
            index = session["selected_index"]
            try:
                session["working_directories"].pop(index)
            except IndexError:
                return self._return_to_directory_list(
                    key,
                    "⚠️ 保存目录条目已不存在。",
                    kind="edit_message",
                )
            return self._return_to_directory_list(
                key, "目录删除已暂存。", kind="edit_message"
            )
        if payload == "config:save" and stage == "directory_list":
            return await self._save_directory_config(key)
        if stage == "config_home":
            self._schedule_sensitive_expiry(key)
            return self._config_home_message(
                key,
                kind="edit_message",
                text_prefix="⚠️ 配置操作与当前步骤不匹配。",
            )
        return self._directory_prompt(
            key,
            "⚠️ 配置操作与当前步骤不匹配。",
            stage=str(stage or "directory_list"),
        )

    def _directory_message(self, request, key, session):
        stage = session.get("stage")
        try:
            if stage == "directory_add_name":
                session["pending_name"] = self._directory_name(
                    request.get("text"), session["working_directories"]
                )
                return self._directory_prompt(
                    key,
                    (
                        "请从 115 根文件夹开始填写保存路径，例如 "
                        "series/live action（末尾 / 可省略）。"
                    ),
                    stage="directory_add_path",
                    status_text="等待输入 115 保存路径。",
                )
            if stage == "directory_add_path":
                path = self._directory_path(
                    request.get("text"), session["working_directories"]
                )
                candidate = deepcopy(session["working_directories"])
                candidate.append({
                    "name": session["pending_name"],
                    "path": path,
                })
                session["working_directories"] = normalize_save_directories(
                    candidate
                )
                return self._return_to_directory_list(
                    key, "目录新增已暂存。"
                )
            if stage in {"directory_edit_name", "directory_edit_path"}:
                index = int(session["selected_index"])
                candidate = deepcopy(session["working_directories"])
                if stage == "directory_edit_name":
                    candidate[index]["name"] = self._directory_name(
                        request.get("text"), candidate, exclude_index=index
                    )
                else:
                    candidate[index]["path"] = self._directory_path(
                        request.get("text"), candidate, exclude_index=index
                    )
                session["working_directories"] = normalize_save_directories(
                    candidate
                )
                return self._return_to_directory_list(
                    key, "目录修改已暂存。"
                )
        except (IndexError, TypeError, ValueError) as exc:
            return self._directory_prompt(
                key,
                f"⚠️ {exc}",
                stage=str(stage),
                status_text="保存目录输入无效，等待重新输入。",
            )
        return self._message_with_session("⚠️ 配置会话已失效。", "close")

    @staticmethod
    def _directory_name(value, directories, *, exclude_index=None):
        name = str(value or "").strip()
        if not name or "\n" in name or "\r" in name:
            raise ValueError("目录名称必须是非空单行文本。")
        if any(
            index != exclude_index and item.get("name") == name
            for index, item in enumerate(directories)
        ):
            raise ValueError("目录名称重复，请重新输入。")
        return name

    @staticmethod
    def _directory_path(value, directories, *, exclude_index=None):
        raw_path = str(value or "").strip()
        if raw_path.startswith("/"):
            raise ValueError(
                "目录路径请从 115 根文件夹开始填写，不要以 / 开头。"
            )
        try:
            path = normalize_save_directory_path(raw_path)
        except ValueError as exc:
            raise ValueError(
                "目录路径只能包含有效的根目录相对路径段。"
            ) from exc
        if any(
            index != exclude_index and item.get("path") == path
            for index, item in enumerate(directories)
        ):
            raise ValueError("目录路径重复，请重新输入。")
        return path

    @staticmethod
    def _bounded_index(items, value):
        index = int(value)
        if index < 0 or index >= len(items):
            raise IndexError("open115 directory index is out of range")
        return index

    def _directory_prompt(
        self,
        key,
        text,
        *,
        stage,
        status_text=None,
        kind="send_message",
    ):
        session = self.sessions.get(key)
        if not session:
            return self._message_with_session("⚠️ 配置会话已失效。", "close")
        session["stage"] = stage
        self._schedule_sensitive_expiry(key)
        operation = self._advance_operation(
            session["operation_id"],
            state="awaiting_input",
            stage=stage,
            status_text=status_text or text,
            control="exit",
        )
        return {
            "actions": [{
                "kind": kind,
                "text": text,
                "data": {"keyboard": [
                    [{
                        "text": "返回目录列表",
                        "callback_data": "open115:config:back",
                    }],
                    self._exit_row(),
                ]},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    def _return_to_directory_list(self, key, status_text, *, kind="send_message"):
        session = self.sessions[key]
        session["stage"] = "directory_list"
        session.pop("pending_name", None)
        session.pop("selected_index", None)
        self._schedule_sensitive_expiry(key)
        operation = self._advance_operation(
            session["operation_id"],
            state="awaiting_input",
            stage="directory_list",
            status_text=status_text,
            control="exit",
        )
        return self._directory_list_message(
            key,
            kind=kind,
            operation=operation,
            text_prefix=status_text,
        )

    async def _save_directory_config(self, key):
        session = self.sessions[key]
        operation_id = session["operation_id"]
        working_directories = normalize_save_directories(
            session["working_directories"]
        )
        await self._report_operation(
            operation_id,
            state="running",
            stage="config_persistence",
            status_text="正在保存 115 目录配置。",
            control="",
        )
        try:
            if self.config_store:
                updated = await asyncio.to_thread(
                    self.config_store.write_save_directories,
                    working_directories,
                )
            else:
                updated = dict(self.config)
                updated["save_directories"] = working_directories
        except Exception as exc:
            logger.error(
                "open115_directory_config_write_failed "
                f"error={type(exc).__name__}"
            )
            session["stage"] = "directory_list"
            self._schedule_sensitive_expiry(key)
            operation = await self._report_operation(
                operation_id,
                state="awaiting_input",
                stage="directory_list",
                status_text="目录配置保存失败，工作副本已保留。",
                control="exit",
            )
            return self._directory_list_message(
                key,
                operation=operation,
                text_prefix="⚠️ 目录配置保存失败，请重试或退出。",
            )
        self.config.clear()
        self.config.update(updated)
        self._clear_auth_session(key)
        operation = await self._report_operation(
            operation_id,
            state="completed",
            stage="completed",
            status_text="115 保存目录已写入并立即生效。",
            control="",
        )
        result = self._message_with_session(
            "✅ 115 保存目录已写入并立即生效。", "close"
        )
        result["operation"] = operation
        return result

    def _start_auth_session(
        self,
        request: dict,
        *,
        operation_id=None,
        kind="send_message",
    ) -> dict:
        key = self._session_key(request)
        if operation_id is None:
            self._clear_auth_session(key)
            operation = self._new_operation(
                request,
                stage="authorization_mode",
                status_text="等待选择 115 授权方式。",
                control="exit",
            )
        else:
            self._clear_auth_session(key)
            operation = self._advance_operation(
                operation_id,
                state="awaiting_input",
                stage="authorization_mode",
                status_text="等待选择 115 授权方式。",
                control="exit",
            )
        self.sessions[key] = {
            "stage": "choose_mode",
            "operation_id": operation["operation_id"],
        }
        return {
            "actions": [{
                "kind": kind,
                "text": "请选择 115 授权方式：",
                "data": {"keyboard": [[
                    {
                        "text": "Access / Refresh Token",
                        "callback_data": "open115:auth:direct",
                    },
                    {
                        "text": "115 扫码授权",
                        "callback_data": "open115:auth:scan",
                    },
                ], self._exit_row()]},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    def _directory_list_message(
        self,
        key,
        *,
        kind="send_message",
        operation=None,
        text_prefix="",
    ):
        session = self.sessions[key]
        directories = session["working_directories"]
        page_count = max(
            1, (len(directories) + DIRECTORY_PAGE_SIZE - 1) // DIRECTORY_PAGE_SIZE
        )
        page = max(0, min(int(session.get("page") or 0), page_count - 1))
        session["page"] = page
        start = page * DIRECTORY_PAGE_SIZE
        keyboard = [[{
            "text": f"📁 {item['name']}",
            "callback_data": f"open115:config:item:{index}",
        }] for index, item in enumerate(
            directories[start:start + DIRECTORY_PAGE_SIZE], start=start
        )]
        if page_count > 1:
            navigation = []
            if page > 0:
                navigation.append({
                    "text": "上一页",
                    "callback_data": f"open115:config:page:{page - 1}",
                })
            if page + 1 < page_count:
                navigation.append({
                    "text": "下一页",
                    "callback_data": f"open115:config:page:{page + 1}",
                })
            keyboard.append(navigation)
        keyboard.extend([
            [{"text": "新增目录", "callback_data": "open115:config:add"}],
            [{
                "text": "保存并完成",
                "callback_data": "open115:config:save",
            }],
            self._exit_row(),
        ])
        if operation is None:
            operation = self._operation_view(
                self.operations[session["operation_id"]]
            )
        return {
            "actions": [{
                "kind": kind,
                "text": (
                    (f"{text_prefix}\n\n" if text_prefix else "") +
                    f"115 保存目录（{len(directories)} 个）\n"
                    f"第 {page + 1}/{page_count} 页\n\n"
                    "逐条新增、编辑或删除，最后统一保存。"
                ),
                "data": {"keyboard": keyboard},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    def _schedule_sensitive_expiry(self, key):
        handle = self.session_expiry_handles.pop(key, None)
        if handle is not None:
            handle.cancel()
        expected = self.sessions.get(key)
        handle = asyncio.get_running_loop().call_later(
            SESSION_TTL_SECONDS,
            self._expire_sensitive_session,
            key,
            expected,
        )
        self.session_expiry_handles[key] = handle

    def _expire_sensitive_session(self, key, expected):
        if self.sessions.get(key) is expected:
            self.sessions.pop(key, None)
            operation_id = expected.get("operation_id") if expected else None
            if operation_id in self.operations:
                stage = str(expected.get("stage") or "")
                status_text = (
                    "目录配置已超时并退出。"
                    if stage.startswith("directory_") or stage == "config_home"
                    else "授权输入已超时并退出。"
                )
                operation = self._advance_operation(
                    operation_id,
                    state="cancelled",
                    stage="session_expired",
                    status_text=status_text,
                    control="",
                )
                try:
                    asyncio.create_task(self.core.report_operation(operation))
                except RuntimeError:
                    pass
        self.session_expiry_handles.pop(key, None)

    def _clear_auth_session(self, key):
        handle = self.session_expiry_handles.pop(key, None)
        if handle is not None:
            handle.cancel()
        self.sessions.pop(key, None)

    async def _start_scan_auth(self, request):
        key = self._session_key(request)
        session = self.sessions.get(key) or {}
        operation_id = session.get("operation_id")
        config = self.config_store.read() if self.config_store else dict(self.config)
        app_id = str(config.get("app_id") or "").strip()
        if not app_id:
            self._clear_auth_session(key)
            operation = self._advance_operation(
                operation_id,
                state="failed",
                stage="authorization_configuration",
                status_text="扫码授权缺少 app_id，任务已结束。",
                control="",
            )
            result = self._message_with_session(
                "⚠️ 扫码授权需要先在私有配置中填写 app_id。",
                "close",
            )
            result["operation"] = operation
            return result
        if self.runtime is None:
            raise FeatureError("not_ready", "open115 runtime is not ready")
        try:
            authorization = await asyncio.to_thread(
                self.client.create_device_authorization,
                app_id,
            )
            qr_text = self._render_qr(authorization["qrcode"])
        except Exception as exc:
            logger.error(
                "open115_scan_auth_start_failed "
                f"error={type(exc).__name__}"
            )
            self._clear_auth_session(key)
            operation = self._advance_operation(
                operation_id,
                state="failed",
                stage="qr_creation",
                status_text=f"115 扫码授权启动失败：{type(exc).__name__}",
                control="",
            )
            result = self._message_with_session(
                f"⚠️ 115 扫码授权启动失败：{type(exc).__name__}",
                "close",
            )
            result["operation"] = operation
            return result
        logger.info(
            "open115_scan_auth_started "
            f"user_id={int(request.get('user_id') or 0)} "
            f"auth_uid={authorization['uid']}"
        )
        task_id = f"open115-auth-{authorization['uid']}"
        self._clear_auth_session(key)
        operation_state = self.operations[operation_id]
        operation_state.update({
            "kind": "scan_auth",
            "cancel_event": threading.Event(),
            "token_decision_lock": asyncio.Lock(),
            "task_id": task_id,
        })
        operation = self._advance_operation(
            operation_id,
            state="running",
            stage="qr_wait",
            status_text="等待 115 App 扫码确认。",
            control="cancel",
        )
        self.runtime.spawn(
            self._complete_scan_auth(
                authorization,
                operation_id,
            ),
            task_id=task_id,
        )
        return {
            "actions": [{
                "kind": "edit_message",
                "text": f"请使用 115 App 扫码并确认：\n<pre>{qr_text}</pre>",
                "parse_mode": "HTML",
            }],
            "session": {"state": "close"},
            "operation": operation,
        }

    async def _complete_scan_auth(self, authorization, operation_id):
        operation = self.operations[operation_id]
        user_id = int(operation.get("user_id") or 0)
        cancel_event = operation["cancel_event"]
        try:
            tokens = await asyncio.to_thread(
                self.client.complete_device_authorization,
                authorization,
                timeout=float(self.config.get("auth_poll_timeout") or 300),
                poll_interval=float(self.config.get("auth_poll_interval") or 2),
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                raise RuntimeError("authorization cancelled")
            await self._report_operation(
                operation_id,
                state="running",
                stage="token_persistence",
                status_text="扫码已确认，正在写入 115 Token。",
                control="rollback",
            )
            await self._persist_tokens(
                operation_id,
                tokens["access_token"],
                tokens["refresh_token"],
                auth_mode="scan",
            )
            completed = await self._commit_token_persistence(
                operation_id,
                status_text="115 扫码授权成功，Token 已写入。",
            )
            logger.info(
                "open115_scan_auth_completed "
                f"user_id={user_id} auth_uid={authorization['uid']}"
            )
            message = "✅ 115 扫码授权成功，Token 已写回 Feature 私有配置。"
            if completed["chat_id"] and completed["user_id"]:
                await self.core.report_operation(completed)
        except Exception as exc:
            if isinstance(exc, TokenPersistenceCancelled):
                logger.info(
                    "open115_scan_auth_token_rollback "
                    f"user_id={user_id} auth_uid={authorization.get('uid') or ''}"
                )
                message = (
                    "已取消 Token 写入，原配置已恢复。"
                    if exc.rollback_verified else
                    "Token 写入已停止，但原配置恢复未能完整验证。"
                )
                await self._report_operation(
                    operation_id,
                    state=(
                        "rolled_back" if exc.rollback_verified
                        else "partially_rolled_back"
                    ),
                    stage="token_persistence",
                    status_text=message,
                    control="",
                )
            elif isinstance(exc, TokenPersistenceFailed):
                message = (
                    "115 Token 写入失败，原配置已恢复。"
                    if exc.rollback_verified else
                    "115 Token 写入失败，原配置恢复未能完整验证。"
                )
                await self._report_operation(
                    operation_id,
                    state=(
                        "failed" if exc.rollback_verified
                        else "partially_rolled_back"
                    ),
                    stage="token_persistence",
                    status_text=message,
                    control="",
                    details={"manual_check_required": not exc.rollback_verified},
                )
            elif cancel_event.is_set():
                logger.info(
                    "open115_scan_auth_cancelled "
                    f"user_id={user_id} auth_uid={authorization.get('uid') or ''}"
                )
                message = "已取消 115 扫码授权。"
                await self._report_operation(
                    operation_id,
                    state="cancelled",
                    stage="qr_wait",
                    status_text="已取消 115 扫码授权，未写入 Token。",
                    control="",
                )
            else:
                logger.error(
                    "open115_scan_auth_failed "
                    f"user_id={user_id} auth_uid={authorization.get('uid') or ''} "
                    f"error={type(exc).__name__}"
                )
                message = f"⚠️ 115 扫码授权失败：{type(exc).__name__}"
                await self._report_operation(
                    operation_id,
                    state="failed",
                    stage=operation.get("stage") or "qr_wait",
                    status_text=f"115 扫码授权失败：{type(exc).__name__}",
                    control="",
                )
        if user_id:
            await self.core.notify_user(user_id, message)

    @staticmethod
    def _render_qr(value: str) -> str:
        import qrcode

        matrix = qrcode.QRCode(border=1, box_size=1)
        matrix.add_data(str(value))
        matrix.make(fit=True)
        rows = matrix.get_matrix()
        if len(rows) % 2:
            rows.append([False] * len(rows[0]))
        chars = {
            (False, False): " ",
            (True, False): "▀",
            (False, True): "▄",
            (True, True): "█",
        }
        return "\n".join(
            "".join(chars[(top, bottom)] for top, bottom in zip(rows[index], rows[index + 1]))
            for index in range(0, len(rows), 2)
        )

    async def _start_download(self, payload: dict, call_context: dict) -> dict:
        if self.runtime is None:
            raise FeatureError("not_ready", "open115 runtime is not ready")
        link = str(payload.get("link") or "").strip()
        selected_path = "/" + str(payload.get("selected_path") or "").strip("/")
        if not _MAGNET.fullmatch(link) or selected_path == "/":
            raise FeatureError("invalid_download", "valid magnet link and selected_path are required")
        job_id = str(call_context.get("idempotency_key") or "").strip() or hashlib.sha256(
            f"{link}\0{selected_path}".encode("utf-8")
        ).hexdigest()
        requested_operation_id = str(payload.get("operation_id") or "")
        operation_id = requested_operation_id
        if self.jobs:
            persisted = self.jobs.get(job_id)
            if persisted is not None:
                stored = (
                    persisted.get("result")
                    if (persisted.get("result") or {}).get("operation_id")
                    else persisted.get("payload")
                ) or {}
                stored_operation_id = str(stored.get("operation_id") or "")
                if (
                    requested_operation_id
                    and stored_operation_id
                    and requested_operation_id != stored_operation_id
                ):
                    raise FeatureError(
                        "idempotency_conflict",
                        "persisted open115 download belongs to another operation",
                    )
                operation_id = stored_operation_id or requested_operation_id
            operation_id = operation_id or uuid.uuid4().hex
            if persisted is None:
                durable_payload = payload | {
                    "link": link,
                    "selected_path": selected_path,
                    "operation_id": operation_id,
                }
                persisted = self.jobs.create_or_get(job_id, durable_payload)
            if persisted["state"] in {
                "cancelled", "completed", "failed", "downloaded",
                "running", "interrupted",
            }:
                return self._persisted_download_duplicate(
                    persisted, requested_operation_id
                )
        else:
            operation_id = operation_id or uuid.uuid4().hex
        if job_id in self.active_job_ids:
            operation = next((
                candidate
                for candidate in self.operations.values()
                if candidate.get("job_id") == job_id
            ), None)
            if operation is None:
                raise FeatureError(
                    "operation_unavailable",
                    "active open115 download has no operation owner",
                )
            if not requested_operation_id:
                operation_id = operation["operation_id"]
            if operation["operation_id"] != operation_id:
                raise FeatureError(
                    "idempotency_conflict",
                    "active open115 download belongs to another operation",
                )
            logger.info(
                "open115_download_duplicate "
                f"job_id={job_id} selected_path={selected_path}"
            )
            return {
                "accepted": True,
                "job_id": job_id,
                "duplicate": True,
                "state": str(operation.get("state") or "running"),
                "operation_id": operation_id,
                "operation": self._operation_view(operation),
            }
        operation = self.operations.get(operation_id)
        if operation is None:
            operation = {
                "operation_id": operation_id,
                "chat_id": int(payload.get("chat_id") or payload.get("user_id") or 0),
                "user_id": int(payload.get("user_id") or 0),
                "state": "running",
                "stage": "accepted",
                "status_text": "115 下载任务已接受。",
                "control": "cancel",
                "revision": max(0, int(payload.get("operation_revision") or 0)),
                "details": {},
            }
            self.operations[operation_id] = operation
        operation.update({
            "kind": "download",
            "job_id": job_id,
            "cancel_event": threading.Event(),
            "info_hash": "",
            "offline_delete_attempted": False,
            "offline_task_record": "unknown",
            "cancel_cleanup_done": asyncio.Event(),
        })
        self.active_job_ids.add(job_id)
        report_error = None
        try:
            operation_view = await self._report_operation(
                operation_id,
                state="running",
                stage="preparing_submission",
                status_text="正在准备提交 115 离线下载任务。",
                control="cancel",
            )
        except Exception as exc:
            if not _ambiguous_core_report_error(exc):
                self.active_job_ids.discard(job_id)
                self._advance_operation(
                    operation_id,
                    state="failed",
                    stage="preparing_submission",
                    status_text="Core 未接受 115 下载任务所有权。",
                    control="",
                )
                raise
            report_error = exc
            operation_view = self._operation_view(operation)
            operation["ownership_pending"] = True
            operation["ownership_report"] = dict(operation_view)
        logger.info(
            "open115_download_started "
            f"job_id={job_id} "
            f"selected_path={selected_path} "
            f"user_id={int(payload.get('user_id') or 0)} "
            f"link_sha1={_link_fingerprint(link)}"
        )
        try:
            if self.jobs:
                self.jobs.update(job_id, "running")
            self.runtime.spawn(self._download_job(job_id, payload | {
                "link": link,
                "selected_path": selected_path,
                "operation_id": operation_id,
            }), task_id=job_id)
        except Exception:
            self.active_job_ids.discard(job_id)
            if self.jobs:
                self.jobs.update(
                    job_id, "interrupted", error="executor start failed"
                )
            raise
        result = {
            "accepted": True,
            "job_id": job_id,
            "operation_id": operation_id,
            "operation": operation_view,
        }
        if report_error is not None:
            result["report_pending"] = True
        return result

    def _persisted_download_duplicate(self, job: dict, requested_operation_id: str):
        result = job.get("result") or {}
        stored = result if result.get("operation_id") else (job.get("payload") or {})
        stored_operation_id = str(stored.get("operation_id") or "")
        if (
            stored_operation_id
            and requested_operation_id
            and stored_operation_id != requested_operation_id
        ):
            raise FeatureError(
                "idempotency_conflict",
                "persisted open115 download belongs to another operation",
            )
        operation_id = stored_operation_id or requested_operation_id
        operation = self.operations.get(operation_id)
        logger.info(
            "open115_download_duplicate "
            f"job_id={job['job_id']} state={job['state']}"
        )
        response = {
            "accepted": True,
            "job_id": str(job["job_id"]),
            "duplicate": True,
            "state": str(job["state"]),
            "operation_id": operation_id,
        }
        if operation is not None:
            response["operation"] = self._operation_view(operation)
        return response

    async def _download_job(self, job_id: str, payload: dict):
        link = payload["link"]
        selected_path = payload["selected_path"]
        user_id = int(payload.get("user_id") or 0)
        operation_id = payload["operation_id"]
        operation = self.operations[operation_id]
        cancel_event = operation["cancel_event"]
        info_hash = ""
        try:
            self._raise_if_cancelled(operation)
            await self._confirm_download_ownership(operation)
            self._raise_if_cancelled(operation)
            await asyncio.to_thread(self.client.add_offline_task, link, selected_path)
            self._raise_if_cancelled(operation)
            await self._report_operation(
                operation_id,
                state="running",
                stage="submitted",
                status_text="115 离线任务已提交。",
                control="cancel",
            )
            self._raise_if_cancelled(operation)
            await self._report_operation(
                operation_id,
                state="running",
                stage="downloading",
                status_text="115 正在下载，等待任务完成。",
                control="cancel",
            )

            def progress(value):
                current_hash = str(value.get("info_hash") or "")
                if current_hash:
                    operation["info_hash"] = current_hash
                operation["details"] = {
                    "progress": float(value.get("progress") or 0),
                }

            completed = await asyncio.to_thread(
                self.client.wait_for_download,
                link,
                timeout=float(self.config.get("download_timeout") or 1800),
                poll_interval=float(self.config.get("poll_interval") or 10),
                cancel_event=cancel_event,
                progress_callback=progress,
            )
            self._raise_if_cancelled(operation)
            resource_name = str(completed.get("resource_name") or "").strip("/")
            info_hash = str(completed.get("info_hash") or "")
            if info_hash:
                operation["info_hash"] = info_hash
            if not resource_name:
                raise RuntimeError("115 completed task has no resource name")
            final_path = f"{selected_path.rstrip('/')}/{resource_name}"
            await self._report_operation(
                operation_id,
                state="running",
                stage="reading_files",
                status_text="下载完成，正在读取 115 文件树。",
                control="cancel",
            )
            self._raise_if_cancelled(operation)
            file_tree = await asyncio.to_thread(
                self.client.get_file_tree,
                final_path,
            )
            self._raise_if_cancelled(operation)
            event_payload = {
                "job_id": job_id,
                "provider": "open115",
                "link": link,
                "selected_path": selected_path,
                "chat_id": int(operation.get("chat_id") or user_id or 0),
                "user_id": user_id,
                "resource_name": resource_name,
                "download_root": final_path,
                "final_path": final_path,
                "file_tree": file_tree,
                "media_metadata": payload.get("media_metadata"),
                "naming_metadata": payload.get("naming_metadata"),
                "release": payload.get("release"),
                "operation_id": operation_id,
            }
            if self.jobs:
                self.jobs.update(job_id, "downloaded", result=event_payload)
            logger.info(
                "open115_download_completed "
                f"job_id={job_id} "
                f"final_path={final_path} "
                f"resource_name={resource_name}"
            )
            await self._publish_downloaded({
                "job_id": job_id,
                "state": "downloaded",
                "result": event_payload,
            })
        except Exception as exc:
            if cancel_event.is_set():
                await self._finish_cancelled(operation_id)
                if self.jobs:
                    self.jobs.update(job_id, "cancelled", error="cancelled")
                return
            if self.jobs and (self.jobs.get(job_id) or {}).get("state") == "downloaded":
                return
            if self.jobs:
                self.jobs.update(job_id, "failed", error=type(exc).__name__)
            logger.error(
                "open115_download_failed "
                f"job_id={job_id} "
                f"selected_path={selected_path} "
                f"error={type(exc).__name__} "
                f"link_sha1={_link_fingerprint(link)}"
            )
            failure = {
                "job_id": job_id,
                "provider": "open115",
                "user_id": user_id,
                "link": link,
                "error": type(exc).__name__,
            }
            try:
                await self.core.publish_event(
                    "download.failed", failure, idempotency_key=f"{job_id}:failed"
                )
            except Exception:
                pass
            if user_id:
                try:
                    await self.core.notify_user(user_id, f"❌ 115 下载任务失败：{type(exc).__name__}", idempotency_key=f"{job_id}:failed-notice")
                except Exception:
                    pass
            await self._report_operation(
                operation_id,
                state="failed",
                stage=operation.get("stage") or "download",
                status_text=f"115 下载任务失败：{type(exc).__name__}",
                control="",
                details={"stopped_at": operation.get("stage") or "download"},
            )
        finally:
            if info_hash and not cancel_event.is_set():
                operation["info_hash"] = info_hash
                await self._cancel_offline_record_once(operation)
            self.active_job_ids.discard(job_id)

    async def _confirm_download_ownership(self, operation):
        if not operation.get("ownership_pending"):
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
            self._raise_if_cancelled(operation)
            try:
                response = await self.core.report_operation(report)
                break
            except Exception as exc:
                if not _ambiguous_core_report_error(exc):
                    raise
                if attempt + 1 >= retries:
                    raise
                await asyncio.sleep(float(
                    self.config.get("operation_confirmation_interval") or 0.1
                ))
        if not isinstance(response, dict) or response.get("accepted") is not True:
            raise FeatureError(
                "ownership_rejected",
                "Core did not confirm open115 download ownership",
            )
        operation["ownership_pending"] = False
        operation.pop("ownership_report", None)

    async def _publish_downloaded(self, job):
        payload = job.get("result") or {}
        job_id = str(job["job_id"])
        operation_id = str(payload.get("operation_id") or "")
        if operation_id and operation_id in self.operations:
            current = self.operations[operation_id]
            if current.get("state") == "handed_off":
                operation = self._operation_view(current)
            else:
                operation = await self._report_operation(
                    operation_id,
                    state="handed_off",
                    stage="handoff_renaming",
                    status_text="115 下载完成，已交给媒体整理。",
                    control="cancel",
                    next_plugin_id="renaming",
                )
            payload["operation_revision"] = operation["revision"]
            if self.jobs:
                self.jobs.update(job_id, "downloaded", result=payload)
            self._raise_if_cancelled(current)
        await self.core.publish_event("download.completed", payload, idempotency_key=f"{job_id}:completed")
        logger.info(
            "open115_download_published "
            f"job_id={job_id} final_path={payload.get('final_path') or ''}"
        )
        if self.jobs:
            self.jobs.update(job_id, "completed", result=payload)
        user_id = int(payload.get("user_id") or 0)
        if user_id:
            try:
                await self.core.notify_user(user_id, f"✅ 115 下载完成，已交给整理管线。\n保存目录：{payload.get('final_path')}", idempotency_key=f"{job_id}:download-notice")
            except Exception:
                pass

    async def operation_control(self, request: dict) -> dict:
        operation_id = str(request.get("operation_id") or "")
        action = str(request.get("action") or "")
        operation = self.operations.get(operation_id)
        if operation is None:
            raise FeatureError("not_found", "open115 operation was not found")
        if operation.get("state") in OPERATION_TERMINAL_STATES:
            return {"actions": [], "operation": self._operation_view(operation)}
        try:
            requested_revision = int(request.get("revision") or 0)
        except (TypeError, ValueError):
            requested_revision = 0
        operation["revision"] = max(
            int(operation.get("revision") or 0), requested_revision
        )
        if action == "exit" and operation.get("state") == "awaiting_input":
            for key, session in list(self.sessions.items()):
                if session.get("operation_id") == operation_id:
                    self._clear_auth_session(key)
            terminal = self._advance_operation(
                operation_id,
                state="cancelled",
                stage=operation.get("stage") or "interaction",
                status_text="已退出当前交互。",
                control="",
            )
            return {"actions": [], "operation": terminal}
        if (
            action == "rollback"
            and operation.get("kind") in {"direct_auth", "scan_auth"}
            and operation.get("stage") == "token_persistence"
        ):
            decision_lock = operation.setdefault(
                "token_decision_lock", asyncio.Lock()
            )
            async with decision_lock:
                if operation.get("state") in OPERATION_TERMINAL_STATES:
                    return {
                        "actions": [],
                        "operation": self._operation_view(operation),
                    }
                cancel_event = operation.get("cancel_event")
                if cancel_event is not None:
                    cancel_event.set()
                rolling = self._advance_operation(
                    operation_id,
                    state="rolling_back",
                    stage="token_persistence",
                    status_text="正在停止 Token 写入并恢复原配置。",
                    control="",
                )
            return {"actions": [], "operation": rolling}
        if action != "cancel":
            raise FeatureError("invalid_control", "open115 operation control is invalid")

        provisional_handoff = operation.get("state") == "handed_off"
        details = dict(operation.get("details") or {})
        details.update({
            "offline_task_record": operation.get("offline_task_record", "retained"),
            "downloaded_content": "preserved",
            "stopped_at": operation.get("stage") or "download",
        })
        cancelling = self._advance_operation(
            operation_id,
            state="cancelling",
            stage=operation.get("stage") or "cancelling",
            status_text="取消请求已接受，正在当前安全检查点停止后续任务。",
            control="cancel",
            details=details,
        )
        cancel_event = operation.get("cancel_event")
        if cancel_event is not None:
            cancel_event.set()
        if operation.get("kind") == "download":
            try:
                await self._cancel_offline_record_once(operation)
            finally:
                operation["cancel_cleanup_done"].set()
            details = dict(operation.get("details") or {})
            details.update({
                "offline_task_record": operation.get(
                    "offline_task_record", "retained"
                ),
                "downloaded_content": "preserved",
                "stopped_at": operation.get("stage") or "download",
            })
            operation["details"] = details
            cancelling = self._operation_view(operation)
        if provisional_handoff:
            terminal = await self._finish_cancelled(operation_id)
            if self.jobs and operation.get("job_id"):
                job = self.jobs.get(operation["job_id"])
                if job is not None:
                    self.jobs.update(
                        operation["job_id"],
                        "cancelled",
                        result=job.get("result") or {},
                        error="cancelled before renaming accepted",
                    )
            return {"actions": [], "operation": terminal}
        return {"actions": [], "operation": cancelling}

    def _token_snapshot(self) -> dict:
        if self.config_store and hasattr(self.config_store, "snapshot"):
            store_snapshot = self.config_store.snapshot()
        elif self.config_store:
            store_snapshot = {
                "exists": True,
                "config": self.config_store.read(),
            }
        else:
            store_snapshot = None
        if hasattr(self.client, "access_token"):
            client_tokens = (
                str(getattr(self.client, "access_token", "") or ""),
                str(getattr(self.client, "refresh_token", "") or ""),
            )
        else:
            client_tokens = tuple(getattr(self.client, "tokens", ("", "")))
        return {
            "store": store_snapshot,
            "config": dict(self.config),
            "client_tokens": client_tokens,
        }

    async def _restore_token_snapshot(self, snapshot: dict):
        store_snapshot = snapshot.get("store")
        if self.config_store and store_snapshot is not None:
            if hasattr(self.config_store, "restore"):
                await asyncio.to_thread(
                    self.config_store.restore, store_snapshot
                )
            else:
                old = store_snapshot.get("config") or {}
                await asyncio.to_thread(
                    self.config_store.write_tokens,
                    old.get("access_token"), old.get("refresh_token"),
                    auth_mode=old.get("auth_mode") or "direct",
                )
        self.config.clear()
        self.config.update(snapshot.get("config") or {})
        access_token, refresh_token = snapshot.get("client_tokens") or ("", "")
        if access_token and refresh_token:
            self.client.set_tokens(access_token, refresh_token)
        elif hasattr(self.client, "access_token"):
            self.client.access_token = access_token
            self.client.refresh_token = refresh_token
        else:
            self.client.tokens = (access_token, refresh_token)

    async def _persist_tokens(
        self, operation_id, access_token, refresh_token, *, auth_mode
    ):
        operation = self.operations[operation_id]
        cancel_event = operation["cancel_event"]
        snapshot = self._token_snapshot()
        operation["token_snapshot"] = snapshot
        try:
            self._raise_if_cancelled(operation)
            if self.config_store:
                updated = await asyncio.to_thread(
                    self.config_store.write_tokens,
                    access_token,
                    refresh_token,
                    auth_mode=auth_mode,
                )
            else:
                updated = dict(self.config)
                updated.update({
                    "auth_mode": auth_mode,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                })
            self._raise_if_cancelled(operation)
            self.config.clear()
            self.config.update(updated)
            self.client.set_tokens(access_token, refresh_token)
            self._raise_if_cancelled(operation)
            return updated
        except Exception as exc:
            try:
                await self._restore_token_snapshot(snapshot)
            except Exception:
                if cancel_event.is_set():
                    raise TokenPersistenceCancelled(False) from None
                raise TokenPersistenceFailed(False) from None
            if cancel_event.is_set():
                raise TokenPersistenceCancelled(True) from exc
            raise TokenPersistenceFailed(True) from exc

    async def _commit_token_persistence(self, operation_id, *, status_text):
        operation = self.operations[operation_id]
        decision_lock = operation.setdefault(
            "token_decision_lock", asyncio.Lock()
        )
        async with decision_lock:
            cancel_event = operation["cancel_event"]
            if cancel_event.is_set():
                try:
                    await self._restore_token_snapshot(
                        operation["token_snapshot"]
                    )
                except Exception:
                    raise TokenPersistenceCancelled(False) from None
                raise TokenPersistenceCancelled(True)
            return self._advance_operation(
                operation_id,
                state="completed",
                stage="completed",
                status_text=status_text,
                control="",
            )

    async def operation_snapshot(self, request: dict) -> dict:
        requested = str(request.get("operation_id") or "")
        operations = [
            self._operation_view(operation)
            for operation_id, operation in self.operations.items()
            if operation.get("state") not in OPERATION_TERMINAL_STATES
            and (not requested or requested == operation_id)
        ]
        return {"operations": operations}

    async def _cancel_offline_record_once(self, operation: dict):
        if operation.get("offline_delete_attempted"):
            return
        info_hash = str(operation.get("info_hash") or "")
        if not info_hash:
            operation["offline_task_record"] = "retained"
            operation["offline_delete_reason"] = "info_hash_unavailable"
            return
        operation["offline_delete_attempted"] = True
        try:
            deleted = await asyncio.to_thread(
                self.client.del_offline_task, info_hash, 0
            )
        except Exception:
            deleted = False
        operation["offline_task_record"] = "deleted" if deleted else "retained"
        if not deleted:
            operation["offline_delete_reason"] = "delete_failed"

    async def _finish_cancelled(self, operation_id: str):
        operation = self.operations[operation_id]
        cleanup_done = operation.get("cancel_cleanup_done")
        if cleanup_done is not None:
            await cleanup_done.wait()
        record_state = operation.get("offline_task_record") or "retained"
        if record_state == "deleted":
            record_text = "115 离线任务记录已删除"
        elif operation.get("offline_delete_reason") == "delete_failed":
            record_text = "115 离线任务记录删除失败，已保留"
        else:
            record_text = "未取得精确 InfoHash，115 离线任务记录已保留"
        details = dict(operation.get("details") or {})
        details.update({
            "offline_task_record": record_state,
            "downloaded_content": "preserved",
            "stopped_at": operation.get("stage") or "download",
        })
        return await self._report_operation(
            operation_id,
            state="cancelled",
            stage=operation.get("stage") or "download",
            status_text=(
                f"下载任务已停止；{record_text}；不删除已经下载的内容。"
            ),
            control="",
            details=details,
        )

    def _restore_downloaded_operation(self, job: dict):
        payload = job.get("result") or {}
        operation_id = str(payload.get("operation_id") or "")
        if not operation_id or operation_id in self.operations:
            return
        try:
            revision = max(1, int(payload.get("operation_revision") or 1))
            user_id = int(payload.get("user_id") or 0)
            chat_id = int(payload.get("chat_id") or user_id or 0)
        except (TypeError, ValueError):
            return
        if user_id <= 0 or chat_id == 0:
            return
        self.operations[operation_id] = {
            "operation_id": operation_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "state": "handed_off",
            "stage": "handoff_renaming",
            "status_text": "115 下载已完成，正在重试交给媒体整理。",
            "control": "cancel",
            "revision": revision,
            "details": {"downloaded_content": "preserved"},
            "next_plugin_id": "renaming",
            "kind": "download",
            "job_id": str(job.get("job_id") or ""),
            "cancel_event": threading.Event(),
            "info_hash": "",
            "offline_delete_attempted": False,
            "offline_task_record": "unknown",
            "cancel_cleanup_done": asyncio.Event(),
        }

    @staticmethod
    def _raise_if_cancelled(operation):
        cancel_event = operation.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("operation cancelled")

    def _new_operation(self, request, *, stage, status_text, control):
        operation_id = uuid.uuid4().hex
        operation = {
            "operation_id": operation_id,
            "chat_id": int(request.get("chat_id") or request.get("user_id") or 0),
            "user_id": int(request.get("user_id") or 0),
            "state": "awaiting_input",
            "stage": stage,
            "status_text": status_text,
            "control": control,
            "revision": 1,
            "details": {},
        }
        self.operations[operation_id] = operation
        return self._operation_view(operation)

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
        operation = self._advance_operation(operation_id, **changes)
        if operation["chat_id"] and operation["user_id"]:
            response = await self.core.report_operation(operation)
            if not isinstance(response, dict) or response.get("accepted") is not True:
                current = self.operations[operation_id]
                current.update({
                    "state": "interrupted",
                    "status_text": "Core 未接受当前 Feature 的任务所有权。",
                    "control": "",
                    "next_plugin_id": "",
                })
                raise FeatureError(
                    "operation_rejected",
                    "Core rejected open115 operation ownership",
                )
        return operation

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
        next_plugin_id = str(operation.get("next_plugin_id") or "")
        if next_plugin_id:
            view["next_plugin_id"] = next_plugin_id
        return view

    def _close_interaction(self, key, status_text):
        session = self.sessions.get(key)
        operation_id = session.get("operation_id") if session else None
        self._clear_auth_session(key)
        if not operation_id or operation_id not in self.operations:
            return None
        return self._advance_operation(
            operation_id,
            state="cancelled",
            stage=self.operations[operation_id].get("stage") or "interaction",
            status_text=status_text,
            control="",
        )

    def _interaction_message(self, key, text, *, kind="send_message", operation=None):
        if operation is None:
            session = self.sessions.get(key) or {}
            operation = self._operation_view(
                self.operations[session["operation_id"]]
            )
        return {
            "actions": [{
                "kind": kind,
                "text": text,
                "data": {"keyboard": [self._exit_row()]},
            }],
            "session": {"state": "open"},
            "operation": operation,
        }

    @staticmethod
    def _exit_row():
        return [{"text": "退出", "callback_data": "open115:exit"}]

    @staticmethod
    def _session_key(request):
        return int(request.get("chat_id") or 0), int(request.get("user_id") or 0)

    @staticmethod
    def _message(text):
        return {"actions": [{"kind": "send_message", "text": text}]}

    @staticmethod
    def _message_with_session(text, state, *, kind="send_message"):
        return {
            "actions": [{"kind": kind, "text": text}],
            "session": {"state": state},
        }

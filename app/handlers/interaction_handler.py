from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Mapping
from dataclasses import replace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop
from telepiplex_plugin_sdk.diagnostics import new_trace_id, set_diagnostic_context

try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init
from app.runtime.interaction_coordinator import TERMINAL_STATES, OperationRecord
from app.runtime.poster_grid import build_poster_grid
from app.runtime.telegram_text import bounded_photo_caption
from app.utils.log_sanitizer import sanitize_log_text


COORDINATOR_KEY = "telepiplex_interaction_coordinator"
ROUTER_KEY = "telepiplex_plugin_router"
OPERATION_RECOVERY_TASK_KEY = "telepiplex_operation_recovery_task"
CONFIG_OPERATION_TASKS_KEY = "telepisync_config_operation_tasks"
OPERATION_RENDER_LOCKS_KEY = "telepiplex_operation_render_locks"
FEATURE_SESSION_KEY = "telepiplex_plugin_sessions"
CONTROL_CALLBACK_PREFIX = "host-operation:"
CONTROL_CALLBACK_PATTERN = r"^host-operation:"
_CONTROL_RE = re.compile(
    r"^host-operation:(?P<action>exit|cancel|rollback):"
    r"(?P<operation_id>[A-Za-z0-9_-]{1,40})$"
)
_CONTROL_LABELS = {
    "exit": "退出",
    "cancel": "取消任务",
    "rollback": "取消并回滚",
}
_TERMINAL_CONTROL_LABELS = frozenset({
    "退出",
    "取消",
    "取消任务",
    "取消并回滚",
})
_CONTROL_IN_PROGRESS_STATES = {"cancelling", "rolling_back"}


def _log(level: str, message: str):
    logger = getattr(init, "logger", None)
    if logger is None:
        return
    method = getattr(logger, level, None) or getattr(logger, "info", None)
    if method is not None:
        method(message)


def _log_incoming_telegram_interaction(update) -> None:
    logger = getattr(init, "logger", None)
    method = getattr(logger, "info", None) if logger is not None else None
    if not callable(method):
        return
    query = getattr(update, "callback_query", None)
    message = getattr(update, "effective_message", None)
    callback_data = str(getattr(query, "data", "") or "") or None
    text = str(
        getattr(message, "text", "")
        or getattr(message, "caption", "")
        or ""
    ) or None
    kind = (
        "callback"
        if query is not None
        else "command"
        if text and text.lstrip().startswith("/")
        else "message"
        if text
        else "update"
    )
    method(
        "收到 Telegram 交互",
        event_name="telegram.interaction.received",
        diagnostic_fields={
            "stage": "telegram_update",
            "status": "received",
            "input": {
                "update_id": getattr(update, "update_id", None),
                "chat_id": getattr(
                    getattr(update, "effective_chat", None), "id", None
                ),
                "user_id": getattr(
                    getattr(update, "effective_user", None), "id", None
                ),
                "message_id": getattr(message, "message_id", None),
            },
            "user_surface": {
                "direction": "incoming",
                "kind": kind,
                "text": text,
                "callback_data": callback_data,
            },
        },
    )


def operation_accepts_text(bot_data: dict, record, chat_id: int, user_id: int) -> bool:
    if record is None or str(getattr(record, "state", "")) != "awaiting_input":
        return False
    sessions = bot_data.get(FEATURE_SESSION_KEY)
    session = (
        sessions.get((int(chat_id), int(user_id)))
        if isinstance(sessions, dict)
        else None
    )
    if not isinstance(session, dict):
        return False
    try:
        expires_at = float(session.get("expires_at") or 0)
    except (TypeError, ValueError):
        return False
    return (
        expires_at > time.time()
        and str(session.get("plugin_id") or "")
        == str(getattr(record, "plugin_id", "") or "")
    )


def operation_render_lock(application, operation_id: str) -> asyncio.Lock:
    bot_data = getattr(application, "bot_data", None)
    if not isinstance(bot_data, dict):
        raise RuntimeError("application bot_data is unavailable")
    locks = bot_data.setdefault(OPERATION_RENDER_LOCKS_KEY, {})
    key = str(operation_id or "")
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


class OperationMilestoneSink:
    def __init__(self, coordinator, delivery, lock_factory=None):
        self.coordinator = coordinator
        self.delivery = delivery
        self.lock_factory = lock_factory
        self._workers: dict[tuple[str, str], asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._started = False

    def attach(self, delivery, lock_factory=None):
        self.delivery = delivery
        self.lock_factory = lock_factory

    async def start(self):
        if self._started:
            return
        recoverable = self.coordinator.recover_milestones()
        self._started = True
        for intent in recoverable:
            self._schedule(intent)

    async def __call__(self, plugin_id: str, payload: dict) -> dict:
        intent, duplicate = self.coordinator.enqueue_milestone(
            plugin_id, payload
        )
        if (
            self._started
            and intent.delivery_state in {"pending", "failed"}
            and intent.attempt_count < 3
        ):
            self._schedule(intent)
        return {
            "accepted": True,
            "queued": True,
            "duplicate": duplicate,
        }

    def _schedule(self, intent):
        key = (intent.operation_id, intent.milestone_id)
        current = self._workers.get(key)
        if current is not None and not current.done():
            return current
        task = asyncio.create_task(
            self._deliver(key),
            name=f"telepiplex-milestone-{intent.operation_id}-{intent.milestone_id}",
        )
        self._workers[key] = task
        self._tasks.add(task)
        task.add_done_callback(
            lambda completed, owned_key=key: self._worker_done(
                owned_key, completed
            )
        )
        return task

    def _worker_done(self, key, task):
        if self._workers.get(key) is task:
            self._workers.pop(key, None)
        self._tasks.discard(task)
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def _deliver(self, key: tuple[str, str]):
        operation_id, milestone_id = key
        while True:
            intent = self.coordinator.claim_milestone_delivery(
                operation_id, milestone_id
            )
            if intent is None:
                return
            try:
                record = self.coordinator.milestone_delivery_record(intent)
                operation_lock = (
                    self.lock_factory(operation_id)
                    if self.lock_factory is not None
                    else None
                )
                if operation_lock is None:
                    result = await self._invoke_delivery(record, intent)
                else:
                    async with operation_lock:
                        result = await self._invoke_delivery(record, intent)
            except asyncio.CancelledError:
                self._mark_unknown(intent, "cancelled")
                raise
            except Exception as exc:
                self._mark_unknown(intent, type(exc).__name__)
                _log(
                    "error",
                    "Telegram 里程碑投影结果不确定："
                    f"operation_id={operation_id}, "
                    f"milestone_id={milestone_id}, "
                    f"error={type(exc).__name__}",
                )
                return
            if _milestone_delivery_failed(result):
                rejected = self.coordinator.reject_milestone_delivery(
                    intent.plugin_id,
                    operation_id,
                    milestone_id,
                    "telegram_rejected",
                )
                if rejected.attempt_count < 3:
                    continue
                return
            if not _milestone_delivery_succeeded(result):
                self._mark_unknown(intent, "invalid_delivery_result")
                return
            try:
                target = _milestone_delivery_target(result, record)
            except (TypeError, ValueError, OverflowError):
                self._mark_unknown(intent, "invalid_delivery_target")
                return
            if target is not None:
                try:
                    self.coordinator.record_milestone_delivery_target(
                        intent.plugin_id,
                        operation_id,
                        milestone_id,
                        target[0],
                        target[1],
                    )
                except Exception as exc:
                    durable = self.coordinator.get_milestone(
                        operation_id, milestone_id
                    )
                    if (
                        durable is None
                        or durable.delivered_message_id is None
                    ):
                        self._mark_unknown(intent, type(exc).__name__)
                        return
            try:
                self.coordinator.complete_milestone_delivery(
                    intent.plugin_id,
                    operation_id,
                    milestone_id,
                )
            except Exception as exc:
                durable = self.coordinator.get_milestone(
                    operation_id, milestone_id
                )
                if durable is not None and durable.delivery_state == "delivered":
                    return
                if durable is not None and durable.delivered_message_id is not None:
                    try:
                        self.coordinator.complete_milestone_delivery(
                            intent.plugin_id,
                            operation_id,
                            milestone_id,
                        )
                    except Exception:
                        _log(
                            "error",
                            "Telegram 里程碑目标已记录但封口待恢复："
                            f"operation_id={operation_id}, "
                            f"milestone_id={milestone_id}",
                        )
                    return
                self._mark_unknown(intent, type(exc).__name__)
            return

    async def _invoke_delivery(self, record, intent):
        result = self.delivery(
            record,
            intent.mode,
            intent.photo_url or None,
            intent.text,
        )
        if inspect.isawaitable(result):
            result = await result
        return result

    def _mark_unknown(self, intent, error_code):
        current = self.coordinator.get_milestone(
            intent.operation_id, intent.milestone_id
        )
        if current is None or current.delivery_state != "delivering":
            return
        try:
            self.coordinator.mark_milestone_delivery_unknown(
                intent.plugin_id,
                intent.operation_id,
                intent.milestone_id,
                error_code,
            )
        except Exception:
            pass

    async def drain(self, timeout: float | None = None) -> bool:
        deadline = (
            asyncio.get_running_loop().time() + max(0, float(timeout))
            if timeout is not None
            else None
        )
        while self._tasks:
            tasks = tuple(self._tasks)
            remaining = (
                max(0, deadline - asyncio.get_running_loop().time())
                if deadline is not None
                else None
            )
            done, pending = await asyncio.wait(tasks, timeout=remaining)
            for task in done:
                try:
                    task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            if pending and deadline is not None:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.sleep(0)
                return False
            await asyncio.sleep(0)
        return True


def _milestone_delivery_failed(result) -> bool:
    if isinstance(result, Mapping):
        return result.get("accepted") is False
    return result is False


def _milestone_delivery_succeeded(result) -> bool:
    if isinstance(result, Mapping):
        return result.get("accepted") is True
    return result is True


def _milestone_delivery_target(
    result,
    record: OperationRecord,
) -> tuple[int, str] | None:
    del record
    if not isinstance(result, Mapping):
        return None
    raw_message_id = result.get("message_id")
    raw_message_kind = result.get("message_kind")
    message_id_empty = raw_message_id is None or (
        isinstance(raw_message_id, str) and not raw_message_id.strip()
    )
    message_kind = str(raw_message_kind or "").strip().casefold()
    message_kind_empty = not message_kind
    if message_id_empty and message_kind_empty:
        return None
    if message_id_empty or message_kind_empty:
        raise ValueError("milestone delivery target is incomplete")
    if isinstance(raw_message_id, bool):
        raise ValueError("milestone delivery message ID is invalid")
    if isinstance(raw_message_id, int):
        message_id = raw_message_id
    elif isinstance(raw_message_id, str) and re.fullmatch(
        r"[1-9][0-9]*", raw_message_id.strip()
    ):
        message_id = int(raw_message_id.strip())
    else:
        raise ValueError("milestone delivery message ID is invalid")
    if message_id <= 0 or message_kind not in {"text", "photo"}:
        raise ValueError("milestone delivery target is invalid")
    return message_id, message_kind


def _milestone_delivery_result(
    message_id: int | None,
    message_kind: str,
) -> dict:
    result = {"accepted": True}
    if message_id is not None:
        result.update({
            "message_id": int(message_id),
            "message_kind": str(message_kind),
        })
    return result


def _milestone_title(text: str) -> str:
    first_line = str(text or "").splitlines()[0].strip()
    return first_line.removeprefix("🎬").strip() or "未知作品"


async def deliver_operation_milestone(
    application,
    record_or_chat_id,
    mode_or_photo_url,
    photo_url_or_text,
    text: str | None = None,
) -> bool | dict:
    if isinstance(record_or_chat_id, OperationRecord):
        record = record_or_chat_id
        chat_id = record.chat_id
        mode = str(mode_or_photo_url or "identity").casefold()
        photo_url = str(photo_url_or_text or "") or None
        rendered_text = str(text or "")
    else:
        record = None
        chat_id = int(record_or_chat_id)
        mode = "identity"
        photo_url = str(mode_or_photo_url or "") or None
        rendered_text = str(photo_url_or_text or "")

    operation_id = record.operation_id if record is not None else ""
    if operation_id:
        set_diagnostic_context(operation_id=operation_id)

    def log_delivery(event_name, status, action, *, result=None, error=None):
        logger = init.logger
        if logger is None:
            return
        fields = {
            "stage": "telegram_milestone",
            "status": status,
            "input": {
                "operation_id": operation_id or None,
                "chat_id": chat_id,
                "mode": mode,
                "existing_message_id": (
                    record.message_id if record is not None else None
                ),
            },
            "user_surface": {
                "direction": "outgoing",
                "action": action,
                "text": rendered_text,
                "photo_url": photo_url,
            },
        }
        if result is not None:
            fields["output"] = dict(result) if isinstance(result, dict) else {"accepted": result}
        if error is not None:
            fields.setdefault("output", {})["error_type"] = type(error).__name__
        method = logger.error if status == "failed" else logger.info
        method(
            "Telegram 里程碑消息发送失败"
            if status == "failed" else
            "Telegram 里程碑消息已送达"
            if status == "completed" else
            "开始投递 Telegram 里程碑消息",
            event_name=event_name,
            diagnostic_fields=fields,
        )

    def complete_delivery(message_id, message_kind, action):
        result = _milestone_delivery_result(message_id, message_kind)
        log_delivery(
            "telegram.milestone.delivery.completed",
            "completed",
            action,
            result=result,
        )
        return result

    initial_action = (
        "edit_message"
        if mode == "stage" and record is not None and record.message_id is not None
        else "send_message"
        if mode == "stage"
        else "edit_photo"
        if record is not None and record.message_id is not None
        else "send_photo"
    )
    log_delivery(
        "telegram.milestone.delivery.started",
        "started",
        initial_action,
    )

    if mode == "stage":
        if record is not None and record.message_id is not None:
            try:
                if record.message_kind == "photo":
                    await application.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=record.message_id,
                        caption=rendered_text[:1024],
                        reply_markup=None,
                    )
                else:
                    await application.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=record.message_id,
                        text=rendered_text,
                        reply_markup=None,
                    )
                return complete_delivery(
                    record.message_id,
                    record.message_kind or "text",
                    "edit_message",
                )
            except BadRequest as exc:
                if _message_not_modified(exc):
                    return complete_delivery(
                        record.message_id,
                        record.message_kind or "text",
                        "edit_message",
                    )
                _log(
                    "warn",
                    "任务阶段封口编辑失败，改发新消息："
                    f"operation_id={record.operation_id}, "
                    f"message_id={record.message_id}, "
                    f"error={_render_error(exc)}",
                )
                await _clear_message_keyboard(application, record)
        try:
            sent = await application.bot.send_message(
                chat_id=chat_id,
                text=rendered_text,
            )
            return complete_delivery(
                getattr(sent, "message_id", None),
                "text",
                "send_message",
            )
        except BadRequest as exc:
            _log(
                "error",
                "任务阶段封口消息发送失败："
                f"chat_id={chat_id}, error={_render_error(exc)}",
            )
            log_delivery(
                "telegram.milestone.delivery.failed",
                "failed",
                "send_message",
                result=False,
                error=exc,
            )
            return {
                "accepted": False,
                "error_code": "telegram_bad_request",
            }

    poster_items = [{
        "number": 1,
        "title": _milestone_title(rendered_text),
        "poster_url": str(photo_url or ""),
    }]
    photo = None
    try:
        try:
            photo = await asyncio.to_thread(
                build_poster_grid,
                poster_items,
            )
        except Exception:
            if photo_url:
                poster_items[0]["poster_url"] = ""
                photo = await asyncio.to_thread(
                    build_poster_grid,
                    poster_items,
                )
            else:
                raise
    except Exception as exc:
        _log(
            "warn",
            "任务身份海报本地构建失败，降级为文本："
            f"chat_id={chat_id}, error={_render_error(exc)}",
        )
    if photo is not None:
        try:
            caption, _parse_mode = bounded_photo_caption(rendered_text, None)
            media = InputMediaPhoto(media=photo, caption=caption)
        except Exception as exc:
            _log(
                "warn",
                "任务身份海报本地组装失败，降级为文本："
                f"chat_id={chat_id}, error={_render_error(exc)}",
            )
            photo = None
    if photo is not None:
        if record is not None and record.message_id is not None:
            if record.message_kind == "photo":
                try:
                    await application.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=record.message_id,
                        media=media,
                        reply_markup=None,
                    )
                    return complete_delivery(
                        record.message_id,
                        "photo",
                        "edit_photo",
                    )
                except BadRequest as exc:
                    if _message_not_modified(exc):
                        return complete_delivery(
                            record.message_id,
                            "photo",
                            "edit_photo",
                        )
                    _log(
                        "warn",
                        "任务身份海报编辑失败，改发新消息："
                        f"operation_id={record.operation_id}, "
                        f"message_id={record.message_id}, "
                        f"error={_render_error(exc)}",
                    )
            await _clear_message_keyboard(application, record)
        try:
            sent = await application.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
            )
        except BadRequest as exc:
            _log(
                "warn",
                "任务身份海报被 Telegram 拒绝，降级为文本："
                f"chat_id={chat_id}, error={_render_error(exc)}",
            )
        else:
            return complete_delivery(
                getattr(sent, "message_id", None),
                "photo",
                "send_photo",
            )
    try:
        sent = await application.bot.send_message(
            chat_id=chat_id,
            text=rendered_text,
        )
    except BadRequest as exc:
        _log(
            "error",
            "任务身份消息发送失败："
            f"chat_id={chat_id}, error={_render_error(exc)}",
        )
        log_delivery(
            "telegram.milestone.delivery.failed",
            "failed",
            "send_message",
            result=False,
            error=exc,
        )
        return {
            "accepted": False,
            "error_code": "telegram_bad_request",
        }
    return complete_delivery(
        getattr(sent, "message_id", None),
        "text",
        "send_message",
    )


class OperationReportSink:
    def __init__(self, coordinator, router=None):
        self.coordinator = coordinator
        self.router = router
        self._listener = None
        self._pending: dict[str, OperationRecord] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()

    def attach(self, listener):
        self._listener = listener

    async def __call__(self, plugin_id: str, report: dict) -> dict:
        unavailable = self._unavailable_handoff(report)
        if unavailable is not None:
            return unavailable
        record = self.coordinator.report(plugin_id, report)
        try:
            submitted_revision = int(report.get("revision"))
        except (TypeError, ValueError):
            submitted_revision = 0
        accepted = (
            submitted_revision == record.revision
            and str(plugin_id) == record.plugin_id
            and int(report.get("chat_id") or 0) == record.chat_id
            and int(report.get("user_id") or 0) == record.user_id
            and str(report.get("state") or "") == record.state
            and str(report.get("stage") or "") == record.stage
            and str(report.get("control") or "") == record.control
            and str(report.get("next_plugin_id") or "")
            == record.next_plugin_id
        )
        if accepted and self._listener is not None:
            pending = self._pending.get(record.operation_id)
            if pending is None or record.revision >= pending.revision:
                self._pending[record.operation_id] = record
            self._ensure_worker(record.operation_id)
        return {
            "accepted": accepted,
            "operation_id": record.operation_id,
            "state": record.state,
            "revision": record.revision,
        }

    def _unavailable_handoff(self, report: dict) -> dict | None:
        if (
            self.router is None
            or not isinstance(report, dict)
            or str(report.get("state") or "") != "handed_off"
        ):
            return None
        target = str(report.get("next_plugin_id") or "").strip()
        if not target:
            return None
        current = self.coordinator.get(
            str(report.get("operation_id") or "")
        )
        if current is not None and current.state in TERMINAL_STATES:
            return None
        if self.router.plugin_route(target) is not None:
            return None
        return {
            "accepted": False,
            "operation_id": str(report.get("operation_id") or ""),
            "state": current.state if current is not None else "",
            "revision": current.revision if current is not None else 0,
            "error_code": "handoff_target_unavailable",
            "target_plugin_id": target,
        }

    def _ensure_worker(self, operation_id: str):
        current = self._workers.get(operation_id)
        if current is not None and not current.done():
            return current
        task = asyncio.create_task(
            self._render_pending(operation_id),
            name=f"telepiplex-operation-render-{operation_id}",
        )
        self._workers[operation_id] = task
        self._tasks.add(task)
        task.add_done_callback(
            lambda completed, key=operation_id: self._worker_done(
                key, completed
            )
        )
        return task

    async def _render_pending(self, operation_id: str):
        while True:
            record = self._pending.pop(operation_id, None)
            if record is None:
                return
            try:
                result = self._listener(record)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(
                    "error",
                    "Feature 任务状态渲染失败："
                    f"operation_id={record.operation_id}, "
                    f"error={type(exc).__name__}",
                )

    def _worker_done(self, operation_id: str, task: asyncio.Task):
        if self._workers.get(operation_id) is task:
            self._workers.pop(operation_id, None)
        self._tasks.discard(task)
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        if operation_id in self._pending and self._listener is not None:
            self._ensure_worker(operation_id)

    async def drain(self, timeout: float | None = None) -> bool:
        deadline = (
            asyncio.get_running_loop().time() + max(0, float(timeout))
            if timeout is not None
            else None
        )
        while self._tasks or self._pending:
            for operation_id in tuple(self._pending):
                self._ensure_worker(operation_id)
            tasks = tuple(self._tasks)
            if not tasks:
                await asyncio.sleep(0)
                continue
            remaining = (
                max(0, deadline - asyncio.get_running_loop().time())
                if deadline is not None
                else None
            )
            done, pending = await asyncio.wait(tasks, timeout=remaining)
            for task in done:
                try:
                    task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            if pending and deadline is not None:
                self._pending.clear()
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.sleep(0)
                return False
            await asyncio.sleep(0)
        return True


async def operation_gate(update, context):
    update_id = getattr(update, "update_id", None)
    set_diagnostic_context(
        trace_id=f"TG-{update_id}" if update_id is not None else new_trace_id(),
        request_id=f"telegram-update:{update_id}" if update_id is not None else None,
    )
    _log_incoming_telegram_interaction(update)
    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)
    if chat is None or user is None:
        return
    bot_data = getattr(context.application, "bot_data", {})
    coordinator = bot_data.get(COORDINATOR_KEY)
    if coordinator is None:
        return
    record = coordinator.active(int(chat.id), int(user.id))
    if record is None:
        return
    set_diagnostic_context(operation_id=record.operation_id)

    query = getattr(update, "callback_query", None)
    if query is not None:
        data = str(getattr(query, "data", "") or "")
        control = _CONTROL_RE.fullmatch(data)
        if control is not None and control.group("operation_id") == record.operation_id:
            return
        running_interaction = bool(
            record.state == "running"
            and record.stage == "prowlarr_search"
            and record.details.get("allow_running_callbacks") is True
        )
        if record.state == "awaiting_input" or running_interaction:
            router = bot_data.get(ROUTER_KEY)
            allowed = {
                str(button.callback_data)
                for row in _feature_status_rows(record, router)
                for button in row
            }
            callback_message_id = getattr(
                getattr(query, "message", None), "message_id", None
            )
            if (
                data in allowed
                and record.message_id is not None
                and callback_message_id == record.message_id
            ):
                return
        await query.answer("当前任务执行中")
        raise ApplicationHandlerStop

    message = getattr(update, "effective_message", None)
    text = str(getattr(message, "text", "") or "")
    if text and not text.lstrip().startswith("/"):
        if operation_accepts_text(
            bot_data,
            record,
            int(chat.id),
            int(user.id),
        ):
            return
        reply_text = getattr(message, "reply_text", None)
        if callable(reply_text):
            if (
                record.state == "awaiting_input"
                and isinstance(record.details.get("keyboard"), list)
            ):
                await reply_text(
                    f"⚠️ 当前 {record.plugin_id} 任务正在等待按钮操作；"
                    "请先完成或退出。"
                )
            else:
                await reply_text(
                    f"⚠️ 当前 {record.plugin_id} 任务正在执行；"
                    "请先等待完成或取消。"
                )
    raise ApplicationHandlerStop


async def operation_control_callback(update, context):
    query = update.callback_query
    match = _CONTROL_RE.fullmatch(str(getattr(query, "data", "") or ""))
    if match is None:
        await query.answer("任务控制请求无效")
        return
    coordinator = context.application.bot_data.get(COORDINATOR_KEY)
    if coordinator is None:
        await query.answer("任务协调器不可用")
        return
    record = coordinator.get(match.group("operation_id"))
    if record is None or (
        record.chat_id != int(update.effective_chat.id)
        or record.user_id != int(update.effective_user.id)
    ):
        await query.answer("任务状态已变化")
        return
    if record.state in TERMINAL_STATES:
        await query.answer("任务已结束")
        await render_operation(context.application, None, record)
        return
    if record.state in _CONTROL_IN_PROGRESS_STATES:
        await query.answer("任务正在取消")
        await render_operation(context.application, None, record)
        return
    action = match.group("action")
    if action != record.control:
        await query.answer("任务状态已更新")
        await render_operation(context.application, None, record)
        return
    config_tasks = context.application.bot_data.get(CONFIG_OPERATION_TASKS_KEY)
    config_task = (
        config_tasks.get(record.operation_id)
        if isinstance(config_tasks, dict)
        else None
    )
    if action == "rollback" and isinstance(config_task, dict):
        config_task["cancel_event"].set()
        rolling = coordinator.report(record.plugin_id, {
            "operation_id": record.operation_id,
            "chat_id": record.chat_id,
            "user_id": record.user_id,
            "state": "rolling_back",
            "stage": "config_apply",
            "status_text": (
                "回滚请求已接受；正在停止配置切换并恢复原配置。"
            ),
            "control": "",
            "revision": record.revision + 1,
            "details": dict(record.details),
        })
        await query.answer("正在回滚配置...")
        await render_operation(context.application, None, rolling)
        return
    await query.answer("处理中...")
    # Answering a Telegram callback yields control.  A Feature handoff may be
    # accepted while that happens, so route the request from a fresh ownership
    # snapshot instead of the record used to validate the button.
    current = coordinator.get(record.operation_id)
    if current is None:
        return
    if current.state in TERMINAL_STATES or current.state in _CONTROL_IN_PROGRESS_STATES:
        await render_operation(context.application, None, current)
        return
    if current.control != action:
        await render_operation(context.application, None, current)
        return
    record = current
    router = context.application.bot_data.get(ROUTER_KEY)
    route = router.plugin_route(record.plugin_id) if router is not None else None
    if route is None:
        return

    try:
        result = None
        deadline_at = asyncio.get_running_loop().time() + 30
        seen_snapshots = set()
        seen_owners = set()
        while len(seen_snapshots) < 8 and len(seen_owners) < 4:
            dispatched = record
            dispatched_key = (dispatched.plugin_id, dispatched.revision)
            seen_snapshots.add(dispatched_key)
            seen_owners.add(dispatched.plugin_id)
            remaining = deadline_at - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("operation control deadline exceeded")
            try:
                result = await route.client.request(
                    "operation.control",
                    {
                        "operation_id": dispatched.operation_id,
                        "action": action,
                        "revision": dispatched.revision,
                    },
                    deadline=remaining,
                    idempotency_key=(
                        f"operation-control:{dispatched.operation_id}:{action}"
                    ),
                )
            except Exception:
                latest = coordinator.get(dispatched.operation_id)
                next_route = (
                    router.plugin_route(latest.plugin_id)
                    if latest is not None and router is not None else None
                )
                if (
                    latest is not None
                    and (
                        latest.plugin_id != dispatched.plugin_id
                        or latest.revision != dispatched.revision
                    )
                    and latest.state not in TERMINAL_STATES
                    and latest.state not in _CONTROL_IN_PROGRESS_STATES
                    and latest.control == action
                    and next_route is not None
                    and (latest.plugin_id, latest.revision) not in seen_snapshots
                    and (
                        latest.plugin_id in seen_owners
                        or len(seen_owners) < 4
                    )
                ):
                    record = latest
                    route = next_route
                    continue
                raise
            latest = coordinator.get(dispatched.operation_id)
            ownership_changed = latest is not None and (
                latest.plugin_id != dispatched.plugin_id
                or latest.revision != dispatched.revision
            )
            if not (
                ownership_changed
                and latest.state not in TERMINAL_STATES
                and latest.state not in _CONTROL_IN_PROGRESS_STATES
                and latest.control == action
                and (latest.plugin_id, latest.revision) not in seen_snapshots
                and (
                    latest.plugin_id in seen_owners
                    or len(seen_owners) < 4
                )
            ):
                record = dispatched
                break
            next_route = (
                router.plugin_route(latest.plugin_id)
                if router is not None else None
            )
            if next_route is None:
                record = dispatched
                break
            record = latest
            route = next_route
        normalized = _normalize_control_result(record, result)
        from app.handlers.plugin_handler import handle_feature_result

        await handle_feature_result(update, context, route, normalized)
    except Exception as exc:
        _log(
            "error",
            "Feature 任务控制失败："
            f"operation_id={record.operation_id}, error={type(exc).__name__}",
        )
        try:
            await context.application.bot.send_message(
                chat_id=record.chat_id,
                text="❌ 任务控制请求未被执行器接受；任务状态未改变。",
            )
        except Exception:
            pass


def _normalize_control_result(record: OperationRecord, result: dict) -> dict:
    if not isinstance(result, dict):
        raise ValueError("operation control result must be an object")
    operation = result.get("operation") if isinstance(result.get("operation"), dict) else result
    base = {
        "operation_id": record.operation_id,
        "chat_id": record.chat_id,
        "user_id": record.user_id,
        "state": record.state,
        "stage": record.stage,
        "status_text": record.status_text,
        "control": record.control,
        "revision": record.revision,
        "details": dict(record.details),
    }
    for key in base:
        if key in operation:
            base[key] = operation[key]
    normalized = dict(result) if "operation" in result else {"actions": []}
    normalized.setdefault("actions", [])
    normalized["operation"] = base
    return normalized


def operation_markup(record: OperationRecord, router=None):
    if record.state in TERMINAL_STATES:
        return None
    rows = deduplicate_terminal_controls(
        _feature_status_rows(record, router)
    )
    explicit_control = any(
        button.text in set(_CONTROL_LABELS.values()) | {"取消"}
        for row in rows
        for button in row
    )
    if (
        record.state not in TERMINAL_STATES
        and record.control
        and not explicit_control
    ):
        label = _CONTROL_LABELS.get(record.control)
        callback_data = (
            f"{CONTROL_CALLBACK_PREFIX}{record.control}:{record.operation_id}"
        )
        if label is not None and len(callback_data.encode("utf-8")) <= 64:
            rows.append([InlineKeyboardButton(label, callback_data=callback_data)])
    return InlineKeyboardMarkup(rows) if rows else None


def deduplicate_terminal_controls(rows):
    seen_callbacks = set()
    result = []
    for row in rows:
        buttons = []
        for button in row:
            callback_data = str(button.callback_data or "")
            if button.text in _TERMINAL_CONTROL_LABELS:
                if callback_data in seen_callbacks:
                    continue
                seen_callbacks.add(callback_data)
            buttons.append(button)
        if buttons:
            result.append(buttons)
    return result


def _feature_status_rows(record: OperationRecord, router):
    keyboard = record.details.get("keyboard")
    if not isinstance(keyboard, list) or router is None:
        return []
    route = router.plugin_route(record.plugin_id)
    if route is None or route.plugin_id != record.plugin_id:
        return []
    namespaces = set(getattr(route.manifest, "callbacks", ()))
    rows = []
    for raw_row in keyboard[:10]:
        if not isinstance(raw_row, list):
            continue
        buttons = []
        for raw_button in raw_row[:8]:
            if not isinstance(raw_button, dict):
                continue
            text = str(raw_button.get("text") or "").strip()
            callback_data = str(raw_button.get("callback_data") or "")
            namespace, separator, _payload = callback_data.partition(":")
            if (
                text
                and separator
                and namespace in namespaces
                and len(callback_data.encode("utf-8")) <= 64
            ):
                buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        if buttons:
            rows.append(buttons)
    return rows


async def render_operation(application, _router, record: OperationRecord):
    async with operation_render_lock(application, record.operation_id):
        return await _render_operation_locked(application, _router, record)


async def _render_operation_locked(application, _router, record: OperationRecord):
    coordinator = application.bot_data.get(COORDINATOR_KEY)
    if coordinator is None:
        return None
    current = coordinator.get(record.operation_id)
    if current is None:
        return None
    # The report fields remain the supplied immutable snapshot.  A cursor
    # created by another renderer under the same owner/revision is presentation
    # state, so it may be overlaid without collapsing to a newer business
    # revision.  The post-send write below still uses owner+revision CAS.
    if (
        current.plugin_id == record.plugin_id
        and current.revision == record.revision
        and (
            current.message_id != record.message_id
            or current.message_kind != record.message_kind
        )
    ):
        record = replace(
            record,
            message_id=current.message_id,
            message_kind=current.message_kind,
        )
    text = record.status_text or (
        f"任务状态：{record.state}\n阶段：{record.stage or '-'}"
    )
    markup = operation_markup(record, _router)
    poster_items = _operation_poster_items(record.details)
    photo_url = _operation_photo_url(record.details)
    if poster_items or photo_url:
        parse_mode = (
            record.details.get("parse_mode")
            if record.details.get("parse_mode") in {
                "HTML",
                "MarkdownV2",
            }
            else None
        )
        caption, parse_mode = bounded_photo_caption(text, parse_mode)

        async def photo_media():
            if poster_items:
                return await asyncio.to_thread(
                    build_poster_grid,
                    poster_items,
                )
            return photo_url

        if record.message_id is not None:
            if record.message_kind == "photo":
                try:
                    await application.bot.edit_message_media(
                        chat_id=record.chat_id,
                        message_id=record.message_id,
                        media=InputMediaPhoto(
                            media=await photo_media(),
                            caption=caption,
                            parse_mode=parse_mode,
                        ),
                        reply_markup=markup,
                    )
                    return record.message_id
                except Exception as exc:
                    if _message_not_modified(exc):
                        return record.message_id
                    _log(
                        "warn",
                        "任务候选海报编辑失败，改发新消息："
                        f"operation_id={record.operation_id}, "
                        f"message_id={record.message_id}, "
                        f"message_kind={record.message_kind}, "
                        f"error={_render_error(exc)}",
                    )
                    await _clear_message_keyboard(application, record)
            else:
                await _clear_message_keyboard(application, record)
        try:
            send_photo_kwargs = {
                "chat_id": record.chat_id,
                "photo": await photo_media(),
                "caption": caption,
                "reply_markup": markup,
            }
            if parse_mode:
                send_photo_kwargs["parse_mode"] = parse_mode
            message = await application.bot.send_photo(
                **send_photo_kwargs,
            )
        except Exception as exc:
            _log(
                "warn",
                "任务候选海报发送失败，降级为文本："
                f"operation_id={record.operation_id}, "
                f"message_id={record.message_id}, "
                f"message_kind={record.message_kind}, "
                f"error={_render_error(exc)}",
            )
        else:
            message_id = getattr(message, "message_id", None)
            if isinstance(message_id, int) and message_id > 0:
                coordinator.set_message_id_if_current(
                    record.operation_id,
                    record.plugin_id,
                    record.revision,
                    message_id,
                    "photo",
                )
                return message_id
    if record.message_id is not None:
        if record.message_kind == "text":
            try:
                await application.bot.edit_message_text(
                    chat_id=record.chat_id,
                    message_id=record.message_id,
                    text=text,
                    reply_markup=markup,
                )
                return record.message_id
            except Exception as exc:
                if _message_not_modified(exc):
                    return record.message_id
                _log(
                    "warn",
                    "任务状态消息编辑失败，改发新消息："
                    f"operation_id={record.operation_id}, "
                    f"message_id={record.message_id}, "
                    f"message_kind={record.message_kind}, "
                    f"error={_render_error(exc)}",
                )
                await _clear_message_keyboard(application, record)
        else:
            await _clear_message_keyboard(application, record)
    try:
        message = await application.bot.send_message(
            chat_id=record.chat_id,
            text=text,
            reply_markup=markup,
        )
    except Exception as exc:
        _log(
            "error",
            "任务状态消息发送失败："
            f"operation_id={record.operation_id}, "
            f"message_id={record.message_id}, "
            f"message_kind={record.message_kind}, "
            f"error={_render_error(exc)}",
        )
        return None
    message_id = getattr(message, "message_id", None)
    if isinstance(message_id, int) and message_id > 0:
        coordinator.set_message_id_if_current(
            record.operation_id,
            record.plugin_id,
            record.revision,
            message_id,
            "text",
        )
        return message_id
    return None


def _render_error(exc: Exception) -> str:
    return sanitize_log_text(
        f"{type(exc).__name__}: {exc}",
        max_chars=500,
    )


def _message_not_modified(exc: Exception) -> bool:
    return "message is not modified" in str(exc).casefold()


async def _clear_message_keyboard(application, record: OperationRecord):
    if record.message_id is None:
        return
    try:
        await application.bot.edit_message_reply_markup(
            chat_id=record.chat_id,
            message_id=record.message_id,
            reply_markup=None,
        )
    except Exception:
        pass


def _operation_photo_url(details) -> str:
    if not isinstance(details, Mapping):
        return ""
    photo_url = str(details.get("photo_url") or "").strip()
    if (
        photo_url.startswith("https://")
        and len(photo_url) <= 2048
        and not any(character.isspace() for character in photo_url)
    ):
        return photo_url
    return ""


def _operation_poster_items(details) -> list[dict]:
    if not isinstance(details, Mapping):
        return []
    raw_items = details.get("poster_items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 6:
        return []
    result = []
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, Mapping):
            return []
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            return []
        title = " ".join(str(item.get("title") or "").split())
        poster_url = str(item.get("poster_url") or "").strip()
        if (
            number != index
            or not title
            or (
                poster_url
                and (
                    not poster_url.startswith("https://")
                    or len(poster_url) > 2048
                    or any(
                        character.isspace()
                        for character in poster_url
                    )
                )
            )
        ):
            return []
        result.append({
            "number": number,
            "title": title[:200],
            "poster_url": poster_url,
        })
    return result


async def recover_active_operations(application, router, coordinator):
    confirmed: set[str] = set()
    deferred: set[str] = set()
    rendered: list[OperationRecord] = []
    baseline_records = coordinator.active_records()
    baseline = {
        record.operation_id: (record.plugin_id, record.revision)
        for record in baseline_records
    }
    for record in baseline_records:
        route = router.plugin_route(record.plugin_id) if router is not None else None
        if route is None:
            continue
        try:
            snapshot = await route.client.request(
                "operation.snapshot",
                {"operation_id": record.operation_id},
                deadline=10,
                idempotency_key=f"operation-snapshot:{record.operation_id}",
            )
            report = _snapshot_report(snapshot, record.operation_id)
            if report is None:
                continue
            current = coordinator.report(route.plugin_id, report)
            rendered.append(current)
            if current.state not in TERMINAL_STATES:
                confirmed.add(current.operation_id)
                if current.state == "awaiting_input":
                    sessions = application.bot_data.setdefault(
                        "telepiplex_plugin_sessions", {}
                    )
                    sessions[(current.chat_id, current.user_id)] = {
                        "plugin_id": current.plugin_id,
                        "expires_at": time.time() + 30 * 60,
                    }
        except Exception as exc:
            deferred.add(record.operation_id)
            _log(
                "warn",
                "Feature 任务恢复确认失败："
                f"operation_id={record.operation_id}, error={type(exc).__name__}",
            )
    for operation_id, expected in baseline.items():
        current = coordinator.get(operation_id)
        if (
            current is not None
            and current.state not in TERMINAL_STATES
            and (current.plugin_id, current.revision) != expected
        ):
            deferred.add(operation_id)
    interrupted = coordinator.interrupt_unconfirmed(
        confirmed | deferred,
        expected=baseline,
    )
    deferred_records = [
        coordinator.get(operation_id) for operation_id in sorted(deferred)
    ]
    for record in [
        *rendered,
        *(item for item in deferred_records if item is not None),
        *interrupted,
    ]:
        await render_operation(application, router, record)
    return {
        "confirmed": sorted(confirmed),
        "deferred": sorted(deferred),
        "interrupted": interrupted,
    }


async def reconcile_deferred_operations(
    application,
    router,
    coordinator,
    *,
    retry_interval=5,
    max_attempts=3,
):
    """Keep a persisted gate closed until its Feature snapshot is authoritative."""
    failures: dict[tuple[str, str, int], int] = {}
    while True:
        result = await recover_active_operations(application, router, coordinator)
        if not result["deferred"]:
            return result
        current_keys = set()
        exhausted = {}
        live_deferred = []
        for operation_id in result["deferred"]:
            record = coordinator.get(operation_id)
            if record is None or record.state in TERMINAL_STATES:
                continue
            live_deferred.append(operation_id)
            key = (record.operation_id, record.plugin_id, record.revision)
            current_keys.add(key)
            failures[key] = failures.get(key, 0) + 1
            if failures[key] >= max(1, int(max_attempts)):
                exhausted[record.operation_id] = (
                    record.plugin_id, record.revision
                )
        result["deferred"] = live_deferred
        if not live_deferred:
            return result
        failures = {
            key: count for key, count in failures.items()
            if key in current_keys
        }
        if exhausted:
            interrupted = coordinator.interrupt_unconfirmed(
                set(), expected=exhausted
            )
            for record in interrupted:
                await render_operation(application, router, record)
            result["interrupted"] = [
                *result["interrupted"], *interrupted
            ]
            result["deferred"] = [
                operation_id for operation_id in result["deferred"]
                if operation_id not in exhausted
            ]
            if not result["deferred"]:
                return result
        await asyncio.sleep(max(0.01, float(retry_interval)))


def _snapshot_report(snapshot: dict, operation_id: str):
    if not isinstance(snapshot, dict):
        return None
    if isinstance(snapshot.get("operation"), dict):
        candidate = snapshot["operation"]
        return candidate if candidate.get("operation_id") == operation_id else None
    if isinstance(snapshot.get("operations"), list):
        for candidate in snapshot["operations"]:
            if isinstance(candidate, dict) and candidate.get("operation_id") == operation_id:
                return candidate
        return None
    return snapshot if snapshot.get("operation_id") == operation_id else None

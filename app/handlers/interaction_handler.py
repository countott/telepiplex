from __future__ import annotations

import asyncio
import inspect
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop

try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init
from app.core.interaction_coordinator import TERMINAL_STATES, OperationRecord


COORDINATOR_KEY = "telepiplex_interaction_coordinator"
ROUTER_KEY = "telepiplex_plugin_router"
CONTROL_CALLBACK_PREFIX = "core-operation:"
CONTROL_CALLBACK_PATTERN = r"^core-operation:"
_CONTROL_RE = re.compile(
    r"^core-operation:(?P<action>exit|cancel|rollback):"
    r"(?P<operation_id>[A-Za-z0-9_-]{1,40})$"
)
_CONTROL_LABELS = {
    "exit": "退出",
    "cancel": "取消任务",
    "rollback": "取消并回滚",
}
_CONTROL_IN_PROGRESS_STATES = {"cancelling", "rolling_back"}


def _log(level: str, message: str):
    logger = getattr(init, "logger", None)
    if logger is None:
        return
    method = getattr(logger, level, None) or getattr(logger, "info", None)
    if method is not None:
        method(message)


class OperationReportSink:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._listener = None
        self._tasks: set[asyncio.Task] = set()

    def attach(self, listener):
        self._listener = listener

    def __call__(self, plugin_id: str, report: dict) -> dict:
        record = self.coordinator.report(plugin_id, report)
        if self._listener is not None:
            try:
                task = asyncio.create_task(self._notify(record))
            except RuntimeError:
                task = None
            if task is not None:
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        try:
            submitted_revision = int(report.get("revision"))
        except (TypeError, ValueError):
            submitted_revision = 0
        return {
            "accepted": submitted_revision == record.revision,
            "operation_id": record.operation_id,
            "state": record.state,
            "revision": record.revision,
        }

    async def _notify(self, record: OperationRecord):
        try:
            result = self._listener(record)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            _log(
                "error",
                "Feature 任务状态渲染失败："
                f"operation_id={record.operation_id}, error={type(exc).__name__}",
            )


async def operation_gate(update, context):
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

    query = getattr(update, "callback_query", None)
    if query is not None:
        data = str(getattr(query, "data", "") or "")
        control = _CONTROL_RE.fullmatch(data)
        if control is not None and control.group("operation_id") == record.operation_id:
            return
        if record.state == "awaiting_input":
            namespace, separator, _payload = data.partition(":")
            router = bot_data.get(ROUTER_KEY)
            route = (
                router.callback_route(namespace)
                if separator and router is not None
                else None
            )
            if route is not None and route.plugin_id == record.plugin_id:
                return
        await query.answer("当前任务执行中")
        raise ApplicationHandlerStop

    message = getattr(update, "effective_message", None)
    text = str(getattr(message, "text", "") or "")
    if record.state == "awaiting_input" and text and not text.lstrip().startswith("/"):
        return
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
    router = context.application.bot_data.get(ROUTER_KEY)
    route = router.plugin_route(record.plugin_id) if router is not None else None
    if route is None:
        await query.answer("任务执行器不可用")
        return

    await query.answer("处理中...")
    try:
        result = await route.client.request(
            "operation.control",
            {
                "operation_id": record.operation_id,
                "action": action,
                "revision": record.revision,
            },
            deadline=30,
            idempotency_key=f"operation-control:{record.operation_id}:{action}",
        )
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


def operation_markup(record: OperationRecord):
    if record.state in TERMINAL_STATES or not record.control:
        return None
    label = _CONTROL_LABELS.get(record.control)
    if label is None:
        return None
    callback_data = f"{CONTROL_CALLBACK_PREFIX}{record.control}:{record.operation_id}"
    if len(callback_data.encode("utf-8")) > 64:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=callback_data)
    ]])


async def render_operation(application, _router, record: OperationRecord):
    coordinator = application.bot_data.get(COORDINATOR_KEY)
    if coordinator is None:
        return None
    latest = coordinator.get(record.operation_id)
    if latest is None:
        return None
    record = latest
    text = record.status_text or (
        f"任务状态：{record.state}\n阶段：{record.stage or '-'}"
    )
    markup = operation_markup(record)
    if record.message_id is not None:
        try:
            await application.bot.edit_message_text(
                chat_id=record.chat_id,
                message_id=record.message_id,
                text=text,
                reply_markup=markup,
            )
            return record.message_id
        except Exception as exc:
            _log(
                "warn",
                "任务状态消息编辑失败，改发新消息："
                f"operation_id={record.operation_id}, error={type(exc).__name__}",
            )
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
            f"operation_id={record.operation_id}, error={type(exc).__name__}",
        )
        return None
    message_id = getattr(message, "message_id", None)
    if isinstance(message_id, int) and message_id > 0:
        coordinator.set_message_id(record.operation_id, message_id)
        return message_id
    return None


async def recover_active_operations(application, router, coordinator):
    confirmed: set[str] = set()
    rendered: list[OperationRecord] = []
    for record in coordinator.active_records():
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
        except Exception as exc:
            _log(
                "warn",
                "Feature 任务恢复确认失败："
                f"operation_id={record.operation_id}, error={type(exc).__name__}",
            )
    interrupted = coordinator.interrupt_unconfirmed(confirmed)
    for record in [*rendered, *interrupted]:
        await render_operation(application, router, record)
    return {"confirmed": sorted(confirmed), "interrupted": interrupted}


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

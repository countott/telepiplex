from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Mapping

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ApplicationHandlerStop

try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init
from app.core.interaction_coordinator import TERMINAL_STATES, OperationRecord


COORDINATOR_KEY = "telepiplex_interaction_coordinator"
ROUTER_KEY = "telepiplex_plugin_router"
OPERATION_RECOVERY_TASK_KEY = "telepiplex_operation_recovery_task"
CONFIG_OPERATION_TASKS_KEY = "telepiplex_config_operation_tasks"
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
        self._locks: dict[str, asyncio.Lock] = {}

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
        return {
            "accepted": accepted,
            "operation_id": record.operation_id,
            "state": record.state,
            "revision": record.revision,
        }

    async def _notify(self, record: OperationRecord):
        try:
            lock = self._locks.setdefault(record.operation_id, asyncio.Lock())
            async with lock:
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
    rows = _feature_status_rows(record, router)
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
    markup = operation_markup(record, _router)
    photo_url = _operation_photo_url(record.details)
    if photo_url:
        caption = text
        if len(caption) > 1024:
            caption = caption[:1003].rstrip() + "\n…内容已截断"
        if record.message_id is not None:
            try:
                await application.bot.edit_message_media(
                    chat_id=record.chat_id,
                    message_id=record.message_id,
                    media=InputMediaPhoto(media=photo_url, caption=caption),
                    reply_markup=markup,
                )
                return record.message_id
            except Exception as exc:
                _log(
                    "warn",
                    "任务候选海报编辑失败，改发新消息："
                    f"operation_id={record.operation_id}, "
                    f"error={type(exc).__name__}",
                )
        try:
            message = await application.bot.send_photo(
                chat_id=record.chat_id,
                photo=photo_url,
                caption=caption,
                reply_markup=markup,
            )
        except Exception as exc:
            _log(
                "warn",
                "任务候选海报发送失败，降级为文本："
                f"operation_id={record.operation_id}, "
                f"error={type(exc).__name__}",
            )
        else:
            message_id = getattr(message, "message_id", None)
            if isinstance(message_id, int) and message_id > 0:
                coordinator.set_message_id(record.operation_id, message_id)
                return message_id
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

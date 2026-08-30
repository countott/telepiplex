#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import time
import tracemalloc
from unittest.mock import patch

from telegram.request import BaseRequest


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
for source in (ROOT, SDK_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


def percentile_summary(values) -> dict:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "n": 0,
            "min": None,
            "mean": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "max": None,
        }

    def nearest_rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    def rounded(value: float) -> float:
        return round(float(value), 3)

    return {
        "n": len(ordered),
        "min": rounded(ordered[0]),
        "mean": rounded(statistics.fmean(ordered)),
        "p50": rounded(nearest_rank(0.50)),
        "p95": rounded(nearest_rank(0.95)),
        "p99": rounded(nearest_rank(0.99)),
        "max": rounded(ordered[-1]),
    }


def _milliseconds(start_ns: int | None, end_ns: int | None) -> float | None:
    if not start_ns or not end_ns or end_ns < start_ns:
        return None
    return (end_ns - start_ns) / 1_000_000


def _fd_count() -> int | None:
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return None


def _task_descriptions(tasks) -> list[dict]:
    descriptions = []
    for task in sorted(tasks, key=lambda item: (item.get_name(), id(item))):
        coroutine = task.get_coro()
        descriptions.append({
            "name": task.get_name(),
            "coroutine": str(
                getattr(
                    coroutine,
                    "__qualname__",
                    getattr(coroutine, "__name__", type(coroutine).__name__),
                )
            ),
        })
    return descriptions


class SimulatedTelegramRequest(BaseRequest):
    """BaseRequest-compatible Telegram transport with measured latency."""

    def __init__(
        self,
        *,
        telegram_latency_ms: float,
        busy_latency_ms: float,
        timelines: dict[str, dict],
        chat_operations: dict[int, str],
        callback_operations: dict[str, str],
        cancelled_busy_late_apply_ms: float | None = None,
    ):
        self.telegram_latency_ms = max(0.0, float(telegram_latency_ms))
        self.busy_latency_ms = max(0.0, float(busy_latency_ms))
        self.timelines = timelines
        self.chat_operations = chat_operations
        self.callback_operations = callback_operations
        self.cancelled_busy_late_apply_ms = (
            None
            if cancelled_busy_late_apply_ms is None
            else max(0.0, float(cancelled_busy_late_apply_ms))
        )
        self.calls: list[dict] = []
        self.messages: dict[tuple[int, int], dict] = {}
        self.identity_milestone_deliveries: Counter[str] = Counter()
        self._terminal_visible = defaultdict(asyncio.Event)
        self._late_delivery_tasks: set[asyncio.Task] = set()
        self._message_id = 10_000

    @property
    def read_timeout(self):
        return None

    async def initialize(self):
        return None

    async def shutdown(self):
        await self.cancel_late_deliveries()

    async def drain_late_deliveries(self, timeout: float) -> bool:
        tasks = list(self._late_delivery_tasks)
        if not tasks:
            return True
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*tasks)
        except TimeoutError:
            return False
        return all(task.done() and not task.cancelled() for task in tasks)

    async def cancel_late_deliveries(self) -> None:
        tasks = list(self._late_delivery_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._late_delivery_tasks.clear()

    async def do_request(
        self,
        url,
        method,
        request_data=None,
        **_kwargs,
    ):
        endpoint = str(url).rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data is not None else {}
        started_ns = time.monotonic_ns()
        text = str(params.get("text") or params.get("caption") or "")
        is_busy_edit = endpoint in {"editMessageText", "editMessageCaption"} and text in {
            "正在确认媒体身份…",
            "正在处理所选片源…",
        }
        intended_wait_ms = (
            self.busy_latency_ms if is_busy_edit else self.telegram_latency_ms
        )
        callback_id = str(params.get("callback_query_id") or "")
        chat_id = params.get("chat_id")
        try:
            numeric_chat_id = int(chat_id) if chat_id is not None else None
        except (TypeError, ValueError):
            numeric_chat_id = None
        operation_id = (
            self.callback_operations.get(callback_id)
            if callback_id
            else self.chat_operations.get(numeric_chat_id)
            if numeric_chat_id is not None
            else None
        )
        action = {
            "endpoint": endpoint,
            "operation_id": operation_id,
            "started_ns": started_ns,
            "intended_wait_ms": intended_wait_ms,
            "outcome": "started",
        }
        self.calls.append(action)
        try:
            if intended_wait_ms:
                await asyncio.sleep(intended_wait_ms / 1000)
        except asyncio.CancelledError:
            action["completed_ns"] = time.monotonic_ns()
            action["duration_ms"] = round(
                (action["completed_ns"] - started_ns) / 1_000_000,
                3,
            )
            action["outcome"] = "cancelled"
            if (
                is_busy_edit
                and operation_id
                and self.cancelled_busy_late_apply_ms is not None
            ):
                late_task = asyncio.create_task(
                    self._apply_cancelled_busy_after_terminal(
                        endpoint,
                        params,
                        operation_id,
                        action,
                    ),
                    name=f"telegram-late-busy-{operation_id}",
                )
                self._late_delivery_tasks.add(late_task)
                action["late_apply_scheduled"] = True
            raise

        completed_ns = time.monotonic_ns()
        action["completed_ns"] = completed_ns
        action["duration_ms"] = round(
            (completed_ns - started_ns) / 1_000_000,
            3,
        )
        action["outcome"] = "delivered"
        result = self._response_result(endpoint, params)
        message_id = self._record_visible_delivery(
            endpoint,
            params,
            result,
            operation_id=operation_id,
            completed_ns=completed_ns,
        )
        action["message_id"] = message_id
        action["text"] = text
        self._record_operation_timeline(
            endpoint,
            text,
            operation_id,
            completed_ns,
        )

        return 200, json.dumps(
            {"ok": True, "result": result},
            ensure_ascii=False,
        ).encode("utf-8")

    async def _apply_cancelled_busy_after_terminal(
        self,
        endpoint: str,
        params: dict,
        operation_id: str,
        action: dict,
    ) -> None:
        await self._terminal_visible[operation_id].wait()
        delay_ms = float(self.cancelled_busy_late_apply_ms or 0)
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        completed_ns = time.monotonic_ns()
        result = self._response_result(endpoint, params)
        action["message_id"] = self._record_visible_delivery(
            endpoint,
            params,
            result,
            operation_id=operation_id,
            completed_ns=completed_ns,
        )
        action["late_applied_ns"] = completed_ns
        action["outcome"] = "cancelled_late_applied"
        self._record_operation_timeline(
            endpoint,
            str(params.get("text") or params.get("caption") or ""),
            operation_id,
            completed_ns,
        )

    def _record_operation_timeline(
        self,
        endpoint: str,
        text: str,
        operation_id: str | None,
        completed_ns: int,
    ) -> None:
        if not operation_id:
            return
        timeline = self.timelines[operation_id]
        if endpoint == "answerCallbackQuery" and text == "处理中...":
            current = timeline.get("callback_ack_ns")
            timeline["callback_ack_ns"] = (
                completed_ns if current is None else min(current, completed_ns)
            )
        if endpoint in {
            "sendMessage",
            "sendPhoto",
            "editMessageText",
            "editMessageCaption",
            "editMessageReplyMarkup",
        }:
            timeline.setdefault("candidate_visible_ns", completed_ns)
            timeline["last_projection_ns"] = max(
                int(timeline.get("last_projection_ns") or 0),
                completed_ns,
            )
            if text == "整理完成。":
                timeline["terminal_projection_ns"] = completed_ns
                self._terminal_visible[operation_id].set()
            if text.startswith("🎬 压测媒体身份"):
                timeline.setdefault("identity_milestone_ns", completed_ns)
                timeline["identity_milestone_last_ns"] = completed_ns
                self.identity_milestone_deliveries[operation_id] += 1

    def _response_result(self, endpoint: str, params: dict):
        if endpoint == "getMe":
            return {
                "id": 9001,
                "is_bot": True,
                "first_name": "telepiplex pressure",
                "username": "telepiplex_pressure_bot",
            }
        if endpoint in {"answerCallbackQuery", "deleteMessage", "setMyCommands"}:
            return True
        if endpoint in {"sendMessage", "sendPhoto"}:
            self._message_id += 1
            message_id = self._message_id
        else:
            try:
                message_id = int(params.get("message_id") or 0)
            except (TypeError, ValueError):
                message_id = 0
            if message_id <= 0:
                self._message_id += 1
                message_id = self._message_id
        try:
            chat_id = int(params.get("chat_id") or 0)
        except (TypeError, ValueError):
            chat_id = 0
        message = {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
        }
        if params.get("text") is not None:
            message["text"] = str(params["text"])
        if params.get("caption") is not None:
            message["caption"] = str(params["caption"])
        return message

    @staticmethod
    def _integer(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _record_visible_delivery(
        self,
        endpoint: str,
        params: dict,
        result,
        *,
        operation_id: str | None,
        completed_ns: int,
    ) -> int:
        if endpoint in {"sendMessage", "sendPhoto"} and isinstance(result, dict):
            chat = result.get("chat") or {}
            chat_id = self._integer(chat.get("id"))
            message_id = self._integer(result.get("message_id"))
        else:
            chat_id = self._integer(params.get("chat_id"))
            message_id = self._integer(params.get("message_id"))

        if chat_id <= 0 or message_id <= 0:
            return message_id

        key = (chat_id, message_id)
        if endpoint in {"sendMessage", "sendPhoto"}:
            self.messages[key] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "operation_id": operation_id,
                "text": str(params.get("text") or params.get("caption") or ""),
                "reply_markup": self._visible_reply_markup(
                    params.get("reply_markup")
                ),
                "deleted": False,
                "updated_ns": completed_ns,
            }
            return message_id

        state = self.messages.setdefault(key, {
            "chat_id": chat_id,
            "message_id": message_id,
            "operation_id": operation_id,
            "text": "",
            "reply_markup": None,
            "deleted": False,
            "updated_ns": completed_ns,
        })
        if operation_id and not state.get("operation_id"):
            state["operation_id"] = operation_id
        if endpoint in {"editMessageText", "editMessageCaption"}:
            state["text"] = str(
                params.get("text") or params.get("caption") or ""
            )
            if "reply_markup" in params:
                state["reply_markup"] = self._visible_reply_markup(
                    params.get("reply_markup")
                )
        elif endpoint == "editMessageReplyMarkup":
            # PTB omits a None value from RequestData. For this endpoint, an
            # omitted reply_markup is Telegram's explicit keyboard removal.
            state["reply_markup"] = params.get("reply_markup")
        elif endpoint == "deleteMessage":
            state["deleted"] = True
        state["updated_ns"] = completed_ns
        return message_id

    @staticmethod
    def _visible_reply_markup(value):
        parsed = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        if (
            isinstance(parsed, dict)
            and (
                parsed == {}
                or parsed.get("inline_keyboard") in ([], ())
            )
        ):
            return None
        return value

    def message_snapshot(self, chat_id: int, message_id: int) -> dict | None:
        state = self.messages.get((int(chat_id), int(message_id)))
        return dict(state) if state is not None else None


def _command_update_payload(
    *,
    update_id: int,
    message_id: int,
    chat_id: int,
    user_id: int,
    query: str,
) -> dict:
    text = f"/search {query}"
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Pressure",
            },
            "text": text,
            "entities": [{
                "type": "bot_command",
                "offset": 0,
                "length": len("/search"),
            }],
        },
    }


def _callback_update_payload(
    *,
    update_id: int,
    callback_id: str,
    message_id: int,
    chat_id: int,
    user_id: int,
    data: str,
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_id,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": "Pressure",
            },
            "chat_instance": f"pressure-chat-{chat_id}",
            "message": {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"},
            },
            "data": data,
        },
    }


async def _run(
    *,
    pipelines: int,
    concurrency: int,
    telegram_latency_ms: float = 25,
    busy_latency_ms: float | None = None,
    search_latency_ms: float = 50,
    download_latency_ms: float = 100,
    rename_latency_ms: float = 50,
    duplicate_clicks: int = 1,
    timeout_seconds: float = 30,
    frontend_mode: str = "direct",
    cancelled_busy_late_apply_ms: float | None = None,
) -> dict:
    from telegram import Update
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        MessageHandler,
        TypeHandler,
        filters,
    )
    from app.handlers.interaction_handler import (
        COORDINATOR_KEY,
        OPERATION_RENDER_LOCKS_KEY,
        deliver_operation_milestone,
        drain_callback_feedback,
        operation_gate,
        operation_markup,
        operation_render_lock,
        render_operation,
    )
    from app.handlers.plugin_handler import (
        dynamic_callback_gateway,
        dynamic_command_gateway,
    )
    from telepiplex_plugin_sdk.host_client import HostClient
    from tests.test_operation_pipeline_e2e import OperationPipelineEndToEndTest

    if pipelines <= 0:
        raise ValueError("pipelines must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if duplicate_clicks <= 0:
        raise ValueError("duplicate_clicks must be positive")
    frontend_mode = str(frontend_mode or "").strip().casefold()
    if frontend_mode not in {"direct", "queue"}:
        raise ValueError("frontend_mode must be 'direct' or 'queue'")
    for name, value in {
        "telegram_latency_ms": telegram_latency_ms,
        "search_latency_ms": search_latency_ms,
        "download_latency_ms": download_latency_ms,
        "rename_latency_ms": rename_latency_ms,
        "timeout_seconds": timeout_seconds,
    }.items():
        if float(value) < 0:
            raise ValueError(f"{name} must not be negative")
    if busy_latency_ms is None:
        busy_latency_ms = telegram_latency_ms
    if float(busy_latency_ms) < 0:
        raise ValueError("busy_latency_ms must not be negative")
    if (
        cancelled_busy_late_apply_ms is not None
        and float(cancelled_busy_late_apply_ms) < 0
    ):
        raise ValueError("cancelled_busy_late_apply_ms must not be negative")

    run_started_ns = time.monotonic_ns()
    timelines = {
        f"telegram-pressure-{index:04d}": {}
        for index in range(pipelines)
    }
    chat_operations = {
        10_000 + index: f"telegram-pressure-{index:04d}"
        for index in range(pipelines)
    }
    operation_chats = {
        operation_id: chat_id
        for chat_id, operation_id in chat_operations.items()
    }
    callback_operations: dict[str, str] = {}
    transport = SimulatedTelegramRequest(
        telegram_latency_ms=telegram_latency_ms,
        busy_latency_ms=busy_latency_ms,
        timelines=timelines,
        chat_operations=chat_operations,
        callback_operations=callback_operations,
        cancelled_busy_late_apply_ms=cancelled_busy_late_apply_ms,
    )
    lifecycle_baseline_tasks = set(asyncio.all_tasks())
    lifecycle_task_baseline = len(lifecycle_baseline_tasks)
    lifecycle_task_final = 0
    lifecycle_unexpected_final_tasks: list[dict] = []
    lifecycle_fd_baseline = _fd_count()
    lifecycle_fd_final = None
    harness = OperationPipelineEndToEndTest(
        methodName="test_full_pipeline_ends_at_rename_without_sync_or_plex"
    )
    await harness.asyncSetUp()
    application = None
    resource_stop = asyncio.Event()
    resource_task = None
    started_tracemalloc = not tracemalloc.is_tracing()
    if started_tracemalloc:
        tracemalloc.start()
    memory_baseline = 0
    memory_current = 0
    memory_peak = 0
    task_baseline = 0
    task_final = 0
    baseline_tasks = set()
    unexpected_final_tasks: list[dict] = []
    fd_baseline = None
    fd_final = None
    resource_peaks = {
        "tasks": 0,
        "fds": None,
        "render_locks": 0,
    }
    active_pipelines = 0
    active_pipeline_peak = 0
    callback_dispatches = 0
    download_effects = 0
    rename_effects = 0
    event_deliveries = 0
    sealed_segments = 0
    reported_segment_seals = {
        operation_id: {}
        for operation_id in timelines
    }
    published_milestones = 0
    delivered_event_types: Counter[str] = Counter()
    handler_errors: list[dict] = []
    pipeline_failures: list[dict] = []
    operation_drain_completed = False
    milestone_drain_completed = False
    callback_feedback_drain_completed = False
    late_telegram_drain_completed = False
    durably_sealed_segments = 0
    operations_without_active_segment = 0
    final_segment_visible = 0
    final_terminal_visible = 0
    durable_seal_failures: list[dict] = []
    final_segment_failures: list[dict] = []
    final_terminal_failures: list[dict] = []
    update_processor_concurrency = 0

    async def sample_resources():
        while not resource_stop.is_set():
            resource_peaks["tasks"] = max(
                int(resource_peaks["tasks"] or 0),
                len(asyncio.all_tasks()),
            )
            current_fds = _fd_count()
            if current_fds is not None:
                resource_peaks["fds"] = max(
                    int(resource_peaks["fds"] or 0),
                    current_fds,
                )
            locks = (
                application.bot_data.get(OPERATION_RENDER_LOCKS_KEY, {})
                if application is not None
                else {}
            )
            resource_peaks["render_locks"] = max(
                int(resource_peaks["render_locks"] or 0),
                len(locks),
            )
            try:
                await asyncio.wait_for(resource_stop.wait(), timeout=0.05)
            except TimeoutError:
                pass

    def timeline_for_chat(chat_id: int) -> tuple[str, dict]:
        operation_id = chat_operations[int(chat_id)]
        return operation_id, timelines[operation_id]

    async def sleep_ms(value: float):
        if value:
            await asyncio.sleep(float(value) / 1000)

    def accept_reported_seal(
        operation_id: str,
        role: str,
        response: dict,
    ) -> None:
        segment = response.get("segment") or {}
        segment_id = str(segment.get("segment_id") or "").strip()
        if (
            response.get("accepted") is not True
            or segment.get("state") != "sealed"
            or not segment_id
        ):
            raise AssertionError(f"{role} segment did not seal")
        reported_segment_seals[operation_id][role] = segment_id

    try:
        search_manifest = harness._manifest(
            "search",
            commands=("search",),
            callbacks=("search",),
            requires=("download.provider",),
        )
        download_manifest = harness._manifest(
            "download",
            publishes=("download.completed",),
            provides=("download.provider",),
        )
        rename_manifest = harness._manifest(
            "rename",
            subscribes=("download.completed",),
        )
        search_host = HostClient(harness.broker.socket_path, "search-telegram-token")
        download_host = HostClient(
            harness.broker.socket_path,
            "download-telegram-token",
        )
        rename_host = HostClient(harness.broker.socket_path, "rename-telegram-token")

        async def search_command(request: dict) -> dict:
            chat_id = int(request["chat_id"])
            user_id = int(request["user_id"])
            operation_id, timeline = timeline_for_chat(chat_id)
            timeline["command_rpc_started_ns"] = time.monotonic_ns()
            return {
                "actions": [],
                "operation": {
                    "operation_id": operation_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "state": "awaiting_input",
                    "stage": "release_selection",
                    "status_text": "请选择片源",
                    "control": "exit",
                    "revision": 1,
                    "details": {
                        "keyboard": [[{
                            "text": "① 压测片源",
                            "callback_data": f"search:select:{operation_id}",
                        }]],
                    },
                    "projection": {"text": "请选择片源"},
                    "segment": {
                        "role": "search",
                        "presentation_kind": "text",
                    },
                },
            }

        async def search_callback(request: dict) -> dict:
            nonlocal callback_dispatches, sealed_segments
            chat_id = int(request["chat_id"])
            user_id = int(request["user_id"])
            operation_id, timeline = timeline_for_chat(chat_id)
            callback_dispatches += 1
            timeline["feature_rpc_started_ns"] = time.monotonic_ns()
            await sleep_ms(search_latency_ms)
            await search_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "prowlarr_search",
                "status_text": "正在搜索片源…",
                "control": "cancel",
                "revision": 2,
                "details": {},
                "projection": {"text": "正在搜索片源…"},
                "segment": {
                    "role": "search",
                    "presentation_kind": "text",
                },
            })
            sealed = await search_host.seal_operation_segment(
                operation_id,
                "search",
            )
            accept_reported_seal(operation_id, "search", sealed)
            sealed_segments += 1
            handoff = {
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "handed_off",
                "stage": "handoff_download",
                "status_text": "已选定片源，提交下载",
                "control": "cancel",
                "revision": 3,
                "next_plugin_id": "download",
            }
            await search_host.report_operation(handoff)
            timeline["search_handoff_ns"] = time.monotonic_ns()
            await search_host.call_capability(
                "download.provider",
                "submit",
                {
                    "operation_id": operation_id,
                    "operation_revision": 3,
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
                idempotency_key=f"{operation_id}:download-submit",
            )
            return {"actions": []}

        async def download_capability(request: dict) -> dict:
            nonlocal download_effects, sealed_segments
            payload = request["payload"]
            operation_id = str(payload["operation_id"])
            timeline = timelines[operation_id]
            chat_id = int(payload["chat_id"])
            user_id = int(payload["user_id"])
            download_effects += 1
            timeline["download_started_ns"] = time.monotonic_ns()
            await download_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "downloading",
                "status_text": "正在下载…",
                "control": "cancel",
                "revision": 4,
                "details": {},
                "projection": {"text": "正在下载…"},
                "segment": {
                    "role": "download",
                    "presentation_kind": "text",
                },
            })
            await sleep_ms(download_latency_ms)
            await download_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "downloaded",
                "status_text": "下载完成，开始整理",
                "control": "cancel",
                "revision": 5,
                "details": {},
                "projection": {"text": "下载完成，开始整理"},
                "segment": {
                    "role": "download",
                    "presentation_kind": "text",
                },
            })
            sealed = await download_host.seal_operation_segment(
                operation_id,
                "download",
            )
            accept_reported_seal(operation_id, "download", sealed)
            sealed_segments += 1
            await download_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "handed_off",
                "stage": "handoff_rename",
                "status_text": "下载完成，开始整理",
                "control": "cancel",
                "revision": 6,
                "next_plugin_id": "rename",
            })
            timeline["download_handoff_ns"] = time.monotonic_ns()
            timeline["event_publish_started_ns"] = time.monotonic_ns()
            response = await download_host.publish_event(
                "download.completed",
                {
                    "operation_id": operation_id,
                    "operation_revision": 6,
                    "chat_id": chat_id,
                    "user_id": user_id,
                },
                idempotency_key=f"{operation_id}:download-completed",
            )
            timeline["event_published_ns"] = time.monotonic_ns()
            return {
                "accepted": bool(response.get("event_id")),
            }

        async def rename_event(request: dict) -> dict:
            nonlocal event_deliveries, published_milestones
            nonlocal rename_effects, sealed_segments
            event_deliveries += 1
            rename_effects += 1
            delivered_event_types[str(request.get("event_type") or "")] += 1
            payload = request["payload"]
            operation_id = str(payload["operation_id"])
            timeline = timelines[operation_id]
            chat_id = int(payload["chat_id"])
            user_id = int(payload["user_id"])
            timeline["rename_started_ns"] = time.monotonic_ns()
            await rename_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "identity_confirmation",
                "status_text": "正在确认媒体身份。",
                "control": "cancel",
                "revision": 7,
                "details": {"telegram_visibility": "silent"},
            })
            milestone = await rename_host.publish_operation_milestone(
                operation_id,
                f"{operation_id}:identity",
                "🎬 压测媒体身份",
                mode="identity",
            )
            if milestone.get("accepted") is not True:
                raise AssertionError("rename identity milestone was not queued")
            published_milestones += 1
            await rename_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "organizing",
                "status_text": "正在整理…",
                "control": "rollback",
                "revision": 8,
                "details": {},
                "projection": {"text": "正在整理…"},
                "segment": {
                    "role": "rename",
                    "presentation_kind": "text",
                },
            })
            await sleep_ms(rename_latency_ms)
            await rename_host.report_operation({
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "completed",
                "stage": "completed",
                "status_text": "整理完成。",
                "control": "",
                "revision": 9,
                "details": {
                    "organized": True,
                    "cleanup_complete": True,
                    "partial_completed": False,
                },
                "projection": {"text": "整理完成。"},
                "segment": {
                    "role": "rename",
                    "presentation_kind": "text",
                },
            })
            timeline["terminal_internal_ns"] = time.monotonic_ns()
            terminal_segment = harness.coordinator.get_active_segment(operation_id)
            timeline["terminal_segment_id"] = (
                terminal_segment.segment_id
                if terminal_segment is not None
                else ""
            )
            sealed = await rename_host.seal_operation_segment(
                operation_id,
                "rename",
            )
            accept_reported_seal(operation_id, "rename", sealed)
            sealed_segments += 1
            timeline["rename_sealed_ns"] = time.monotonic_ns()
            return {"accepted": True}

        await harness._start_runtime(
            download_manifest,
            "download-telegram-token",
            capabilities={"download.provider": download_capability},
        )
        await harness._start_runtime(
            rename_manifest,
            "rename-telegram-token",
            events={"download.completed": rename_event},
        )
        await harness._start_runtime(
            search_manifest,
            "search-telegram-token",
            commands={"search": search_command},
            callbacks={"search": search_callback},
        )

        application = (
            Application.builder()
            .token("123456:telegram-pressure-token")
            .request(transport)
            .build()
        )
        application.bot_data[COORDINATOR_KEY] = harness.coordinator
        application.bot_data["telepiplex_plugin_router"] = harness.router
        harness.operation_sink.attach(
            lambda record: render_operation(application, harness.router, record)
        )
        harness.milestone_sink.attach(
            lambda record, mode, photo_url, text: deliver_operation_milestone(
                application,
                record,
                mode,
                photo_url,
                text,
            ),
            lambda operation_id: operation_render_lock(
                application,
                operation_id,
            ),
        )
        application.add_handler(TypeHandler(Update, operation_gate), group=-100)
        application.add_handler(CallbackQueryHandler(dynamic_callback_gateway))
        application.add_handler(
            MessageHandler(filters.COMMAND, dynamic_command_gateway)
        )

        async def capture_handler_error(update, context):
            chat = getattr(update, "effective_chat", None)
            operation_id = (
                chat_operations.get(int(chat.id)) if chat is not None else None
            )
            error = getattr(context, "error", None)
            handler_errors.append({
                "operation_id": operation_id,
                "error_type": type(error).__name__,
            })

        application.add_error_handler(capture_handler_error)
        await application.initialize()
        update_processor_concurrency = int(
            application.update_processor.max_concurrent_updates
        )
        if frontend_mode == "queue":
            await application.start()
        memory_baseline, _memory_peak = tracemalloc.get_traced_memory()
        baseline_tasks = set(asyncio.all_tasks())
        task_baseline = len(baseline_tasks)
        fd_baseline = _fd_count()
        resource_peaks.update({
            "tasks": task_baseline,
            "fds": fd_baseline,
        })
        run_started_ns = time.monotonic_ns()
        resource_task = asyncio.create_task(
            sample_resources(),
            name="telepiplex-telegram-pressure-resources",
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def pipeline(index: int):
            nonlocal active_pipelines, active_pipeline_peak
            operation_id = f"telegram-pressure-{index:04d}"
            chat_id = 10_000 + index
            user_id = 20_000 + index
            timeline = timelines[operation_id]
            async with semaphore:
                active_pipelines += 1
                active_pipeline_peak = max(active_pipeline_peak, active_pipelines)
                try:
                    timeline["command_update_started_ns"] = time.monotonic_ns()
                    command_update = Update.de_json(
                        _command_update_payload(
                            update_id=100_000 + index,
                            message_id=1_000 + index,
                            chat_id=chat_id,
                            user_id=user_id,
                            query=f"Pressure Movie {index}",
                        ),
                        application.bot,
                    )
                    if frontend_mode == "direct":
                        await application.process_update(command_update)
                    else:
                        await application.update_queue.put(command_update)
                        async with asyncio.timeout(timeout_seconds):
                            while True:
                                queued_record = harness.coordinator.get(operation_id)
                                queued_segment = (
                                    harness.coordinator.get_active_segment(operation_id)
                                )
                                if (
                                    queued_record is not None
                                    and queued_segment is not None
                                    and queued_segment.message_id is not None
                                ):
                                    break
                                await asyncio.sleep(0.001)
                    timeline["command_update_completed_ns"] = time.monotonic_ns()
                    record = harness.coordinator.get(operation_id)
                    segment = harness.coordinator.get_active_segment(operation_id)
                    if record is None or segment is None or segment.message_id is None:
                        raise AssertionError("command produced no active Telegram segment")
                    markup = operation_markup(
                        record,
                        harness.router,
                        segment=segment,
                    )
                    if markup is None or not markup.inline_keyboard:
                        raise AssertionError("command produced no callback keyboard")
                    callback_data = markup.inline_keyboard[0][0].callback_data
                    timeline["callback_update_started_ns"] = time.monotonic_ns()
                    callback_updates = []
                    for click_index in range(duplicate_clicks):
                        callback_id = f"pressure-{index}-{click_index}"
                        callback_operations[callback_id] = operation_id
                        callback_updates.append(Update.de_json(
                            _callback_update_payload(
                                update_id=(
                                    200_000
                                    + index * duplicate_clicks
                                    + click_index
                                ),
                                callback_id=callback_id,
                                message_id=segment.message_id,
                                chat_id=chat_id,
                                user_id=user_id,
                                data=callback_data,
                            ),
                            application.bot,
                        ))
                    if frontend_mode == "direct":
                        callback_results = await asyncio.gather(
                            *(
                                application.process_update(item)
                                for item in callback_updates
                            ),
                            return_exceptions=True,
                        )
                        callback_errors = [
                            item for item in callback_results
                            if isinstance(item, Exception)
                        ]
                        if callback_errors:
                            raise callback_errors[0]
                    else:
                        for callback_update in callback_updates:
                            await application.update_queue.put(callback_update)
                    if frontend_mode == "direct":
                        timeline["callback_update_completed_ns"] = (
                            time.monotonic_ns()
                        )
                    async with asyncio.timeout(timeout_seconds):
                        while True:
                            current = harness.coordinator.get(operation_id)
                            if current is not None and current.state == "completed":
                                timeline["terminal_owner"] = current.plugin_id
                                timeline["terminal_state"] = current.state
                                if frontend_mode == "queue":
                                    timeline["callback_update_completed_ns"] = (
                                        time.monotonic_ns()
                                    )
                                break
                            await asyncio.sleep(0.005)
                finally:
                    active_pipelines -= 1

        with patch(
            "app.handlers.plugin_handler.init.check_user",
            return_value=True,
        ):
            pipeline_results = await asyncio.gather(
                *(pipeline(index) for index in range(pipelines)),
                return_exceptions=True,
            )
        for index, outcome in enumerate(pipeline_results):
            if isinstance(outcome, Exception):
                pipeline_failures.append({
                    "operation_id": f"telegram-pressure-{index:04d}",
                    "error_type": type(outcome).__name__,
                })
        try:
            async with asyncio.timeout(timeout_seconds):
                while any(
                    not timeline.get("rename_sealed_ns")
                    for timeline in timelines.values()
                ):
                    await asyncio.sleep(0.001)
        except TimeoutError:
            for operation_id, timeline in timelines.items():
                if not timeline.get("rename_sealed_ns"):
                    pipeline_failures.append({
                        "operation_id": operation_id,
                        "error_type": "RenameSealTimeout",
                    })
        if frontend_mode == "queue":
            async with asyncio.timeout(timeout_seconds):
                await application.update_queue.join()
        operation_drain_completed = await harness.operation_sink.drain(
            timeout=timeout_seconds
        )
        milestone_drain_completed = await harness.milestone_sink.drain(
            timeout=timeout_seconds
        )
        callback_feedback_drain_completed = await drain_callback_feedback(
            application,
            timeout=timeout_seconds,
        )
        late_telegram_drain_completed = await transport.drain_late_deliveries(
            timeout_seconds
        )
        expected_roles = ("search", "download", "rename")
        for operation_id, timeline in timelines.items():
            reported = reported_segment_seals[operation_id]
            reported_ids = [
                reported.get(role, "")
                for role in expected_roles
            ]
            duplicate_ids = {
                segment_id
                for segment_id, count in Counter(reported_ids).items()
                if segment_id and count > 1
            }
            for role in expected_roles:
                segment_id = reported.get(role, "")
                segment = (
                    harness.coordinator.get_segment(segment_id)
                    if segment_id
                    else None
                )
                reasons = []
                if not segment_id:
                    reasons.append("missing_reported_segment_id")
                if segment_id in duplicate_ids:
                    reasons.append("duplicate_reported_segment_id")
                if segment is None:
                    reasons.append("segment_not_found")
                else:
                    if segment.operation_id != operation_id:
                        reasons.append("wrong_operation")
                    if segment.owner_plugin_id != role or segment.role != role:
                        reasons.append("wrong_owner_or_role")
                    if segment.state != "sealed" or segment.sealed_at is None:
                        reasons.append("not_durably_sealed")
                    if segment.rendered_revision != segment.business_revision:
                        reasons.append("latest_revision_not_rendered")
                    if segment.message_id is None:
                        reasons.append("missing_message_id")
                if reasons:
                    durable_seal_failures.append({
                        "operation_id": operation_id,
                        "role": role,
                        "segment_id": segment_id,
                        "reasons": reasons,
                    })
                else:
                    durably_sealed_segments += 1

            if harness.coordinator.get_active_segment(operation_id) is None:
                operations_without_active_segment += 1
        await asyncio.sleep(0.05)
        resource_stop.set()
        if resource_task is not None:
            await asyncio.gather(resource_task, return_exceptions=True)
            resource_task = None
        await asyncio.sleep(0)
        # Snapshot the Telegram foreground only after the run has quiesced.
        # A delivery task that is still pending below fails the exact task
        # delta gate; one that completed during the grace period is reflected
        # in this final visible-state read.
        for operation_id, timeline in timelines.items():
            for role in expected_roles:
                segment_id = str(
                    reported_segment_seals[operation_id].get(role) or ""
                )
                segment = (
                    harness.coordinator.get_segment(segment_id)
                    if segment_id
                    else None
                )
                message = None
                if segment is not None and segment.message_id is not None:
                    message = transport.message_snapshot(
                        operation_chats[operation_id],
                        segment.message_id,
                    )
                expected_text = (
                    str(segment.projection.get("text") or "")
                    if segment is not None
                    else ""
                )
                segment_reasons = []
                if segment is None:
                    segment_reasons.append("segment_not_found")
                if message is None:
                    segment_reasons.append("message_not_found")
                else:
                    if message.get("deleted"):
                        segment_reasons.append("message_deleted")
                    if message.get("text") != expected_text:
                        segment_reasons.append("projection_text_mismatch")
                    if message.get("reply_markup") is not None:
                        segment_reasons.append("controls_still_visible")
                if segment_reasons:
                    final_segment_failures.append({
                        "operation_id": operation_id,
                        "role": role,
                        "segment_id": segment_id,
                        "reasons": segment_reasons,
                    })
                else:
                    final_segment_visible += 1

            terminal_segment_id = str(
                timeline.get("terminal_segment_id") or ""
            )
            terminal_segment = (
                harness.coordinator.get_segment(terminal_segment_id)
                if terminal_segment_id
                else None
            )
            terminal_message = None
            # Segments don't duplicate chat_id; the pressure scenario has a
            # deterministic operation-to-chat mapping.
            if (
                terminal_segment is not None
                and terminal_segment.message_id is not None
            ):
                terminal_message = transport.message_snapshot(
                    operation_chats[operation_id],
                    terminal_segment.message_id,
                )
            terminal_reasons = []
            if terminal_segment is None:
                terminal_reasons.append("terminal_segment_not_found")
            if terminal_message is None:
                terminal_reasons.append("terminal_message_not_found")
            else:
                if terminal_message.get("deleted"):
                    terminal_reasons.append("terminal_message_deleted")
                if terminal_message.get("text") != "整理完成。":
                    terminal_reasons.append("terminal_text_not_final")
                if terminal_message.get("reply_markup") is not None:
                    terminal_reasons.append("terminal_controls_still_visible")
            if terminal_reasons:
                final_terminal_failures.append({
                    "operation_id": operation_id,
                    "segment_id": terminal_segment_id,
                    "reasons": terminal_reasons,
                })
            else:
                final_terminal_visible += 1
        run_finished_ns = time.monotonic_ns()
        final_tasks = set(asyncio.all_tasks())
        task_final = len(final_tasks)
        unexpected_final_tasks = _task_descriptions(
            final_tasks - baseline_tasks
        )
        fd_final = _fd_count()
        render_locks_final = len(
            application.bot_data.get(OPERATION_RENDER_LOCKS_KEY, {})
        )
        memory_current, memory_peak = tracemalloc.get_traced_memory()
    finally:
        resource_stop.set()
        if resource_task is not None:
            await asyncio.gather(resource_task, return_exceptions=True)
        if application is not None:
            if application.running:
                await application.stop()
            await application.shutdown()
        await harness.asyncTearDown()
        await asyncio.sleep(0)
        lifecycle_final_tasks = set(asyncio.all_tasks())
        lifecycle_task_final = len(lifecycle_final_tasks)
        lifecycle_unexpected_final_tasks = _task_descriptions(
            lifecycle_final_tasks - lifecycle_baseline_tasks
        )
        lifecycle_fd_final = _fd_count()

    if started_tracemalloc:
        tracemalloc.stop()

    # asyncTearDown closes the coordinator. Durable segment and final Telegram
    # facts above were independently snapshotted after both sinks drained.
    completed_operations = sum(
        1 for timeline in timelines.values()
        if timeline.get("terminal_internal_ns")
        and timeline.get("terminal_owner") == "rename"
        and timeline.get("terminal_state") == "completed"
    )
    terminal_owners = sorted({
        str(timeline.get("terminal_owner") or "")
        for timeline in timelines.values()
        if timeline.get("terminal_owner")
    })
    terminal_projections = sum(
        1 for timeline in timelines.values()
        if timeline.get("terminal_projection_ns")
    )
    milestone_delivery_counts = {
        operation_id: int(
            transport.identity_milestone_deliveries.get(operation_id, 0)
        )
        for operation_id in timelines
    }
    delivered_milestones = sum(milestone_delivery_counts.values())
    exactly_once_milestones = sum(
        1 for count in milestone_delivery_counts.values()
        if count == 1
    )
    ordered_milestones = sum(
        1 for operation_id, timeline in timelines.items()
        if milestone_delivery_counts[operation_id] == 1
        and timeline.get("identity_milestone_ns")
        and timeline.get("terminal_projection_ns")
        and timeline["identity_milestone_ns"]
        <= timeline["terminal_projection_ns"]
    )
    total_callback_updates = pipelines * duplicate_clicks
    duplicate_callbacks_rejected = total_callback_updates - callback_dispatches
    failures = len(pipeline_failures) + len(handler_errors)
    task_final_delta = task_final - task_baseline
    fd_final_delta = (
        fd_final - fd_baseline
        if fd_final is not None and fd_baseline is not None
        else None
    )
    lifecycle_task_final_delta = (
        lifecycle_task_final - lifecycle_task_baseline
    )
    lifecycle_fd_final_delta = (
        lifecycle_fd_final - lifecycle_fd_baseline
        if (
            lifecycle_fd_final is not None
            and lifecycle_fd_baseline is not None
        )
        else None
    )
    correctness_passed = (
        completed_operations == pipelines
        and callback_dispatches == pipelines
        and duplicate_callbacks_rejected
        == pipelines * (duplicate_clicks - 1)
        and download_effects == pipelines
        and rename_effects == pipelines
        and event_deliveries == pipelines
        and delivered_event_types == Counter({"download.completed": pipelines})
        and terminal_owners == ["rename"]
        and sealed_segments == pipelines * 3
        and durably_sealed_segments == pipelines * 3
        and operations_without_active_segment == pipelines
        and published_milestones == pipelines
        and delivered_milestones == pipelines
        and exactly_once_milestones == pipelines
        and ordered_milestones == pipelines
        and terminal_projections == pipelines
        and final_segment_visible == pipelines * 3
        and final_terminal_visible == pipelines
        and operation_drain_completed
        and milestone_drain_completed
        and callback_feedback_drain_completed
        and late_telegram_drain_completed
        and task_final_delta == 0
        and not unexpected_final_tasks
        and render_locks_final == 0
        and (
            fd_final_delta is None
            or fd_final_delta == 0
        )
        and lifecycle_task_final_delta == 0
        and not lifecycle_unexpected_final_tasks
        and (
            lifecycle_fd_final_delta is None
            or lifecycle_fd_final_delta == 0
        )
        and failures == 0
    )

    samples = []
    metric_values: dict[str, list[float]] = {
        "command_to_candidate_ms": [],
        "callback_ack_ms": [],
        "callback_to_feature_rpc_ms": [],
        "search_stage_ms": [],
        "download_stage_ms": [],
        "event_queue_ms": [],
        "rename_stage_ms": [],
        "terminal_internal_ms": [],
        "foreground_complete_ms": [],
    }
    for operation_id, timeline in timelines.items():
        sample = {"operation_id": operation_id}
        pairs = {
            "command_to_candidate_ms": (
                timeline.get("command_update_started_ns"),
                timeline.get("candidate_visible_ns"),
            ),
            "callback_ack_ms": (
                timeline.get("callback_update_started_ns"),
                timeline.get("callback_ack_ns"),
            ),
            "callback_to_feature_rpc_ms": (
                timeline.get("callback_update_started_ns"),
                timeline.get("feature_rpc_started_ns"),
            ),
            "search_stage_ms": (
                timeline.get("feature_rpc_started_ns"),
                timeline.get("search_handoff_ns"),
            ),
            "download_stage_ms": (
                timeline.get("download_started_ns"),
                timeline.get("download_handoff_ns"),
            ),
            "event_queue_ms": (
                timeline.get("event_publish_started_ns"),
                timeline.get("rename_started_ns"),
            ),
            "rename_stage_ms": (
                timeline.get("rename_started_ns"),
                timeline.get("terminal_internal_ns"),
            ),
            "terminal_internal_ms": (
                timeline.get("feature_rpc_started_ns"),
                timeline.get("terminal_internal_ns"),
            ),
            "foreground_complete_ms": (
                timeline.get("command_update_started_ns"),
                (
                    timeline.get("last_projection_ns")
                    if timeline.get("terminal_projection_ns")
                    else None
                ),
            ),
        }
        for name, (started_ns, completed_ns) in pairs.items():
            value = _milliseconds(started_ns, completed_ns)
            sample[name] = round(value, 3) if value is not None else None
            if value is not None:
                metric_values[name].append(value)
        sample["completed"] = bool(timeline.get("terminal_internal_ns"))
        samples.append(sample)

    telegram_actions = Counter(call["endpoint"] for call in transport.calls)
    telegram_outcomes = Counter(call["outcome"] for call in transport.calls)
    telegram_durations = [
        float(call["duration_ms"])
        for call in transport.calls
        if call.get("outcome") == "delivered" and "duration_ms" in call
    ]
    elapsed_seconds = (run_finished_ns - run_started_ns) / 1_000_000_000
    task_api_calls = max(
        0,
        sum(telegram_actions.values()) - telegram_actions.get("getMe", 0),
    )
    ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "schema_version": "telepiplex.telegram_pressure.v1",
        "frontend": "python-telegram-bot.Application.process_update",
        "frontend_mode": frontend_mode,
        "frontend_semantics": (
            "direct handler concurrency; bypasses the default polling update_queue"
            if frontend_mode == "direct"
            else "Application.start default serial update_queue/BaseUpdateProcessor"
        ),
        "scenario": {
            "pipelines": pipelines,
            "concurrency": concurrency,
            "duplicate_clicks": duplicate_clicks,
            "telegram_latency_ms": float(telegram_latency_ms),
            "busy_latency_ms": float(busy_latency_ms),
            "search_latency_ms": float(search_latency_ms),
            "download_latency_ms": float(download_latency_ms),
            "rename_latency_ms": float(rename_latency_ms),
            "cancelled_busy_late_apply_ms": (
                float(cancelled_busy_late_apply_ms)
                if cancelled_busy_late_apply_ms is not None
                else None
            ),
            "update_processor_concurrency": update_processor_concurrency,
        },
        "correctness": {
            "completed_operations": completed_operations,
            "callback_dispatches": callback_dispatches,
            "duplicate_callbacks_rejected": duplicate_callbacks_rejected,
            "download_effects": download_effects,
            "rename_effects": rename_effects,
            "event_deliveries": event_deliveries,
            "sealed_segments": sealed_segments,
            "durably_sealed_segments": durably_sealed_segments,
            "operations_without_active_segment": (
                operations_without_active_segment
            ),
            "durable_seal_failures": durable_seal_failures,
            "published_milestones": published_milestones,
            "delivered_milestones": delivered_milestones,
            "exactly_once_milestones": exactly_once_milestones,
            "milestone_delivery_counts": milestone_delivery_counts,
            "ordered_milestones": ordered_milestones,
            "terminal_projections": terminal_projections,
            "final_segment_visible": final_segment_visible,
            "final_segment_failures": final_segment_failures,
            "final_terminal_visible": final_terminal_visible,
            "final_terminal_failures": final_terminal_failures,
            "operation_drain_completed": operation_drain_completed,
            "milestone_drain_completed": milestone_drain_completed,
            "callback_feedback_drain_completed": (
                callback_feedback_drain_completed
            ),
            "late_telegram_drain_completed": (
                late_telegram_drain_completed
            ),
            "event_types": sorted(
                event_type
                for event_type in delivered_event_types
                if event_type
            ),
            "terminal_owners": terminal_owners,
            "handler_errors": handler_errors,
            "pipeline_failures": pipeline_failures,
            "failures": failures,
            "passed": correctness_passed,
        },
        "latency_ms": {
            name: percentile_summary(values)
            for name, values in metric_values.items()
        },
        "telegram": {
            "actions": dict(sorted(telegram_actions.items())),
            "outcomes": dict(sorted(telegram_outcomes.items())),
            "api_duration_ms": percentile_summary(telegram_durations),
            "api_calls_per_pipeline": round(task_api_calls / pipelines, 2),
        },
        "throughput": {
            "elapsed_seconds": round(elapsed_seconds, 3),
            "pipelines_per_second": round(pipelines / elapsed_seconds, 2),
            "active_pipeline_peak": active_pipeline_peak,
        },
        "resources": {
            "measurement_phase": (
                "initialized runtimes before updates -> drained runtimes "
                "before teardown"
            ),
            "lifecycle": {
                "measurement_phase": "before setup -> after teardown",
                "tasks": {
                    "baseline": lifecycle_task_baseline,
                    "final": lifecycle_task_final,
                    "final_delta": lifecycle_task_final_delta,
                    "unexpected_final": lifecycle_unexpected_final_tasks,
                },
                "fds": {
                    "baseline": lifecycle_fd_baseline,
                    "final": lifecycle_fd_final,
                    "final_delta": lifecycle_fd_final_delta,
                },
            },
            "tasks": {
                "baseline": task_baseline,
                "peak": resource_peaks["tasks"],
                "final": task_final,
                "final_delta": task_final_delta,
                "unexpected_final": unexpected_final_tasks,
            },
            "render_locks": {
                "baseline": 0,
                "peak": resource_peaks["render_locks"],
                "final": render_locks_final,
            },
            "fds": {
                "baseline": fd_baseline,
                "peak": resource_peaks["fds"],
                "final": fd_final,
                "final_delta": fd_final_delta,
            },
            "memory": {
                "tracemalloc_baseline_bytes": memory_baseline,
                "tracemalloc_current_bytes": memory_current,
                "tracemalloc_current_delta_bytes": (
                    memory_current - memory_baseline
                ),
                "tracemalloc_peak_bytes": memory_peak,
                "ru_maxrss": ru_maxrss,
                "ru_maxrss_unit": "bytes" if sys.platform == "darwin" else "KiB",
            },
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "samples": samples,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pressure-test Telegram updates through real PTB handlers, Unix RPC, "
            "Host state, download handoff, event delivery, and rename terminal."
        )
    )
    parser.add_argument("--pipelines", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--telegram-latency-ms", type=float, default=25)
    parser.add_argument("--busy-latency-ms", type=float)
    parser.add_argument("--search-latency-ms", type=float, default=50)
    parser.add_argument("--download-latency-ms", type=float, default=100)
    parser.add_argument("--rename-latency-ms", type=float, default=50)
    parser.add_argument("--duplicate-clicks", type=int, default=1)
    parser.add_argument(
        "--cancelled-busy-late-apply-ms",
        type=float,
        help=(
            "fault injection: after a cancelled busy edit, apply it after "
            "the terminal projection plus this delay"
        ),
    )
    parser.add_argument(
        "--frontend-mode",
        choices=("direct", "queue"),
        default="direct",
        help=(
            "direct runs concurrent process_update calls; queue uses "
            "Application.start with PTB's default serial update processor"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = asyncio.run(_run(
        pipelines=args.pipelines,
        concurrency=args.concurrency,
        telegram_latency_ms=args.telegram_latency_ms,
        busy_latency_ms=args.busy_latency_ms,
        search_latency_ms=args.search_latency_ms,
        download_latency_ms=args.download_latency_ms,
        rename_latency_ms=args.rename_latency_ms,
        duplicate_clicks=args.duplicate_clicks,
        timeout_seconds=args.timeout_seconds,
        frontend_mode=args.frontend_mode,
        cancelled_busy_late_apply_ms=args.cancelled_busy_late_apply_ms,
    ))
    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if result.get("correctness", {}).get("passed") is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

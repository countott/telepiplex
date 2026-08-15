from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import threading
import time

from .diagnostics import (
    bounded_diagnostic_event,
    bounded_diagnostic_value,
    build_diagnostic_event,
    infer_legacy_diagnostics,
    render_machine_event,
)
from .log_sanitizer import sanitize_log_text, sanitize_log_value


FEATURE_DIAGNOSTIC_TRANSPORT_PREFIX = "@tpx-event-v1 "
FEATURE_DIAGNOSTIC_MAX_LINE_BYTES = 32 * 1024
FEATURE_DIAGNOSTIC_QUEUE_SIZE = 512
_HANDLER_MARKER = "_telepiplex_sdk_handler"


def _async_task_name() -> str | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return task.get_name() if task is not None else None


class _FeatureDiagnosticTransportHandler(logging.Handler):
    def __init__(self, context, stream=None):
        super().__init__()
        self.context = context
        self.stream = stream or sys.stdout
        self._sequence = 0
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=FEATURE_DIAGNOSTIC_QUEUE_SIZE)
        self._drained = threading.Event()
        self._drained.set()
        self._closed = False
        self._writer = threading.Thread(
            target=self._write_loop,
            name="telepiplex-diagnostics-stdout",
            daemon=True,
        )
        self._writer.start()
        setattr(self, _HANDLER_MARKER, True)

    def _write_loop(self):
        while True:
            try:
                line = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._closed:
                    return
                continue
            try:
                if line is None:
                    return
                self.stream.write(line)
                self.stream.flush()
            except Exception as exc:
                self._transport_failure(exc)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._drained.set()

    @staticmethod
    def _transport_failure(exc):
        try:
            sys.__stderr__.write(
                "telepiplex Feature diagnostics transport failed: "
                f"{type(exc).__name__}\n"
            )
            sys.__stderr__.flush()
        except Exception:
            pass

    def emit(self, record: logging.LogRecord):
        try:
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
            plugin_id = str(self.context.manifest.get("plugin_id") or "unknown")
            message = record.getMessage()
            explicit_event_name = getattr(record, "event_name", None)
            explicit_fields = getattr(record, "diagnostic_fields", None)
            if explicit_event_name is None and explicit_fields is None:
                event_name, diagnostic_fields = infer_legacy_diagnostics(message)
            else:
                event_name = str(explicit_event_name or "log.message")
                diagnostic_fields = explicit_fields
            event = build_diagnostic_event(
                level=record.levelname,
                event_name=event_name,
                message=message,
                session_id=str(os.environ.get("TPX_LOG_SESSION_ID") or "standalone"),
                logger_name=record.name,
                component=str(getattr(record, "diagnostic_component", plugin_id)),
                fields=diagnostic_fields,
                runtime={
                    "host_version": os.environ.get("TPX_HOST_VERSION") or None,
                    "plugin_id": plugin_id,
                    "plugin_version": str(self.context.manifest.get("version") or ""),
                    "instance_id": os.environ.get("TPX_INSTANCE_ID") or plugin_id,
                    "pid": os.getpid(),
                    "thread_name": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                    "async_task": _async_task_name(),
                },
                error=(
                    record.exc_info[1]
                    if record.exc_info and isinstance(record.exc_info[1], BaseException)
                    else None
                ),
                sequence=sequence,
            )
            event = bounded_diagnostic_event(
                event,
                max_line_bytes=FEATURE_DIAGNOSTIC_MAX_LINE_BYTES,
                prefix=FEATURE_DIAGNOSTIC_TRANSPORT_PREFIX,
            )
            line = (
                FEATURE_DIAGNOSTIC_TRANSPORT_PREFIX
                + render_machine_event(event)
                + "\n"
            )
            self._drained.clear()
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                if self._queue.empty():
                    self._drained.set()
        except Exception as exc:
            self._transport_failure(exc)

    def flush(self):
        self._drained.wait(timeout=1)

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        super().close()


def configure_feature_logging(context) -> logging.Logger:
    level_name = str(os.environ.get("TPX_LOG_LEVEL") or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    handler = _FeatureDiagnosticTransportHandler(context, stream=sys.stdout)
    handler.setLevel(level)
    root.addHandler(handler)
    logger = logging.getLogger(f"telepiplex.feature.{context.manifest['plugin_id']}")
    logger.setLevel(level)
    logger.info(
        "Feature 运行时已启动",
        extra={
            "event_name": "feature.runtime.bootstrap",
            "diagnostic_fields": {
                "stage": "bootstrap",
                "status": "ready",
                "input": {
                    "config_path": str(context.config_path),
                    "state_path": str(context.state_path),
                },
                "output": {
                    "runtime_log": os.environ.get("TPX_RUNTIME_LOG_PATH") or "",
                },
            },
        },
    )
    return logger


def log_dispatch_start(method: str, key: str, params: dict):
    logging.getLogger("telepiplex.runtime").info(
        "Feature 开始处理请求",
        extra={
            "event_name": "feature.dispatch.started",
            "diagnostic_fields": {
                "stage": "dispatch",
                "status": "started",
                "input": {
                    "method": str(method),
                    "handler": str(key),
                    "params": bounded_diagnostic_value(params),
                },
            },
        },
    )


def log_dispatch_finish(method: str, key: str, result: dict, *, duration_ms=None):
    logging.getLogger("telepiplex.runtime").info(
        "Feature 请求处理完成",
        extra={
            "event_name": "feature.dispatch.completed",
            "diagnostic_fields": {
                "stage": "dispatch",
                "status": "completed",
                "duration_ms": duration_ms,
                "input": {"method": str(method), "handler": str(key)},
                "output": {"result": bounded_diagnostic_value(result)},
            },
        },
    )


def log_dispatch_error(
    method: str,
    key: str,
    code: str,
    detail,
    *,
    duration_ms=None,
):
    logger = logging.getLogger("telepiplex.runtime")
    fields = {
        "stage": "dispatch",
        "status": "failed",
        "duration_ms": duration_ms,
        "input": {"method": str(method), "handler": str(key)},
        "output": {"error_code": str(code), "detail": str(detail)},
    }
    if isinstance(detail, BaseException):
        logger.error(
            "Feature 请求处理失败",
            exc_info=(type(detail), detail, detail.__traceback__),
            extra={
                "event_name": "feature.dispatch.failed",
                "diagnostic_fields": fields,
            },
        )
    else:
        logger.error(
            "Feature 请求处理失败",
            extra={
                "event_name": "feature.dispatch.failed",
                "diagnostic_fields": fields,
            },
        )

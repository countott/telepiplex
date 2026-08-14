from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time

from .diagnostics import (
    build_diagnostic_event,
    infer_legacy_diagnostics,
    render_machine_event,
)
from .log_sanitizer import sanitize_log_text, sanitize_log_value


FEATURE_DIAGNOSTIC_TRANSPORT_PREFIX = "@tpx-event-v1 "
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
        setattr(self, _HANDLER_MARKER, True)

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
            self.stream.write(
                FEATURE_DIAGNOSTIC_TRANSPORT_PREFIX
                + render_machine_event(event)
                + "\n"
            )
            self.stream.flush()
        except Exception as exc:
            try:
                sys.__stderr__.write(
                    f"telepiplex Feature diagnostics transport failed: {type(exc).__name__}\n"
                )
                sys.__stderr__.flush()
            except Exception:
                pass


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
                    "params": params,
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
                "output": {"result": result},
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

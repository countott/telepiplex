"""Privacy-safe Download performance observations."""

from __future__ import annotations

import math

from .context import logger


_EVENT_NAMES = {
    "download.request.completed",
    "download.request.failed",
    "download.pacing.waited",
    "download.pacing.throttled",
    "download.poll.backoff_changed",
}
_OPERATIONS = {
    "add_offline_task",
    "create_directory",
    "delete_file",
    "delete_offline_task",
    "get_file_info",
    "get_file_list",
    "get_offline_tasks",
    "move_files",
    "refresh_access_token",
    "rename_file",
    "wait_for_download",
}
_ENDPOINT_CLASSES = {
    "offline.poll",
    "offline.mutation",
    "storage.read",
    "storage.mutation",
    "token.refresh",
}
_STATUS_CLASSES = {"2xx", "4xx", "5xx", "unknown"}
_MILLISECOND_FACTS = {
    "pacer_wait_ms",
    "http_elapsed_ms",
    "cooldown_ms",
    "previous_delay_ms",
    "next_delay_ms",
}


def _safe_milliseconds(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(round(value), 300_000)


def _safe_facts(facts: dict) -> dict:
    """Whitelist fixed measurement facts; never serialize provider inputs."""
    safe = {}
    endpoint_class = facts.get("endpoint_class")
    if endpoint_class in _ENDPOINT_CLASSES:
        safe["endpoint_class"] = endpoint_class
    operation = facts.get("operation")
    if operation in _OPERATIONS:
        safe["operation"] = operation
    status_class = facts.get("status_class")
    if status_class in _STATUS_CLASSES:
        safe["status_class"] = status_class
    if isinstance(facts.get("retryable"), bool):
        safe["retryable"] = facts["retryable"]
    for name in _MILLISECOND_FACTS:
        value = _safe_milliseconds(facts.get(name))
        if value is not None:
            safe[name] = value
    return safe


def emit_download_observation(event_name: str, **facts) -> None:
    """Emit a bounded diagnostic record without affecting Download behavior."""
    if event_name not in _EVENT_NAMES:
        return
    status = "failed" if event_name.endswith(".failed") else "completed"
    try:
        logger.info(
            "download_observation",
            extra={
                "event_name": event_name,
                "diagnostic_fields": {
                    "stage": "performance",
                    "status": status,
                    "output": _safe_facts(facts),
                },
            },
        )
    except Exception:
        pass

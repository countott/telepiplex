"""Structured, sanitized logging for one search business session."""

from __future__ import annotations

import re
import time

from .log_sanitizer import sanitize_log_value


_SEARCH_LOG_CONTEXTS: dict[str, dict] = {}


def bind_search_log_context(
    search_session_id: str,
    *,
    chat_id=None,
    user_id=None,
    operation_id=None,
    update_id=None,
) -> None:
    session_id = str(search_session_id or "").strip()
    if not session_id:
        return
    _SEARCH_LOG_CONTEXTS[session_id] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "operation_id": operation_id,
        "update_id": update_id,
        "started_at": time.monotonic(),
    }


def clear_search_log_context(search_session_id: str) -> None:
    _SEARCH_LOG_CONTEXTS.pop(
        str(search_session_id or "").strip(),
        None,
    )


def log_search_event(
    logger,
    event: str,
    *,
    search_session_id: str,
    level: str = "info",
    **fields,
) -> None:
    session_id = str(search_session_id or "").strip()
    if logger is None:
        if str(event or "").strip() == "search.completed":
            clear_search_log_context(session_id)
        return
    context = _SEARCH_LOG_CONTEXTS.get(session_id) or {}
    for key in ("chat_id", "user_id", "operation_id", "update_id"):
        if key not in fields and context.get(key) is not None:
            fields[key] = context[key]
    if "elapsed_ms" not in fields and context.get("started_at") is not None:
        fields["elapsed_ms"] = max(
            0,
            round((time.monotonic() - context["started_at"]) * 1000),
        )
    method = getattr(logger, str(level or "info").casefold(), None)
    if not callable(method):
        method = getattr(logger, "info", None)
    if not callable(method):
        if str(event or "").strip() == "search.completed":
            clear_search_log_context(session_id)
        return
    parts = [
        f"event={sanitize_log_value(event, max_chars=120)}",
        "search_session_id="
        + sanitize_log_value(session_id, max_chars=120),
    ]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        safe_value = (
            "***redacted***"
            if re.search(
                r"(?i)token|api.?key|secret|authorization|password|cookie",
                str(key),
            )
            else sanitize_log_value(value, max_chars=2000)
        )
        parts.append(
            f"{key}={safe_value}"
        )
    method(" ".join(parts))
    if str(event or "").strip() == "search.completed":
        clear_search_log_context(session_id)

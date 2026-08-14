from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import shlex
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = "1.0"
REDACTED = "***redacted***"
DIAGNOSTIC_CONTEXT_FIELDS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "operation_id",
    "request_id",
    "incident_id",
)

_SENSITIVE_KEY_PARTS = (
    "token",
    "apikey",
    "api_key",
    "secret",
    "authorization",
    "password",
    "cookie",
    "sign",
    "codeverifier",
    "accesskey",
)
_SENSITIVE_EXACT_KEYS = {
    "link",
    "url",
    "endpoint",
    "download_link",
    "downloadlink",
    "download_url",
    "downloadurl",
    "magnet",
    "magnet_url",
    "magneturl",
    "post_url",
    "posturl",
    "video_url",
    "videourl",
    "downurl",
    "photo_url",
}
_CONTEXT = contextvars.ContextVar(
    "telepiplex_diagnostic_context",
    default={field: None for field in DIAGNOSTIC_CONTEXT_FIELDS},
)
_EVENT_TITLES = {
    "search.source.completed": "搜索来源查询已完成",
    "search.source.failed": "搜索来源查询失败",
    "feature.dispatch.started": "Feature 开始处理请求",
    "feature.dispatch.completed": "Feature 请求处理完成",
    "feature.dispatch.failed": "Feature 请求处理失败",
    "feature.process_output": "Feature 进程输出",
    "telegram.interaction.received": "收到 Telegram 交互",
    "telegram.feature_action.delivered": "已回复 Telegram 用户",
    "telegram.feedback.delivered": "已回复 Telegram 用户",
    "telegram.api.delivered": "已回复 Telegram 用户",
    "telegram.milestone.delivery.started": "准备更新 Telegram 进度",
    "telegram.milestone.delivery.completed": "已更新 Telegram 进度",
    "telegram.milestone.delivery.failed": "Telegram 进度更新失败",
    "telegram.update.failed": "Telegram 请求处理失败",
    "telegram.error_notice.failed": "Telegram 错误通知发送失败",
    "diagnostics.event_gap": "诊断事件序号出现缺口",
    "log.message": "运行日志",
}
_HUMAN_FIELD_NAMES = {
    "stage": "阶段",
    "status": "状态",
    "duration_ms": "耗时",
    "input": "输入",
    "output": "结果",
    "state_transition": "状态变化",
    "retry": "重试",
    "transport": "传输",
    "user_surface": "前台",
    "title": "标题",
    "source": "来源",
    "tvdb_id": "TVDB ID",
    "inventory_count": "库存数量",
    "action": "动作",
    "text": "文案",
}
_HUMAN_TECHNICAL_FACT_KEYS = {
    "async_task",
    "chat_id",
    "deadline_ms",
    "event_id",
    "existing_message_id",
    "idempotency_key_present",
    "instance_id",
    "message_id",
    "operation_id",
    "parent_span_id",
    "pid",
    "plugin_id",
    "request_id",
    "session_id",
    "span_id",
    "thread_id",
    "trace_id",
    "update_id",
    "user_id",
}
_HUMAN_OMITTED_FACT_KEYS = {"transport"}
_HUMAN_MACHINE_ONLY_EVENTS = {
    "telegram.feature_action.delivered",
    "telegram.feedback.delivered",
    "telegram.milestone.delivery.started",
    "telegram.milestone.delivery.completed",
}
_HUMAN_ACTION_NAMES = {
    "edit_message": "编辑消息",
    "edit_photo": "编辑图片消息",
    "send_message": "发送消息",
    "send_photo": "发送图片消息",
    "send_photo_grid": "发送海报墙",
    "answer_callback": "回应按钮操作",
}


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key or "").lower())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    compact = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or compact in _SENSITIVE_EXACT_KEYS
        or any(part in normalized or part in compact for part in _SENSITIVE_KEY_PARTS)
    )


def _escape_pointer(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _redact_text(value: object) -> tuple[str, bool]:
    text = str(value)
    original = text
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+\-/=]+", f"Bearer {REDACTED}", text)
    text = re.sub(r"sk-[A-Za-z0-9._~+\-/=]{8,}", f"sk-{REDACTED}", text)
    text = re.sub(
        r"(?i)(access_token|refresh_token|api_key|token|secret|password)=([^&\s\"'`,;}\]\)]+)",
        lambda match: f"{match.group(1)}={REDACTED}",
        text,
    )
    text = re.sub(r"magnet:\?[^\s\"'`]+", f"magnet:?{REDACTED}", text)
    text = re.sub(r"https?://[^\s\"']+", f"https://{REDACTED}", text)
    return text, text != original


def sanitize_diagnostic_value(value: Any) -> tuple[Any, list[str]]:
    redacted_paths: list[str] = []

    def visit(item: Any, path: str, depth: int) -> Any:
        if depth > 32:
            redacted_paths.append(path or "/")
            return "[maximum-depth-reached]"
        if isinstance(item, Mapping):
            result = {}
            for key, nested in item.items():
                child_path = f"{path}/{_escape_pointer(key)}"
                if _is_sensitive_key(key):
                    result[str(key)] = REDACTED
                    redacted_paths.append(child_path)
                else:
                    result[str(key)] = visit(nested, child_path, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [
                visit(nested, f"{path}/{index}", depth + 1)
                for index, nested in enumerate(item)
            ]
        if isinstance(item, str):
            sanitized, changed = _redact_text(item)
            if changed:
                redacted_paths.append(path or "/")
            return sanitized
        if item is None or isinstance(item, (bool, int, float)):
            return item
        sanitized, changed = _redact_text(item)
        if changed:
            redacted_paths.append(path or "/")
        return sanitized

    return visit(value, "", 0), sorted(set(redacted_paths))


def current_diagnostic_context() -> dict[str, str | None]:
    current = dict(_CONTEXT.get())
    return {field: current.get(field) for field in DIAGNOSTIC_CONTEXT_FIELDS}


@contextlib.contextmanager
def bind_diagnostic_context(**fields: object) -> Iterator[dict[str, str | None]]:
    current = current_diagnostic_context()
    for key, value in fields.items():
        if key in DIAGNOSTIC_CONTEXT_FIELDS:
            current[key] = str(value) if value not in (None, "") else None
    token = _CONTEXT.set(current)
    try:
        yield current_diagnostic_context()
    finally:
        _CONTEXT.reset(token)


def set_diagnostic_context(**fields: object) -> dict[str, str | None]:
    current = current_diagnostic_context()
    for key, value in fields.items():
        if key in DIAGNOSTIC_CONTEXT_FIELDS:
            current[key] = str(value) if value not in (None, "") else None
    _CONTEXT.set(current)
    return current_diagnostic_context()


def new_trace_id() -> str:
    return f"TRC-{uuid.uuid4().hex[:12].upper()}"


def new_span_id() -> str:
    return f"SPN-{uuid.uuid4().hex[:12].upper()}"


def new_incident_id() -> str:
    return f"INC-{uuid.uuid4().hex[:12].upper()}"


def _typed_legacy_value(value: str) -> object:
    normalized = str(value)
    if normalized.casefold() == "true":
        return True
    if normalized.casefold() == "false":
        return False
    if normalized.casefold() in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", normalized):
        try:
            return int(normalized)
        except ValueError:
            return normalized
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", normalized):
        try:
            return float(normalized)
        except ValueError:
            return normalized
    return normalized


def infer_legacy_diagnostics(message: str) -> tuple[str, dict[str, object]]:
    try:
        tokens = shlex.split(str(message or ""))
    except ValueError:
        tokens = str(message or "").split()
    if not tokens:
        return "log.message", {}
    event_name = "log.message"
    values: dict[str, object] = {}
    first = tokens[0]
    if "=" not in first and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,127}", first):
        event_name = first
        tokens = tokens[1:]
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
            continue
        values[key] = _typed_legacy_value(value)
    if values.get("event") not in (None, ""):
        event_name = str(values.pop("event"))
    fields: dict[str, object] = {}
    for promoted in ("stage", "status", "duration_ms"):
        if promoted in values:
            fields[promoted] = values.pop(promoted)
    if values:
        fields["legacy_fields"] = values
    return event_name, fields


def outbound_diagnostic_context(
    *,
    request_id: str,
    operation_id: object = None,
) -> dict[str, str | None]:
    current = current_diagnostic_context()
    trace_id = current.get("trace_id") or new_trace_id()
    parent_span_id = current.get("span_id")
    return {
        "trace_id": trace_id,
        "span_id": new_span_id(),
        "parent_span_id": parent_span_id,
        "operation_id": (
            str(operation_id)
            if operation_id not in (None, "")
            else current.get("operation_id")
        ),
        "request_id": str(request_id),
        "incident_id": current.get("incident_id"),
    }


def _error_payload(error: BaseException | None) -> dict[str, Any]:
    if error is None:
        return {
            "code": None,
            "type": None,
            "message": None,
            "retryable": None,
            "stack": None,
            "causes": [],
        }
    stack = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    causes = []
    seen: set[int] = set()
    current = error.__cause__ or error.__context__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        causes.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or current.__context__
    return {
        "code": str(getattr(error, "code", "") or type(error).__name__),
        "type": type(error).__name__,
        "message": str(error),
        "retryable": getattr(error, "retryable", None),
        "stack": stack,
        "causes": causes,
    }


def build_diagnostic_event(
    *,
    level: str,
    event_name: str,
    message: str,
    session_id: str,
    logger_name: str,
    component: str = "telepiplex",
    context: Mapping[str, object] | None = None,
    fields: Mapping[str, object] | None = None,
    runtime: Mapping[str, object] | None = None,
    error: BaseException | None = None,
    error_payload: Mapping[str, object] | None = None,
    event_id: str | None = None,
    sequence: int = 0,
    ingest_sequence: int | None = None,
    timestamp: datetime | None = None,
    monotonic_ns: int | None = None,
) -> dict[str, Any]:
    occurred_at = timestamp or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    utc_time = occurred_at.astimezone(timezone.utc)
    local_time = occurred_at.astimezone()
    active_context = current_diagnostic_context()
    for key, value in (context or {}).items():
        if key in DIAGNOSTIC_CONTEXT_FIELDS:
            active_context[key] = str(value) if value not in (None, "") else None
    facts = dict(fields or {})
    stage = facts.pop("stage", None)
    status = facts.pop("status", None)
    duration_ms = facts.pop("duration_ms", None)
    runtime_values = {
        "host_version": None,
        "plugin_id": None,
        "plugin_version": None,
        "instance_id": None,
        "pid": os.getpid(),
        "thread_name": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        "async_task": None,
    }
    runtime_values.update(dict(runtime or {}))
    raw_event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event_id or f"EVT-{uuid.uuid4().hex.upper()}"),
        "sequence": {
            "producer": max(0, int(sequence)),
            "ingest": None if ingest_sequence is None else max(0, int(ingest_sequence)),
        },
        "time": {
            "utc": utc_time.isoformat(),
            "local": local_time.isoformat(),
            "timezone": local_time.tzname() or str(local_time.tzinfo or "UTC"),
            "unix_ns": int(utc_time.timestamp()) * 1_000_000_000 + utc_time.microsecond * 1_000,
            "monotonic_ns": int(monotonic_ns if monotonic_ns is not None else time.monotonic_ns()),
        },
        "level": str(level or "INFO").upper(),
        "logger": str(logger_name or "telepiplex"),
        "component": str(component or "telepiplex"),
        "identity": {
            "session_id": str(session_id),
            **active_context,
        },
        "event": {
            "name": str(event_name or "log.message"),
            "message": str(message or ""),
            "stage": None if stage in (None, "") else str(stage),
            "status": None if status in (None, "") else str(status),
            "duration_ms": None if duration_ms is None else float(duration_ms),
        },
        "runtime": runtime_values,
        "facts": facts,
        "error": dict(error_payload or _error_payload(error)),
    }
    sanitized, paths = sanitize_diagnostic_value(raw_event)
    sanitized["privacy"] = {
        "redacted_paths": paths,
        "redaction_count": len(paths),
        "sanitized": True,
    }
    return sanitized


def render_machine_event(event: Mapping[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)


def _human_scalar(value: object) -> str:
    if value is None:
        return "无"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _human_value(value: object) -> str:
    if isinstance(value, Mapping):
        return "；".join(
            f"{_HUMAN_FIELD_NAMES.get(str(key), str(key))}：{_human_value(item)}"
            for key, item in value.items()
        ) or "无"
    if isinstance(value, (list, tuple)):
        return "、".join(_human_value(item) for item in value) or "无"
    return _human_scalar(value)


def _human_business_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): filtered
            for key, item in value.items()
            if str(key) not in _HUMAN_TECHNICAL_FACT_KEYS
            if (filtered := _human_business_value(item)) not in (None, "", {}, [])
        }
    if isinstance(value, (list, tuple)):
        return [
            filtered
            for item in value
            if (filtered := _human_business_value(item)) not in (None, "", {}, [])
        ]
    return value


def _human_user_surface_lines(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    direction = str(value.get("direction") or "outgoing").casefold()
    kind = str(value.get("kind") or "message").casefold()
    text = value.get("text")
    callback_data = value.get("callback_data")
    if direction in {"planned", "delivery_contract"}:
        return []
    if direction == "incoming":
        if kind == "command" and text not in (None, ""):
            return [f"收到指令：{text}"]
        if kind == "callback" and callback_data not in (None, ""):
            return [f"收到回调：{callback_data}"]
        if text not in (None, ""):
            return [f"收到消息：{text}"]
        return ["收到交互：Telegram 更新未包含文字或回调数据"]

    lines = []
    if text not in (None, ""):
        lines.append(f"回复内容：{text}")
    action = str(value.get("action") or "")
    if action:
        lines.append(f"回复方式：{_HUMAN_ACTION_NAMES.get(action, action)}")
    data = _human_business_value(value.get("data"))
    if data not in (None, "", {}, []):
        lines.append(f"界面内容：{_human_value(data)}")
    return lines


def render_human_event(event: Mapping[str, object]) -> str:
    event_data = event.get("event") if isinstance(event.get("event"), Mapping) else {}
    identity = event.get("identity") if isinstance(event.get("identity"), Mapping) else {}
    facts = event.get("facts") if isinstance(event.get("facts"), Mapping) else {}
    error = event.get("error") if isinstance(event.get("error"), Mapping) else {}
    privacy = event.get("privacy") if isinstance(event.get("privacy"), Mapping) else {}
    time_data = event.get("time") if isinstance(event.get("time"), Mapping) else {}
    local = str(time_data.get("local") or "")
    local_second = local[:19].replace("T", " ") if len(local) >= 19 else local
    event_name = str(event_data.get("name") or "log.message")
    if event_name in _HUMAN_MACHINE_ONLY_EVENTS:
        return ""
    message = str(event_data.get("message") or "")
    title = (
        message or "运行日志"
        if event_name == "log.message"
        else _EVENT_TITLES.get(event_name, message or event_name)
    )
    lines = [f"[{local_second}] {title}", ""]
    if event_data.get("message") and str(event_data.get("message")) != title:
        lines.append(f"说明：{event_data['message']}")
    lines.append(
        f"组件 {event.get('component')}｜级别 {event.get('level')}｜"
        f"记录器 {event.get('logger')}"
    )
    processing = []
    if event_data.get("stage") is not None:
        processing.append(f"阶段 {_human_scalar(event_data['stage'])}")
    if event_data.get("status") is not None:
        processing.append(f"状态 {_human_scalar(event_data['status'])}")
    if event_data.get("duration_ms") is not None:
        processing.append(f"耗时 {_human_scalar(event_data['duration_ms'])} ms")
    if processing:
        lines.append("处理：" + "｜".join(processing))
    for key, value in facts.items():
        if key in _HUMAN_OMITTED_FACT_KEYS or value in (None, "", {}, []):
            continue
        if key == "user_surface":
            lines.extend(_human_user_surface_lines(value))
            continue
        filtered = _human_business_value(value)
        if filtered in (None, "", {}, []):
            continue
        lines.append(
            f"{_HUMAN_FIELD_NAMES.get(str(key), str(key))}：{_human_value(filtered)}"
        )
    if error.get("type"):
        if identity.get("incident_id"):
            lines.append(f"问题编号：{identity.get('incident_id')}")
        lines.extend([
            f"错误代码：{error.get('code')}",
            f"异常类型：{error.get('type')}",
            f"异常说明：{error.get('message')}",
        ])
        if error.get("stack"):
            lines.append(f"异常调用路径：\n{error.get('stack')}")
        if error.get("causes"):
            lines.append(f"异常链：{_human_value(error.get('causes'))}")
    if privacy.get("redaction_count"):
        lines.append("敏感信息：已按脱敏规则隐藏")
    return "\n".join(lines).rstrip() + "\n"


def payload_chunks(
    value: str,
    *,
    payload_ref: str | None = None,
    chunk_chars: int = 64 * 1024,
) -> list[dict[str, object]]:
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be positive")
    sanitized, paths = sanitize_diagnostic_value(str(value))
    text = str(sanitized)
    reference = str(payload_ref or f"PAY-{uuid.uuid4().hex.upper()}")
    parts = [
        text[index:index + chunk_chars]
        for index in range(0, len(text), chunk_chars)
    ] or [""]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return [
        {
            "payload_ref": reference,
            "index": index,
            "count": len(parts),
            "content": part,
            "sanitized_length": len(text),
            "sha256": digest,
            "redacted_paths": paths,
        }
        for index, part in enumerate(parts)
    ]

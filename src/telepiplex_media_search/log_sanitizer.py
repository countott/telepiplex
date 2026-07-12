# -*- coding: utf-8 -*-
import json
import re

REDACTED = "***redacted***"
DEFAULT_LOG_VALUE_LIMIT = 2000

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
}


def _normalized_key(key) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key or "").lower())


def _is_sensitive_key(key) -> bool:
    normalized = _normalized_key(key)
    compact = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or compact in _SENSITIVE_EXACT_KEYS
        or any(part in normalized or part in compact for part in _SENSITIVE_KEY_PARTS)
    )


def _redact_text(value: str) -> str:
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+\-/=]+", f"Bearer {REDACTED}", value)
    text = re.sub(r"sk-[A-Za-z0-9._~+\-/=]{8,}", f"sk-{REDACTED}", text)
    text = re.sub(
        r"(?i)(access_token|refresh_token|api_key|token|secret)=([^&\s]+)",
        lambda match: f"{match.group(1)}={REDACTED}",
        text,
    )
    text = re.sub(r"magnet:\?[^\s\"'`]+", f"magnet:?{REDACTED}", text)
    text = re.sub(r"https?://[^\s\"']+", f"https://{REDACTED}", text)
    return text


def _sanitize(value, depth=0):
    if depth > 8:
        return "[truncated]"

    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else _sanitize(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item, depth + 1) for item in value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                return _redact_text(value)
            return _sanitize(parsed, depth + 1)
        return _redact_text(value)
    return value


def sanitize_log_value(value, max_chars=DEFAULT_LOG_VALUE_LIMIT) -> str:
    sanitized = _sanitize(value)
    if isinstance(sanitized, (dict, list, tuple)):
        text = json.dumps(sanitized, ensure_ascii=False, default=str)
    else:
        text = str(sanitized)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text

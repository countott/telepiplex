from __future__ import annotations

import json
import re


REDACTED = "***redacted***"
DEFAULT_LOG_VALUE_LIMIT = 2000


def sanitize_log_text(value: str, max_chars=DEFAULT_LOG_VALUE_LIMIT) -> str:
    text = str(value or "")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+\-/=]+", f"Bearer {REDACTED}", text)
    text = re.sub(r"sk-[A-Za-z0-9._~+\-/=]{8,}", f"sk-{REDACTED}", text)
    text = re.sub(
        r"(?i)(access_token|refresh_token|api_key|token|secret|password)=([^&\s]+)",
        lambda match: f"{match.group(1)}={REDACTED}",
        text,
    )
    text = re.sub(r"magnet:\?[^\s\"'`]+", f"magnet:?{REDACTED}", text)
    text = re.sub(r"https?://[^\s\"']+", f"https://{REDACTED}", text)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def sanitize_log_value(value, max_chars=DEFAULT_LOG_VALUE_LIMIT) -> str:
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return sanitize_log_text(text, max_chars=max_chars)

"""Telegram text and caption boundary helpers."""

from __future__ import annotations

import html
import re


_HTML_TAG = re.compile(r"<[^>]+>")


def visible_html_text(value: str) -> str:
    return html.unescape(_HTML_TAG.sub("", str(value or "")))


def bounded_photo_caption(
    value: str,
    parse_mode: str | None,
    *,
    limit: int = 1024,
) -> tuple[str, str | None]:
    """Keep Telegram photo captions within the post-entity text limit."""

    value = str(value or "")
    visible = (
        visible_html_text(value)
        if parse_mode == "HTML"
        else value
    )
    if len(visible) <= limit:
        return value, parse_mode
    suffix = "\n…内容已截断"
    bounded = visible[: max(0, limit - len(suffix))].rstrip() + suffix
    return bounded, None


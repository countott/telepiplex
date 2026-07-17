"""Shared normalization for bounded user-entered media queries."""

from __future__ import annotations

import re
import unicodedata


_SEPARATOR_PATTERN = re.compile(
    r"""[
        :：,，;；!！?？。、
        《》〈〉「」『』【】\[\]
        “‘”’"'`·•—–_
    ]+""",
    re.VERBOSE,
)
_PARENTHESES = re.compile(r"[()（）]")
_UNSUPPORTED_PATTERNS = (
    re.compile(r"(?i)\bS\d{1,2}\s*[-~至到]\s*S?\d{1,2}\b"),
    re.compile(
        r"(?i)\bS\d{1,2}E\d{1,3}\s*[-~至到]\s*"
        r"(?:S\d{1,2})?E?\d{1,3}\b"
    ),
    re.compile(r"(?i)\b\d{1,2}\s*x\s*\d{1,3}\b"),
    re.compile(
        r"(?i)\bseason\s+"
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
        r"seventeen|eighteen|nineteen|twenty)\b"
    ),
    re.compile(
        r"第?\s*[零〇一二两三四五六七八九十\d]+\s*[季集话話]\s*"
        r"(?:-|~|至|到)\s*第?\s*[零〇一二两三四五六七八九十\d]+\s*"
        r"[季集话話]"
    ),
    re.compile(r"前\s*[零〇一二两三四五六七八九十\d]+\s*集"),
    re.compile(r"最新\s*(?:几|[零〇一二两三四五六七八九十\d]+)\s*集"),
)


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace("\xa0", " ")


def normalize_query_text(value: str) -> str:
    """Normalize typography while preserving every title word."""

    text = _nfkc(value)
    text = _SEPARATOR_PATTERN.sub(" ", text)
    text = _PARENTHESES.sub(" ", text)
    return " ".join(text.split())


def has_unsupported_range_syntax(value: str) -> bool:
    """Return whether a query asks for an intentionally unsupported range."""

    text = " ".join(_nfkc(value).split())
    return any(pattern.search(text) for pattern in _UNSUPPORTED_PATTERNS)

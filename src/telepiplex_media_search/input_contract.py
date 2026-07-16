"""Deterministic, bounded input classification for media-search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .search_query import extract_douban_subject_id, is_supported_metadata_url
from .search_resolution import parse_search_intent


@dataclass(frozen=True)
class NumericToken:
    value: int
    role: str


@dataclass(frozen=True)
class MetadataLink:
    provider: str
    media_type: str
    entity_id: str
    scope: str
    url: str


@dataclass(frozen=True)
class ParsedInput:
    kind: str
    raw_query: str
    title: str = ""
    year: str = ""
    media_type: str = ""
    scope: str = "work"
    season_number: int | None = None
    episode_number: int | None = None
    link: MetadataLink | None = None
    numeric_tokens: tuple[NumericToken, ...] = ()
    reason: str = ""


_TRAILING_BARE_NUMBER = re.compile(r"(?<!\d)(\d{1,3})\s*$")


def _tvdb_link(raw_query: str) -> MetadataLink | None:
    parsed = urlparse(raw_query)
    if "tvdb.com" not in parsed.netloc.casefold():
        return None
    match = re.fullmatch(
        r"/(?:[a-z]{2}/)?(movies|series|seasons|episodes)/([^/?#]+)/?",
        parsed.path,
        re.IGNORECASE,
    )
    if not match:
        return None
    kind, entity_id = match.groups()
    kind = kind.casefold()
    return MetadataLink(
        provider="tvdb",
        media_type="movie" if kind == "movies" else "series",
        entity_id=entity_id,
        scope={
            "movies": "work",
            "series": "work",
            "seasons": "season",
            "episodes": "episode",
        }[kind],
        url=raw_query,
    )


def _metadata_link(raw_query: str) -> MetadataLink | None:
    subject_id = extract_douban_subject_id(raw_query)
    if subject_id:
        return MetadataLink(
            provider="douban",
            media_type="",
            entity_id=subject_id,
            scope="work",
            url=raw_query,
        )
    return _tvdb_link(raw_query)


def classify_search_input(raw_query: str) -> ParsedInput:
    raw_query = " ".join(str(raw_query or "").split())
    link = _metadata_link(raw_query)
    if link:
        return ParsedInput(
            kind="link",
            raw_query=raw_query,
            media_type=link.media_type,
            scope=link.scope,
            link=link,
        )
    if is_supported_metadata_url(raw_query):
        return ParsedInput(
            kind="invalid_link",
            raw_query=raw_query,
            reason="unsupported_metadata_link",
        )

    intent = parse_search_intent(raw_query)
    scope = str(intent.get("scope") or "movie_or_series")
    if scope == "movie_or_series":
        scope = "work"
    numeric_tokens = []
    match = _TRAILING_BARE_NUMBER.search(raw_query)
    if match and not (
        intent.get("year")
        or intent.get("season_number") is not None
        or intent.get("episode_number") is not None
    ):
        numeric_tokens.append(NumericToken(int(match.group(1)), "ambiguous"))
    return ParsedInput(
        kind="text",
        raw_query=raw_query,
        title=str(intent.get("title") or "").strip(),
        year=str(intent.get("year") or "").strip(),
        scope=scope,
        season_number=intent.get("season_number"),
        episode_number=intent.get("episode_number"),
        numeric_tokens=tuple(numeric_tokens),
    )


def has_ambiguous_bare_number(
    raw_query: str,
    parsed: ParsedInput | None = None,
) -> bool:
    parsed = parsed or classify_search_input(raw_query)
    return any(token.role == "ambiguous" for token in parsed.numeric_tokens)

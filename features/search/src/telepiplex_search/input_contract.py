"""Deterministic, bounded input classification for search."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from .query_normalization import (
    has_unsupported_range_syntax,
    normalize_query_text,
)
from .search_query import extract_douban_subject_id
from .search_resolution import parse_search_intent, quoted_numeric_title


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
    urls: tuple[str, ...] = ()
    fallback_title: str = ""
    reason: str = ""


_TRAILING_BARE_NUMBER = re.compile(r"(?<!\d)(\d{1,3})\s*$")
_LEADING_QUOTED_NUMERIC_TITLE = re.compile(
    r'^\s*(?:"(?P<ascii>(?:19|20)\d{2})"|'
    r'“(?P<curly>(?:19|20)\d{2})”)'
    r'(?=\s|$)'
)
_MEDIA_TYPE_TOKEN = re.compile(
    r"(?i)(?<!\S)"
    r"(电影|電影|movie|film|电视剧|電視劇|剧集|劇集|series|tv\s*show)"
    r"(?!\S)"
)
_NATURAL_LANGUAGE_REQUEST = re.compile(
    r"(?:帮我|請幫我|请帮我)(?:找|搜|推荐)|"
    r"我想(?:看|找|搜)|"
    r"有没有.*(?:电影|電影|电视剧|電視劇|剧集|劇集|美剧|日剧|韩剧|动画|動畫)|"
    r"类似.+的(?:电影|電影|电视剧|電視劇|剧|劇|动画|動畫)",
    re.IGNORECASE,
)
_MESSAGE_URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?，。；：！？"
_TRAILING_URL_CLOSERS = {
    ")": "(",
    "]": "[",
    "}": "{",
    ">": "<",
    "》": "《",
    "】": "【",
    "」": "「",
    "』": "『",
}
_SUPPORTED_LINK_HOSTS = (
    "douban.com",
    "wikipedia.org",
    "wikidata.org",
    "w.wiki",
    "thetvdb.com",
    "tvdb.com",
    "themoviedb.org",
    "anilist.co",
)


def extract_message_urls(raw_query: str) -> tuple[str, ...]:
    urls = []
    for match in _MESSAGE_URL.finditer(str(raw_query or "")):
        url = html.unescape(match.group(0)).rstrip(
            _TRAILING_URL_PUNCTUATION
        )
        while (
            url
            and url[-1] in _TRAILING_URL_CLOSERS
            and url.count(url[-1])
            > url.count(_TRAILING_URL_CLOSERS[url[-1]])
        ):
            url = url[:-1]
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def contains_url(raw_query: str) -> bool:
    return bool(extract_message_urls(raw_query))


def _host_matches(host: str, root: str) -> bool:
    return host == root or host.endswith(f".{root}")


def _supported_provider(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        return ""
    host = str(parsed.hostname or "").casefold()
    if _host_matches(host, "douban.com"):
        return "douban"
    if _host_matches(host, "wikipedia.org") or host == "w.wiki":
        return "wikipedia"
    if _host_matches(host, "wikidata.org"):
        return "wikidata"
    if (
        _host_matches(host, "thetvdb.com")
        or _host_matches(host, "tvdb.com")
    ):
        return "tvdb"
    if _host_matches(host, "themoviedb.org"):
        return "tmdb"
    if _host_matches(host, "anilist.co"):
        return "anilist"
    return ""


def is_supported_direct_message_url(raw_url: str) -> bool:
    return bool(_supported_provider(raw_url))


def _fallback_title(raw_query: str, urls: tuple[str, ...]) -> str:
    text = str(raw_query or "")
    for url in urls:
        text = text.replace(url, " ")
    return " ".join(text.split()).strip()


def _douban_link(raw_url: str) -> MetadataLink | None:
    if _supported_provider(raw_url) != "douban":
        return None
    subject_id = extract_douban_subject_id(raw_url)
    if not subject_id:
        query = parse_qs(urlparse(raw_url).query)
        for key in ("uri", "url", "target"):
            for value in query.get(key, ()):
                subject_id = extract_douban_subject_id(unquote(value))
                if subject_id:
                    break
            if subject_id:
                break
    if not subject_id:
        return None
    return MetadataLink(
        provider="douban",
        media_type="",
        entity_id=subject_id,
        scope="work",
        url=raw_url,
    )


def _tvdb_link(raw_query: str) -> MetadataLink | None:
    if _supported_provider(raw_query) != "tvdb":
        return None
    parsed = urlparse(raw_query)
    host = str(parsed.hostname or "").casefold()
    if host not in {
        "tvdb.com",
        "www.tvdb.com",
        "thetvdb.com",
        "www.thetvdb.com",
    }:
        return None
    match = re.fullmatch(
        r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?"
        r"(movies|series|seasons|episodes)/([^/?#]+)/?",
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


def _wikipedia_link(raw_query: str) -> MetadataLink | None:
    parsed = urlparse(raw_query)
    match = re.fullmatch(
        r"([a-z][a-z0-9-]*)(?:\.m)?\.wikipedia\.org",
        str(parsed.hostname or "").casefold(),
    )
    if not match:
        return None
    path_match = re.fullmatch(r"/wiki/([^/?#]+)", parsed.path)
    if not path_match:
        return None
    title = unquote(path_match.group(1)).replace("_", " ").strip()
    if not title:
        return None
    return MetadataLink(
        provider="wikipedia",
        media_type="",
        entity_id=f"{match.group(1)}:{title}",
        scope="work",
        url=raw_query,
    )


def _tmdb_link(raw_query: str) -> MetadataLink | None:
    if _supported_provider(raw_query) != "tmdb":
        return None
    parsed = urlparse(raw_query)
    match = re.fullmatch(
        r"/(movie|tv)/(\d+)(?:-[^/?#]+)?/?",
        parsed.path,
        re.IGNORECASE,
    )
    if not match:
        return None
    kind, entity_id = match.groups()
    return MetadataLink(
        provider="tmdb",
        media_type="series" if kind.casefold() == "tv" else "movie",
        entity_id=entity_id,
        scope="work",
        url=raw_query,
    )


def _wikidata_link(raw_query: str) -> MetadataLink | None:
    if _supported_provider(raw_query) != "wikidata":
        return None
    parsed = urlparse(raw_query)
    match = re.fullmatch(r"/wiki/(Q\d+)/?", parsed.path, re.IGNORECASE)
    if not match:
        return None
    return MetadataLink(
        provider="wikidata",
        media_type="",
        entity_id=match.group(1).upper(),
        scope="work",
        url=raw_query,
    )


def _anilist_link(raw_query: str) -> MetadataLink | None:
    if _supported_provider(raw_query) != "anilist":
        return None
    parsed = urlparse(raw_query)
    match = re.fullmatch(
        r"/anime/(\d+)(?:/[^/?#]+)?/?",
        parsed.path,
        re.IGNORECASE,
    )
    if not match:
        return None
    return MetadataLink(
        provider="anilist",
        media_type="",
        entity_id=match.group(1),
        scope="work",
        url=raw_query,
    )


def metadata_link_from_url(raw_url: str) -> MetadataLink | None:
    return (
        _douban_link(raw_url)
        or _tvdb_link(raw_url)
        or _wikipedia_link(raw_url)
        or _wikidata_link(raw_url)
        or _tmdb_link(raw_url)
        or _anilist_link(raw_url)
    )


def _resolvable_link(raw_url: str) -> MetadataLink | None:
    provider = _supported_provider(raw_url)
    if not provider:
        return None
    return MetadataLink(
        provider=provider,
        media_type="",
        entity_id="",
        scope="work",
        url=raw_url,
    )


def classify_search_input(raw_query: str) -> ParsedInput:
    collapsed_query = " ".join(str(raw_query or "").split())
    urls = extract_message_urls(raw_query)
    links = tuple(
        link
        for url in urls
        if (link := metadata_link_from_url(url)) is not None
    )
    unresolved = tuple(
        link
        for url in urls
        if metadata_link_from_url(url) is None
        and (link := _resolvable_link(url)) is not None
    )
    stable_identities = {
        (link.provider, link.entity_id)
        for link in links
    }
    unresolved_urls = {link.url for link in unresolved}
    if (
        len(stable_identities) > 1
        or (stable_identities and unresolved_urls)
        or len(unresolved_urls) > 1
    ):
        return ParsedInput(
            kind="invalid_link",
            raw_query=collapsed_query,
            urls=urls,
            fallback_title=_fallback_title(raw_query, urls),
            reason="multiple_metadata_entities",
        )
    if links:
        link = links[0]
        return ParsedInput(
            kind="link",
            raw_query=collapsed_query,
            media_type=link.media_type,
            scope=link.scope,
            link=link,
            urls=urls,
            fallback_title=_fallback_title(raw_query, urls),
        )
    if unresolved:
        link = unresolved[0]
        return ParsedInput(
            kind="resolvable_link",
            raw_query=collapsed_query,
            link=link,
            urls=urls,
            fallback_title=_fallback_title(raw_query, urls),
        )
    if has_unsupported_range_syntax(collapsed_query):
        return ParsedInput(
            kind="unsupported_text",
            raw_query=collapsed_query,
            reason="unsupported_scope_syntax",
        )
    if _NATURAL_LANGUAGE_REQUEST.search(collapsed_query):
        return ParsedInput(
            kind="unsupported_text",
            raw_query=collapsed_query,
            reason="natural_language_not_supported",
        )

    leading_numeric_match = _LEADING_QUOTED_NUMERIC_TITLE.match(
        collapsed_query
    )
    leading_numeric_title = (
        (
            leading_numeric_match.group("ascii")
            or leading_numeric_match.group("curly")
        )
        if leading_numeric_match
        else ""
    )
    explicit_numeric_title = quoted_numeric_title(collapsed_query)
    raw_query = (
        explicit_numeric_title
        or normalize_query_text(collapsed_query)
    )
    intent = parse_search_intent(
        collapsed_query if explicit_numeric_title else raw_query
    )
    scope = str(intent.get("scope") or "movie_or_series")
    if scope == "movie_or_series":
        scope = "work"
    title = str(intent.get("title") or "").strip()
    media_type = ""
    media_type_matches = list(_MEDIA_TYPE_TOKEN.finditer(title))
    if media_type_matches:
        token = media_type_matches[-1].group(1).casefold()
        media_type = (
            "movie"
            if token in {"电影", "電影", "movie", "film"}
            else "series"
        )
        title = _MEDIA_TYPE_TOKEN.sub(" ", title)
    if scope in {"whole_series", "season", "episode"}:
        media_type = "series"
    year = str(intent.get("year") or "").strip()
    if leading_numeric_title:
        remainder = collapsed_query[leading_numeric_match.end():]
        release_year = re.search(
            r"(?<!\d)((?:19|20)\d{2})(?!\d)",
            remainder,
        )
        if release_year:
            year = release_year.group(1)
            title = leading_numeric_title
    if year:
        title = re.sub(
            rf"(?<!\d){re.escape(year)}(?!\d)",
            " ",
            title,
        )
    title = " ".join(title.split())
    normalized_raw_query = " ".join(
        item for item in (title, year) if item
    )
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
        raw_query=normalized_raw_query,
        title=title,
        year=year,
        media_type=media_type,
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

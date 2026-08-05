"""Resolve supported metadata links into one request-scoped anchor."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from .adapters.douban import (
    DoubanSubjectLookupError,
    lookup_douban_subject,
)
from .adapters.tvdb import (
    TvdbAuthenticationError,
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_episode,
    get_tvdb_movie,
    get_tvdb_season,
    get_tvdb_series,
)
from .adapters.wikipedia import (
    WikipediaPageLookupError,
    lookup_wikipedia_page,
)
from .input_contract import (
    MetadataLink,
    ParsedInput,
    metadata_link_from_url,
)
from .prowlarr_query import build_prowlarr_query
from .search_query import parse_media_page_title


class DirectLinkError(ValueError):
    def __init__(self, code: str, details=()):
        self.code = str(code or "direct_link_invalid")
        self.details = tuple(str(item) for item in details or ())
        super().__init__(self.code)


@dataclass(frozen=True)
class DirectEntity:
    provider: str
    evidence: dict
    stable_identity: tuple[str, str]
    title: str
    year: str
    media_type: str
    scope: str
    season_number: int | None = None
    episode_number: int | None = None

    @property
    def query(self) -> str:
        return build_prowlarr_query(
            self.title,
            self.scope,
            self.season_number,
            self.episode_number,
        )


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_DIRECT_LINK_HOSTS = (
    "douban.com",
    "wikipedia.org",
    "w.wiki",
    "thetvdb.com",
    "tvdb.com",
)


class _CanonicalLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical_urls = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() not in {"link", "meta"}:
            return
        values = {
            str(key).casefold(): str(value or "")
            for key, value in attrs
        }
        if (
            tag.casefold() == "link"
            and "canonical" in values.get("rel", "").casefold().split()
            and values.get("href")
        ):
            self.canonical_urls.append(values["href"])
        if (
            tag.casefold() == "meta"
            and values.get("property", "").casefold() == "og:url"
            and values.get("content")
        ):
            self.canonical_urls.append(values["content"])


def _allowed_direct_url(raw_url: str) -> bool:
    parsed = urlparse(str(raw_url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or port:
        return False
    host = str(parsed.hostname or "").casefold()
    return any(
        host == root or host.endswith(f".{root}")
        for root in _DIRECT_LINK_HOSTS
    )


def _read_shared_link(
    raw_url: str,
    *,
    timeout: int,
    max_redirects: int,
):
    current_url = str(raw_url or "").strip()
    redirects = 0
    while True:
        if not _allowed_direct_url(current_url):
            raise DirectLinkError("direct_link_redirect_rejected")
        try:
            response = requests.get(
                current_url,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"request_error:{type(exc).__name__}",),
            ) from exc
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in _REDIRECT_STATUS_CODES:
            location = str(
                getattr(response, "headers", {}).get("Location") or ""
            ).strip()
            if not location:
                raise DirectLinkError(
                    "fixed_link_read_failed",
                    ("redirect_missing",),
                )
            if redirects >= max_redirects:
                raise DirectLinkError(
                    "fixed_link_read_failed",
                    ("redirect_limit",),
                )
            current_url = urljoin(current_url, location)
            redirects += 1
            continue
        if not 200 <= status_code < 300:
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"http_status:{status_code}",),
            )
        final_url = str(getattr(response, "url", "") or current_url)
        if not _allowed_direct_url(final_url):
            raise DirectLinkError("direct_link_redirect_rejected")
        return response, final_url


def resolve_shared_metadata_link(
    parsed: ParsedInput,
    *,
    timeout: int = 10,
    max_redirects: int = 3,
) -> tuple[MetadataLink | None, str]:
    if parsed.kind == "link" and parsed.link is not None:
        return parsed.link, parsed.fallback_title
    if parsed.kind != "resolvable_link" or parsed.link is None:
        raise DirectLinkError(parsed.reason or "direct_link_invalid")

    response, final_url = _read_shared_link(
        parsed.link.url,
        timeout=timeout,
        max_redirects=max_redirects,
    )
    html_text = str(getattr(response, "text", "") or "")
    candidates = [final_url]
    parser = _CanonicalLinkParser()
    try:
        parser.feed(html_text)
    except (TypeError, ValueError):
        pass
    candidates.extend(
        urljoin(final_url, candidate)
        for candidate in parser.canonical_urls
    )
    for candidate in candidates:
        if not _allowed_direct_url(candidate):
            continue
        link = metadata_link_from_url(candidate)
        if link is not None:
            return link, parsed.fallback_title
    return None, (
        parsed.fallback_title
        or parse_media_page_title(html_text)
    )


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tvdb_series_entity(link: MetadataLink):
    if link.scope == "work":
        return get_tvdb_series(link.entity_id), None, None
    if link.scope == "season":
        season = get_tvdb_season(link.entity_id)
        if not isinstance(season, dict):
            return None, None, None
        return (
            get_tvdb_series(_text(season.get("tvdb_series_id"))),
            _integer(season.get("season_number")),
            None,
        )
    episode = get_tvdb_episode(link.entity_id)
    if not isinstance(episode, dict):
        return None, None, None
    return (
        get_tvdb_series(_text(episode.get("tvdb_series_id"))),
        _integer(episode.get("season_number")),
        _integer(episode.get("episode_number")),
    )


def resolve_direct_link(link: MetadataLink) -> DirectEntity:
    if link.provider == "wikipedia":
        language, separator, title_hint = link.entity_id.partition(":")
        if not separator:
            raise DirectLinkError("direct_link_invalid")
        try:
            fact = lookup_wikipedia_page(language, title_hint)
        except WikipediaPageLookupError as exc:
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"wikipedia:{exc.code}",),
            ) from exc
        if not isinstance(fact, dict):
            raise DirectLinkError("direct_link_not_found")
        stable_id = _text(fact.get("wikibase_item"))
        title = _text(
            fact.get("official_english_title")
            or fact.get("title")
            or fact.get("chinese_title")
        )
        media_type = _text(fact.get("media_type"))
        if (
            not stable_id
            or not title
            or media_type not in {"movie", "series"}
        ):
            raise DirectLinkError("direct_link_invalid")
        return DirectEntity(
            provider="wikipedia",
            evidence={
                "source": "wikipedia",
                "status": "ok",
                "facts": [fact],
                "source_urls": [fact.get("url") or link.url],
                "error": "",
            },
            stable_identity=("wikipedia", stable_id),
            title=title,
            year=_text(fact.get("year")),
            media_type=media_type,
            scope="work",
        )
    if link.provider == "douban":
        try:
            fact = lookup_douban_subject(link.entity_id)
        except DoubanSubjectLookupError as exc:
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"douban:{exc.code}",),
            ) from exc
        if not isinstance(fact, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(
            fact.get("english_title")
            or fact.get("title")
            or fact.get("chinese_title")
        )
        if not title:
            raise DirectLinkError("direct_link_invalid")
        media_type = _text(fact.get("media_type"))
        if media_type not in {"movie", "series"}:
            raise DirectLinkError("direct_link_invalid")
        return DirectEntity(
            provider="douban",
            evidence={
                "source": "douban",
                "status": "ok",
                "facts": [fact],
                "source_urls": [fact.get("url") or link.url],
                "error": "",
            },
            stable_identity=("douban_subject", link.entity_id),
            title=title,
            year=_text(fact.get("year")),
            media_type=media_type,
            scope="work",
        )
    if link.provider != "tvdb":
        raise DirectLinkError("direct_link_provider_unsupported")

    try:
        if link.media_type == "movie":
            movie = get_tvdb_movie(link.entity_id)
            if not isinstance(movie, dict):
                raise DirectLinkError("direct_link_not_found")
            movie = dict(movie)
            movie.setdefault("url", link.url)
            title = _text(movie.get("english_title") or movie.get("name"))
            entity_id = _text(
                movie.get("tvdb_movie_id") or movie.get("tvdb_id")
            )
            fact = {
                "movies": [movie],
                "series": [],
                "episodes_by_series": {},
            }
            media_type = "movie"
            season_number = episode_number = None
        else:
            series, season_number, episode_number = _tvdb_series_entity(
                link
            )
            if not isinstance(series, dict):
                raise DirectLinkError("direct_link_not_found")
            series = dict(series)
            series.setdefault("url", link.url)
            if (
                link.scope in {"season", "episode"}
                and season_number == 0
            ):
                raise DirectLinkError("unsupported_special_scope")
            title = _text(
                series.get("english_title") or series.get("name")
            )
            entity_id = _text(
                series.get("tvdb_series_id") or series.get("tvdb_id")
            )
            fact = {
                "movies": [],
                "series": [series],
                "episodes_by_series": {
                    entity_id: list(series.get("episodes") or [])
                },
            }
            media_type = "series"
    except DirectLinkError:
        raise
    except (
        TvdbAuthenticationError,
        TvdbConfigError,
        TvdbRequestError,
        OSError,
    ) as exc:
        detail = str(
            getattr(exc, "code", "")
            or (
                "server_down"
                if isinstance(exc, OSError)
                else "unavailable"
            )
        )
        raise DirectLinkError(
            "fixed_link_read_failed",
            (f"tvdb:{detail}",),
        ) from exc
    if not title or not entity_id:
        raise DirectLinkError("direct_link_invalid")
    return DirectEntity(
        provider="tvdb",
        evidence={
            "source": "tvdb",
            "status": "ok",
            "facts": [fact],
            "source_urls": [link.url],
            "error": "",
        },
        stable_identity=("tvdb", entity_id),
        title=title,
        year=_text(
            (movie if media_type == "movie" else series).get("year")
        ),
        media_type=media_type,
        scope=link.scope,
        season_number=season_number,
        episode_number=episode_number,
    )

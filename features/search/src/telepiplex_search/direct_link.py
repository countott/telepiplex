"""Resolve supported metadata links into one request-scoped anchor."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse

import requests

from .adapters.douban import (
    DoubanSubjectLookupError,
    clean_douban_series_title,
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
    lookup_wikipedia_episode_page,
    lookup_wikipedia_page,
)
from .wikipedia_episode_inventory import merge_wikipedia_episode_results
from .adapters.wikidata import enrich_wikidata_entities
from .adapters.tmdb import (
    TmdbAuthenticationError,
    TmdbConfigError,
    TmdbRequestError,
    get_tmdb_entity,
)
from .adapters.anilist import (
    AniListConfigError,
    AniListRequestError,
    get_anilist_media,
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
    search_title: str = ""

    @property
    def query(self) -> str:
        return build_prowlarr_query(
            self.search_title or self.title,
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
    "themoviedb.org",
    "anilist.co",
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


def _canonical_episode_list_link(values) -> dict | None:
    links = []
    seen = set()
    for raw in values or ():
        if not isinstance(raw, dict):
            continue
        title = _text(raw.get("title"))
        href = _text(raw.get("href"))
        if not title or not href or (title, href) in seen:
            continue
        seen.add((title, href))
        links.append({"title": title, "href": href})
    if not links:
        return None
    overview_links = [
        link for link in links
        if not re.search(
            r"(?:\(|_)\s*(?:season|series)[ _-]*\d+",
            f"{link['title']} {link['href']}",
            re.IGNORECASE,
        )
    ]
    if len(overview_links) == 1:
        return overview_links[0]
    return links[0] if len(links) == 1 else None


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
        if fact.get("is_disambiguation") is True:
            raise DirectLinkError(
                "wikipedia_disambiguation",
                (_text(fact.get("title") or title_hint),),
            )
        stable_id = _text(fact.get("wikibase_item"))
        if stable_id:
            try:
                structural = enrich_wikidata_entities([stable_id]).get(
                    stable_id
                )
            except Exception:
                structural = None
            if isinstance(structural, dict):
                fact = {
                    **fact,
                    **{
                        key: value
                        for key, value in structural.items()
                        if value not in (None, "", [], {})
                    },
                    "url": fact.get("url") or link.url,
                    "cover_url": fact.get("cover_url") or "",
                }
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
        if media_type == "series":
            primary_title = _text(
                fact.get("canonical_title") or title_hint
            )
            primary_inventory = lookup_wikipedia_episode_page(
                language,
                primary_title,
            )
            primary_inventory["wikibase_item"] = stable_id
            episode_list_relationship = None
            primary_inventory_merged = False
            episode_list_links = [
                value
                for value in primary_inventory.get("episode_list_links") or ()
                if isinstance(value, dict) and _text(value.get("title"))
            ]
            episode_list_link = _canonical_episode_list_link(
                episode_list_links
            )
            if (
                _text(primary_inventory.get("status")) != "complete"
                and episode_list_link is not None
            ):
                linked_title = _text(episode_list_link["title"])
                linked_inventory = lookup_wikipedia_episode_page(
                    language,
                    linked_title,
                )
                linked_inventory["wikibase_item"] = stable_id
                if linked_inventory.get("items"):
                    primary_inventory = merge_wikipedia_episode_results(
                        primary_inventory,
                        linked_inventory,
                        expected_qid=stable_id,
                    )
                    primary_inventory_merged = True
                    episode_list_relationship = {
                        "from_title": primary_title,
                        "to_title": linked_title,
                        "href": _text(episode_list_link.get("href")),
                        "verification": "wikipedia_explicit_link",
                    }
            secondary_inventory = None
            english_page_title = _text(fact.get("english_page_title"))
            if (
                _text(primary_inventory.get("status")) != "complete"
                and not language.casefold().startswith("en")
                and english_page_title
            ):
                try:
                    english_fact = lookup_wikipedia_page(
                        "en",
                        english_page_title,
                    )
                except WikipediaPageLookupError as exc:
                    secondary_inventory = {
                        "status": exc.code,
                        "items": [],
                        "season_totals": {},
                        "source_language": "en",
                        "revision_id": 0,
                        "error": f"wikipedia_{exc.code}",
                        "wikibase_item": stable_id,
                    }
                else:
                    english_qid = _text(
                        (english_fact or {}).get("wikibase_item")
                    )
                    if english_qid != stable_id:
                        secondary_inventory = {
                            "status": "conflict",
                            "items": [],
                            "season_totals": {},
                            "source_language": "en",
                            "revision_id": 0,
                            "error": "wikipedia_fact_conflict",
                            "wikibase_item": english_qid,
                        }
                    else:
                        secondary_inventory = lookup_wikipedia_episode_page(
                            "en",
                            _text(
                                english_fact.get("canonical_title")
                                or english_page_title
                            ),
                        )
                        secondary_inventory["wikibase_item"] = english_qid
            episode_inventory = (
                primary_inventory
                if primary_inventory_merged and secondary_inventory is None
                else merge_wikipedia_episode_results(
                    primary_inventory,
                    secondary_inventory,
                    expected_qid=stable_id,
                )
            )
            if episode_list_relationship is not None:
                episode_inventory["episode_list_relationship"] = (
                    episode_list_relationship
                )
            season_totals = episode_inventory.get("season_totals") or {}
            inventory_status = _text(episode_inventory.get("status"))
            fact["episodes"] = [
                {
                    **item,
                    "season_total": season_totals.get(
                        item.get("season_number")
                    ),
                    "inventory_status": inventory_status,
                }
                for item in episode_inventory.get("items") or ()
            ]
            fact["wikipedia_episode_inventory"] = episode_inventory
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
    if link.provider == "wikidata":
        try:
            fact = enrich_wikidata_entities([link.entity_id]).get(
                link.entity_id
            )
        except Exception as exc:
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"wikidata:{type(exc).__name__}",),
            ) from exc
        if not isinstance(fact, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(
            fact.get("english_title")
            or fact.get("chinese_title")
        )
        media_type = _text(fact.get("media_type"))
        if not title or media_type not in {"movie", "series"}:
            raise DirectLinkError("direct_link_invalid")
        fact = {
            **fact,
            "url": link.url,
            "title": fact.get("chinese_title") or title,
            "official_english_title": fact.get("english_title") or "",
        }
        return DirectEntity(
            provider="wikidata",
            evidence={
                "source": "wikidata",
                "status": "ok",
                "facts": [fact],
                "source_urls": [link.url],
                "error": "",
            },
            stable_identity=("wikidata", link.entity_id),
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
        media_type = _text(fact.get("media_type"))
        if media_type not in {"movie", "series"}:
            raise DirectLinkError("direct_link_invalid")
        try:
            season_number = int(fact.get("season_number") or 0)
        except (TypeError, ValueError):
            season_number = 0
        if season_number <= 0:
            season_number = 0
        chinese_title, chinese_season = clean_douban_series_title(
            fact.get("chinese_title")
            or fact.get("title")
            or fact.get("douban_title_raw"),
            media_type,
        )
        english_title, english_season = clean_douban_series_title(
            fact.get("english_title") or fact.get("original_title"),
            media_type,
        )
        season_number = season_number or chinese_season or english_season or 0
        display_title = chinese_title or english_title
        search_title = english_title or display_title
        if not display_title:
            raise DirectLinkError("direct_link_invalid")
        normalized_fact = dict(fact)
        normalized_fact["chinese_title"] = chinese_title
        normalized_fact["title"] = display_title
        if english_title:
            normalized_fact["english_title"] = english_title
            normalized_fact["official_english_title"] = english_title
        if season_number:
            normalized_fact["season_number"] = season_number
        root_lookup_year = "" if season_number else _text(fact.get("year"))
        if season_number:
            normalized_fact["season_entity_year"] = _text(fact.get("year"))
            normalized_fact["year"] = ""
        return DirectEntity(
            provider="douban",
            evidence={
                "source": "douban",
                "status": "ok",
                "facts": [normalized_fact],
                "source_urls": [fact.get("url") or link.url],
                "error": "",
                "root_lookup_year": root_lookup_year,
            },
            stable_identity=("douban_subject", link.entity_id),
            title=display_title,
            year=root_lookup_year,
            media_type=media_type,
            scope="season" if season_number else "work",
            season_number=season_number or None,
            search_title=search_title,
        )
    if link.provider == "tmdb":
        try:
            fact = get_tmdb_entity(link.media_type, link.entity_id)
        except (
            TmdbAuthenticationError,
            TmdbConfigError,
            TmdbRequestError,
            OSError,
        ) as exc:
            detail = str(getattr(exc, "code", "") or "server_down")
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"tmdb:{detail}",),
            ) from exc
        if not isinstance(fact, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(
            fact.get("official_english_title")
            or fact.get("title")
            or fact.get("original_title")
        )
        entity_id = _text(
            fact.get("tmdb_id")
            or (fact.get("external_ids") or {}).get("tmdb")
        )
        media_type = _text(fact.get("media_type"))
        if not title or not entity_id or media_type not in {"movie", "series"}:
            raise DirectLinkError("direct_link_invalid")
        return DirectEntity(
            provider="tmdb",
            evidence={
                "source": "tmdb",
                "status": "ok",
                "facts": [fact],
                "source_urls": [fact.get("url") or link.url],
                "error": "",
            },
            stable_identity=("tmdb", entity_id),
            title=title,
            year=_text(fact.get("year")),
            media_type=media_type,
            scope="work",
        )
    if link.provider == "anilist":
        try:
            fact = get_anilist_media(link.entity_id)
        except (AniListConfigError, AniListRequestError, OSError) as exc:
            detail = str(getattr(exc, "code", "") or "server_down")
            raise DirectLinkError(
                "fixed_link_read_failed",
                (f"anilist:{detail}",),
            ) from exc
        if not isinstance(fact, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(
            fact.get("romanized_original_title")
            or fact.get("official_english_title")
            or fact.get("title")
        )
        entity_id = _text(
            fact.get("anilist_id")
            or (fact.get("external_ids") or {}).get("anilist")
        )
        media_type = _text(fact.get("media_type"))
        if not title or not entity_id or media_type not in {"movie", "series"}:
            raise DirectLinkError("direct_link_invalid")
        return DirectEntity(
            provider="anilist",
            evidence={
                "source": "anilist",
                "status": "ok",
                "facts": [fact],
                "source_urls": [fact.get("url") or link.url],
                "error": "",
            },
            stable_identity=("anilist", entity_id),
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

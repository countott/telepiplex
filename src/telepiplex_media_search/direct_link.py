"""Resolve supported metadata links into one request-scoped anchor."""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.douban import lookup_douban_subject
from .adapters.tvdb import (
    get_tvdb_episode,
    get_tvdb_movie,
    get_tvdb_season,
    get_tvdb_series,
)
from .input_contract import MetadataLink


class DirectLinkError(ValueError):
    pass


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
        base = " ".join(value for value in (self.title, self.year) if value)
        if self.scope == "season" and self.season_number is not None:
            return f"{base} S{self.season_number:02d}"
        if (
            self.scope == "episode"
            and self.season_number is not None
            and self.episode_number is not None
        ):
            return (
                f"{base} S{self.season_number:02d}"
                f"E{self.episode_number:02d}"
            )
        return base


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
    if link.provider == "douban":
        fact = lookup_douban_subject(link.entity_id)
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

    if link.media_type == "movie":
        movie = get_tvdb_movie(link.entity_id)
        if not isinstance(movie, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(movie.get("english_title") or movie.get("name"))
        entity_id = _text(movie.get("tvdb_movie_id") or movie.get("tvdb_id"))
        fact = {"movies": [movie], "series": [], "episodes_by_series": {}}
        media_type = "movie"
        season_number = episode_number = None
    else:
        series, season_number, episode_number = _tvdb_series_entity(link)
        if not isinstance(series, dict):
            raise DirectLinkError("direct_link_not_found")
        title = _text(series.get("english_title") or series.get("name"))
        entity_id = _text(series.get("tvdb_series_id") or series.get("tvdb_id"))
        fact = {
            "movies": [],
            "series": [series],
            "episodes_by_series": {
                entity_id: list(series.get("episodes") or [])
            },
        }
        media_type = "series"
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

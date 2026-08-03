"""Build the only supported Prowlarr query shapes."""

from __future__ import annotations

import re


_BASE_SCOPES = {"movie", "movie_or_series", "whole_series", "work"}
_DISAMBIGUATION_SUFFIX = re.compile(
    r"\s*[\(（]\s*"
    r"(?:(?:19|20)\d{2}\s*年?\s*)?"
    r"(?:film|movie|television\s+series|tv\s+series|"
    r"电影|電影|影片|电视剧|電視劇|剧集|劇集)"
    r"\s*[\)）]\s*$",
    re.IGNORECASE,
)


def _clean_title(value: str) -> str:
    title = _DISAMBIGUATION_SUFFIX.sub("", str(value or ""))
    title = re.sub(r"[^\w]+", " ", title, flags=re.UNICODE)
    title = title.replace("_", " ")
    return " ".join(title.split())


def build_prowlarr_query(
    title: str,
    scope: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> str:
    """Return one canonical, deliberately loose Prowlarr query."""

    title = _clean_title(title)
    if not title:
        raise ValueError("canonical_title_missing")
    scope = str(scope or "")
    if scope in _BASE_SCOPES:
        return title
    if scope == "season" and season_number is not None:
        season = int(season_number)
        if season <= 0:
            raise ValueError("bounded_scope_incomplete")
        return f"{title} S{season:02d}"
    if (
        scope == "episode"
        and season_number is not None
        and episode_number is not None
    ):
        season = int(season_number)
        episode = int(episode_number)
        if season <= 0 or episode <= 0:
            raise ValueError("bounded_scope_incomplete")
        width = 2 if episode < 100 else 3
        return f"{title} S{season:02d}E{episode:0{width}d}"
    raise ValueError("bounded_scope_incomplete")


def build_prowlarr_query_chain(
    media_metadata: dict,
    raw_query: str,
) -> list[str]:
    """Build one canonical query only from verified metadata v1."""

    del raw_query
    if not isinstance(media_metadata, dict):
        raise ValueError("media_metadata_missing")
    identity = media_metadata.get("identity")
    retrieval = media_metadata.get("retrieval")
    evidence = media_metadata.get("evidence")
    if not all(isinstance(value, dict) for value in (
        identity,
        retrieval,
        evidence,
    )):
        raise ValueError("media_metadata_incomplete")
    decision = evidence.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("media_metadata_incomplete")

    scope = str(retrieval.get("scope") or "work")
    media_type = str(retrieval.get("media_type") or "")
    if media_type == "movie":
        scope = "movie"
    season_number = decision.get("season_number")
    episode_number = decision.get("episode_number")
    if scope in {"season", "episode"} and season_number is None:
        raise ValueError("bounded_scope_incomplete")
    if scope == "episode" and episode_number is None:
        raise ValueError("bounded_scope_incomplete")

    title = str(
        identity.get("canonical_search_title")
        or identity.get("official_english_title")
        or identity.get("english_title")
        or ""
    ).strip()
    if not title:
        raise ValueError("query_chain_empty")
    if scope == "movie":
        year = str(identity.get("year") or "")[:4]
        if not re.fullmatch(r"(?:19|20)\d{2}", year):
            raise ValueError("movie_year_missing")
        title = f"{title} {year}"
    query = build_prowlarr_query(
        title,
        scope,
        season_number=season_number,
        episode_number=episode_number,
    )
    return [query]

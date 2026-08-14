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
    title = re.sub(r"[^\w%]+", " ", title, flags=re.UNICODE)
    title = title.replace("_", " ")
    return " ".join(title.split())


def _strip_season_title(value: str) -> tuple[str, bool]:
    original = str(value or "")
    title = re.sub(
        r"(?i)\s+season[ ._-]*\d{1,2}\s*$",
        "",
        original,
    ).strip()
    title = re.sub(r"(?i)\s+S\d{1,2}\s*$", "", title).strip()
    return title, title != original.strip()


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
    """Build at most three queries only from verified metadata v1 titles."""

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

    raw_titles = identity.get("query_titles")
    if not isinstance(raw_titles, list):
        raw_titles = []
    titles = [
        *raw_titles,
        identity.get("canonical_search_title"),
        identity.get("official_english_title"),
        identity.get("english_title"),
    ]
    original_language = str(
        identity.get("original_language") or ""
    ).strip().casefold()
    foreign_work = bool(
        original_language
        and original_language not in {
            "zh", "zh-cn", "zh-hans", "cmn", "yue",
        }
    )
    if foreign_work:
        latin_titles = [
            title for title in titles
            if re.search(r"[A-Za-z]", str(title or ""))
        ]
        if not latin_titles:
            raise ValueError("foreign_search_title_missing")
        titles = latin_titles
    if not any(str(title or "").strip() for title in titles):
        raise ValueError("query_chain_empty")
    year = str(identity.get("year") or "")[:4]
    if scope == "movie" and not re.fullmatch(r"(?:19|20)\d{2}", year):
        raise ValueError("movie_year_missing")

    queries = []
    season_bases = []
    for raw_title in titles:
        title = str(raw_title or "").strip()
        if not title:
            continue
        if scope == "season":
            title, _stripped = _strip_season_title(title)
            if title and title not in season_bases:
                season_bases.append(title)
        if scope == "movie":
            title = f"{title} {year}"
        query = build_prowlarr_query(
            title,
            scope,
            season_number=season_number,
            episode_number=episode_number,
        )
        if query not in queries:
            queries.append(query)
        if len(queries) == 3:
            break
    if scope == "season" and len(queries) < 3:
        for title in season_bases:
            textual = build_prowlarr_query(
                f"{title} Season {int(season_number):02d}",
                "work",
            )
            if textual not in queries:
                queries.append(textual)
            if len(queries) == 3:
                break
    if not queries:
        raise ValueError("query_chain_empty")
    return queries

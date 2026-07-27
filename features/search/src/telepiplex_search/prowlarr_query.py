"""Build the only supported Prowlarr query shapes."""

from __future__ import annotations

import re


_BASE_SCOPES = {"movie", "movie_or_series", "whole_series", "work"}
_LATIN = re.compile(r"[A-Za-z]")
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


def _unique_titles(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        value = " ".join(str(value or "").replace("\xa0", " ").split())
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _raw_query_title(value: str) -> str:
    value = " ".join(str(value or "").replace("\xa0", " ").split())
    value = re.sub(r"(?i)\bS\d{1,2}(?:E\d{1,3})?\b", " ", value)
    value = re.sub(
        r"(?i)\bseason\s*\d+(?:\s*(?:episode|ep)\s*\d+)?\b",
        " ",
        value,
    )
    value = re.sub(
        r"第?\s*[零〇一二两三四五六七八九十百两\d]+\s*季"
        r"(?:\s*第?\s*[零〇一二两三四五六七八九十百两\d]+\s*[集话話])?",
        " ",
        value,
    )
    value = re.sub(
        r"全集|全季|整季|整剧|整劇|全剧|全劇",
        " ",
        value,
    )
    return " ".join(value.split())


def build_prowlarr_query_chain(
    media_metadata: dict,
    raw_query: str,
) -> list[str]:
    """Build ordered, deduplicated queries only from verified metadata v1."""

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

    aliases = identity.get("aliases")
    aliases = list(aliases) if isinstance(aliases, list) else []
    romanized = str(identity.get("romanized_original_title") or "")
    official_english = str(identity.get("official_english_title") or "")
    original = str(identity.get("original_title") or "")
    original_language = str(identity.get("original_language") or "").casefold()
    content_kind = str(identity.get("content_kind") or "")
    category = str(
        (media_metadata.get("placement") or {}).get("category_kind") or ""
    )
    japanese_animation = bool(
        original_language == "ja"
        and (
            content_kind in {"movie", "series"}
            and category.startswith("animated_")
        )
    )

    ordered_titles = []
    if japanese_animation:
        ordered_titles.append(romanized)
    ordered_titles.append(official_english)
    ordered_titles.extend(
        alias for alias in aliases if _LATIN.search(str(alias or ""))
    )
    ordered_titles.extend((original, _raw_query_title(raw_query)))

    queries = []
    seen = set()
    for title in _unique_titles(ordered_titles):
        query = build_prowlarr_query(
            title,
            scope,
            season_number=season_number,
            episode_number=episode_number,
        )
        key = query.casefold()
        if key not in seen:
            queries.append(query)
            seen.add(key)
    if not queries:
        raise ValueError("query_chain_empty")
    return queries

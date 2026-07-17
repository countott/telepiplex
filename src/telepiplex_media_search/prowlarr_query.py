"""Build the only supported Prowlarr query shapes."""

from __future__ import annotations

import re


_BASE_SCOPES = {"movie", "movie_or_series", "whole_series", "work"}


def _clean_title(value: str) -> str:
    title = re.sub(r"[^\w]+", " ", str(value or ""), flags=re.UNICODE)
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

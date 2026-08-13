"""Deterministic regular-season catalog with provider priority."""

from __future__ import annotations


def _integer(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result


def _regular_inventory(values) -> dict[int, list[int]]:
    result: dict[int, set[int]] = {}
    for raw in values or ():
        if not isinstance(raw, dict):
            continue
        season = _integer(raw.get("season_number"))
        episode = _integer(raw.get("episode_number"))
        if season is None or season < 1 or episode is None or episode < 1:
            continue
        result.setdefault(season, set()).add(episode)
    return {
        season: sorted(episodes)
        for season, episodes in sorted(result.items())
    }


def build_scope_catalog(
    *,
    tvdb_items,
    tmdb_items,
    wikipedia_season_count,
) -> dict:
    for source, values in (("tvdb", tvdb_items), ("tmdb", tmdb_items)):
        episodes = _regular_inventory(values)
        if episodes:
            return {
                "source": source,
                "seasons": list(episodes),
                "episodes_by_season": episodes,
                "season_complete": True,
                "episode_complete": True,
            }
    count = _integer(wikipedia_season_count)
    if count is not None and count > 0:
        return {
            "source": "wikipedia",
            "seasons": list(range(1, count + 1)),
            "episodes_by_season": {},
            "season_complete": True,
            "episode_complete": False,
        }
    return {
        "source": "",
        "seasons": [],
        "episodes_by_season": {},
        "season_complete": False,
        "episode_complete": False,
    }

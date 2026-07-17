"""TVDB-backed series range choices for a selected canonical work."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date

from .prowlarr_query import build_prowlarr_query


class SeriesScopeError(ValueError):
    pass


@dataclass(frozen=True)
class SeriesInventory:
    seasons: tuple[int, ...]
    aired_by_season: dict[int, tuple[int, ...]]
    all_by_season: dict[int, tuple[int, ...]]


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aired(value, today: date) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    try:
        return date.fromisoformat(raw[:10]) <= today
    except ValueError:
        return False


def series_inventory(contract: dict, *, today: date | None = None) -> SeriesInventory:
    today = today or date.today()
    all_by_season: dict[int, set[int]] = {}
    aired_by_season: dict[int, set[int]] = {}
    for item in contract.get("items") or []:
        if not isinstance(item, dict):
            continue
        season = _integer(item.get("season_number"))
        episode = _integer(item.get("episode_number"))
        if season is None or season < 1 or episode is None or episode < 1:
            continue
        all_by_season.setdefault(season, set()).add(episode)
        if _aired(item.get("aired"), today):
            aired_by_season.setdefault(season, set()).add(episode)
    seasons = tuple(sorted(all_by_season))
    return SeriesInventory(
        seasons=seasons,
        aired_by_season={
            key: tuple(sorted(values)) for key, values in aired_by_season.items()
        },
        all_by_season={
            key: tuple(sorted(values)) for key, values in all_by_season.items()
        },
    )


def series_scope_options(contract: dict) -> tuple[str, ...]:
    decision = ((contract.get("evidence") or {}).get("decision") or {})
    scope = str(decision.get("scope") or "movie_or_series")
    if scope == "episode":
        return ()
    if scope == "season":
        return ("season_all", "season_episode")
    if scope == "whole_series":
        return ()
    inventory = series_inventory(contract)
    if len(inventory.seasons) <= 1:
        return ("whole_series", "episode")
    return ("whole_series", "season", "episode")


def apply_series_scope(
    contract: dict,
    choice: str,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    result = deepcopy(contract)
    inventory = series_inventory(result, today=today)
    english = " ".join(
        str((result.get("identity") or {}).get("english_title") or "").split()
    )
    if not english:
        raise SeriesScopeError("english_title_missing")
    choice = str(choice or "")
    if choice == "whole_series":
        query = build_prowlarr_query(english, "whole_series")
        selected = [
            item
            for item in result.get("items") or []
            if _aired(item.get("aired"), today)
        ]
        scope = "whole_series"
        season_number = None
        episode_number = None
    elif choice in {"season", "episode"}:
        season_number = _integer(season_number)
        if season_number not in inventory.seasons:
            raise SeriesScopeError("season_not_found")
        if choice == "season":
            aired = inventory.aired_by_season.get(season_number, ())
            if not aired:
                raise SeriesScopeError("season_not_aired")
            query = build_prowlarr_query(
                english,
                "season",
                season_number=season_number,
            )
            selected = [
                item
                for item in result.get("items") or []
                if _integer(item.get("season_number")) == season_number
                and _aired(item.get("aired"), today)
            ]
            scope = "season"
            episode_number = None
        else:
            episode_number = _integer(episode_number)
            if episode_number not in inventory.all_by_season.get(season_number, ()):
                raise SeriesScopeError("episode_not_found")
            if episode_number not in inventory.aired_by_season.get(season_number, ()):
                raise SeriesScopeError("episode_not_aired")
            query = build_prowlarr_query(
                english,
                "episode",
                season_number=season_number,
                episode_number=episode_number,
            )
            selected = [
                item
                for item in result.get("items") or []
                if _integer(item.get("season_number")) == season_number
                and _integer(item.get("episode_number")) == episode_number
            ]
            scope = "episode"
    else:
        raise SeriesScopeError("invalid_scope_choice")

    result["items"] = selected
    result["retrieval"] = {
        "media_type": "series",
        "scope": scope,
        "query": query,
    }
    evidence = result.setdefault("evidence", {})
    decision = evidence.setdefault("decision", {})
    decision.update({
        "scope": scope,
        "season_number": season_number,
        "episode_number": episode_number,
    })
    return result

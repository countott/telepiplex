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
    scheduled_by_season: dict[int, tuple[int, ...]]
    unknown_by_season: dict[int, tuple[int, ...]]
    season_totals: dict[int, int]
    state_by_season: dict[int, str]


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _airing_state(value, today: date) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    try:
        parsed = date.fromisoformat(raw[:10])
    except ValueError:
        return "unknown"
    return "aired" if parsed <= today else "scheduled"


def _aired(value, today: date) -> bool:
    return _airing_state(value, today) == "aired"


def _item_airing_state(item: dict, today: date) -> str:
    if item.get("air_date_conflict") is not True:
        return _airing_state(
            item.get("aired") or item.get("air_date"),
            today,
        )
    states = {
        _airing_state(value, today)
        for value in item.get("air_date_candidates") or ()
    }
    if states == {"aired"}:
        return "aired"
    if states == {"scheduled"}:
        return "scheduled"
    return "unknown"


def _season_totals(contract: dict) -> dict[int, int]:
    totals = {}
    evidence = contract.get("evidence") or {}
    inventory = evidence.get("series_inventory") or {}
    for season, total in (inventory.get("season_totals") or {}).items():
        season_number = _integer(season)
        episode_total = _integer(total)
        if season_number and episode_total and episode_total > 0:
            totals[season_number] = episode_total
    for item in contract.get("items") or ():
        if not isinstance(item, dict):
            continue
        season_number = _integer(item.get("season_number"))
        episode_total = _integer(item.get("season_total"))
        if season_number and episode_total and episode_total > 0:
            totals.setdefault(season_number, episode_total)
    return totals


def series_inventory(contract: dict, *, today: date | None = None) -> SeriesInventory:
    today = today or date.today()
    all_by_season: dict[int, set[int]] = {}
    aired_by_season: dict[int, set[int]] = {}
    scheduled_by_season: dict[int, set[int]] = {}
    unknown_by_season: dict[int, set[int]] = {}
    season_totals = _season_totals(contract)
    for item in contract.get("items") or []:
        if not isinstance(item, dict):
            continue
        season = _integer(item.get("season_number"))
        episode = _integer(item.get("episode_number"))
        if season is None or season < 1 or episode is None or episode < 1:
            continue
        all_by_season.setdefault(season, set()).add(episode)
        airing_state = _item_airing_state(item, today)
        if airing_state == "aired":
            aired_by_season.setdefault(season, set()).add(episode)
        elif airing_state == "scheduled":
            scheduled_by_season.setdefault(season, set()).add(episode)
        else:
            unknown_by_season.setdefault(season, set()).add(episode)
    seasons = tuple(sorted(set(all_by_season) | set(season_totals)))
    state_by_season = {}
    for season in seasons:
        all_episodes = all_by_season.get(season, set())
        aired_episodes = aired_by_season.get(season, set())
        scheduled_episodes = scheduled_by_season.get(season, set())
        unknown_episodes = unknown_by_season.get(season, set())
        total = season_totals.get(season)
        if unknown_episodes:
            state = "unknown"
        elif total is None:
            state = "unknown"
        elif (
            scheduled_episodes
            or len(aired_episodes) < total
            or all_episodes != set(range(1, total + 1))
        ):
            state = "incomplete"
        else:
            state = "completed"
        state_by_season[season] = state
    return SeriesInventory(
        seasons=seasons,
        aired_by_season={
            key: tuple(sorted(values)) for key, values in aired_by_season.items()
        },
        all_by_season={
            key: tuple(sorted(values)) for key, values in all_by_season.items()
        },
        scheduled_by_season={
            key: tuple(sorted(values))
            for key, values in scheduled_by_season.items()
        },
        unknown_by_season={
            key: tuple(sorted(values))
            for key, values in unknown_by_season.items()
        },
        season_totals=dict(sorted(season_totals.items())),
        state_by_season=state_by_season,
    )


def series_seasons(contract: dict) -> tuple[int, ...]:
    inventory = series_inventory(contract)
    if inventory.seasons:
        return inventory.seasons
    try:
        count = int((contract.get("identity") or {}).get("season_count"))
    except (TypeError, ValueError):
        return ()
    return tuple(range(1, count + 1)) if count > 0 else ()


def series_scope_options(contract: dict) -> tuple[str, ...]:
    decision = ((contract.get("evidence") or {}).get("decision") or {})
    scope = str(decision.get("scope") or "movie_or_series")
    if scope == "episode":
        return ()
    if scope == "season":
        season = _integer(decision.get("season_number"))
        inventory = series_inventory(contract)
        if inventory.state_by_season.get(season) == "completed":
            return ("season_all", "season_episode")
        return ("season_episode",)
    if scope == "whole_series":
        return ()
    inventory = series_inventory(contract)
    seasons = series_seasons(contract)
    if not inventory.seasons:
        return ("season",) if seasons else ()
    all_completed = bool(seasons) and all(
        inventory.state_by_season.get(season) == "completed"
        for season in seasons
    )
    if all_completed:
        if len(seasons) <= 1:
            return ("whole_series",)
        return ("whole_series", "season", "episode")
    if len(seasons) <= 1:
        return ("episode",) if seasons else ()
    return ("season", "episode")


def apply_inventory_probe_scope(contract: dict, probe: dict) -> dict:
    """Select files already observed by rename without air-date filtering."""

    result = deepcopy(contract)
    items = [
        item
        for item in result.get("items") or []
        if isinstance(item, dict)
    ]
    by_coordinate: dict[tuple[int, int], list[dict]] = {}
    for item in items:
        season = _integer(item.get("season_number"))
        episode = _integer(item.get("episode_number"))
        if season and season > 0 and episode and episode > 0:
            by_coordinate.setdefault((season, episode), []).append(item)

    observed_coordinates = set()
    unscoped_episodes = set()
    for item in (probe or {}).get("observed_episodes") or []:
        if not isinstance(item, dict):
            continue
        season = _integer(item.get("season_number"))
        episode = _integer(item.get("episode_number"))
        if not episode or episode < 1:
            continue
        if season and season > 0:
            observed_coordinates.add((season, episode))
        else:
            unscoped_episodes.add(episode)

    observed_seasons = set()
    for value in (probe or {}).get("observed_seasons") or []:
        season = _integer(
            value.get("season_number") if isinstance(value, dict) else value
        )
        if season and season > 0:
            observed_seasons.add(season)
    observed_seasons.update(season for season, _episode in observed_coordinates)

    if unscoped_episodes:
        matching_seasons = [
            season
            for season, episodes in series_inventory(result).all_by_season.items()
            if unscoped_episodes.issubset(set(episodes))
        ]
        if len(matching_seasons) != 1:
            raise SeriesScopeError("scope_unresolved")
        observed_coordinates.update(
            (matching_seasons[0], episode)
            for episode in unscoped_episodes
        )
        observed_seasons.add(matching_seasons[0])

    if observed_coordinates:
        matched_coordinates = sorted(
            coordinate
            for coordinate in observed_coordinates
            if len(by_coordinate.get(coordinate, ())) == 1
        )
        unresolved = []
        for season, episode in sorted(observed_coordinates):
            candidates = by_coordinate.get((season, episode), ())
            if len(candidates) == 1:
                continue
            unresolved.append({
                "season_number": season,
                "episode_number": episode,
                "reason_code": (
                    "canonical_coordinate_unavailable"
                    if not candidates
                    else "canonical_coordinate_non_unique"
                ),
            })
        if not matched_coordinates:
            raise SeriesScopeError("probe_inventory_mismatch missing=all")
        selected = [
            by_coordinate[coordinate][0]
            for coordinate in matched_coordinates
        ]
    elif observed_seasons:
        known_seasons = {
            season
            for season, _episode in by_coordinate
        }
        missing_seasons = sorted(observed_seasons - known_seasons)
        if missing_seasons:
            formatted = ",".join(f"S{season:02d}" for season in missing_seasons)
            raise SeriesScopeError(
                f"probe_inventory_mismatch missing={formatted}"
            )
        selected = [
            item
            for item in items
            if _integer(item.get("season_number")) in observed_seasons
        ]
    else:
        raise SeriesScopeError("scope_unresolved")

    if not selected:
        raise SeriesScopeError("probe_inventory_mismatch missing=all")

    selected_seasons = sorted({
        _integer(item.get("season_number"))
        for item in selected
        if _integer(item.get("season_number")) is not None
    })
    shape = str((probe or {}).get("content_shape") or "").casefold()
    if len(selected) == 1 and shape in {
        "single_episode",
        "single_episode_unscoped",
    }:
        scope = "episode"
        season_number = _integer(selected[0].get("season_number"))
        episode_number = _integer(selected[0].get("episode_number"))
    elif len(selected_seasons) == 1:
        scope = "season"
        season_number = selected_seasons[0]
        episode_number = None
    else:
        scope = "whole_series"
        season_number = None
        episode_number = None

    identity = result.get("identity") or {}
    search_title = " ".join(
        str(
            identity.get("english_title")
            or identity.get("chinese_title")
            or ""
        ).split()
    )
    if not search_title:
        raise SeriesScopeError("search_title_missing")
    result["items"] = selected
    result["retrieval"] = {
        "media_type": "series",
        "scope": scope,
        "query": build_prowlarr_query(
            search_title,
            scope,
            season_number=season_number,
            episode_number=episode_number,
        ),
    }
    decision = result.setdefault("evidence", {}).setdefault("decision", {})
    decision.update({
        "scope": scope,
        "season_number": season_number,
        "episode_number": episode_number,
        "scope_source": "file_probe",
    })
    if observed_coordinates:
        reconciliation = {
            "status": "partial" if unresolved else "complete",
            "observed_count": len(observed_coordinates),
            "matched_count": len(selected),
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }
        result["evidence"]["inventory_reconciliation"] = reconciliation
        if unresolved:
            warnings = result.setdefault("warnings", [])
            warning = "warning:inventory_partial_match"
            if warning not in warnings:
                warnings.append(warning)
    return result


def apply_series_scope(
    contract: dict,
    choice: str,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
    today: date | None = None,
    allow_incomplete_aggregate: bool = False,
) -> dict:
    today = today or date.today()
    result = deepcopy(contract)
    inventory = series_inventory(result, today=today)
    english = " ".join(
        str((result.get("identity") or {}).get("english_title") or "").split()
    )
    search_title = english or " ".join(
        str((result.get("identity") or {}).get("chinese_title") or "").split()
    )
    if not search_title:
        raise SeriesScopeError("search_title_missing")
    choice = str(choice or "")
    if choice == "whole_series":
        if not allow_incomplete_aggregate and inventory.seasons and any(
            inventory.state_by_season.get(season) != "completed"
            for season in inventory.seasons
        ):
            raise SeriesScopeError("series_incomplete")
        query = build_prowlarr_query(search_title, "whole_series")
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
        known_seasons = series_seasons(result)
        if season_number not in known_seasons:
            raise SeriesScopeError("season_not_found")
        if choice == "season":
            if (
                not allow_incomplete_aggregate
                and inventory.state_by_season.get(season_number) != "completed"
            ):
                raise SeriesScopeError("season_incomplete")
            aired = inventory.aired_by_season.get(season_number, ())
            if inventory.seasons and not aired:
                raise SeriesScopeError("season_not_aired")
            query = build_prowlarr_query(
                search_title,
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
                search_title,
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

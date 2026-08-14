"""Select one complete episode order without intersecting provider schemes."""

from __future__ import annotations

from dataclasses import dataclass


class ProviderOrderConflict(ValueError):
    def __init__(self, reason: str = "provider_order_conflict"):
        self.code = "provider_order_conflict"
        self.reason = str(reason or self.code)
        super().__init__(self.code)


@dataclass(frozen=True)
class SeriesTopology:
    provider: str
    items: tuple[dict, ...]
    season_totals: dict[int, int]
    diagnostics: dict


def _positive(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _normalize(items) -> tuple[dict, ...]:
    by_coordinate = {}
    for raw in items or ():
        if not isinstance(raw, dict):
            continue
        season = _positive(raw.get("season_number"))
        episode = _positive(raw.get("episode_number"))
        if season is None or episode is None:
            continue
        value = dict(raw)
        value["season_number"] = season
        value["episode_number"] = episode
        by_coordinate.setdefault((season, episode), value)
    return tuple(by_coordinate[key] for key in sorted(by_coordinate))


def _coordinates(items) -> set[tuple[int, int]]:
    return {
        (item["season_number"], item["episode_number"])
        for item in items
    }


def _totals(items) -> dict[int, int]:
    result = {}
    for item in items:
        season = item["season_number"]
        result[season] = max(result.get(season, 0), item["episode_number"])
    return result


def _air_date(item: dict) -> str:
    return str(item.get("aired") or item.get("air_date") or "").strip()


def _merge_matching(
    profiles: dict[str, tuple[dict, ...]],
) -> SeriesTopology:
    providers = list(profiles)
    coordinates = sorted(_coordinates(profiles[providers[0]]))
    indexes = {
        provider: {
            (item["season_number"], item["episode_number"]): item
            for item in items
        }
        for provider, items in profiles.items()
    }
    merged = []
    for coordinate in coordinates:
        value = {}
        dates = set()
        for provider in providers:
            item = indexes[provider][coordinate]
            value.update(item)
            if date := _air_date(item):
                dates.add(date)
        value["season_number"], value["episode_number"] = coordinate
        if len(dates) == 1:
            value["aired"] = next(iter(dates))
        elif len(dates) > 1:
            value["aired"] = ""
            value["air_date_conflict"] = True
        merged.append(value)
    provider = "_".join(providers)
    return SeriesTopology(
        provider=provider,
        items=tuple(merged),
        season_totals=_totals(merged),
        diagnostics={
            "status": "merged",
            "selected_provider": provider,
            "profile_counts": {
                key: len(value) for key, value in profiles.items()
            },
        },
    )


def select_series_topology(
    raw_profiles: dict[str, tuple[dict, ...] | list[dict]],
    *,
    trusted_episode_count: int | None = None,
    trusted_season_count: int | None = None,
    requested_season_number: int | None = None,
) -> SeriesTopology:
    """Choose a complete provider profile or expose an explicit conflict."""

    profiles = {
        str(provider): normalized
        for provider, items in (raw_profiles or {}).items()
        if (normalized := _normalize(items))
    }
    if not profiles:
        raise ProviderOrderConflict("no_provider_profile")
    if len(profiles) == 1:
        provider, items = next(iter(profiles.items()))
        return SeriesTopology(
            provider=provider,
            items=items,
            season_totals=_totals(items),
            diagnostics={
                "status": "single_profile",
                "selected_provider": provider,
                "profile_counts": {provider: len(items)},
            },
        )

    coordinate_sets = {
        provider: _coordinates(items)
        for provider, items in profiles.items()
    }
    if len({frozenset(value) for value in coordinate_sets.values()}) == 1:
        return _merge_matching(profiles)

    expected_episodes = _positive(trusted_episode_count)
    expected_seasons = _positive(trusted_season_count)
    requested_season = _positive(requested_season_number)
    if not any((expected_episodes, expected_seasons, requested_season)):
        raise ProviderOrderConflict("unscored_divergent_profiles")

    scored = []
    for provider, items in profiles.items():
        totals = _totals(items)
        score = 0
        reasons = []
        if expected_episodes:
            distance = abs(len(items) - expected_episodes)
            score -= distance * 10
            reasons.append(f"episode_distance:{distance}")
            if distance == 0:
                score += 10_000
        if expected_seasons:
            distance = abs(len(totals) - expected_seasons)
            score -= distance * 100
            reasons.append(f"season_distance:{distance}")
            if distance == 0:
                score += 1_000
        if requested_season:
            if requested_season in totals:
                score += 500
                reasons.append("requested_season_present")
            else:
                score -= 10_000
                reasons.append("requested_season_missing")
        scored.append((score, provider, items, totals, reasons))
    scored.sort(key=lambda value: (-value[0], value[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        raise ProviderOrderConflict("tied_divergent_profiles")
    score, provider, items, totals, reasons = scored[0]
    return SeriesTopology(
        provider=provider,
        items=items,
        season_totals=totals,
        diagnostics={
            "status": "profile_selected",
            "selected_provider": provider,
            "selected_score": score,
            "score_reasons": reasons,
            "profile_counts": {
                key: len(value) for key, value in profiles.items()
            },
        },
    )

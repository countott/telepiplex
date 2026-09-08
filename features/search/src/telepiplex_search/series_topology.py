"""Select the first usable episode order and fill only missing values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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


PROVIDER_PRIORITY = ("wikipedia", "tvdb", "tmdb")
_MISSING_VALUES = frozenset({"", "n/a", "na", "none", "null", "unknown"})
_EPISODE_ID_KEYS = ("tvdb_episode_id", "tmdb_episode_id")


def _available(value) -> bool:
    return str(value or "").strip().casefold() not in _MISSING_VALUES


def episode_air_date(item: dict) -> str:
    """Read a usable date, including provider aliases; N/A is not a date."""

    for key in ("aired", "firstAired", "air_date"):
        raw = str(item.get(key) or "").strip()
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            continue
    return ""


def _episode_ids(item: dict) -> dict[str, str]:
    return {
        key: str(item[key]).strip()
        for key in _EPISODE_ID_KEYS
        if _available(item.get(key))
    }


def _matching_episode(
    item: dict, indexed: dict, by_id: dict, primary_by_id: dict, aligned: bool,
):
    ids = _episode_ids(item)
    matches = {
        coordinate
        for identity in ids.items()
        if len(primary_by_id.get(identity, ())) == 1
        for coordinate in by_id.get(identity, ())
    }
    if len(matches) > 1:
        return None
    coordinate = (item["season_number"], item["episode_number"])
    if matches:
        candidate = indexed[next(iter(matches))]
    elif aligned:
        candidate = indexed[coordinate]
    else:
        return None
    candidate_ids = _episode_ids(candidate)
    if any(ids[key] != candidate_ids[key] for key in ids.keys() & candidate_ids.keys()):
        return None
    return candidate


def select_series_topology(
    raw_profiles: dict[str, tuple[dict, ...] | list[dict]],
    *,
    trusted_episode_count: int | None = None,
    trusted_season_count: int | None = None,
    requested_season_number: int | None = None,
) -> SeriesTopology:
    """Use Wikipedia, then TVDB, then TMDB only when inventory is absent.

    Counts and requested scope cannot overrule an available source. Lower
    profiles can fill missing dates/IDs when their complete coordinate sets
    agree or stable episode IDs identify the same episode in another order.
    """

    del trusted_episode_count, trusted_season_count, requested_season_number
    profiles = {
        provider: normalized
        for provider in PROVIDER_PRIORITY
        if (normalized := _normalize((raw_profiles or {}).get(provider)))
    }
    if not profiles:
        raise ProviderOrderConflict("no_provider_profile")
    provider = next(iter(profiles))
    coordinates = _coordinates(profiles[provider])
    indexes = {
        name: {
            (item["season_number"], item["episode_number"]): item
            for item in items
        }
        for name, items in profiles.items()
    }
    aligned = {name for name, values in profiles.items() if _coordinates(values) == coordinates}
    id_indexes = {}
    for name, indexed in indexes.items():
        by_id = {}
        for coordinate, source_item in indexed.items():
            for identity in _episode_ids(source_item).items():
                by_id.setdefault(identity, set()).add(coordinate)
        id_indexes[name] = by_id
    items = []
    date_fallback_count = 0
    date_difference_count = 0
    for raw in profiles[provider]:
        item = dict(raw)
        coordinate = (item["season_number"], item["episode_number"])
        matched = {provider: raw}
        for name, indexed in indexes.items():
            if name == provider:
                continue
            candidate = _matching_episode(
                raw, indexed, id_indexes[name], id_indexes[provider], name in aligned,
            )
            if candidate is not None:
                matched[name] = candidate
        available_dates = {
            name: aired
            for name, candidate in matched.items()
            if (aired := episode_air_date(candidate))
        }
        date_source = next(iter(available_dates), "")
        item["aired"] = available_dates.get(date_source, "")
        item["air_date_source"] = date_source
        item["inventory_source"] = provider
        date_fallback_count += bool(date_source and date_source != provider)
        date_difference_count += len(set(available_dates.values())) > 1
        for name, source_item in matched.items():
            keys = list(_EPISODE_ID_KEYS)
            if name in aligned and (
                source_item["season_number"], source_item["episode_number"]
            ) == coordinate:
                keys.append("season_total")
            for key in keys:
                if not _available(item.get(key)) and _available(source_item.get(key)):
                    item[key] = source_item[key]
        items.append(item)
    return SeriesTopology(
        provider=provider,
        items=tuple(items),
        season_totals=_totals(items),
        diagnostics={
            "status": "source_priority",
            "selected_provider": provider,
            "source_priority": list(PROVIDER_PRIORITY),
            "profile_counts": {
                name: len(values) for name, values in profiles.items()
            },
            "date_fallback_count": date_fallback_count,
            "date_difference_count": date_difference_count,
            "different_order_profiles": [
                name for name in profiles if name not in aligned
            ],
        },
    )

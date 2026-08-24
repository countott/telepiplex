"""Deterministic Prowlarr indexer wave planning."""

from __future__ import annotations

import math


def _positive_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _name(value) -> str:
    return str(value or "").strip().casefold()


def _positive_score(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        score = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(score) and score > 0


def plan_prowlarr_waves(
    indexers,
    *,
    explicit_ids,
    indexer_scores,
):
    """Partition enabled indexers without changing their relative order."""

    normalized = []
    seen = set()
    for raw in indexers or ():
        if not isinstance(raw, dict):
            continue
        indexer_id = _positive_id(raw.get("id"))
        if indexer_id is None or indexer_id in seen:
            continue
        seen.add(indexer_id)
        item = dict(raw)
        item["id"] = indexer_id
        item["name"] = str(raw.get("name") or indexer_id)
        normalized.append(item)

    preferred_ids = {
        indexer_id
        for indexer_id in (
            _positive_id(value) for value in explicit_ids or ()
        )
        if indexer_id is not None
    }
    chosen = {
        item["id"] for item in normalized if item["id"] in preferred_ids
    }

    if not chosen:
        positive_names = {
            _name(name)
            for name, score in (
                indexer_scores.items()
                if isinstance(indexer_scores, dict)
                else ()
            )
            if _name(name) and _positive_score(score)
        }
        chosen = {
            item["id"]
            for item in normalized
            if _name(item.get("name")) in positive_names
        }

    if not chosen:
        return tuple(normalized), ()
    return (
        tuple(item for item in normalized if item["id"] in chosen),
        tuple(item for item in normalized if item["id"] not in chosen),
    )


__all__ = ["plan_prowlarr_waves"]

from __future__ import annotations

from copy import deepcopy
from threading import Lock


TEMPORARY_MAPPING_KIND = "temporary_related_special"
VALID_LIBRARY_TYPES = {"movie", "series"}
VALID_CATEGORY_KINDS = {
    "live_action_movie",
    "animated_movie",
    "live_action_series",
    "animated_series",
}


def _text(value) -> str:
    return " ".join(str(value or "").split())


def validate_draft_download_plan(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None
    if not _text(value.get("plan_id")) or not _text(value.get("display_title")):
        return None

    placement = value.get("placement")
    if not isinstance(placement, dict):
        return None
    if placement.get("library_type") not in VALID_LIBRARY_TYPES:
        return None
    if placement.get("category_kind") not in VALID_CATEGORY_KINDS:
        return None

    queries = value.get("prowlarr_queries")
    if not isinstance(queries, list) or not any(_text(item) for item in queries):
        return None

    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        if placement.get("season_number") != 0 or placement.get("episode_number") is not None:
            return None
        source_entry = value.get("source_entry")
        if not isinstance(source_entry, dict):
            return None
        if not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None

    result = deepcopy(value)
    result["confirmed"] = False
    return result


class TemporarySpecialAllocator:
    def __init__(self):
        self._lock = Lock()
        self._reservations: dict[str, int] = {}

    def reserve(self, plan_id: str, occupied: set[int]) -> int:
        with self._lock:
            if plan_id in self._reservations:
                return self._reservations[plan_id]

            unavailable = set()
            for item in occupied:
                try:
                    episode_number = int(item)
                except (TypeError, ValueError):
                    continue
                if episode_number >= 100:
                    unavailable.add(episode_number)
            unavailable.update(self._reservations.values())

            candidate = 100
            while candidate in unavailable:
                candidate += 1
            self._reservations[plan_id] = candidate
            return candidate

    def release(self, plan_id: str) -> None:
        with self._lock:
            self._reservations.pop(plan_id, None)


def finalize_download_plan(
    draft: dict,
    allocator: TemporarySpecialAllocator,
    occupied: set[int],
) -> dict:
    validated = validate_draft_download_plan(draft)
    if validated is None:
        raise ValueError("invalid draft download plan")
    placement = validated["placement"]
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        placement["episode_number"] = allocator.reserve(validated["plan_id"], occupied)
    return validated


def confirm_download_plan(plan: dict) -> dict:
    confirmed = deepcopy(plan)
    confirmed["confirmed"] = True
    return confirmed


def attach_download_plan(metadata: dict | None, plan: dict) -> dict:
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    result["download_plan"] = deepcopy(plan)
    return result

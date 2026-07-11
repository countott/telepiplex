from __future__ import annotations

from copy import deepcopy
from threading import Lock

from app.core.media_metadata import series_scope_key, validate_media_metadata


TEMPORARY_MAPPING_KIND = "temporary_related_special"


def _text(value) -> str:
    return " ".join(str(value or "").split())


def validate_draft_search_plan(value: object):
    if not isinstance(value, dict) or not _text(value.get("plan_id")):
        return None
    contract = value.get("media_metadata")
    queries = value.get("prowlarr_queries")
    if not isinstance(contract, dict) or not isinstance(queries, list):
        return None
    normalized_queries = [_text(item) for item in queries if _text(item)]
    if not normalized_queries:
        return None
    placement = contract.get("placement")
    identity = contract.get("identity")
    relation = contract.get("relation")
    if not all(isinstance(item, dict) for item in (placement, identity, relation)):
        return None
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        source_entry = contract.get("source_entry")
        if placement.get("season_number") != 0 or placement.get("episode_number") is not None:
            return None
        if not isinstance(source_entry, dict) or not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None
        provider = _text(source_entry.get("provider"))
        evidence = contract.get("evidence")
        if not isinstance(evidence, dict):
            return None
        statuses = evidence.get("provider_statuses") or {}
        if not isinstance(statuses, dict):
            return None
        status = _text(statuses.get(provider))
        if status and status != "ok":
            if _text(source_entry.get("availability")) != status:
                return None
            if _text(source_entry.get("verification")) in {"", "verified"}:
                return None
            if not contract.get("warnings"):
                return None
    evidence = contract.get("evidence")
    if not isinstance(evidence, dict):
        return None
    official_hint = evidence.get("tvdb_official_special") or {}
    if not isinstance(official_hint, dict):
        return None
    if official_hint and placement.get("mapping_kind") != "tvdb_official":
        return None
    if official_hint:
        target = relation.get("target_series") or {}
        target_ids = target.get("external_ids") or {} if isinstance(target, dict) else {}
        if not isinstance(target_ids, dict):
            return None
        if _text(target_ids.get("tvdb")) != _text(official_hint.get("series_id")):
            return None
        if _text(placement.get("tvdb_episode_id")) != _text(official_hint.get("episode_id")):
            return None
        verified_key = (
            f"{_text(official_hint.get('series_id'))}:"
            f"{_text(official_hint.get('episode_id'))}"
        )
        verified_values = evidence.get("verified_tvdb_episode_keys") or []
        if not isinstance(verified_values, list):
            return None
        verified_keys = {_text(item) for item in verified_values}
        if verified_key not in verified_keys:
            return None
    result = deepcopy(value)
    result["prowlarr_queries"] = normalized_queries
    result["media_metadata"]["metadata_id"] = _text(result["plan_id"])
    result["media_metadata"]["confirmed"] = False
    return result


class TemporarySpecialAllocator:
    def __init__(self):
        self._lock = Lock()
        self._reservations: dict[str, tuple[str, int]] = {}

    def reserve(self, plan_id: str, scope_key: str, occupied: set[int]) -> int:
        with self._lock:
            if plan_id in self._reservations:
                reserved_scope, number = self._reservations[plan_id]
                if reserved_scope != scope_key:
                    raise ValueError("plan_id changed target series")
                return number
            unavailable = set()
            for item in occupied:
                try:
                    number = int(item)
                except (TypeError, ValueError):
                    continue
                if number >= 100:
                    unavailable.add(number)
            unavailable.update(
                number
                for reserved_scope, number in self._reservations.values()
                if reserved_scope == scope_key
            )
            candidate = 100
            while candidate in unavailable:
                candidate += 1
            self._reservations[plan_id] = (scope_key, candidate)
            return candidate

    def release(self, plan_id: str) -> None:
        with self._lock:
            self._reservations.pop(plan_id, None)


def finalize_search_plan(
    draft: dict,
    allocator: TemporarySpecialAllocator,
    occupied: set[int],
):
    plan = validate_draft_search_plan(draft)
    if plan is None:
        raise ValueError("invalid search plan")
    placement = plan["media_metadata"]["placement"]
    if placement.get("mapping_kind") == TEMPORARY_MAPPING_KIND:
        placement["episode_number"] = allocator.reserve(
            plan["plan_id"],
            series_scope_key(plan["media_metadata"]),
            occupied,
        )
    if validate_media_metadata(plan["media_metadata"], require_confirmed=False) is None:
        raise ValueError("invalid finalized media_metadata")
    return plan


def confirm_media_metadata(plan: dict) -> dict:
    contract = deepcopy((plan or {}).get("media_metadata"))
    if not isinstance(contract, dict):
        raise ValueError("search plan has no media_metadata")
    contract["confirmed"] = True
    validated = validate_media_metadata(contract, require_confirmed=True)
    if validated is None:
        raise ValueError("invalid confirmed media_metadata")
    return validated

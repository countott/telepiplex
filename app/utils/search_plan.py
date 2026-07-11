from __future__ import annotations

from copy import deepcopy
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

from app.core.media_metadata import series_scope_key, validate_media_metadata


TEMPORARY_MAPPING_KIND = "temporary_related_special"
KNOWN_EVIDENCE_PROVIDERS = {"wikipedia", "douban", "tvdb"}
SOFT_FAILURE_STATUSES = {"server_down", "not_found", "disabled"}


def _text(value) -> str:
    return " ".join(str(value or "").split())


def normalize_source_locator(value) -> str:
    text = _text(value)
    if not text:
        return ""
    parsed = urlsplit(text)
    if not parsed.scheme or not parsed.netloc:
        return text
    path = parsed.path
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        path,
        parsed.query,
        "",
    ))


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
        provider = _text(source_entry.get("provider")).casefold()
        if provider not in KNOWN_EVIDENCE_PROVIDERS:
            return None
        evidence = contract.get("evidence")
        if not isinstance(evidence, dict):
            return None
        statuses = evidence.get("provider_statuses") or {}
        if not isinstance(statuses, dict):
            return None
        status = _text(statuses.get(provider)).casefold()
        if not status:
            return None
        support_by_provider = evidence.get("provider_support") or {}
        if not isinstance(support_by_provider, dict):
            return None
        support = support_by_provider.get(provider)
        if not isinstance(support, dict):
            return None
        source_urls = support.get("source_urls")
        if not isinstance(source_urls, list):
            return None
        normalized_urls = {
            normalize_source_locator(item)
            for item in source_urls
            if normalize_source_locator(item)
        }
        locator = normalize_source_locator(
            source_entry.get("url") or source_entry.get("external_id")
        )
        if status == "ok":
            if support.get("has_facts") is not True and locator not in normalized_urls:
                return None
        elif status in SOFT_FAILURE_STATUSES:
            if _text(source_entry.get("availability")) != status:
                return None
            if _text(source_entry.get("verification")) != "ai_supplied_unverified":
                return None
            if not any(
                isinstance(warning, str) and _text(warning)
                for warning in contract.get("warnings") or []
            ):
                return None
        else:
            return None
    evidence = contract.get("evidence")
    if not isinstance(evidence, dict):
        return None
    if "tvdb_official_special" in evidence:
        return None
    verified_candidates = evidence.get("verified_tvdb_special_candidates") or []
    official_candidates = evidence.get("tvdb_official_special_candidates") or []
    if not isinstance(verified_candidates, list) or not isinstance(official_candidates, list):
        return None
    verified_keys = set()
    for candidate in verified_candidates:
        if not isinstance(candidate, dict):
            return None
        series_id = _text(candidate.get("series_id"))
        episode_id = _text(candidate.get("episode_id"))
        if (
            not series_id
            or not episode_id
            or candidate.get("season_number") != 0
        ):
            return None
        verified_keys.add((series_id, episode_id))
    official_keys = set()
    for candidate in official_candidates:
        if not isinstance(candidate, dict):
            return None
        series_id = _text(candidate.get("series_id"))
        episode_id = _text(candidate.get("episode_id"))
        if (
            not series_id
            or not episode_id
            or candidate.get("season_number") != 0
            or not _text(candidate.get("name"))
            or (series_id, episode_id) not in verified_keys
        ):
            return None
        official_keys.add((series_id, episode_id))
    if official_keys and placement.get("mapping_kind") != "tvdb_official":
        return None
    if placement.get("mapping_kind") == "tvdb_official":
        target = relation.get("target_series") or {}
        target_ids = target.get("external_ids") or {} if isinstance(target, dict) else {}
        if not isinstance(target_ids, dict):
            return None
        selected_key = (
            _text(target_ids.get("tvdb")),
            _text(placement.get("tvdb_episode_id")),
        )
        if selected_key not in official_keys:
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

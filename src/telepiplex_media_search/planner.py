"""AI and evidence based canonical search-plan builder."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from collections.abc import Callable

from .context import runtime_context

from .ai import (
    infer_media_metadata_draft_with_ai,
    infer_search_hypotheses_with_ai,
)
from .deterministic import build_rule_hypotheses, evaluate_deterministic_plan
from .search_plan import (
    TEMPORARY_MAPPING_KIND,
    TemporarySpecialAllocator,
    finalize_search_plan,
    normalize_source_locator,
)


class SearchPlanningError(RuntimeError):
    def __init__(self, code: str, reason_codes=()):
        self.code = str(code or "search_planning_failed")
        self.reason_codes = tuple(str(item) for item in reason_codes or ())
        super().__init__(self.code)


def _log_info(message: str):
    runtime_context.logger.info(message)


def _provider_failure(name: str, exc: Exception) -> dict:
    return {
        "source": name,
        "status": "server_down",
        "facts": [],
        "source_urls": [],
        "error": str(exc),
    }


async def collect_evidence(
    hypotheses: dict,
    providers: dict[str, Callable],
) -> list[dict]:
    names = list(providers)
    tasks = [asyncio.to_thread(providers[name], hypotheses) for name in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    evidence = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            evidence.append(_provider_failure(name, result))
        elif isinstance(result, dict):
            normalized = dict(result)
            normalized["source"] = str(name).strip().casefold()
            evidence.append(normalized)
        else:
            evidence.append(
                _provider_failure(name, RuntimeError("invalid provider response"))
            )
    for item in evidence:
        _log_info(
            "search_evidence "
            f"source={item.get('source')} status={item.get('status')} "
            f"facts={len(item.get('facts') or [])}"
        )
    return evidence


def _provider_status_and_support(
    sources: list[dict],
) -> tuple[dict[str, str], dict[str, dict]]:
    statuses = {}
    support = {}
    for item in sources:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("source") or "").strip().casefold()
        if not provider:
            continue
        statuses[provider] = str(item.get("status") or "invalid").strip().casefold()
        facts = item.get("facts")
        has_facts = isinstance(facts, list) and any(bool(fact) for fact in facts)
        raw_urls = item.get("source_urls")
        source_urls = []
        if isinstance(raw_urls, list):
            for raw_url in raw_urls:
                _append_source_url(source_urls, raw_url)
        stable_ids = []
        _collect_fact_support(provider, facts, source_urls, stable_ids)
        support[provider] = {
            "has_facts": has_facts,
            "source_urls": source_urls,
            "stable_ids": stable_ids,
        }
    return statuses, support


def _append_source_url(source_urls: list[str], value) -> None:
    normalized_url = normalize_source_locator(value)
    if normalized_url and normalized_url not in source_urls:
        source_urls.append(normalized_url)


def _append_stable_id(stable_ids: list[str], value) -> None:
    if value is None or isinstance(value, (bool, dict, list, tuple, set)):
        return
    stable_id = " ".join(str(value).split())
    if stable_id and stable_id not in stable_ids:
        stable_ids.append(stable_id)


def _is_provider_stable_id_key(provider: str, key: str) -> bool:
    if provider == "wikipedia":
        return key == "wikibase_item"
    if provider == "douban":
        return key in {
            "subject_id",
            "douban",
            "douban_id",
            "douban_subject",
            "douban_subject_id",
        }
    if provider == "tvdb":
        return key.startswith("tvdb_") and key.endswith("_id")
    return False


def _collect_fact_support(
    provider: str,
    value,
    source_urls: list[str],
    stable_ids: list[str],
) -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().casefold()
            if key in {"url", "original_url"}:
                _append_source_url(source_urls, nested)
            if _is_provider_stable_id_key(provider, key):
                _append_stable_id(stable_ids, nested)
            _collect_fact_support(provider, nested, source_urls, stable_ids)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_fact_support(provider, nested, source_urls, stable_ids)


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _verified_tvdb_special_candidates(sources: list[dict]) -> list[dict]:
    candidates = []
    seen = set()
    for source in sources:
        if not (
            isinstance(source, dict)
            and source.get("source") == "tvdb"
            and source.get("status") == "ok"
        ):
            continue
        for fact in source.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            episodes_by_series = fact.get("episodes_by_series") or {}
            if not isinstance(episodes_by_series, dict):
                continue
            for series_id, episodes in episodes_by_series.items():
                series_id = _text(series_id)
                if not series_id or not isinstance(episodes, list):
                    continue
                for episode in episodes or []:
                    if not isinstance(episode, dict):
                        continue
                    if _integer(episode.get("season_number")) != 0:
                        continue
                    episode_id = _text(
                        episode.get("tvdb_episode_id") or episode.get("id") or ""
                    )
                    key = (series_id, episode_id)
                    if not episode_id or key in seen:
                        continue
                    seen.add(key)
                    candidates.append({
                        "series_id": series_id,
                        "episode_id": episode_id,
                        "name": _text(
                            episode.get("name") or episode.get("title") or ""
                        ),
                        "season_number": 0,
                    })
    return candidates


def _normalized_media_title(value) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    normalized = "".join(character for character in normalized if character.isalnum())
    suffixes = (
        "themovie",
        "电影版",
        "劇場版",
        "剧场版",
        "movie",
        "电影",
    )
    changed = True
    while normalized and changed:
        changed = False
        for suffix in suffixes:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)]
                changed = True
                break
    return normalized


def _matching_tvdb_official_candidates(
    contract: dict,
    candidates: list[dict],
) -> list[dict]:
    relation = contract.get("relation") if isinstance(contract, dict) else None
    target = relation.get("target_series") if isinstance(relation, dict) else None
    target_ids = target.get("external_ids") if isinstance(target, dict) else None
    target_series_id = _text(
        target_ids.get("tvdb") if isinstance(target_ids, dict) else ""
    )
    identity = contract.get("identity") if isinstance(contract, dict) else None
    title_keys = {
        _normalized_media_title((identity or {}).get(field))
        for field in ("chinese_title", "english_title")
    }
    title_keys.discard("")
    if not target_series_id or not title_keys:
        return []
    return [
        candidate
        for candidate in candidates
        if candidate["series_id"] == target_series_id
        and _normalized_media_title(candidate.get("name")) in title_keys
    ]


def _merge_evidence_passes(
    first: list[dict],
    second: list[dict],
) -> list[dict]:
    merged = {}
    for item in [*(first or []), *(second or [])]:
        if not isinstance(item, dict):
            continue
        provider = _text(item.get("source")).casefold()
        if not provider:
            continue
        target = merged.setdefault(provider, {
            "source": provider,
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "error": "",
        })
        status = _text(item.get("status")).casefold() or "invalid"
        if status == "ok" or target["status"] != "ok":
            target["status"] = status
        seen_facts = {
            json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
            for fact in target["facts"]
        }
        for fact in item.get("facts") or []:
            key = json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen_facts:
                target["facts"].append(fact)
                seen_facts.add(key)
        for url in item.get("source_urls") or []:
            if url and url not in target["source_urls"]:
                target["source_urls"].append(url)
        error = _text(item.get("error"))
        if error and error not in target["error"]:
            target["error"] = "; ".join(
                value for value in (target["error"], error) if value
            )
    return list(merged.values())


def _finalize_draft(
    draft: dict,
    *,
    plan_id: str,
    sources: list[dict],
    decision: dict,
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    draft["plan_id"] = plan_id
    contract = (
        draft.get("media_metadata")
        if isinstance(draft.get("media_metadata"), dict)
        else {}
    )
    evidence = contract.get("evidence")
    if not isinstance(evidence, dict):
        raise SearchPlanningError("invalid_media_metadata")
    provider_statuses, provider_support = _provider_status_and_support(sources)
    evidence["provider_statuses"] = provider_statuses
    evidence["provider_support"] = provider_support
    evidence["decision"] = decision
    verified_specials = _verified_tvdb_special_candidates(sources)
    evidence.pop("tvdb_official_special", None)
    evidence["verified_tvdb_special_candidates"] = verified_specials
    evidence["tvdb_official_special_candidates"] = (
        _matching_tvdb_official_candidates(contract, verified_specials)
    )
    evidence["verified_tvdb_episode_keys"] = sorted(
        f"{candidate['series_id']}:{candidate['episode_id']}"
        for candidate in verified_specials
    )
    try:
        occupied = (
            set(occupied_loader(contract) or set())
            if (contract.get("placement") or {}).get("mapping_kind")
            == TEMPORARY_MAPPING_KIND
            else set()
        )
    except Exception as exc:
        raise SearchPlanningError("temporary_occupancy_unavailable") from exc
    try:
        return finalize_search_plan(draft, allocator, occupied)
    except ValueError as exc:
        raise SearchPlanningError("invalid_media_metadata") from exc


async def build_confirmable_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    rule_hypotheses = build_rule_hypotheses(raw_query)
    first_sources = await collect_evidence(rule_hypotheses, providers)
    deterministic = evaluate_deterministic_plan(
        plan_id, raw_query, first_sources
    )
    if deterministic.plan is not None:
        plan = _finalize_draft(
            deterministic.plan,
            plan_id=plan_id,
            sources=first_sources,
            decision=deterministic.decision,
            occupied_loader=occupied_loader,
            allocator=allocator,
        )
        placement = plan["media_metadata"]["placement"]
        _log_info(
            "search_plan status=ready decision=deterministic "
            f"metadata_id={plan_id} mapping_kind={placement.get('mapping_kind')} "
            f"query={(plan.get('prowlarr_queries') or [''])[0]}"
        )
        return plan

    ai_input = {
        "raw_query": raw_query,
        "intent": rule_hypotheses["intent"],
        "sources": first_sources,
        "gate_reason_codes": list(deterministic.reason_codes),
    }
    hypotheses = await asyncio.to_thread(infer_search_hypotheses_with_ai, ai_input)
    if not isinstance(hypotheses, dict):
        _log_info(f"ai_stage=hypothesis status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_unavailable_after_gate_failure")
    _log_info(f"ai_stage=hypothesis status=ok metadata_id={plan_id}")
    second_sources = await collect_evidence(hypotheses, providers)
    sources = _merge_evidence_passes(first_sources, second_sources)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "intent": rule_hypotheses["intent"],
        "gate_reason_codes": list(deterministic.reason_codes),
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_media_metadata_draft_with_ai, context)
    if not isinstance(draft, dict):
        _log_info(f"ai_stage=media_metadata status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_invalid_after_gate_failure")
    _log_info(f"ai_stage=media_metadata status=ok metadata_id={plan_id}")
    decision = dict(deterministic.decision)
    decision.update({
        "mode": "ai",
        "ai_required": True,
        "ai_stage_one_status": "ok",
        "ai_stage_two_status": "ok",
    })
    plan = _finalize_draft(
        draft,
        plan_id=plan_id,
        sources=sources,
        decision=decision,
        occupied_loader=occupied_loader,
        allocator=allocator,
    )
    placement = plan["media_metadata"]["placement"]
    _log_info(
        "search_plan status=ready "
        f"metadata_id={plan_id} mapping_kind={placement.get('mapping_kind')} "
        f"query={(plan.get('prowlarr_queries') or [''])[0]}"
    )
    return plan

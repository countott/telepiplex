"""AI and evidence based canonical search-plan builder."""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

from .context import runtime_context

from .ai import (
    infer_search_hypotheses_with_ai,
)
from .candidate_score import (
    MINIMUM_SCORE,
    SCORING_VERSION,
    apply_thresholds,
    combine_score,
    program_score,
)
from .deterministic import build_rule_hypotheses
from .entity_graph import CandidateEntity, build_search_graph, normalize_title
from .input_contract import classify_search_input, has_ambiguous_bare_number
from .search_plan import (
    TEMPORARY_MAPPING_KIND,
    TemporarySpecialAllocator,
    finalize_search_plan,
    normalize_source_locator,
)
from .title_policy import TitlePolicyError, resolve_title_policy


class SearchPlanningError(RuntimeError):
    def __init__(self, code: str, reason_codes=()):
        self.code = str(code or "search_planning_failed")
        self.reason_codes = tuple(str(item) for item in reason_codes or ())
        super().__init__(self.code)


MAX_DISPLAY_CANDIDATES = 7


def _log_info(message: str):
    if runtime_context.logger:
        runtime_context.logger.info(message)


class PlanningBudget:
    TOTAL = 90.0
    STAGES = {
        "base_evidence": 15.0,
        "intent_fallback": 20.0,
        "candidate_finalize": 25.0,
    }

    def __init__(
        self,
        *,
        clock=time.monotonic,
        total: float | None = None,
        stages: dict[str, float] | None = None,
    ):
        self.clock = clock
        self.started_at = clock()
        self.total = self.TOTAL if total is None else max(0.0, float(total))
        self.deadline = self.started_at + self.total
        self.stages = {**self.STAGES, **(stages or {})}

    def remaining_for(self, stage: str) -> float:
        stage_limit = self.stages[stage]
        return max(0.0, min(stage_limit, self.deadline - self.clock()))

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started_at)


async def _budgeted(stage: str, budget: PlanningBudget, awaitable):
    remaining = budget.remaining_for(stage)
    if remaining <= 0:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise SearchPlanningError("planning_timed_out", (stage,))
    try:
        async with asyncio.timeout(remaining):
            return await awaitable
    except TimeoutError as exc:
        raise SearchPlanningError("planning_timed_out", (stage,)) from exc


async def _optional_budgeted(
    stage: str,
    budget: PlanningBudget,
    awaitable,
    default,
):
    try:
        return await _budgeted(stage, budget, awaitable)
    except SearchPlanningError:
        if budget.deadline - budget.clock() <= 0:
            raise
        _log_info(f"search_stage status=timed_out stage={stage}")
        return default


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
                    episode_number = _integer(
                        episode.get("episode_number")
                        or episode.get("number")
                    )
                    key = (series_id, episode_id)
                    if (
                        not episode_id
                        or episode_number is None
                        or episode_number < 1
                        or key in seen
                    ):
                        continue
                    seen.add(key)
                    candidates.append({
                        "series_id": series_id,
                        "episode_id": episode_id,
                        "name": _text(
                            episode.get("name") or episode.get("title") or ""
                        ),
                        "season_number": 0,
                        "episode_number": episode_number,
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


def _candidate_context(candidate: CandidateEntity) -> dict:
    return {
        "candidate_key": candidate.candidate_key,
        "fact_ids": [fact.fact_id for fact in candidate.facts],
        "facts": [{
            "fact_id": fact.fact_id,
            "provider": fact.provider,
            "titles": list(fact.titles),
            "year": fact.year,
            "media_type": fact.media_type,
            "external_ids": dict(fact.external_ids),
            "original_language": fact.original_language,
            "complex_signals": list(fact.complex_signals),
        } for fact in candidate.facts],
    }


def _relation_pool_entry(candidate: CandidateEntity) -> dict | None:
    try:
        titles = resolve_title_policy(candidate)
    except TitlePolicyError:
        return None
    year = next(iter(sorted(candidate.years)), "")
    identity = {
        **titles.identity_fields(),
        "year": year,
        "external_ids": dict(candidate.external_ids),
    }
    if not identity.get("chinese_title"):
        identity["chinese_title"] = (
            identity.get("original_title")
            or identity.get("english_title")
            or ""
        )
    return {
        **_candidate_context(candidate),
        "media_type": next(iter(sorted(candidate.media_types)), ""),
        "identity": identity,
    }


def _verify_relation_hypotheses(
    payload: dict,
    candidates: list[CandidateEntity],
) -> dict[str, dict]:
    by_key = {candidate.candidate_key: candidate for candidate in candidates}
    valid_types = {
        "prequel", "sequel", "spin_off", "special", "extension_movie",
    }
    verified = {}
    for hypothesis in (payload or {}).get("hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        source = by_key.get(_text(hypothesis.get("candidate_key")))
        target = by_key.get(_text(hypothesis.get("target_candidate_key")))
        relation_type = _text(hypothesis.get("relation_type")).casefold()
        fact_ids = hypothesis.get("fact_ids")
        if (
            source is None
            or target is None
            or source is target
            or source.media_types != frozenset({"movie"})
            or target.media_types != frozenset({"series"})
            or relation_type not in valid_types
            or not isinstance(fact_ids, list)
            or not fact_ids
        ):
            continue
        known = {fact.fact_id for fact in (*source.facts, *target.facts)}
        source_relation_facts = {
            fact.fact_id for fact in source.facts if fact.complex_signals
        }
        if not set(fact_ids).issubset(known) or not set(fact_ids).intersection(source_relation_facts):
            continue
        verified[source.candidate_key] = {
            "relation_type": relation_type,
            "target_candidate_key": target.candidate_key,
            "fact_ids": tuple(dict.fromkeys(fact_ids)),
            "verification": "source_relation_signal_and_target_entity",
        }
    return verified


def _explicit_media_type(raw_query: str, intent: dict) -> str:
    if intent.get("scope") in {"whole_series", "season", "episode"}:
        return "series"
    lowered = _text(raw_query).casefold()
    if re.search(r"电影|電影|movie|film", lowered):
        return "movie"
    if re.search(r"电视剧|電視劇|剧集|劇集|series|tv\s*show", lowered):
        return "series"
    return ""


def _candidate_poster_source(candidate: CandidateEntity) -> str:
    poster = candidate.poster_url
    return next(
        (fact.provider for fact in candidate.facts if fact.poster_url == poster),
        "",
    )


def _candidate_items(candidate: CandidateEntity, intent: dict) -> list[dict]:
    if candidate.media_types != frozenset({"series"}):
        return []
    scope = intent.get("scope") or "movie_or_series"
    items = []
    seen = set()
    for fact in candidate.facts:
        for episode in fact.episodes:
            key = (_integer(episode.get("season_number")), _integer(episode.get("episode_number")))
            if None in key or key[0] < 0 or key[1] < 1 or key in seen:
                continue
            if scope == "season" and key[0] != _integer(intent.get("season_number")):
                continue
            if scope == "episode" and key != (
                _integer(intent.get("season_number")),
                _integer(intent.get("episode_number")),
            ):
                continue
            seen.add(key)
            items.append({
                "item_id": _text(episode.get("tvdb_episode_id") or episode.get("id"))
                or f"S{key[0]:02d}E{key[1]:03d}",
                "content_role": "main_episode",
                "season_number": key[0],
                "episode_number": key[1],
                "aired": _text(episode.get("aired") or episode.get("firstAired")),
            })
    return sorted(items, key=lambda item: (item["season_number"], item["episode_number"]))


def _expanded_hypotheses(candidates: list[CandidateEntity]) -> dict:
    queries = []
    for candidate in candidates[:3]:
        try:
            titles = resolve_title_policy(candidate)
        except TitlePolicyError:
            continue
        year = next(iter(sorted(candidate.years)), "")
        query = _text(f"{titles.canonical_search_title} {year}")
        if query and query not in queries:
            queries.append(query)
    return {
        "status": "ok",
        "hypotheses": [],
        "source_queries": {
            "wikipedia": list(queries),
            "douban": list(queries),
            "tvdb": list(queries),
        },
        "warnings": ["controlled_expansion"],
    }


def _expanded_candidate(
    original: CandidateEntity,
    graph_candidates: tuple[CandidateEntity, ...],
) -> CandidateEntity:
    matches = [
        candidate for candidate in graph_candidates
        if original.normalized_titles.intersection(candidate.normalized_titles)
        and original.years == candidate.years
        and original.media_types == candidate.media_types
    ]
    if not matches:
        return original
    best = max(matches, key=lambda item: (len(item.providers), len(item.facts)))
    return CandidateEntity(original.candidate_key, best.facts)


def _candidate_query(canonical_title: str, year: str, media_type: str, intent: dict) -> str:
    scope = intent.get("scope") or "movie_or_series"
    if media_type == "series" and scope in {"season", "episode"}:
        season = _integer(intent.get("season_number"))
        episode = _integer(intent.get("episode_number"))
        if season is not None:
            marker = f"S{season:02d}"
            if scope == "episode" and episode is not None:
                marker += f"E{episode:0{3 if episode >= 100 else 2}d}"
            return f"{canonical_title} {marker}"
    return _text(f"{canonical_title} {year}")


def _candidate_is_qualified(
    candidate: CandidateEntity,
    intent: dict,
    *,
    direct_anchor: bool,
) -> bool:
    if not candidate.facts or len(candidate.media_types) != 1:
        return False
    if len(candidate.years) != 1:
        return False
    requested_year = _text(intent.get("year"))
    if requested_year and requested_year not in candidate.years:
        return False
    media_type = next(iter(candidate.media_types))
    if media_type == "series" and not _text(candidate.external_ids.get("tvdb")):
        return False
    return direct_anchor or len(candidate.providers) >= 2


def _candidate_contract(
    candidate: CandidateEntity,
    titles,
    intent: dict,
    plan_id: str,
    sources: list[dict],
    verified_relation: dict | None = None,
    candidates_by_key: dict[str, CandidateEntity] | None = None,
) -> tuple[dict, dict, dict]:
    year = next(iter(sorted(candidate.years)), "") or _text(intent.get("year"))
    media_type = next(iter(sorted(candidate.media_types)), "movie")
    animation = any(
        signal in _text(genre).casefold()
        for fact in candidate.facts
        for genre in fact.genres
        for signal in ("animation", "animated", "anime", "动画", "動畫")
    )
    category = f"{'animated' if animation else 'live_action'}_{media_type}"
    source_fact = next((fact for fact in candidate.facts if fact.source_url), candidate.facts[0])
    relation_type = "standalone"
    target_contract = {}
    relation_snapshot = {"relation_type": "standalone", "mapping_kind": "standalone"}
    mapping_kind = "standalone"
    library_type = media_type
    season_number = None
    target_candidate = None
    if verified_relation and candidates_by_key:
        target_candidate = candidates_by_key.get(
            verified_relation.get("target_candidate_key") or ""
        )
    if target_candidate is not None:
        try:
            target_titles = resolve_title_policy(target_candidate)
        except TitlePolicyError:
            target_candidate = None
        else:
            relation_type = verified_relation["relation_type"]
            target_year = next(iter(sorted(target_candidate.years)), "")
            target_contract = {
                **target_titles.identity_fields(),
                "year": target_year,
                "external_ids": dict(target_candidate.external_ids),
            }
            if not target_contract["chinese_title"]:
                target_contract["chinese_title"] = (
                    target_contract["original_title"]
                    or target_contract["english_title"]
                )
            mapping_kind = "temporary_related_special"
            library_type = "series"
            season_number = 0
            category = f"{'animated' if animation else 'live_action'}_series"
            relation_snapshot = {
                "relation_type": relation_type,
                "target_entity_key": target_candidate.candidate_key,
                "target_chinese_title": target_contract["chinese_title"],
                "target_canonical_latin_title": target_contract["english_title"],
                "target_year": target_year,
                "target_external_ids": dict(target_candidate.external_ids),
                "mapping_kind": mapping_kind,
                "season_number": 0,
                "episode_number": None,
                "tvdb_episode_id": "",
            }
    content_kind = media_type
    if target_candidate is not None:
        content_kind = {
            "prequel": "prequel_movie",
            "sequel": "sequel_movie",
            "extension_movie": "extension_movie",
            "spin_off": "spin_off",
            "special": "special",
        }[relation_type]
    identity = {
        **titles.identity_fields(),
        "year": year,
        "content_kind": content_kind,
        "summary": "",
        "original_release_date": "",
        "poster_url": candidate.poster_url,
        "poster_source": _candidate_poster_source(candidate),
        "external_ids": dict(candidate.external_ids),
    }
    if not identity["chinese_title"]:
        identity["chinese_title"] = identity["original_title"] or identity["english_title"]
    provider_statuses, provider_support = _provider_status_and_support(sources)
    contract = {
        "schema_version": 1,
        "metadata_id": plan_id,
        "confirmed": False,
        "identity": identity,
        "retrieval": {
            "media_type": media_type,
            "scope": intent.get("scope") or "movie_or_series",
            "query": _candidate_query(
                titles.canonical_search_title,
                year,
                media_type,
                intent,
            ),
        },
        "relation": {
            "type": relation_type,
            "target_series": target_contract,
            "source": (
                "verified_relation_scorecard"
                if target_candidate is not None
                else "request_entity_graph"
            ),
        },
        "placement": {
            "library_type": library_type,
            "category_kind": category,
            "season_number": season_number,
            "episode_number": None,
            "mapping_kind": mapping_kind,
            "mapping_source": (
                "local_allocator_after_verified_relation"
                if target_candidate is not None
                else "request_entity_graph"
            ),
            "tvdb_episode_id": "",
        },
        "source_entry": {
            "title": identity["chinese_title"] or identity["english_title"],
            "url": source_fact.source_url,
            "external_id": next(iter(source_fact.external_ids.values()), ""),
            "provider": source_fact.provider,
            "verification": "verified",
        },
        "items": _candidate_items(candidate, intent),
        "evidence": {
            "provider_statuses": provider_statuses,
            "provider_support": provider_support,
            "decision": {
                "mode": "deterministic_bounded",
                "scoring_version": SCORING_VERSION,
                "scope": intent.get("scope") or "movie_or_series",
                "season_number": intent.get("season_number"),
                "episode_number": intent.get("episode_number"),
            },
        },
        "warnings": [],
    }
    verified_specials = _verified_tvdb_special_candidates(sources)
    contract["evidence"]["verified_tvdb_special_candidates"] = verified_specials
    contract["evidence"]["tvdb_official_special_candidates"] = (
        _matching_tvdb_official_candidates(contract, verified_specials)
    )
    entity = {
        "entity_key": candidate.candidate_key,
        "content_kind": content_kind,
        "year": year,
        **{key: value for key, value in titles.identity_fields().items() if key != "english_title"},
        "canonical_latin_title": titles.canonical_latin_title,
        "poster_url": candidate.poster_url,
        "poster_source": _candidate_poster_source(candidate),
        "external_ids": dict(candidate.external_ids),
        "scoring_version": SCORING_VERSION,
    }
    return contract, entity, relation_snapshot


async def build_confirmable_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
    *,
    budget: PlanningBudget | None = None,
    locked_identity: tuple[str, str] | None = None,
) -> dict:
    # occupied_loader/allocator are applied only after an interactive selection;
    # no unselected candidate may reserve a persistent or logical episode slot.
    del occupied_loader, allocator
    budget = budget or PlanningBudget()
    parsed_input = classify_search_input(raw_query)
    if parsed_input.kind == "invalid_link":
        raise SearchPlanningError("unsupported_metadata_link")
    rule_hypotheses = build_rule_hypotheses(raw_query)
    sources = await _budgeted(
        "base_evidence",
        budget,
        collect_evidence(rule_hypotheses, providers),
    )
    graph = build_search_graph(sources)
    all_candidates = list(graph.candidates)
    candidates = list(all_candidates)
    if locked_identity:
        key, value = locked_identity
        candidates = [
            candidate
            for candidate in candidates
            if _text(candidate.external_ids.get(key)) == _text(value)
        ]
    target = normalize_title((rule_hypotheses.get("intent") or {}).get("title"))
    exact = [
        item
        for item in candidates
        if target and target in item.normalized_titles
    ]
    title_matches = [
        item
        for item in candidates
        if target
        and any(
            title.startswith(target)
            for title in item.normalized_titles
        )
    ]
    if title_matches:
        candidates = title_matches
    else:
        candidates = []
    if has_ambiguous_bare_number(raw_query, parsed_input) and not exact:
        raise SearchPlanningError("ambiguous_numeric_role")
    if not candidates:
        ai_hypotheses = await _optional_budgeted(
            "intent_fallback",
            budget,
            asyncio.to_thread(
                infer_search_hypotheses_with_ai,
                {
                    "raw_query": raw_query,
                    "intent": rule_hypotheses.get("intent") or {},
                },
            ),
            None,
        )
        if ai_hypotheses:
            retry_sources = await _optional_budgeted(
                "candidate_finalize",
                budget,
                collect_evidence(ai_hypotheses, providers),
                [],
            )
            if retry_sources:
                sources = _merge_evidence_passes(sources, retry_sources)
                candidates = list(build_search_graph(sources).candidates)
                retry_targets = {
                    normalize_title(item.get("title"))
                    for item in ai_hypotheses.get("hypotheses") or []
                    if normalize_title(item.get("title"))
                }
                matches = [
                    item
                    for item in candidates
                    if any(
                        title.startswith(retry_target)
                        for retry_target in retry_targets
                        for title in item.normalized_titles
                    )
                ]
                candidates = matches
        if not candidates:
            raise SearchPlanningError("insufficient_independent_support")
    intent = dict(rule_hypotheses.get("intent") or {})
    intent["media_type"] = _explicit_media_type(raw_query, intent)

    verified_relations = {}

    combined = []
    title_values = {}
    for candidate in candidates:
        if not _candidate_is_qualified(
            candidate,
            intent,
            direct_anchor=bool(locked_identity),
        ):
            continue
        try:
            title_values[candidate.candidate_key] = resolve_title_policy(candidate)
        except TitlePolicyError:
            continue
        program = program_score(
            candidate,
            intent,
            verified_relations.get(candidate.candidate_key),
        )
        combined.append(combine_score(candidate.candidate_key, program))
    ranked_scores = apply_thresholds(combined)
    if locked_identity:
        ranked_scores = [
            replace(
                item,
                total=max(item.total, MINIMUM_SCORE),
                selectable=not item.program.excluded,
            )
            for item in ranked_scores
        ]
    if not ranked_scores or ranked_scores[0].total < MINIMUM_SCORE:
        expansion_sources = await _optional_budgeted(
            "candidate_finalize",
            budget,
            collect_evidence(_expanded_hypotheses(candidates), providers),
            [],
        )
        if expansion_sources:
            sources = _merge_evidence_passes(sources, expansion_sources)
            expanded_graph = build_search_graph(sources)
            candidates = [
                _expanded_candidate(candidate, expanded_graph.candidates)
                for candidate in candidates
            ]
            combined = []
            for candidate in candidates:
                if not _candidate_is_qualified(
                    candidate,
                    intent,
                    direct_anchor=bool(locked_identity),
                ):
                    continue
                combined.append(combine_score(
                    candidate.candidate_key,
                    program_score(
                        candidate,
                        intent,
                        verified_relations.get(candidate.candidate_key),
                    ),
                ))
            ranked_scores = apply_thresholds(combined)
            if locked_identity:
                ranked_scores = [
                    replace(
                        item,
                        total=max(item.total, MINIMUM_SCORE),
                        selectable=not item.program.excluded,
                    )
                    for item in ranked_scores
                ]
            title_values = {}
            for candidate in candidates:
                if any(
                    item.candidate_key == candidate.candidate_key
                    for item in ranked_scores
                ):
                    title_values[candidate.candidate_key] = (
                        resolve_title_policy(candidate)
                    )
            _log_info(
                f"search_stage status=expanded stage=candidate_finalize "
                f"candidates={len(candidates)}"
            )

    ranked_scores = [item for item in ranked_scores if item.selectable]
    if not ranked_scores:
        raise SearchPlanningError("insufficient_independent_support")
    if len(ranked_scores) > MAX_DISPLAY_CANDIDATES:
        raise SearchPlanningError("too_many_candidates")

    by_key = {item.candidate_key: item for item in candidates}
    ranked = []
    if budget.remaining_for("candidate_finalize") <= 0:
        raise SearchPlanningError("planning_timed_out", ("candidate_finalize",))
    for score in ranked_scores:
        candidate = by_key[score.candidate_key]
        contract, entity, relation = _candidate_contract(
            candidate,
            title_values[score.candidate_key],
            intent,
            plan_id,
            sources,
            verified_relations.get(score.candidate_key),
            by_key,
        )
        query = contract["retrieval"]["query"]
        contract["evidence"]["decision"]["score"] = score.total
        score_value = {
            "version": score.program.version,
            "stable_identity": score.program.stable_identity,
            "independent_sources": score.program.independent_sources,
            "release_consistency": score.program.release_consistency,
            "type_and_scope": score.program.type_and_scope,
            "program_total": score.program.total,
            "total": score.total,
        }
        ranked.append({
            "candidate_key": score.candidate_key,
            "score": score_value,
            "recommended": score.recommended,
            "selectable": score.selectable,
            "media_metadata": contract,
            "prowlarr_queries": [query],
            "poster_url": candidate.poster_url,
            "reasons": list(score.program.reason_codes),
            "entity_snapshot": entity,
            "relation_snapshot": relation,
        })
    top = ranked[0]
    _log_info(
        f"search_plan status=ranked metadata_id={plan_id} "
        f"candidates={len(ranked)} elapsed={budget.elapsed:.3f}"
    )
    return {
        "plan_id": plan_id,
        "raw_query": raw_query,
        "media_metadata": deepcopy(top["media_metadata"]),
        "prowlarr_queries": list(top["prowlarr_queries"]),
        "candidates": ranked,
        "source_queries": deepcopy(rule_hypotheses.get("source_queries") or {}),
        "scoring_version": SCORING_VERSION,
        "relation_pool": [
            entry
            for candidate in all_candidates
            if (entry := _relation_pool_entry(candidate)) is not None
        ],
    }

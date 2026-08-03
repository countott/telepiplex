"""Douban-only discovery and one context-preserving AI decision."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Callable

from .anchored_candidate import materialize_anchored_candidates
from .context import runtime_context
from .entity_graph import build_discovery_graph, normalize_title
from .input_contract import classify_search_input
from .media_metadata_v1 import MetadataV1Error, build_media_metadata_v1
from .planner import (
    SearchPlanningError,
    _anchored_fact_snapshot,
    _candidate_preview_metadata,
)
from .search_logging import log_search_event


_DECISION_KEYS = {"action", "candidate_ids", "rewrite_query"}
_ACTIONS = {"show_candidates", "retry", "no_match"}
_SOURCE_FAILURES = {
    "authentication_failed",
    "blocked",
    "credential_missing",
    "disabled",
    "rate_limited",
    "server_down",
    "timeout",
    "unavailable",
}


class SearchDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class SearchContext:
    search_session_id: str
    original_input: str
    title: str
    year: str
    media_type: str
    scope: str
    season_number: int | None
    episode_number: int | None
    attempt: int
    query: str
    candidates: tuple[dict, ...]
    history: tuple[dict, ...]
    retry_available: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SearchDecision:
    action: str
    candidate_ids: tuple[str, ...] = ()
    rewrite_query: str = ""


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _subject_id(candidate: dict) -> str:
    external_ids = (
        candidate.get("external_ids")
        if isinstance(candidate.get("external_ids"), dict)
        else {}
    )
    return _text(
        candidate.get("subject_id")
        or external_ids.get("douban_subject")
        or external_ids.get("douban")
    )


def validate_search_decision(
    payload: object,
    context: SearchContext,
) -> SearchDecision:
    if not isinstance(payload, dict) or set(payload) != _DECISION_KEYS:
        raise SearchDecisionError("ai_output_invalid")
    action = _text(payload.get("action")).casefold()
    candidate_ids = payload.get("candidate_ids")
    rewrite_query = _text(payload.get("rewrite_query"))
    if (
        action not in _ACTIONS
        or not isinstance(candidate_ids, list)
        or any(not isinstance(item, str) for item in candidate_ids)
    ):
        raise SearchDecisionError("ai_output_invalid")
    normalized_ids = tuple(_text(item) for item in candidate_ids)
    if any(not item for item in normalized_ids):
        raise SearchDecisionError("ai_output_invalid")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise SearchDecisionError("ai_output_invalid")
    allowed_ids = {
        subject_id
        for candidate in context.candidates
        if (subject_id := _subject_id(candidate))
    }

    if action == "show_candidates":
        if (
            rewrite_query
            or not 1 <= len(normalized_ids) <= 5
            or not set(normalized_ids).issubset(allowed_ids)
            or (len(allowed_ids) > 1 and len(normalized_ids) < 2)
        ):
            raise SearchDecisionError("ai_output_invalid")
    elif action == "retry":
        if (
            normalized_ids
            or not context.retry_available
            or context.attempt != 1
            or not rewrite_query
            or normalize_title(rewrite_query)
            == normalize_title(context.query)
        ):
            raise SearchDecisionError("ai_output_invalid")
    else:
        if (
            normalized_ids
            or rewrite_query
            or (context.attempt == 1 and context.retry_available)
        ):
            raise SearchDecisionError("ai_output_invalid")
    return SearchDecision(
        action=action,
        candidate_ids=normalized_ids,
        rewrite_query=rewrite_query,
    )


def decide_with_technical_retry(
    context: SearchContext,
    decide: Callable[[dict], object],
    *,
    logger,
) -> SearchDecision | None:
    payload = context.to_dict()
    for technical_attempt in (1, 2):
        log_search_event(
            logger,
            "search.ai_request",
            search_session_id=context.search_session_id,
            attempt=context.attempt,
            technical_attempt=technical_attempt,
            query=context.query,
            retry_available=context.retry_available,
            prompt_version="douban-search-decision-v1",
            candidate_count=len(context.candidates),
        )
        try:
            decision = validate_search_decision(decide(payload), context)
            log_search_event(
                logger,
                "search.ai_response",
                search_session_id=context.search_session_id,
                attempt=context.attempt,
                technical_attempt=technical_attempt,
                action=decision.action,
                candidate_ids=list(decision.candidate_ids),
                rewrite_query=decision.rewrite_query,
                validation="valid",
            )
            return decision
        except Exception as exc:
            log_search_event(
                logger,
                (
                    "search.ai_technical_retry"
                    if technical_attempt == 1
                    else "search.ai_response"
                ),
                search_session_id=context.search_session_id,
                level="warning",
                attempt=context.attempt,
                technical_attempt=technical_attempt,
                validation="invalid",
                error=type(exc).__name__,
            )
    return None


def _normalized_facts(raw_facts, media_type: str) -> list[dict]:
    result = []
    seen = set()
    for raw in raw_facts or ():
        if not isinstance(raw, dict):
            continue
        fact = deepcopy(raw)
        subject_id = _subject_id(fact)
        fact_type = _text(fact.get("media_type")).casefold()
        if (
            not subject_id
            or subject_id in seen
            or fact_type not in {"movie", "series"}
            or (media_type and fact_type != media_type)
        ):
            continue
        title = _text(
            fact.get("chinese_title")
            or fact.get("title")
            or fact.get("name")
        )
        if not title:
            continue
        fact.update({
            "subject_id": subject_id,
            "title": title,
            "chinese_title": _text(
                fact.get("chinese_title") or title
            ),
            "english_title": _text(
                fact.get("english_title")
                or fact.get("official_english_title")
            ),
            "year": _text(fact.get("year"))[:4],
            "media_type": fact_type,
            "url": _text(fact.get("url"))
            or f"https://movie.douban.com/subject/{subject_id}/",
        })
        seen.add(subject_id)
        result.append(fact)
        if len(result) >= 15:
            break
    return result


def _provider_payload(parsed, query: str) -> dict:
    return {
        "status": "ok",
        "intent": {
            "title": parsed.title,
            "year": parsed.year,
            "media_type": parsed.media_type,
            "scope": parsed.scope,
            "season_number": parsed.season_number,
            "episode_number": parsed.episode_number,
        },
        "hypotheses": [{
            "title": query,
            "year": parsed.year,
            "content_identity": parsed.media_type or "movie_or_series",
            "scope": parsed.scope,
            "season_number": parsed.season_number,
            "episode_number": parsed.episode_number,
            "explicit_facts": [],
            "inferred_facts": [],
        }],
        "source_queries": {"douban": [query]},
        "warnings": [],
    }


def _unique_hard_match(parsed, facts: list[dict]) -> dict | None:
    expected_title = normalize_title(parsed.title)
    matched = []
    for fact in facts:
        titles = {
            normalize_title(value)
            for value in (
                fact.get("title"),
                fact.get("chinese_title"),
                fact.get("english_title"),
                fact.get("original_title"),
                *(fact.get("aliases") or ()),
            )
            if _text(value)
        }
        if (
            expected_title
            and expected_title in titles
            and fact.get("year")
            and fact.get("media_type") in {"movie", "series"}
            and (
                not parsed.year
                or parsed.year == _text(fact.get("year"))
            )
            and (
                not parsed.media_type
                or parsed.media_type == fact.get("media_type")
            )
        ):
            matched.append(fact)
    return matched[0] if len(matched) == 1 else None


def _candidate_from_fact(
    fact: dict,
    *,
    plan_id: str,
    raw_query: str,
    scope: str,
    season_number: int | None,
    episode_number: int | None,
    selection_mode: str,
) -> dict:
    graph = build_discovery_graph([{
        "source": "douban",
        "status": "ok",
        "facts": [fact],
        "source_urls": [fact.get("url")],
        "error": "",
    }])
    graph_fact = graph.candidates[0].facts[0]
    media_type = graph_fact.media_type
    intended_scope = (
        "movie"
        if media_type == "movie"
        else scope if scope in {"whole_series", "season", "episode"} else "work"
    )
    anchored = materialize_anchored_candidates(
        graph,
        {
            "status": "resolved",
            "candidates": [{
                "candidate_id": f"douban:{_subject_id(fact)}",
                "anchor_fact_id": graph_fact.fact_id,
                "identity_role": (
                    "movie" if media_type == "movie" else "series_root"
                ),
                "intended_scope": intended_scope,
                "fact_bindings": [{
                    "fact_id": graph_fact.fact_id,
                    "role": (
                        "movie"
                        if media_type == "movie"
                        else "series_root"
                    ),
                    "season_number": None,
                    "episode_number": None,
                }],
                "ai_confidence": (
                    1.0 if selection_mode == "hard_match" else 0.0
                ),
                "ai_reason": selection_mode,
            }],
        },
        provider_statuses={"douban": "ok"},
    )[0]
    candidate = _candidate_from_anchored(
        anchored,
        plan_id=plan_id,
        raw_query=raw_query,
        selection_mode=selection_mode,
    )
    candidate["requested_season_number"] = season_number
    candidate["requested_episode_number"] = episode_number
    return candidate


def _candidate_from_anchored(
    anchored,
    *,
    plan_id: str,
    raw_query: str,
    selection_mode: str,
) -> dict:
    try:
        contract = build_media_metadata_v1(
            anchored,
            metadata_id=plan_id,
            raw_query=raw_query,
        )
        metadata_ready = True
        metadata_error = {}
    except MetadataV1Error as exc:
        contract = _candidate_preview_metadata(
            anchored,
            metadata_id=plan_id,
            raw_query=raw_query,
            metadata_error=exc,
        )
        metadata_ready = False
        metadata_error = {
            "code": exc.code,
            "missing_fields": list(exc.missing_fields),
        }
    return {
        "candidate_key": anchored.candidate_id,
        "candidate_id": anchored.candidate_id,
        "anchor_fact_id": anchored.anchor_fact_id,
        "identity_role": anchored.identity_role,
        "intended_scope": anchored.intended_scope,
        "score": {"total": 0},
        "recommended": False,
        "selectable": True,
        "media_metadata": contract,
        "prowlarr_queries": list(
            (contract.get("retrieval") or {}).get("queries") or []
        ),
        "poster_url": anchored.primary_poster_url,
        "poster_assets": [
            item.to_dict() for item in anchored.poster_assets
        ],
        "source_links": [
            item.to_dict() for item in anchored.source_links
        ],
        "unresolved_sources": list(anchored.unresolved_sources),
        "ai_confidence": anchored.ai_confidence,
        "ai_reason": anchored.ai_reason,
        "reasons": [],
        "candidate_version": "douban-confirmation",
        "metadata_ready": metadata_ready,
        "metadata_error": metadata_error,
        "links_frozen": True,
        "fact_snapshot": _anchored_fact_snapshot(anchored),
        "entity_snapshot": {
            "entity_key": anchored.candidate_id,
            "content_kind": (
                (contract.get("identity") or {}).get("content_kind")
            ),
            "external_ids": dict(
                (contract.get("identity") or {}).get("external_ids") or {}
            ),
        },
        "relation_snapshot": {
            "relation_type": "standalone",
            "mapping_kind": "standalone",
        },
    }


def build_direct_entity_plan(
    direct,
    *,
    raw_query: str,
    plan_id: str,
) -> dict:
    source = deepcopy(direct.evidence)
    source["source"] = direct.provider
    graph = build_discovery_graph([source])
    facts = [
        fact
        for entity in graph.candidates
        for fact in entity.facts
    ]
    matching = [
        fact
        for fact in facts
        if (
            direct.stable_identity[1]
            in set(fact.external_ids.values())
            or len(facts) == 1
        )
    ]
    if len(matching) != 1:
        raise SearchPlanningError("direct_link_invalid")
    fact = matching[0]
    media_type = direct.media_type
    role = (
        "movie"
        if media_type == "movie"
        else direct.scope
        if direct.scope in {"season", "episode"}
        else "series_root"
    )
    intended_scope = (
        "movie"
        if media_type == "movie"
        else direct.scope
        if direct.scope in {"whole_series", "season", "episode"}
        else "work"
    )
    anchored = materialize_anchored_candidates(
        graph,
        {
            "status": "resolved",
            "candidates": [{
                "candidate_id": (
                    f"{direct.stable_identity[0]}:"
                    f"{direct.stable_identity[1]}"
                ),
                "anchor_fact_id": fact.fact_id,
                "identity_role": (
                    "movie"
                    if media_type == "movie"
                    else direct.scope
                    if direct.scope in {"season", "episode"}
                    else "series_root"
                ),
                "intended_scope": intended_scope,
                "fact_bindings": [{
                    "fact_id": fact.fact_id,
                    "role": role,
                    "season_number": (
                        direct.season_number
                        if direct.scope in {"season", "episode"}
                        else None
                    ),
                    "episode_number": (
                        direct.episode_number
                        if direct.scope == "episode"
                        else None
                    ),
                }],
                "ai_confidence": 1.0,
                "ai_reason": "direct_stable_identity",
            }],
        },
        provider_statuses={direct.provider: "ok"},
        locked_anchor_fact_id=fact.fact_id,
    )[0]
    candidate = _candidate_from_anchored(
        anchored,
        plan_id=plan_id,
        raw_query=raw_query,
        selection_mode="direct_link",
    )
    candidate["requested_season_number"] = direct.season_number
    candidate["requested_episode_number"] = direct.episode_number
    return {
        "plan_id": plan_id,
        "search_session_id": plan_id,
        "raw_query": raw_query,
        "entry_kind": "link",
        "links_frozen": True,
        "auto_confirm": True,
        "selection_mode": "direct_link",
        "media_metadata": deepcopy(candidate["media_metadata"]),
        "prowlarr_queries": list(candidate["prowlarr_queries"]),
        "candidates": [candidate],
        "source_queries": {},
        "scoring_version": "direct-link-v1",
        "relation_pool": [],
    }


async def build_douban_first_search_plan(
    raw_query: str,
    plan_id: str,
    douban_provider: Callable[[dict], dict],
    *,
    ai_decider: Callable[[dict], object],
) -> dict:
    parsed = classify_search_input(raw_query)
    if parsed.kind != "text":
        raise SearchPlanningError(parsed.reason or "invalid_query")
    query = _text(parsed.raw_query or parsed.title)
    history = []
    selected_facts = None
    selection_mode = ""
    logger = runtime_context.logger

    for attempt in (1, 2):
        log_search_event(
            logger,
            "search.douban_started",
            search_session_id=plan_id,
            attempt=attempt,
            query=query,
        )
        try:
            source = await asyncio.to_thread(
                douban_provider,
                _provider_payload(parsed, query),
            )
        except Exception as exc:
            log_search_event(
                logger,
                "search.douban_failed",
                search_session_id=plan_id,
                level="error",
                attempt=attempt,
                error=type(exc).__name__,
            )
            raise SearchPlanningError(
                "source_failure",
                (f"douban:{type(exc).__name__}",),
            ) from exc
        if not isinstance(source, dict):
            log_search_event(
                logger,
                "search.douban_failed",
                search_session_id=plan_id,
                level="error",
                attempt=attempt,
                status="invalid_response",
            )
            raise SearchPlanningError(
                "source_failure",
                ("douban:invalid_response",),
            )
        status = _text(source.get("status")).casefold() or "server_down"
        if status in _SOURCE_FAILURES:
            log_search_event(
                logger,
                "search.douban_failed",
                search_session_id=plan_id,
                level="error",
                attempt=attempt,
                status=status,
            )
            raise SearchPlanningError(
                "source_failure",
                (f"douban:{status}",),
            )
        facts = _normalized_facts(
            source.get("facts"),
            parsed.media_type,
        )
        log_search_event(
            logger,
            "search.douban_completed",
            search_session_id=plan_id,
            attempt=attempt,
            status=status,
            result_count=len(facts),
            candidates=[{
                "subject_id": _subject_id(fact),
                "title": fact.get("chinese_title"),
                "year": fact.get("year"),
                "media_type": fact.get("media_type"),
            } for fact in facts],
        )
        hard_match = (
            _unique_hard_match(parsed, facts)
            if attempt == 1
            else None
        )
        if hard_match is not None:
            log_search_event(
                logger,
                "search.hard_match_evaluated",
                search_session_id=plan_id,
                attempt=attempt,
                matched=True,
                subject_id=_subject_id(hard_match),
            )
            selected_facts = [hard_match]
            selection_mode = "hard_match"
            break
        log_search_event(
            logger,
            "search.hard_match_evaluated",
            search_session_id=plan_id,
            attempt=attempt,
            matched=False,
        )

        context = SearchContext(
            search_session_id=plan_id,
            original_input=raw_query,
            title=parsed.title,
            year=parsed.year,
            media_type=parsed.media_type,
            scope=parsed.scope,
            season_number=parsed.season_number,
            episode_number=parsed.episode_number,
            attempt=attempt,
            query=query,
            candidates=tuple(deepcopy(facts)),
            history=tuple(deepcopy(history)),
            retry_available=attempt == 1,
        )
        decision = await asyncio.to_thread(
            decide_with_technical_retry,
            context,
            ai_decider,
            logger=logger,
        )
        if decision is None:
            if facts:
                log_search_event(
                    logger,
                    "search.ai_fallback",
                    search_session_id=plan_id,
                    level="warning",
                    attempt=attempt,
                    candidate_count=min(len(facts), 5),
                )
                selected_facts = facts[:5]
                selection_mode = "program_fallback"
                break
            raise SearchPlanningError("ai_candidate_failure")
        history.append({
            "attempt": attempt,
            "query": query,
            "candidate_ids": [
                _subject_id(fact) for fact in facts
            ],
            "action": decision.action,
            "selected_candidate_ids": list(decision.candidate_ids),
            "rewrite_query": decision.rewrite_query,
        })
        if decision.action == "show_candidates":
            by_id = {_subject_id(fact): fact for fact in facts}
            selected_facts = [
                by_id[subject_id]
                for subject_id in decision.candidate_ids
            ]
            selection_mode = "ai_shortlist"
            break
        if decision.action == "retry":
            query = decision.rewrite_query
            continue
        raise SearchPlanningError("no_match")

    if not selected_facts:
        raise SearchPlanningError("no_match")
    candidates = [
        _candidate_from_fact(
            fact,
            plan_id=plan_id,
            raw_query=raw_query,
            scope=parsed.scope,
            season_number=parsed.season_number,
            episode_number=parsed.episode_number,
            selection_mode=selection_mode,
        )
        for fact in selected_facts
    ]
    candidates[0]["recommended"] = True
    top = candidates[0]
    return {
        "plan_id": plan_id,
        "search_session_id": plan_id,
        "raw_query": raw_query,
        "entry_kind": "text",
        "links_frozen": True,
        "auto_confirm": selection_mode == "hard_match",
        "selection_mode": selection_mode,
        "media_metadata": deepcopy(top["media_metadata"]),
        "prowlarr_queries": list(top["prowlarr_queries"]),
        "candidates": candidates,
        "source_queries": {"douban": [query]},
        "scoring_version": "douban-first-v1",
        "relation_pool": [],
    }

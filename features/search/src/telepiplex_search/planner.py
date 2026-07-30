"""AI and evidence based canonical search-plan builder."""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from collections.abc import Callable
from copy import deepcopy

from .context import runtime_context

from .ai import (
    infer_anchored_candidates_with_ai,
    infer_candidate_scorecard_with_ai,
    infer_search_hypotheses_with_ai,
)
from .anchored_candidate import (
    CandidateBindingError,
    materialize_anchored_candidates,
)
from .candidate_score import (
    SCORING_VERSION,
    apply_thresholds,
    combine_score,
    program_score,
    validate_ai_candidate_score,
)
from .deterministic import build_rule_hypotheses
from .entity_graph import (
    CandidateEntity,
    EvidenceFact,
    EvidenceFactConflict,
    SearchGraph,
    build_discovery_graph,
    build_search_graph,
    merge_verified_equivalence_edges,
    normalize_title,
)
from .input_contract import classify_search_input, has_ambiguous_bare_number
from .media_metadata_v1 import MetadataV1Error, build_media_metadata_v1
from .prowlarr_query import build_prowlarr_query
from .search_plan import (
    TEMPORARY_MAPPING_KIND,
    TemporarySpecialAllocator,
    finalize_search_plan,
    normalize_source_locator,
)
from .source_orchestrator import orchestrate_sources
from .title_policy import TitlePolicyError, resolve_title_policy


class SearchPlanningError(RuntimeError):
    def __init__(self, code: str, reason_codes=()):
        self.code = str(code or "search_planning_failed")
        self.reason_codes = tuple(str(item) for item in reason_codes or ())
        super().__init__(self.code)


def _log_info(message: str):
    if runtime_context.logger:
        runtime_context.logger.info(message)


def _log_warning(message: str):
    if runtime_context.logger:
        runtime_context.logger.warning(message)


def _build_logged_search_graph(
    sources: list[dict],
    *,
    stage: str,
):
    try:
        graph = build_search_graph(sources)
    except EvidenceFactConflict as exc:
        fields = list(exc.conflicting_fields)
        _log_warning(
            "search_fact_merge status=conflict "
            f"stage={stage} provider={exc.provider} "
            f"fact_id={exc.fact_id} "
            f"fields={json.dumps(fields, ensure_ascii=False)}"
        )
        raise SearchPlanningError(
            "source_fact_conflict",
            (
                exc.fact_id,
                *(f"field:{field}" for field in fields),
            ),
        ) from exc
    for diagnostic in graph.fact_merges:
        _log_info(
            "search_fact_merge status=merged "
            f"stage={stage} provider={diagnostic.provider} "
            f"fact_id={diagnostic.fact_id} "
            f"occurrences={diagnostic.occurrences}"
        )
    return graph


def _build_logged_discovery_graph(
    sources: list[dict],
    *,
    stage: str,
):
    graph = build_discovery_graph(sources)
    for diagnostic in graph.fact_merges:
        if diagnostic.conflicting_fields:
            _log_warning(
                "search_fact_merge status=deferred "
                f"stage={stage} provider={diagnostic.provider} "
                f"fact_id={diagnostic.fact_id} "
                "fields="
                f"{json.dumps(list(diagnostic.conflicting_fields), ensure_ascii=False)} "
                f"occurrences={diagnostic.occurrences}"
            )
        else:
            _log_info(
                "search_fact_merge status=merged "
                f"stage={stage} provider={diagnostic.provider} "
                f"fact_id={diagnostic.fact_id} "
                f"occurrences={diagnostic.occurrences}"
            )
    return graph


class PlanningBudget:
    def __init__(
        self,
        *,
        clock=time.monotonic,
        total: float | None = None,
        stages: dict[str, float] | None = None,
    ):
        # Kept as a compatibility clock for callers and logs. search 1.2.0
        # deliberately has no business-layer planning deadline; provider HTTP
        # clients retain their independently configurable fault timeouts.
        del total, stages
        self.clock = clock
        self.started_at = clock()

    def remaining_for(self, stage: str) -> float:
        del stage
        return float("inf")

    @property
    def elapsed(self) -> float:
        return max(0.0, self.clock() - self.started_at)


async def _budgeted(stage: str, budget: PlanningBudget, awaitable):
    del stage, budget
    return await awaitable


async def _optional_budgeted(
    stage: str,
    budget: PlanningBudget,
    awaitable,
    default,
):
    try:
        return await _budgeted(stage, budget, awaitable)
    except SearchPlanningError:
        raise


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
    status_priority = {
        "not_found": 0,
        "disabled": 1,
        "unavailable": 2,
        "invalid": 3,
        "server_down": 4,
        "timeout": 5,
        "blocked": 6,
        "rate_limited": 7,
        "credential_missing": 8,
        "authentication_failed": 9,
        "ok": 10,
    }
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
        if status_priority.get(status, 3) > status_priority.get(
            target["status"],
            3,
        ):
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


def _no_fact_failure(sources: list[dict]) -> tuple[str, tuple[str, ...]]:
    source_statuses = [
        (
            _text(item.get("source")).casefold(),
            _text(item.get("status")).casefold(),
        )
        for item in sources
        if isinstance(item, dict)
        and _text(item.get("source"))
    ]
    if source_statuses and all(
        status == "disabled"
        for _provider, status in source_statuses
    ):
        return (
            "source_failure",
            tuple(
                f"{provider}:{status}"
                for provider, status in source_statuses
            ),
        )
    hard_failures = [
        (
            f"{_text(item.get('source')).casefold()}:"
            f"{_text(item.get('status')).casefold()}"
        )
        for item in sources
        if _text(item.get("status")).casefold() not in {
            "ok",
            "not_found",
            "disabled",
        }
    ]
    hard_failures = tuple(dict.fromkeys(hard_failures))
    if not hard_failures:
        return "", ()
    statuses = [
        item.rsplit(":", 1)[-1]
        for item in hard_failures
    ]
    return (
        (
            "source_rate_limited"
            if all(status == "rate_limited" for status in statuses)
            else "source_failure"
        ),
        hard_failures,
    )


def _verified_ai_title(
    payload: dict | None,
    candidates: list[CandidateEntity],
) -> str:
    for hypothesis in (payload or {}).get("hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        value = _text(hypothesis.get("title"))
        target = normalize_title(value)
        if not target:
            continue
        if any(
            target in candidate.normalized_titles
            or any(
                title.startswith(target) or target.startswith(title)
                for title in candidate.normalized_titles
            )
            for candidate in candidates
        ):
            return value
    return ""


def _ai_clarification_plan(
    *,
    plan_id: str,
    raw_query: str,
    rule_intent: dict,
    payload: dict | None,
) -> dict | None:
    if (payload or {}).get("status") != "needs_clarification":
        return None
    hint = (payload.get("intent_hint") or {}).get("media_type_hint")
    if _text(hint).casefold() != "unknown":
        return None
    if (
        _explicit_media_type(raw_query, rule_intent)
        or _text(rule_intent.get("year"))
    ):
        return None
    raw_title = _text(rule_intent.get("title")) or _text(raw_query)
    if not normalize_title(raw_title):
        return None
    year = _text(rule_intent.get("year"))
    query_title = " ".join(item for item in (raw_title, year) if item)
    options = [{
        "label": f"电影《{query_title}》",
        "query": f"{query_title}（电影）",
        "media_type": "movie",
        "year": year,
    }, {
        "label": f"剧集《{query_title}》",
        "query": f"{query_title}（电视剧）",
        "media_type": "series",
        "year": year,
    }]
    return {
        "plan_id": plan_id,
        "raw_query": raw_query,
        "status": "needs_clarification",
        "clarification": {
            "reason": (
                _text(payload.get("clarification_reason"))
                or "存在多个媒体类型，请选择后继续验证。"
            ),
            "options": options[:6],
        },
        "candidates": [],
    }


def _candidate_titles_related(
    left: CandidateEntity,
    right: CandidateEntity,
) -> bool:
    suffixes = (
        "themovie",
        "movie",
        "film",
        "电影版",
        "電影版",
        "电影",
        "電影",
        "剧场版",
        "劇場版",
    )

    def family(title: str) -> str:
        for suffix in suffixes:
            if title.endswith(suffix) and len(title) - len(suffix) >= 4:
                return title[:-len(suffix)]
        return title

    for left_title in left.normalized_titles:
        for right_title in right.normalized_titles:
            if left_title == right_title or family(left_title) == family(
                right_title
            ):
                return True
    return False


def _candidate_display_title(
    candidate: CandidateEntity,
    target: str,
    preferred_title: str,
) -> str:
    exact = next(
        (
            title
            for title in candidate.titles
            if normalize_title(title) == target
        ),
        "",
    )
    if exact:
        return exact
    preferred = _text(preferred_title)
    if preferred and re.search(r"[\u3400-\u9fff]", preferred):
        return preferred
    for provider in ("douban", "tvdb", "wikipedia"):
        for fact in candidate.facts:
            if fact.provider != provider:
                continue
            for title in fact.titles:
                if re.search(r"[\u3400-\u9fff]", title):
                    return title
    return next(iter(candidate.titles), "")


def _candidate_clarification_lock(
    candidate: CandidateEntity,
) -> dict:
    for key in ("tvdb", "douban_subject", "wikipedia"):
        value = _text(candidate.external_ids.get(key))
        if value:
            return {"key": key, "value": value}
    return {}


def _source_clarification_option(
    candidate: CandidateEntity,
    *,
    media_type: str,
    target: str,
    preferred_title: str,
) -> dict | None:
    display_title = _candidate_display_title(
        candidate,
        target,
        preferred_title,
    )
    year = next(iter(sorted(candidate.years)), "")
    if not display_title or not year:
        return None
    try:
        query_title = resolve_title_policy(
            candidate
        ).canonical_search_title
    except TitlePolicyError:
        query_title = display_title
    suffix = "电影" if media_type == "movie" else "电视剧"
    type_label = "电影" if media_type == "movie" else "剧集"
    option = {
        "label": f"{type_label}《{display_title}》({year})",
        "query": f"{query_title} {year}（{suffix}）",
        "media_type": media_type,
        "year": year,
    }
    locked_identity = _candidate_clarification_lock(candidate)
    if locked_identity:
        option["locked_identity"] = locked_identity
    return option


def _source_media_type_clarification_plan(
    *,
    plan_id: str,
    raw_query: str,
    intent: dict,
    candidates: list[CandidateEntity],
    locked_identity: tuple[str, str] | None = None,
) -> dict | None:
    if locked_identity or _explicit_media_type(raw_query, intent):
        return None
    target = normalize_title(intent.get("title") or raw_query)
    if not target:
        return None
    requested_year = _text(intent.get("year"))
    year_bounded = [
        candidate
        for candidate in candidates
        if (
            not requested_year
            or requested_year in candidate.years
        )
    ]
    direct = [
        candidate
        for candidate in year_bounded
        if any(
            title == target
            or title.startswith(target)
            or target.startswith(title)
            for title in candidate.normalized_titles
        )
    ]
    bounded = list(direct)
    for candidate in year_bounded:
        if candidate in bounded:
            continue
        if any(
            _candidate_titles_related(candidate, anchor)
            for anchor in direct
        ):
            bounded.append(candidate)
    raw_movies = [
        candidate
        for candidate in bounded
        if candidate.media_types == frozenset({"movie"})
    ]
    raw_series = [
        candidate
        for candidate in bounded
        if candidate.media_types == frozenset({"series"})
    ]
    related_pairs = [
        (movie, show)
        for movie in raw_movies
        for show in raw_series
        if (
            _candidate_titles_related(movie, show)
            and (
                target in movie.normalized_titles
                or target in show.normalized_titles
            )
        )
    ]
    if not related_pairs:
        return None

    movies = [
        candidate
        for candidate in raw_movies
        if any(candidate is movie for movie, _show in related_pairs)
    ]
    series = [
        candidate
        for candidate in raw_series
        if any(candidate is show for _movie, show in related_pairs)
    ]

    def group_options(media_type: str, group: list[CandidateEntity]) -> list[dict]:
        options = []
        seen = set()
        for candidate in sorted(
            group,
            key=lambda item: (
                next(iter(sorted(item.years)), ""),
                item.candidate_key,
            ),
        ):
            option = _source_clarification_option(
                candidate,
                media_type=media_type,
                target=target,
                preferred_title=_text(intent.get("title")),
            )
            if option is None:
                continue
            key = (
                media_type,
                normalize_title(option.get("label")),
                _text(option.get("year")),
                normalize_title(option.get("query")),
            )
            if key in seen:
                continue
            seen.add(key)
            options.append(option)
        return options

    movie_options = group_options("movie", movies)
    series_options = group_options("series", series)
    if not movie_options or not series_options:
        return None
    options = []
    for index in range(max(len(movie_options), len(series_options))):
        for group in (movie_options, series_options):
            if index < len(group):
                options.append(group[index])
            if len(options) >= 6:
                break
        if len(options) >= 6:
            break
    return {
        "plan_id": plan_id,
        "raw_query": raw_query,
        "status": "needs_clarification",
        "clarification": {
            "reason": (
                "来源证据同时匹配电影和剧集，请选择后继续验证。"
            ),
            "options": options,
        },
        "candidates": [],
    }


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
        "aliases": list(candidate.titles),
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


def _ordered_expansion_candidates(
    candidates: list[CandidateEntity],
    intent: dict,
) -> list[CandidateEntity]:
    target = normalize_title(intent.get("title"))
    requested_year = _text(intent.get("year"))
    requested_type = _text(intent.get("media_type")).casefold()

    def rank(candidate: CandidateEntity) -> tuple:
        titles = candidate.normalized_titles
        exact = bool(target and target in titles)
        prefix_lengths = [
            len(title) - len(target)
            for title in titles
            if target and title.startswith(target)
        ]
        prefix_length = min(prefix_lengths, default=10**6)
        year_conflict = bool(
            requested_year
            and candidate.years
            and requested_year not in candidate.years
        )
        type_conflict = bool(
            requested_type in {"movie", "series"}
            and candidate.media_types
            and requested_type not in candidate.media_types
        )
        return (
            0 if exact else 1,
            1 if year_conflict else 0,
            1 if type_conflict else 0,
            prefix_length,
            -len(candidate.providers),
            candidate.candidate_key,
        )

    return sorted(candidates, key=rank)


def _expanded_hypotheses(
    candidates: list[CandidateEntity],
    intent: dict,
) -> dict:
    queries = []
    for candidate in _ordered_expansion_candidates(candidates, intent)[:3]:
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
    del year
    scope = intent.get("scope") or "movie_or_series"
    if media_type == "movie":
        scope = "movie"
    return build_prowlarr_query(
        canonical_title,
        scope,
        _integer(intent.get("season_number")),
        _integer(intent.get("episode_number")),
    )


def _candidate_score_context(
    raw_query: str,
    intent: dict,
    candidates: list[CandidateEntity],
) -> dict:
    return {
        "intent": {
            "raw_query": _text(raw_query),
            "title": _text(intent.get("title")),
            "year": _text(intent.get("year")),
            "media_type": _text(intent.get("media_type")),
            "scope": _text(intent.get("scope")),
            "season_number": _integer(intent.get("season_number")),
            "episode_number": _integer(intent.get("episode_number")),
        },
        "candidates": [{
            "candidate_key": candidate.candidate_key,
            "facts": [{
                "fact_id": fact.fact_id,
                "provider": fact.provider,
                "titles": list(fact.titles),
                "year": fact.year,
                "media_type": fact.media_type,
                "external_ids": dict(fact.external_ids),
                "original_language": fact.original_language,
                "official_english_title": fact.official_english_title,
                "romanized_original_title": fact.romanized_original_title,
                "complex_signals": list(fact.complex_signals),
            } for fact in candidate.facts],
        } for candidate in candidates],
    }


def _validated_candidate_ai_scores(
    payload,
    candidates: list[CandidateEntity],
) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        return {}
    scores = payload.get("scores")
    if not isinstance(scores, list) or len(scores) != len(candidates):
        return {}
    expected_keys = [candidate.candidate_key for candidate in candidates]
    actual_keys = [
        str(item.get("candidate_key") or "")
        if isinstance(item, dict)
        else ""
        for item in scores
    ]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != set(
        expected_keys
    ):
        return {}
    raw_by_key = {item["candidate_key"]: item for item in scores}
    result = {}
    for candidate in candidates:
        validated = validate_ai_candidate_score(
            raw_by_key[candidate.candidate_key],
            candidate_key=candidate.candidate_key,
            allowed_fact_ids={
                fact.fact_id for fact in candidate.facts
            },
        )
        if validated is None:
            return {}
        result[candidate.candidate_key] = validated
    return result


def _candidate_qualification_reason(
    candidate: CandidateEntity,
    intent: dict,
    *,
    direct_anchor: bool,
) -> str:
    if not candidate.facts or len(candidate.media_types) != 1:
        return "media_type"
    if len(candidate.years) != 1:
        return "year"
    requested_year = _text(intent.get("year"))
    if requested_year and requested_year not in candidate.years:
        return "year"
    media_type = next(iter(candidate.media_types))
    requested_type = _text(intent.get("media_type")).casefold()
    if (
        requested_type in {"movie", "series"}
        and media_type != requested_type
    ):
        return "media_type"
    if media_type == "series" and not _text(candidate.external_ids.get("tvdb")):
        return "missing_tvdb"
    if (
        media_type == "series"
        and _text(intent.get("scope")).casefold() in {"season", "episode"}
        and not _candidate_items(candidate, intent)
    ):
        return "missing_scope"
    if not direct_anchor and len(candidate.providers) < 2:
        return "single_source"
    return ""


def _selectable_thresholds(scores):
    return [
        item
        for item in apply_thresholds(scores)
        if item.selectable
    ]


def _candidate_rejection_counts() -> dict[str, int]:
    return {
        "single_source": 0,
        "missing_tvdb": 0,
        "missing_scope": 0,
        "media_type": 0,
        "year": 0,
        "title_policy": 0,
    }


def _log_candidate_funnel(
    *,
    phase: str,
    raw_count: int,
    title_matched: int,
    qualified: int,
    rejected: dict[str, int],
) -> None:
    _log_info(
        "search_stage status=filtered stage=candidate_funnel "
        f"phase={phase} raw={raw_count} "
        f"title_matched={title_matched} qualified={qualified} "
        f"rejected_single_source={rejected['single_source']} "
        f"rejected_missing_tvdb={rejected['missing_tvdb']} "
        f"rejected_missing_scope={rejected['missing_scope']} "
        f"rejected_media_type={rejected['media_type']} "
        f"rejected_year={rejected['year']} "
        f"rejected_title_policy={rejected['title_policy']}"
    )


def _orchestrated_intent(
    ai_intent: dict,
    rule_intent: dict,
    raw_query: str,
) -> dict:
    hints = ai_intent.get("title_hints")
    title = next(
        (
            _text(item)
            for item in (hints if isinstance(hints, list) else [])
            if _text(item)
        ),
        _text(rule_intent.get("title")),
    )
    ai_scope = {
        "work": "movie_or_series",
        "unknown": "movie_or_series",
    }.get(
        _text(ai_intent.get("scope")).casefold(),
        _text(ai_intent.get("scope")).casefold(),
    )
    rule_scope = _text(rule_intent.get("scope")).casefold()
    explicit_scope = (
        rule_scope
        if rule_scope in {"whole_series", "season", "episode"}
        else ""
    )
    explicit_type = _explicit_media_type(raw_query, rule_intent)
    ai_type = _text(ai_intent.get("media_type_hint")).casefold()
    if ai_type == "unknown":
        ai_type = ""
    return {
        "title": title,
        "year": (
            _text(rule_intent.get("year"))
            or _text(ai_intent.get("year_hint"))
        ),
        "media_type": explicit_type or ai_type,
        "scope": explicit_scope or ai_scope or "movie_or_series",
        "season_number": (
            rule_intent.get("season_number")
            or ai_intent.get("season_number")
        ),
        "episode_number": (
            rule_intent.get("episode_number")
            or ai_intent.get("episode_number")
        ),
    }


def _resolve_episode_title_intent(
    raw_query: str,
    intent: dict,
    candidates: list[CandidateEntity],
) -> tuple[dict, str]:
    resolved = dict(intent or {})
    if (
        _text(resolved.get("scope")).casefold() != "episode"
        or (
            _integer(resolved.get("season_number")) is not None
            and _integer(resolved.get("episode_number")) is not None
        )
    ):
        return resolved, ""

    target = normalize_title(raw_query)
    matches = {}
    for candidate in candidates:
        for fact in candidate.facts:
            for episode in fact.episodes:
                episode_title = normalize_title(
                    episode.get("name") or episode.get("title")
                )
                season_number = _integer(episode.get("season_number"))
                episode_number = _integer(episode.get("episode_number"))
                if (
                    not target
                    or episode_title != target
                    or season_number is None
                    or season_number < 0
                    or episode_number is None
                    or episode_number < 1
                ):
                    continue
                key = (
                    candidate.candidate_key,
                    season_number,
                    episode_number,
                )
                matches[key] = candidate.candidate_key

    if not matches:
        raise SearchPlanningError("tvdb_scope_not_verified")
    if len(matches) > 1:
        raise SearchPlanningError("ambiguous_candidates")

    (candidate_key, season_number, episode_number), _ = next(
        iter(matches.items())
    )
    resolved.update({
        "media_type": "series",
        "scope": "episode",
        "season_number": season_number,
        "episode_number": episode_number,
    })
    return resolved, candidate_key


def _actual_source_queries(sources: list[dict]) -> dict:
    result = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = _text(source.get("source")).casefold()
        if not name:
            continue
        queries = source.get("query_summaries")
        result[name] = list(queries) if isinstance(queries, list) else []
    return result


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
        "aliases": list(candidate.titles),
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


def _anchored_fact_payload(graph) -> list[dict]:
    result = []
    seen = set()
    provider_counts = {}
    for entity in graph.candidates:
        for fact in entity.facts:
            if fact.fact_id in seen:
                continue
            provider = _text(fact.provider).casefold()
            if provider_counts.get(provider, 0) >= 20:
                continue
            seen.add(fact.fact_id)
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            result.append({
                "fact_id": fact.fact_id,
                "provider": fact.provider,
                "titles": list(fact.titles),
                "year": fact.year,
                "media_type": fact.media_type,
                "external_ids": dict(fact.external_ids),
                "source_url": fact.source_url,
                "poster_url": fact.poster_url,
                "original_title": fact.original_title,
                "original_language": fact.original_language,
                "official_english_title": fact.official_english_title,
                "romanized_original_title": fact.romanized_original_title,
                "chinese_title": fact.chinese_title,
                "genres": list(fact.genres),
                "tvdb_inventory": [],
            })
    return result


def _locked_anchor_fact_id(graph, locked_identity) -> str:
    if not locked_identity:
        return ""
    key, expected = locked_identity
    expected = _text(expected)
    matches = [
        fact.fact_id
        for entity in graph.candidates
        for fact in entity.facts
        if _text(fact.external_ids.get(key)) == expected
    ]
    if len(set(matches)) != 1:
        raise SearchPlanningError("direct_link_anchor_missing")
    return matches[0]


async def _call_candidate_editor(candidate_editor, context: dict):
    try:
        if asyncio.iscoroutinefunction(candidate_editor):
            return await candidate_editor(context)
        return await asyncio.to_thread(candidate_editor, context)
    except Exception as exc:
        raise SearchPlanningError(
            "ai_candidate_failure",
            (type(exc).__name__,),
        ) from exc


async def _call_supplement_query_editor(
    supplement_query_editor,
    context: dict,
):
    if supplement_query_editor is None:
        return None
    try:
        if asyncio.iscoroutinefunction(supplement_query_editor):
            return await supplement_query_editor(context)
        return await asyncio.to_thread(supplement_query_editor, context)
    except Exception as exc:
        _log_info(
            "search_supplement status=ai_hint_failed "
            f"error={type(exc).__name__}"
        )
        return None


def _anchored_editor_context(
    raw_query: str,
    graph,
    *,
    intent: dict,
    locked_anchor_fact_id: str,
    provisional_candidates=(),
    stage: str,
    binding_error: str = "",
    invalid_candidates=(),
) -> dict:
    context = {
        "raw_query": _text(raw_query),
        "intent": {
            "title": _text(intent.get("title")),
            "year": _text(intent.get("year")),
            "media_type": _text(intent.get("media_type")),
            "scope": _text(intent.get("scope")),
            "season_number": _integer(intent.get("season_number")),
            "episode_number": _integer(intent.get("episode_number")),
        },
        "locked_anchor_fact_id": _text(locked_anchor_fact_id),
        "stage": stage,
        "facts": _anchored_fact_payload(graph),
        "provisional_candidates": [
            candidate.to_dict() for candidate in provisional_candidates
        ],
    }
    if binding_error:
        context["binding_error"] = _text(binding_error)
        context["invalid_candidates"] = [
            dict(candidate)
            for candidate in list(invalid_candidates or ())[:6]
            if isinstance(candidate, dict)
        ]
    return context


def _log_binding_payload(payload, stage: str) -> None:
    candidates = (
        payload.get("candidates")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(candidates, list):
        _log_info(
            "search_binding status=received "
            f"stage={stage} candidates=invalid"
        )
        return
    if not candidates:
        _log_info(
            "search_binding status=received "
            f"stage={stage} candidates=0"
        )
        return
    for index, candidate in enumerate(candidates[:6], start=1):
        if not isinstance(candidate, dict):
            _log_info(
                "search_binding status=received "
                f"stage={stage} candidate_index={index} shape=invalid"
            )
            continue
        bindings = []
        raw_bindings = candidate.get("fact_bindings")
        if isinstance(raw_bindings, list):
            bindings = [{
                "fact_id": _text(binding.get("fact_id")),
                "role": _text(binding.get("role")),
                "season": binding.get("season_number"),
                "episode": binding.get("episode_number"),
            } for binding in raw_bindings[:30] if isinstance(binding, dict)]
        _log_info(
            "search_binding status=received "
            f"stage={stage} candidate_index={index} "
            f"candidate_id={_text(candidate.get('candidate_id'))} "
            f"anchor_fact_id={_text(candidate.get('anchor_fact_id'))} "
            f"identity_role={_text(candidate.get('identity_role'))} "
            f"bindings={json.dumps(bindings, ensure_ascii=False)}"
        )


def _log_binding_result(candidates, stage: str) -> None:
    if not candidates:
        _log_info(f"search_binding status=ok stage={stage} candidates=0")
        return
    for candidate in candidates:
        facts = [{
            "fact_id": fact.fact_id,
            "provider": fact.provider,
        } for fact in candidate.facts]
        _log_info(
            "search_binding status=ok "
            f"stage={stage} candidate_id={candidate.candidate_id} "
            f"anchor_fact_id={candidate.anchor_fact_id} "
            f"identity_role={candidate.identity_role} "
            f"facts={json.dumps(facts, ensure_ascii=False)}"
        )


async def _materialize_with_binding_repair(
    *,
    candidate_editor,
    graph,
    payload,
    provider_statuses: dict[str, str],
    locked_anchor_fact_id: str,
    raw_query: str,
    intent: dict,
    provisional_candidates,
    stage: str,
    repair_state: dict,
):
    _log_binding_payload(payload, stage)
    try:
        candidates = materialize_anchored_candidates(
            graph,
            payload,
            provider_statuses=provider_statuses,
            locked_anchor_fact_id=locked_anchor_fact_id,
        )
    except CandidateBindingError as initial_error:
        error_details = dict(initial_error.details)
        details_text = (
            " details="
            + json.dumps(error_details, ensure_ascii=False, sort_keys=True)
            if error_details
            else ""
        )
        _log_warning(
            "search_binding status=invalid "
            f"stage={stage} error={initial_error.code}"
            f"{details_text}"
        )
        if initial_error.code == "duplicate_fact_id":
            reasons = [initial_error.code]
            if error_details.get("fact_id"):
                reasons.append(f"fact_id:{error_details['fact_id']}")
            raise SearchPlanningError(
                "candidate_binding_failed",
                tuple(reasons),
            ) from initial_error
        if repair_state.get("used"):
            raise SearchPlanningError(
                "candidate_binding_failed",
                (initial_error.code, "binding_repair_already_used"),
            ) from initial_error
        repair_state["used"] = True
        invalid_candidates = (
            payload.get("candidates")
            if isinstance(payload, dict)
            and isinstance(payload.get("candidates"), list)
            else []
        )
        _log_warning(
            "search_binding status=repairing "
            f"stage={stage} error={initial_error.code} "
            f"candidate_ids={json.dumps([_text(item.get('candidate_id')) for item in invalid_candidates[:6] if isinstance(item, dict)], ensure_ascii=False)}"
        )
        repaired_payload = await _call_candidate_editor(
            candidate_editor,
            _anchored_editor_context(
                raw_query,
                graph,
                intent=intent,
                locked_anchor_fact_id=locked_anchor_fact_id,
                provisional_candidates=provisional_candidates,
                stage="binding_repair",
                binding_error=initial_error.code,
                invalid_candidates=invalid_candidates,
            ),
        )
        if repaired_payload is None:
            raise SearchPlanningError(
                "candidate_binding_failed",
                (initial_error.code, "binding_repair_unavailable"),
            ) from initial_error
        _log_binding_payload(repaired_payload, "binding_repair")
        try:
            candidates = materialize_anchored_candidates(
                graph,
                repaired_payload,
                provider_statuses=provider_statuses,
                locked_anchor_fact_id=locked_anchor_fact_id,
            )
        except CandidateBindingError as repair_error:
            _log_warning(
                "search_binding status=invalid "
                f"stage=binding_repair error={repair_error.code} "
                f"initial_error={initial_error.code}"
            )
            raise SearchPlanningError(
                "candidate_binding_failed",
                tuple(dict.fromkeys((
                    initial_error.code,
                    repair_error.code,
                ))),
            ) from repair_error
        _log_binding_result(candidates, "binding_repair")
        return candidates
    _log_binding_result(candidates, stage)
    return candidates


def _supplement_title(value) -> str:
    title = unicodedata.normalize("NFKC", str(value or ""))
    title = "".join(
        character
        for character in title
        if unicodedata.category(character) != "Cf"
    )
    title = _text(title)
    title = re.sub(
        r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]\s*$",
        "",
        title,
    ).strip()
    if not title or len(title) > 160 or re.search(r"https?://", title, re.I):
        return ""
    return title


def _candidate_supplement_profile(
    candidate,
    missing_providers: set[str],
) -> dict:
    facts = tuple(candidate.facts)
    anchor = next(
        (
            fact for fact in facts
            if fact.fact_id == candidate.anchor_fact_id
        ),
        facts[0],
    )
    titles = list(dict.fromkeys(
        cleaned
        for fact in facts
        for title in (
            fact.chinese_title,
            fact.official_english_title,
            fact.romanized_original_title,
            fact.original_title,
            *fact.titles,
        )
        if (cleaned := _supplement_title(title))
    ))[:8]
    media_type = (
        "movie"
        if candidate.identity_role == "movie"
        else "series"
        if candidate.identity_role in {"series_root", "season", "episode"}
        else next(
            (
                fact.media_type
                for fact in facts
                if fact.media_type in {"movie", "series"}
            ),
            "movie_or_series",
        )
    )
    return {
        "candidate_id": candidate.candidate_id,
        "missing_providers": sorted(missing_providers),
        "titles": titles,
        "year": anchor.year or next(
            (fact.year for fact in facts if fact.year),
            "",
        ),
        "media_type": media_type,
    }


def _supplement_query_context(
    raw_query: str,
    candidates,
    missing_by_candidate: dict[str, set[str]],
) -> dict:
    return {
        "raw_query": _text(raw_query),
        "candidates": [
            _candidate_supplement_profile(
                candidate,
                missing_by_candidate.get(candidate.candidate_id, set()),
            )
            for candidate in candidates
            if missing_by_candidate.get(candidate.candidate_id)
        ],
    }


def _supplement_ai_hints(payload) -> dict[tuple[str, str], list[str]]:
    if not isinstance(payload, dict) or set(payload) != {"queries"}:
        return {}
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list):
        return {}
    result = {}
    for item in raw_queries[:18]:
        if not isinstance(item, dict) or set(item) != {
            "candidate_id",
            "provider",
            "title_hints",
        }:
            continue
        candidate_id = _text(item.get("candidate_id"))
        provider = _text(item.get("provider")).casefold()
        title_hints = item.get("title_hints")
        if (
            not candidate_id
            or provider not in _COMPLETE_CANDIDATE_PROVIDERS
            or not isinstance(title_hints, list)
        ):
            continue
        cleaned_hints = list(dict.fromkeys(
            cleaned
            for hint in title_hints[:3]
            if isinstance(hint, str)
            if (cleaned := _supplement_title(hint))
        ))
        if cleaned_hints:
            result[(candidate_id, provider)] = cleaned_hints
    return result


def _supplement_hypotheses(
    candidates,
    intent: dict,
    *,
    provider: str,
    missing_candidate_ids: set[str],
    ai_payload=None,
) -> dict:
    ai_hints = _supplement_ai_hints(ai_payload)
    hypotheses = []
    queries = []
    for candidate in candidates:
        if candidate.candidate_id not in missing_candidate_ids:
            continue
        profile = _candidate_supplement_profile(candidate, {provider})
        titles = list(dict.fromkeys([
            *ai_hints.get((candidate.candidate_id, provider), []),
            *profile["titles"],
        ]))[:8]
        _log_info(
            "search_supplement status=planned "
            f"candidate_id={candidate.candidate_id} provider={provider} "
            f"queries={json.dumps(titles, ensure_ascii=False)} "
            f"year={profile['year']} media_type={profile['media_type']}"
        )
        for title in titles:
            queries.append(title)
            hypotheses.append({
                "title": title,
                "year": profile["year"],
                "content_identity": profile["media_type"],
                "scope": (
                    candidate.intended_scope
                    or intent.get("scope")
                    or "movie_or_series"
                ),
                "season_number": intent.get("season_number"),
                "episode_number": intent.get("episode_number"),
                "explicit_facts": [],
                "inferred_facts": ["candidate_source_supplement"],
            })
    return {
        "status": "ok",
        "intent": dict(intent),
        "hypotheses": hypotheses,
        "source_queries": {provider: list(dict.fromkeys(queries))},
        "warnings": ["candidate_source_supplement"],
    }


def _anchored_fact_snapshot(candidate) -> list[dict]:
    return [
        {
            "fact_id": fact.fact_id,
            "stable_fact_id": fact.stable_fact_id or fact.fact_id,
            "provider": fact.provider,
            "titles": list(fact.titles),
            "year": fact.year,
            "media_type": fact.media_type,
            "external_ids": dict(fact.external_ids),
            "source_url": fact.source_url,
            "poster_url": fact.poster_url,
            "original_title": fact.original_title,
            "original_language": fact.original_language,
            "official_english_title": fact.official_english_title,
            "romanized_original_title": fact.romanized_original_title,
            "chinese_title": fact.chinese_title,
            "poster_language": fact.poster_language,
            "genres": list(fact.genres),
            "episodes": [dict(item) for item in fact.episodes],
            "complex_signals": list(fact.complex_signals),
        }
        for fact in candidate.facts
    ]


_COMPLETE_CANDIDATE_PROVIDERS = frozenset({
    "wikipedia",
    "douban",
    "tvdb",
})


def _candidate_version(candidate) -> str:
    return (
        "v1"
        if _COMPLETE_CANDIDATE_PROVIDERS.issubset(candidate.providers)
        else "v0"
    )


def _fact_from_snapshot(raw: dict) -> EvidenceFact:
    return EvidenceFact(
        fact_id=_text(raw.get("fact_id")),
        stable_fact_id=(
            _text(raw.get("stable_fact_id"))
            or _text(raw.get("fact_id"))
        ),
        provider=_text(raw.get("provider")).casefold(),
        titles=tuple(
            _text(item)
            for item in (raw.get("titles") or ())
            if _text(item)
        ),
        year=_text(raw.get("year")),
        media_type=_text(raw.get("media_type")).casefold(),
        external_ids=dict(raw.get("external_ids") or {}),
        source_url=_text(raw.get("source_url")),
        poster_url=_text(raw.get("poster_url")),
        original_title=_text(raw.get("original_title")),
        original_language=_text(raw.get("original_language")),
        official_english_title=_text(raw.get("official_english_title")),
        romanized_original_title=_text(
            raw.get("romanized_original_title")
        ),
        chinese_title=_text(raw.get("chinese_title")),
        poster_language=_text(raw.get("poster_language")),
        genres=tuple(
            _text(item)
            for item in (raw.get("genres") or ())
            if _text(item)
        ),
        episodes=tuple(
            dict(item)
            for item in (raw.get("episodes") or ())
            if isinstance(item, dict)
        ),
        complex_signals=tuple(
            _text(item)
            for item in (raw.get("complex_signals") or ())
            if _text(item)
        ),
    )


def _candidate_from_frozen_snapshot(candidate: dict):
    facts = tuple(
        _fact_from_snapshot(raw)
        for raw in (candidate.get("fact_snapshot") or ())
        if isinstance(raw, dict) and _text(raw.get("fact_id"))
    )
    graph = SearchGraph((
        CandidateEntity(
            _text(candidate.get("candidate_id")) or "selected-candidate",
            facts,
        ),
    ))
    bindings = [{
        "fact_id": _text(link.get("fact_id")),
        "role": _text(link.get("role")),
        "season_number": link.get("proposed_season_number")
        if link.get("verification") == "unresolved_scope_link"
        else link.get("season_number"),
        "episode_number": link.get("proposed_episode_number")
        if link.get("verification") == "unresolved_scope_link"
        else link.get("episode_number"),
    } for link in (candidate.get("source_links") or ()) if isinstance(link, dict)]
    payload = {
        "status": "resolved",
        "candidates": [{
            "candidate_id": _text(candidate.get("candidate_id")),
            "anchor_fact_id": _text(candidate.get("anchor_fact_id")),
            "identity_role": _text(candidate.get("identity_role")),
            "intended_scope": _text(candidate.get("intended_scope")),
            "fact_bindings": bindings,
            "ai_confidence": float(candidate.get("ai_confidence") or 0),
            "ai_reason": (
                _text(candidate.get("ai_reason"))
                or "User selected this request-scoped candidate."
            ),
        }],
    }
    statuses = {
        _text(link.get("provider")).casefold(): "ok"
        for link in (candidate.get("source_links") or ())
        if isinstance(link, dict) and _text(link.get("provider"))
    }
    materialized = materialize_anchored_candidates(
        graph,
        payload,
        provider_statuses=statuses,
        locked_anchor_fact_id=_text(candidate.get("anchor_fact_id")),
    )
    return graph, materialized[0]


def _selected_candidate_result(candidate: dict, enriched) -> dict:
    result = deepcopy(candidate)
    result.update({
        "anchor_fact_id": enriched.anchor_fact_id,
        "identity_role": enriched.identity_role,
        "intended_scope": enriched.intended_scope,
        "poster_url": enriched.primary_poster_url,
        "poster_assets": [
            poster.to_dict() for poster in enriched.poster_assets
        ],
        "source_links": [
            link.to_dict() for link in enriched.source_links
        ],
        "unresolved_sources": list(enriched.unresolved_sources),
        "ai_confidence": enriched.ai_confidence,
        "ai_reason": enriched.ai_reason,
        "reasons": [enriched.ai_reason],
        "candidate_version": _candidate_version(enriched),
        "fact_snapshot": _anchored_fact_snapshot(enriched),
    })
    return result


def _supplement_fact_is_compatible(
    fact: EvidenceFact,
    selected,
    *,
    queried_titles,
) -> bool:
    selected_types = {
        item.media_type for item in selected.facts if item.media_type
    }
    if (
        fact.media_type
        and selected_types
        and fact.media_type not in selected_types
    ):
        return False
    selected_years = {
        item.year for item in selected.facts if item.year
    }
    if fact.year and selected_years and fact.year not in selected_years:
        return False
    if any(
        key in other.external_ids
        and value
        and value == other.external_ids[key]
        for other in selected.facts
        for key, value in fact.external_ids.items()
    ):
        return True
    allowed_titles = {
        normalized
        for item in selected.facts
        for normalized in item.normalized_titles
    }
    allowed_titles.update(
        normalized
        for title in queried_titles
        if (normalized := normalize_title(title))
    )
    return bool(fact.normalized_titles.intersection(allowed_titles))


async def supplement_selected_candidate(
    candidate: dict,
    raw_query: str,
    providers: dict[str, Callable],
    *,
    candidate_editor,
    supplement_query_editor=None,
) -> dict:
    """Fill missing sources for one user-selected candidate only."""

    if not candidate.get("links_frozen"):
        return deepcopy(candidate)
    try:
        _base_graph, selected = _candidate_from_frozen_snapshot(candidate)
    except (CandidateBindingError, TypeError, ValueError) as exc:
        _log_warning(
            "search_supplement status=skipped "
            f"stage=selected_candidate error={type(exc).__name__}"
        )
        return deepcopy(candidate)

    missing = {
        provider
        for provider in _COMPLETE_CANDIDATE_PROVIDERS - selected.providers
        if provider in providers
    }
    if not missing:
        return deepcopy(candidate)

    missing_by_candidate = {selected.candidate_id: missing}
    ai_payload = await _call_supplement_query_editor(
        supplement_query_editor,
        _supplement_query_context(
            raw_query,
            (selected,),
            missing_by_candidate,
        ),
    )
    intent = {
        "title": _text(raw_query),
        "year": "",
        "media_type": (
            "movie"
            if selected.identity_role == "movie"
            else "series"
            if selected.identity_role in {"series_root", "season", "episode"}
            else ""
        ),
        "scope": selected.intended_scope,
        "season_number": None,
        "episode_number": None,
    }
    supplement_sources = []
    queried_titles_by_provider = {}
    for provider in sorted(missing):
        hypotheses = _supplement_hypotheses(
            (selected,),
            intent,
            provider=provider,
            missing_candidate_ids={selected.candidate_id},
            ai_payload=ai_payload,
        )
        queried_titles_by_provider[provider] = tuple(
            (hypotheses.get("source_queries") or {}).get(provider) or ()
        )
        supplement_sources.extend(await collect_evidence(
            hypotheses,
            {provider: providers[provider]},
        ))

    supplement_graph = _build_logged_discovery_graph(
        supplement_sources,
        stage="selected_supplement",
    )
    raw_supplement_facts = tuple(
        fact
        for entity in supplement_graph.candidates
        for fact in entity.facts
    )
    supplement_facts = tuple(
        fact
        for fact in raw_supplement_facts
        if _supplement_fact_is_compatible(
            fact,
            selected,
            queried_titles=queried_titles_by_provider.get(
                fact.provider,
                (),
            ),
        )
    )
    for fact in raw_supplement_facts:
        if fact not in supplement_facts:
            _log_info(
                "search_supplement status=rejected "
                f"stage=selected_candidate "
                f"candidate_id={selected.candidate_id} "
                f"provider={fact.provider} "
                f"fact_id={fact.stable_fact_id or fact.fact_id} "
                "reason=identity_incompatible"
            )
    if not supplement_facts:
        _log_info(
            "search_supplement status=no_new_facts "
            f"stage=selected_candidate candidate_id={selected.candidate_id}"
        )
        return deepcopy(candidate)

    combined_graph = SearchGraph(
        (CandidateEntity(
            selected.candidate_id,
            (*selected.facts, *supplement_facts),
        ),),
        supplement_graph.fact_merges,
    )
    payload = await _call_candidate_editor(
        candidate_editor,
        _anchored_editor_context(
            raw_query,
            combined_graph,
            intent=intent,
            locked_anchor_fact_id=selected.anchor_fact_id,
            provisional_candidates=(selected,),
            stage="selected_supplement",
        ),
    )
    statuses = {
        link.provider: "ok"
        for link in selected.source_links
    }
    supplement_statuses, _support = _provider_status_and_support(
        supplement_sources
    )
    statuses.update(supplement_statuses)
    try:
        enriched = materialize_anchored_candidates(
            combined_graph,
            payload,
            provider_statuses=statuses,
            locked_anchor_fact_id=selected.anchor_fact_id,
        )
    except CandidateBindingError as exc:
        _log_warning(
            "search_supplement status=binding_failed "
            f"stage=selected_candidate candidate_id={selected.candidate_id} "
            f"error={exc.code}"
        )
        return deepcopy(candidate)
    if (
        len(enriched) != 1
        or enriched[0].candidate_id != selected.candidate_id
    ):
        _log_warning(
            "search_supplement status=identity_rejected "
            f"stage=selected_candidate candidate_id={selected.candidate_id}"
        )
        return deepcopy(candidate)
    _log_info(
        "search_supplement status=accepted "
        f"stage=selected_candidate candidate_id={selected.candidate_id} "
        f"providers={json.dumps(sorted(enriched[0].providers))}"
    )
    return _selected_candidate_result(candidate, enriched[0])


def _candidate_preview_metadata(
    candidate,
    *,
    metadata_id: str,
    raw_query: str,
    metadata_error: MetadataV1Error,
) -> dict:
    facts = tuple(candidate.facts)
    anchor = next(
        (
            fact for fact in facts
            if fact.fact_id == candidate.anchor_fact_id
        ),
        facts[0],
    )
    media_type = (
        "movie"
        if candidate.identity_role == "movie"
        else "series"
        if candidate.identity_role in {"series_root", "season", "episode"}
        else next(
            (
                fact.media_type for fact in facts
                if fact.media_type in {"movie", "series"}
            ),
            "",
        )
    )
    chinese_title = next(
        (
            fact.chinese_title for fact in facts
            if fact.chinese_title
        ),
        "",
    )
    official_english = next(
        (
            fact.official_english_title for fact in facts
            if fact.official_english_title
        ),
        "",
    )
    original_title = next(
        (
            fact.original_title for fact in facts
            if fact.original_title
        ),
        "",
    )
    romanized = next(
        (
            fact.romanized_original_title for fact in facts
            if fact.romanized_original_title
        ),
        "",
    )
    fallback_title = next(
        (
            title for fact in facts for title in fact.titles
            if _text(title)
        ),
        _text(raw_query) or "未知",
    )
    display_title = (
        chinese_title
        or official_english
        or romanized
        or original_title
        or fallback_title
    )
    year = anchor.year or next(
        (fact.year for fact in facts if fact.year),
        "",
    )
    animation = any(
        signal in _text(genre).casefold()
        for fact in facts
        for genre in fact.genres
        for signal in ("animation", "animated", "anime", "动画", "動畫")
    )
    external_ids = {}
    for fact in facts:
        external_ids.update(dict(fact.external_ids))
    provider_statuses = {
        link.provider: "ok" for link in candidate.source_links
    }
    for unresolved in candidate.unresolved_sources:
        provider, separator, status = unresolved.partition(":")
        if separator and provider in _COMPLETE_CANDIDATE_PROVIDERS:
            provider_statuses[provider] = status
    scope = (
        "movie"
        if media_type == "movie"
        else candidate.intended_scope or "whole_series"
    )
    return {
        "schema_version": 1,
        "metadata_id": _text(metadata_id),
        "confirmed": False,
        "identity": {
            "chinese_title": chinese_title or display_title,
            "english_title": official_english or romanized or original_title,
            "official_english_title": official_english,
            "romanized_original_title": romanized,
            "original_title": original_title,
            "original_language": next(
                (
                    fact.original_language for fact in facts
                    if fact.original_language
                ),
                "",
            ),
            "aliases": list(dict.fromkeys(
                title for fact in facts for title in fact.titles
                if _text(title)
            )),
            "year": year,
            "content_kind": media_type,
            "poster_url": candidate.primary_poster_url,
            "external_ids": external_ids,
            "root_fact_id": anchor.fact_id,
        },
        "retrieval": {
            "media_type": media_type,
            "scope": scope,
            "query": "",
            "queries": [],
        },
        "placement": {
            "library_type": media_type,
            "category_kind": (
                f"{'animated' if animation else 'live_action'}_{media_type}"
                if media_type
                else ""
            ),
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "unresolved",
            "mapping_source": "anchored_candidate_preview",
            "tvdb_episode_id": "",
        },
        "items": [],
        "evidence": {
            "anchor_fact_id": candidate.anchor_fact_id,
            "source_links": [
                link.to_dict() for link in candidate.source_links
            ],
            "poster_assets": [
                poster.to_dict() for poster in candidate.poster_assets
            ],
            "provider_statuses": provider_statuses,
            "unresolved": list(candidate.unresolved_sources),
            "ai": {
                "confidence": candidate.ai_confidence,
                "reason": candidate.ai_reason,
            },
            "decision": {
                "mode": "ai_fact_binding_preview",
                "scope": scope,
                "season_number": None,
                "episode_number": None,
            },
        },
        "warnings": [
            "warning:candidate_v0",
            f"warning:{metadata_error.code}",
        ],
    }


async def _build_anchored_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    *,
    candidate_editor,
    supplement_query_editor,
    locked_identity: tuple[str, str] | None,
) -> dict:
    binding_repair_state = {"used": False}
    hypotheses = build_rule_hypotheses(raw_query)
    sources = await collect_evidence(hypotheses, providers)
    graph = _build_logged_discovery_graph(sources, stage="discovery")
    if not _anchored_fact_payload(graph):
        failure_code, hard_failures = _no_fact_failure(sources)
        if failure_code:
            raise SearchPlanningError(
                failure_code,
                hard_failures,
            )
        recovery = await asyncio.to_thread(
            infer_search_hypotheses_with_ai,
            {
                "raw_query": raw_query,
                "intent": hypotheses.get("intent") or {},
                "failure": "zero_provider_facts",
            },
        )
        if recovery is None:
            raise SearchPlanningError(
                "ai_candidate_failure",
                ("zero_provider_facts",),
            )
        sources = _merge_evidence_passes(
            sources,
            await collect_evidence(recovery, providers),
        )
        graph = _build_logged_discovery_graph(
            sources,
            stage="ai_recovery",
        )
        if not _anchored_fact_payload(graph):
            failure_code, hard_failures = _no_fact_failure(sources)
            if failure_code:
                raise SearchPlanningError(
                    failure_code,
                    hard_failures,
                )
            raise SearchPlanningError("no_match")

    intent = dict(hypotheses.get("intent") or {})
    intent["media_type"] = _explicit_media_type(raw_query, intent)
    anchor_fact_id = _locked_anchor_fact_id(graph, locked_identity)
    statuses, _support = _provider_status_and_support(sources)
    payload = await _call_candidate_editor(
        candidate_editor,
        _anchored_editor_context(
            raw_query,
            graph,
            intent=intent,
            locked_anchor_fact_id=anchor_fact_id,
            stage="discovery",
        ),
    )
    if payload is None:
        raise SearchPlanningError("ai_candidate_failure")
    anchored = await _materialize_with_binding_repair(
        candidate_editor=candidate_editor,
        graph=graph,
        payload=payload,
        provider_statuses=statuses,
        locked_anchor_fact_id=anchor_fact_id,
        raw_query=raw_query,
        intent=intent,
        provisional_candidates=(),
        stage="discovery",
        repair_state=binding_repair_state,
    )
    if not anchored:
        raise SearchPlanningError("no_match")

    ranked = []
    for index, candidate in enumerate(anchored):
        metadata_ready = True
        metadata_error = {}
        try:
            contract = build_media_metadata_v1(
                candidate,
                metadata_id=plan_id,
                raw_query=raw_query,
            )
        except MetadataV1Error as exc:
            metadata_ready = False
            metadata_error = {
                "code": exc.code,
                "missing_fields": list(exc.missing_fields),
            }
            _log_warning(
                "search_metadata status=incomplete "
                f"candidate_id={candidate.candidate_id} "
                f"code={exc.code} "
                "missing_fields="
                f"{json.dumps(list(exc.missing_fields), ensure_ascii=False)}"
            )
            contract = _candidate_preview_metadata(
                candidate,
                metadata_id=plan_id,
                raw_query=raw_query,
                metadata_error=exc,
            )
        else:
            _log_info(
                "search_metadata status=ready "
                f"candidate_id={candidate.candidate_id} "
                f"anchor_fact_id={candidate.anchor_fact_id}"
            )
        queries = list(
            (contract.get("retrieval") or {}).get("queries") or []
        )
        confidence_score = round(candidate.ai_confidence * 100)
        ranked.append({
            "candidate_key": candidate.candidate_id,
            "candidate_id": candidate.candidate_id,
            "anchor_fact_id": candidate.anchor_fact_id,
            "identity_role": candidate.identity_role,
            "intended_scope": candidate.intended_scope,
            "score": {
                "version": "anchored-candidate-v1",
                "program_total": 0,
                "ai_total": confidence_score,
                "total": confidence_score,
            },
            "recommended": index == 0,
            "selectable": True,
            "media_metadata": contract,
            "prowlarr_queries": queries,
            "poster_url": candidate.primary_poster_url,
            "poster_assets": [
                poster.to_dict() for poster in candidate.poster_assets
            ],
            "source_links": [
                link.to_dict() for link in candidate.source_links
            ],
            "unresolved_sources": list(candidate.unresolved_sources),
            "ai_confidence": candidate.ai_confidence,
            "ai_reason": candidate.ai_reason,
            "reasons": [candidate.ai_reason],
            "candidate_version": _candidate_version(candidate),
            "metadata_ready": metadata_ready,
            "metadata_error": metadata_error,
            "links_frozen": True,
            "fact_snapshot": _anchored_fact_snapshot(candidate),
            "entity_snapshot": {
                "entity_key": candidate.candidate_id,
                "content_kind": (
                    (contract.get("identity") or {}).get("content_kind")
                ),
                "external_ids": dict(
                    (contract.get("identity") or {}).get(
                        "external_ids"
                    ) or {}
                ),
            },
            "relation_snapshot": {
                "relation_type": "standalone",
                "mapping_kind": "standalone",
            },
        })
    top = ranked[0]
    _log_info(
        f"search_plan status=anchored metadata_id={plan_id} "
        f"candidates={len(ranked)}"
    )
    return {
        "plan_id": plan_id,
        "raw_query": raw_query,
        "entry_kind": "link" if locked_identity else "text",
        "links_frozen": True,
        "media_metadata": deepcopy(top["media_metadata"]),
        "prowlarr_queries": list(top["prowlarr_queries"]),
        "candidates": ranked,
        "source_queries": _actual_source_queries(sources),
        "scoring_version": "anchored-candidate-v1",
        "relation_pool": [],
    }


async def build_confirmable_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
    *,
    budget: PlanningBudget | None = None,
    locked_identity: tuple[str, str] | None = None,
    source_gateway=None,
    source_orchestrator=orchestrate_sources,
    candidate_editor=None,
    supplement_query_editor=None,
) -> dict:
    # occupied_loader/allocator are applied only after an interactive selection;
    # no unselected candidate may reserve a persistent or logical episode slot.
    del occupied_loader, allocator
    if candidate_editor is not None:
        return await _build_anchored_search_plan(
            raw_query,
            plan_id,
            providers,
            candidate_editor=candidate_editor,
            supplement_query_editor=supplement_query_editor,
            locked_identity=locked_identity,
        )
    budget = budget or PlanningBudget()
    parsed_input = classify_search_input(raw_query)
    if parsed_input.kind in {"invalid_link", "unsupported_text"}:
        raise SearchPlanningError(parsed_input.reason)
    rule_hypotheses = build_rule_hypotheses(raw_query)
    orchestrated = False
    sources = []
    candidates = []
    all_candidates = []
    intent = {}
    orchestration = None
    intent_fallback_attempted = False
    verified_ai_title = ""
    if source_gateway is not None and locked_identity is None:
        orchestration = await _optional_budgeted(
            "source_orchestration",
            budget,
            source_orchestrator(
                raw_query,
                source_gateway,
            ),
            None,
        )
        if (
            orchestration is not None
            and getattr(orchestration, "status", "fallback") != "fallback"
            and getattr(orchestration, "decision", None) is not None
        ):
            sources = [
                dict(item)
                for item in (getattr(orchestration, "sources", ()) or ())
                if isinstance(item, dict)
            ]
            graph = _build_logged_search_graph(
                sources,
                stage="source_orchestration",
            )
            graph = merge_verified_equivalence_edges(
                graph,
                orchestration.decision.equivalence_edges,
            )
            all_candidates = list(graph.candidates)
            candidates = list(all_candidates)
            if locked_identity:
                key, value = locked_identity
                candidates = [
                    candidate
                    for candidate in candidates
                    if _text(candidate.external_ids.get(key)) == _text(value)
                ]
            intent = _orchestrated_intent(
                getattr(orchestration, "intent", {}) or {},
                rule_hypotheses.get("intent") or {},
                raw_query,
            )
            source_clarification = _source_media_type_clarification_plan(
                plan_id=plan_id,
                raw_query=raw_query,
                intent=intent,
                candidates=candidates,
            )
            if source_clarification is not None:
                return source_clarification
            if getattr(orchestration, "status", "") == "ambiguous":
                clarification = _ai_clarification_plan(
                    plan_id=plan_id,
                    raw_query=raw_query,
                    rule_intent=rule_hypotheses.get("intent") or {},
                    payload={
                        "status": "needs_clarification",
                        "intent_hint": (
                            getattr(orchestration, "intent", {}) or {}
                        ),
                        "clarification_reason": (
                            "来源证据对应多个媒体类型，"
                            "请选择后继续验证。"
                        ),
                    },
                )
                if clarification is not None:
                    return clarification
            intent, episode_parent_key = _resolve_episode_title_intent(
                raw_query,
                intent,
                candidates,
            )
            if episode_parent_key:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.candidate_key == episode_parent_key
                ]
            orchestrated = True
            _log_info(
                "search_stage status=orchestrated "
                f"targeted_rounds={getattr(orchestration, 'targeted_rounds', 0)} "
                f"candidates={len(candidates)}"
            )
        elif orchestration is not None:
            _log_info(
                "search_stage status=fallback stage=source_orchestration "
                f"reason={getattr(orchestration, 'fallback_reason', '')}"
            )

    if not orchestrated:
        sources = await _budgeted(
            "base_evidence",
            budget,
            collect_evidence(rule_hypotheses, providers),
        )
        graph = _build_logged_search_graph(sources, stage="base_evidence")
        all_candidates = list(graph.candidates)
        candidates = list(all_candidates)
        if locked_identity:
            key, value = locked_identity
            candidates = [
                candidate
                for candidate in candidates
                if _text(candidate.external_ids.get(key)) == _text(value)
            ]
        target = normalize_title(
            (rule_hypotheses.get("intent") or {}).get("title")
        )
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
        rule_intent = dict(rule_hypotheses.get("intent") or {})
        prefer_exact = bool(
            exact
            and (
                _text(rule_intent.get("scope")).casefold()
                in {"whole_series", "season", "episode"}
                or _text(rule_intent.get("year"))
                or _explicit_media_type(raw_query, rule_intent)
            )
        )
        if exact or title_matches:
            candidates = exact if prefer_exact else title_matches
        else:
            candidates = []
        if has_ambiguous_bare_number(raw_query, parsed_input) and not exact:
            raise SearchPlanningError("ambiguous_numeric_role")
        if not candidates:
            intent_fallback_attempted = True
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
                clarification = _ai_clarification_plan(
                    plan_id=plan_id,
                    raw_query=raw_query,
                    rule_intent=rule_intent,
                    payload=ai_hypotheses,
                )
                if clarification is not None:
                    return clarification
                retry_sources = await _optional_budgeted(
                    "candidate_finalize",
                    budget,
                    collect_evidence(ai_hypotheses, providers),
                    [],
                )
                if retry_sources:
                    sources = _merge_evidence_passes(
                        sources,
                        retry_sources,
                    )
                    retry_graph = _build_logged_search_graph(
                        sources,
                        stage="intent_fallback",
                    )
                    all_candidates = list(retry_graph.candidates)
                    candidates = list(all_candidates)
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
                    verified_ai_title = _verified_ai_title(
                        ai_hypotheses,
                        candidates,
                    )
        intent = dict(rule_hypotheses.get("intent") or {})
        if verified_ai_title:
            intent["title"] = verified_ai_title
        intent["media_type"] = _explicit_media_type(raw_query, intent)
        source_clarification = _source_media_type_clarification_plan(
            plan_id=plan_id,
            raw_query=raw_query,
            intent=intent,
            candidates=(
                all_candidates
                if not locked_identity
                else candidates
            ),
            locked_identity=locked_identity,
        )
        if source_clarification is not None:
            return source_clarification

    if not candidates:
        raise SearchPlanningError("insufficient_independent_support")

    verified_relations = {}

    combined = []
    title_values = {}
    rejected = _candidate_rejection_counts()
    for candidate in candidates:
        reason = _candidate_qualification_reason(
            candidate,
            intent,
            direct_anchor=bool(locked_identity),
        )
        if reason:
            rejected[reason] += 1
            continue
        try:
            title_values[candidate.candidate_key] = resolve_title_policy(
                candidate,
                preferred_chinese_title=intent.get("title") or "",
            )
        except TitlePolicyError:
            rejected["title_policy"] += 1
            continue
        program = program_score(
            candidate,
            intent,
            verified_relations.get(candidate.candidate_key),
        )
        combined.append(combine_score(candidate.candidate_key, program))
    _log_candidate_funnel(
        phase="initial",
        raw_count=len(all_candidates),
        title_matched=len(candidates),
        qualified=len(combined),
        rejected=rejected,
    )
    ranked_scores = _selectable_thresholds(combined)
    if not ranked_scores and not orchestrated:
        expansion_sources = await _optional_budgeted(
            "candidate_finalize",
            budget,
            collect_evidence(
                _expanded_hypotheses(candidates, intent),
                providers,
            ),
            [],
        )
        if expansion_sources:
            sources = _merge_evidence_passes(sources, expansion_sources)
            expanded_graph = _build_logged_search_graph(
                sources,
                stage="candidate_expansion",
            )
            candidates = [
                _expanded_candidate(candidate, expanded_graph.candidates)
                for candidate in candidates
            ]
            combined = []
            rejected = _candidate_rejection_counts()
            for candidate in candidates:
                reason = _candidate_qualification_reason(
                    candidate,
                    intent,
                    direct_anchor=bool(locked_identity),
                )
                if reason:
                    rejected[reason] += 1
                    continue
                try:
                    resolved_titles = resolve_title_policy(
                        candidate,
                        preferred_chinese_title=intent.get("title") or "",
                    )
                except TitlePolicyError:
                    rejected["title_policy"] += 1
                    continue
                title_values[candidate.candidate_key] = resolved_titles
                combined.append(
                    combine_score(
                        candidate.candidate_key,
                        program_score(
                            candidate,
                            intent,
                            verified_relations.get(candidate.candidate_key),
                        ),
                    )
                )
            _log_candidate_funnel(
                phase="expanded",
                raw_count=len(expanded_graph.candidates),
                title_matched=len(candidates),
                qualified=len(combined),
                rejected=rejected,
            )
            ranked_scores = _selectable_thresholds(combined)
            title_values = {}
            for candidate in candidates:
                if any(
                    item.candidate_key == candidate.candidate_key
                    for item in ranked_scores
                ):
                    title_values[candidate.candidate_key] = (
                        resolve_title_policy(
                            candidate,
                            preferred_chinese_title=intent.get("title") or "",
                        )
                    )
            _log_info(
                f"search_stage status=expanded stage=candidate_finalize "
                f"candidates={len(candidates)}"
            )

    if (
        not ranked_scores
        and not orchestrated
        and not locked_identity
        and not intent_fallback_attempted
    ):
        intent_fallback_attempted = True
        ai_hypotheses = await _optional_budgeted(
            "intent_fallback",
            budget,
            asyncio.to_thread(
                infer_search_hypotheses_with_ai,
                {
                    "raw_query": raw_query,
                    "intent": dict(intent),
                    "failure": "lexical_candidates_failed_qualification",
                },
            ),
            None,
        )
        if ai_hypotheses:
            clarification = _ai_clarification_plan(
                plan_id=plan_id,
                raw_query=raw_query,
                rule_intent=intent,
                payload=ai_hypotheses,
            )
            if clarification is not None:
                return clarification
            retry_sources = await _optional_budgeted(
                "candidate_finalize",
                budget,
                collect_evidence(ai_hypotheses, providers),
                [],
            )
            if retry_sources:
                sources = _merge_evidence_passes(sources, retry_sources)
                recovered_graph = _build_logged_search_graph(
                    sources,
                    stage="qualification_recovery",
                )
                all_candidates = list(recovered_graph.candidates)
                retry_targets = {
                    normalize_title(item.get("title"))
                    for item in ai_hypotheses.get("hypotheses") or []
                    if isinstance(item, dict)
                    and normalize_title(item.get("title"))
                }
                candidates = [
                    candidate
                    for candidate in recovered_graph.candidates
                    if any(
                        title.startswith(retry_target)
                        or retry_target.startswith(title)
                        for retry_target in retry_targets
                        for title in candidate.normalized_titles
                    )
                ]
                verified_ai_title = _verified_ai_title(
                    ai_hypotheses,
                    candidates,
                )
                if verified_ai_title:
                    intent["title"] = verified_ai_title
                source_clarification = _source_media_type_clarification_plan(
                    plan_id=plan_id,
                    raw_query=raw_query,
                    intent=intent,
                    candidates=candidates,
                )
                if source_clarification is not None:
                    return source_clarification
                combined = []
                title_values = {}
                rejected = _candidate_rejection_counts()
                for candidate in candidates:
                    reason = _candidate_qualification_reason(
                        candidate,
                        intent,
                        direct_anchor=False,
                    )
                    if reason:
                        rejected[reason] += 1
                        continue
                    try:
                        title_values[candidate.candidate_key] = (
                            resolve_title_policy(
                                candidate,
                                preferred_chinese_title=(
                                    intent.get("title") or ""
                                ),
                            )
                        )
                    except TitlePolicyError:
                        rejected["title_policy"] += 1
                        continue
                    combined.append(
                        combine_score(
                            candidate.candidate_key,
                            program_score(
                                candidate,
                                intent,
                                verified_relations.get(
                                    candidate.candidate_key
                                ),
                            ),
                        )
                    )
                _log_candidate_funnel(
                    phase="ai_typo_recovery",
                    raw_count=len(recovered_graph.candidates),
                    title_matched=len(candidates),
                    qualified=len(combined),
                    rejected=rejected,
                )
                ranked_scores = _selectable_thresholds(combined)
                if ranked_scores:
                    _log_info(
                        "search_stage status=recovered "
                        "stage=ai_typo_recovery "
                        f"candidates={len(candidates)}"
                    )

    if not orchestrated and ranked_scores:
        candidates_by_key = {
            candidate.candidate_key: candidate for candidate in candidates
        }
        score_candidates = [
            candidates_by_key[item.candidate_key]
            for item in ranked_scores
            if item.candidate_key in candidates_by_key
        ]
        ai_payload = await _optional_budgeted(
            "candidate_finalize",
            budget,
            asyncio.to_thread(
                infer_candidate_scorecard_with_ai,
                _candidate_score_context(raw_query, intent, score_candidates),
            ),
            None,
        )
        ai_scores = _validated_candidate_ai_scores(
            ai_payload,
            score_candidates,
        )
        ranked_scores = _selectable_thresholds([
            combine_score(
                item.candidate_key,
                item.program,
                ai_scores.get(item.candidate_key),
            )
            for item in ranked_scores
        ])
    if not ranked_scores:
        if rejected["missing_scope"]:
            raise SearchPlanningError("tvdb_scope_not_verified")
        raise SearchPlanningError("insufficient_independent_support")

    by_key = {item.candidate_key: item for item in candidates}
    ranked = []
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
        contract["evidence"]["decision"]["mode"] = (
            "ai_tool_orchestrated"
            if orchestrated
            else "deterministic_bounded"
        )
        if orchestrated and orchestration is not None:
            contract["evidence"]["decision"]["targeted_rounds"] = int(
                getattr(orchestration, "targeted_rounds", 0)
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
            "ai_total": score.ai.total if score.ai else 0,
            "ai_dimensions": {
                "title_equivalence": (
                    score.ai.title_equivalence if score.ai else 0
                ),
                "intent_relevance": (
                    score.ai.intent_relevance if score.ai else 0
                ),
                "relation_consistency": (
                    score.ai.relation_consistency if score.ai else 0
                ),
            },
            "ai_fact_ids": list(score.ai.fact_ids) if score.ai else [],
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
        "source_queries": (
            _actual_source_queries(sources)
            if orchestrated
            else deepcopy(rule_hypotheses.get("source_queries") or {})
        ),
        "scoring_version": SCORING_VERSION,
        "relation_pool": [
            entry
            for candidate in all_candidates
            if (entry := _relation_pool_entry(candidate)) is not None
        ],
    }

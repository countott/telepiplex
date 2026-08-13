"""Repeatable live audit helpers for the deterministic Search pipeline."""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from pathlib import Path

from telepiplex_plugin_sdk.media_metadata import (
    attach_media_metadata,
    extract_confirmed_media_metadata,
)

from .entity_graph import normalize_title
from .candidate_hydration import hydrate_frozen_candidate
from .input_contract import classify_search_input
from .prowlarr_query import build_prowlarr_query_chain
from .search_plan import confirm_media_metadata
from .series_scope import SeriesScopeError, apply_series_scope
from .work_discovery import build_root_work_search_plan, discover_root_works


_MEDIA_TYPES = {"movie", "series"}
_SCOPES = {"work", "season", "episode"}
_REQUIRED_KEYS = {
    "case_id",
    "query",
    "expected_titles",
    "year",
    "media_type",
    "country_group",
    "scope",
    "season_number",
    "episode_number",
    "ambiguity_group",
    "single_season",
    "multi_season",
    "full_pipeline",
    "japanese_animation",
}


class LivePipelineCorpusError(ValueError):
    pass


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _positive_integer(value, *, field: str, required: bool) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LivePipelineCorpusError(f"invalid_{field}")
    return value


def load_real_media_corpus(path: str | Path) -> list[dict]:
    """Load and validate literal real-work expectations for live audits."""

    corpus_path = Path(path)
    try:
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LivePipelineCorpusError("corpus_unreadable") from exc
    if not isinstance(payload, list) or not payload:
        raise LivePipelineCorpusError("corpus_invalid")

    result = []
    seen_ids = set()
    for raw in payload:
        if not isinstance(raw, dict) or set(raw) != _REQUIRED_KEYS:
            raise LivePipelineCorpusError("case_shape_invalid")
        case_id = _text(raw.get("case_id"))
        query = _text(raw.get("query"))
        year = _text(raw.get("year"))
        media_type = _text(raw.get("media_type")).casefold()
        country_group = _text(raw.get("country_group"))
        scope = _text(raw.get("scope")).casefold()
        expected_titles = raw.get("expected_titles")
        if (
            not case_id
            or case_id in seen_ids
            or not query
            or not (len(year) == 4 and year.isdigit())
            or media_type not in _MEDIA_TYPES
            or not country_group
            or scope not in _SCOPES
            or not isinstance(expected_titles, list)
            or not expected_titles
            or any(not _text(item) for item in expected_titles)
        ):
            raise LivePipelineCorpusError("case_value_invalid")
        booleans = {
            key: raw.get(key)
            for key in (
                "single_season",
                "multi_season",
                "full_pipeline",
                "japanese_animation",
            )
        }
        if any(not isinstance(value, bool) for value in booleans.values()):
            raise LivePipelineCorpusError("case_boolean_invalid")
        if media_type == "movie" and (
            booleans["single_season"] or booleans["multi_season"]
        ):
            raise LivePipelineCorpusError("movie_season_flag_invalid")
        if booleans["single_season"] and booleans["multi_season"]:
            raise LivePipelineCorpusError("season_flags_conflict")
        season_number = _positive_integer(
            raw.get("season_number"),
            field="season_number",
            required=scope in {"season", "episode"},
        )
        episode_number = _positive_integer(
            raw.get("episode_number"),
            field="episode_number",
            required=scope == "episode",
        )
        if scope == "work" and (season_number is not None or episode_number is not None):
            raise LivePipelineCorpusError("work_scope_coordinates_invalid")
        if scope == "season" and episode_number is not None:
            raise LivePipelineCorpusError("season_scope_episode_invalid")

        seen_ids.add(case_id)
        result.append({
            **raw,
            "case_id": case_id,
            "query": query,
            "expected_titles": [_text(item) for item in expected_titles],
            "year": year,
            "media_type": media_type,
            "country_group": country_group,
            "scope": scope,
            "season_number": season_number,
            "episode_number": episode_number,
            "ambiguity_group": _text(raw.get("ambiguity_group")),
            **booleans,
        })
    return result


def _provider_lookup_with_retry(callable_, value, attempts: int = 3):
    last_error = None
    result = None
    for attempt in range(max(1, attempts)):
        try:
            result = callable_(value)
        except Exception as exc:
            last_error = exc
        else:
            status = (
                _text(result.get("status")).casefold()
                if isinstance(result, dict)
                else ""
            )
            if status not in {"server_down", "timeout", "rate_limited"}:
                return result
        if attempt + 1 < max(1, attempts):
            import time
            time.sleep(min(1.0, 0.25 * (attempt + 1)))
    if last_error is not None:
        raise last_error
    return result


def _candidate_titles(candidate: dict) -> set[str]:
    identity = (candidate.get("media_metadata") or {}).get("identity") or {}
    values = (
        candidate.get("display_title"),
        candidate.get("chinese_title"),
        candidate.get("english_title"),
        *(candidate.get("aliases") or ()),
        identity.get("chinese_title"),
        identity.get("english_title"),
        identity.get("official_english_title"),
        *(identity.get("aliases") or ()),
    )
    return {
        normalize_title(value)
        for value in values
        if _text(value)
    }


def _expected_candidate(case: dict, candidates: list[dict]) -> dict | None:
    expected = {
        normalize_title(value)
        for value in case.get("expected_titles") or ()
        if _text(value)
    }
    year = _text(case.get("year"))
    media_type = _text(case.get("media_type")).casefold()
    for candidate in candidates:
        identity = (candidate.get("media_metadata") or {}).get("identity") or {}
        candidate_year = _text(candidate.get("year") or identity.get("year"))[:4]
        candidate_type = _text(
            candidate.get("media_type") or identity.get("content_kind")
        ).casefold()
        if candidate_year != year or candidate_type != media_type:
            continue
        if expected & _candidate_titles(candidate):
            return candidate
    return None


def _failed_report(case: dict, stages: dict, code: str, **extra) -> dict:
    return {
        "case_id": _text(case.get("case_id")),
        "passed": False,
        "failure_code": code,
        "stages": stages,
        **extra,
    }


def audit_root_case(
    case: dict,
    *,
    wikipedia_lookup,
    wikidata_lookup,
    wikidata_search=None,
) -> dict:
    """Audit deterministic parsing and expected Wikipedia/Wikidata root recall."""

    stages = {
        "input": "pending",
        "wikipedia": "pending",
        "wikidata": "pending",
        "root_match": "pending",
    }
    parsed = classify_search_input(_text(case.get("query")))
    if parsed.kind != "text":
        stages["input"] = "failed"
        return _failed_report(case, stages, parsed.reason or "invalid_query")
    stages["input"] = "ok"

    wikipedia_calls = []
    wikidata_calls = []

    def tracked_wikipedia(payload):
        wikipedia_calls.append(deepcopy(payload))
        try:
            result = _provider_lookup_with_retry(
                wikipedia_lookup,
                payload,
            )
        except Exception as exc:
            stages["wikipedia"] = type(exc).__name__
            return {
                "source": "wikipedia",
                "status": "server_down",
                "facts": [],
            }
        stages["wikipedia"] = (
            "ok"
            if isinstance(result, dict) and result.get("status") == "ok"
            else _text((result or {}).get("status")) or "failed"
        )
        return result

    def tracked_wikidata(qids):
        wikidata_calls.append(list(qids))
        try:
            result = _provider_lookup_with_retry(
                wikidata_lookup,
                qids,
            )
        except Exception as exc:
            stages["wikidata"] = type(exc).__name__
            return {}
        stages["wikidata"] = "ok" if isinstance(result, dict) else "failed"
        return result

    roots = discover_root_works(
        parsed,
        tracked_wikipedia,
        tracked_wikidata,
        wikidata_search=wikidata_search,
    )
    if stages["wikipedia"] == "pending":
        stages["wikipedia"] = "not_called"
    if stages["wikidata"] == "pending":
        stages["wikidata"] = "not_called"
    matched = _expected_candidate(case, roots)
    if matched is None:
        stages["root_match"] = "failed"
        return _failed_report(
            case,
            stages,
            "expected_root_not_found",
            candidate_qids=[_text(item.get("qid")) for item in roots],
            wikipedia_calls=wikipedia_calls,
            wikidata_calls=wikidata_calls,
        )
    stages["root_match"] = "ok"
    return {
        "case_id": _text(case.get("case_id")),
        "passed": True,
        "failure_code": "",
        "matched_qid": _text(matched.get("qid")),
        "matched_title": _text(matched.get("display_title")),
        "stages": stages,
        "candidate_qids": [_text(item.get("qid")) for item in roots],
        "wikipedia_calls": wikipedia_calls,
        "wikidata_calls": wikidata_calls,
    }


def _apply_requested_scope(case: dict, contract: dict) -> dict:
    if _text(case.get("media_type")).casefold() != "series":
        return deepcopy(contract)
    scope = _text(case.get("scope")).casefold()
    if scope == "work":
        scope = "whole_series"
    return apply_series_scope(
        contract,
        scope,
        season_number=case.get("season_number"),
        episode_number=case.get("episode_number"),
    )


def audit_full_case(
    case: dict,
    *,
    wikipedia_lookup,
    wikidata_lookup,
) -> dict:
    """Audit a frozen root through scope, query, confirmation, and SDK handoff."""

    stages = {
        "input": "pending",
        "wikipedia": "pending",
        "wikidata": "pending",
        "root_match": "pending",
        "candidate_contract": "pending",
        "scope": "pending",
        "query": "pending",
        "downstream_contract": "pending",
    }
    root_report = audit_root_case(
        case,
        wikipedia_lookup=wikipedia_lookup,
        wikidata_lookup=wikidata_lookup,
    )
    stages.update(root_report["stages"])
    if not root_report["passed"]:
        return _failed_report(case, stages, root_report["failure_code"])
    try:
        plan = build_root_work_search_plan(
            _text(case.get("query")),
            f"audit:{_text(case.get('case_id'))}",
            wikipedia_lookup,
            wikidata_lookup,
        )
    except Exception as exc:
        return _failed_report(case, stages, f"plan:{type(exc).__name__}")
    candidate = _expected_candidate(case, plan.get("candidates") or [])
    if candidate is None:
        return _failed_report(case, stages, "expected_plan_candidate_not_found")
    if candidate.get("metadata_ready") is not True:
        stages["candidate_contract"] = "failed"
        return _failed_report(
            case,
            stages,
            _text((candidate.get("metadata_error") or {}).get("code"))
            or "candidate_metadata_not_ready",
        )
    stages["candidate_contract"] = "ok"
    try:
        scoped = _apply_requested_scope(case, candidate["media_metadata"])
    except SeriesScopeError as exc:
        stages["scope"] = "failed"
        return _failed_report(case, stages, f"scope:{exc}")
    stages["scope"] = "ok"
    retrieval = scoped.get("retrieval") or {}
    queries = build_prowlarr_query_chain(
        scoped,
        _text(case.get("query")),
    )
    if not queries:
        stages["query"] = "failed"
        return _failed_report(case, stages, "retrieval_query_missing")
    stages["query"] = "ok"
    scoped.setdefault("retrieval", {})["queries"] = list(queries)
    selected_plan = {
        "plan_id": f"audit:{_text(case.get('case_id'))}",
        "media_metadata": scoped,
        "prowlarr_queries": list(queries),
    }
    try:
        confirmed = confirm_media_metadata(selected_plan)
        payload = attach_media_metadata({}, confirmed)
        extracted = extract_confirmed_media_metadata(payload)
    except ValueError as exc:
        stages["downstream_contract"] = "failed"
        return _failed_report(case, stages, f"downstream:{exc}")
    if extracted is None:
        stages["downstream_contract"] = "failed"
        return _failed_report(case, stages, "downstream_round_trip_failed")
    stages["downstream_contract"] = "ok"
    return {
        "case_id": _text(case.get("case_id")),
        "passed": True,
        "failure_code": "",
        "matched_qid": root_report["matched_qid"],
        "retrieval_scope": _text((extracted.get("retrieval") or {}).get("scope")),
        "queries": queries,
        "sdk_metadata_id": _text(extracted.get("metadata_id")),
        "stages": stages,
    }


async def audit_live_full_case(
    case: dict,
    feature,
    *,
    wikipedia_lookup=None,
    wikidata_lookup=None,
    wikidata_search=None,
) -> dict:
    """Run one live case through frozen exact-read and downstream handoff."""

    stages = {
        "input": "pending",
        "wikipedia": "pending",
        "wikidata": "pending",
        "root_match": "pending",
        "metadata_supplement": "pending",
        "exact_read": "pending",
        "scope": "pending",
        "query": "pending",
        "downstream_contract": "pending",
    }
    try:
        plan = await asyncio.to_thread(
            build_root_work_search_plan,
            _text(case.get("query")),
            f"live-audit:{_text(case.get('case_id'))}",
            wikipedia_lookup or feature._wikipedia_provider,
            wikidata_lookup,
            wikidata_search,
        )
    except Exception as exc:
        stages["input"] = "failed"
        return _failed_report(case, stages, f"plan:{type(exc).__name__}:{exc}")
    stages["input"] = "ok"
    stages["wikipedia"] = "ok"
    stages["wikidata"] = "ok"
    candidate = _expected_candidate(case, plan.get("candidates") or [])
    if candidate is None:
        stages["root_match"] = "failed"
        return _failed_report(case, stages, "expected_plan_candidate_not_found")
    stages["root_match"] = "ok"
    try:
        supplemented = await feature._supplement_selected_candidate(
            candidate,
            _text(case.get("query")),
        )
    except Exception as exc:
        stages["metadata_supplement"] = "failed"
        return _failed_report(
            case,
            stages,
            f"supplement:{type(exc).__name__}:{exc}",
        )
    stages["metadata_supplement"] = "ok"
    try:
        hydrated = await asyncio.to_thread(
            hydrate_frozen_candidate,
            supplemented,
            metadata_id=f"live-audit:{_text(case.get('case_id'))}",
            raw_query=_text(case.get("query")),
            require_anchor=True,
        )
    except Exception as exc:
        if (
            _text(case.get("scope")).casefold() == "episode"
            and "metadata_incomplete:verified_scope" in str(exc)
        ):
            stages["exact_read"] = "safe_rejected"
            return {
                "case_id": _text(case.get("case_id")),
                "passed": True,
                "outcome": "safe_rejection",
                "failure_code": "",
                "reason_code": "episode_inventory_unavailable",
                "queries": [],
                "stages": stages,
            }
        stages["exact_read"] = "failed"
        return _failed_report(
            case,
            stages,
            f"exact_read:{type(exc).__name__}:{exc}",
        )
    stages["exact_read"] = "ok"
    try:
        scoped = _apply_requested_scope(case, hydrated["media_metadata"])
    except SeriesScopeError as exc:
        stages["scope"] = "failed"
        return _failed_report(case, stages, f"scope:{exc}")
    stages["scope"] = "ok"
    queries = build_prowlarr_query_chain(
        scoped,
        _text(case.get("query")),
    )
    if not queries:
        stages["query"] = "failed"
        return _failed_report(case, stages, "retrieval_query_missing")
    scoped.setdefault("retrieval", {})["query"] = queries[0]
    scoped["retrieval"]["queries"] = list(queries)
    stages["query"] = "ok"
    try:
        confirmed = confirm_media_metadata({
            "plan_id": f"live-audit:{_text(case.get('case_id'))}",
            "media_metadata": scoped,
            "prowlarr_queries": list(queries),
        })
        extracted = extract_confirmed_media_metadata(
            attach_media_metadata({}, confirmed)
        )
    except ValueError as exc:
        stages["downstream_contract"] = "failed"
        return _failed_report(case, stages, f"downstream:{exc}")
    if extracted is None:
        stages["downstream_contract"] = "failed"
        return _failed_report(case, stages, "downstream_round_trip_failed")
    stages["downstream_contract"] = "ok"
    return {
        "case_id": _text(case.get("case_id")),
        "passed": True,
        "failure_code": "",
        "matched_qid": _text(
            ((extracted.get("identity") or {}).get("external_ids") or {}).get(
                "wikidata"
            )
            or ((extracted.get("identity") or {}).get("external_ids") or {}).get(
                "wikipedia"
            )
        ),
        "retrieval_scope": _text((extracted.get("retrieval") or {}).get("scope")),
        "queries": list(queries),
        "sdk_metadata_id": _text(extracted.get("metadata_id")),
        "providers": sorted(
            {
                _text(item.get("provider"))
                for item in (extracted.get("evidence") or {}).get("source_links") or ()
                if isinstance(item, dict) and _text(item.get("provider"))
            }
        ),
        "warnings": list(extracted.get("warnings") or ()),
        "stages": stages,
    }

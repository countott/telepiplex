"""Repeatable live audit helpers for the deterministic Search pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from telepiplex_plugin_sdk.media_metadata import (
    attach_media_metadata,
    extract_confirmed_media_metadata,
)

from .entity_graph import normalize_title
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
        # This deterministic audit validates contract/query round-tripping
        # before the live exact-read stage supplies episode inventory. Runtime
        # resource search keeps the conservative aggregate gate enabled.
        allow_incomplete_aggregate=True,
    )


def audit_full_case(
    case: dict,
    *,
    wikipedia_lookup,
    wikidata_lookup,
) -> dict:
    """Legacy component check for contract/query round-tripping, not business success."""

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


def _business_report(case, outcome, reason, stages, **details):
    expected = case.get("expected_outcome", "business_success")
    return {
        "case_id": _text(case.get("case_id")), "outcome": outcome,
        "passed": outcome == expected and outcome != "skipped",
        "reason_code": reason, "failure_code": "" if outcome == expected else reason,
        "stages": dict(stages), **details,
    }


async def audit_live_full_case(case: dict, feature, **unused) -> dict:
    """Drive actual Search command/callback state; only a capture Host is allowed.

    External provider fixtures belong at adapter boundaries, never at planning,
    hydration, confirmation, gating or submission methods. This routine does not
    execute a download, and refuses an arbitrary/installed Host transport.
    """
    from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2
    from .audit_transport import AuditHost, AuditRuntime

    stages = {name: "pending" for name in (
        "command", "candidate_confirmation", "scope", "release_selection", "download_capture")}
    if not isinstance(getattr(feature, "host", None), AuditHost):
        return _business_report(case, "skipped", "capture_host_required", stages)
    if not isinstance(getattr(feature, "runtime", None), AuditRuntime):
        return _business_report(case, "skipped", "audit_runtime_required", stages)
    host, runtime = feature.host, feature.runtime
    request = {"chat_id": 91001, "user_id": 91001}
    callback_trace = []
    candidate_ids = []
    selected_identity = None

    async def callback(payload):
        callback_trace.append(payload.split(":", 1)[0])
        result = await feature.callback({**request, "payload": payload})
        await runtime.drain()
        return result

    def report(outcome, reason, **extra):
        return _business_report(case, outcome, reason, stages,
            candidate_identities=candidate_ids, callback_trace=callback_trace,
            submission_count=len(host.submissions), **extra)

    try:
        await feature.command({**request, "command": "s", "args": [case["query"]]})
        await runtime.drain()
        stages["command"] = "ok"
        if not feature.plans:
            return report("source_failure", "discovery_unavailable")
        plan_id, stored = next(iter(feature.plans.items()))
        candidates = list(stored.get("candidates") or ())
        candidate_ids = sorted(_text(c.get("qid") or (
            ((c.get("media_metadata") or {}).get("identity") or {}).get("external_ids", {}).get("wikidata")))
            for c in candidates)
        candidate = _expected_candidate(case, candidates)
        if candidate is None:
            outcome = "source_failure" if stored.get("kind") == "planning_failure" else "unexpected_failure"
            return report(outcome, "expected_candidate_unavailable")
        selected_identity = deepcopy(candidate["media_metadata"]["identity"])
        if case.get("scenario") == "cancel":
            result = await callback(f"cancel:{plan_id}")
            assert result["operation"]["state"] == "cancelled"
            assert plan_id not in feature.plans and not host.submissions
            stages["candidate_confirmation"] = "cancelled"
            return report("safe_rejection", "user_cancelled")
        await callback(f"select:{plan_id}:{candidates.index(candidate)}")
        if not stored.get("selected_candidate"):
            stages["candidate_confirmation"] = "rejected"
            return report("safe_rejection", "metadata_not_verified")
        stages["candidate_confirmation"] = "ok"
        # Scope callbacks go through the same public entry point as Telegram.
        # Explicit Sxx/SxxExx may already have started the release search.
        if not stored.get("confirmed_contract") and plan_id in feature.plans:
            kind = case.get("scope", "work")
            scope = "whole_series" if kind == "work" else kind
            suffix = ""
            if kind in {"season", "episode"}:
                suffix += ":" + str(case["season_number"])
            if kind == "episode":
                suffix += ":" + str(case["episode_number"])
            await callback(f"scope:{plan_id}:{scope}{suffix}")
        contract = stored.get("confirmed_contract")
        if not contract:
            reason = "category_route_missing" if not stored.get("selected_path") else "scope_not_verified"
            return report("safe_rejection", reason)
        assert validate_media_metadata_v2(contract, require_confirmed=True) is not None, "v2_invalid"
        identity = contract["identity"]
        assert str(identity["year"]) == str(case["year"]), "year_changed"
        assert identity["media_type"] == case["media_type"], "media_type_changed"
        assert identity["provider_refs"].get("wikidata") == selected_identity.get("external_ids", {}).get("wikidata"), "frozen_identity_changed"
        expected_scope = {"kind": "movie" if case["media_type"] == "movie" else
            "whole_series" if case.get("scope", "work") == "work" else case["scope"],
            "season_number": case.get("season_number"), "episode_number": case.get("episode_number")}
        assert contract["scope"] == expected_scope, "scope_changed"
        stages["scope"] = "ok"
        releases = stored.get("release_by_id") or {}
        if not releases:
            failed = any(item.get("stage") == "prowlarr_recovery" for item in host.reports)
            return report("source_failure" if failed else "safe_rejection",
                          "release_source_unavailable" if failed else "no_eligible_release")
        release_id, release = next(iter(releases.items()))
        stages["release_selection"] = "ok"
        if case.get("scenario") == "restart":
            from .service import SearchFeature
            restarted = SearchFeature(config=deepcopy(feature.config), host=host)
            restarted.bind_runtime(runtime)
            result = await restarted.callback({**request, "payload": f"release:{plan_id}:{release_id}"})
            await runtime.drain()
            assert result.get("session", {}).get("state") == "close", "stale_session_open"
            assert not host.submissions, "stale_session_submitted"
            return report("safe_rejection", "session_expired_after_restart")
        await callback(f"release:{plan_id}:{release_id}")
        if not host.submissions and release_id not in (stored.get("release_by_id") or {}):
            return report("safe_rejection", "release_magnet_unavailable")
        assert len(host.submissions) == 1, "submission_count_invalid"
        payload, options = host.submissions[0]
        assert payload["media_metadata"] == contract, "handoff_metadata_changed"
        assert payload["release"] == {"title": release.get("title") or "",
            "indexer": release.get("indexer") or "", "size": release.get("size") or 0}, "handoff_release_changed"
        assert payload["selected_path"] == stored["selected_path"], "handoff_path_changed"
        assert options["idempotency_key"] == f"{plan_id}:release:{release_id}", "idempotency_key_changed"
        assert payload["operation_id"] == stored["operation_id"], "operation_changed"
        frozen = deepcopy(payload)
        before = len(host.submissions)
        await callback(f"release:{plan_id}:{release_id}")
        assert len(host.submissions) == before, "duplicate_submission"
        stages["download_capture"] = "ok"
        # Reports intentionally omit magnet, credentials, and live indexer URLs.
        frozen.pop("link", None)
        if host.lose_submit_response:
            return report("source_failure", "submission_response_lost",
                          duplicate_submission_count=len(host.submissions) - before)
        return report("business_success", "", submission=frozen,
            idempotency_key=options["idempotency_key"], release_id=release_id,
            duplicate_submission_count=len(host.submissions) - before,
            eligible_release_ids=sorted(releases),
            queries=list(stored.get("active_prowlarr_queries") or ()))
    except SeriesScopeError:
        return report("safe_rejection", "scope_not_verified")
    except Exception as exc:
        # Error type only: exception messages may contain provider credentials.
        return report("unexpected_failure", str(exc) if isinstance(exc, AssertionError) and str(exc).isidentifier() else type(exc).__name__)
    finally:
        await runtime.close(feature)


async def run_business_case(case: dict, *, mode="offline", config=None, allow_network=False) -> dict:
    """Create an isolated Feature and capture Host for a single sequential case."""
    from contextlib import ExitStack
    from .audit_transport import AuditHost, AuditRuntime, FixtureProviders, OfflineNetworkGuard, audit_config
    from .context import runtime_context
    from .service import SearchFeature

    if mode not in {"offline", "public", "prowlarr"}:
        raise ValueError("invalid_audit_mode")
    if mode != "offline" and not allow_network:
        return _business_report(case, "skipped", "explicit_network_opt_in_required", {})
    fixture = FixtureProviders(case, scenario=case.get("scenario", "success"))
    if mode == "offline" and fixture.match is None:
        return _business_report(case, "skipped", "offline_fixture_unavailable", {})
    current_config = deepcopy(config if config is not None else audit_config())
    if mode == "prowlarr":
        prowlarr = (current_config.get("search") or {}).get("prowlarr") or {}
        if not prowlarr.get("base_url") or not prowlarr.get("api_key"):
            return _business_report(case, "skipped", "prowlarr_credentials_missing", {})
    if mode == "public" and fixture.match is None:
        return _business_report(case, "skipped", "capture_release_fixture_unavailable", {})
    if case.get("scenario") == "missing_directory":
        current_config["category_folder"] = []
    previous_config = runtime_context.config
    previous_logger = runtime_context.logger
    import logging
    audit_logger = logging.Logger("telepiplex.search.audit")
    audit_logger.addHandler(logging.NullHandler())
    audit_logger.propagate = False
    observed_prowlarr_calls = []
    failed_exact_reads = []
    with ExitStack() as stack:
        guard = stack.enter_context(OfflineNetworkGuard()) if mode == "offline" else None
        if mode == "offline":
            stack.enter_context(fixture.active())
        elif mode == "public":
            from unittest.mock import patch
            stack.enter_context(patch("telepiplex_search.service.search_prowlarr", fixture.releases))
            stack.enter_context(patch("telepiplex_search.service.list_prowlarr_indexers", lambda: []))
            stack.enter_context(patch("telepiplex_search.service.get_prowlarr_indexer_summary", lambda: {}))
        if mode == "prowlarr":
            from unittest.mock import patch
            from . import service
            for name in ("search_prowlarr", "search_prowlarr_indexer", "list_prowlarr_indexers", "get_prowlarr_indexer_summary"):
                original = getattr(service, name)
                def observe(*args, _original=original, _name=name, **kwargs):
                    observed_prowlarr_calls.append(_name)
                    return _original(*args, **kwargs)
                stack.enter_context(patch.object(service, name, observe))
        from unittest.mock import patch
        from . import direct_link
        for name in ("lookup_wikipedia_page", "lookup_wikipedia_episode_page", "enrich_wikidata_entities"):
            original = getattr(direct_link, name)
            def observe_exact(*args, _original=original, _name=name, **kwargs):
                try:
                    value = _original(*args, **kwargs)
                except Exception:
                    failed_exact_reads.append(_name)
                    raise
                if isinstance(value, dict) and value.get("status") in {
                    "timeout", "rate_limited", "server_down", "unavailable"}:
                    failed_exact_reads.append(_name)
                return value
            stack.enter_context(patch.object(direct_link, name, observe_exact))
        runtime_context.configure(current_config)
        runtime_context.logger = audit_logger
        try:
            host = AuditHost(lose_submit_response=case.get("scenario") == "submit_response_loss")
            feature = SearchFeature(config=current_config, host=host,
                # Do not follow Prowlarr download URLs: GET can trigger a grab.
                release_resolver=lambda release: release.get("magnet_url") or "")
            feature.bind_runtime(AuditRuntime())
            result = await audit_live_full_case(case, feature)
            if failed_exact_reads and result.get("reason_code") in {
                "metadata_not_verified", "scope_not_verified"}:
                result.update(outcome="source_failure",
                    passed=case.get("expected_outcome") == "source_failure",
                    reason_code="metadata_source_unavailable", failure_code=("" if case.get("expected_outcome") == "source_failure" else "metadata_source_unavailable"))
            if guard is not None and guard.attempts:
                result.update(outcome="unexpected_failure", passed=False, reason_code="offline_network_attempt", failure_code="offline_network_attempt")
            result["mode"] = mode
            result["data_origin"] = "simulated_provider_fixtures" if mode == "offline" else "live_metadata"
            result["provider_calls"] = list(fixture.calls) + observed_prowlarr_calls
            result["prowlarr_called"] = bool(observed_prowlarr_calls)
            return result
        finally:
            runtime_context.configure(previous_config)
            runtime_context.logger = previous_logger

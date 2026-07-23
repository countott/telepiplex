"""Fail-closed validation for AI decisions over request-scoped facts."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entity_graph import EvidenceFact, SearchGraph


class EvidenceVerificationError(ValueError):
    def __init__(self, code: str):
        self.code = str(code or "ai_output_invalid")
        super().__init__(self.code)


@dataclass(frozen=True)
class VerifiedEquivalenceEdge:
    left_fact_id: str
    right_fact_id: str
    relation: str
    reason: str


@dataclass(frozen=True)
class VerifiedCandidateAssessment:
    candidate_key: str
    supporting_fact_ids: tuple[str, ...]
    conflicting_fact_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class VerifiedAiDecision:
    status: str
    intent: dict
    equivalence_edges: tuple[VerifiedEquivalenceEdge, ...]
    candidate_assessments: tuple[VerifiedCandidateAssessment, ...]
    recommended_next_action: str


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _string_list(
    value,
    *,
    allow_empty: bool = True,
    maximum: int = 100,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise EvidenceVerificationError("ai_output_invalid")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise EvidenceVerificationError("ai_output_invalid")
        item = _text(item)
        if not item or len(item) > 500:
            raise EvidenceVerificationError("ai_output_invalid")
        if item not in result:
            result.append(item)
    if not result and not allow_empty:
        raise EvidenceVerificationError("ai_output_invalid")
    return tuple(result)


def _optional_positive_integer(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvidenceVerificationError("ai_output_invalid")
    return value


def _verified_intent(value) -> dict:
    expected = {
        "title_hints",
        "media_type_hint",
        "year_hint",
        "scope",
        "season_number",
        "episode_number",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceVerificationError("ai_output_invalid")
    media_type = _text(value.get("media_type_hint")).casefold()
    scope = _text(value.get("scope")).casefold()
    year = _text(value.get("year_hint"))
    if media_type not in {"movie", "series", "unknown"}:
        raise EvidenceVerificationError("ai_output_invalid")
    if scope not in {"work", "whole_series", "season", "episode", "unknown"}:
        raise EvidenceVerificationError("ai_output_invalid")
    if year and not re.fullmatch(r"(?:19|20)\d{2}", year):
        raise EvidenceVerificationError("ai_output_invalid")
    return {
        "title_hints": list(
            _string_list(value.get("title_hints"), allow_empty=False, maximum=3)
        ),
        "media_type_hint": media_type,
        "year_hint": year,
        "scope": scope,
        "season_number": _optional_positive_integer(
            value.get("season_number")
        ),
        "episode_number": _optional_positive_integer(
            value.get("episode_number")
        ),
    }


def _fact_conflicts(left: EvidenceFact, right: EvidenceFact) -> bool:
    if left.year and right.year and left.year != right.year:
        return True
    if (
        left.media_type
        and right.media_type
        and left.media_type != right.media_type
    ):
        return True
    for key in set(left.external_ids).intersection(right.external_ids):
        left_value = _text(left.external_ids.get(key))
        right_value = _text(right.external_ids.get(key))
        if left_value and right_value and left_value != right_value:
            return True
    return False


def validate_orchestrator_output(
    payload,
    graph: SearchGraph,
) -> VerifiedAiDecision:
    expected = {
        "status",
        "intent",
        "equivalence_edges",
        "candidate_assessments",
        "recommended_next_action",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise EvidenceVerificationError("ai_output_invalid")
    status = _text(payload.get("status")).casefold()
    action = _text(payload.get("recommended_next_action")).casefold()
    if status not in {"resolved", "ambiguous", "insufficient_evidence"}:
        raise EvidenceVerificationError("ai_output_invalid")
    if action not in {"confirm", "clarify", "stop"}:
        raise EvidenceVerificationError("ai_output_invalid")
    if status == "resolved" and action != "confirm":
        raise EvidenceVerificationError("ai_output_invalid")
    if status == "ambiguous" and action != "clarify":
        raise EvidenceVerificationError("ai_output_invalid")
    if status == "insufficient_evidence" and action == "confirm":
        raise EvidenceVerificationError("ai_output_invalid")
    intent = _verified_intent(payload.get("intent"))

    candidates = list((graph or SearchGraph(())).candidates)
    candidate_by_key = {
        candidate.candidate_key: candidate for candidate in candidates
    }
    fact_by_id = {
        fact.fact_id: fact
        for candidate in candidates
        for fact in candidate.facts
    }
    candidate_for_fact = {
        fact.fact_id: candidate.candidate_key
        for candidate in candidates
        for fact in candidate.facts
    }

    raw_edges = payload.get("equivalence_edges")
    if not isinstance(raw_edges, list) or len(raw_edges) > 21:
        raise EvidenceVerificationError("ai_output_invalid")
    edges = []
    seen_edges = set()
    for raw in raw_edges:
        if not isinstance(raw, dict) or set(raw) != {
            "left_fact_id",
            "right_fact_id",
            "relation",
            "reason",
        }:
            raise EvidenceVerificationError("ai_output_invalid")
        left_id = _text(raw.get("left_fact_id"))
        right_id = _text(raw.get("right_fact_id"))
        if left_id not in fact_by_id or right_id not in fact_by_id:
            raise EvidenceVerificationError("unknown_fact_id")
        if (
            not left_id
            or left_id == right_id
            or _text(raw.get("relation")) != "same_entity"
        ):
            raise EvidenceVerificationError("ai_output_invalid")
        left = fact_by_id[left_id]
        right = fact_by_id[right_id]
        if (
            left.provider == right.provider
            or candidate_for_fact[left_id] == candidate_for_fact[right_id]
        ):
            raise EvidenceVerificationError("ai_output_invalid")
        if _fact_conflicts(left, right):
            raise EvidenceVerificationError("hard_fact_conflict")
        reason = _text(raw.get("reason"))
        if not reason or len(reason) > 500:
            raise EvidenceVerificationError("ai_output_invalid")
        key = tuple(sorted((left_id, right_id)))
        if key in seen_edges:
            raise EvidenceVerificationError("ai_output_invalid")
        seen_edges.add(key)
        edges.append(VerifiedEquivalenceEdge(
            left_id,
            right_id,
            "same_entity",
            reason,
        ))

    raw_assessments = payload.get("candidate_assessments")
    if not isinstance(raw_assessments, list):
        raise EvidenceVerificationError("candidate_assessment_mismatch")
    actual_keys = []
    assessments = []
    for raw in raw_assessments:
        if not isinstance(raw, dict) or set(raw) != {
            "candidate_key",
            "supporting_fact_ids",
            "conflicting_fact_ids",
            "reason",
        }:
            raise EvidenceVerificationError("ai_output_invalid")
        candidate_key = _text(raw.get("candidate_key"))
        candidate = candidate_by_key.get(candidate_key)
        if candidate is None:
            raise EvidenceVerificationError("unknown_candidate_key")
        supporting = _string_list(
            raw.get("supporting_fact_ids"),
            allow_empty=False,
        )
        conflicting = _string_list(raw.get("conflicting_fact_ids"))
        if (
            not set(supporting).issubset(fact_by_id)
            or not set(conflicting).issubset(fact_by_id)
        ):
            raise EvidenceVerificationError("unknown_fact_id")
        candidate_fact_ids = {fact.fact_id for fact in candidate.facts}
        if not set(supporting).intersection(candidate_fact_ids):
            raise EvidenceVerificationError("ai_output_invalid")
        if set(supporting).intersection(conflicting):
            raise EvidenceVerificationError("ai_output_invalid")
        reason = _text(raw.get("reason"))
        if not reason or len(reason) > 500:
            raise EvidenceVerificationError("ai_output_invalid")
        actual_keys.append(candidate_key)
        assessments.append(VerifiedCandidateAssessment(
            candidate_key,
            supporting,
            conflicting,
            reason,
        ))
    expected_keys = set(candidate_by_key)
    if (
        len(actual_keys) != len(set(actual_keys))
        or set(actual_keys) != expected_keys
    ):
        raise EvidenceVerificationError("candidate_assessment_mismatch")

    return VerifiedAiDecision(
        status,
        intent,
        tuple(edges),
        tuple(assessments),
        action,
    )

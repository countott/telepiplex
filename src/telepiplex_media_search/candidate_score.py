"""Fixed, versioned candidate scoring. No runtime weight learning."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .entity_graph import CandidateEntity


SCORING_VERSION = "media-entity-v1"
RECOMMENDED_SCORE = 85
MINIMUM_SCORE = 65
RECOMMENDED_LEAD = 10


def _text(value) -> str:
    return " ".join(str(value or "").split()).casefold()


@dataclass(frozen=True)
class ProgramScore:
    stable_identity: int
    independent_sources: int
    release_consistency: int
    type_and_scope: int
    excluded: bool = False
    reason_codes: tuple[str, ...] = ()
    version: str = SCORING_VERSION

    @property
    def total(self) -> int:
        if self.excluded:
            return 0
        return min(60, sum((
            self.stable_identity,
            self.independent_sources,
            self.release_consistency,
            self.type_and_scope,
        )))


@dataclass(frozen=True)
class AIScore:
    title_equivalence: int
    relation_consistency: int
    intent_relevance: int
    fact_ids: tuple[str, ...]

    @property
    def total(self) -> int:
        return min(40, sum((
            self.title_equivalence,
            self.relation_consistency,
            self.intent_relevance,
        )))


@dataclass(frozen=True)
class CandidateScore:
    candidate_key: str
    program: ProgramScore
    ai: AIScore
    total: int
    recommended: bool = False
    selectable: bool = False


class ScorecardError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _shared_stable_identity(candidate: CandidateEntity) -> int:
    occurrences: dict[tuple[str, str], set[str]] = {}
    for fact in candidate.facts:
        for key, value in fact.external_ids.items():
            value = _text(value)
            if value:
                occurrences.setdefault((_text(key), value), set()).add(fact.provider)
    if any(len(providers) >= 2 for providers in occurrences.values()):
        return 25
    if occurrences and len(candidate.providers) >= 2:
        return 20
    if occurrences:
        return 15
    if len(candidate.providers) >= 2 and len(candidate.years) == 1 and len(candidate.media_types) == 1:
        return 10
    return 5


def _independent_source_score(candidate: CandidateEntity) -> int:
    count = len(candidate.providers)
    if count >= 3:
        return 15
    if count == 2:
        return 10
    return 5 if count == 1 else 0


def _release_score(candidate: CandidateEntity, intent: dict) -> int:
    years = candidate.years
    requested = _text(intent.get("year"))
    if not years:
        return 2
    if requested:
        return 10 if requested in {_text(year) for year in years} else 0
    return 10 if len(years) == 1 else 3


def _type_scope_score(candidate: CandidateEntity, intent: dict) -> int:
    media_types = candidate.media_types
    requested = _text(intent.get("media_type"))
    if len(media_types) != 1:
        return 0 if media_types else 3
    actual = next(iter(media_types))
    if requested and requested == actual:
        scope = _text(intent.get("scope"))
        if scope and scope not in {"movie_or_series", ""}:
            return 10
        return 7
    if requested:
        return 0
    return 7


def program_score(
    candidate: CandidateEntity,
    intent: dict | None,
    relation: dict | None,
) -> ProgramScore:
    del relation  # relation facts affect the fixed type/scope input, not its weights.
    intent = intent if isinstance(intent, dict) else {}
    reasons = []
    requested_type = _text(intent.get("media_type"))
    if requested_type in {"movie", "series"} and candidate.media_types and requested_type not in candidate.media_types:
        reasons.append("explicit_type_conflict")
    if not candidate.facts:
        reasons.append("no_auditable_facts")
    return ProgramScore(
        stable_identity=_shared_stable_identity(candidate),
        independent_sources=_independent_source_score(candidate),
        release_consistency=_release_score(candidate, intent),
        type_and_scope=_type_scope_score(candidate, intent),
        excluded=bool(reasons),
        reason_codes=tuple(reasons),
    )


_COMPONENTS = {
    "title_equivalence": 20,
    "relation_consistency": 10,
    "intent_relevance": 10,
}


def validate_ai_scorecard(payload: object, valid_fact_ids: set[str]) -> AIScore:
    if not isinstance(payload, dict):
        raise ScorecardError("invalid_scorecard")
    allowed = {"candidate_key", "reasons", *_COMPONENTS}
    if set(payload).difference(allowed):
        raise ScorecardError("unexpected_scorecard_field")
    if not _text(payload.get("candidate_key")):
        raise ScorecardError("candidate_key_missing")
    scores = {}
    referenced = []
    for name, maximum in _COMPONENTS.items():
        component = payload.get(name)
        if not isinstance(component, dict) or set(component).difference({"score", "fact_ids", "reason"}):
            raise ScorecardError("invalid_score_component")
        score = component.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= maximum:
            raise ScorecardError("score_out_of_range")
        fact_ids = component.get("fact_ids")
        if not isinstance(fact_ids, list) or any(not isinstance(item, str) for item in fact_ids):
            raise ScorecardError("invalid_fact_ids")
        if score and not fact_ids:
            raise ScorecardError("missing_fact_reference")
        for fact_id in fact_ids:
            if fact_id not in valid_fact_ids:
                raise ScorecardError("unknown_fact_id")
            if fact_id not in referenced:
                referenced.append(fact_id)
        scores[name] = score
    return AIScore(
        title_equivalence=scores["title_equivalence"],
        relation_consistency=scores["relation_consistency"],
        intent_relevance=scores["intent_relevance"],
        fact_ids=tuple(referenced),
    )


def combine_score(candidate_key: str, program: ProgramScore, ai: AIScore) -> CandidateScore:
    total = 0 if program.excluded else min(100, program.total + ai.total)
    return CandidateScore(candidate_key, program, ai, total)


def apply_thresholds(scores: list[CandidateScore]) -> list[CandidateScore]:
    ranked = sorted(scores, key=lambda item: (-item.total, item.candidate_key))
    if not ranked:
        return []
    second = ranked[1].total if len(ranked) > 1 else 0
    result = []
    for index, item in enumerate(ranked):
        recommended = bool(
            index == 0
            and item.total >= RECOMMENDED_SCORE
            and item.total - second >= RECOMMENDED_LEAD
            and not item.program.excluded
        )
        result.append(replace(
            item,
            recommended=recommended,
            selectable=item.total >= MINIMUM_SCORE and not item.program.excluded,
        ))
    return result

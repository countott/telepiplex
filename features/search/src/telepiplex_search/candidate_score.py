"""Fixed, deterministic candidate ordering. No runtime weight learning."""

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
class CandidateAiScore:
    title_equivalence: int
    intent_relevance: int
    relation_consistency: int
    fact_ids: tuple[str, ...]

    @property
    def total(self) -> int:
        return (
            self.title_equivalence
            + self.intent_relevance
            + self.relation_consistency
        )


@dataclass(frozen=True)
class CandidateScore:
    candidate_key: str
    program: ProgramScore
    total: int
    ai: CandidateAiScore | None = None
    recommended: bool = False
    selectable: bool = False


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


def validate_ai_candidate_score(
    payload,
    *,
    candidate_key: str,
    allowed_fact_ids: set[str] | frozenset[str],
) -> CandidateAiScore | None:
    if not isinstance(payload, dict):
        return None
    allowed_keys = {
        "candidate_key",
        "title_equivalence",
        "intent_relevance",
        "relation_consistency",
        "fact_ids",
        "total",
    }
    required_keys = allowed_keys - {"total"}
    if not required_keys.issubset(payload) or not set(payload).issubset(
        allowed_keys
    ):
        return None
    if str(payload.get("candidate_key") or "") != candidate_key:
        return None
    dimensions = (
        ("title_equivalence", 20),
        ("intent_relevance", 10),
        ("relation_consistency", 10),
    )
    values = {}
    for name, maximum in dimensions:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if value < 0 or value > maximum:
            return None
        values[name] = value
    fact_ids = payload.get("fact_ids")
    if (
        not isinstance(fact_ids, list)
        or not fact_ids
        or any(not isinstance(item, str) or not item for item in fact_ids)
        or not set(fact_ids).issubset(set(allowed_fact_ids))
    ):
        return None
    return CandidateAiScore(
        title_equivalence=values["title_equivalence"],
        intent_relevance=values["intent_relevance"],
        relation_consistency=values["relation_consistency"],
        fact_ids=tuple(dict.fromkeys(fact_ids)),
    )


def combine_score(
    candidate_key: str,
    program: ProgramScore,
    ai: CandidateAiScore | None = None,
) -> CandidateScore:
    total = 0 if program.excluded else program.total + (ai.total if ai else 0)
    return CandidateScore(candidate_key, program, total, ai)


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
            selectable=not item.program.excluded,
        ))
    return result

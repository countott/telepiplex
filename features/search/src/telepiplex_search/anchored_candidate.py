"""Materialize AI fact references into immutable multi-source candidates."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .entity_graph import EvidenceFact, SearchGraph


_CANDIDATE_KEYS = {
    "candidate_id",
    "anchor_fact_id",
    "identity_role",
    "intended_scope",
    "fact_bindings",
    "ai_confidence",
    "ai_reason",
}
_BINDING_KEYS = {
    "fact_id",
    "role",
    "season_number",
    "episode_number",
}
_SOURCE_ROLES = {
    "movie",
    "series_root",
    "season",
    "episode",
    "related_work",
}
_IDENTITY_ROLES = {"movie", "series_root", "season", "episode"}
_SCOPES = {"movie", "work", "whole_series", "season", "episode"}


class CandidateBindingError(ValueError):
    def __init__(self, code: str, **details):
        self.code = str(code or "ai_output_invalid")
        self.details = {
            str(key): str(value)
            for key, value in details.items()
            if str(key) and str(value)
        }
        super().__init__(self.code)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _optional_positive_integer(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CandidateBindingError("ai_output_invalid")
    return value


def _mapping(value: Mapping[str, str] | None) -> Mapping[str, str]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class SourceLink:
    provider: str
    fact_id: str
    url: str
    external_ids: Mapping[str, str]
    role: str
    season_number: int | None
    episode_number: int | None
    verification: str
    proposed_season_number: int | None = None
    proposed_episode_number: int | None = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "fact_id": self.fact_id,
            "url": self.url,
            "external_ids": dict(self.external_ids),
            "role": self.role,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
            "verification": self.verification,
            "proposed_season_number": self.proposed_season_number,
            "proposed_episode_number": self.proposed_episode_number,
        }


@dataclass(frozen=True)
class PosterAsset:
    provider: str
    fact_id: str
    url: str
    role: str
    season_number: int | None
    language: str

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "fact_id": self.fact_id,
            "url": self.url,
            "role": self.role,
            "season_number": self.season_number,
            "language": self.language,
        }


@dataclass(frozen=True)
class AnchoredCandidate:
    candidate_id: str
    anchor_fact_id: str
    identity_role: str
    intended_scope: str
    source_links: tuple[SourceLink, ...]
    poster_assets: tuple[PosterAsset, ...]
    unresolved_sources: tuple[str, ...]
    ai_confidence: float
    ai_reason: str
    facts: tuple[EvidenceFact, ...]

    @property
    def providers(self) -> frozenset[str]:
        return frozenset(link.provider for link in self.source_links)

    @property
    def primary_poster_url(self) -> str:
        anchor = next(
            (
                poster.url
                for poster in self.poster_assets
                if poster.fact_id == self.anchor_fact_id
            ),
            "",
        )
        return anchor or next(
            (poster.url for poster in self.poster_assets),
            "",
        )

    @property
    def primary_summary(self) -> str:
        summaries = {
            fact.summary for fact in self.facts if _text(fact.summary)
        }
        return (
            max(summaries, key=lambda value: (len(value), value))
            if summaries
            else ""
        )

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "anchor_fact_id": self.anchor_fact_id,
            "identity_role": self.identity_role,
            "intended_scope": self.intended_scope,
            "source_links": [link.to_dict() for link in self.source_links],
            "poster_assets": [
                poster.to_dict() for poster in self.poster_assets
            ],
            "unresolved_sources": list(self.unresolved_sources),
            "ai_confidence": self.ai_confidence,
            "ai_reason": self.ai_reason,
        }


def _fact_registry(graph: SearchGraph) -> dict[str, EvidenceFact]:
    registry = {}
    for entity in (graph or SearchGraph(())).candidates:
        for fact in entity.facts:
            existing = registry.get(fact.fact_id)
            if existing is not None:
                raise CandidateBindingError(
                    "duplicate_fact_id",
                    fact_id=fact.fact_id,
                    provider=fact.provider,
                )
            registry[fact.fact_id] = fact
    return registry


def _regular_inventories(
    facts: tuple[EvidenceFact, ...],
) -> dict[str, set[tuple[int, int]]]:
    result = {"tvdb": set(), "tmdb": set()}
    for fact in facts:
        if fact.provider not in result or fact.media_type != "series":
            continue
        for raw in fact.episodes:
            if not isinstance(raw, dict):
                continue
            try:
                season = int(raw.get("season_number"))
                episode = int(raw.get("episode_number"))
            except (TypeError, ValueError):
                continue
            if season > 0 and episode > 0:
                result[fact.provider].add((season, episode))
    return result


def _wikipedia_season_counts(
    facts: tuple[EvidenceFact, ...],
) -> tuple[int, ...]:
    counts = set()
    for fact in facts:
        if (
            fact.provider not in {"wikipedia", "wikidata"}
            or fact.media_type != "series"
        ):
            continue
        try:
            count = int(fact.season_count)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            counts.add(count)
        episode_seasons = set()
        for raw in fact.episodes:
            if not isinstance(raw, dict):
                continue
            try:
                season = int(raw.get("season_number"))
                episode = int(raw.get("episode_number"))
            except (TypeError, ValueError):
                continue
            if season > 0 and episode > 0:
                episode_seasons.add(season)
        if episode_seasons:
            counts.add(max(episode_seasons))
    return tuple(sorted(counts))


def _verified_scope(
    role: str,
    season_number: int | None,
    episode_number: int | None,
    inventories: dict[str, set[tuple[int, int]]],
    wikipedia_season_counts: tuple[int, ...],
) -> tuple[int | None, int | None, str]:
    if role == "season":
        for provider in ("tvdb", "tmdb"):
            inventory = inventories.get(provider, set())
            if (
                season_number is not None
                and any(
                    season == season_number
                    for season, _episode in inventory
                )
            ):
                return season_number, None, f"{provider}_inventory_verified"
        if (
            season_number is not None
            and any(season_number <= count for count in wikipedia_season_counts)
        ):
            return season_number, None, "wikipedia_season_count_verified"
        return None, None, "unresolved_scope_link"
    if role == "episode":
        for provider in ("tvdb", "tmdb"):
            inventory = inventories.get(provider, set())
            if (
                season_number is not None
                and episode_number is not None
                and (season_number, episode_number) in inventory
            ):
                return (
                    season_number,
                    episode_number,
                    f"{provider}_inventory_verified",
                )
        return None, None, "unresolved_scope_link"
    return season_number, episode_number, (
        "ai_related_fact" if role == "related_work" else "fact_verified"
    )


def _validate_role_against_fact(role: str, fact: EvidenceFact) -> None:
    if role == "movie" and fact.media_type and fact.media_type != "movie":
        raise CandidateBindingError("media_type_conflict")
    if role in {"series_root", "season", "episode"} and (
        fact.media_type and fact.media_type != "series"
    ):
        raise CandidateBindingError("media_type_conflict")


def _candidate_from_payload(
    raw: dict,
    *,
    facts_by_id: dict[str, EvidenceFact],
    provider_statuses: dict[str, str],
) -> AnchoredCandidate:
    if not isinstance(raw, dict) or set(raw) != _CANDIDATE_KEYS:
        raise CandidateBindingError("ai_output_invalid")
    candidate_id = _text(raw.get("candidate_id"))
    anchor_fact_id = _text(raw.get("anchor_fact_id"))
    identity_role = _text(raw.get("identity_role")).casefold()
    intended_scope = _text(raw.get("intended_scope")).casefold()
    reason = _text(raw.get("ai_reason"))
    confidence = raw.get("ai_confidence")
    bindings = raw.get("fact_bindings")
    if (
        not candidate_id
        or len(candidate_id) > 120
        or anchor_fact_id not in facts_by_id
        or identity_role not in _IDENTITY_ROLES
        or intended_scope not in _SCOPES
        or not isinstance(bindings, list)
        or not bindings
        or len(bindings) > 30
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
        or not reason
        or len(reason) > 500
    ):
        raise CandidateBindingError("ai_output_invalid")

    binding_values = []
    seen_fact_ids = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != _BINDING_KEYS:
            raise CandidateBindingError("ai_output_invalid")
        fact_id = _text(binding.get("fact_id"))
        role = _text(binding.get("role")).casefold()
        if fact_id not in facts_by_id:
            raise CandidateBindingError("unknown_fact_id")
        if fact_id in seen_fact_ids or role not in _SOURCE_ROLES:
            raise CandidateBindingError("ai_output_invalid")
        seen_fact_ids.add(fact_id)
        fact = facts_by_id[fact_id]
        _validate_role_against_fact(role, fact)
        binding_values.append((
            fact,
            role,
            _optional_positive_integer(binding.get("season_number")),
            _optional_positive_integer(binding.get("episode_number")),
        ))
    if anchor_fact_id not in seen_fact_ids:
        raise CandidateBindingError("ai_output_invalid")

    selected_facts = tuple(value[0] for value in binding_values)
    inventories = _regular_inventories(selected_facts)
    wikipedia_season_counts = _wikipedia_season_counts(selected_facts)
    source_links = []
    posters = []
    unresolved = []
    for fact, role, season_number, episode_number in binding_values:
        proposed_season_number = season_number
        proposed_episode_number = episode_number
        season_number, episode_number, verification = _verified_scope(
            role,
            season_number,
            episode_number,
            inventories,
            wikipedia_season_counts,
        )
        if verification == "unresolved_scope_link":
            unresolved.append(f"{fact.fact_id}:unresolved_scope_link")
        if not fact.source_url:
            unresolved.append(f"{fact.fact_id}:source_url_missing")
        source_links.append(SourceLink(
            provider=fact.provider,
            fact_id=fact.fact_id,
            url=fact.source_url,
            external_ids=_mapping(fact.external_ids),
            role=role,
            season_number=season_number,
            episode_number=episode_number,
            verification=verification,
            proposed_season_number=(
                proposed_season_number
                if verification == "unresolved_scope_link"
                else None
            ),
            proposed_episode_number=(
                proposed_episode_number
                if verification == "unresolved_scope_link"
                else None
            ),
        ))
        if fact.poster_url:
            posters.append(PosterAsset(
                provider=fact.provider,
                fact_id=fact.fact_id,
                url=fact.poster_url,
                role=role,
                season_number=season_number,
                language=fact.poster_language,
            ))

    bound_providers = {fact.provider for fact in selected_facts}
    for provider, status in provider_statuses.items():
        provider = _text(provider).casefold()
        status = _text(status).casefold() or "unavailable"
        if status != "ok":
            unresolved.append(f"{provider}:{status}")
        elif provider not in bound_providers:
            unresolved.append(f"{provider}:not_bound")
    return AnchoredCandidate(
        candidate_id=candidate_id,
        anchor_fact_id=anchor_fact_id,
        identity_role=identity_role,
        intended_scope=intended_scope,
        source_links=tuple(source_links),
        poster_assets=tuple(posters),
        unresolved_sources=tuple(dict.fromkeys(unresolved)),
        ai_confidence=float(confidence),
        ai_reason=reason,
        facts=selected_facts,
    )


def materialize_anchored_candidates(
    graph: SearchGraph,
    payload,
    *,
    provider_statuses: dict[str, str] | None = None,
    locked_anchor_fact_id: str = "",
) -> tuple[AnchoredCandidate, ...]:
    """Validate an AI shortlist and resolve every reference from provider facts."""

    if not isinstance(payload, dict) or set(payload) != {
        "status",
        "candidates",
    }:
        raise CandidateBindingError("ai_output_invalid")
    status = _text(payload.get("status")).casefold()
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise CandidateBindingError("ai_output_invalid")
    if status == "no_match":
        if raw_candidates:
            raise CandidateBindingError("ai_output_invalid")
        return ()
    if status != "resolved":
        raise CandidateBindingError("ai_output_invalid")
    if not 1 <= len(raw_candidates) <= 6:
        raise CandidateBindingError("candidate_count_invalid")
    if locked_anchor_fact_id and len(raw_candidates) != 1:
        raise CandidateBindingError("locked_anchor_invalid")

    facts_by_id = _fact_registry(graph)
    result = []
    errors = []
    for raw in raw_candidates:
        try:
            result.append(_candidate_from_payload(
                raw,
                facts_by_id=facts_by_id,
                provider_statuses=dict(provider_statuses or {}),
            ))
        except CandidateBindingError as exc:
            if locked_anchor_fact_id:
                raise
            errors.append(exc)
    result = tuple(result)
    if not result and errors:
        raise errors[0]
    candidate_ids = [item.candidate_id for item in result]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CandidateBindingError("ai_output_invalid")
    used_fact_ids = [
        fact.fact_id
        for candidate in result
        for fact in candidate.facts
    ]
    if len(used_fact_ids) != len(set(used_fact_ids)):
        raise CandidateBindingError("fact_bound_multiple_times")
    if locked_anchor_fact_id and (
        result[0].anchor_fact_id != locked_anchor_fact_id
        or locked_anchor_fact_id not in {
            fact.fact_id for fact in result[0].facts
        }
    ):
        raise CandidateBindingError("locked_anchor_invalid")
    return result

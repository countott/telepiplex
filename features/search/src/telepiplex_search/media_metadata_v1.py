"""Strict media_metadata v1 construction from frozen source links."""

from __future__ import annotations

from .anchored_candidate import AnchoredCandidate
from .entity_graph import CandidateEntity, EvidenceFact
from .prowlarr_query import build_prowlarr_query_chain
from .title_policy import (
    CanonicalTitles,
    TitlePolicyError,
    resolve_title_policy,
)


class MetadataV1Error(ValueError):
    def __init__(self, code: str, missing_fields=()):
        self.code = str(code or "metadata_incomplete")
        self.missing_fields = tuple(
            str(field) for field in missing_fields or ()
        )
        message = self.code
        if self.missing_fields:
            message += ":" + ",".join(self.missing_fields)
        super().__init__(message)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _unique(values) -> list[str]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _link_by_fact(candidate: AnchoredCandidate) -> dict[str, object]:
    return {link.fact_id: link for link in candidate.source_links}


def _primary_facts(candidate: AnchoredCandidate) -> tuple[EvidenceFact, ...]:
    links = _link_by_fact(candidate)
    result = tuple(
        fact
        for fact in candidate.facts
        if (
            fact.fact_id not in links
            or getattr(links[fact.fact_id], "role", "") != "related_work"
        )
    )
    return result or candidate.facts


def _root_fact(candidate: AnchoredCandidate) -> EvidenceFact:
    facts = {fact.fact_id: fact for fact in candidate.facts}
    for link in candidate.source_links:
        if link.role == "series_root" and link.fact_id in facts:
            return facts[link.fact_id]
    return facts.get(candidate.anchor_fact_id) or candidate.facts[0]


def _field_sources(
    facts: tuple[EvidenceFact, ...],
    *,
    values: dict[str, str],
) -> dict[str, list[dict]]:
    result = {}
    fact_fields = {
        "chinese_title": "chinese_title",
        "official_english_title": "official_english_title",
        "original_title": "original_title",
        "romanized_original_title": "romanized_original_title",
        "year": "year",
    }
    for target, fact_field in fact_fields.items():
        expected = _text(values.get(target))
        result[target] = [
            {
                "provider": fact.provider,
                "fact_id": fact.fact_id,
                "value": expected,
            }
            for fact in facts
            if expected and _text(getattr(fact, fact_field, "")) == expected
        ]
    return result


def _inventory(facts: tuple[EvidenceFact, ...]) -> list[dict]:
    items = []
    seen = set()
    for fact in facts:
        if fact.provider != "tvdb" or fact.media_type != "series":
            continue
        for raw in fact.episodes:
            if not isinstance(raw, dict):
                continue
            try:
                season = int(raw.get("season_number"))
                episode = int(raw.get("episode_number"))
            except (TypeError, ValueError):
                continue
            if season < 0 or episode < 1 or (season, episode) in seen:
                continue
            seen.add((season, episode))
            items.append({
                "item_id": _text(
                    raw.get("tvdb_episode_id") or raw.get("id")
                ) or f"S{season:02d}E{episode:03d}",
                "content_role": "main_episode",
                "season_number": season,
                "episode_number": episode,
                "aired": _text(
                    raw.get("aired") or raw.get("firstAired")
                ),
            })
    return sorted(
        items,
        key=lambda item: (
            item["season_number"],
            item["episode_number"],
        ),
    )


def _scope(candidate: AnchoredCandidate, media_type: str) -> tuple[
    str,
    int | None,
    int | None,
]:
    if media_type == "movie":
        return "movie", None, None
    scope = candidate.intended_scope
    if scope in {"movie", "work"}:
        scope = "whole_series"
    season = episode = None
    if scope in {"season", "episode"}:
        anchor_link = next(
            (
                link
                for link in candidate.source_links
                if link.fact_id == candidate.anchor_fact_id
                and link.role in {"season", "episode"}
            ),
            None,
        )
        scoped_links = [
            link
            for link in candidate.source_links
            if link.role == scope
            and link.verification == "tvdb_inventory_verified"
        ]
        selected = anchor_link or (
            scoped_links[0] if len(scoped_links) == 1 else None
        )
        if (
            selected is None
            or selected.verification != "tvdb_inventory_verified"
            or selected.season_number is None
            or (scope == "episode" and selected.episode_number is None)
        ):
            raise MetadataV1Error(
                "metadata_incomplete",
                ("verified_scope",),
            )
        season = selected.season_number
        episode = selected.episode_number
    return scope, season, episode


def _provider_statuses(candidate: AnchoredCandidate) -> dict[str, str]:
    statuses = {
        link.provider: "ok" for link in candidate.source_links
    }
    for unresolved in candidate.unresolved_sources:
        parts = unresolved.split(":", 1)
        if len(parts) == 2 and parts[0] in {
            "wikipedia",
            "douban",
            "tvdb",
        }:
            statuses[parts[0]] = parts[1]
    return statuses


def build_media_metadata_v1(
    candidate: AnchoredCandidate,
    *,
    metadata_id: str,
    raw_query: str,
) -> dict:
    """Build the canonical strict contract from a frozen candidate."""

    if not isinstance(candidate, AnchoredCandidate) or not candidate.facts:
        raise MetadataV1Error("metadata_incomplete", ("candidate_facts",))
    primary_facts = _primary_facts(candidate)
    media_types = {
        fact.media_type
        for fact in primary_facts
        if fact.media_type
    }
    if len(media_types) > 1:
        raise MetadataV1Error("metadata_conflict", ("media_type",))
    if not media_types or next(iter(media_types)) not in {"movie", "series"}:
        raise MetadataV1Error("metadata_incomplete", ("media_type",))
    media_type = next(iter(media_types))
    entity = CandidateEntity(candidate.candidate_id, primary_facts)
    degraded_series_candidate = bool(
        media_type == "series"
        and candidate.intended_scope in {"work", "whole_series"}
        and any(
            _text(item).startswith("tvdb:")
            and not _text(item).endswith(":ok")
            for item in candidate.unresolved_sources
        )
    )
    try:
        titles = resolve_title_policy(
            entity,
            preferred_chinese_title=raw_query,
        )
    except TitlePolicyError as exc:
        if not degraded_series_candidate:
            raise MetadataV1Error(
                "metadata_incomplete",
                ("canonical_latin_title",),
            ) from exc
        chinese_title = next(
            (
                fact.chinese_title
                for fact in primary_facts
                if fact.chinese_title
            ),
            next(
                (
                    title
                    for fact in primary_facts
                    for title in fact.titles
                    if _text(title)
                ),
                _text(raw_query),
            ),
        )
        original_title = next(
            (
                fact.original_title
                for fact in primary_facts
                if fact.original_title
            ),
            "",
        )
        titles = CanonicalTitles(
            chinese_title=_text(chinese_title),
            original_title=_text(original_title),
            original_language=next(
                (
                    fact.original_language
                    for fact in primary_facts
                    if fact.original_language
                ),
                "",
            ),
            official_english_title="",
            romanized_original_title="",
            canonical_search_title=_text(chinese_title),
            canonical_latin_title="",
            search_title_policy="",
        )

    root = _root_fact(candidate)
    year = root.year or next(
        (fact.year for fact in primary_facts if fact.year),
        "",
    )
    if not year:
        raise MetadataV1Error("metadata_incomplete", ("year",))
    scope, season_number, episode_number = _scope(
        candidate,
        media_type,
    )
    inventory = _inventory(primary_facts)
    degraded_tvdb_inventory = bool(
        media_type == "series"
        and scope == "whole_series"
        and any(
            _text(item).startswith("tvdb:")
            and not _text(item).endswith(":ok")
            for item in candidate.unresolved_sources
        )
    )
    if media_type == "series":
        if not any(
            fact.provider == "tvdb"
            and _text(fact.external_ids.get("tvdb"))
            for fact in primary_facts
        ) and not degraded_tvdb_inventory:
            raise MetadataV1Error(
                "metadata_incomplete",
                ("tvdb_root",),
            )
        if not inventory and not degraded_tvdb_inventory:
            raise MetadataV1Error(
                "metadata_incomplete",
                ("tvdb_inventory",),
            )
    if not candidate.source_links or not any(
        _text(link.url) for link in candidate.source_links
    ):
        raise MetadataV1Error(
            "metadata_incomplete",
            ("source_links",),
        )
    if any(
        "unresolved_scope_link" in item
        for item in candidate.unresolved_sources
    ):
        raise MetadataV1Error(
            "metadata_incomplete",
            ("verified_scope",),
        )

    aliases = _unique(
        title for fact in primary_facts for title in fact.titles
    )
    external_ids = {}
    for fact in (root, *primary_facts):
        for key, value in fact.external_ids.items():
            external_ids.setdefault(key, value)
    external_id_records = [{
        "provider": link.provider,
        "fact_id": link.fact_id,
        "external_ids": dict(link.external_ids),
        "role": link.role,
        "season_number": link.season_number,
        "episode_number": link.episode_number,
        "proposed_season_number": link.proposed_season_number,
        "proposed_episode_number": link.proposed_episode_number,
        "verification": link.verification,
    } for link in candidate.source_links if link.external_ids]
    animation = any(
        signal in _text(genre).casefold()
        for fact in primary_facts
        for genre in fact.genres
        for signal in ("animation", "animated", "anime", "动画", "動畫")
    )
    category = f"{'animated' if animation else 'live_action'}_{media_type}"
    chinese_title = (
        titles.chinese_title
        or titles.original_title
        or titles.canonical_latin_title
    )
    poster = candidate.primary_poster_url
    poster_source = next(
        (
            item.provider
            for item in candidate.poster_assets
            if item.url == poster
        ),
        "",
    )
    identity = {
        **titles.identity_fields(),
        "chinese_title": chinese_title,
        "aliases": aliases,
        "year": year,
        "content_kind": media_type,
        "summary": "",
        "original_release_date": "",
        "poster_url": poster,
        "poster_source": poster_source,
        "external_ids": external_ids,
        "external_id_records": external_id_records,
        "root_fact_id": root.fact_id,
    }
    years = sorted({
        fact.year for fact in primary_facts if fact.year
    })
    source_title_sets = {
        tuple(fact.titles)
        for fact in primary_facts
        if fact.titles
    }
    warnings = []
    if len(years) > 1:
        warnings.append("warning:source_years_differ")
    if len(source_title_sets) > 1:
        warnings.append("warning:source_titles_differ")
    if candidate.unresolved_sources:
        warnings.append("warning:source_unresolved")
    if degraded_tvdb_inventory:
        warnings.append("warning:tvdb_inventory_unavailable")
    anchor_link = next(
        (
            link for link in candidate.source_links
            if link.fact_id == candidate.anchor_fact_id
        ),
        candidate.source_links[0],
    )
    contract = {
        "schema_version": 1,
        "metadata_id": _text(metadata_id),
        "confirmed": False,
        "identity": identity,
        "retrieval": {
            "media_type": media_type,
            "scope": scope,
            "query": "",
            "queries": [],
        },
        "relation": {
            "type": "standalone",
            "target_series": {},
            "source": "anchored_candidate",
        },
        "placement": {
            "library_type": media_type,
            "category_kind": category,
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "standalone",
            "mapping_source": "media_metadata_v1",
            "tvdb_episode_id": "",
        },
        "source_entry": {
            "title": chinese_title or titles.canonical_latin_title,
            "url": anchor_link.url,
            "external_id": next(
                iter(anchor_link.external_ids.values()),
                "",
            ),
            "provider": anchor_link.provider,
            "verification": anchor_link.verification,
        },
        "items": inventory,
        "evidence": {
            "anchor_fact_id": candidate.anchor_fact_id,
            "source_links": [
                link.to_dict() for link in candidate.source_links
            ],
            "poster_assets": [
                poster_asset.to_dict()
                for poster_asset in candidate.poster_assets
            ],
            "source_facts": [{
                "provider": fact.provider,
                "fact_id": fact.fact_id,
                "titles": list(fact.titles),
                "year": fact.year,
                "media_type": fact.media_type,
                "external_ids": dict(fact.external_ids),
            } for fact in primary_facts],
            "provider_statuses": _provider_statuses(candidate),
            "field_sources": _field_sources(
                primary_facts,
                values={
                    **titles.identity_fields(),
                    "year": year,
                },
            ),
            "tvdb_inventory": list(inventory),
            "ai": {
                "confidence": candidate.ai_confidence,
                "reason": candidate.ai_reason,
            },
            "unresolved": list(candidate.unresolved_sources),
            "decision": {
                "mode": "ai_fact_binding",
                "scope": scope,
                "season_number": season_number,
                "episode_number": episode_number,
            },
            "verified_tvdb_special_candidates": [],
            "tvdb_official_special_candidates": [],
        },
        "warnings": warnings,
    }
    queries = build_prowlarr_query_chain(contract, raw_query)
    contract["retrieval"]["query"] = queries[0]
    contract["retrieval"]["queries"] = queries
    return contract

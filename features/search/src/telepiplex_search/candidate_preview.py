"""Preview one deterministic frozen candidate before full enrichment."""

from __future__ import annotations

from .media_metadata_v1 import MetadataV1Error


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def anchored_fact_snapshot(candidate) -> list[dict]:
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
            "summary": fact.summary,
            "original_title": fact.original_title,
            "original_language": fact.original_language,
            "official_english_title": fact.official_english_title,
            "romanized_original_title": fact.romanized_original_title,
            "chinese_title": fact.chinese_title,
            "poster_language": fact.poster_language,
            "genres": list(fact.genres),
            "countries": list(fact.countries),
            "episodes": [dict(item) for item in fact.episodes],
            "complex_signals": list(fact.complex_signals),
        }
        for fact in candidate.facts
    ]


def candidate_preview_metadata(
    candidate,
    *,
    metadata_id: str,
    raw_query: str,
    metadata_error: MetadataV1Error,
) -> dict:
    facts = tuple(candidate.facts)
    anchor = next(
        (fact for fact in facts if fact.fact_id == candidate.anchor_fact_id),
        facts[0],
    )
    media_type = (
        "movie"
        if candidate.identity_role == "movie"
        else "series"
        if candidate.identity_role in {"series_root", "season", "episode"}
        else next(
            (fact.media_type for fact in facts if fact.media_type in {"movie", "series"}),
            "",
        )
    )
    chinese_title = next((fact.chinese_title for fact in facts if fact.chinese_title), "")
    english_title = next(
        (fact.official_english_title for fact in facts if fact.official_english_title),
        "",
    )
    original_title = next((fact.original_title for fact in facts if fact.original_title), "")
    romanized = next(
        (fact.romanized_original_title for fact in facts if fact.romanized_original_title),
        "",
    )
    display_title = (
        chinese_title
        or english_title
        or romanized
        or original_title
        or next((title for fact in facts for title in fact.titles if _text(title)), _text(raw_query) or "未知")
    )
    external_ids = {}
    for fact in facts:
        external_ids.update(dict(fact.external_ids))
    countries = []
    for fact in facts:
        for country in fact.countries:
            if _text(country) and _text(country) not in countries:
                countries.append(_text(country))
    year = anchor.year or next((fact.year for fact in facts if fact.year), "")
    animation = any(
        signal in _text(genre).casefold()
        for fact in facts
        for genre in fact.genres
        for signal in ("animation", "animated", "anime", "动画", "動畫")
    )
    scope = "movie" if media_type == "movie" else candidate.intended_scope or "work"
    return {
        "schema_version": 1,
        "metadata_id": _text(metadata_id),
        "confirmed": False,
        "identity": {
            "chinese_title": chinese_title,
            "english_title": english_title or romanized or original_title,
            "official_english_title": english_title,
            "romanized_original_title": romanized,
            "original_title": original_title,
            "original_language": next(
                (fact.original_language for fact in facts if fact.original_language),
                "",
            ),
            "aliases": list(dict.fromkeys(
                title for fact in facts for title in fact.titles if _text(title)
            )),
            "countries": countries,
            "year": year,
            "content_kind": media_type,
            "summary": candidate.primary_summary,
            "poster_url": "",
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
                if media_type else ""
            ),
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "unresolved",
            "mapping_source": "deterministic_candidate_preview",
            "tvdb_episode_id": "",
        },
        "items": [],
        "evidence": {
            "anchor_fact_id": candidate.anchor_fact_id,
            "source_links": [link.to_dict() for link in candidate.source_links],
            "poster_assets": [item.to_dict() for item in candidate.poster_assets],
            "provider_statuses": {link.provider: "ok" for link in candidate.source_links},
            "unresolved": list(candidate.unresolved_sources),
            "decision": {
                "mode": "deterministic_fact_binding_preview",
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

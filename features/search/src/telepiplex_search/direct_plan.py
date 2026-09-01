"""Build a frozen search plan from one exact metadata link."""

from __future__ import annotations

from copy import deepcopy

from .anchored_candidate import materialize_anchored_candidates
from .candidate_preview import anchored_fact_snapshot, candidate_preview_metadata
from .entity_graph import build_discovery_graph
from .errors import SearchPlanningError
from .media_metadata_v1 import MetadataV1Error, build_media_metadata_v1


def _candidate_from_anchored(anchored, *, plan_id: str, raw_query: str) -> dict:
    try:
        contract = build_media_metadata_v1(
            anchored,
            metadata_id=plan_id,
            raw_query=raw_query,
        )
        metadata_ready = True
        metadata_error = {}
    except MetadataV1Error as exc:
        contract = candidate_preview_metadata(
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
        "ai_confidence": 0.0,
        "ai_reason": "deterministic_direct_identity",
        "reasons": [],
        "candidate_version": "direct-link-v1",
        "metadata_ready": metadata_ready,
        "metadata_error": metadata_error,
        "links_frozen": True,
        "fact_snapshot": anchored_fact_snapshot(anchored),
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
    """Freeze the stable identity returned by an exact provider link."""

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
            direct.stable_identity[1] in set(fact.external_ids.values())
            or len(facts) == 1
        )
    ]
    if len(matching) != 1:
        raise SearchPlanningError("direct_link_invalid")
    fact = matching[0]
    media_type = direct.media_type
    identity_role = (
        "movie"
        if media_type == "movie"
        else direct.scope
        if direct.scope in {"season", "episode"}
        else "series_root"
    )
    role = "anime_entry" if direct.provider == "anilist" else identity_role
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
                "identity_role": identity_role,
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
                "ai_confidence": 0.0,
                "ai_reason": "deterministic_direct_identity",
            }],
        },
        provider_statuses={direct.provider: "ok"},
        locked_anchor_fact_id=fact.fact_id,
    )[0]
    candidate = _candidate_from_anchored(
        anchored,
        plan_id=plan_id,
        raw_query=raw_query,
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

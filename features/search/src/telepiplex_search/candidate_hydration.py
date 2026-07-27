"""Exact-read a frozen candidate without issuing any title search."""

from __future__ import annotations

from dataclasses import replace

from .anchored_candidate import (
    CandidateBindingError,
    materialize_anchored_candidates,
)
from .direct_link import DirectLinkError, resolve_direct_link
from .entity_graph import build_search_graph
from .input_contract import classify_search_input
from .media_metadata_v1 import MetadataV1Error, build_media_metadata_v1


class CandidateHydrationError(ValueError):
    def __init__(self, code: str, details=()):
        self.code = str(code or "metadata_incomplete")
        self.details = tuple(str(item) for item in details or ())
        message = self.code
        if self.details:
            message += ":" + ",".join(self.details)
        super().__init__(message)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _merge_exact_sources(sources: list[dict]) -> list[dict]:
    merged = {}
    for source in sources:
        provider = _text(source.get("source")).casefold()
        if not provider:
            continue
        target = merged.setdefault(provider, {
            "source": provider,
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "error": "",
        })
        if source.get("status") == "ok":
            target["status"] = "ok"
        target["facts"].extend(
            fact for fact in source.get("facts") or []
            if isinstance(fact, dict)
        )
        for url in source.get("source_urls") or []:
            url = _text(url)
            if url and url not in target["source_urls"]:
                target["source_urls"].append(url)
    return list(merged.values())


def _stable_identity_matches(link: dict, direct) -> bool:
    key, value = direct.stable_identity
    frozen_ids = (
        link.get("external_ids")
        if isinstance(link.get("external_ids"), dict)
        else {}
    )
    expected = _text(frozen_ids.get(key))
    return not expected or expected == _text(value)


def hydrate_frozen_candidate(
    candidate: dict,
    *,
    metadata_id: str,
    raw_query: str,
    require_anchor: bool = False,
    resolver=resolve_direct_link,
) -> dict:
    """Return a hydrated candidate using only its saved source URLs."""

    if not isinstance(candidate, dict) or not candidate.get("links_frozen"):
        raise CandidateHydrationError("candidate_not_frozen")
    frozen_links = candidate.get("source_links")
    if not isinstance(frozen_links, list) or not frozen_links:
        raise CandidateHydrationError(
            "metadata_incomplete",
            ("source_links",),
        )

    exact_sources = []
    successful_fact_ids = set()
    failures = []
    for frozen in frozen_links:
        if not isinstance(frozen, dict):
            failures.append("invalid_frozen_link")
            continue
        url = _text(frozen.get("url"))
        parsed = classify_search_input(url)
        if parsed.kind != "link" or parsed.link is None:
            failures.append(
                f"{_text(frozen.get('fact_id'))}:fixed_link_invalid"
            )
            continue
        try:
            direct = resolver(parsed.link)
        except Exception as exc:
            code = (
                str(exc)
                if isinstance(exc, DirectLinkError)
                else type(exc).__name__
            )
            failures.append(
                f"{_text(frozen.get('fact_id'))}:fixed_link_read_failed:{code}"
            )
            continue
        if not _stable_identity_matches(frozen, direct):
            failures.append(
                f"{_text(frozen.get('fact_id'))}:stable_id_mismatch"
            )
            continue
        source = dict(direct.evidence)
        exact_sources.append(source)
        successful_fact_ids.add(_text(frozen.get("fact_id")))

    anchor_fact_id = _text(candidate.get("anchor_fact_id"))
    if require_anchor and anchor_fact_id not in successful_fact_ids:
        raise CandidateHydrationError(
            "fixed_link_read_failed",
            tuple(failures),
        )
    sources = _merge_exact_sources(exact_sources)
    graph = build_search_graph(sources)
    known_fact_ids = {
        fact.fact_id
        for entity in graph.candidates
        for fact in entity.facts
    }
    bindings = [
        {
            "fact_id": fact_id,
            "role": _text(link.get("role")).casefold(),
            "season_number": (
                link.get("season_number")
                or link.get("proposed_season_number")
            ),
            "episode_number": (
                link.get("episode_number")
                or link.get("proposed_episode_number")
            ),
        }
        for link in frozen_links
        if isinstance(link, dict)
        and (fact_id := _text(link.get("fact_id"))) in known_fact_ids
    ]
    if not bindings:
        raise CandidateHydrationError(
            "metadata_incomplete",
            tuple(failures) or ("all_fixed_links_failed",),
        )
    if anchor_fact_id not in known_fact_ids:
        anchor_fact_id = bindings[0]["fact_id"]
    payload = {
        "status": "resolved",
        "candidates": [{
            "candidate_id": _text(
                candidate.get("candidate_id")
                or candidate.get("candidate_key")
            ),
            "anchor_fact_id": anchor_fact_id,
            "identity_role": _text(
                candidate.get("identity_role")
                or (
                    (candidate.get("media_metadata") or {}).get(
                        "identity"
                    ) or {}
                ).get("content_kind")
            ).casefold(),
            "intended_scope": _text(
                candidate.get("intended_scope")
                or (
                    (candidate.get("media_metadata") or {}).get(
                        "retrieval"
                    ) or {}
                ).get("scope")
            ).casefold(),
            "fact_bindings": bindings,
            "ai_confidence": float(
                candidate.get("ai_confidence") or 0
            ),
            "ai_reason": _text(
                candidate.get("ai_reason")
                or "Frozen candidate exact-link hydration."
            ),
        }],
    }
    statuses = {
        _text(source.get("source")).casefold(): _text(
            source.get("status")
        ).casefold()
        for source in sources
    }
    try:
        anchored = materialize_anchored_candidates(
            graph,
            payload,
            provider_statuses=statuses,
            locked_anchor_fact_id=(
                _text(candidate.get("anchor_fact_id"))
                if require_anchor
                else ""
            ),
        )[0]
    except CandidateBindingError as exc:
        raise CandidateHydrationError(
            "candidate_binding_failed",
            (exc.code,),
        ) from exc
    verified_scope_fact_ids = {
        link.fact_id
        for link in anchored.source_links
        if link.verification == "tvdb_inventory_verified"
    }
    previous_unresolved = [
        item
        for item in (candidate.get("unresolved_sources") or [])
        if not (
            _text(item).endswith(":unresolved_scope_link")
            and _text(item).removesuffix(
                ":unresolved_scope_link"
            ) in verified_scope_fact_ids
        )
    ]
    unresolved = tuple(dict.fromkeys([
        *previous_unresolved,
        *anchored.unresolved_sources,
        *failures,
    ]))
    anchored = replace(
        anchored,
        unresolved_sources=unresolved,
    )
    try:
        contract = build_media_metadata_v1(
            anchored,
            metadata_id=metadata_id,
            raw_query=raw_query,
        )
    except MetadataV1Error as exc:
        raise CandidateHydrationError(
            exc.code,
            exc.missing_fields,
        ) from exc

    result = dict(candidate)
    result.update({
        "anchor_fact_id": anchored.anchor_fact_id,
        "media_metadata": contract,
        "prowlarr_queries": list(
            (contract.get("retrieval") or {}).get("queries") or []
        ),
        "poster_url": anchored.primary_poster_url,
        "poster_assets": [
            poster.to_dict() for poster in anchored.poster_assets
        ],
        "source_links": [
            link.to_dict() for link in anchored.source_links
        ],
        "unresolved_sources": list(anchored.unresolved_sources),
        "metadata_hydrated": True,
    })
    return result

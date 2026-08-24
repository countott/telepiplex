"""Exact-read a frozen candidate without issuing any title search."""

from __future__ import annotations

from dataclasses import replace

from .anchored_candidate import (
    CandidateBindingError,
    materialize_anchored_candidates,
)
from .direct_link import DirectLinkError, resolve_direct_link
from .entity_graph import EvidenceFactConflict, build_search_graph
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


def _anchor_provider(candidate: dict, frozen_links: list[dict]) -> str:
    anchor_fact_id = _text(candidate.get("anchor_fact_id"))
    return next(
        (
            _text(link.get("provider")).casefold()
            for link in frozen_links
            if isinstance(link, dict)
            and _text(link.get("fact_id")) == anchor_fact_id
        ),
        "",
    )


def _strict_graph_with_quarantine(
    sources: list[dict],
    *,
    anchor_provider: str,
    failures: list[str],
):
    remaining = list(sources)
    while remaining:
        try:
            return build_search_graph(remaining), remaining
        except EvidenceFactConflict as exc:
            if exc.provider == anchor_provider:
                raise CandidateHydrationError(
                    "source_fact_conflict",
                    (
                        exc.fact_id,
                        *(
                            f"field:{field}"
                            for field in exc.conflicting_fields
                        ),
                    ),
                ) from exc
            failures.append(
                f"{exc.provider}:source_fact_conflict:{exc.fact_id}:"
                + ",".join(exc.conflicting_fields)
            )
            remaining = [
                source
                for source in remaining
                if _text(source.get("source")).casefold() != exc.provider
            ]
    raise CandidateHydrationError(
        "metadata_incomplete",
        tuple(failures) or ("all_fixed_links_failed",),
    )


def _exact_fact_id(link: dict, facts) -> str:
    provider = _text(link.get("provider")).casefold()
    frozen_ids = (
        link.get("external_ids")
        if isinstance(link.get("external_ids"), dict)
        else {}
    )
    frozen_pairs = {
        (_text(key), _text(value))
        for key, value in frozen_ids.items()
        if _text(key) and _text(value)
    }
    matches = [
        fact.fact_id
        for fact in facts
        if fact.provider == provider
        and (
            frozen_pairs.intersection({
                (_text(key), _text(value))
                for key, value in fact.external_ids.items()
                if _text(key) and _text(value)
            })
            or (
                _text(link.get("fact_id")).split(
                    "@occurrence:",
                    1,
                )[0]
                in {
                    fact.fact_id,
                    fact.stable_fact_id,
                }
            )
        )
    ]
    return matches[0] if len(set(matches)) == 1 else ""


def _materialize_exact_candidate(
    candidate: dict,
    *,
    require_anchor: bool = False,
    resolver=resolve_direct_link,
):
    """Exact-read and validate a frozen anchor without building a contract."""

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
    graph, sources = _strict_graph_with_quarantine(
        sources,
        anchor_provider=_anchor_provider(candidate, frozen_links),
        failures=failures,
    )
    facts = tuple(
        fact
        for entity in graph.candidates
        for fact in entity.facts
    )
    resolved_fact_ids = {
        _text(link.get("fact_id")): _exact_fact_id(link, facts)
        for link in frozen_links
        if isinstance(link, dict)
    }
    bindings = [{
            "fact_id": resolved_fact_id,
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
        and (
            resolved_fact_id := resolved_fact_ids.get(
                _text(link.get("fact_id")),
                "",
            )
        )
    ]
    if not bindings:
        raise CandidateHydrationError(
            "metadata_incomplete",
            tuple(failures) or ("all_fixed_links_failed",),
        )
    resolved_anchor_fact_id = resolved_fact_ids.get(anchor_fact_id, "")
    if require_anchor and not resolved_anchor_fact_id:
        raise CandidateHydrationError(
            "fixed_link_read_failed",
            tuple(failures) or (f"{anchor_fact_id}:exact_fact_missing",),
        )
    anchor_fact_id = resolved_anchor_fact_id or bindings[0]["fact_id"]
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
                anchor_fact_id
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
        if link.verification in {
            "tvdb_inventory_verified",
            "tmdb_inventory_verified",
            "wikipedia_season_count_verified",
        }
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
    return anchored


def _strict_contract(anchored, *, metadata_id: str, raw_query: str) -> dict:
    try:
        return build_media_metadata_v1(
            anchored,
            metadata_id=metadata_id,
            raw_query=raw_query,
        )
    except MetadataV1Error as exc:
        raise CandidateHydrationError(
            exc.code,
            exc.missing_fields,
        ) from exc


def _candidate_result(
    candidate: dict,
    anchored,
    contract: dict,
    *,
    metadata_hydrated: bool,
) -> dict:
    """Project exact-read facts back onto the frozen candidate payload."""

    douban_match_mode = _text(candidate.get("douban_match_mode"))
    if douban_match_mode:
        facts_by_id = {fact.fact_id: fact for fact in anchored.facts}
        field_sources = (
            (contract.get("evidence") or {}).get("field_sources") or {}
        )
        for record in field_sources.get("chinese_title") or ():
            if record.get("provider") != "douban":
                continue
            fact = facts_by_id.get(_text(record.get("fact_id")))
            if fact is None:
                continue
            record.update({
                "match_mode": douban_match_mode,
                "douban_title_raw": fact.douban_title_raw,
                "season_number": fact.source_season_number,
                "subject_id": _text(
                    fact.external_ids.get("douban_subject")
                ),
            })

    result = dict(candidate)
    result.update({
        "anchor_fact_id": anchored.anchor_fact_id,
        "media_metadata": contract,
        "prowlarr_queries": list(
            (contract.get("retrieval") or {}).get("queries") or []
        ),
        "poster_url": _text(
            (contract.get("identity") or {}).get("poster_url")
        ),
        "poster_assets": [
            poster.to_dict() for poster in anchored.poster_assets
        ],
        "source_links": [
            link.to_dict() for link in anchored.source_links
        ],
        "unresolved_sources": list(anchored.unresolved_sources),
        "candidate_version": (
            "v1"
            if {"wikipedia", "douban", "tvdb"}.issubset(
                link.provider for link in anchored.source_links
            )
            else "v0"
        ),
        "anchor_hydrated": True,
        "metadata_hydrated": metadata_hydrated,
    })
    if not metadata_hydrated:
        result["prowlarr_queries"] = []
    return result


def _scope_coordinates(anchored) -> tuple[int | None, int | None]:
    scoped = next(
        (
            link
            for link in anchored.source_links
            if link.fact_id == anchored.anchor_fact_id
            and link.role in {"season", "episode"}
        ),
        next(
            (
                link
                for link in anchored.source_links
                if link.role == anchored.intended_scope
            ),
            None,
        ),
    )
    if scoped is None:
        return None, None
    return (
        scoped.season_number or scoped.proposed_season_number,
        scoped.episode_number or scoped.proposed_episode_number,
    )


def _identity_validated_intermediate_contract(
    anchored,
    *,
    metadata_id: str,
    raw_query: str,
) -> dict:
    """Validate every strict field except the known-missing bounded scope."""

    identity_links = tuple(
        replace(
            link,
            role=(
                "series_root"
                if link.role in {"season", "episode"}
                else link.role
            ),
            season_number=None,
            episode_number=None,
            verification=(
                "fact_verified"
                if link.verification == "unresolved_scope_link"
                else link.verification
            ),
            proposed_season_number=None,
            proposed_episode_number=None,
        )
        for link in anchored.source_links
    )
    identity_candidate = replace(
        anchored,
        intended_scope="whole_series",
        source_links=identity_links,
        unresolved_sources=tuple(
            item
            for item in anchored.unresolved_sources
            if "unresolved_scope_link" not in _text(item)
        ),
    )
    contract = _strict_contract(
        identity_candidate,
        metadata_id=metadata_id,
        raw_query=raw_query,
    )
    season_number, episode_number = _scope_coordinates(anchored)
    contract["retrieval"].update({
        "scope": anchored.intended_scope,
        "query": "",
        "queries": [],
    })
    evidence = contract.get("evidence") or {}
    evidence["source_links"] = [
        link.to_dict() for link in anchored.source_links
    ]
    evidence["unresolved"] = list(anchored.unresolved_sources)
    decision = evidence.get("decision") or {}
    decision.update({
        "scope": anchored.intended_scope,
        "season_number": season_number,
        "episode_number": episode_number,
    })
    evidence["decision"] = decision
    contract["evidence"] = evidence
    anchor_link = next(
        (
            link
            for link in anchored.source_links
            if link.fact_id == anchored.anchor_fact_id
        ),
        anchored.source_links[0],
    )
    contract["source_entry"]["verification"] = anchor_link.verification
    return contract


def hydrate_frozen_candidate_anchor(
    candidate: dict,
    *,
    metadata_id: str,
    raw_query: str,
    require_anchor: bool = False,
    resolver=resolve_direct_link,
) -> dict:
    """Exact-read a frozen anchor, allowing only missing verified scope."""

    anchored = _materialize_exact_candidate(
        candidate,
        require_anchor=require_anchor,
        resolver=resolver,
    )
    try:
        contract = build_media_metadata_v1(
            anchored,
            metadata_id=metadata_id,
            raw_query=raw_query,
        )
    except MetadataV1Error as exc:
        if not (
            exc.code == "metadata_incomplete"
            and exc.missing_fields == ("verified_scope",)
        ):
            raise CandidateHydrationError(
                exc.code,
                exc.missing_fields,
            ) from exc
        contract = _identity_validated_intermediate_contract(
            anchored,
            metadata_id=metadata_id,
            raw_query=raw_query,
        )
        return _candidate_result(
            candidate,
            anchored,
            contract,
            metadata_hydrated=False,
        )
    return _candidate_result(
        candidate,
        anchored,
        contract,
        metadata_hydrated=True,
    )


def hydrate_frozen_candidate(
    candidate: dict,
    *,
    metadata_id: str,
    raw_query: str,
    require_anchor: bool = False,
    resolver=resolve_direct_link,
) -> dict:
    """Return a strict hydrated candidate using only saved source URLs."""

    anchored = _materialize_exact_candidate(
        candidate,
        require_anchor=require_anchor,
        resolver=resolver,
    )
    contract = _strict_contract(
        anchored,
        metadata_id=metadata_id,
        raw_query=raw_query,
    )
    return _candidate_result(
        candidate,
        anchored,
        contract,
        metadata_hydrated=True,
    )

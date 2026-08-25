"""Deterministic root-work discovery from Wikipedia and Wikidata."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Callable

from .anchored_candidate import materialize_anchored_candidates
from .entity_graph import normalize_title
from .input_contract import ParsedInput
from .input_contract import classify_search_input
from .media_metadata_v1 import MetadataV1Error, build_media_metadata_v1
from .candidate_preview import anchored_fact_snapshot, candidate_preview_metadata
from .entity_graph import build_discovery_graph
from .errors import SearchPlanningError


_CJK = re.compile(r"[\u3400-\u9fff]")
_LEAD_QUOTED_TITLE = re.compile(r"[《“\"]([^》”\"]+)[》”\"]")


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _unique(values) -> list[str]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _lead_title_aliases(page: dict) -> list[str]:
    lead = _text(page.get("extract"))[:240]
    return _unique(
        match.group(1)
        for match in _LEAD_QUOTED_TITLE.finditer(lead)
    )


def _entity_title_relevant(entity: dict, expected_title: str) -> bool:
    titles = _unique((
        entity.get("chinese_title"),
        entity.get("english_title"),
        *(entity.get("aliases") or ()),
    ))
    return any(
        expected_title in normalized or normalized in expected_title
        for normalized in (normalize_title(title) for title in titles)
        if normalized
    )


def _query_payload(
    parsed: ParsedInput,
    *,
    languages: tuple[str, ...] = ("zh", "en"),
) -> dict:
    query = _text(parsed.raw_query)
    return {
        "source_queries": {
            "wikipedia_zh": [query] if query and "zh" in languages else [],
            "wikipedia_en": [query] if query and "en" in languages else [],
        }
    }


def _structurally_eligible(parsed: ParsedInput, entity: dict) -> bool:
    media_type = _text(entity.get("media_type")).casefold()
    year = _text(entity.get("year"))[:4]
    if media_type not in {"movie", "series"}:
        return False
    if parsed.media_type and parsed.media_type != media_type:
        return False
    if parsed.year and parsed.year != year:
        return False
    return not (
        parsed.scope in {"whole_series", "season", "episode"}
        and media_type != "series"
    )


def _preferred_page(pages: list[dict]) -> dict:
    return min(
        pages,
        key=lambda page: (
            0 if _text(page.get("language")).startswith("zh") else 1,
            int(page.get("search_rank") or 1_000_000),
            int(page.get("page_id") or 0),
        ),
    )


def _country_labels(
    country_ids: list[str],
    wikidata_lookup: Callable[[list[str]], dict[str, dict]],
) -> dict[str, str]:
    if not country_ids:
        return {}
    try:
        entities = wikidata_lookup(country_ids)
    except Exception:
        return {}
    result = {}
    for country_id in country_ids:
        entity = entities.get(country_id) if isinstance(entities, dict) else None
        if not isinstance(entity, dict):
            continue
        label = _text(
            entity.get("chinese_title")
            or entity.get("english_title")
        )
        if label:
            result[country_id] = label
    return result


def discover_root_works(
    parsed: ParsedInput,
    wikipedia_lookup: Callable[[dict], dict],
    wikidata_lookup: Callable[[list[str]], dict[str, dict]],
    *,
    wikidata_search=None,
) -> list[dict]:
    """Return structurally valid roots in Wikipedia search-rank order."""

    if parsed.kind != "text" or not _text(parsed.title):
        return []

    pages_by_qid: dict[str, list[dict]] = {}
    first_order: dict[str, int] = {}
    next_order = 0

    def collect_wikipedia(language: str) -> list[str]:
        nonlocal next_order
        try:
            result = wikipedia_lookup(
                _query_payload(parsed, languages=(language,))
            )
        except Exception:
            result = {}
        facts = (
            result.get("facts") or ()
            if isinstance(result, dict) and result.get("status") == "ok"
            else ()
        )
        collected = []
        for raw in facts:
            if not isinstance(raw, dict) or raw.get("is_disambiguation") is True:
                continue
            fact_language = _text(raw.get("language")).casefold()
            if fact_language and not fact_language.startswith(language):
                continue
            qid = _text(
                raw.get("wikibase_item")
                or (raw.get("external_ids") or {}).get("wikidata")
            ).upper()
            if not (qid.startswith("Q") and qid[1:].isdigit()):
                continue
            pages_by_qid.setdefault(qid, []).append(deepcopy(raw))
            first_order.setdefault(qid, next_order)
            next_order += 1
            if qid not in collected:
                collected.append(qid)
        return collected

    zh_qids = collect_wikipedia("zh")
    looked_up = wikidata_lookup(zh_qids) if zh_qids else {}
    entities = dict(looked_up) if isinstance(looked_up, dict) else {}
    expected_title = normalize_title(parsed.title)

    def normalized_titles(qid: str, entity: dict) -> set[str]:
        pages = pages_by_qid.get(qid) or ()
        values = _unique((
            entity.get("chinese_title"),
            entity.get("english_title"),
            *(entity.get("aliases") or ()),
            *(page.get("title") for page in pages),
            *(page.get("canonical_title") for page in pages),
            *(
                title
                for page in pages
                for title in _lead_title_aliases(page)
            ),
            *(alias for page in pages for alias in page.get("aliases") or ()),
        ))
        return {
            normalize_title(value) for value in values
            if normalize_title(value)
        }

    def ranked_wikipedia_admitted(qid: str) -> bool:
        titles = normalized_titles(qid, entities.get(qid) or {})
        if expected_title in titles:
            return True
        pages = pages_by_qid.get(qid) or ()
        best_rank = min(
            (int(page.get("search_rank") or 1_000_000) for page in pages),
            default=1_000_000,
        )
        return bool(
            best_rank == 1
            and expected_title
            and any(title.startswith(expected_title) for title in titles)
        )

    if not any(
        _structurally_eligible(parsed, entities.get(qid) or {})
        and ranked_wikipedia_admitted(qid)
        for qid in zh_qids
    ):
        en_qids = collect_wikipedia("en")
        missing = [qid for qid in en_qids if qid not in entities]
        if missing:
            looked_up = wikidata_lookup(missing)
            if isinstance(looked_up, dict):
                entities.update(looked_up)

    search_qids: list[str] = []
    if wikidata_search is not None:
        try:
            found = wikidata_search(parsed.title)
        except Exception:
            found = ()
        for raw_qid in found or ():
            qid = _text(raw_qid).upper()
            if (
                qid.startswith("Q")
                and qid[1:].isdigit()
                and qid not in search_qids
            ):
                search_qids.append(qid)

    seed_qids = list(pages_by_qid)
    seed_qids.extend(qid for qid in search_qids if qid not in seed_qids)
    if not seed_qids:
        return []
    missing = [qid for qid in seed_qids if qid not in entities]
    if missing:
        looked_up = wikidata_lookup(missing)
        if isinstance(looked_up, dict):
            entities.update(looked_up)
    exact_seed_qids = [
        qid for qid in seed_qids
        if isinstance(entities.get(qid), dict)
        and expected_title in normalized_titles(qid, entities[qid])
    ]

    relation_paths: dict[str, list[dict]] = {}
    queue = [(qid, 0) for qid in exact_seed_qids]
    expanded = set()
    entity_budget = 60
    while queue and len(entities) < entity_budget:
        source_qid, depth = queue.pop(0)
        if source_qid in expanded or depth >= 2:
            continue
        expanded.add(source_qid)
        entity = entities.get(source_qid)
        if not isinstance(entity, dict):
            continue
        relations = [("adaptation", value) for value in (
            entity.get("adaptation_ids") or ()
        )]
        if not _text(entity.get("media_type")):
            relations.extend(
                ("part", value) for value in entity.get("part_ids") or ()
            )
        targets = []
        edges = []
        for property_name, raw_target in relations:
            target = _text(raw_target).upper()
            if not (target.startswith("Q") and target[1:].isdigit()):
                continue
            if target in relation_paths:
                continue
            edge = {
                "from_qid": source_qid,
                "to_qid": target,
                "property": property_name,
                "depth": depth + 1,
            }
            relation_paths[target] = [
                *relation_paths.get(source_qid, ()),
                edge,
            ]
            targets.append(target)
            edges.append(edge)
            if len(entities) + len(targets) >= entity_budget:
                break
        missing = [target for target in targets if target not in entities]
        if missing:
            related = wikidata_lookup(missing)
            if isinstance(related, dict):
                entities.update(related)
        for edge in edges:
            if isinstance(entities.get(edge["to_qid"]), dict):
                queue.append((edge["to_qid"], depth + 1))

    selectable_qids = []
    for qid in [*seed_qids, *relation_paths]:
        entity = entities.get(qid)
        if not isinstance(entity, dict):
            continue
        is_exact = qid in exact_seed_qids
        is_ranked_wikipedia_result = ranked_wikipedia_admitted(qid)
        is_related = qid in relation_paths
        if not (is_ranked_wikipedia_result or is_exact or is_related):
            continue
        if not _structurally_eligible(parsed, entity):
            continue
        if qid not in selectable_qids:
            selectable_qids.append(qid)

    country_ids = _unique(
        country
        for qid in selectable_qids
        for country in (entities[qid].get("countries") or ())
    )
    country_labels = _country_labels(country_ids, wikidata_lookup)
    candidates = []
    for qid in selectable_qids:
        entity = entities[qid]
        pages = pages_by_qid.get(qid) or ()
        page = _preferred_page(list(pages)) if pages else {}
        provider = "wikipedia" if pages else "wikidata"
        chinese_title = _text(entity.get("chinese_title"))
        english_title = _text(
            entity.get("english_title")
            or page.get("official_english_title")
            or page.get("english_title")
        )
        display_title = chinese_title or english_title
        if not display_title:
            continue
        aliases = _unique((
            *(entity.get("aliases") or ()),
            *(page.get("aliases") or ()),
        ))
        countries = [
            country_labels.get(value, value)
            for value in _unique(entity.get("countries") or ())
        ]
        year = _text(entity.get("year"))[:4]
        media_type = _text(entity.get("media_type")).casefold()
        source_url = _text(page.get("url")) or (
            f"https://www.wikidata.org/wiki/{qid}"
        )
        external_ids = {
            **dict(entity.get("external_ids") or {}),
            "wikidata": qid,
            **({"wikipedia": qid} if pages else {}),
        }
        source_fact = {
            "language": _text(page.get("language")),
            "search_rank": int(page.get("search_rank") or 1_000_000),
            "page_id": int(page.get("page_id") or 0),
            "is_disambiguation": False,
            "title": display_title,
            "chinese_title": chinese_title,
            "english_title": english_title,
            "official_english_title": english_title,
            "aliases": list(aliases),
            "extract": _text(page.get("extract")),
            "url": source_url,
            "wikibase_item": qid,
            "external_ids": external_ids,
            "year": year,
            "media_type": media_type,
            "countries": countries,
            "genres": list(entity.get("genres") or ()),
            "original_language": _text(entity.get("original_language")),
            "season_count": entity.get("season_count"),
            "episode_count": entity.get("episode_count"),
            "cover_url": _text(page.get("cover_url")),
            "source_provider": provider,
        }
        exact = qid in exact_seed_qids
        path = list(relation_paths.get(qid) or ())
        candidates.append({
            "qid": qid,
            "source_provider": provider,
            "display_title": display_title,
            "chinese_title": chinese_title,
            "english_title": english_title,
            "aliases": aliases,
            "year": year,
            "countries": countries,
            "media_type": media_type,
            "season_count": entity.get("season_count"),
            "episode_count": entity.get("episode_count"),
            "external_ids": external_ids,
            "poster_url": _text(page.get("cover_url")),
            "source_url": source_url,
            "search_rank": int(page.get("search_rank") or 1_000_000),
            "page_id": int(page.get("page_id") or 0),
            "exact_title": exact,
            "relation_path": path,
            "score_reasons": _unique((
                "exact_title" if exact else "verified_relation",
                "explicit_year" if parsed.year else "",
                "explicit_media_type" if parsed.media_type else "",
                "chinese_page"
                if _text(page.get("language")).startswith("zh") else "",
            )),
            "source_fact": source_fact,
            "_sort": (
                0 if exact else 1,
                len(path),
                first_order.get(qid, 1_000_000),
                seed_qids.index(qid) if qid in seed_qids else 1_000_000,
                qid,
            ),
        })
    candidates.sort(key=lambda candidate: candidate["_sort"])
    for candidate in candidates:
        candidate.pop("_sort", None)
    return candidates[:40]


def _plan_candidate(
    root: dict,
    *,
    plan_id: str,
    raw_query: str,
    parsed: ParsedInput,
) -> dict:
    provider = _text(root.get("source_provider")).casefold() or "wikipedia"
    fact = deepcopy(root["source_fact"])
    graph = build_discovery_graph([{
        "source": provider,
        "status": "ok",
        "facts": [fact],
        "source_urls": [root["source_url"]],
        "error": "",
    }])
    graph_fact = graph.candidates[0].facts[0]
    role = "movie" if root["media_type"] == "movie" else (
        parsed.scope
        if parsed.scope in {"season", "episode"}
        else "series_root"
    )
    intended_scope = (
        "movie"
        if root["media_type"] == "movie"
        else parsed.scope
        if parsed.scope in {"whole_series", "season", "episode"}
        else "work"
    )
    anchored = materialize_anchored_candidates(
        graph,
        {
            "status": "resolved",
            "candidates": [{
                "candidate_id": f"{provider}:{root['qid']}",
                "anchor_fact_id": graph_fact.fact_id,
                "identity_role": role,
                "intended_scope": intended_scope,
                "fact_bindings": [{
                    "fact_id": graph_fact.fact_id,
                    "role": role,
                    "season_number": (
                        parsed.season_number
                        if role in {"season", "episode"}
                        else None
                    ),
                    "episode_number": (
                        parsed.episode_number if role == "episode" else None
                    ),
                }],
                "ai_confidence": 0.0,
                "ai_reason": "deterministic_wikidata_root",
            }],
        },
        provider_statuses={provider: "ok"},
        locked_anchor_fact_id=graph_fact.fact_id,
    )[0]
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
    identity = contract.setdefault("identity", {})
    identity.update({
        "chinese_title": root["chinese_title"] or root["display_title"],
        "english_title": root["english_title"],
        "official_english_title": root["english_title"],
        "aliases": list(root["aliases"]),
        "year": root["year"],
        "content_kind": root["media_type"],
        "countries": list(root["countries"]),
        "season_count": root["season_count"],
        "episode_count": root["episode_count"],
        "poster_url": root["poster_url"],
        "external_ids": {
            **dict(identity.get("external_ids") or {}),
            **dict(root.get("external_ids") or {}),
            "wikidata": root["qid"],
            **(
                {"wikipedia": root["qid"]}
                if provider == "wikipedia"
                else {}
            ),
        },
    })
    contract.setdefault("placement", {})["library_type"] = root["media_type"]
    return {
        "candidate_key": f"{provider}:{root['qid']}",
        "candidate_id": f"{provider}:{root['qid']}",
        "anchor_fact_id": anchored.anchor_fact_id,
        "identity_role": role,
        "intended_scope": intended_scope,
        "score": {"total": 100 if root["exact_title"] else 80},
        "recommended": False,
        "selectable": True,
        "media_metadata": contract,
        "prowlarr_queries": list(
            (contract.get("retrieval") or {}).get("queries") or []
        ),
        "poster_url": root["poster_url"],
        "poster_assets": [
            item.to_dict() for item in anchored.poster_assets
        ],
        "source_links": [
            item.to_dict() for item in anchored.source_links
        ],
        "unresolved_sources": list(anchored.unresolved_sources),
        "ai_confidence": 0.0,
        "ai_reason": "deterministic_wikidata_root",
        "reasons": list(root["score_reasons"]),
        "candidate_version": "wikipedia-wikidata-root-v2",
        "metadata_ready": metadata_ready,
        "metadata_error": metadata_error,
        "links_frozen": True,
        "requested_season_number": parsed.season_number,
        "requested_episode_number": parsed.episode_number,
        "fact_snapshot": anchored_fact_snapshot(anchored),
        "entity_snapshot": {
            "entity_key": f"{provider}:{root['qid']}",
            "content_kind": root["media_type"],
            "external_ids": dict(identity["external_ids"]),
        },
        "relation_snapshot": {
            "relation_type": (
                "verified_wikidata_relation"
                if root.get("relation_path")
                else "standalone"
            ),
            "mapping_kind": (
                "verified_relation"
                if root.get("relation_path")
                else "standalone"
            ),
            "path": deepcopy(root.get("relation_path") or []),
        },
    }


def build_root_work_search_plan(
    raw_query: str,
    plan_id: str,
    wikipedia_lookup: Callable[[dict], dict],
    wikidata_lookup: Callable[[list[str]], dict[str, dict]],
    wikidata_search=None,
) -> dict:
    parsed = classify_search_input(raw_query)
    if parsed.kind != "text":
        raise SearchPlanningError(parsed.reason or "invalid_query")
    roots = discover_root_works(
        parsed,
        wikipedia_lookup,
        wikidata_lookup,
        wikidata_search=wikidata_search,
    )
    if not roots:
        raise SearchPlanningError("no_match")
    candidates = [
        _plan_candidate(
            root,
            plan_id=plan_id,
            raw_query=raw_query,
            parsed=parsed,
        )
        for root in roots
    ]
    top = candidates[0]
    return {
        "plan_id": plan_id,
        "search_session_id": plan_id,
        "raw_query": raw_query,
        "entry_kind": "text",
        "links_frozen": True,
        "auto_confirm": False,
        "selection_mode": "user_root_identity",
        "media_metadata": deepcopy(top["media_metadata"]),
        "prowlarr_queries": list(top["prowlarr_queries"]),
        "candidates": candidates,
        "source_queries": _query_payload(parsed)["source_queries"],
        "scoring_version": "wikipedia-wikidata-root-v2",
        "relation_pool": [
            deepcopy(edge)
            for root in roots
            for edge in root.get("relation_path") or ()
        ],
        "discovery_summary": {
            "candidate_count": len(candidates),
            "exact_count": sum(
                1 for root in roots if root.get("exact_title")
            ),
            "relation_count": sum(
                1 for root in roots if root.get("relation_path")
            ),
            "candidate_limit": 40,
        },
    }

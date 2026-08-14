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


def _query_payload(parsed: ParsedInput) -> dict:
    if not parsed.media_type:
        base = _text(" ".join(filter(None, (parsed.title, parsed.year))))
        return {
            "source_queries": {
                "wikipedia_zh": [
                    f"{base} 电视剧",
                    f"{base} 电影",
                ] if base else [],
                "wikipedia_en": [
                    f"{base} TV series",
                    f"{base} film",
                ] if base else [],
            }
        }
    zh_suffix = ""
    en_suffix = ""
    if parsed.media_type == "movie":
        zh_suffix = "电影"
        en_suffix = "film"
    elif parsed.media_type == "series":
        zh_suffix = "电视剧"
        en_suffix = "TV series"
    base_query = _text(" ".join(filter(None, (
        parsed.title,
        parsed.year,
    ))))
    zh_query = _text(" ".join(filter(None, (
        parsed.title,
        parsed.year,
        zh_suffix,
    ))))
    en_query = _text(" ".join(filter(None, (
        parsed.title,
        parsed.year,
        en_suffix,
    ))))
    return {
        "source_queries": {
            "wikipedia_zh": _unique((zh_query, parsed.title)),
            "wikipedia_en": _unique((en_query, parsed.title)),
        }
    }


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
    _allow_retry: bool = True,
) -> list[dict]:
    """Return roots verified by exact titles or bounded Wikidata edges."""

    if parsed.kind != "text" or not _text(parsed.title):
        return []

    try:
        wikipedia_result = wikipedia_lookup(_query_payload(parsed))
    except Exception:
        wikipedia_result = {}
    wikipedia_facts = (
        wikipedia_result.get("facts") or ()
        if isinstance(wikipedia_result, dict)
        and wikipedia_result.get("status") == "ok"
        else ()
    )
    pages_by_qid: dict[str, list[dict]] = {}
    first_order: dict[str, int] = {}
    for order, raw in enumerate(wikipedia_facts):
        if not isinstance(raw, dict) or raw.get("is_disambiguation") is True:
            continue
        qid = _text(
            raw.get("wikibase_item")
            or (raw.get("external_ids") or {}).get("wikidata")
        ).upper()
        if not (qid.startswith("Q") and qid[1:].isdigit()):
            continue
        pages_by_qid.setdefault(qid, []).append(deepcopy(raw))
        first_order.setdefault(qid, order)

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
    looked_up = wikidata_lookup(seed_qids)
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
        is_related = qid in relation_paths
        if not (is_exact or is_related):
            continue
        media_type = _text(entity.get("media_type")).casefold()
        year = _text(entity.get("year"))[:4]
        if media_type not in {"movie", "series"}:
            continue
        if parsed.media_type and parsed.media_type != media_type:
            continue
        if parsed.year and parsed.year != year:
            continue
        if (
            parsed.scope in {"whole_series", "season", "episode"}
            and media_type != "series"
        ):
            continue
        if qid not in selectable_qids:
            selectable_qids.append(qid)

    if not selectable_qids and parsed.media_type and _allow_retry:
        retry_titles = []
        for qid in exact_seed_qids:
            entity = entities.get(qid) or {}
            for value in (
                entity.get("english_title"),
                *(entity.get("aliases") or ()),
            ):
                value = _text(value)
                if (
                    re.search(r"[A-Za-z]", value)
                    and normalize_title(value) != expected_title
                    and value not in retry_titles
                ):
                    retry_titles.append(value)
        if retry_titles:
            retry_parsed = ParsedInput(
                kind="text",
                raw_query=parsed.raw_query,
                title=retry_titles[0],
                year=parsed.year,
                media_type=parsed.media_type,
                scope=parsed.scope,
                season_number=parsed.season_number,
                episode_number=parsed.episode_number,
            )
            retry_result = wikipedia_lookup(_query_payload(retry_parsed))
            roots = discover_root_works(
                retry_parsed,
                lambda _payload: retry_result,
                wikidata_lookup,
                wikidata_search=wikidata_search,
                _allow_retry=False,
            )
            for root in roots:
                if parsed.title not in root["aliases"]:
                    root["aliases"].append(parsed.title)
                    root["source_fact"]["aliases"].append(parsed.title)
            return roots

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

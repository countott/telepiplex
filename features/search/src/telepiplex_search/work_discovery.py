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
    """Return structurally verified movie/series roots in deterministic order."""

    if parsed.kind != "text" or not _text(parsed.title):
        return []
    result = wikipedia_lookup(_query_payload(parsed))
    if not isinstance(result, dict) or result.get("status") != "ok":
        return []

    pages_by_qid: dict[str, list[dict]] = {}
    first_order: dict[str, int] = {}
    for order, raw in enumerate(result.get("facts") or ()): 
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
    if not pages_by_qid:
        return []

    entities = wikidata_lookup(list(pages_by_qid))
    if not isinstance(entities, dict):
        return []
    alias_search_titles = []
    expected_normalized = normalize_title(parsed.title)
    for qid, pages in pages_by_qid.items():
        entity = entities.get(qid)
        if not isinstance(entity, dict):
            continue
        candidate_titles = _unique((
            entity.get("chinese_title"),
            *(entity.get("aliases") or ()),
            *(
                page.get("title")
                for page in pages
                if isinstance(page, dict)
            ),
        ))
        if not any(
            expected_normalized in normalize_title(value)
            or normalize_title(value) in expected_normalized
            for value in candidate_titles
            if normalize_title(value)
        ):
            continue
        for value in (
            entity.get("chinese_title"),
            entity.get("english_title"),
            *(entity.get("aliases") or ()),
        ):
            value = _text(value)
            if value and normalize_title(value) != expected_normalized:
                alias_search_titles.append(value)
    has_structural_match = any(
        isinstance(entity, dict)
        and _text(entity.get("media_type")).casefold()
        in (
            {parsed.media_type}
            if parsed.media_type
            else {"movie", "series"}
        )
        and (
            not parsed.year
            or _text(entity.get("year"))[:4] == parsed.year
        )
        for entity in entities.values()
    )
    if not has_structural_match and parsed.media_type and _allow_retry:
        retry_title_options = []
        for qid, pages in pages_by_qid.items():
            entity = entities.get(qid)
            if not isinstance(entity, dict):
                continue
            for value in (
                entity.get("english_title"),
                *(entity.get("aliases") or ()),
            ):
                value = _text(value)
                if (
                    value
                    and re.search(r"[A-Za-z]", value)
                    and normalize_title(value) != normalize_title(parsed.title)
                    and all(
                        value != item[2] for item in retry_title_options
                    )
                ):
                    retry_title_options.append((
                        0
                        if normalize_title(parsed.title) in {
                            normalize_title(title)
                            for title in (
                                entity.get("chinese_title"),
                                *(entity.get("aliases") or ()),
                                *(
                                    page.get("title")
                                    for page in pages
                                    if isinstance(page, dict)
                                ),
                            )
                            if _text(title)
                        }
                        else 1,
                        min(
                            int(page.get("search_rank") or 1_000_000)
                            for page in pages
                            if isinstance(page, dict)
                        ),
                        value,
                    ))
        retry_title_options.sort()
        if retry_title_options:
            retry_parsed = ParsedInput(
                kind="text",
                raw_query=parsed.raw_query,
                title=retry_title_options[0][2],
                year=parsed.year,
                media_type=parsed.media_type,
                scope=parsed.scope,
                season_number=parsed.season_number,
                episode_number=parsed.episode_number,
            )
            retry = wikipedia_lookup(_query_payload(retry_parsed))
            if isinstance(retry, dict) and retry.get("status") == "ok":
                retry_roots = discover_root_works(
                    retry_parsed,
                    lambda _payload: retry,
                    wikidata_lookup,
                    wikidata_search=wikidata_search,
                    _allow_retry=False,
                )
                if retry_roots:
                    for root in retry_roots:
                        if normalize_title(parsed.title) not in {
                            normalize_title(value)
                            for value in root.get("aliases") or ()
                        }:
                            root.setdefault("aliases", []).append(parsed.title)
                            root["source_fact"].setdefault("aliases", []).append(
                                parsed.title
                            )
                    return retry_roots
    expected_title = normalize_title(parsed.title)
    candidates = []
    country_ids = []
    for qid, pages in pages_by_qid.items():
        entity = entities.get(qid)
        if not isinstance(entity, dict):
            continue
        media_type = _text(entity.get("media_type")).casefold()
        year = _text(entity.get("year"))[:4]
        if media_type not in {"movie", "series"}:
            continue
        if parsed.media_type and parsed.media_type != media_type:
            continue
        if parsed.year and parsed.year != year:
            continue
        if parsed.scope in {"whole_series", "season", "episode"} and media_type != "series":
            continue

        page = _preferred_page(pages)
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
        titles = _unique((
            chinese_title,
            english_title,
            page.get("title"),
            page.get("canonical_title"),
            *_lead_title_aliases(page),
            *aliases,
        ))
        normalized_titles = {
            normalize_title(title) for title in titles if _text(title)
        }
        if not any(
            expected_title in title or title in expected_title
            for title in normalized_titles
            if title
        ):
            continue
        exact = expected_title in normalized_titles
        ids = _unique(entity.get("countries") or ())
        country_ids.extend(
            country_id for country_id in ids if country_id not in country_ids
        )
        source_url = _text(page.get("url"))
        source_provider = _text(
            page.get("source_provider")
        ).casefold() or "wikipedia"
        source_fact = {
            "language": _text(page.get("language")),
            "search_rank": int(page.get("search_rank") or 1_000_000),
            "page_id": int(page.get("page_id") or 0),
            "is_disambiguation": False,
            "title": display_title,
            "chinese_title": chinese_title,
            "english_title": english_title,
            "official_english_title": english_title,
            "aliases": aliases,
            "extract": _text(page.get("extract")),
            "url": source_url,
            "wikibase_item": qid,
            "external_ids": {
                **dict(entity.get("external_ids") or {}),
                "wikidata": qid,
                **(
                    {"wikipedia": qid}
                    if source_provider == "wikipedia"
                    else {}
                ),
            },
            "year": year,
            "media_type": media_type,
            "countries": ids,
            "genres": list(entity.get("genres") or ()),
            "original_language": _text(entity.get("original_language")),
            "season_count": entity.get("season_count"),
            "episode_count": entity.get("episode_count"),
            "external_ids": dict(entity.get("external_ids") or {}),
            "cover_url": _text(page.get("cover_url")),
        }
        candidates.append({
            "qid": qid,
            "source_provider": source_provider,
            "display_title": display_title,
            "chinese_title": chinese_title,
            "english_title": english_title,
            "aliases": aliases,
            "year": year,
            "countries": ids,
            "media_type": media_type,
            "season_count": entity.get("season_count"),
            "episode_count": entity.get("episode_count"),
            "poster_url": _text(page.get("cover_url")),
            "source_url": source_url,
            "search_rank": min(
                int(item.get("search_rank") or 1_000_000)
                for item in pages
            ),
            "page_id": int(page.get("page_id") or 0),
            "exact_title": exact,
            "score_reasons": [
                reason for reason, matched in (
                    ("exact_title", exact),
                    ("explicit_year", bool(parsed.year)),
                    ("explicit_media_type", bool(parsed.media_type)),
                    ("chinese_page", _text(page.get("language")).startswith("zh")),
                ) if matched
            ],
            "source_fact": source_fact,
            "_first_order": first_order[qid],
        })

    labels = _country_labels(country_ids, wikidata_lookup)
    for candidate in candidates:
        candidate["countries"] = [
            labels.get(value, value) for value in candidate["countries"]
        ]
        candidate["source_fact"]["countries"] = list(candidate["countries"])
    candidates.sort(key=lambda candidate: (
        0 if candidate["exact_title"] else 1,
        candidate["_first_order"],
        candidate["search_rank"],
        candidate["page_id"],
    ))
    for candidate in candidates:
        candidate.pop("_first_order", None)
    if not candidates and wikidata_search is not None:
        search_titles = [parsed.title, *alias_search_titles[:3]]
        if "retry_title_options" in locals():
            search_titles.extend(
                item[2] for item in retry_title_options[:1]
            )
        qids = []
        primary_relation_sources = []
        for qid, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            if not _entity_title_relevant(entity, expected_normalized):
                continue
            if qid not in qids:
                qids.append(qid)
            if qid not in primary_relation_sources:
                primary_relation_sources.append(qid)
        for title in _unique(search_titles):
            try:
                found = wikidata_search(title)
            except Exception:
                continue
            for index, qid in enumerate(found or ()):
                qid = _text(qid).upper()
                if qid.startswith("Q") and qid[1:].isdigit() and qid not in qids:
                    qids.append(qid)
                if (
                    index == 0
                    and qid.startswith("Q")
                    and qid[1:].isdigit()
                    and qid not in primary_relation_sources
                ):
                    primary_relation_sources.append(qid)
        looked_up = wikidata_lookup(qids)
        structural = {
            **entities,
            **(looked_up if isinstance(looked_up, dict) else {}),
        }
        relevant_part_qids = []
        for qid in primary_relation_sources:
            entity = structural.get(qid)
            if not isinstance(entity, dict):
                continue
            for part_qid in entity.get("part_ids") or ():
                part_qid = _text(part_qid).upper()
                if (
                    part_qid.startswith("Q")
                    and part_qid[1:].isdigit()
                    and part_qid not in relevant_part_qids
                ):
                    relevant_part_qids.append(part_qid)
        if relevant_part_qids:
            parts = wikidata_lookup(relevant_part_qids)
            if isinstance(parts, dict):
                structural.update(parts)
                for qid in relevant_part_qids:
                    entity = parts.get(qid)
                    if (
                        isinstance(entity, dict)
                        and _entity_title_relevant(entity, expected_normalized)
                        and qid not in primary_relation_sources
                    ):
                        primary_relation_sources.append(qid)
        relation_verified_qids = set()
        adaptation_qids = []
        for qid in primary_relation_sources:
            entity = structural.get(qid) if isinstance(structural, dict) else None
            if not isinstance(entity, dict):
                continue
            for adaptation_qid in entity.get("adaptation_ids") or ():
                adaptation_qid = _text(adaptation_qid).upper()
                if (
                    adaptation_qid.startswith("Q")
                    and adaptation_qid[1:].isdigit()
                    and adaptation_qid not in adaptation_qids
                ):
                    adaptation_qids.append(adaptation_qid)
                    relation_verified_qids.add(adaptation_qid)
        if adaptation_qids:
            related = wikidata_lookup(adaptation_qids)
            if isinstance(related, dict):
                structural = {
                    **(structural if isinstance(structural, dict) else {}),
                    **related,
                }
            for qid in adaptation_qids:
                if qid not in qids:
                    qids.append(qid)
        facts = []
        expected_titles = {
            normalize_title(value)
            for value in _unique(search_titles)
        }
        for rank, qid in enumerate(qids, 1):
            entity = structural.get(qid) if isinstance(structural, dict) else None
            if not isinstance(entity, dict):
                continue
            media_type = _text(entity.get("media_type")).casefold()
            year = _text(entity.get("year"))[:4]
            titles = _unique((
                entity.get("chinese_title"),
                entity.get("english_title"),
                *(entity.get("aliases") or ()),
            ))
            if media_type not in {"movie", "series"}:
                continue
            if parsed.media_type and media_type != parsed.media_type:
                continue
            if parsed.year and year != parsed.year:
                continue
            if (
                qid not in relation_verified_qids
                and not expected_titles.intersection({
                    normalize_title(title) for title in titles
                })
            ):
                continue
            fact_aliases = list(entity.get("aliases") or ())
            if qid in relation_verified_qids and parsed.title not in fact_aliases:
                fact_aliases.append(parsed.title)
            facts.append({
                "language": "",
                "search_rank": rank,
                "page_id": rank,
                "is_disambiguation": False,
                "title": entity.get("chinese_title") or entity.get("english_title"),
                "chinese_title": entity.get("chinese_title"),
                "english_title": entity.get("english_title"),
                "official_english_title": entity.get("english_title"),
                "aliases": fact_aliases,
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "wikibase_item": qid,
                "external_ids": {
                    **dict(entity.get("external_ids") or {}),
                    "wikidata": qid,
                },
                "year": year,
                "media_type": media_type,
                "countries": list(entity.get("countries") or ()),
                "genres": list(entity.get("genres") or ()),
                "original_language": entity.get("original_language"),
                "season_count": entity.get("season_count"),
                "episode_count": entity.get("episode_count"),
                "source_provider": "wikidata",
            })
        if facts:
            return discover_root_works(
                parsed,
                lambda _payload: {
                    "source": "wikidata",
                    "status": "ok",
                    "facts": facts,
                },
                wikidata_lookup,
                wikidata_search=None,
                _allow_retry=False,
            )
    return candidates[:5]


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
        "candidate_version": "wikipedia-wikidata-root-v1",
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
            "relation_type": "standalone",
            "mapping_kind": "standalone",
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
        "scoring_version": "wikipedia-wikidata-root-v1",
        "relation_pool": [],
    }

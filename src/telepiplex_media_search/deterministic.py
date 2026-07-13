"""Strict rule-first planning for unambiguous ordinary media."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .search_resolution import parse_search_intent


COMPLEX_PATTERN = re.compile(
    r"(?i)\b(?:ova|special|spin[ -]?off|prequel|sequel)\b|"
    r"前传|前傳|续集|續集|特别篇|特別篇|番外|衍生|电影版|電影版|剧场版|劇場版"
)
ANIMATION_SIGNALS = ("动画", "動畫", "anime", "animation", "animated")


@dataclass(frozen=True)
class DeterministicResult:
    plan: dict | None
    reason_codes: tuple[str, ...]
    decision: dict


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _normalize_title(value) -> str:
    value = unicodedata.normalize("NFKC", _text(value)).casefold()
    value = re.sub(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", " ", value)
    value = re.sub(
        r"(?i)\bS\d{1,2}(?:E\d{1,3})?\b|"
        r"第?\s*[零〇一二两三四五六七八九十\d]+\s*季"
        r"(?:\s*第?\s*[零〇一二两三四五六七八九十\d]+\s*[集话話])?",
        " ",
        value,
    )
    value = re.sub(r"[\(（]\s*(?:电影|電影|film|movie|电视剧|電視劇|series)\s*[\)）]$", "", value)
    return "".join(character for character in value if character.isalnum())


def _clean_intent(raw_query: str) -> dict:
    intent = parse_search_intent(raw_query)
    title = _text(intent.get("title"))
    year = _text(intent.get("year"))
    if year:
        title = re.sub(rf"(?<!\d){re.escape(year)}(?!\d)", " ", title)
    intent["title"] = _text(title)
    return intent


def build_rule_hypotheses(raw_query: str) -> dict:
    intent = _clean_intent(raw_query)
    query = _text(" ".join(item for item in (intent["title"], intent.get("year", "")) if item))
    hypothesis = {
        "title": intent["title"],
        "year": intent.get("year") or "",
        "content_identity": "series" if intent.get("scope") in {"whole_series", "season", "episode"} else "movie_or_series",
        "scope": intent.get("scope"),
        "season_number": intent.get("season_number"),
        "episode_number": intent.get("episode_number"),
        "possible_related_series": [],
        "explicit_facts": [raw_query],
        "inferred_facts": [],
    }
    return {
        "status": "ok",
        "intent": intent,
        "hypotheses": [hypothesis],
        "source_queries": {
            "wikipedia": [query] if query else [],
            "douban": [query] if query else [],
            "tvdb": [query] if query else [],
        },
        "warnings": [],
    }


def _candidate(
    provider: str,
    *,
    titles: list[str],
    year: str = "",
    media_type: str = "",
    external_ids: dict | None = None,
    source_urls: list[str] | None = None,
    genres: list[str] | None = None,
    episodes: list[dict] | None = None,
    cover_url: str = "",
    complex_signals: list[str] | None = None,
) -> dict:
    clean_titles = []
    for title in titles:
        title = _text(title)
        if title and title not in clean_titles:
            clean_titles.append(title)
    return {
        "providers": {provider},
        "titles": clean_titles,
        "normalized_titles": {_normalize_title(item) for item in clean_titles if _normalize_title(item)},
        "years": {_text(year)} if _text(year) else set(),
        "media_types": {_text(media_type)} if _text(media_type) else set(),
        "external_ids": dict(external_ids or {}),
        "source_urls": list(source_urls or []),
        "genres": [_text(item) for item in (genres or []) if _text(item)],
        "episodes": list(episodes or []),
        "cover_url": _text(cover_url),
        "complex_signals": set(complex_signals or []),
    }


def _wikipedia_candidates(source: dict) -> list[dict]:
    grouped = {}
    for index, fact in enumerate(source.get("facts") or []):
        if not isinstance(fact, dict):
            continue
        key = _text(fact.get("wikibase_item")) or f"fact:{index}"
        item = grouped.setdefault(key, {
            "titles": [],
            "years": set(),
            "types": set(),
            "urls": [],
            "genres": [],
            "complex_signals": set(),
        })
        for field in ("title", "chinese_title", "english_title"):
            value = _text(fact.get(field))
            if value and value not in item["titles"]:
                item["titles"].append(value)
        if _text(fact.get("year")):
            item["years"].add(_text(fact["year"]))
        if _text(fact.get("media_type")):
            item["types"].add(_text(fact["media_type"]))
        if _text(fact.get("url")):
            item["urls"].append(_text(fact["url"]))
        extract = _text(fact.get("extract"))
        if COMPLEX_PATTERN.search(f"{fact.get('title') or ''} {extract}"):
            item["complex_signals"].add("wikipedia_relation")
        if any(signal in extract.casefold() for signal in ANIMATION_SIGNALS):
            item["genres"].append("animation")
    result = []
    for key, item in grouped.items():
        result.append(_candidate(
            "wikipedia",
            titles=item["titles"],
            year=next(iter(item["years"]), ""),
            media_type=next(iter(item["types"]), ""),
            external_ids={"wikipedia": key} if not key.startswith("fact:") else {},
            source_urls=item["urls"],
            genres=item["genres"],
            complex_signals=sorted(item["complex_signals"]),
        ))
        result[-1]["years"] = item["years"]
        result[-1]["media_types"] = item["types"]
    return result


def _douban_candidates(source: dict) -> list[dict]:
    result = []
    for fact in source.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        subject_id = _text(fact.get("subject_id"))
        external_ids = dict(fact.get("external_ids") or {})
        if subject_id:
            external_ids["douban_subject"] = subject_id
        result.append(_candidate(
            "douban",
            titles=[
                fact.get("title"),
                fact.get("chinese_title"),
                fact.get("english_title"),
                *(fact.get("aliases") or []),
            ],
            year=fact.get("year"),
            media_type=fact.get("media_type"),
            external_ids=external_ids,
            source_urls=[fact.get("url")] if fact.get("url") else [],
            genres=fact.get("genres") or [],
            cover_url=fact.get("cover_url"),
        ))
    return result


def _tvdb_candidates(source: dict) -> list[dict]:
    result = []
    for fact in source.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        episodes_by_series = fact.get("episodes_by_series") or {}
        for media_type, key in (("movie", "movies"), ("series", "series")):
            for entry in fact.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                entity_id = _text(
                    entry.get(f"tvdb_{media_type}_id")
                    or entry.get("tvdb_id")
                    or entry.get("id")
                )
                external_ids = {"tvdb": entity_id} if entity_id else {}
                episodes = (
                    episodes_by_series.get(entity_id) or []
                    if media_type == "series"
                    else []
                )
                result.append(_candidate(
                    "tvdb",
                    titles=[
                        entry.get("name"),
                        entry.get("english_title"),
                        *(entry.get("aliases") or []),
                    ],
                    year=entry.get("year"),
                    media_type=media_type,
                    external_ids=external_ids,
                    source_urls=[
                        f"https://thetvdb.com/{'series' if media_type == 'series' else 'movies'}/{entity_id}"
                    ] if entity_id else [],
                    genres=entry.get("genres") or [],
                    episodes=episodes,
                    cover_url=entry.get("cover_url"),
                ))
    return result


def _source_candidates(sources: list[dict]) -> list[dict]:
    result = []
    for source in sources or []:
        if not isinstance(source, dict) or source.get("status") != "ok":
            continue
        provider = _text(source.get("source")).casefold()
        if provider == "wikipedia":
            result.extend(_wikipedia_candidates(source))
        elif provider == "douban":
            result.extend(_douban_candidates(source))
        elif provider == "tvdb":
            result.extend(_tvdb_candidates(source))
    return result


def _merge_cluster(target: dict, source: dict) -> None:
    for key in (
        "providers",
        "normalized_titles",
        "years",
        "media_types",
        "complex_signals",
    ):
        target[key].update(source[key])
    for key in ("titles", "source_urls", "genres", "episodes"):
        for item in source[key]:
            if item not in target[key]:
                target[key].append(item)
    target["external_ids"].update(source["external_ids"])
    target["cover_url"] = target["cover_url"] or source["cover_url"]


def _clusters(candidates: list[dict]) -> list[dict]:
    result = []
    for candidate in candidates:
        matches = [
            existing
            for existing in result
            if existing["normalized_titles"].intersection(candidate["normalized_titles"])
        ]
        if not matches:
            result.append(candidate)
            continue
        primary = matches[0]
        _merge_cluster(primary, candidate)
        for extra in matches[1:]:
            _merge_cluster(primary, extra)
            result.remove(extra)
    return result


def _blocked(intent: dict, reasons: list[str], providers: set[str] | None = None) -> DeterministicResult:
    unique_reasons = tuple(dict.fromkeys(reasons))
    decision = {
        "mode": "undetermined",
        "gate_status": "failed",
        "scope": intent.get("scope") or "movie_or_series",
        "matched_providers": sorted(providers or set()),
        "candidate_count": 0,
        "reason_codes": list(unique_reasons),
        "ai_required": True,
        "ai_stage_one_status": "not_started",
        "ai_stage_two_status": "not_started",
    }
    return DeterministicResult(None, unique_reasons, decision)


def _episode_key(value: dict):
    try:
        return int(value.get("season_number")), int(value.get("episode_number"))
    except (TypeError, ValueError):
        return None


def _series_items(scope: str, intent: dict, episodes: list[dict]) -> list[dict]:
    selected = []
    for episode in episodes:
        key = _episode_key(episode)
        if not key or key[0] < 0 or key[1] < 1:
            continue
        if scope == "season" and key[0] != int(intent["season_number"]):
            continue
        if scope == "episode" and key != (
            int(intent["season_number"]),
            int(intent["episode_number"]),
        ):
            continue
        selected.append({
            "item_id": _text(episode.get("tvdb_episode_id")) or f"S{key[0]:02d}E{key[1]:03d}",
            "content_role": "main_episode",
            "season_number": key[0],
            "episode_number": key[1],
        })
    selected.sort(key=lambda item: (item["season_number"], item["episode_number"]))
    return selected


def _pick_title(values: list[str], *, chinese: bool) -> str:
    matches = [
        _text(item)
        for item in values
        if bool(re.search(r"[\u3400-\u9fff]", _text(item))) is chinese
        and (chinese or bool(re.search(r"[A-Za-z]", _text(item))))
    ]
    return min(matches, key=len) if matches else ""


def evaluate_deterministic_plan(
    plan_id: str,
    raw_query: str,
    sources: list[dict],
) -> DeterministicResult:
    intent = _clean_intent(raw_query)
    if COMPLEX_PATTERN.search(raw_query):
        return _blocked(intent, ["complex_identity_requires_ai"])

    clusters = _clusters(_source_candidates(sources))
    target_key = _normalize_title(intent.get("title"))
    matches = [
        item for item in clusters
        if target_key and target_key in item["normalized_titles"]
    ]
    if not matches:
        return _blocked(intent, ["insufficient_independent_support"])
    if len(matches) > 1:
        providers = set().union(*(item["providers"] for item in matches))
        return _blocked(intent, ["ambiguous_candidates"], providers)

    selected = matches[0]
    providers = selected["providers"]
    if selected["complex_signals"]:
        return _blocked(intent, ["complex_identity_requires_ai"], providers)
    if len(selected["years"]) > 1 or len(selected["media_types"]) > 1:
        return _blocked(intent, ["evidence_conflict"], providers)
    year = next(iter(selected["years"]), "")
    media_type = next(iter(selected["media_types"]), "")
    if intent.get("year") and year and intent["year"] != year:
        return _blocked(intent, ["evidence_conflict"], providers)
    if media_type not in {"movie", "series"}:
        return _blocked(intent, ["evidence_conflict"], providers)

    if media_type == "movie" and len(providers) < 2:
        return _blocked(intent, ["insufficient_independent_support"], providers)
    if media_type == "series" and not (
        "tvdb" in providers and providers.intersection({"wikipedia", "douban"})
    ):
        reason = "tvdb_identity_required" if "tvdb" not in providers else "insufficient_independent_support"
        return _blocked(intent, [reason], providers)

    chinese_title = _pick_title(selected["titles"], chinese=True)
    english_title = _pick_title(selected["titles"], chinese=False)
    if not chinese_title or not english_title:
        return _blocked(intent, ["missing_bilingual_identity"], providers)

    scope = "movie" if media_type == "movie" else (
        intent.get("scope") if intent.get("scope") != "movie_or_series" else "whole_series"
    )
    items = []
    if media_type == "series":
        tvdb_id = _text(selected["external_ids"].get("tvdb"))
        if not tvdb_id:
            return _blocked(intent, ["tvdb_identity_required"], providers)
        items = _series_items(scope, intent, selected["episodes"])
        if not items:
            return _blocked(intent, ["tvdb_scope_not_verified"], providers)

    animation = any(
        signal in _text(value).casefold()
        for value in selected["genres"]
        for signal in ANIMATION_SIGNALS
    )
    category_kind = (
        ("animated_" if animation else "live_action_")
        + ("movie" if media_type == "movie" else "series")
    )
    decision_reasons = ["unique_cross_source_identity"]
    if not animation:
        decision_reasons.append("default_live_action_without_animation_signal")
    decision = {
        "mode": "deterministic",
        "gate_status": "passed",
        "scope": scope,
        "matched_providers": sorted(providers),
        "candidate_count": 1,
        "reason_codes": decision_reasons,
        "ai_required": False,
        "ai_stage_one_status": "not_needed",
        "ai_stage_two_status": "not_needed",
    }
    query = english_title
    if scope == "movie" or scope == "whole_series":
        query = _text(f"{english_title} {year}")
    elif scope == "season":
        query = f"{english_title} S{int(intent['season_number']):02d}"
    elif scope == "episode":
        query = (
            f"{english_title} S{int(intent['season_number']):02d}"
            f"E{int(intent['episode_number']):02d}"
        )
    source_url = next(iter(selected["source_urls"]), "")
    source_provider = next(
        (
            provider
            for provider, host in (
                ("wikipedia", "wikipedia.org"),
                ("douban", "douban.com"),
                ("tvdb", "thetvdb.com"),
            )
            if host in source_url
        ),
        sorted(providers)[0],
    )
    contract = {
        "schema_version": 1,
        "metadata_id": plan_id,
        "confirmed": False,
        "identity": {
            "chinese_title": chinese_title,
            "english_title": english_title,
            "year": year or intent.get("year") or "",
            "content_kind": "movie" if media_type == "movie" else "series",
            "summary": "",
            "original_release_date": "",
            "poster_url": selected["cover_url"],
            "poster_source": "",
            "external_ids": dict(selected["external_ids"]),
        },
        "relation": {
            "type": "standalone",
            "target_series": {},
            "source": "deterministic_evidence",
        },
        "placement": {
            "library_type": media_type,
            "category_kind": category_kind,
            "season_number": None,
            "episode_number": None,
            "mapping_kind": "standalone",
            "mapping_source": "deterministic_evidence",
            "tvdb_episode_id": "",
        },
        "source_entry": {
            "title": chinese_title or english_title,
            "url": source_url,
            "provider": source_provider,
            "verification": "verified",
        },
        "items": items,
        "evidence": {"decision": decision},
        "warnings": [],
    }
    return DeterministicResult(
        {"plan_id": plan_id, "media_metadata": contract, "prowlarr_queries": [query]},
        (),
        decision,
    )

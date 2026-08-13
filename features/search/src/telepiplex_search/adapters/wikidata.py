"""Bounded Wikidata structure lookup for Wikipedia media candidates."""

from __future__ import annotations

import threading
import time

import requests


WIKIDATA_ENDPOINT = "https://www.wikidata.org/w/api.php"
USER_AGENT = (
    "telepiplex/1.9.3 (media metadata lookup; "
    "https://github.com/openai/codex)"
)

_MOVIE_TYPES = {
    "Q11424",  # film
    "Q24869",  # feature film
    "Q506240",  # television film
    "Q202866",  # animated film
    "Q20650540",  # anime film
}
_SERIES_TYPES = {
    "Q5398426",  # television series
    "Q1259759",  # miniseries
    "Q223393",  # web series
    "Q15416",  # television program
    "Q63952888",  # television anime
}
_JAPANESE_LANGUAGE = "Q5287"
_ANIMATION_TYPES = {"Q63952888"}
_CACHE_LOCK = threading.Lock()
_ENTITY_CACHE: dict[str, tuple[float, dict]] = {}


class WikidataLookupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code or "server_down")
        super().__init__(self.code)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _claim_values(entity: dict, property_id: str) -> list:
    result = []
    claims = entity.get("claims") if isinstance(entity, dict) else {}
    for claim in (claims or {}).get(property_id) or ():
        if not isinstance(claim, dict):
            continue
        snak = claim.get("mainsnak") or {}
        datavalue = snak.get("datavalue") or {}
        value = datavalue.get("value")
        if value not in (None, ""):
            result.append(value)
    return result


def _entity_ids(entity: dict, property_id: str) -> list[str]:
    result = []
    for value in _claim_values(entity, property_id):
        entity_id = _text(
            value.get("id") if isinstance(value, dict) else value
        )
        if entity_id.startswith("Q") and entity_id not in result:
            result.append(entity_id)
    return result


def _quantity(entity: dict, property_id: str) -> int | None:
    for value in _claim_values(entity, property_id):
        raw = value.get("amount") if isinstance(value, dict) else value
        try:
            parsed = int(float(str(raw)))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _year(entity: dict) -> str:
    for property_id in ("P577", "P580"):
        for value in _claim_values(entity, property_id):
            raw = _text(
                value.get("time") if isinstance(value, dict) else value
            )
            if len(raw) >= 5 and raw[0] in {"+", "-"}:
                year = raw[1:5]
                if year.isdigit():
                    return year
    return ""


def _localized_value(values: dict, languages: tuple[str, ...]) -> str:
    for language in languages:
        item = values.get(language) if isinstance(values, dict) else None
        value = _text(item.get("value") if isinstance(item, dict) else "")
        if value:
            return value
    return ""


def _aliases(entity: dict) -> list[str]:
    aliases = entity.get("aliases") or {}
    result = []
    for language in ("zh-hans", "zh-cn", "zh", "en"):
        for item in aliases.get(language) or ():
            value = _text(
                item.get("value") if isinstance(item, dict) else item
            )
            if value and value not in result:
                result.append(value)
    return result


def is_media_work(entity: dict) -> str:
    """Return the supported root media kind from structural instance IDs."""

    instance_ids = {
        _text(value)
        for value in (
            entity.get("instance_of")
            or _entity_ids(entity, "P31")
        )
        if _text(value)
    }
    is_movie = bool(instance_ids.intersection(_MOVIE_TYPES))
    is_series = bool(instance_ids.intersection(_SERIES_TYPES))
    if is_movie == is_series:
        return ""
    return "movie" if is_movie else "series"


def _normalize(entity: dict) -> dict:
    qid = _text(entity.get("id"))
    labels = entity.get("labels") or {}
    instance_of = _entity_ids(entity, "P31")
    result = {
        "wikibase_item": qid,
        "external_ids": {"wikidata": qid} if qid else {},
        "chinese_title": _localized_value(
            labels, ("zh-hans", "zh-cn", "zh")
        ),
        "english_title": _localized_value(labels, ("en",)),
        "aliases": _aliases(entity),
        "instance_of": instance_of,
        "year": _year(entity),
        "countries": _entity_ids(entity, "P495"),
        "original_language": (
            "ja"
            if _JAPANESE_LANGUAGE in _entity_ids(entity, "P364")
            else ""
        ),
        "genres": (
            ["anime"]
            if set(instance_of).intersection(_ANIMATION_TYPES)
            else []
        ),
        "adaptation_ids": _entity_ids(entity, "P4969"),
        "part_ids": _entity_ids(entity, "P527"),
        "season_count": _quantity(entity, "P2437"),
        "episode_count": _quantity(entity, "P1113"),
    }
    result["media_type"] = is_media_work(result)
    return result


def _chunks(values: list[str], size: int = 50):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def enrich_wikidata_entities(
    qids: list[str] | tuple[str, ...],
    *,
    timeout: float = 10,
    cache_ttl: float = 86400,
) -> dict[str, dict]:
    cleaned = []
    for value in qids or ():
        value = _text(value).upper()
        if value.startswith("Q") and value[1:].isdigit() and value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        return {}

    now = time.monotonic()
    result: dict[str, dict] = {}
    missing = []
    with _CACHE_LOCK:
        for qid in cleaned:
            cached = _ENTITY_CACHE.get(qid)
            if cached and now - cached[0] <= max(0.0, float(cache_ttl)):
                result[qid] = dict(cached[1])
            else:
                missing.append(qid)

    for batch in _chunks(missing):
        try:
            response = requests.get(
                WIKIDATA_ENDPOINT,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "labels|aliases|claims",
                    "languages": "zh-hans|zh-cn|zh|en",
                    "languagefallback": 1,
                    "format": "json",
                    "formatversion": 2,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            entities = (response.json() or {}).get("entities") or {}
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            code = (
                "rate_limited" if status == 429
                else "timeout" if isinstance(exc, requests.Timeout)
                else "server_down"
            )
            raise WikidataLookupError(code) from exc
        normalized_batch = {}
        for qid in batch:
            raw = entities.get(qid)
            if isinstance(raw, dict) and not raw.get("missing"):
                normalized_batch[qid] = _normalize(raw)
        with _CACHE_LOCK:
            for qid, value in normalized_batch.items():
                _ENTITY_CACHE[qid] = (now, dict(value))
        result.update(normalized_batch)
    return {qid: result[qid] for qid in cleaned if qid in result}


def search_wikidata_entities(
    query: str,
    *,
    timeout: float = 10,
    limit: int = 10,
) -> list[str]:
    """Return bounded Wikidata item IDs in provider relevance order."""

    query = _text(query)
    if not query:
        return []
    result = []
    for language in ("zh", "en"):
        try:
            response = requests.get(
                WIKIDATA_ENDPOINT,
                params={
                    "action": "wbsearchentities",
                    "search": query,
                    "language": language,
                    "uselang": "zh",
                    "type": "item",
                    "limit": max(1, min(int(limit), 20)),
                    "format": "json",
                },
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            items = (response.json() or {}).get("search") or []
        except Exception as exc:
            status = getattr(
                getattr(exc, "response", None),
                "status_code",
                None,
            )
            code = (
                "rate_limited" if status == 429
                else "timeout" if isinstance(exc, requests.Timeout)
                else "server_down"
            )
            raise WikidataLookupError(code) from exc
        for item in items:
            qid = _text(item.get("id") if isinstance(item, dict) else "")
            if qid.startswith("Q") and qid[1:].isdigit() and qid not in result:
                result.append(qid)
    return result[: max(1, min(int(limit), 20))]

"""Public AniList title evidence adapter for confirmed Japanese animation."""

from __future__ import annotations

import requests

from ..context import runtime_context


ANILIST_ENDPOINT = "https://graphql.anilist.co"


class AniListConfigError(RuntimeError):
    def __init__(self, message: str, code: str = "disabled"):
        self.code = str(code or "disabled")
        super().__init__(message)


class AniListRequestError(RuntimeError):
    def __init__(self, message: str, code: str = "server_down"):
        self.code = str(code or "server_down")
        super().__init__(message)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _get_anilist_config() -> dict:
    config = ((runtime_context.config.get("metadata") or {}).get("anilist") or {})
    if not config.get("enable", True):
        raise AniListConfigError("metadata.anilist.enable 未开启", "disabled")
    try:
        timeout = float(config.get("timeout") or 15)
    except (TypeError, ValueError):
        timeout = 15
    return {
        "endpoint": _text(config.get("endpoint") or ANILIST_ENDPOINT),
        "timeout": max(5, min(timeout, 60)),
    }


def _anilist_post(query: str, variables: dict) -> dict:
    config = _get_anilist_config()
    try:
        response = requests.post(
            config["endpoint"],
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=config["timeout"],
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        code = (
            "rate_limited" if status == 429
            else "timeout" if isinstance(exc, requests.Timeout)
            else "server_down"
        )
        raise AniListRequestError(f"AniList 请求失败: {type(exc).__name__}", code) from exc
    if not isinstance(payload, dict) or payload.get("errors"):
        raise AniListRequestError("AniList 响应无效")
    return payload


_FIELDS = """
id type format status seasonYear episodes duration countryOfOrigin siteUrl
title { native romaji english }
synonyms genres
coverImage { extraLarge large }
startDate { year month day }
"""


def _unique(values) -> list[str]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _date(item: dict) -> str:
    value = item.get("startDate") if isinstance(item.get("startDate"), dict) else {}
    try:
        year = int(value.get("year"))
    except (TypeError, ValueError):
        return str(item.get("seasonYear") or "")[:4]
    result = f"{year:04d}"
    if value.get("month"):
        result += f"-{int(value['month']):02d}"
        if value.get("day"):
            result += f"-{int(value['day']):02d}"
    return result


def _normalize_media(item: dict) -> dict | None:
    entity_id = _text(item.get("id"))
    titles = item.get("title") if isinstance(item.get("title"), dict) else {}
    native = _text(titles.get("native"))
    romaji = _text(titles.get("romaji"))
    english = _text(titles.get("english"))
    if not entity_id or not (native or romaji or english):
        return None
    media_type = "movie" if _text(item.get("format")).casefold() == "movie" else "series"
    release_date = _date(item)
    cover = item.get("coverImage") if isinstance(item.get("coverImage"), dict) else {}
    return {
        "anilist_id": entity_id,
        "external_ids": {"anilist": entity_id},
        "url": _text(item.get("siteUrl")) or f"https://anilist.co/anime/{entity_id}",
        "title": romaji or english or native,
        "name": romaji or english or native,
        "chinese_title": "",
        "english_title": english,
        "official_english_title": english,
        "original_title": native,
        "original_language": "ja",
        "romanized_original_title": romaji,
        "year": release_date[:4],
        "media_type": media_type,
        "aliases": _unique((native, romaji, english, *(item.get("synonyms") or []))),
        "genres": _unique(item.get("genres") or []),
        "countries": ["JP"] if _text(item.get("countryOfOrigin")) == "JP" else [],
        "cover_url": _text(cover.get("extraLarge") or cover.get("large")),
        "poster_language": "ja",
        "summary": "",
        "original_release_date": release_date,
        "runtime_minutes": item.get("duration"),
        "status": _text(item.get("status")),
        "episode_count": item.get("episodes"),
    }


def search_anilist(query: str, year: str = "") -> list[dict]:
    query = _text(query)
    if not query:
        return []
    document = f"query ($search: String, $page: Int) {{ Page(page: $page, perPage: 10) {{ media(search: $search, type: ANIME) {{ {_FIELDS} }} }} }}"
    payload = _anilist_post(document, {"search": query, "page": 1})
    rows = (((payload.get("data") or {}).get("Page") or {}).get("media") or [])
    facts = [
        fact
        for row in rows
        if isinstance(row, dict) and (fact := _normalize_media(row)) is not None
    ]
    year = _text(year)[:4]
    return [fact for fact in facts if not year or not fact.get("year") or fact["year"] == year]


def get_anilist_media(entity_id: str) -> dict | None:
    entity_id = _text(entity_id)
    if not entity_id:
        return None
    document = f"query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {_FIELDS} }} }}"
    payload = _anilist_post(document, {"id": int(entity_id)})
    row = (payload.get("data") or {}).get("Media")
    return _normalize_media(row) if isinstance(row, dict) else None

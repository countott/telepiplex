# -*- coding: utf-8 -*-

"""TVDB adapter owned by the search Feature."""

from __future__ import annotations

import re
import time

import requests

from ..context import runtime_context


TVDB_BASE_URL = "https://api4.thetvdb.com/v4"
TVDB_TOKEN_TTL_SECONDS = 25 * 24 * 60 * 60

_token_cache = {
    "token": "",
    "created_at": 0.0,
    "api_key": "",
    "subscriber_pin": "",
}


class TvdbConfigError(Exception):
    """Raised when TVDB metadata config is missing."""

    def __init__(self, message: str, code: str = "credential_missing"):
        self.code = str(code or "credential_missing")
        super().__init__(message)


class TvdbRequestError(Exception):
    """Raised when TVDB API calls fail."""

    def __init__(self, message: str, code: str = "server_down"):
        self.code = str(code or "server_down")
        super().__init__(message)


class TvdbAuthenticationError(TvdbRequestError):
    """Raised when configured TVDB credentials are rejected."""

    def __init__(self, message: str):
        super().__init__(message, "authentication_failed")


def _get_tvdb_config():
    metadata_config = runtime_context.config.get("metadata") or {}
    tvdb_config = metadata_config.get("tvdb") or {}
    if not tvdb_config.get("enable", False):
        raise TvdbConfigError("metadata.tvdb.enable 未开启", "disabled")

    api_key = str(tvdb_config.get("api_key") or "").strip()
    if not api_key:
        raise TvdbConfigError(
            "metadata.tvdb.api_key 未配置",
            "credential_missing",
        )

    base_url = str(tvdb_config.get("base_url") or TVDB_BASE_URL).strip().rstrip("/")
    subscriber_pin = str(tvdb_config.get("subscriber_pin") or "").strip()
    try:
        timeout = float(tvdb_config.get("timeout", 15))
    except (TypeError, ValueError):
        timeout = 15

    return {
        "base_url": base_url,
        "api_key": api_key,
        "subscriber_pin": subscriber_pin,
        "timeout": max(5, min(timeout, 60)),
    }


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    try:
        return int(getattr(response, "status_code", None))
    except (TypeError, ValueError):
        return None


def _clear_token_cache() -> None:
    _token_cache.update({
        "token": "",
        "created_at": 0.0,
        "api_key": "",
        "subscriber_pin": "",
    })


def _login_tvdb(config: dict) -> str:
    now = time.time()
    if (
        _token_cache.get("token")
        and now - float(_token_cache.get("created_at") or 0) < TVDB_TOKEN_TTL_SECONDS
        and _token_cache.get("api_key") == config["api_key"]
        and _token_cache.get("subscriber_pin") == config["subscriber_pin"]
    ):
        return _token_cache["token"]

    payload = {"apikey": config["api_key"]}
    if config["subscriber_pin"]:
        payload["pin"] = config["subscriber_pin"]

    try:
        response = requests.post(
            f"{config['base_url']}/login",
            json=payload,
            timeout=config["timeout"],
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        if _status_code(e) in {401, 403}:
            _clear_token_cache()
            raise TvdbAuthenticationError(
                "TVDB 登录认证失败"
            ) from e
        code = "timeout" if isinstance(e, requests.Timeout) else "server_down"
        raise TvdbRequestError(f"TVDB 登录失败: {e}", code) from e

    token = ""
    if isinstance(data, dict):
        token = str((data.get("data") or {}).get("token") or "").strip()
    if not token:
        _clear_token_cache()
        raise TvdbAuthenticationError("TVDB 登录响应缺少 token")

    _token_cache.update(
        {
            "token": token,
            "created_at": now,
            "api_key": config["api_key"],
            "subscriber_pin": config["subscriber_pin"],
        }
    )
    return token


def _tvdb_get(path: str, params: dict | None = None):
    config = _get_tvdb_config()
    token = _login_tvdb(config)
    for attempt in range(2):
        try:
            response = requests.get(
                f"{config['base_url']}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
                timeout=config["timeout"],
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if _status_code(e) in {401, 403}:
                _clear_token_cache()
                if attempt == 0:
                    token = _login_tvdb(config)
                    continue
                raise TvdbAuthenticationError(
                    "TVDB 请求认证失败"
                ) from e
            code = (
                "timeout"
                if isinstance(e, requests.Timeout)
                else "server_down"
            )
            raise TvdbRequestError(f"TVDB 请求失败: {e}", code) from e
    raise TvdbRequestError("TVDB 请求失败")


def _contains_latin(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", str(value or "")))


def _alias_values(value) -> list[str]:
    if not isinstance(value, list):
        return []
    aliases = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("name") or item.get("title") or ""
        else:
            text = item
        text = " ".join(str(text or "").split())
        if text and text not in aliases:
            aliases.append(text)
    return aliases


def _search_alias_values(item: dict) -> list[str]:
    aliases = _alias_values(item.get("aliases"))
    translated_values = [item.get("name_translated")]
    translations = item.get("translations")
    if isinstance(translations, dict):
        translated_values.extend(translations.values())
    translated_values.extend(_alias_values(item.get("translationsWithLang")))

    for value in translated_values:
        value = " ".join(str(value or "").split())
        if value and value not in aliases:
            aliases.append(value)
    return aliases


def _strip_alias_qualifiers(value: str) -> str:
    value = " ".join(str(value or "").split())
    while value:
        cleaned = re.sub(r"\s*\((?:(?:19|20)\d{2}|[A-Za-z]{2,3})\)\s*$", "", value).strip()
        if cleaned == value:
            return value
        value = cleaned
    return ""


def _translated_name(item: dict) -> str:
    translated = item.get("name_translated")
    if _contains_latin(translated):
        return _strip_alias_qualifiers(translated)

    translations = item.get("translations")
    if isinstance(translations, dict):
        translated = translations.get("eng") or translations.get("en") or ""
        if _contains_latin(translated):
            return _strip_alias_qualifiers(translated)
    return ""


def _preferred_english_title(item: dict) -> str:
    translated = _translated_name(item)
    if translated:
        return translated

    for alias in _alias_values(item.get("aliases")):
        if _contains_latin(alias):
            return _strip_alias_qualifiers(alias)

    for key in ("title", "name"):
        value = str(item.get(key) or "").strip()
        if _contains_latin(value):
            return _strip_alias_qualifiers(value)
    return ""


def _original_language(item: dict, original_title: str) -> str:
    value = str(
        item.get("original_language")
        or item.get("originalLanguage")
        or item.get("language")
        or ""
    ).strip().casefold()
    if value in {"ja", "jpn", "japanese", "日语", "日語"}:
        return "ja"
    if re.search(r"[\u3040-\u30ff]", original_title):
        return "ja"
    return value


def _search_cover_url(item: dict) -> str:
    for key in ("image_url", "poster"):
        value = str(item.get(key) or "").strip()
        if value:
            return value

    posters = item.get("posters")
    if isinstance(posters, list):
        for poster in posters:
            value = _artwork_url(poster) if isinstance(poster, dict) else str(poster or "").strip()
            if value:
                return value

    for key in ("thumbnail", "image"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_search_item(item: dict, media_type: str) -> dict:
    entity_type = "series" if media_type == "series" else "movie"
    entity_id = str(item.get("tvdb_id") or item.get("id") or "").strip()
    original_title = str(
        item.get("original_title")
        or item.get("originalTitle")
        or item.get("original_name")
        or item.get("originalName")
        or item.get("name")
        or ""
    ).strip()
    official_english_title = str(
        item.get("official_english_title")
        or item.get("officialEnglishTitle")
        or _preferred_english_title(item)
    ).strip()
    normalized = {
        "tvdb_id": entity_id,
        "media_type": entity_type,
        "name": str(item.get("name") or "").strip(),
        "english_title": official_english_title,
        "original_title": original_title,
        "original_language": _original_language(item, original_title),
        "official_english_title": official_english_title,
        "romanized_original_title": str(
            item.get("romanized_original_title")
            or item.get("romanizedOriginalTitle")
            or item.get("romaji_title")
            or item.get("romajiTitle")
            or ""
        ).strip(),
        "year": str(item.get("year") or item.get("first_air_time") or item.get("firstAired") or "").strip()[:4],
        "type": str(item.get("type") or entity_type).strip(),
        "overview": str(item.get("overview") or "").strip(),
        "aliases": _search_alias_values(item),
        "cover_url": _search_cover_url(item),
        "slug": str(item.get("slug") or "").strip(),
    }
    normalized[f"tvdb_{entity_type}_id"] = entity_id
    return normalized


def _translation_name(entity_type: str, entity_id: str) -> str:
    if not entity_id:
        return ""
    data = _tvdb_get(f"/{entity_type}/{entity_id}/translations/eng")
    payload = data.get("data") if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return ""
    return _preferred_english_title(payload)


def _search_tvdb(query: str, entity_type: str, year: str = "") -> list[dict]:
    query = str(query or "").strip()
    entity_type = "series" if entity_type == "series" else "movies"
    if not query:
        return []

    search_type = "series" if entity_type == "series" else "movie"
    params = {"query": query, "type": search_type}
    if str(year or "").strip():
        params["year"] = str(year).strip()

    data = _tvdb_get("/search", params=params)
    items = data.get("data") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []

    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_search_item(item, "series" if search_type == "series" else "movie")
        if not normalized.get("english_title") and normalized.get("tvdb_id"):
            try:
                normalized["english_title"] = _translation_name(entity_type, normalized["tvdb_id"])
                normalized["official_english_title"] = normalized["english_title"]
            except (TvdbConfigError, TvdbRequestError) as e:
                logger = runtime_context.logger
                if logger:
                    logger.warn(
                        f"TVDB英文翻译读取失败 type={search_type} id={normalized['tvdb_id']}: {e}"
                    )
        normalized_items.append(normalized)
    return normalized_items


def search_tvdb_series(query: str, year: str = "") -> list[dict]:
    return _search_tvdb(query, "series", year)


def search_tvdb_movies(query: str, year: str = "") -> list[dict]:
    return _search_tvdb(query, "movies", year)


def _entity_payload(path: str) -> dict | None:
    data = _tvdb_get(path)
    payload = data.get("data") if isinstance(data, dict) else None
    return payload if isinstance(payload, dict) else None


def _find_by_slug(items: list[dict], slug: str) -> dict | None:
    slug = str(slug or "").strip().casefold()
    return next(
        (
            item
            for item in items
            if str(item.get("slug") or "").strip().casefold() == slug
        ),
        None,
    )


def get_tvdb_series(series_id: str) -> dict | None:
    series_id = str(series_id or "").strip()
    if not series_id:
        return None
    if series_id.isdigit():
        payload = _entity_payload(f"/series/{series_id}/extended")
        if not payload:
            return None
        result = _normalize_search_item(payload, "series")
    else:
        result = _find_by_slug(
            search_tvdb_series(series_id.replace("-", " ")),
            series_id,
        )
        if not result:
            return None
    result["episodes"] = get_tvdb_series_episodes(
        result.get("tvdb_series_id") or result.get("tvdb_id")
    )
    return result


def get_tvdb_movie(movie_id: str) -> dict | None:
    movie_id = str(movie_id or "").strip()
    if not movie_id:
        return None
    if movie_id.isdigit():
        payload = _entity_payload(f"/movies/{movie_id}/extended")
        return _normalize_search_item(payload, "movie") if payload else None
    return _find_by_slug(
        search_tvdb_movies(movie_id.replace("-", " ")),
        movie_id,
    )


def get_tvdb_season(season_id: str) -> dict | None:
    payload = _entity_payload(f"/seasons/{str(season_id or '').strip()}/extended")
    if not payload:
        return None
    return {
        "tvdb_season_id": payload.get("id"),
        "tvdb_series_id": (
            payload.get("seriesId")
            or payload.get("series_id")
            or (payload.get("series") or {}).get("id")
        ),
        "season_number": payload.get("number") or payload.get("seasonNumber"),
    }


def get_tvdb_episode(episode_id: str) -> dict | None:
    payload = _entity_payload(f"/episodes/{str(episode_id or '').strip()}/extended")
    if not payload:
        return None
    result = _normalize_episode(payload)
    result["tvdb_series_id"] = (
        payload.get("seriesId")
        or payload.get("series_id")
        or (payload.get("series") or {}).get("id")
    )
    return result


def _normalize_episode(item: dict) -> dict:
    return {
        "tvdb_episode_id": item.get("id"),
        "name": str(item.get("name") or "").strip(),
        "season_number": (
            item.get("seasonNumber")
            if item.get("seasonNumber") not in (None, "")
            else item.get("season_number")
        ),
        "episode_number": item.get("number") or item.get("episodeNumber") or item.get("episode_number"),
        "aired": str(item.get("aired") or item.get("firstAired") or "").strip(),
    }


def get_tvdb_series_episodes(series_id: str, season_type: str = "default", page: int = 0) -> list[dict]:
    series_id = str(series_id or "").strip()
    season_type = str(season_type or "default").strip()
    if not series_id:
        return []

    data = _tvdb_get(f"/series/{series_id}/episodes/{season_type}", params={"page": int(page or 0)})
    payload = data.get("data") if isinstance(data, dict) else {}
    episodes = payload.get("episodes") if isinstance(payload, dict) else []
    if not isinstance(episodes, list):
        return []
    return [_normalize_episode(item) for item in episodes if isinstance(item, dict)]


def _artwork_url(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("image") or item.get("thumbnail") or "").strip()


def get_tvdb_series_artwork_url(series_id: str) -> str:
    series_id = str(series_id or "").strip()
    if not series_id:
        return ""

    data = _tvdb_get(f"/series/{series_id}/artworks")
    payload = data.get("data") if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return ""

    primary = _artwork_url(payload)
    if primary:
        return primary

    artworks = payload.get("artworks")
    if not isinstance(artworks, list):
        return ""

    usable = [item for item in artworks if _artwork_url(item)]
    if not usable:
        return ""

    usable.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return _artwork_url(usable[0])


def get_tvdb_movie_artwork_url(movie_id: str) -> str:
    movie_id = str(movie_id or "").strip()
    if not movie_id:
        return ""

    data = _tvdb_get(f"/movies/{movie_id}/extended", params={"short": True})
    payload = data.get("data") if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return ""

    primary = _artwork_url(payload)
    if primary:
        return primary

    artworks = payload.get("artworks")
    if not isinstance(artworks, list):
        return ""
    usable = [item for item in artworks if _artwork_url(item)]
    if not usable:
        return ""
    usable.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return _artwork_url(usable[0])

"""TMDB evidence adapter owned by the search Feature."""

from __future__ import annotations

from urllib.parse import quote

import requests

from ..context import runtime_context


TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"


class TmdbConfigError(RuntimeError):
    def __init__(self, message: str, code: str = "credential_missing"):
        self.code = str(code or "credential_missing")
        super().__init__(message)


class TmdbRequestError(RuntimeError):
    def __init__(self, message: str, code: str = "server_down"):
        self.code = str(code or "server_down")
        super().__init__(message)


class TmdbAuthenticationError(TmdbRequestError):
    def __init__(self, message: str):
        super().__init__(message, "authentication_failed")


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_tmdb_config() -> dict:
    config = ((runtime_context.config.get("metadata") or {}).get("tmdb") or {})
    if not config.get("enable", False):
        raise TmdbConfigError("metadata.tmdb.enable 未开启", "disabled")
    api_key = _text(config.get("api_key"))
    if not api_key:
        raise TmdbConfigError("metadata.tmdb.api_key 未配置", "credential_missing")
    try:
        timeout = float(config.get("timeout") or 15)
    except (TypeError, ValueError):
        timeout = 15
    return {
        "base_url": _text(config.get("base_url") or TMDB_BASE_URL).rstrip("/"),
        "api_key": api_key,
        "timeout": max(5, min(timeout, 60)),
    }


def _status_code(exc: Exception) -> int | None:
    try:
        return int(getattr(getattr(exc, "response", None), "status_code", None))
    except (TypeError, ValueError):
        return None


def _tmdb_get(path: str, *, params: dict | None = None) -> dict:
    config = _get_tmdb_config()
    try:
        response = requests.get(
            f"{config['base_url']}{path}",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            params=params or {},
            timeout=config["timeout"],
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        status = _status_code(exc)
        if status in {401, 403}:
            raise TmdbAuthenticationError("TMDB 认证失败") from exc
        code = (
            "rate_limited" if status == 429
            else "not_found" if status == 404
            else "timeout" if isinstance(exc, requests.Timeout)
            else "server_down"
        )
        raise TmdbRequestError(f"TMDB 请求失败: {type(exc).__name__}", code) from exc
    if not isinstance(payload, dict):
        raise TmdbRequestError("TMDB 响应无效")
    return payload


def _unique_text(values) -> list[str]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _translation_rows(payload: dict) -> list[dict]:
    translations = payload.get("translations") or {}
    rows = translations.get("translations") if isinstance(translations, dict) else []
    return [row for row in rows or [] if isinstance(row, dict)]


def _translation_value(payload: dict, language: str, field: str) -> str:
    candidates = []
    for row in _translation_rows(payload):
        if _text(row.get("iso_639_1")).casefold() != language:
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        value = _text(data.get(field) or data.get("name" if field == "title" else field))
        if value:
            candidates.append((
                0 if _text(row.get("iso_3166_1")).casefold() == "cn" else 1,
                value,
            ))
    return min(candidates, default=(9, ""))[1]


def _alternative_titles(payload: dict) -> list[str]:
    container = payload.get("alternative_titles") or {}
    rows = []
    if isinstance(container, dict):
        rows = container.get("titles") or container.get("results") or []
    return _unique_text(
        (row.get("title") or row.get("name"))
        for row in rows
        if isinstance(row, dict)
    )


def _external_ids(payload: dict, tmdb_id: str) -> dict[str, str]:
    raw = payload.get("external_ids") or {}
    result = {"tmdb": tmdb_id}
    for source, key in (
        ("imdb", "imdb_id"),
        ("wikidata", "wikidata_id"),
        ("tvdb", "tvdb_id"),
    ):
        value = _text(raw.get(key) if isinstance(raw, dict) else "")
        if value:
            result[source] = value
    return result


def _credits(payload: dict, key: str) -> list[dict]:
    container = payload.get("aggregate_credits") or payload.get("credits") or {}
    rows = container.get(key) if isinstance(container, dict) else []
    result = []
    for row in rows or []:
        if not isinstance(row, dict) or not _text(row.get("name")):
            continue
        item = {
            "id": _text(row.get("id")),
            "name": _text(row.get("name")),
        }
        role = _text(
            row.get("character")
            or row.get("job")
            or next(
                (
                    role.get("character") or role.get("job")
                    for role in row.get("roles") or []
                    if isinstance(role, dict)
                ),
                "",
            )
        )
        if role:
            item["role"] = role
        result.append(item)
    return result[:30]


def _certifications(payload: dict, media_type: str) -> list[str]:
    key = "release_dates" if media_type == "movie" else "content_ratings"
    container = payload.get(key) or {}
    result = []
    for country in container.get("results") or [] if isinstance(container, dict) else []:
        if not isinstance(country, dict):
            continue
        code = _text(country.get("iso_3166_1"))
        rows = country.get("release_dates") or [country]
        for row in rows:
            certification = _text(row.get("certification") if isinstance(row, dict) else "")
            value = f"{code}:{certification}" if code and certification else certification
            if value and value not in result:
                result.append(value)
    return result


def _image_url(path) -> str:
    path = _text(path)
    return f"{TMDB_IMAGE_BASE}{path}" if path.startswith("/") else ""


def _normalize_entity(payload: dict, media_type: str) -> dict | None:
    media_type = "series" if media_type in {"series", "tv"} else "movie"
    tmdb_id = _text(payload.get("id"))
    if not tmdb_id:
        return None
    title_key = "name" if media_type == "series" else "title"
    original_key = "original_name" if media_type == "series" else "original_title"
    date_key = "first_air_date" if media_type == "series" else "release_date"
    title = _text(payload.get(title_key))
    original_title = _text(payload.get(original_key) or title)
    original_language = _text(payload.get("original_language")).casefold()
    english_title = title if original_language == "en" else _translation_value(payload, "en", "title")
    chinese_title = _translation_value(payload, "zh", "title")
    aliases = _unique_text((
        title,
        original_title,
        english_title,
        chinese_title,
        *_alternative_titles(payload),
    ))
    release_date = _text(payload.get(date_key))
    countries = _unique_text(
        row.get("name") or row.get("iso_3166_1")
        for row in payload.get("production_countries") or []
        if isinstance(row, dict)
    )
    networks = _unique_text(
        row.get("name")
        for row in payload.get("networks") or []
        if isinstance(row, dict)
    )
    result = {
        "tmdb_id": tmdb_id,
        "external_ids": _external_ids(payload, tmdb_id),
        "url": f"https://www.themoviedb.org/{'tv' if media_type == 'series' else 'movie'}/{quote(tmdb_id)}",
        "title": english_title or title or original_title,
        "name": title,
        "chinese_title": chinese_title,
        "english_title": english_title,
        "official_english_title": english_title,
        "original_title": original_title,
        "original_language": original_language,
        "romanized_original_title": "",
        "year": release_date[:4],
        "media_type": media_type,
        "aliases": aliases,
        "genres": _unique_text(
            row.get("name")
            for row in payload.get("genres") or []
            if isinstance(row, dict)
        ),
        "countries": countries,
        "cover_url": _image_url(payload.get("poster_path")),
        "poster_language": "",
        "backdrop_urls": _unique_text((
            _image_url(payload.get("backdrop_path")),
            *(
                _image_url(row.get("file_path"))
                for row in ((payload.get("images") or {}).get("backdrops") or [])
                if isinstance(row, dict)
            ),
        )),
        "summary": _translation_value(payload, "zh", "overview") or _text(payload.get("overview")),
        "original_release_date": release_date,
        "runtime_minutes": payload.get("runtime") or next(
            (value for value in payload.get("episode_run_time") or [] if value),
            None,
        ),
        "status": _text(payload.get("status")),
        "studios": _unique_text(
            row.get("name")
            for row in payload.get("production_companies") or []
            if isinstance(row, dict)
        ),
        "networks": networks,
        "cast": _credits(payload, "cast"),
        "crew": _credits(payload, "crew"),
        "certifications": _certifications(payload, media_type),
        "season_count": payload.get("number_of_seasons"),
        "episode_count": payload.get("number_of_episodes"),
    }
    return result


def _normalize_search_item(payload: dict, media_type: str) -> dict | None:
    fact = _normalize_entity(payload, media_type)
    if fact is None:
        return None
    fact["cover_url"] = _image_url(payload.get("poster_path"))
    return fact


def search_tmdb(query: str, media_type: str, year: str = "") -> list[dict]:
    query = _text(query)
    media_type = "series" if media_type in {"series", "tv"} else "movie"
    if not query:
        return []
    path_type = "tv" if media_type == "series" else "movie"
    params = {"query": query, "include_adult": "false", "language": "en-US"}
    year = _text(year)[:4]
    if year:
        params["first_air_date_year" if media_type == "series" else "year"] = year
    payload = _tmdb_get(f"/search/{path_type}", params=params)
    return [
        fact
        for row in payload.get("results") or []
        if isinstance(row, dict)
        and (fact := _normalize_search_item(row, media_type)) is not None
    ]


def find_tmdb_by_external_id(
    source: str,
    external_id: str,
    media_type: str,
) -> list[dict]:
    source = _text(source).casefold()
    external_id = _text(external_id)
    external_source = {
        "imdb": "imdb_id",
        "tvdb": "tvdb_id",
        "wikidata": "wikidata_id",
    }.get(source)
    if not external_source or not external_id:
        return []
    media_type = "series" if media_type in {"series", "tv"} else "movie"
    result_key = "tv_results" if media_type == "series" else "movie_results"
    payload = _tmdb_get(
        f"/find/{quote(external_id)}",
        params={
            "external_source": external_source,
            "language": "en-US",
        },
    )
    return [
        fact
        for row in payload.get(result_key) or []
        if isinstance(row, dict)
        and (fact := _normalize_search_item(row, media_type)) is not None
    ]


def get_tmdb_entity(media_type: str, entity_id: str) -> dict | None:
    media_type = "series" if media_type in {"series", "tv"} else "movie"
    entity_id = _text(entity_id)
    if not entity_id:
        return None
    path_type = "tv" if media_type == "series" else "movie"
    append = (
        "external_ids,translations,alternative_titles,aggregate_credits,"
        "content_ratings,images"
        if media_type == "series"
        else "external_ids,translations,alternative_titles,credits,release_dates,images"
    )
    payload = _tmdb_get(
        f"/{path_type}/{entity_id}",
        params={
            "language": "en-US",
            "append_to_response": append,
            "include_image_language": "zh,en,null",
        },
    )
    fact = _normalize_entity(payload, media_type)
    if fact is not None and media_type == "series":
        fact["episodes"] = get_tmdb_series_inventory(entity_id)
    return fact


def get_tmdb_series_inventory(entity_id: str) -> list[dict]:
    """Return regular numbered episodes only; TMDB season zero is excluded."""

    entity_id = _text(entity_id)
    if not entity_id:
        return []
    root = _tmdb_get(
        f"/tv/{quote(entity_id)}",
        params={"language": "en-US"},
    )
    season_numbers = sorted({
        number
        for raw in root.get("seasons") or ()
        if isinstance(raw, dict)
        and (number := _integer(raw.get("season_number"))) is not None
        and number > 0
    })
    items = []
    for season_number in season_numbers:
        season = _tmdb_get(
            f"/tv/{quote(entity_id)}/season/{season_number}",
            params={"language": "en-US"},
        )
        for raw in season.get("episodes") or ():
            if not isinstance(raw, dict):
                continue
            episode_number = _integer(raw.get("episode_number"))
            if episode_number is None or episode_number < 1:
                continue
            items.append({
                "item_id": _text(raw.get("id"))
                or f"tmdb:{entity_id}:S{season_number:02d}E{episode_number:03d}",
                "content_role": "main_episode",
                "season_number": season_number,
                "episode_number": episode_number,
                "aired": _text(raw.get("air_date")),
                "tmdb_episode_id": _text(raw.get("id")),
            })
    return items

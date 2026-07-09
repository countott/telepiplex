# -*- coding: utf-8 -*-

from __future__ import annotations

import time

import requests

import init


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


class TvdbRequestError(Exception):
    """Raised when TVDB API calls fail."""


def _get_tvdb_config():
    metadata_config = init.bot_config.get("metadata") or {}
    tvdb_config = metadata_config.get("tvdb") or {}
    if not tvdb_config.get("enable", False):
        raise TvdbConfigError("metadata.tvdb.enable 未开启")

    api_key = str(tvdb_config.get("api_key") or "").strip()
    if not api_key:
        raise TvdbConfigError("metadata.tvdb.api_key 未配置")

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
        raise TvdbRequestError(f"TVDB 登录失败: {e}") from e

    token = ""
    if isinstance(data, dict):
        token = str((data.get("data") or {}).get("token") or "").strip()
    if not token:
        raise TvdbRequestError("TVDB 登录响应缺少 token")

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
        raise TvdbRequestError(f"TVDB 请求失败: {e}") from e


def _normalize_series(item: dict) -> dict:
    return {
        "tvdb_series_id": str(item.get("tvdb_id") or item.get("id") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "year": str(item.get("year") or item.get("first_air_time") or item.get("firstAired") or "").strip()[:4],
        "type": str(item.get("type") or "").strip(),
        "overview": str(item.get("overview") or "").strip(),
        "aliases": item.get("aliases") if isinstance(item.get("aliases"), list) else [],
        "cover_url": str(item.get("image") or "").strip(),
    }


def search_tvdb_series(query: str, year: str = "") -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []

    params = {"query": query, "type": "series"}
    if str(year or "").strip():
        params["year"] = str(year).strip()

    data = _tvdb_get("/search", params=params)
    items = data.get("data") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    return [_normalize_series(item) for item in items if isinstance(item, dict)]


def _normalize_episode(item: dict) -> dict:
    return {
        "tvdb_episode_id": item.get("id"),
        "name": str(item.get("name") or "").strip(),
        "season_number": item.get("seasonNumber") or item.get("season_number"),
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

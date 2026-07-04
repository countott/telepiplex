# -*- coding: utf-8 -*-

import requests

import init


class ProwlarrConfigError(Exception):
    """Raised when search or Prowlarr config is missing."""


class ProwlarrRequestError(Exception):
    """Raised when Prowlarr cannot be reached or returns invalid data."""


def _get_prowlarr_config():
    search_config = init.bot_config.get("search") or {}
    if not search_config.get("enable", False):
        raise ProwlarrConfigError("搜索功能未开启")

    prowlarr_config = search_config.get("prowlarr") or {}
    base_url = str(prowlarr_config.get("base_url") or "").strip()
    api_key = str(prowlarr_config.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise ProwlarrConfigError("search.prowlarr.base_url 或 api_key 未配置")

    return prowlarr_config, base_url.rstrip("/"), api_key


def _normalize_result(item):
    magnet_url = item.get("magnetUrl") or item.get("magnet_url") or ""
    download_url = magnet_url or item.get("downloadUrl") or item.get("download_url") or ""
    return {
        "title": item.get("title") or "",
        "download_url": download_url,
        "magnet_url": magnet_url,
        "size": item.get("size") or 0,
        "seeders": item.get("seeders") or 0,
        "indexer": item.get("indexer") or item.get("indexerName") or "",
        "publish_date": item.get("publishDate") or item.get("publish_date") or "",
        "protocol": item.get("protocol") or "",
        "info_url": item.get("guidUrl") or item.get("infoUrl") or item.get("info_url") or "",
    }


def search_prowlarr(query: str, media_type: str = "movie") -> list[dict]:
    prowlarr_config, base_url, api_key = _get_prowlarr_config()
    categories = prowlarr_config.get("categories") or {}

    params = {
        "query": query,
        "indexerIds": prowlarr_config.get("indexer_ids", "-2"),
        "categories": categories.get(media_type, 2000 if media_type == "movie" else 5000),
        "type": "search",
    }
    url = f"{base_url}/api/v1/search"
    headers = {"X-Api-Key": api_key}
    timeout = prowlarr_config.get("timeout", 20)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise ProwlarrRequestError(f"Prowlarr 请求失败: {e}") from e

    if not isinstance(data, list):
        raise ProwlarrRequestError("Prowlarr 返回数据格式异常")

    return [_normalize_result(item) for item in data if isinstance(item, dict)]

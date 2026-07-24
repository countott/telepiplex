# -*- coding: utf-8 -*-

"""Prowlarr adapter owned by the search Feature."""

from __future__ import annotations

from collections import Counter
import hashlib
from html import unescape
import re
from urllib.parse import quote

import requests

from ..context import runtime_context


DEFAULT_PROWLARR_SEARCH_TIMEOUT = 200
PROWLARR_STATUS_TIMEOUT = 15


class ProwlarrConfigError(Exception):
    """Raised when search or Prowlarr config is missing."""


class ProwlarrRequestError(Exception):
    """Raised when Prowlarr cannot be reached or returns invalid data."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "request_failed",
        http_status: int = 0,
        retryable: bool = False,
    ):
        self.kind = str(kind or "request_failed")
        self.http_status = int(http_status or 0)
        self.retryable = bool(retryable)
        super().__init__(str(message or "Prowlarr 请求失败"))

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "http_status": self.http_status,
            "message": str(self),
            "retryable": self.retryable,
        }


MAGNET_PATTERN = re.compile(r"^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.*)?$")
MAGNET_IN_TEXT_PATTERN = re.compile(
    r"magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def _get_prowlarr_config():
    search_config = runtime_context.config.get("search") or {}
    prowlarr_config = search_config.get("prowlarr") or {}
    base_url = str(prowlarr_config.get("base_url") or "").strip()
    api_key = str(prowlarr_config.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise ProwlarrConfigError("search.prowlarr.base_url 或 api_key 未配置")

    return prowlarr_config, base_url.rstrip("/"), api_key


def _search_timeout(prowlarr_config: dict):
    try:
        timeout = float(prowlarr_config.get("timeout", DEFAULT_PROWLARR_SEARCH_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_PROWLARR_SEARCH_TIMEOUT
    return max(timeout, 1)


def _status_timeout(prowlarr_config: dict):
    try:
        timeout = float(prowlarr_config.get("status_timeout", PROWLARR_STATUS_TIMEOUT))
    except (TypeError, ValueError):
        timeout = PROWLARR_STATUS_TIMEOUT
    return max(5, min(timeout, 30))


def _warn(message: str):
    logger = runtime_context.logger
    warning = (
        getattr(logger, "warning", None)
        or getattr(logger, "warn", None)
        if logger is not None
        else None
    )
    if warning is not None:
        warning(message)


def _is_magnet_url(url: str) -> bool:
    return isinstance(url, str) and MAGNET_PATTERN.match(url.strip()) is not None


def _normalize_info_hash(info_hash: str) -> str:
    info_hash = str(info_hash or "").strip()
    if re.fullmatch(r"[a-fA-F0-9]{40}", info_hash) or re.fullmatch(r"[a-zA-Z2-7]{32}", info_hash):
        return info_hash.upper()
    return ""


def _build_magnet_url(info_hash: str, display_name: str = "") -> str:
    magnet = f"magnet:?xt=urn:btih:{_normalize_info_hash(info_hash)}"
    if display_name:
        magnet += f"&dn={quote(str(display_name), safe='')}"
    return magnet


def _magnet_from_item_fields(item: dict) -> str:
    magnet_url = item.get("magnetUrl") or item.get("magnet_url") or item.get("magnet") or ""
    if _is_magnet_url(magnet_url):
        return magnet_url

    info_hash = _normalize_info_hash(item.get("infoHash") or item.get("info_hash") or "")
    if info_hash:
        return _build_magnet_url(info_hash, item.get("title") or "")

    return ""


def _normalize_result(item):
    magnet_url = _magnet_from_item_fields(item)
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
    timeout = _search_timeout(prowlarr_config)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.exceptions.Timeout as e:
        raise ProwlarrRequestError(
            f"Prowlarr 查询超时（已等待 {int(timeout)} 秒）。"
            "部分索引器可能响应过慢，请检查 Prowlarr 索引器日志。",
            kind="timeout",
            retryable=True,
        ) from e
    except requests.HTTPError as e:
        status = int(getattr(getattr(e, "response", None), "status_code", 0) or 0)
        kind = (
            "authentication_failed"
            if status in {401, 403}
            else "rate_limited"
            if status == 429
            else "server_error"
            if status >= 500
            else "http_error"
        )
        raise ProwlarrRequestError(
            f"Prowlarr HTTP 请求失败（{status or '未知状态'}）：{e}",
            kind=kind,
            http_status=status,
            retryable=status == 429 or status >= 500,
        ) from e
    except requests.ConnectionError as e:
        raise ProwlarrRequestError(
            f"Prowlarr 连接失败：{e}",
            kind="connection_failed",
            retryable=True,
        ) from e
    except requests.RequestException as e:
        raise ProwlarrRequestError(
            f"Prowlarr 请求失败：{e}",
            kind="request_failed",
        ) from e

    try:
        data = response.json()
    except (TypeError, ValueError) as e:
        raise ProwlarrRequestError(
            f"Prowlarr 返回的 JSON 无法解析：{e}",
            kind="invalid_response",
        ) from e

    if not isinstance(data, list):
        raise ProwlarrRequestError(
            "Prowlarr 返回数据格式异常",
            kind="invalid_response",
        )

    return [_normalize_result(item) for item in data if isinstance(item, dict)]


def _prowlarr_get_json(path: str, timeout: float | None = None):
    prowlarr_config, base_url, api_key = _get_prowlarr_config()
    response = requests.get(
        f"{base_url}{path}",
        headers={"X-Api-Key": api_key},
        timeout=timeout if timeout is not None else _status_timeout(prowlarr_config),
    )
    response.raise_for_status()
    return response.json()


def _normalize_health_entry(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None

    source = str(item.get("source") or item.get("name") or "").strip()
    message = str(item.get("message") or item.get("type") or "").strip()
    if not source and not message:
        return None

    return {
        "source": source or "Prowlarr",
        "message": message,
    }


def get_prowlarr_indexer_summary(results: list[dict] | None = None) -> dict:
    result_sources = Counter()
    for item in results or []:
        indexer = str((item or {}).get("indexer") or "未知").strip() or "未知"
        result_sources[indexer] += 1

    summary = {
        "enabled_indexers": [],
        "result_sources": dict(result_sources),
        "down_indexers": [],
        "error": "",
    }

    try:
        prowlarr_config, _, _ = _get_prowlarr_config()
        timeout = _status_timeout(prowlarr_config)
        indexers = _prowlarr_get_json("/api/v1/indexer", timeout=timeout)
        if isinstance(indexers, list):
            summary["enabled_indexers"] = [
                str(item.get("name") or "").strip()
                for item in indexers
                if isinstance(item, dict)
                and item.get("enable", True)
                and str(item.get("name") or "").strip()
            ]

        health = _prowlarr_get_json("/api/v1/health", timeout=timeout)
        if isinstance(health, list):
            summary["down_indexers"] = [
                entry
                for entry in (_normalize_health_entry(item) for item in health)
                if entry
            ]
    except Exception as e:
        summary["error"] = str(e)

    return summary


def _read_bencoded_bytes(data: bytes, pos: int) -> tuple[bytes, int]:
    colon = data.find(b":", pos)
    if colon == -1:
        raise ValueError("invalid bencode string")

    length_text = data[pos:colon]
    if not length_text.isdigit():
        raise ValueError("invalid bencode string length")

    length = int(length_text)
    start = colon + 1
    end = start + length
    if end > len(data):
        raise ValueError("truncated bencode string")

    return data[start:end], end


def _skip_bencoded_value(data: bytes, pos: int) -> int:
    if pos >= len(data):
        raise ValueError("truncated bencode value")

    token = data[pos:pos + 1]
    if token == b"i":
        end = data.find(b"e", pos)
        if end == -1:
            raise ValueError("unterminated bencode integer")
        return end + 1

    if token == b"l":
        pos += 1
        while pos < len(data) and data[pos:pos + 1] != b"e":
            pos = _skip_bencoded_value(data, pos)
        if pos >= len(data):
            raise ValueError("unterminated bencode list")
        return pos + 1

    if token == b"d":
        pos += 1
        while pos < len(data) and data[pos:pos + 1] != b"e":
            _, pos = _read_bencoded_bytes(data, pos)
            pos = _skip_bencoded_value(data, pos)
        if pos >= len(data):
            raise ValueError("unterminated bencode dict")
        return pos + 1

    if token.isdigit():
        _, pos = _read_bencoded_bytes(data, pos)
        return pos

    raise ValueError("unknown bencode value")


def _decode_bencoded_value(data: bytes, pos: int = 0):
    if pos >= len(data):
        raise ValueError("truncated bencode value")

    token = data[pos:pos + 1]
    if token == b"i":
        end = data.find(b"e", pos)
        if end == -1:
            raise ValueError("unterminated bencode integer")
        return int(data[pos + 1:end]), end + 1

    if token == b"l":
        values = []
        pos += 1
        while pos < len(data) and data[pos:pos + 1] != b"e":
            value, pos = _decode_bencoded_value(data, pos)
            values.append(value)
        if pos >= len(data):
            raise ValueError("unterminated bencode list")
        return values, pos + 1

    if token == b"d":
        values = {}
        pos += 1
        while pos < len(data) and data[pos:pos + 1] != b"e":
            key, pos = _read_bencoded_bytes(data, pos)
            value, pos = _decode_bencoded_value(data, pos)
            values[key] = value
        if pos >= len(data):
            raise ValueError("unterminated bencode dict")
        return values, pos + 1

    if token.isdigit():
        return _read_bencoded_bytes(data, pos)

    raise ValueError("unknown bencode value")


def _find_torrent_info_slice(torrent_data: bytes) -> bytes:
    if not torrent_data or torrent_data[:1] != b"d":
        raise ValueError("torrent metadata must be a bencoded dict")

    pos = 1
    while pos < len(torrent_data) and torrent_data[pos:pos + 1] != b"e":
        key, pos = _read_bencoded_bytes(torrent_data, pos)
        value_start = pos
        value_end = _skip_bencoded_value(torrent_data, pos)
        if key == b"info":
            return torrent_data[value_start:value_end]
        pos = value_end

    raise ValueError("torrent metadata missing info dict")


def magnet_from_torrent_bytes(torrent_data: bytes, display_name: str = "") -> str:
    info_data = _find_torrent_info_slice(torrent_data)
    info, _ = _decode_bencoded_value(info_data)
    torrent_name = ""
    if isinstance(info, dict):
        raw_name = info.get(b"name.utf-8") or info.get(b"name")
        if isinstance(raw_name, bytes):
            torrent_name = raw_name.decode("utf-8", errors="replace")

    info_hash = hashlib.sha1(info_data).hexdigest().upper()
    return _build_magnet_url(info_hash, torrent_name or display_name)


def _extract_magnet_from_text(text: str) -> str:
    for match in MAGNET_IN_TEXT_PATTERN.finditer(unescape(text or "")):
        magnet = match.group(0).rstrip(").,;")
        if _is_magnet_url(magnet):
            return magnet
    return ""


def _response_text(response) -> str:
    text = getattr(response, "text", "")
    if text:
        return text

    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="ignore")
    return str(content or "")


def _fetch_magnet_from_info_page(item: dict, timeout) -> str:
    info_url = item.get("info_url") or item.get("guidUrl") or item.get("guid_url") or item.get("infoUrl") or ""
    if not info_url:
        return ""

    response = requests.get(info_url, timeout=timeout)
    response.raise_for_status()
    return _extract_magnet_from_text(_response_text(response))


def _fetch_magnet_from_torrent_download(link: str, item: dict, timeout) -> str:
    response = requests.get(link, timeout=timeout, allow_redirects=False)
    try:
        status_code = int(getattr(response, "status_code", 200) or 200)
    except (TypeError, ValueError):
        status_code = 200

    location = (getattr(response, "headers", {}) or {}).get("Location", "")
    if 300 <= status_code < 400 and _is_magnet_url(location):
        return location

    response.raise_for_status()
    return magnet_from_torrent_bytes(response.content, item.get("title") or "")


def resolve_prowlarr_download_url(item: dict, timeout=None) -> str:
    magnet_url = _magnet_from_item_fields(item)
    if magnet_url:
        return magnet_url

    link = item.get("download_url") or item.get("downloadUrl") or ""
    if _is_magnet_url(link):
        return link

    if not link:
        return ""

    protocol = str(item.get("protocol") or "").lower()
    if protocol != "torrent":
        return link

    if timeout is None:
        timeout = _search_timeout(((runtime_context.config.get("search") or {}).get("prowlarr") or {}))

    torrent_error = None
    try:
        return _fetch_magnet_from_torrent_download(link, item, timeout)
    except Exception as e:
        torrent_error = e
        _warn(f"Prowlarr torrent 转磁力失败，将尝试详情页磁力兜底: {e}")

    try:
        magnet_url = _fetch_magnet_from_info_page(item, timeout)
        if magnet_url:
            return magnet_url
    except Exception as e:
        raise ProwlarrRequestError(
            f"Prowlarr torrent 转磁力失败，详情页磁力提取也失败: torrent={torrent_error}; info_page={e}"
        ) from e

    raise ProwlarrRequestError(f"Prowlarr torrent 转磁力失败: {torrent_error}")

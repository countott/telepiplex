"""Decide which registered sources can usefully search a confirmed work.

This policy never ranks or accepts subtitle content. ``enabled`` permits a
source to participate; the profile and query decide whether it is applicable.
Direct source links are handled separately by the engine, respecting enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SourceDecision:
    search: bool
    status: str = ""
    message: str = ""


_KEY_SOURCES = frozenset({"opensubtitles", "subdl", "subsource"})
_CATALOG_SOURCES = frozenset({"nekomoe", "mingy", "kitauji", "haruhana"})
_DETAIL_SOURCES = frozenset({"zimuku", "subf2m"})
_LIVE_ACTION_CATEGORIES = frozenset({"live_action_movie", "live_action_series"})


def _configured(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def source_search_decision(provider, query, *, profile: str = "balanced") -> SourceDecision:
    """Return a local decision without provider, network, or filesystem calls.

    Missing credentials, hashes, and archive roots cannot yield a search even
    in the all profile. Unknown adapters are allowed so custom providers retain
    their existing contract. Unknown media categories do not exclude catalogs.
    """
    name = str(getattr(provider, "name", ""))
    settings = getattr(provider, "config", {})
    settings = settings if isinstance(settings, Mapping) else {}
    if settings.get("enabled", True) is False:
        return SourceDecision(False, "disabled", "此字幕来源已停用")
    if profile not in {"balanced", "all"}:
        raise ValueError("invalid_subtitle_source_profile")
    if name in _KEY_SOURCES and not _configured(settings.get("api_key")):
        return SourceDecision(False, "skipped", "未配置此来源的 API Key，已跳过自动检索")
    if name == "shooter" and not _configured(getattr(query, "shooter_hash", "")):
        return SourceDecision(False, "skipped", "未提供视频哈希，已跳过射手哈希检索")
    if name == "local_archive" and not settings.get("roots"):
        return SourceDecision(False, "skipped", "未配置本地字幕归档目录，已跳过归档检索")
    if profile == "all":
        return SourceDecision(True)
    if name in _DETAIL_SOURCES:
        return SourceDecision(False, "skipped", "精简方案保留此来源的指定详情链接检索")
    if name == "lwltv":
        # A cookie cannot substitute for the current per-download Turnstile
        # challenge. The all profile keeps this adapter available for attempts.
        return SourceDecision(False, "skipped", "此来源下载需要浏览器验证，精简方案已跳过自动检索")
    if name == "yysub" and not _configured(settings.get("cookie")):
        return SourceDecision(False, "skipped", "未配置网站登录 Cookie，精简方案已跳过此来源")
    if name == "r3sub" and getattr(query, "media_type", "") != "movie":
        return SourceDecision(False, "skipped", "此来源用于电影字幕，已跳过剧集检索")
    if name == "addic7ed" and getattr(query, "media_type", "") not in {"series", "tv"}:
        return SourceDecision(False, "skipped", "此来源用于剧集字幕，已跳过电影检索")
    if name in _CATALOG_SOURCES and getattr(query, "category_kind", "") in _LIVE_ACTION_CATEGORIES:
        return SourceDecision(False, "skipped", "已确认为真人作品，已跳过动画字幕组目录")
    return SourceDecision(True)

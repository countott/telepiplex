# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import html
import re
import time
import uuid
from copy import deepcopy
from warnings import filterwarnings
from urllib.parse import unquote, unquote_plus, urlparse

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.warnings import PTBUserWarning

import init
from app.adapters.wikipedia import lookup_wikipedia_evidence
from app.core.media_metadata import (
    attach_media_metadata,
    extract_confirmed_media_metadata,
    resolve_category_route,
    series_folder_name,
    series_season_directory_name,
)
from app.core.module_registry import DownloadProviderUnavailable, DownloadRequest
from app.services.search_planner import (
    SearchPlanningError,
    build_confirmable_search_plan,
)
from app.utils.directory_config import get_save_directories
from app.adapters.prowlarr import (
    ProwlarrConfigError,
    ProwlarrRequestError,
    get_prowlarr_indexer_summary,
    resolve_prowlarr_download_url,
    search_prowlarr,
)
from app.adapters.tvdb import (
    TvdbConfigError,
    TvdbRequestError,
    get_tvdb_movie_artwork_url,
    get_tvdb_series_artwork_url,
    get_tvdb_series_episodes,
    search_tvdb_movies,
    search_tvdb_series,
)
from app.utils.ai import infer_metadata_backfill_with_ai, infer_verified_search_match_with_ai, normalize_search_query_with_ai
from app.utils.media_metadata import build_external_metadata, build_search_metadata
from app.utils.release_score import rank_releases
from app.utils.search_resolution import (
    build_confirmation_candidates,
    candidate_to_prowlarr_query,
    is_unreleased_episode,
    merge_primary_entries,
    parse_search_intent,
)
from app.utils.search_query import (
    extract_douban_subject_id,
    is_supported_metadata_url,
    parse_douban_mobile_title,
    parse_douban_page_title,
    parse_douban_rexxar_title,
    parse_douban_subject_abstract_title,
    parse_media_page_title,
)
from app.utils.search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
)

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SEARCH_SELECT_RESULT, SEARCH_CONFIRM_MEDIA_METADATA = range(30, 32)
SEARCH_TASK_TTL_SECONDS = 30 * 60
SEARCH_PROGRESS_INTERVAL_SECONDS = 1
TELEGRAM_SEND_TIMEOUT_SECONDS = 30
METADATA_URL_PATTERN = r"(?i)^https?://(?:[^/\s]+\.)*(?:douban\.com|imdb\.com|thetvdb\.com|tvdb\.com|themoviedb\.org|tmdb\.org)(?::\d+)?/\S+$"
HTTP_URL_PATTERN = r"(?i)^https?://[^\s]+$"
UNICODE_FORMAT_CODEPOINTS = {0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E}

pending_search_tasks = {}
pending_entry_confirmations = {}
temporary_special_allocator = TemporarySpecialAllocator()


def _log_info(message: str):
    logger = getattr(init, "logger", None)
    if logger:
        logger.info(message)


def _log_warn(message: str):
    logger = getattr(init, "logger", None)
    if logger:
        logger.warn(message)


def _log_error(message: str):
    logger = getattr(init, "logger", None)
    if logger:
        logger.error(message)


def _title_contains_latin(title: str) -> bool:
    return bool(re.search(r"[A-Za-z]", str(title or "")))


def _collapse_title_spaces(title: str) -> str:
    title = "".join(char for char in str(title or "") if ord(char) not in UNICODE_FORMAT_CODEPOINTS)
    return " ".join(title.replace("\xa0", " ").split())


def _clean_prowlarr_query(query: str) -> str:
    query = re.sub(r"[:：\u2010-\u2015-]+", " ", str(query or ""))
    return _collapse_title_spaces(query)


def _as_title_candidates(value) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = re.split(r"\s*/\s*|\s*[、,]\s*", value)
    else:
        values = []
    return [_collapse_title_spaces(item) for item in values if _collapse_title_spaces(item)]


def _strip_year_suffix(title: str) -> str:
    title = _collapse_title_spaces(title)
    title = re.sub(r"\([^)]*\b(?:19\d{2}|20\d{2})(?:[–-]\d{0,4})?[^)]*\)", "", title).strip()
    title = re.sub(r"\b(?:19\d{2}|20\d{2})\b", "", title).strip()
    return _collapse_title_spaces(title)


def _split_mixed_douban_title(title: str) -> tuple[str, str]:
    title = _strip_year_suffix(title)
    if not title:
        return "", ""

    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", title))
    has_latin = _title_contains_latin(title)
    if not has_chinese:
        return "", re.sub(r"[:：]+", " ", title).strip() if has_latin else ""
    if not has_latin:
        return title, ""

    first_latin = re.search(r"[A-Za-z]", title)
    if not first_latin:
        return title, ""

    chinese_title = title[: first_latin.start()].strip(" \t\r\n-—|/")
    english_title = title[first_latin.start() :].strip(" \t\r\n-—|/")
    english_title = re.sub(r"[:：]+", " ", english_title).strip()
    return _collapse_title_spaces(chinese_title), _collapse_title_spaces(english_title)


def _clean_english_title(title: str) -> str:
    title = _strip_year_suffix(title)
    title = re.sub(r"[:：]+", " ", title).strip()
    return _collapse_title_spaces(title)


def _douban_media_type(data: dict) -> str:
    raw_types = {
        str(data.get("type") or "").strip().lower(),
        str(data.get("subtype") or "").strip().lower(),
    }
    if data.get("is_tv") or raw_types.intersection({"tv", "series", "tv_series", "show"}):
        return "series"
    if raw_types.intersection({"movie", "film"}):
        return "movie"
    return ""


def _douban_cover_url(data: dict) -> str:
    direct = data.get("cover_url")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    pic = data.get("pic") if isinstance(data.get("pic"), dict) else {}
    for key in ("large", "normal", "small"):
        value = pic.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    cover = data.get("cover")
    if isinstance(cover, str):
        return cover.strip()
    if isinstance(cover, dict):
        for key in ("large", "normal", "url"):
            value = cover.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_douban_metadata(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    data = payload.get("subject") if isinstance(payload.get("subject"), dict) else payload
    if not isinstance(data, dict):
        return None

    raw_title = _collapse_title_spaces(data.get("title") or data.get("name"))
    year = _collapse_title_spaces(data.get("release_year") or data.get("year"))
    chinese_title, mixed_english_title = _split_mixed_douban_title(raw_title)
    chinese_title = chinese_title or raw_title
    english_candidates = [
        data.get("original_title"),
        data.get("originalTitle"),
        data.get("original_name"),
        data.get("originalName"),
        data.get("foreign_title"),
        data.get("foreignTitle"),
    ]
    english_candidates.extend(_as_title_candidates(data.get("aka")))
    english_candidates.extend(_as_title_candidates(data.get("aka_titles")))
    english_candidates.extend(_as_title_candidates(data.get("aliases")))
    english_candidates.extend(_as_title_candidates(data.get("alias")))

    english_title = ""
    for candidate in english_candidates:
        candidate = _clean_english_title(candidate)
        if candidate and _title_contains_latin(candidate):
            english_title = candidate
            break

    if not english_title:
        english_title = mixed_english_title
    if not english_title and raw_title and _title_contains_latin(raw_title):
        _, english_title = _split_mixed_douban_title(raw_title)
    if not english_title and chinese_title and _title_contains_latin(chinese_title):
        english_title = _clean_english_title(chinese_title)

    if not chinese_title:
        return None

    return {
        "source": "douban",
        "subject_id": str(data.get("id") or data.get("subject_id") or "").strip(),
        "media_type": _douban_media_type(data),
        "chinese_title": chinese_title,
        "english_title": english_title,
        "year": year,
        "cover_url": _douban_cover_url(data),
    }


def _query_from_naming_metadata(metadata: dict) -> str:
    query = metadata.get("english_title") or metadata.get("chinese_title") or ""
    year = metadata.get("year") or ""
    if query and year and year not in query:
        query = f"{query} {year}"
    return _collapse_title_spaces(query)


def _metadata_from_naming_metadata(naming_metadata: dict, query: str = "", original_url: str = "") -> dict:
    external_ids = {}
    source = naming_metadata.get("source") or ""
    if source == "douban" and naming_metadata.get("subject_id"):
        external_ids["douban_subject"] = naming_metadata["subject_id"]

    metadata = build_search_metadata(
        source=source,
        media_type=naming_metadata.get("media_type") or "",
        chinese_title=naming_metadata.get("chinese_title") or "",
        english_title=naming_metadata.get("english_title") or "",
        year=naming_metadata.get("year") or "",
        query=query or _query_from_naming_metadata(naming_metadata),
        original_url=original_url,
        collection_chinese_title=naming_metadata.get("collection_chinese_title")
        or naming_metadata.get("chinese_collection_title")
        or "",
        collection_english_title=naming_metadata.get("collection_english_title")
        or naming_metadata.get("english_collection_title")
        or "",
        external_ids=external_ids,
        evidence=[
            {
                "source": source,
                "field": "naming_metadata",
            }
        ],
    )
    if naming_metadata.get("cover_url"):
        metadata["cover_url"] = naming_metadata["cover_url"]
        metadata["cover_source"] = naming_metadata.get("cover_source") or source
    return metadata


def _metadata_matches_plain_query(metadata: dict, query: str) -> bool:
    normalized_query = _normalize_match_title(query)
    if not normalized_query:
        return False

    candidates = [
        metadata.get("chinese_title"),
        metadata.get("english_title"),
        _query_from_naming_metadata(metadata),
    ]
    return any(_normalize_match_title(candidate) == normalized_query for candidate in candidates)


def _normalize_match_title(title: str) -> str:
    title = _collapse_title_spaces(title).casefold()
    title = re.sub(r"\b(?:19\d{2}|20\d{2})\b", " ", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title)
    return _collapse_title_spaces(title)


def _fetch_douban_json_metadata(endpoint: str, referer: str) -> dict | None:
    response = requests.get(
        endpoint,
        headers={
            **_douban_request_headers(referer),
            "Accept": "application/json, text/plain, */*",
        },
        timeout=10,
    )
    response.raise_for_status()
    return _extract_douban_metadata(response.json())


def _extract_douban_subject_urls(html_text: str) -> list[str]:
    urls = []
    seen = set()
    text = html.unescape(str(html_text or ""))
    text = unquote_plus(unquote(text.replace("\\/", "/")))

    pattern = re.compile(r"(?:https?:)?//movie\.douban\.com/subject/(\d+)/?|(?<![\w/])/subject/(\d+)/?")
    for match in pattern.finditer(text):
        subject_id = match.group(1) or match.group(2)
        if subject_id in seen:
            continue
        seen.add(subject_id)
        urls.append(f"https://movie.douban.com/subject/{subject_id}/")
    return urls


def _fetch_douban_metadata_from_search(query: str, require_exact_match: bool) -> dict | None:
    response = requests.get(
        "https://www.douban.com/search",
        params={"cat": "1002", "q": query},
        headers=_douban_request_headers("https://www.douban.com/"),
        timeout=10,
    )
    response.raise_for_status()

    for subject_url in _extract_douban_subject_urls(response.text):
        metadata = _fetch_builtin_douban_metadata(subject_url)
        if metadata and (not require_exact_match or _metadata_matches_plain_query(metadata, query)):
            _log_info(f"豆瓣反查命中元数据 query={query} url={subject_url} metadata={metadata}")
            return metadata

    return None


def _fetch_douban_metadata_for_plain_query(query: str) -> dict | None:
    query = _collapse_title_spaces(query)
    if not query:
        return None

    metadata = _fetch_douban_metadata_from_search(query, require_exact_match=True)
    if metadata:
        return metadata

    _log_info(f"普通片名豆瓣反查未命中 query={query}")
    return None


def _metadata_lookup_query_from_ai_candidate(item: dict) -> str:
    query = _clean_prowlarr_query(item.get("query") or item.get("title") or "")
    if not query:
        return ""

    if item.get("scope") == "episode":
        query = re.sub(r"(?i)\b(S\d{1,2})\s*E\d{1,3}\b", r"\1", query)
        query = re.sub(r"(?i)\bepisode\s*\d{1,3}\b", " ", query)
        query = re.sub(r"\s*第\s*(?:\d+|[零〇一二两三四五六七八九十]+)\s*[集话話]\s*", " ", query)

    return _clean_prowlarr_query(query)


def _fetch_douban_metadata_for_external_title(title: str, year: str = "") -> tuple[dict | None, str]:
    query = _clean_prowlarr_query(
        _query_from_naming_metadata(
            {
                "english_title": title,
                "year": year,
            }
        )
    )
    if not query:
        return None, ""

    metadata = _fetch_douban_metadata_from_search(query, require_exact_match=True)
    if metadata:
        _log_info(f"外站标题豆瓣反查命中 query={query} metadata={metadata}")
        return metadata, query

    _log_info(f"外站标题豆瓣反查未命中 query={query}")
    return None, query


def format_size(size) -> str:
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0

    if size <= 0:
        return "未知"

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def parse_douban_title(html: str) -> str:
    return parse_media_page_title(html)


def get_pending_search_task(task_id: str):
    task = pending_search_tasks.get(task_id)
    if not task:
        return None

    if time.time() - task.get("created_at", 0) > SEARCH_TASK_TTL_SECONDS:
        pending_search_tasks.pop(task_id, None)
        _release_search_plan(task.get("search_plan"))
        return None

    return task


def get_pending_entry_confirmation(task_id: str):
    task = pending_entry_confirmations.get(task_id)
    if not task:
        return None
    if time.time() - task.get("created_at", 0) > SEARCH_TASK_TTL_SECONDS:
        pending_entry_confirmations.pop(task_id, None)
        temporary_special_allocator.release(task_id)
        return None
    return task


def _contract_from_search_plan(plan: dict) -> dict:
    value = plan.get("media_metadata") if isinstance(plan, dict) else None
    return value if isinstance(value, dict) else {}


def _build_media_metadata_text(plan: dict) -> str:
    contract = _contract_from_search_plan(plan)
    identity = contract.get("identity") or {}
    relation = contract.get("relation") or {}
    target = relation.get("target_series") or {}
    placement = contract.get("placement") or {}
    source_entry = contract.get("source_entry") or {}
    episode = placement.get("episode_number")
    episode_number = int(episode) if episode is not None else None
    width = 3 if episode_number is not None and episode_number >= 100 else 2
    marker = (
        f"S{int(placement.get('season_number') or 0):02d}E{episode_number:0{width}d}"
        if episode_number is not None
        else "未分配"
    )
    lines = [
        "📋 媒体元数据方案",
        f"目标：{identity.get('chinese_title') or ''} / {identity.get('english_title') or ''} ({identity.get('year') or '年份未知'})",
        f"内容身份：{identity.get('content_kind') or 'unknown'}",
        f"关联剧集：{target.get('chinese_title') or target.get('english_title') or '无'}",
        f"关系依据：{relation.get('source') or 'ai'}",
        f"归属：{placement.get('category_kind') or 'unknown'} / {marker}",
        f"来源条目：{source_entry.get('title') or '无'}",
    ]
    item_markers = []
    for item in contract.get("items") or []:
        season = item.get("season_number")
        episode = item.get("episode_number")
        if season is not None and episode is not None:
            item_markers.append(f"S{int(season):02d}E{int(episode):02d}")
    if item_markers:
        lines.append(f"已锁定集：{', '.join(item_markers)}")
    locator = source_entry.get("url") or source_entry.get("external_id") or ""
    if locator:
        lines.append(f"来源定位：{locator}")
    lines.append(f"搜索词：{(plan.get('prowlarr_queries') or [''])[0]}")
    lines.extend(f"⚠️ {warning}" for warning in contract.get("warnings") or [])
    return "\n".join(lines)


def _resolve_plan_selected_path(plan: dict) -> str:
    contract = _contract_from_search_plan(plan)
    route = resolve_category_route(
        init.bot_config,
        (contract.get("placement") or {}).get("category_kind"),
    )
    return str((route or {}).get("path") or "")


def _wikipedia_plan_provider(hypotheses: dict) -> dict:
    config = (((init.bot_config or {}).get("metadata") or {}).get("wikipedia") or {})
    if not config.get("enable", True):
        return {
            "source": "wikipedia",
            "status": "disabled",
            "facts": [],
            "source_urls": [],
            "error": "",
        }
    queries = ((hypotheses.get("source_queries") or {}).get("wikipedia") or [])
    languages = tuple(
        str(item)
        for item in (config.get("languages") or ["zh", "en"])
        if str(item).strip()
    )
    timeout = float(config.get("timeout") or 10)
    return lookup_wikipedia_evidence(
        queries,
        languages=languages or ("zh", "en"),
        timeout=timeout,
    )


def _douban_plan_provider(hypotheses: dict) -> dict:
    facts = []
    for query in ((hypotheses.get("source_queries") or {}).get("douban") or []):
        try:
            metadata = _fetch_douban_metadata_for_plain_query(query)
        except Exception as exc:
            return {
                "source": "douban",
                "status": "server_down",
                "facts": [],
                "source_urls": [],
                "error": str(exc),
            }
        if metadata:
            facts.append(metadata)
    return {
        "source": "douban",
        "status": "ok" if facts else "not_found",
        "facts": facts,
        "source_urls": [],
        "error": "",
    }


def _tvdb_plan_provider(hypotheses: dict) -> dict:
    facts = []
    try:
        for hypothesis in hypotheses.get("hypotheses") or []:
            title = hypothesis.get("title") or ""
            year = hypothesis.get("year") or ""
            movies = [
                item
                for item in (search_tvdb_movies(title, year=year) or [])
                if isinstance(item, dict)
                and str(
                    item.get("tvdb_movie_id")
                    or item.get("tvdb_id")
                    or item.get("id")
                    or ""
                ).strip()
            ][:5]
            series = [
                item
                for item in (search_tvdb_series(title, year=year) or [])
                if isinstance(item, dict)
                and str(
                    item.get("tvdb_series_id")
                    or item.get("tvdb_id")
                    or item.get("id")
                    or ""
                ).strip()
            ][:5]
            episodes_by_series = {}
            for item in series:
                series_id = str(item.get("tvdb_series_id") or "")
                if series_id:
                    episodes_by_series[series_id] = get_tvdb_series_episodes(series_id)
            if movies or series or any(episodes_by_series.values()):
                facts.append(
                    {
                        "hypothesis": hypothesis,
                        "movies": movies,
                        "series": series,
                        "episodes_by_series": episodes_by_series,
                    }
                )
    except TvdbConfigError as exc:
        return {
            "source": "tvdb",
            "status": "disabled",
            "facts": [],
            "source_urls": [],
            "error": str(exc),
        }
    except (TvdbRequestError, OSError) as exc:
        return {
            "source": "tvdb",
            "status": "server_down",
            "facts": [],
            "source_urls": [],
            "error": str(exc),
        }
    return {
        "source": "tvdb",
        "status": "ok" if facts else "not_found",
        "facts": facts,
        "source_urls": [],
        "error": "",
    }


def _occupied_special_numbers(contract: dict) -> set[int]:
    evidence_values = (
        ((contract.get("evidence") or {}).get("occupied_special_numbers") or [])
        if isinstance(contract, dict)
        else []
    )
    occupied = {
        int(value)
        for value in evidence_values
        if str(value).isdigit() and int(value) >= 100
    }
    route = resolve_category_route(
        init.bot_config,
        (contract.get("placement") or {}).get("category_kind"),
    )
    storage = getattr(init, "openapi_115", None)
    category_info = storage.get_file_info(route["path"]) if storage and route else None
    if not category_info:
        raise RuntimeError("cannot inspect configured category root")
    season_path = "/".join((
        route["path"].rstrip("/"),
        series_folder_name(contract),
        series_season_directory_name(contract, 0),
    ))
    if storage.get_file_info(season_path):
        for item in storage.get_files_from_dir(season_path) or []:
            name = (
                str(item.get("name") or item.get("fn") or item)
                if isinstance(item, dict)
                else str(item)
            )
            match = re.search(r"(?i)\bS00E(\d{3,})\b", name)
            if match:
                occupied.add(int(match.group(1)))
    return occupied


def _candidate_display_scope(candidate: dict) -> str:
    scope = candidate.get("scope")
    if scope == "episode":
        return f"S{int(candidate.get('season_number')):02d}E{int(candidate.get('episode_number')):02d}"
    if scope == "season":
        return f"S{int(candidate.get('season_number')):02d} 整季"
    if scope == "whole_series":
        return "全集"
    return "电影"


def _candidate_display_title(candidate: dict) -> str:
    title = candidate.get("title") or candidate.get("english_title") or ""
    chinese_title = candidate.get("chinese_title") or ""
    year = candidate.get("year") or ""
    parts = []
    if chinese_title and chinese_title != title:
        parts.append(chinese_title)
    if title:
        parts.append(title)
    display = " ".join(parts) or "未知条目"
    if year and year not in display:
        display = f"{display} ({year})"
    return display


def _candidate_button_text(candidate: dict) -> str:
    prefix = "✅ 推荐 " if candidate.get("recommended") else ""
    text = f"{prefix}{_candidate_display_title(candidate)} · {_candidate_display_scope(candidate)}"
    return text[:60]


def _build_entry_confirmation_keyboard(task_id: str, candidates: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(_candidate_button_text(candidate), callback_data=f"entry_confirm:{task_id}:{index}")]
        for index, candidate in enumerate(candidates)
    ]
    keyboard.append([InlineKeyboardButton("取消", callback_data=f"entry_cancel:{task_id}")])
    return InlineKeyboardMarkup(keyboard)


def _build_entry_confirmation_text(candidates: list[dict]) -> str:
    lines = ["请确认要搜索的影视条目和范围：", ""]
    for index, candidate in enumerate(candidates, start=1):
        marker = "（推荐）" if candidate.get("recommended") else ""
        lines.append(f"{index}. {_candidate_display_title(candidate)} · {_candidate_display_scope(candidate)}{marker}")
        external_ids = candidate.get("external_ids") if isinstance(candidate.get("external_ids"), dict) else {}
        source = " / ".join(f"{key}:{value}" for key, value in external_ids.items() if value)
        if source:
            lines.append(f"   来源: {source}")
    return "\n".join(lines).strip()


def _candidate_tvdb_id(candidate: dict) -> str:
    external_ids = candidate.get("external_ids") if isinstance(candidate.get("external_ids"), dict) else {}
    return str(
        external_ids.get("tvdb")
        or candidate.get("tvdb_id")
        or candidate.get("tvdb_series_id")
        or candidate.get("tvdb_movie_id")
        or ""
    ).strip()


async def _backfill_candidate_covers(candidates: list[dict]) -> list[dict]:
    backfilled = []
    for candidate in candidates or []:
        item = candidate.copy()
        tvdb_id = _candidate_tvdb_id(item)
        if not tvdb_id or (item.get("cover_source") == "tvdb" and item.get("cover_url")):
            backfilled.append(item)
            continue

        try:
            if item.get("media_type") == "movie":
                cover_url = await asyncio.to_thread(get_tvdb_movie_artwork_url, tvdb_id)
            else:
                cover_url = await asyncio.to_thread(get_tvdb_series_artwork_url, tvdb_id)
        except (TvdbConfigError, TvdbRequestError) as e:
            _log_warn(f"TVDB 封面读取失败 type={item.get('media_type')} id={tvdb_id}: {e}")
            cover_url = ""

        if cover_url:
            item["cover_url"] = cover_url
            item["cover_source"] = "tvdb"
        backfilled.append(item)
    return backfilled


async def _send_candidate_info_card(update: Update, candidate: dict):
    if not candidate.get("cover_url"):
        return

    message = getattr(update, "message", None)
    if message is None and getattr(update, "callback_query", None) is not None:
        message = getattr(update.callback_query, "message", None)
    if message is None:
        return

    media_label = "电影" if candidate.get("media_type") == "movie" else "剧集"
    lines = [f"已识别{media_label}：{_candidate_display_title(candidate)}"]
    tvdb_id = _candidate_tvdb_id(candidate)
    if tvdb_id:
        lines.append(f"TVDB：`{tvdb_id}`")
    if candidate.get("year"):
        lines.append(f"年份：{candidate['year']}")
    caption = "\n".join(lines)
    try:
        await message.reply_photo(photo=candidate["cover_url"], caption=caption)
    except Exception as e:
        _log_warn(f"媒体封面消息发送失败 type={candidate.get('media_type')} tvdb_id={tvdb_id}: {e}")


def _candidate_naming_metadata(candidate: dict):
    metadata = candidate.get("naming_metadata")
    result = metadata.copy() if isinstance(metadata, dict) else {}
    english_title = candidate.get("english_title") or candidate.get("title") or ""
    result.update(
        {
            "source": result.get("source") or candidate.get("source") or "confirmed",
            "media_type": candidate.get("media_type") or "",
            "chinese_title": candidate.get("chinese_title") or "",
            "english_title": english_title,
            "year": candidate.get("year") or "",
            "cover_url": candidate.get("cover_url") or "",
            "cover_source": candidate.get("cover_source") or "",
        }
    )
    return result


def _candidate_search_metadata(candidate: dict):
    metadata = candidate.get("metadata")
    result = metadata.copy() if isinstance(metadata, dict) else {}
    external_ids = candidate.get("external_ids") if isinstance(candidate.get("external_ids"), dict) else {}
    result.update(
        {
            "source": result.get("source") or candidate.get("source") or "confirmed",
            "media_type": candidate.get("media_type") or "",
            "english_title": candidate.get("english_title") or candidate.get("title") or "",
            "chinese_title": candidate.get("chinese_title") or "",
            "year": candidate.get("year") or "",
            "query": candidate_to_prowlarr_query(candidate),
            "external_ids": external_ids.copy(),
            "selected_scope": candidate.get("scope") or "",
            "season_number": candidate.get("season_number"),
            "episode_number": candidate.get("episode_number"),
            "cover_url": candidate.get("cover_url") or "",
            "cover_source": candidate.get("cover_source") or "",
        }
    )
    return result


async def _backfill_missing_chinese_title(naming_metadata: dict | None, metadata: dict | None) -> tuple[dict | None, dict | None]:
    if not naming_metadata:
        return naming_metadata, metadata
    if naming_metadata.get("chinese_title") or (metadata or {}).get("chinese_title"):
        return naming_metadata, metadata

    english_title = naming_metadata.get("english_title") or (metadata or {}).get("english_title") or ""
    year = naming_metadata.get("year") or (metadata or {}).get("year") or ""
    if not english_title or not year:
        return naming_metadata, metadata

    douban_metadata, lookup_query = await asyncio.to_thread(
        _fetch_douban_metadata_for_external_title,
        english_title,
        year,
    )
    if not douban_metadata or not douban_metadata.get("chinese_title"):
        _log_info(
            f"metadata_backfill source=douban reason=missing_chinese_title "
            f"query={lookup_query or _clean_prowlarr_query(f'{english_title} {year}'.strip())} status=miss"
        )
        external_ids = (metadata or {}).get("external_ids") if isinstance((metadata or {}).get("external_ids"), dict) else {}
        ai_metadata = await asyncio.to_thread(
            infer_metadata_backfill_with_ai,
            {
                "reason": "missing_chinese_title",
                "media_type": naming_metadata.get("media_type") or (metadata or {}).get("media_type") or "",
                "english_title": english_title,
                "year": year,
                "query": (metadata or {}).get("query") or _clean_prowlarr_query(f"{english_title} {year}".strip()),
                "external_ids": external_ids.copy(),
            },
        )
        if not ai_metadata or not ai_metadata.get("chinese_title"):
            _log_info(
                f"metadata_backfill source=ai_metadata_backfill reason=missing_chinese_title "
                f"title={english_title} year={year} status=miss"
            )
            return naming_metadata, metadata

        _log_info(
            f"metadata_backfill source=ai_metadata_backfill reason=missing_chinese_title "
            f"title={english_title} year={year} chinese_title={ai_metadata.get('chinese_title')}"
        )
        backfilled_naming = naming_metadata.copy()
        backfilled_naming["chinese_title"] = ai_metadata["chinese_title"]
        if ai_metadata.get("english_title"):
            backfilled_naming["english_title"] = ai_metadata["english_title"]
        if ai_metadata.get("year"):
            backfilled_naming["year"] = ai_metadata["year"]
        if ai_metadata.get("media_type"):
            backfilled_naming["media_type"] = ai_metadata["media_type"]

        backfilled_metadata = (metadata or {}).copy()
        backfilled_metadata["chinese_title"] = ai_metadata["chinese_title"]
        if ai_metadata.get("english_title"):
            backfilled_metadata["english_title"] = ai_metadata["english_title"]
        if ai_metadata.get("year"):
            backfilled_metadata["year"] = ai_metadata["year"]
        if ai_metadata.get("media_type"):
            backfilled_metadata["media_type"] = ai_metadata["media_type"]
        existing_external_ids = (
            backfilled_metadata.get("external_ids").copy()
            if isinstance(backfilled_metadata.get("external_ids"), dict)
            else {}
        )
        for key, value in (ai_metadata.get("external_ids") or {}).items():
            if value and key not in existing_external_ids:
                existing_external_ids[key] = value
        backfilled_metadata["external_ids"] = existing_external_ids
        evidence = backfilled_metadata.get("evidence")
        if isinstance(evidence, list):
            evidence = list(evidence)
        else:
            evidence = []
        evidence.append(
            {
                "source": "ai_metadata_backfill",
                "field": "missing_chinese_title_backfill",
                "title": english_title,
                "year": year,
            }
        )
        backfilled_metadata["evidence"] = evidence
        return backfilled_naming, backfilled_metadata

    _log_info(
        f"metadata_backfill source=douban reason=missing_chinese_title "
        f"query={lookup_query or _clean_prowlarr_query(f'{english_title} {year}'.strip())} "
        f"chinese_title={douban_metadata.get('chinese_title')}"
    )
    backfilled_naming = naming_metadata.copy()
    backfilled_naming["chinese_title"] = douban_metadata["chinese_title"]

    backfilled_metadata = (metadata or {}).copy()
    backfilled_metadata["chinese_title"] = douban_metadata["chinese_title"]
    evidence = backfilled_metadata.get("evidence")
    if isinstance(evidence, list):
        evidence = list(evidence)
    else:
        evidence = []
    evidence.append(
        {
            "source": "douban",
            "field": "missing_chinese_title_backfill",
            "query": lookup_query,
        }
    )
    backfilled_metadata["evidence"] = evidence
    return backfilled_naming, backfilled_metadata


async def _send_confirmed_candidate_search(update: Update, context: ContextTypes.DEFAULT_TYPE, candidate: dict):
    query = candidate_to_prowlarr_query(candidate)
    await _send_candidate_info_card(update, candidate)
    return await _send_search_results(
        update,
        context,
        query,
        naming_metadata=_candidate_naming_metadata(candidate),
        metadata=_candidate_search_metadata(candidate),
    )


def _format_indexer_summary(indexer_summary: dict | None) -> list[str]:
    if not indexer_summary:
        return []

    lines = ["", "📡 搜刮器总结"]
    result_sources = indexer_summary.get("result_sources") or {}
    if result_sources:
        source_text = "、".join(
            f"{name} x{count}"
            for name, count in sorted(result_sources.items(), key=lambda item: (-item[1], item[0]))
        )
    else:
        source_text = "无"
    lines.append(f"结果来源: {source_text}")

    enabled_indexers = indexer_summary.get("enabled_indexers") or []
    if enabled_indexers:
        lines.append(f"启用搜刮器: {len(enabled_indexers)} 个")

    down_indexers = indexer_summary.get("down_indexers") or []
    if down_indexers:
        down_lines = []
        for item in down_indexers[:5]:
            source = item.get("source") or "Prowlarr"
            message = item.get("message") or "健康检查异常"
            down_lines.append(f"{source} - {message}")
        lines.append(f"疑似 Down: {'; '.join(down_lines)}")
    elif not indexer_summary.get("error"):
        lines.append("疑似 Down: Prowlarr 健康检查未报告异常")

    if indexer_summary.get("error"):
        lines.append(f"健康检查读取失败: {indexer_summary['error']}")

    return lines


def build_results_text(query: str, results: list[dict], indexer_summary: dict | None = None) -> str:
    lines = [f"🔍 搜索结果: {query}", ""]
    for index, item in enumerate(results, start=1):
        title = item.get("title") or "未命名资源"
        if len(title) > 160:
            title = f"{title[:157]}..."
        features = item.get("features") or []
        feature_text = " / ".join(features) if features else "未识别"
        indexer = item.get("indexer") or "未知"
        seeders = item.get("seeders", 0)
        score = item.get("score", 0)

        lines.extend(
            [
                f"{index}. 评分: {score}",
                title,
                f"大小: {format_size(item.get('size'))} | seeders: {seeders} | indexer: {indexer}",
                f"特征: {feature_text}",
                "",
            ]
        )

    lines.extend(_format_indexer_summary(indexer_summary))
    return "\n".join(lines).strip()


def _build_results_keyboard(task_id: str, results: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"选择 {index}", callback_data=f"search_pick:{task_id}:{index - 1}")]
        for index, item in enumerate(results, start=1)
        if item.get("magnet_url") or item.get("download_url")
    ]
    keyboard.append([InlineKeyboardButton("取消", callback_data=f"search_cancel:{task_id}")])
    return InlineKeyboardMarkup(keyboard)


def _build_main_category_keyboard(task_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"search_path:{task_id}:{index}")]
        for index, category in enumerate(get_save_directories())
    ]
    if hasattr(init, "bot_session") and "movie_last_save" in init.bot_session:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 上次保存: {init.bot_session['movie_last_save']}",
                    callback_data=f"search_last:{task_id}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("取消", callback_data=f"search_cancel:{task_id}")])
    return InlineKeyboardMarkup(keyboard)


def _get_selected_link(context: ContextTypes.DEFAULT_TYPE):
    item = context.user_data.get("search_selected_item") or {}
    return item.get("magnet_url") or item.get("download_url")


async def _resolve_selected_link(context: ContextTypes.DEFAULT_TYPE):
    item = context.user_data.get("search_selected_item") or {}
    return await asyncio.to_thread(resolve_prowlarr_download_url, item)


def _get_result_limit() -> int:
    prowlarr_config = (init.bot_config.get("search") or {}).get("prowlarr") or {}
    try:
        return int(prowlarr_config.get("result_limit", 8))
    except (TypeError, ValueError):
        return 8


def _extract_command_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    if context.args:
        return " ".join(context.args).strip()

    text = update.message.text if update.message else ""
    return text.split(maxsplit=1)[1].strip() if " " in text else ""


def _is_douban_url(raw_query: str) -> bool:
    return bool(extract_douban_subject_id(raw_query))


def _extract_imdb_title_id(raw_query: str) -> str:
    match = re.search(r"(?i)/title/(tt\d+)/?", str(raw_query or ""))
    return match.group(1).lower() if match else ""


def _is_imdb_url(raw_query: str) -> bool:
    return bool(_extract_imdb_title_id(raw_query))


def _metadata_source_from_url(raw_url: str) -> str:
    host = urlparse(str(raw_url or "").strip()).netloc.lower()
    if "imdb.com" in host:
        return "imdb"
    if "thetvdb.com" in host or "tvdb.com" in host:
        return "tvdb"
    if "themoviedb.org" in host or "tmdb.org" in host:
        return "tmdb"
    if "douban.com" in host:
        return "douban"
    return "metadata_url"


def _media_type_from_metadata_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "thetvdb.com" in host or "tvdb.com" in host or "/tv/" in path:
        return "series"
    if "/movie/" in path:
        return "movie"
    return "movie"


def _is_supported_http_download(raw_query: str) -> bool:
    return is_supported_metadata_url(raw_query)


def _douban_request_headers(referer: str = "") -> dict:
    headers = {
        "User-Agent": init.USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _fetch_douban_json_title(endpoint: str, parser, referer: str) -> str:
    response = requests.get(
        endpoint,
        headers={
            **_douban_request_headers(referer),
            "Accept": "application/json, text/plain, */*",
        },
        timeout=10,
    )
    response.raise_for_status()
    return parser(response.json())


def _fetch_builtin_douban_title(url: str) -> str:
    return _fetch_builtin_douban_title_fallback(url)


def _fetch_builtin_douban_metadata(url: str) -> dict | None:
    subject_id = extract_douban_subject_id(url)
    if not subject_id:
        return None

    attempts = [
        (
            "subject_abstract",
            f"https://movie.douban.com/j/subject_abstract?subject_id={subject_id}",
            f"https://movie.douban.com/subject/{subject_id}/",
        ),
        (
            "rexxar",
            f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}",
            f"https://m.douban.com/movie/subject/{subject_id}/",
        ),
    ]
    for source, endpoint, referer in attempts:
        try:
            metadata = _fetch_douban_json_metadata(endpoint, referer)
            if metadata:
                _log_info(f"豆瓣链接解析元数据 source={source} subject={subject_id} metadata={metadata}")
                return metadata
        except Exception as e:
            _log_warn(f"豆瓣内建JSON元数据解析失败 source={source} subject={subject_id}: {e}")

    return None


def _fetch_builtin_douban_title_fallback(url: str) -> str:
    subject_id = extract_douban_subject_id(url)
    if not subject_id:
        return ""

    fallback_title = ""
    attempts = [
        (
            "subject_abstract",
            f"https://movie.douban.com/j/subject_abstract?subject_id={subject_id}",
            parse_douban_subject_abstract_title,
            f"https://movie.douban.com/subject/{subject_id}/",
        ),
        (
            "rexxar",
            f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}",
            parse_douban_rexxar_title,
            f"https://m.douban.com/movie/subject/{subject_id}/",
        ),
    ]
    for source, endpoint, parser, referer in attempts:
        try:
            title = _fetch_douban_json_title(endpoint, parser, referer)
            if title:
                _log_info(f"豆瓣链接解析候选 source={source} subject={subject_id} title={title}")
                if _title_contains_latin(title):
                    _log_info(f"豆瓣链接解析命中英文/原标题 source={source} subject={subject_id} title={title}")
                    return title
                fallback_title = fallback_title or title
        except Exception as e:
            _log_warn(f"豆瓣内建JSON标题解析失败 source={source} subject={subject_id}: {e}")

    try:
        response = requests.get(
            f"https://m.douban.com/movie/subject/{subject_id}/",
            headers=_douban_request_headers("https://m.douban.com/movie/"),
            timeout=10,
        )
        response.raise_for_status()
        title = parse_douban_mobile_title(response.text)
        if title:
            _log_info(f"豆瓣链接解析候选 source=mobile_html subject={subject_id} title={title}")
            if _title_contains_latin(title):
                _log_info(f"豆瓣链接解析命中英文/原标题 source=mobile_html subject={subject_id} title={title}")
                return title
            fallback_title = fallback_title or title
    except Exception as e:
        _log_warn(f"豆瓣移动页标题解析失败 subject={subject_id}: {e}")

    if fallback_title:
        _log_info(f"豆瓣链接解析使用中文兜底 subject={subject_id} title={fallback_title}")
    return fallback_title


def _fetch_media_page_title(url: str) -> str:
    if _is_douban_url(url):
        try:
            douban_title = _fetch_builtin_douban_title(url)
            if douban_title:
                return douban_title
        except Exception as e:
            _log_warn(f"豆瓣内建标题解析失败，回退到页面标题解析: {e}")

    response = requests.get(url, headers={"User-Agent": init.USER_AGENT}, timeout=10)
    response.raise_for_status()
    title = parse_media_page_title(response.text)
    if _is_douban_url(url) and title in {"豆瓣", "豆瓣电影"}:
        title = parse_douban_page_title(response.text)
    _log_info(f"媒体页面标题解析完成 url={url} title={title}")
    return title


def _fetch_imdb_suggestion_metadata(imdb_id: str) -> dict | None:
    imdb_id = _collapse_title_spaces(imdb_id).lower()
    if not re.fullmatch(r"tt\d+", imdb_id or ""):
        return None

    response = requests.get(
        f"https://v3.sg.media-imdb.com/suggestion/t/{imdb_id}.json",
        headers={"User-Agent": init.USER_AGENT, "Accept": "application/json, text/plain, */*"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("d") if isinstance(data, dict) else []
    if not isinstance(candidates, list):
        return None

    for item in candidates:
        if not isinstance(item, dict) or str(item.get("id") or "").lower() != imdb_id:
            continue
        title = _clean_english_title(item.get("l") or "")
        year = _collapse_title_spaces(item.get("y") or item.get("yr") or "")
        if title:
            return {"title": title, "year": year}

    return None


def _split_external_title_year(title: str) -> dict | None:
    title = _collapse_title_spaces(title)
    if not title:
        return None

    year = ""
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", title)
    if year_match:
        year = year_match.group(1)

    english_title = _clean_english_title(title)
    if not english_title:
        return None

    return {
        "title": english_title,
        "year": year,
    }


def _title_from_metadata_url_slug(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not parts:
        return ""

    slug = parts[-1]
    if re.fullmatch(r"tt\d+", slug, re.IGNORECASE):
        return ""
    slug = re.sub(r"^\d+[-_]+", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\b(19\d{2}|20\d{2})\b.*$", r"\1", slug)
    return _collapse_title_spaces(slug).title()


def _fetch_external_title_metadata(raw_url: str) -> dict | None:
    if _is_imdb_url(raw_url):
        metadata = _fetch_imdb_suggestion_metadata(_extract_imdb_title_id(raw_url))
        if metadata:
            metadata["source"] = "imdb"
            metadata["external_id"] = _extract_imdb_title_id(raw_url)
            metadata["original_url"] = raw_url
        return metadata

    title = ""
    try:
        title = _fetch_media_page_title(raw_url)
    except Exception as e:
        _log_warn(f"外站页面标题解析失败，尝试从URL slug兜底: {e}")

    metadata = _split_external_title_year(title)
    if metadata:
        metadata["source"] = _metadata_source_from_url(raw_url)
        metadata["original_url"] = raw_url
        return metadata

    slug_title = _title_from_metadata_url_slug(raw_url)
    metadata = _split_external_title_year(slug_title)
    if metadata:
        metadata["source"] = _metadata_source_from_url(raw_url)
        metadata["original_url"] = raw_url
    return metadata


async def _resolve_query(raw_query: str) -> str | None:
    request = await _resolve_search_request(raw_query)
    return request.get("query") if request else None


async def _resolve_search_request(raw_query: str, allow_ai_fallback: bool = True) -> dict | None:
    if not is_supported_metadata_url(raw_query):
        query = _clean_prowlarr_query(raw_query)
        if not query:
            return None

        try:
            metadata = await asyncio.to_thread(_fetch_douban_metadata_for_plain_query, query)
            if metadata:
                resolved_query = _clean_prowlarr_query(_query_from_naming_metadata(metadata))
                return {
                    "query": resolved_query,
                    "naming_metadata": metadata,
                    "metadata": _metadata_from_naming_metadata(metadata, query=resolved_query),
                }
        except Exception as e:
            _log_warn(f"普通片名豆瓣反查失败: {e}")

        if allow_ai_fallback:
            _log_info(f"AI搜索清洗开始 raw={raw_query}")
            normalized = await asyncio.to_thread(normalize_search_query_with_ai, raw_query)
            if normalized:
                _log_info(f"AI搜索清洗完成 raw={raw_query} status={normalized.get('status')} candidates={len(normalized.get('lookup_candidates') or [])}")
            else:
                _log_info(f"AI搜索清洗无结果 raw={raw_query}")
            for item in (normalized or {}).get("lookup_candidates") or []:
                candidate_query = _metadata_lookup_query_from_ai_candidate(item)
                if not candidate_query:
                    continue
                try:
                    metadata = await asyncio.to_thread(_fetch_douban_metadata_for_plain_query, candidate_query)
                    if metadata:
                        resolved_query = _clean_prowlarr_query(_query_from_naming_metadata(metadata))
                        search_metadata = _metadata_from_naming_metadata(metadata, query=resolved_query)
                        if item.get("title"):
                            search_metadata["normalized_title"] = _collapse_title_spaces(item.get("title"))
                        scope = item.get("scope")
                        if scope:
                            search_metadata["selected_scope"] = scope
                            if scope in {"whole_series", "season", "episode"}:
                                search_metadata["media_type"] = "series"
                        if item.get("season_number") is not None:
                            search_metadata["season_number"] = item.get("season_number")
                        if item.get("episode_number") is not None:
                            search_metadata["episode_number"] = item.get("episode_number")
                        return {"query": resolved_query, "naming_metadata": metadata, "metadata": search_metadata}
                except Exception as e:
                    _log_warn(f"AI清洗候选豆瓣反查失败 query={candidate_query}: {e}")

            _log_info(f"AI验证兜底开始 raw={raw_query}")
            verified = await asyncio.to_thread(infer_verified_search_match_with_ai, raw_query)
            if verified:
                _log_info(f"AI验证兜底完成 raw={raw_query} status={verified.get('status')} candidates={len(verified.get('candidates') or [])}")
            else:
                _log_info(f"AI验证兜底无结果 raw={raw_query}")
            if verified and verified.get("status") == "ok" and verified.get("candidates"):
                first = verified["candidates"][0]
                title = first.get("title") or ""
                year = first.get("year") or ""
                query = _clean_prowlarr_query(f"{title} {year}".strip())
                if query:
                    metadata = build_external_metadata(
                        source="ai_verified",
                        title=title,
                        year=year,
                        external_id="",
                        original_url="",
                        media_type=first.get("media_type") or "",
                    )
                    metadata["external_ids"] = (first.get("external_ids") or {}).copy()
                    metadata["selected_scope"] = first.get("scope") or ""
                    if first.get("season_number") is not None:
                        metadata["season_number"] = first.get("season_number")
                    if first.get("episode_number") is not None:
                        metadata["episode_number"] = first.get("episode_number")
                    return {"query": query, "naming_metadata": None, "metadata": metadata}

        return None

    try:
        if _is_douban_url(raw_query):
            metadata = await asyncio.to_thread(_fetch_builtin_douban_metadata, raw_query)
            if metadata:
                query = _clean_prowlarr_query(_query_from_naming_metadata(metadata))
                _log_info(f"豆瓣链接解析为搜索词 raw={raw_query} query={query} metadata={metadata}")
                return {
                    "query": query,
                    "naming_metadata": metadata,
                    "metadata": _metadata_from_naming_metadata(metadata, query=query, original_url=raw_query),
                }

        external_metadata = await asyncio.to_thread(_fetch_external_title_metadata, raw_query)
        if external_metadata:
            metadata, query = await asyncio.to_thread(
                _fetch_douban_metadata_for_external_title,
                external_metadata.get("title"),
                external_metadata.get("year"),
            )
            if metadata:
                query = _clean_prowlarr_query(_query_from_naming_metadata(metadata))
                _log_info(
                    f"外站链接经豆瓣反查解析为搜索词 raw={raw_query} "
                    f"title={external_metadata.get('title')} year={external_metadata.get('year')} "
                    f"query={query} metadata={metadata}"
                )
                search_metadata = _metadata_from_naming_metadata(metadata, query=query, original_url=raw_query)
                search_metadata["evidence"].append(
                    {
                        "source": external_metadata.get("source") or _metadata_source_from_url(raw_query),
                        "field": "external_title_year",
                        "title": external_metadata.get("title"),
                        "year": external_metadata.get("year"),
                    }
                )
                return {"query": query, "naming_metadata": metadata, "metadata": search_metadata}

            if query:
                source = external_metadata.get("source") or _metadata_source_from_url(raw_query)
                search_metadata = build_external_metadata(
                    source=source,
                    title=external_metadata.get("title"),
                    year=external_metadata.get("year"),
                    external_id=external_metadata.get("external_id") or "",
                    original_url=raw_query,
                    media_type=_media_type_from_metadata_url(raw_query),
                )
                _log_info(
                    f"外站链接解析为英文搜索词 raw={raw_query} "
                    f"title={external_metadata.get('title')} year={external_metadata.get('year')} query={query}"
                )
                return {"query": query, "naming_metadata": None, "metadata": search_metadata}

        return None
    except Exception as e:
        _log_warn(f"媒体页面标题解析失败: {e}")
        return None


def _naming_metadata_for_selected_release(task: dict, selected_item: dict):
    metadata = task.get("naming_metadata")
    if not metadata:
        return None

    result = metadata.copy()
    result["release_title"] = selected_item.get("title") or task.get("query") or ""
    return result


def _metadata_for_selected_release(task: dict, selected_item: dict):
    metadata = task.get("metadata")
    if not metadata:
        return None

    result = metadata.copy()
    if isinstance(metadata.get("external_ids"), dict):
        result["external_ids"] = metadata["external_ids"].copy()
    if isinstance(metadata.get("evidence"), list):
        result["evidence"] = [item.copy() if isinstance(item, dict) else item for item in metadata["evidence"]]
    result["release_title"] = selected_item.get("title") or task.get("query") or metadata.get("query") or ""
    return result


def _owner_matches(task: dict, user_id: int) -> bool:
    return task.get("user_id") == user_id


def _submit_download_request(context: ContextTypes.DEFAULT_TYPE, request: DownloadRequest):
    application = getattr(context, "application", None)
    registry = None
    if application is not None:
        registry = application.bot_data.get("telepiplex_registry")
    if registry is None:
        raise DownloadProviderUnavailable("未注册下载 provider，无法处理媒体搜索下载请求。")
    return registry.dispatch_download(request)


async def _reply_or_send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    timeout_kwargs = {
        "connect_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "read_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "write_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "pool_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
    }
    timeout_kwargs.update(kwargs)
    try:
        if update.callback_query:
            return await update.callback_query.edit_message_text(text=text, **timeout_kwargs)
        if update.message:
            return await update.message.reply_text(text, **timeout_kwargs)
        return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **timeout_kwargs)
    except NetworkError as e:
        _log_warn(f"Telegram 搜索消息发送超时/网络异常，继续执行搜索流程: {e}")
        return None


async def _send_search_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs):
    timeout_kwargs = {
        "connect_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "read_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "write_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "pool_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
    }
    timeout_kwargs.update(kwargs)
    try:
        return await context.bot.send_message(chat_id=chat_id, text=text, **timeout_kwargs)
    except NetworkError as e:
        _log_warn(f"Telegram 搜索消息发送超时/网络异常，继续执行搜索流程: {e}")
        return None


def _build_prowlarr_progress_text(
    query: str,
    elapsed_seconds: int,
    completed: bool = False,
) -> str:
    if completed:
        return f"✅ Prowlarr 搜索完成：{query}\n用时 {elapsed_seconds} 秒。"
    return (
        f"⏳ Prowlarr 正在搜索：{query}\n"
        f"已等待 {elapsed_seconds} 秒。部分索引器需要 Cloudflare 解析，请继续等待。"
    )


async def _edit_prowlarr_progress_message(status_message, text: str):
    edit_text = getattr(status_message, "edit_text", None)
    if not callable(edit_text):
        return
    try:
        await edit_text(
            text=text,
            disable_web_page_preview=True,
            connect_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            read_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            write_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
            pool_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        )
    except Exception as e:
        _log_warn(f"Telegram Prowlarr 搜索进度更新失败，继续执行搜索流程: {e}")


async def _search_prowlarr_with_progress(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    progress_interval: float = SEARCH_PROGRESS_INTERVAL_SECONDS,
    media_type: str = "",
    status_message=None,
    clock=time.monotonic,
):
    search_task = asyncio.create_task(
        asyncio.to_thread(_search_prowlarr_release_categories, query, media_type=media_type)
    )
    started_at = clock()
    while True:
        done, _ = await asyncio.wait({search_task}, timeout=progress_interval)
        if done:
            results = search_task.result()
            elapsed_seconds = max(0, int(clock() - started_at))
            await _edit_prowlarr_progress_message(
                status_message,
                _build_prowlarr_progress_text(
                    query,
                    elapsed_seconds,
                    completed=True,
                ),
            )
            return results

        elapsed_seconds = max(0, int(clock() - started_at))
        await _edit_prowlarr_progress_message(
            status_message,
            _build_prowlarr_progress_text(query, elapsed_seconds),
        )


def _search_prowlarr_release_categories(query: str, media_type: str = "") -> list[dict]:
    results = []
    seen = set()
    lookup_types = {
        "movie": ("movie",),
        "series": ("tv",),
    }.get(str(media_type or "").strip(), ("movie", "tv"))
    for lookup_type in lookup_types:
        for item in search_prowlarr(query, lookup_type):
            key = (
                item.get("magnet_url")
                or item.get("download_url")
                or item.get("title")
                or ""
            )
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            results.append(item)
    return results


def _release_search_plan(search_plan):
    if isinstance(search_plan, dict):
        temporary_special_allocator.release(str(search_plan.get("plan_id") or ""))


def _store_pending_search_task(
    update,
    query: str,
    results: list[dict],
    naming_metadata,
    metadata,
    search_plan,
    selected_path: str,
) -> str:
    task_id = uuid.uuid4().hex[:10]
    pending_search_tasks[task_id] = {
        "created_at": time.time(),
        "query": query,
        "results": results,
        "user_id": update.effective_user.id,
        "naming_metadata": naming_metadata,
        "metadata": deepcopy(metadata) if isinstance(metadata, dict) else None,
        "search_plan": (
            deepcopy(search_plan) if isinstance(search_plan, dict) else None
        ),
        "selected_path": selected_path,
    }
    return task_id


async def _send_search_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    naming_metadata=None,
    metadata=None,
    search_plan=None,
    selected_path: str = "",
):
    media_type = str(
        (metadata or {}).get("media_type")
        or (naming_metadata or {}).get("media_type")
        or ""
    ).strip()
    if media_type not in {"movie", "series"}:
        media_type = ""
    status_message = await _reply_or_send(
        update,
        context,
        _build_prowlarr_progress_text(query, 0),
        disable_web_page_preview=True,
    )
    _log_info(f"搜索片源开始 query={query} media_type={media_type or 'movie_and_series'}")

    try:
        items = await _search_prowlarr_with_progress(
            update,
            context,
            query,
            media_type=media_type,
            status_message=status_message,
        )
        results = rank_releases(items, _get_result_limit())
        indexer_summary = await asyncio.to_thread(get_prowlarr_indexer_summary, results)
    except ProwlarrConfigError as e:
        _release_search_plan(search_plan)
        await _send_search_message(context, update.effective_chat.id, f"⚠️ {e}")
        return ConversationHandler.END
    except ProwlarrRequestError as e:
        _release_search_plan(search_plan)
        await _send_search_message(context, update.effective_chat.id, f"❌ {e}")
        return ConversationHandler.END
    except Exception as e:
        _release_search_plan(search_plan)
        _log_error(f"搜索处理失败: {e}")
        await _send_search_message(context, update.effective_chat.id, f"❌ 搜索失败：{e}")
        return ConversationHandler.END

    if not results:
        _release_search_plan(search_plan)
        _log_info(f"搜索片源无结果 query={query}")
        await _send_search_message(context, update.effective_chat.id, "⚠️ 未找到可用片源，请调整关键词后重试。")
        return ConversationHandler.END

    try:
        naming_metadata, metadata = await _backfill_missing_chinese_title(
            naming_metadata, metadata
        )
    except Exception as e:
        _release_search_plan(search_plan)
        _log_error(f"搜索元数据补全失败: {e}")
        await _send_search_message(
            context,
            update.effective_chat.id,
            f"❌ 搜索元数据补全失败：{e}",
        )
        return ConversationHandler.END
    _log_info(
        f"搜索片源完成 query={query} media_type={media_type or 'movie_and_series'} "
        f"results={len(results)}"
    )
    task_id = _store_pending_search_task(
        update,
        query,
        results,
        naming_metadata,
        metadata,
        search_plan,
        selected_path,
    )

    await _send_search_message(
        context,
        update.effective_chat.id,
        build_results_text(query, results, indexer_summary=indexer_summary),
        reply_markup=_build_results_keyboard(task_id, results),
        disable_web_page_preview=True,
    )
    return SEARCH_SELECT_RESULT


async def _send_resolved_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, request: dict):
    kwargs = {"naming_metadata": request.get("naming_metadata")}
    if request.get("metadata") is not None:
        kwargs["metadata"] = request.get("metadata")
    return await _send_search_results(update, context, request["query"], **kwargs)


def _entry_from_request(request: dict) -> dict | None:
    if not request:
        return None
    naming_metadata = request.get("naming_metadata") or {}
    metadata = request.get("metadata") or {}
    external_ids = metadata.get("external_ids") if isinstance(metadata.get("external_ids"), dict) else {}
    title = (
        naming_metadata.get("english_title")
        or metadata.get("english_title")
        or request.get("query")
        or naming_metadata.get("chinese_title")
        or metadata.get("chinese_title")
        or ""
    )
    if not title:
        return None
    selected_scope = metadata.get("selected_scope") or metadata.get("scope") or ""
    media_type = metadata.get("media_type") or naming_metadata.get("media_type") or ""
    if selected_scope in {"whole_series", "season", "episode"}:
        media_type = "series"
        if metadata.get("normalized_title"):
            title = metadata.get("normalized_title")
    media_type = media_type or "movie"
    scope = selected_scope if selected_scope in {"movie", "whole_series", "season", "episode"} else ""
    if not scope:
        scope = "movie" if media_type != "series" else "whole_series"

    return {
        "media_type": media_type if media_type in {"movie", "series"} else "movie",
        "scope": scope,
        "title": title,
        "english_title": naming_metadata.get("english_title") or metadata.get("english_title") or title,
        "chinese_title": naming_metadata.get("chinese_title") or metadata.get("chinese_title") or "",
        "year": naming_metadata.get("year") or metadata.get("year") or "",
        "external_ids": external_ids.copy(),
        "source": naming_metadata.get("source") or metadata.get("source") or "metadata",
        "naming_metadata": request.get("naming_metadata"),
        "metadata": request.get("metadata"),
        "aliases": [],
        "cover_url": naming_metadata.get("cover_url") or metadata.get("cover_url") or "",
        "cover_source": naming_metadata.get("cover_source") or metadata.get("cover_source") or (
            "douban" if (naming_metadata.get("source") or metadata.get("source")) == "douban" else ""
        ),
        "season_number": metadata.get("season_number"),
        "episode_number": metadata.get("episode_number"),
    }


def _intent_override_from_request(request: dict) -> dict:
    metadata = request.get("metadata") if isinstance(request, dict) else {}
    if not isinstance(metadata, dict):
        return {}

    override = {}
    media_type = metadata.get("media_type") or (request.get("naming_metadata") or {}).get("media_type")
    if media_type in {"movie", "series"}:
        override["media_type"] = media_type

    scope = metadata.get("selected_scope") or metadata.get("scope")
    if scope in {"whole_series", "season", "episode"}:
        override["scope"] = scope
    if metadata.get("normalized_title") or metadata.get("english_title") or metadata.get("chinese_title"):
        override["title"] = metadata.get("normalized_title") or metadata.get("english_title") or metadata.get("chinese_title")
    if metadata.get("year"):
        override["year"] = metadata.get("year")
    if metadata.get("season_number") is not None:
        override["season_number"] = metadata.get("season_number")
    if metadata.get("episode_number") is not None:
        override["episode_number"] = metadata.get("episode_number")
    return override


def _entry_external_id(entry: dict, key: str) -> str:
    external_ids = entry.get("external_ids") if isinstance(entry.get("external_ids"), dict) else {}
    return str(external_ids.get(key) or entry.get(f"{key}_id") or entry.get(f"{key}_series_id") or "").strip()


def _episode_key_from_item(episode: dict):
    try:
        return int(episode.get("season_number")), int(episode.get("episode_number"))
    except (TypeError, ValueError):
        return None


def _episode_air_date_value(episode: dict) -> str:
    return str(
        episode.get("aired")
        or episode.get("first_aired")
        or episode.get("firstAired")
        or ""
    ).strip()


def _requested_scope_text(intent: dict) -> str:
    scope = (intent or {}).get("scope") or ""
    if scope == "episode":
        return f"S{int((intent or {}).get('season_number') or 0):02d}E{int((intent or {}).get('episode_number') or 0):02d}"
    if scope == "season":
        return f"S{int((intent or {}).get('season_number') or 0):02d}"
    return "请求范围"


def _blocked_tvdb_resolution(status: str, message: str) -> dict:
    return {"status": status, "message": message}


def _candidate_block_reason(entries: list[dict], intent: dict, episodes_by_series: dict) -> dict:
    scope = (intent or {}).get("scope") or ""
    if scope not in {"episode", "season"}:
        return _blocked_tvdb_resolution(
            "blocked_unreleased",
            "未找到可确认的可搜索剧集范围，已停止搜索。",
        )

    requested_scope = _requested_scope_text(intent)
    has_series_entry = False
    for entry in entries or []:
        series_id = _entry_external_id(entry, "tvdb")
        if not series_id:
            continue
        has_series_entry = True
        episodes = episodes_by_series.get(series_id)
        if episodes is None:
            return _blocked_tvdb_resolution(
                "blocked_tvdb_unavailable",
                f"已识别剧集，但 TVDB 剧集列表暂时不可用，无法确认 {requested_scope} 是否可搜索。请稍后重试，或提供更明确的豆瓣/IMDb/TVDB 链接。",
            )

        episodes = episodes or []
        if scope == "episode":
            requested = (int((intent or {}).get("season_number") or 0), int((intent or {}).get("episode_number") or 0))
            episode = next((item for item in episodes if _episode_key_from_item(item) == requested), None)
            if episode:
                if not _episode_air_date_value(episode):
                    return _blocked_tvdb_resolution(
                        "blocked_air_date_unknown",
                        f"已识别剧集和 {requested_scope}，但 TVDB 缺少播出日期，无法确认是否已播出，已停止自动搜索以避免误投。",
                    )
                if is_unreleased_episode(episode):
                    return _blocked_tvdb_resolution(
                        "blocked_unreleased",
                        f"TVDB 显示 {requested_scope} 尚未播出，已停止搜索。",
                    )
            elif episodes:
                return _blocked_tvdb_resolution(
                    "blocked_episode_missing",
                    f"已识别剧集，但 TVDB 未找到 {requested_scope}，可能尚未收录或输入范围有误。",
                )

        if scope == "season":
            requested_season = int((intent or {}).get("season_number") or 0)
            season_episodes = [
                item for item in episodes if _episode_key_from_item(item) and _episode_key_from_item(item)[0] == requested_season
            ]
            if season_episodes and any(not _episode_air_date_value(item) for item in season_episodes):
                return _blocked_tvdb_resolution(
                    "blocked_air_date_unknown",
                    f"已识别剧集和 {requested_scope}，但 TVDB 存在缺少播出日期的剧集，无法完整确认该季可搜索范围。",
                )
            if season_episodes:
                return _blocked_tvdb_resolution(
                    "blocked_unreleased",
                    f"TVDB 显示 {requested_scope} 暂无已播出剧集，已停止搜索。",
                )
            if episodes:
                return _blocked_tvdb_resolution(
                    "blocked_episode_missing",
                    f"已识别剧集，但 TVDB 未找到 {requested_scope}，可能尚未收录或输入范围有误。",
                )

    if has_series_entry:
        return _blocked_tvdb_resolution(
            "blocked_episode_missing",
            f"已识别剧集，但无法在 TVDB 确认 {requested_scope}，已停止自动搜索。",
        )
    return _blocked_tvdb_resolution(
        "blocked_no_verified_match",
        "未匹配到明确的影视条目，请提供豆瓣/TVDB/IMDb/TMDB 链接或更明确的关键词。",
    )


def _entry_from_tvdb_series(item: dict) -> dict | None:
    if not item or not item.get("tvdb_series_id"):
        return None
    english_title = item.get("english_title") or item.get("name") or ""
    return {
        "media_type": "series",
        "scope": "whole_series",
        "title": english_title,
        "english_title": english_title,
        "chinese_title": "",
        "year": item.get("year") or "",
        "external_ids": {"tvdb": str(item.get("tvdb_series_id"))},
        "tvdb_id": str(item.get("tvdb_id") or item.get("tvdb_series_id")),
        "tvdb_series_id": str(item.get("tvdb_series_id")),
        "aliases": list(item.get("aliases") or []),
        "cover_url": item.get("cover_url") or "",
        "cover_source": "tvdb" if item.get("cover_url") else "",
        "source": "tvdb",
        "metadata": build_external_metadata(
            source="tvdb",
            title=english_title,
            year=item.get("year") or "",
            external_id=str(item.get("tvdb_series_id")),
            media_type="series",
        ),
    }


def _entry_from_tvdb_movie(item: dict) -> dict | None:
    if not item or not item.get("tvdb_movie_id"):
        return None
    english_title = item.get("english_title") or item.get("name") or ""
    return {
        "media_type": "movie",
        "scope": "movie",
        "title": english_title,
        "english_title": english_title,
        "chinese_title": "",
        "year": item.get("year") or "",
        "external_ids": {"tvdb": str(item.get("tvdb_movie_id"))},
        "tvdb_id": str(item.get("tvdb_id") or item.get("tvdb_movie_id")),
        "tvdb_movie_id": str(item.get("tvdb_movie_id")),
        "aliases": list(item.get("aliases") or []),
        "cover_url": item.get("cover_url") or "",
        "cover_source": "tvdb" if item.get("cover_url") else "",
        "source": "tvdb",
        "metadata": build_external_metadata(
            source="tvdb",
            title=english_title,
            year=item.get("year") or "",
            external_id=str(item.get("tvdb_movie_id")),
            media_type="movie",
        ),
    }


def _intent_from_ai_lookup_candidate(item: dict, fallback: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = _collapse_title_spaces(item.get("title") or "")
    query = _collapse_title_spaces(item.get("query") or "")
    parsed = parse_search_intent(query or title)
    scope = item.get("scope") or parsed.get("scope") or fallback.get("scope") or "movie_or_series"
    intent = {
        "raw_query": fallback.get("raw_query") or "",
        "title": title or parsed.get("title") or query,
        "scope": scope,
        "season_number": item.get("season_number") if item.get("season_number") is not None else parsed.get("season_number"),
        "episode_number": item.get("episode_number") if item.get("episode_number") is not None else parsed.get("episode_number"),
        "year": _collapse_title_spaces(item.get("year") or parsed.get("year") or fallback.get("year") or ""),
    }
    return intent if intent["title"] else None


async def _resolve_entries_with_primary_sources(raw_query: str, base_intent: dict) -> tuple[list[dict], dict, dict]:
    entries = []
    episodes_by_series = {}
    intent = base_intent.copy()

    request = await _resolve_search_request(raw_query, allow_ai_fallback=False)
    entry = _entry_from_request(request) if request else None
    if entry:
        entries.append(entry)
        if is_supported_metadata_url(raw_query):
            intent.update(_intent_override_from_request(request))
            _log_info(
                f"主来源意图采用显式链接约束 query={raw_query} "
                f"media_type={intent.get('media_type') or ''} year={intent.get('year') or ''}"
            )
        else:
            _log_info(
                f"主来源意图保留普通标题歧义 query={raw_query} "
                f"douban_media_type={entry.get('media_type') or ''} douban_year={entry.get('year') or ''}"
            )

    tvdb_entries, tvdb_episodes = await asyncio.to_thread(_lookup_tvdb_entries, intent)
    entries.extend(tvdb_entries)
    episodes_by_series.update(tvdb_episodes)
    return merge_primary_entries(entries), episodes_by_series, intent


async def _resolve_entries_with_ai_fallback(raw_query: str, base_intent: dict) -> tuple[list[dict], dict, dict]:
    normalized = await asyncio.to_thread(normalize_search_query_with_ai, raw_query)
    if normalized:
        _log_info(
            f"AI搜索清洗兜底 raw={raw_query} "
            f"status={normalized.get('status')} candidates={len(normalized.get('lookup_candidates') or [])}"
        )
    else:
        _log_info(f"AI搜索清洗兜底无结果 raw={raw_query}")

    seen = set()
    for item in (normalized or {}).get("lookup_candidates") or []:
        intent = _intent_from_ai_lookup_candidate(item, base_intent)
        if not intent:
            continue
        candidate_query = _metadata_lookup_query_from_ai_candidate(item) or intent.get("title") or item.get("query") or ""
        key = (
            candidate_query,
            intent.get("title"),
            intent.get("scope"),
            intent.get("season_number"),
            intent.get("episode_number"),
        )
        if key in seen:
            continue
        seen.add(key)
        entries, episodes_by_series, resolved_intent = await _resolve_entries_with_primary_sources(candidate_query, intent)
        if entries:
            return entries, episodes_by_series, resolved_intent

    verified = await asyncio.to_thread(infer_verified_search_match_with_ai, raw_query)
    if verified:
        _log_info(
            f"AI验证兜底 raw={raw_query} status={verified.get('status')} "
            f"candidates={len(verified.get('candidates') or [])}"
        )
    for item in (verified or {}).get("candidates") or []:
        intent = _intent_from_ai_lookup_candidate(item, base_intent)
        if not intent:
            continue
        candidate_query = _clean_prowlarr_query(f"{item.get('title') or intent.get('title')} {item.get('year') or intent.get('year') or ''}".strip())
        entries, episodes_by_series, resolved_intent = await _resolve_entries_with_primary_sources(candidate_query, intent)
        if entries:
            return entries, episodes_by_series, resolved_intent
    return [], {}, base_intent


def _lookup_tvdb_entries(intent: dict) -> tuple[list[dict], dict]:
    title = intent.get("title") or intent.get("raw_query") or ""
    if not title:
        return [], {}
    _log_info(
        f"TVDB条目回查开始 query={title} scope={intent.get('scope') or ''} "
        f"season={intent.get('season_number') or ''} episode={intent.get('episode_number') or ''}"
    )
    entries = []
    episodes_by_series = {}
    requested_type = intent.get("media_type")
    scope = intent.get("scope") or ""
    lookup_types = [requested_type] if requested_type in {"movie", "series"} else ["movie", "series"]
    if scope in {"whole_series", "season", "episode"}:
        lookup_types = ["series"]

    for lookup_type in lookup_types:
        try:
            if lookup_type == "movie":
                items = search_tvdb_movies(title, year=intent.get("year") or "")
            else:
                items = search_tvdb_series(title, year=intent.get("year") or "")
        except (TvdbConfigError, TvdbRequestError) as e:
            _log_warn(f"TVDB 搜索跳过 type={lookup_type} query={title}: {e}")
            continue

        for item in items[:5]:
            entry = _entry_from_tvdb_movie(item) if lookup_type == "movie" else _entry_from_tvdb_series(item)
            if not entry:
                continue
            entries.append(entry)
            if lookup_type != "series":
                continue
            series_id = entry["external_ids"]["tvdb"]
            try:
                episodes_by_series[series_id] = get_tvdb_series_episodes(series_id)
            except (TvdbConfigError, TvdbRequestError) as e:
                _log_warn(f"TVDB 剧集列表读取失败 series_id={series_id}: {e}")
                episodes_by_series[series_id] = None
    _log_info(f"TVDB条目回查完成 query={title} entries={len(entries)}")
    return entries, episodes_by_series


async def _resolve_entry_candidates(raw_query: str) -> dict:
    intent = parse_search_intent(raw_query)
    entries, episodes_by_series, intent = await _resolve_entries_with_primary_sources(raw_query, intent)
    if not entries:
        entries, episodes_by_series, intent = await _resolve_entries_with_ai_fallback(raw_query, intent)

    for item in entries:
        if item.get("media_type") != "series":
            continue
        series_id = _entry_external_id(item, "tvdb")
        if not series_id or series_id in episodes_by_series:
            continue
        try:
            episodes_by_series[series_id] = await asyncio.to_thread(get_tvdb_series_episodes, series_id)
        except (TvdbConfigError, TvdbRequestError) as e:
            _log_warn(f"AI/外部ID TVDB 剧集列表读取失败 series_id={series_id}: {e}")
            episodes_by_series[series_id] = None

    seen = set()
    unique_entries = []
    for item in entries:
        external_ids = item.get("external_ids") if isinstance(item.get("external_ids"), dict) else {}
        key = tuple(sorted(external_ids.items())) or (item.get("media_type"), item.get("title"), item.get("year"))
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(item)

    if not unique_entries:
        return {
            "status": "blocked_no_verified_match",
            "message": "未匹配到明确的影视条目，请提供豆瓣/TVDB/IMDb/TMDB 链接或更明确的关键词。",
        }

    candidates = build_confirmation_candidates(unique_entries, intent, episodes_by_series)
    if not candidates:
        return _candidate_block_reason(unique_entries, intent, episodes_by_series)
    candidates = await _backfill_candidate_covers(candidates)

    is_link = is_supported_metadata_url(raw_query)
    if is_link and len(candidates) == 1 and candidates[0].get("media_type") == "movie":
        return {
            "status": "auto_confirm",
            "message": f"已识别电影：{_candidate_display_title(candidates[0])}",
            "candidate": candidates[0],
        }

    return {"status": "needs_confirmation", "candidates": candidates}


async def _start_entry_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_query: str):
    plan_id = uuid.uuid4().hex[:10]
    providers = {
        "wikipedia": _wikipedia_plan_provider,
        "douban": _douban_plan_provider,
        "tvdb": _tvdb_plan_provider,
    }
    try:
        plan = await build_confirmable_search_plan(
            raw_query,
            plan_id,
            providers,
            _occupied_special_numbers,
            temporary_special_allocator,
        )
    except SearchPlanningError as exc:
        await update.message.reply_text(f"❌ 无法生成媒体元数据：{exc}")
        return ConversationHandler.END

    selected_path = _resolve_plan_selected_path(plan)
    if not selected_path:
        temporary_special_allocator.release(plan_id)
        await update.message.reply_text("❌ 媒体元数据无法对应到已配置的分类目录。")
        return ConversationHandler.END

    pending_entry_confirmations[plan_id] = {
        "created_at": time.time(),
        "user_id": update.effective_user.id,
        "plan": plan,
        "selected_path": selected_path,
    }
    await update.message.reply_text(
        _build_media_metadata_text(plan),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "确认并搜索", callback_data=f"plan_confirm:{plan_id}"
                    ),
                    InlineKeyboardButton(
                        "取消", callback_data=f"plan_cancel:{plan_id}"
                    ),
                ]
            ]
        ),
        disable_web_page_preview=True,
    )
    return SEARCH_CONFIRM_MEDIA_METADATA


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    raw_query = _extract_command_query(update, context)
    if not raw_query:
        await update.message.reply_text("请输入搜索内容：/search 片名 或 /s 片名，也可以发送豆瓣/IMDb/TVDB 链接。")
        return ConversationHandler.END

    return await _start_entry_resolution(update, context, raw_query)


async def search_metadata_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    raw_query = (update.message.text or "").strip()
    return await _start_entry_resolution(update, context, raw_query)


async def confirm_media_metadata_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    callback = update.callback_query
    await callback.answer()
    try:
        action, plan_id = (callback.data or "").split(":", 1)
    except ValueError:
        await callback.edit_message_text("⚠️ 媒体元数据确认请求无效，请重新搜索。")
        return ConversationHandler.END

    task = get_pending_entry_confirmation(plan_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await callback.edit_message_text("⚠️ 媒体元数据已过期，请重新搜索。")
        return ConversationHandler.END
    if action == "plan_cancel":
        pending_entry_confirmations.pop(plan_id, None)
        temporary_special_allocator.release(plan_id)
        await callback.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    search_plan = task["plan"]
    contract = confirm_media_metadata(search_plan)
    identity = contract["identity"]
    metadata = attach_media_metadata({"source": "confirmed"}, contract)
    pending_entry_confirmations.pop(plan_id, None)
    await callback.edit_message_text(
        f"✅ 已确认媒体元数据：{identity.get('chinese_title') or identity.get('english_title') or ''}"
    )
    return await _send_search_results(
        update,
        context,
        (search_plan.get("prowlarr_queries") or [""])[0],
        naming_metadata={
            "source": "confirmed",
            "media_type": contract["placement"]["library_type"],
            "chinese_title": identity.get("chinese_title") or "",
            "english_title": identity.get("english_title") or "",
            "year": identity.get("year") or "",
        },
        metadata=metadata,
        search_plan=search_plan,
        selected_path=task["selected_path"],
    )


async def unsupported_http_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    await update.message.reply_text("⚠️ 不支持该网页链接，请发送 /magnet 磁力链接，或使用 /search /s 搜索片源。")
    return ConversationHandler.END


async def select_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback = update.callback_query
    await callback.answer()
    data = callback.data or ""
    if data.startswith("search_cancel:"):
        task_id = data.split(":", 1)[1]
        task = pending_search_tasks.pop(task_id, None) or {}
        _release_search_plan(task.get("search_plan"))
        await callback.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    try:
        _, task_id, index_text = data.split(":", 2)
    except ValueError:
        await callback.edit_message_text("⚠️ 搜索选择请求无效，请重新搜索。")
        return ConversationHandler.END
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await callback.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
        return ConversationHandler.END

    try:
        selected_item = task["results"][int(index_text)]
    except (IndexError, ValueError):
        await callback.edit_message_text("⚠️ 候选资源不可用，请重新搜索。")
        return ConversationHandler.END

    if not (selected_item.get("magnet_url") or selected_item.get("download_url")):
        await callback.edit_message_text("⚠️ 该候选缺少可用下载链接，请选择其他结果。")
        return ConversationHandler.END

    context.user_data["search_task_id"] = task_id
    context.user_data["search_selected_item"] = selected_item
    await callback.edit_message_text("⏳ 正在解析下载链接，请稍候。")
    try:
        link = await _resolve_selected_link(context)
    except ProwlarrRequestError as e:
        pending_search_tasks.pop(task_id, None)
        _release_search_plan(task.get("search_plan"))
        await callback.edit_message_text(f"❌ {e}")
        return ConversationHandler.END

    naming_metadata = _naming_metadata_for_selected_release(task, selected_item)
    search_plan = task.get("search_plan") or {}
    metadata = (
        deepcopy(task.get("metadata"))
        if isinstance(task.get("metadata"), dict)
        else None
    )
    if extract_confirmed_media_metadata(metadata) is None:
        pending_search_tasks.pop(task_id, None)
        _release_search_plan(search_plan)
        await callback.edit_message_text("❌ 已确认媒体元数据无效，请重新搜索。")
        return ConversationHandler.END
    try:
        _submit_download_request(
            context,
            DownloadRequest(
                link=link,
                selected_path=task["selected_path"],
                user_id=update.effective_user.id,
                naming_metadata=naming_metadata,
                metadata=metadata,
                source="media-search",
            ),
        )
    except DownloadProviderUnavailable as e:
        pending_search_tasks.pop(task_id, None)
        _release_search_plan(search_plan)
        await callback.edit_message_text(f"❌ {e}")
        return ConversationHandler.END

    pending_search_tasks.pop(task_id, None)
    await callback.edit_message_text(
        "✅ 已加入下载队列。\n系统将按已确认媒体元数据处理，请稍后查看结果。"
    )
    return ConversationHandler.END


async def quit_search_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text(text="已取消本次搜索。")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已取消本次搜索。")
    return ConversationHandler.END


def register_search_handlers(application):
    search_handler = ConversationHandler(
        entry_points=[
            CommandHandler("search", search_command),
            CommandHandler("s", search_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(METADATA_URL_PATTERN), search_metadata_link_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(HTTP_URL_PATTERN), unsupported_http_link_command),
        ],
        states={
            SEARCH_CONFIRM_MEDIA_METADATA: [
                CallbackQueryHandler(
                    confirm_media_metadata_callback,
                    pattern=r"^plan_(confirm|cancel):",
                )
            ],
            SEARCH_SELECT_RESULT: [
                CallbackQueryHandler(
                    select_search_result, pattern=r"^search_(pick|cancel):"
                )
            ],
        },
        fallbacks=[CommandHandler("q", quit_search_conversation)],
    )
    application.add_handler(search_handler)
    _log_info("✅ Search处理器已注册，支持 /search /s 搜索和直接发送豆瓣/IMDb/TVDB链接；豆瓣解析使用内建英文/原标题优先策略")

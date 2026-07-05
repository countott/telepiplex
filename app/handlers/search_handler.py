# -*- coding: utf-8 -*-

import asyncio
import re
import time
import uuid
from warnings import filterwarnings

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.warnings import PTBUserWarning

import init
from app.adapters.prowlarr import (
    ProwlarrConfigError,
    ProwlarrRequestError,
    resolve_prowlarr_download_url,
    search_prowlarr,
)
from app.handlers.download_handler import download_executor, download_task
from app.utils.release_score import rank_releases
from app.utils.search_query import (
    extract_douban_subject_id,
    is_supported_metadata_url,
    parse_douban_mobile_title,
    parse_douban_page_title,
    parse_douban_rexxar_title,
    parse_douban_subject_abstract_title,
    parse_media_page_title,
)

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SEARCH_SELECT_RESULT, SEARCH_SELECT_MAIN_CATEGORY, SEARCH_SELECT_SUB_CATEGORY, SEARCH_RESOLVE_METADATA = range(30, 34)
SEARCH_TASK_TTL_SECONDS = 30 * 60
SEARCH_PROGRESS_INTERVAL_SECONDS = 30
TELEGRAM_SEND_TIMEOUT_SECONDS = 30
METADATA_URL_PATTERN = r"(?i)^https?://(?:[^/\s]+\.)*(?:douban\.com|imdb\.com|thetvdb\.com|tvdb\.com)(?::\d+)?/\S+$"

pending_search_tasks = {}


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
    return " ".join(str(title or "").replace("\xa0", " ").split())


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


def _extract_douban_metadata(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    data = payload.get("subject") if isinstance(payload.get("subject"), dict) else payload
    if not isinstance(data, dict):
        return None

    chinese_title = _collapse_title_spaces(data.get("title") or data.get("name"))
    year = _collapse_title_spaces(data.get("release_year") or data.get("year"))
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
        candidate = _collapse_title_spaces(candidate)
        if candidate and _title_contains_latin(candidate):
            english_title = candidate
            break

    if not english_title and chinese_title and _title_contains_latin(chinese_title):
        english_title = chinese_title

    if not chinese_title or not english_title:
        return None

    return {
        "source": "douban",
        "chinese_title": chinese_title,
        "english_title": english_title,
        "year": year,
    }


def _query_from_plex_metadata(metadata: dict) -> str:
    query = metadata.get("english_title") or metadata.get("chinese_title") or ""
    year = metadata.get("year") or ""
    if query and year and year not in query:
        query = f"{query} {year}"
    return _collapse_title_spaces(query)


def _metadata_matches_plain_query(metadata: dict, query: str) -> bool:
    normalized_query = _normalize_match_title(query)
    if not normalized_query:
        return False

    candidates = [
        metadata.get("chinese_title"),
        metadata.get("english_title"),
        _query_from_plex_metadata(metadata),
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
    for subject_id in re.findall(r"https?://movie\.douban\.com/subject/(\d+)/?", str(html_text or "")):
        if subject_id in seen:
            continue
        seen.add(subject_id)
        urls.append(f"https://movie.douban.com/subject/{subject_id}/")
    return urls


def _fetch_douban_metadata_for_plain_query(query: str) -> dict | None:
    query = _collapse_title_spaces(query)
    if not query:
        return None

    response = requests.get(
        "https://www.douban.com/search",
        params={"cat": "1002", "q": query},
        headers=_douban_request_headers("https://www.douban.com/"),
        timeout=10,
    )
    response.raise_for_status()

    for subject_url in _extract_douban_subject_urls(response.text):
        metadata = _fetch_builtin_douban_metadata(subject_url)
        if metadata and _metadata_matches_plain_query(metadata, query):
            _log_info(f"普通片名命中豆瓣元数据 query={query} url={subject_url} metadata={metadata}")
            return metadata

    _log_info(f"普通片名豆瓣反查未命中 query={query}")
    return None


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
        return None

    return task


def build_results_text(query: str, results: list[dict]) -> str:
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
        [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=f"search_main:{task_id}:{category['name']}")]
        for category in init.bot_config.get("category_folder", [])
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


def _build_sub_category_keyboard(task_id: str, category_name: str) -> InlineKeyboardMarkup:
    sub_categories = []
    for category in init.bot_config.get("category_folder", []):
        if category.get("name") == category_name:
            sub_categories = category.get("path_map") or []
            break

    keyboard = [
        [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"search_path:{task_id}:{index}")]
        for index, category in enumerate(sub_categories)
    ]
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


async def _resolve_query(raw_query: str) -> str | None:
    request = await _resolve_search_request(raw_query)
    return request.get("query") if request else None


async def _resolve_search_request(raw_query: str) -> dict | None:
    if not is_supported_metadata_url(raw_query):
        query = _clean_prowlarr_query(raw_query)
        if not query:
            return {"query": "", "plex_metadata": None}

        try:
            metadata = await asyncio.to_thread(_fetch_douban_metadata_for_plain_query, query)
            if metadata:
                return {
                    "query": _clean_prowlarr_query(_query_from_plex_metadata(metadata)),
                    "plex_metadata": metadata,
                }
        except Exception as e:
            _log_warn(f"普通片名豆瓣反查失败，等待用户补充元数据: {e}")

        return {
            "query": query,
            "plex_metadata": None,
            "needs_metadata": True,
        }

    try:
        if _is_douban_url(raw_query):
            metadata = await asyncio.to_thread(_fetch_builtin_douban_metadata, raw_query)
            if metadata:
                query = _clean_prowlarr_query(_query_from_plex_metadata(metadata))
                _log_info(f"豆瓣链接解析为搜索词 raw={raw_query} query={query} metadata={metadata}")
                return {"query": query, "plex_metadata": metadata}

        query = _clean_prowlarr_query(await asyncio.to_thread(_fetch_media_page_title, raw_query))
        _log_info(f"媒体链接解析为搜索词 raw={raw_query} query={query}")
        return {"query": query, "plex_metadata": None}
    except Exception as e:
        _log_warn(f"媒体页面标题解析失败: {e}")
        return None


def _plex_metadata_for_selected_release(task: dict, selected_item: dict):
    metadata = task.get("plex_metadata")
    if not metadata:
        return None

    result = metadata.copy()
    result["release_title"] = selected_item.get("title") or task.get("query") or ""
    return result


def _owner_matches(task: dict, user_id: int) -> bool:
    return task.get("user_id") == user_id


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


async def _search_prowlarr_with_progress(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
    progress_interval: float = SEARCH_PROGRESS_INTERVAL_SECONDS,
):
    search_task = asyncio.create_task(asyncio.to_thread(search_prowlarr, query, "movie"))
    elapsed = 0.0
    while True:
        done, _ = await asyncio.wait({search_task}, timeout=progress_interval)
        if done:
            return search_task.result()

        elapsed += progress_interval
        await _send_search_message(
            context,
            update.effective_chat.id,
            (
                f"⏳ Prowlarr 仍在搜索：{query}\n"
                f"已等待约 {int(elapsed)} 秒。部分索引器需要 Cloudflare 解析，请继续等待。"
            ),
            disable_web_page_preview=True,
        )


async def _send_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, plex_metadata=None):
    await _reply_or_send(update, context, f"🔍 正在搜索片源：{query}")
    _log_info(f"搜索片源开始 query={query}")

    try:
        items = await _search_prowlarr_with_progress(update, context, query)
        results = rank_releases(items, _get_result_limit())
    except ProwlarrConfigError as e:
        await _send_search_message(context, update.effective_chat.id, f"⚠️ {e}")
        return ConversationHandler.END
    except ProwlarrRequestError as e:
        await _send_search_message(context, update.effective_chat.id, f"❌ {e}")
        return ConversationHandler.END
    except Exception as e:
        _log_error(f"搜索处理失败: {e}")
        await _send_search_message(context, update.effective_chat.id, f"❌ 搜索失败：{e}")
        return ConversationHandler.END

    if not results:
        _log_info(f"搜索片源无结果 query={query}")
        await _send_search_message(context, update.effective_chat.id, "⚠️ 未找到可用片源，请调整关键词后重试。")
        return ConversationHandler.END

    _log_info(f"搜索片源完成 query={query} results={len(results)}")
    task_id = uuid.uuid4().hex[:10]
    pending_search_tasks[task_id] = {
        "created_at": time.time(),
        "query": query,
        "results": results,
        "user_id": update.effective_user.id,
        "plex_metadata": plex_metadata,
    }

    await _send_search_message(
        context,
        update.effective_chat.id,
        build_results_text(query, results),
        reply_markup=_build_results_keyboard(task_id, results),
        disable_web_page_preview=True,
    )
    return SEARCH_SELECT_RESULT


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    raw_query = _extract_command_query(update, context)
    if not raw_query:
        await update.message.reply_text("请输入搜索内容：/s 片名，或 /s 豆瓣/IMDb/TVDB 链接。")
        return ConversationHandler.END

    request = await _resolve_search_request(raw_query)
    if not request or not request.get("query"):
        await update.message.reply_text("⚠️ 页面链接解析失败，请改用片名搜索。")
        return ConversationHandler.END

    if request.get("needs_metadata"):
        context.user_data["pending_plain_search_query"] = request["query"]
        await update.message.reply_text(
            "⚠️ 未从豆瓣匹配到准确信息。\n"
            "请直接回复豆瓣链接，或回复中文片名作为保存文件夹名称。\n"
            "发送 /q 取消。"
        )
        return SEARCH_RESOLVE_METADATA

    return await _send_search_results(
        update,
        context,
        request["query"],
        plex_metadata=request.get("plex_metadata"),
    )


async def search_metadata_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    raw_query = (update.message.text or "").strip()
    request = await _resolve_search_request(raw_query)
    if not request or not request.get("query"):
        await update.message.reply_text("⚠️ 页面链接解析失败，请改用片名搜索。")
        return ConversationHandler.END

    return await _send_search_results(
        update,
        context,
        request["query"],
        plex_metadata=request.get("plex_metadata"),
    )


async def resolve_plain_search_metadata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    pending_query = context.user_data.get("pending_plain_search_query")
    if not pending_query:
        await update.message.reply_text("⚠️ 未找到待补充的搜索任务，请重新发送 /s。")
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("请回复豆瓣链接，或直接回复中文片名。")
        return SEARCH_RESOLVE_METADATA

    if is_supported_metadata_url(text):
        request = await _resolve_search_request(text)
        metadata = request.get("plex_metadata") if request else None
        if not metadata:
            await update.message.reply_text("⚠️ 无法从该链接取得豆瓣元数据，请发送豆瓣链接或直接回复中文片名。")
            return SEARCH_RESOLVE_METADATA

        context.user_data.pop("pending_plain_search_query", None)
        return await _send_search_results(
            update,
            context,
            request["query"],
            plex_metadata=metadata,
        )

    if re.match(r"(?i)^https?://", text):
        await update.message.reply_text("⚠️ 该链接暂不支持，请发送豆瓣链接或直接回复中文片名。")
        return SEARCH_RESOLVE_METADATA

    chinese_title = _collapse_title_spaces(text)
    if not chinese_title:
        await update.message.reply_text("请回复豆瓣链接，或直接回复中文片名。")
        return SEARCH_RESOLVE_METADATA

    context.user_data.pop("pending_plain_search_query", None)
    return await _send_search_results(
        update,
        context,
        pending_query,
        plex_metadata={
            "source": "search_query",
            "chinese_title": chinese_title,
        },
    )


async def select_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("search_cancel:"):
        await query.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    _, task_id, index_text = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
        return ConversationHandler.END

    try:
        selected_item = task["results"][int(index_text)]
    except (IndexError, ValueError):
        await query.edit_message_text("⚠️ 候选资源不可用，请重新搜索。")
        return ConversationHandler.END

    link = selected_item.get("magnet_url") or selected_item.get("download_url")
    if not link:
        await query.edit_message_text("⚠️ 该候选缺少可用下载链接，请选择其他结果。")
        return ConversationHandler.END

    context.user_data["search_task_id"] = task_id
    context.user_data["search_selected_item"] = selected_item

    await query.edit_message_text("📁 请选择保存分类：", reply_markup=_build_main_category_keyboard(task_id))
    return SEARCH_SELECT_MAIN_CATEGORY


async def select_search_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("search_cancel:"):
        await query.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    if data.startswith("search_last:"):
        task_id = data.split(":", 1)[1]
        task = get_pending_search_task(task_id)
        if not task or not _owner_matches(task, update.effective_user.id):
            await query.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
            return ConversationHandler.END

        selected_path = init.bot_session.get("movie_last_save") if hasattr(init, "bot_session") else None
        if not selected_path or not _get_selected_link(context):
            await query.edit_message_text("⚠️ 未找到上次保存路径或候选链接，请重新选择。")
            return ConversationHandler.END

        try:
            link = await _resolve_selected_link(context)
        except ProwlarrRequestError as e:
            await query.edit_message_text(f"❌ {e}")
            return ConversationHandler.END

        selected_item = context.user_data.get("search_selected_item") or {}
        plex_metadata = _plex_metadata_for_selected_release(task, selected_item)
        await query.edit_message_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
        download_executor.submit(download_task, link, selected_path, update.effective_user.id, plex_metadata=plex_metadata)
        pending_search_tasks.pop(task_id, None)
        return ConversationHandler.END

    _, task_id, category_name = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
        return ConversationHandler.END

    context.user_data["search_selected_main_category"] = category_name
    await query.edit_message_text("📁 请选择保存目录：", reply_markup=_build_sub_category_keyboard(task_id, category_name))
    return SEARCH_SELECT_SUB_CATEGORY


async def select_search_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("search_cancel:"):
        await query.edit_message_text("已取消本次搜索。")
        return ConversationHandler.END

    _, task_id, index_text = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("⚠️ 搜索任务已过期，请重新发起搜索。")
        return ConversationHandler.END

    category_name = context.user_data.get("search_selected_main_category")
    sub_categories = []
    for category in init.bot_config.get("category_folder", []):
        if category.get("name") == category_name:
            sub_categories = category.get("path_map") or []
            break

    try:
        selected_path = sub_categories[int(index_text)]["path"]
    except (IndexError, KeyError, TypeError, ValueError):
        await query.edit_message_text("⚠️ 保存目录不可用，请重新搜索。")
        return ConversationHandler.END

    if not hasattr(init, "bot_session"):
        init.bot_session = {}
    init.bot_session["movie_last_save"] = selected_path

    if not _get_selected_link(context):
        await query.edit_message_text("⚠️ 候选链接已失效，请重新搜索。")
        return ConversationHandler.END

    try:
        link = await _resolve_selected_link(context)
    except ProwlarrRequestError as e:
        await query.edit_message_text(f"❌ {e}")
        return ConversationHandler.END

    selected_item = context.user_data.get("search_selected_item") or {}
    plex_metadata = _plex_metadata_for_selected_release(task, selected_item)
    await query.edit_message_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
    download_executor.submit(download_task, link, selected_path, update.effective_user.id, plex_metadata=plex_metadata)
    pending_search_tasks.pop(task_id, None)
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
            CommandHandler("s", search_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(METADATA_URL_PATTERN), search_metadata_link_command),
        ],
        states={
            SEARCH_RESOLVE_METADATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, resolve_plain_search_metadata)
            ],
            SEARCH_SELECT_RESULT: [CallbackQueryHandler(select_search_result, pattern=r"^search_(pick|cancel):")],
            SEARCH_SELECT_MAIN_CATEGORY: [
                CallbackQueryHandler(select_search_main_category, pattern=r"^search_(main|last|cancel):")
            ],
            SEARCH_SELECT_SUB_CATEGORY: [
                CallbackQueryHandler(select_search_sub_category, pattern=r"^search_(path|cancel):")
            ],
        },
        fallbacks=[CommandHandler("q", quit_search_conversation)],
    )
    application.add_handler(search_handler)
    _log_info("✅ Search处理器已注册，支持直接发送豆瓣/IMDb/TVDB链接；豆瓣解析使用内建英文/原标题优先策略")

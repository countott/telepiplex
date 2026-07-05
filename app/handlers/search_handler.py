# -*- coding: utf-8 -*-

import asyncio
import time
import uuid
from warnings import filterwarnings

import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler
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

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SEARCH_SELECT_RESULT, SEARCH_SELECT_MAIN_CATEGORY, SEARCH_SELECT_SUB_CATEGORY = range(30, 33)
SEARCH_TASK_TTL_SECONDS = 30 * 60

pending_search_tasks = {}


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
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.string if soup.title and soup.title.string else ""
    title = " ".join(title.split())
    if title.endswith("(豆瓣)"):
        title = title[:-4].strip()
    return title


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


def _fetch_douban_title(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": init.USER_AGENT}, timeout=10)
    response.raise_for_status()
    return parse_douban_title(response.text)


async def _resolve_query(raw_query: str) -> str | None:
    if "douban.com/subject/" not in raw_query:
        return raw_query

    try:
        return await asyncio.to_thread(_fetch_douban_title, raw_query)
    except Exception as e:
        init.logger.warn(f"豆瓣标题解析失败: {e}")
        return None


def _owner_matches(task: dict, user_id: int) -> bool:
    return task.get("user_id") == user_id


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return ConversationHandler.END

    raw_query = _extract_command_query(update, context)
    if not raw_query:
        await update.message.reply_text("请使用 /s 片名，例如：/s The Grand Budapest Hotel 2014")
        return ConversationHandler.END

    query = await _resolve_query(raw_query)
    if not query:
        await update.message.reply_text("豆瓣链接解析失败，请手动输入片名搜索。")
        return ConversationHandler.END

    await update.message.reply_text(f"🔍 正在搜索：{query}")

    try:
        items = await asyncio.to_thread(search_prowlarr, query, "movie")
        results = rank_releases(items, _get_result_limit())
    except ProwlarrConfigError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return ConversationHandler.END
    except ProwlarrRequestError as e:
        await update.message.reply_text(f"❌ {e}")
        return ConversationHandler.END
    except Exception as e:
        init.logger.error(f"搜索处理失败: {e}")
        await update.message.reply_text(f"❌ 搜索处理失败: {e}")
        return ConversationHandler.END

    if not results:
        await update.message.reply_text("没有找到可用结果。")
        return ConversationHandler.END

    task_id = uuid.uuid4().hex[:10]
    pending_search_tasks[task_id] = {
        "created_at": time.time(),
        "query": query,
        "results": results,
        "user_id": user_id,
    }

    await update.message.reply_text(
        build_results_text(query, results),
        reply_markup=_build_results_keyboard(task_id, results),
        disable_web_page_preview=True,
    )
    return SEARCH_SELECT_RESULT


async def select_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("search_cancel:"):
        await query.edit_message_text("🚪用户退出本次会话")
        return ConversationHandler.END

    _, task_id, index_text = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("❌ 搜索任务已过期，请重新搜索。")
        return ConversationHandler.END

    try:
        selected_item = task["results"][int(index_text)]
    except (IndexError, ValueError):
        await query.edit_message_text("❌ 候选资源不可用，请重新搜索。")
        return ConversationHandler.END

    link = selected_item.get("magnet_url") or selected_item.get("download_url")
    if not link:
        await query.edit_message_text("❌ 该候选没有可用 magnet/downloadUrl，请选择其他结果。")
        return ConversationHandler.END

    context.user_data["search_task_id"] = task_id
    context.user_data["search_selected_item"] = selected_item

    await query.edit_message_text("❓请选择要保存到哪个分类：", reply_markup=_build_main_category_keyboard(task_id))
    return SEARCH_SELECT_MAIN_CATEGORY


async def select_search_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("search_cancel:"):
        await query.edit_message_text("🚪用户退出本次会话")
        return ConversationHandler.END

    if data.startswith("search_last:"):
        task_id = data.split(":", 1)[1]
        task = get_pending_search_task(task_id)
        if not task or not _owner_matches(task, update.effective_user.id):
            await query.edit_message_text("❌ 搜索任务已过期，请重新搜索。")
            return ConversationHandler.END

        selected_path = init.bot_session.get("movie_last_save") if hasattr(init, "bot_session") else None
        if not selected_path or not _get_selected_link(context):
            await query.edit_message_text("❌ 未找到最后一次保存路径或候选链接，请重新选择。")
            return ConversationHandler.END

        try:
            link = await _resolve_selected_link(context)
        except ProwlarrRequestError as e:
            await query.edit_message_text(f"❌ {e}")
            return ConversationHandler.END

        await query.edit_message_text("✅ 已为您添加到下载队列！\n请稍后~")
        download_executor.submit(download_task, link, selected_path, update.effective_user.id)
        pending_search_tasks.pop(task_id, None)
        return ConversationHandler.END

    _, task_id, category_name = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("❌ 搜索任务已过期，请重新搜索。")
        return ConversationHandler.END

    context.user_data["search_selected_main_category"] = category_name
    await query.edit_message_text("❓请选择分类保存目录：", reply_markup=_build_sub_category_keyboard(task_id, category_name))
    return SEARCH_SELECT_SUB_CATEGORY


async def select_search_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("search_cancel:"):
        await query.edit_message_text("🚪用户退出本次会话")
        return ConversationHandler.END

    _, task_id, index_text = data.split(":", 2)
    task = get_pending_search_task(task_id)
    if not task or not _owner_matches(task, update.effective_user.id):
        await query.edit_message_text("❌ 搜索任务已过期，请重新搜索。")
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
        await query.edit_message_text("❌ 保存目录不可用，请重新搜索。")
        return ConversationHandler.END

    if not hasattr(init, "bot_session"):
        init.bot_session = {}
    init.bot_session["movie_last_save"] = selected_path

    if not _get_selected_link(context):
        await query.edit_message_text("❌ 候选链接已失效，请重新搜索。")
        return ConversationHandler.END

    try:
        link = await _resolve_selected_link(context)
    except ProwlarrRequestError as e:
        await query.edit_message_text(f"❌ {e}")
        return ConversationHandler.END

    await query.edit_message_text("✅ 已为您添加到下载队列！\n请稍后~")
    download_executor.submit(download_task, link, selected_path, update.effective_user.id)
    pending_search_tasks.pop(task_id, None)
    return ConversationHandler.END


async def quit_search_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
    return ConversationHandler.END


def register_search_handlers(application):
    search_handler = ConversationHandler(
        entry_points=[CommandHandler("s", search_command)],
        states={
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
    init.logger.info("✅ Search处理器已注册")

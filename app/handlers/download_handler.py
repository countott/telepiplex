# -*- coding: utf-8 -*-

import re
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from warnings import filterwarnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.helpers import escape_markdown
from telegram.warnings import PTBUserWarning

import init
from app.utils.directory_config import get_save_directories
from app.utils.log_sanitizer import sanitize_log_value
from app.utils.message_queue import add_task_to_queue


filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SELECT_SUB_CATEGORY = 10
SELECT_TARGET_FOLDER = 11

download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="Movie_Download")
INVALID_FOLDER_CHARS = re.compile(r'[\\/*?"<>|]+')


class DownloadUrlType(Enum):
    ED2K = "ED2K"
    THUNDER = "thunder"
    MAGNET = "magnet"
    UNKNOWN = "unknown"

    def __str__(self):
        return self.value


async def _start_download_link(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str, allowed_types=None):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END
    link = str(link or "").strip()
    context.user_data["link"] = link
    init.logger.info(f"download link: {sanitize_log_value({'link': link})}")
    dl_url_type = is_valid_link(link)
    if dl_url_type == DownloadUrlType.UNKNOWN or (allowed_types and dl_url_type not in allowed_types):
        await update.message.reply_text("⚠️ 下载链接格式不受支持，请检查后重试。")
        return ConversationHandler.END

    context.user_data["dl_url_type"] = dl_url_type
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"save_path:{index}")]
        for index, category in enumerate(get_save_directories())
    ]
    if hasattr(init, "bot_session") and "movie_last_save" in init.bot_session:
        last_save_path = init.bot_session["movie_last_save"]
        keyboard.append([InlineKeyboardButton(f"📁 上次保存: {last_save_path}", callback_data="last_save_path")])
    keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📁 请选择保存目录：",
        reply_markup=reply_markup,
    )
    return SELECT_SUB_CATEGORY


async def magnet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = " ".join(context.args or []).strip()
    if not link:
        await update.message.reply_text("请输入磁力链接：/magnet magnet:?xt=urn:btih:...")
        return ConversationHandler.END
    return await _start_download_link(update, context, link, allowed_types={DownloadUrlType.MAGNET})


async def select_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    query_data = query.data
    if query_data == "cancel":
        return await quit_conversation(update, context)
    if query_data == "last_save_path":
        selected_path = init.bot_session.get("movie_last_save") if hasattr(init, "bot_session") else ""
        if not selected_path:
            await query.edit_message_text("⚠️ 未找到上次保存路径，请重新选择。")
            return ConversationHandler.END
    elif str(query_data).startswith("save_path:"):
        directories = get_save_directories()
        try:
            selected_path = directories[int(str(query_data).split(":", 1)[1])]["path"]
        except (IndexError, KeyError, TypeError, ValueError):
            await query.edit_message_text("⚠️ 保存目录不可用，请重新选择。")
            return ConversationHandler.END
    else:
        selected_path = query_data

    if not hasattr(init, "bot_session"):
        init.bot_session = {}
    init.bot_session["movie_last_save"] = selected_path
    context.user_data["selected_path"] = selected_path

    await query.edit_message_text(
        text=(
            "请输入保存后的文件夹名。\n\n"
            "只会重命名下载完成后的顶层文件夹，不会改内部文件名。\n"
            "发送 - 可保留 115 原始名称。"
        )
    )
    return SELECT_TARGET_FOLDER


async def receive_target_folder_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = context.user_data.get("link")
    selected_path = context.user_data.get("selected_path")
    if not link or not selected_path:
        await update.message.reply_text("⚠️ 下载会话已失效，请重新发送 /magnet。")
        return ConversationHandler.END

    target_folder_name = update.message.text or ""
    user_id = update.effective_user.id
    await update.message.reply_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
    download_executor.submit(download_task, link, selected_path, user_id, target_folder_name)
    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text(text="已取消本次操作。")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已取消本次操作。")
    return ConversationHandler.END


def is_valid_link(link: str) -> DownloadUrlType:
    patterns = {
        DownloadUrlType.MAGNET: r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$',
        DownloadUrlType.ED2K: r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        DownloadUrlType.THUNDER: r'^thunder://[a-zA-Z0-9=]+',
    }
    for url_type, pattern in patterns.items():
        if re.match(pattern, link):
            return url_type
    return DownloadUrlType.UNKNOWN


def sanitize_target_folder_name(name: str) -> str:
    name = str(name or "").strip().strip("`").strip('"').strip("'")
    if not name or name == "-":
        return ""
    name = name.replace("：", ":")
    name = INVALID_FOLDER_CHARS.sub("", name)
    return " ".join(name.split()).strip().strip(".")


def _join_path(parent: str, leaf: str) -> str:
    return f"{str(parent or '').rstrip('/')}/{str(leaf or '').strip('/')}"


def _target_path_exists(path: str) -> bool:
    try:
        return bool(init.openapi_115.get_file_info(path))
    except Exception as e:
        init.logger.warn(f"检查目标目录是否存在失败 path={path}: {e}")
        return False


def _unique_target_leaf(selected_path: str, desired_leaf: str, current_leaf: str) -> str:
    desired_leaf = sanitize_target_folder_name(desired_leaf)
    if not desired_leaf or desired_leaf == current_leaf:
        return current_leaf

    candidate = desired_leaf
    suffix = 2
    while _target_path_exists(_join_path(selected_path, candidate)):
        candidate = f"{desired_leaf} ({suffix})"
        suffix += 1
    return candidate


def _resource_leaf(resource_name: str) -> str:
    return str(resource_name or "").strip("/").split("/")[-1] or "unknown"


def _progress_percent(value) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = 0.0
    return max(0.0, min(percent, 100.0))


def _progress_bar(percent: float) -> str:
    filled = int(_progress_percent(percent) // 5)
    return "█" * filled + "░" * (20 - filled) + f" {_progress_percent(percent):.1f}%"


def _queue_download_failure_notice(user_id, reason, resource_name, progress_percent=0):
    progress_text = _progress_bar(progress_percent)
    add_task_to_queue(
        user_id,
        None,
        message=escape_markdown(
            f"⚠️ {reason}：{resource_name}\n"
            f"进度：{progress_text}",
            version=2,
        ),
    )


def _rename_top_folder(selected_path: str, current_leaf: str, target_folder_name: str):
    target_leaf = _unique_target_leaf(selected_path, target_folder_name, current_leaf)
    current_path = _join_path(selected_path, current_leaf)
    if target_leaf == current_leaf:
        return current_path, False, ""

    if init.openapi_115.rename(current_path, target_leaf):
        return _join_path(selected_path, target_leaf), True, ""
    return current_path, False, f"重命名失败，已保留 115 原始目录：{current_leaf}"


def download_task(link, selected_path, user_id, target_folder_name=None):
    info_hash = ""
    if init.openapi_115 is None:
        add_task_to_queue(
            user_id,
            None,
            message="❌ 115 OpenAPI 尚未初始化，暂时无法投递离线任务。请检查 Token 或使用 `/auth` 重新授权。",
        )
        return

    try:
        offline_success = init.openapi_115.offline_download_specify_path(link, selected_path)
        if not offline_success:
            _queue_download_failure_notice(user_id, "115 离线任务创建失败", _resource_leaf(link), 0)
            return

        check_result = init.openapi_115.check_offline_download_success(link)
        download_success, resource_name, info_hash = check_result[:3]
        progress_percent = check_result[3] if len(check_result) > 3 else 0

        if not download_success:
            init.logger.warn(f"❌ {resource_name} 离线下载未完成")
            _queue_download_failure_notice(user_id, "115 离线下载未完成", resource_name, progress_percent)
            return

        init.logger.info(f"✅ {resource_name} 离线下载成功！")
        time.sleep(1)

        final_leaf = resource_name
        final_path = _join_path(selected_path, final_leaf)
        if init.openapi_115.is_directory(final_path):
            init.openapi_115.auto_clean_all(final_path)
        else:
            final_leaf = Path(resource_name).stem
            init.openapi_115.create_dir_for_file(selected_path, final_leaf)
            init.openapi_115.move_file(final_path, _join_path(selected_path, final_leaf))
            final_path = _join_path(selected_path, final_leaf)

        final_path, renamed, rename_warning = _rename_top_folder(selected_path, final_leaf, target_folder_name)
        if rename_warning:
            message = f"⚠️ 离线下载完成，但{rename_warning}\n\n保存目录：`{final_path}`"
        else:
            action = "已重命名并保存" if renamed else "已保存"
            message = f"✅ 离线下载完成，{action}。\n\n保存目录：`{final_path}`"
        add_task_to_queue(user_id, None, message=message)
    except Exception as e:
        init.logger.error(f"下载任务执行失败: {str(e)}")
        add_task_to_queue(
            user_id,
            None,
            message=f"❌ 下载任务执行失败：{escape_markdown(str(e), version=2)}",
        )
    finally:
        if init.openapi_115 is not None and info_hash:
            init.openapi_115.del_offline_task(info_hash, del_source_file=0)


def register_download_handlers(application):
    download_command_handler = ConversationHandler(
        entry_points=[
            CommandHandler("magnet", magnet_command),
            CommandHandler("m", magnet_command),
        ],
        states={
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)],
            SELECT_TARGET_FOLDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_target_folder_name)],
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_command_handler)
    init.logger.info("✅ Downloader处理器已注册")

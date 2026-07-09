# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
from telegram.helpers import escape_markdown
import init
import re
import time
import uuid
from pathlib import Path
from app.utils.message_queue import add_task_to_queue
import requests
from enum import Enum
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
from app.utils.sqlitelib import *
from concurrent.futures import ThreadPoolExecutor
from app.adapters.tvdb import TvdbConfigError, TvdbRequestError, get_tvdb_series_episodes, search_tvdb_series
from app.utils.ai import infer_tvdb_episode_plan_with_ai
from app.utils.directory_config import get_plex_library_id_for_path, get_save_directories
from app.utils.log_sanitizer import sanitize_log_value
from app.utils.media_naming import build_media_naming_plan, infer_english_title_from_release
from app.utils.tvdb_rename import VIDEO_EXTENSIONS, build_tvdb_rename_plan

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SELECT_SUB_CATEGORY = 10

# 全局线程池，用于处理下载任务
download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="Movie_Download")
PARTIAL_RETRY_PROGRESS_THRESHOLD = 20

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
    # 检查链接格式是否正确
    if dl_url_type == DownloadUrlType.UNKNOWN or (allowed_types and dl_url_type not in allowed_types):
        await update.message.reply_text("⚠️ 下载链接格式不受支持，请检查后重试。")
        return ConversationHandler.END
    # 保存下载类型到context.user_data
    context.user_data["dl_url_type"] = dl_url_type
    # 显示保存目录
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"save_path:{index}")]
        for index, category in enumerate(get_save_directories())
    ]
    # 只在有最后保存路径时才显示该选项
    if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
        last_save_path = init.bot_session['movie_last_save']
        keyboard.append([InlineKeyboardButton(f"📁 上次保存: {last_save_path}", callback_data="last_save_path")])
    keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="📁 请选择保存目录：",
                                   reply_markup=reply_markup)
    return SELECT_SUB_CATEGORY


async def start_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _start_download_link(update, context, update.message.text.strip())


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

    # 保存最后一次选择路径
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    init.bot_session['movie_last_save'] = selected_path

    link = context.user_data["link"]
    user_id = update.effective_user.id
    
    await query.edit_message_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
    
    # 使用全局线程池异步执行下载任务
    download_executor.submit(download_task, link, selected_path, user_id)
    return ConversationHandler.END


async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="已取消本次操作。")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已取消本次操作。")
    return ConversationHandler.END


def is_valid_link(link: str) -> DownloadUrlType:    
    # 定义链接模式字典
    patterns = {
        DownloadUrlType.MAGNET: r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$',
        DownloadUrlType.ED2K: r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        DownloadUrlType.THUNDER: r'^thunder://[a-zA-Z0-9=]+',
    }
    
    # 检查基本链接类型
    for url_type, pattern in patterns.items():
        if re.match(pattern, link):
            return url_type
        
    return DownloadUrlType.UNKNOWN


def _media_config():
    return init.bot_config.get("media") or {}


def _configured(value, placeholder=""):
    value = str(value or "").strip()
    if not value:
        return False
    if placeholder and value.lower() == placeholder.lower():
        return False
    return not value.lower().startswith("your_")


def _plex_config():
    media = _media_config()
    return media.get("plex") or {}


def _get_unorganized_path():
    media = _media_config()
    return str(media.get("unorganized_path") or "/未整理").rstrip("/") or "/未整理"


def _has_plex_credentials():
    plex_config = _plex_config()
    return (
        _configured(plex_config.get("base_url"))
        and _configured(plex_config.get("token"))
    )


def notice_plex_scan_library(path, library_id=None):
    if not _has_plex_credentials():
        return None
    plex_config = _plex_config()
    base_url = str(plex_config.get("base_url") or "").rstrip("/")
    library_id = str(library_id or get_plex_library_id_for_path(path) or "all").strip()
    token = str(plex_config.get("token") or "").strip()
    url = f"{base_url}/library/sections/{library_id}/refresh"
    response = requests.get(url, params={"X-Plex-Token": token}, timeout=15)
    response.raise_for_status()
    init.logger.info(f"通知 Plex 扫库成功 library_id={library_id} path={path}")
    return True


def _pending_plex_scans():
    if not hasattr(init, "pending_plex_scans"):
        init.pending_plex_scans = {}
    return init.pending_plex_scans


def _folder_display_name(path):
    return str(path or "").rstrip("/").split("/")[-1] or str(path or "").strip("/") or "媒体库"


def _plex_scan_button_text(path, library_id):
    if str(library_id or "").strip() == "all":
        return "扫描 Plex 全部资料库"
    folder_name = _folder_display_name(path)
    return f"扫描 Plex {folder_name} 资料库"


def _build_plex_scan_keyboard(scan_id, path, library_id):
    button_text = _plex_scan_button_text(path, library_id)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button_text, callback_data=f"plex_scan_confirm:{scan_id}")],
            [InlineKeyboardButton("跳过", callback_data=f"plex_scan_skip:{scan_id}")],
        ]
    )


def queue_plex_scan_confirmation(path):
    if not _has_plex_credentials():
        return None
    user_id = init.bot_config.get("allowed_user")
    if not user_id:
        init.logger.warn("Plex 扫库待确认，但 allowed_user 未配置，无法发送确认消息")
        return None

    library_id = get_plex_library_id_for_path(path)
    if not _configured(library_id):
        library_id = "all"

    scan_id = uuid.uuid4().hex[:10]
    _pending_plex_scans()[scan_id] = {
        "path": path,
        "library_id": str(library_id),
        "created_at": time.time(),
    }

    escaped_path = escape_markdown(str(path), version=2)
    button_text = _plex_scan_button_text(path, library_id)
    library_line = ""
    if str(library_id) != "all":
        library_line = f"Library ID：`{escape_markdown(str(library_id), version=2)}`\n\n"
    queued = add_task_to_queue(
        user_id,
        None,
        message=(
            f"📚 {escape_markdown(button_text, version=2)}\n\n"
            f"整理目录：`{escaped_path}`\n\n"
            f"{library_line}"
            f"点击“{escape_markdown(button_text, version=2)}”后会刷新 Plex 媒体库。"
        ),
        keyboard=_build_plex_scan_keyboard(scan_id, path, library_id),
    )
    if not queued:
        _pending_plex_scans().pop(scan_id, None)
        init.logger.warn(f"Plex 扫库确认消息入队失败，已清理待确认任务 path={path}")
        return None
    return scan_id


async def handle_plex_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return

    try:
        action, scan_id = query.data.split(":", 1)
    except (AttributeError, ValueError):
        await query.edit_message_text("⚠️ Plex 扫库请求无效。")
        return

    pending_scans = _pending_plex_scans()
    scan = pending_scans.get(scan_id)
    if not scan:
        await query.edit_message_text("⚠️ Plex 扫库请求已过期。")
        return

    if action == "plex_scan_skip":
        pending_scans.pop(scan_id, None)
        await query.edit_message_text("已跳过 Plex 扫库。")
        return

    try:
        notice_plex_scan_library(scan["path"], library_id=scan.get("library_id"))
    except Exception as e:
        init.logger.error(f"Plex 扫库触发失败: {e}")
        await query.edit_message_text(f"❌ Plex 扫库触发失败：{e}")
        return

    pending_scans.pop(scan_id, None)
    await query.edit_message_text("✅ 已触发 Plex 媒体库刷新。")


def handle_media_library_update(path, file_list=None):
    if _has_plex_credentials() and queue_plex_scan_confirmation(path):
        return "plex_pending"

    init.logger.info(f"未配置 Plex 扫库，跳过媒体库通知 path={path}")
    return None


def save_failed_download_to_db(
    title,
    magnet,
    save_path,
    *,
    progress_percent=0,
    retry_category="partial",
    last_error="",
):
    """保存失败的下载任务到数据库"""
    try:
        with SqlLiteLib() as sqlite:
            # 检查是否已存在相同的任务
            check_sql = "SELECT * FROM offline_task WHERE magnet = ? AND save_path = ? AND title = ?"
            existing = sqlite.query_one(check_sql, (magnet, save_path, title))
            
            if not existing:
                sql = (
                    "INSERT INTO offline_task "
                    "(title, magnet, save_path, progress_percent, retry_category, last_error) "
                    "VALUES (?, ?, ?, ?, ?, ?)"
                )
                sqlite.execute_sql(
                    sql,
                    (title, magnet, save_path, float(progress_percent or 0), retry_category, last_error),
                )
                init.logger.info(f"[{title}]已添加到重试列表")
    except Exception as e:
        raise RuntimeError(f"保存重试任务失败: {e}") from e
    

def _list_response_items(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            return data["list"]
        if isinstance(data, list):
            return data
        if isinstance(response.get("list"), list):
            return response["list"]
    return []


def _file_name_from_115_item(item):
    return str(item.get("fn") or item.get("n") or item.get("file_name") or item.get("name") or "").strip()


def _file_id_from_115_item(item):
    return str(item.get("fid") or item.get("cid") or item.get("file_id") or item.get("id") or "").strip()


def _is_dir_115_item(item):
    if "is_dir" in item:
        return bool(item.get("is_dir"))
    if "file_category" in item:
        return str(item.get("file_category")) == "0"
    if "fc" in item:
        return str(item.get("fc")) != "1"
    return False


def collect_115_file_tree(openapi, root_path, max_depth=4, limit=1000):
    root_info = openapi.get_file_info(root_path)
    if not root_info:
        init.logger.warn(f"TVDB整理跳过：无法读取115目录 {root_path}")
        return []

    root_id = str(root_info.get("file_id") or root_info.get("cid") or root_info.get("fid") or "").strip()
    if not root_id:
        init.logger.warn(f"TVDB整理跳过：115目录缺少ID {root_path}")
        return []

    tree = []

    def walk(parent_id, prefix="", depth=0):
        if depth > max_depth:
            return
        items = _list_response_items(openapi.get_file_list({"cid": parent_id, "limit": limit, "show_dir": 1}))
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _file_name_from_115_item(item)
            if not name:
                continue
            relative_path = f"{prefix}/{name}".strip("/")
            is_dir = _is_dir_115_item(item)
            node = {
                "name": name,
                "relative_path": relative_path,
                "is_dir": is_dir,
                "file_id": _file_id_from_115_item(item),
                "size": item.get("fs") or item.get("size") or item.get("size_byte") or 0,
            }
            if is_dir:
                tree.append(node)
                child_id = node["file_id"]
                if child_id:
                    walk(child_id, relative_path, depth + 1)
            elif Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                tree.append(node)

    walk(root_id)
    return tree


def _tvdb_title_from_metadata(metadata):
    metadata = metadata or {}
    title = metadata.get("english_title") or metadata.get("query") or ""
    year = str(metadata.get("year") or "").strip()
    if title and year and title.endswith(f" {year}"):
        title = title[: -len(year)].strip()
    return " ".join(str(title or "").split())


def _get_tvdb_candidates_and_episodes(metadata):
    title = _tvdb_title_from_metadata(metadata)
    if not title:
        init.logger.warn(f"TVDB整理跳过：元数据缺少英文标题 {metadata}")
        return [], []

    try:
        candidates = search_tvdb_series(title, year=str((metadata or {}).get("year") or "").strip())[:3]
    except TvdbConfigError as e:
        init.logger.info(f"TVDB整理跳过：{e}")
        return [], []
    except TvdbRequestError as e:
        init.logger.warn(f"TVDB搜索失败，跳过TVDB整理: {e}")
        return [], []

    episodes = []
    for candidate in candidates:
        series_id = str(candidate.get("tvdb_series_id") or "").strip()
        if not series_id:
            continue
        try:
            series_episodes = get_tvdb_series_episodes(series_id, season_type="default")
        except TvdbRequestError as e:
            init.logger.warn(f"TVDB剧集列表获取失败 series_id={series_id}: {e}")
            continue
        for episode in series_episodes:
            item = dict(episode)
            item["tvdb_series_id"] = series_id
            episodes.append(item)
    return candidates, episodes


def _has_ai_episode_inference_config():
    ai_config = init.bot_config.get("ai") or {}
    return bool(
        str(ai_config.get("api_url") or "").strip()
        and str(ai_config.get("api_key") or "").strip()
        and str(ai_config.get("model") or "").strip()
    )


def _attempt_tvdb_ai_episode_rename(final_path, selected_path, resource_name, metadata):
    if not metadata:
        return None

    if not _has_ai_episode_inference_config():
        return None

    tvdb_candidates, tvdb_episodes = _get_tvdb_candidates_and_episodes(metadata)
    if not tvdb_candidates or not tvdb_episodes:
        return None

    file_tree = collect_115_file_tree(init.openapi_115, final_path)
    video_count = len([item for item in file_tree if not item.get("is_dir")])
    if not video_count:
        init.logger.warn(f"TVDB整理跳过：目录中未找到视频文件 {final_path}")
        return None

    context = {
        "metadata": metadata,
        "release_title": metadata.get("release_title") or resource_name,
        "resource_name": resource_name,
        "download_path": final_path,
        "file_tree": file_tree,
        "tvdb_candidates": tvdb_candidates,
        "tvdb_episodes": tvdb_episodes,
        "naming_rules": {
            "target_root": "selected_path / chinese_title (tvdb series_name)",
            "target_relative_path": "Series Name Season XX / Series Name SXXEXX.ext",
            "source_file": "must exactly match one file_tree relative_path or a unique file name",
        },
    }
    ai_plan = infer_tvdb_episode_plan_with_ai(context)
    rename_plan = build_tvdb_rename_plan(
        final_path=final_path,
        selected_path=selected_path,
        metadata=metadata,
        ai_plan=ai_plan,
        file_tree=file_tree,
        tvdb_candidates=tvdb_candidates,
        tvdb_episodes=tvdb_episodes,
    )
    if not rename_plan:
        init.logger.warn(f"TVDB整理跳过：AI映射未通过交叉校验 path={final_path}")
        return None

    for operation in rename_plan["operations"]:
        init.openapi_115.create_dir_recursive(operation["target_dir"])
        current_source_path = operation["source_path"]
        if Path(operation["source_path"]).name != operation["rename_to"]:
            if not init.openapi_115.rename(operation["source_path"], operation["rename_to"]):
                raise RuntimeError(f"TVDB整理失败：重命名失败 {operation['source_path']}")
            current_source_path = operation["renamed_source_path"]
        if not init.openapi_115.move_file(current_source_path, operation["target_dir"]):
            raise RuntimeError(f"TVDB整理失败：移动失败 {current_source_path}")

    if final_path != rename_plan["target_root"]:
        init.openapi_115.delete_single_file(final_path)

    handle_media_library_update(rename_plan["target_root"])
    return rename_plan

    
def _attempt_media_auto_rename(final_path, selected_path, resource_name, naming_metadata):
    if not naming_metadata:
        return None

    file_list = init.openapi_115.get_files_from_dir(final_path)
    if not file_list:
        init.logger.warn(f"自动整理跳过：目录中未找到视频文件 {final_path}")
        return None

    original_file_name = file_list[0]
    release_title = naming_metadata.get("release_title") or resource_name
    plan = build_media_naming_plan(naming_metadata, release_title, original_file_name)
    if not plan:
        init.logger.warn(f"自动整理跳过：豆瓣元数据不足 {naming_metadata}")
        return None

    target_path = f"{selected_path}/{plan.target_relative_dir}"
    init.openapi_115.create_dir_recursive(target_path)

    original_file_path = f"{final_path}/{original_file_name}"
    renamed_file_path = f"{final_path}/{plan.file_name}"
    if original_file_name != plan.file_name:
        init.openapi_115.rename(original_file_path, plan.file_name)

    init.openapi_115.move_file(renamed_file_path, target_path)
    if final_path != target_path:
        init.openapi_115.delete_single_file(final_path)

    handle_media_library_update(target_path)
    return target_path, plan


def _filename_metadata_from_resource(resource_name):
    inferred_title = infer_english_title_from_release(resource_name)
    if not inferred_title:
        return None
    return {
        "source": "filename",
        "chinese_title": inferred_title,
        "english_title": inferred_title,
        "query": inferred_title,
        "release_title": resource_name,
    }


def _has_metadata_value(value):
    return value is not None and value != "" and value != [] and value != {}


def _merge_tvdb_metadata(naming_metadata=None, metadata=None, filename_metadata=None):
    merged = {}
    for source in (naming_metadata, metadata):
        if not source:
            continue
        for key, value in source.items():
            if _has_metadata_value(value) or key not in merged:
                if key in {"external_ids", "evidence"} and isinstance(value, (dict, list)):
                    merged[key] = value.copy()
                elif _has_metadata_value(value):
                    merged[key] = value
    if filename_metadata:
        for key, value in filename_metadata.items():
            if key not in merged and _has_metadata_value(value):
                merged[key] = value
    return merged or None


def _resource_leaf(resource_name: str) -> str:
    return str(resource_name or "").strip("/").split("/")[-1] or "unknown"


def _move_to_unorganized(final_path, resource_name):
    unorganized_path = _get_unorganized_path()
    init.openapi_115.create_dir_recursive(unorganized_path)
    init.openapi_115.move_file(final_path, unorganized_path)
    return f"{unorganized_path}/{_resource_leaf(resource_name)}"


def _progress_percent(value) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = 0.0
    return max(0.0, min(percent, 100.0))


def _progress_bar(percent: float) -> str:
    filled = int(_progress_percent(percent) // 5)
    return "█" * filled + "░" * (20 - filled) + f" {_progress_percent(percent):.1f}%"


def _queue_download_failure_notice(user_id, reason, resource_name, progress_text, detail):
    add_task_to_queue(
        user_id,
        None,
        message=escape_markdown(
            f"⚠️ {reason}：{resource_name}\n"
            f"进度：{progress_text}\n\n"
            f"{detail}",
            version=2,
        ),
    )


def _handle_failed_offline_download(user_id, link, selected_path, resource_name, reason, progress_percent):
    percent = _progress_percent(progress_percent)
    progress_text = _progress_bar(percent)
    resource_name = resource_name or _resource_leaf(link)
    if percent >= PARTIAL_RETRY_PROGRESS_THRESHOLD:
        save_failed_download_to_db(
            resource_name,
            link,
            selected_path,
            progress_percent=percent,
            retry_category="partial",
            last_error=reason,
        )
        _queue_download_failure_notice(
            user_id,
            reason,
            resource_name,
            progress_text,
            "已保留到重试列表，可使用 /retry 单独选择重试。",
        )
        return

    discard_reason = "进度为 0%，判定为死种" if percent <= 0 else "进度过低，重试价值较低"
    _queue_download_failure_notice(
        user_id,
        reason,
        resource_name,
        progress_text,
        f"{discard_reason}，已丢弃。",
    )


def download_task(link, selected_path, user_id, naming_metadata=None, metadata=None):
    """异步下载任务"""
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
            _handle_failed_offline_download(
                user_id,
                link,
                selected_path,
                _resource_leaf(link),
                "115 离线任务创建失败",
                0,
            )
            return
            
        # 检查下载状态
        check_result = init.openapi_115.check_offline_download_success(link)
        download_success, resource_name, info_hash = check_result[:3]
        progress_percent = check_result[3] if len(check_result) > 3 else 0
        
        if download_success:
            init.logger.info(f"✅ {resource_name} 离线下载成功！")
            time.sleep(1)
            
            # 处理下载结果
            final_path = f"{selected_path}/{resource_name}"
            if init.openapi_115.is_directory(final_path):
                # 如果下载的内容是目录，清除垃圾文件
                init.openapi_115.auto_clean_all(final_path)
            else:
                # 如果下载的内容是文件，为文件套一个文件夹
                temp_folder = Path(resource_name).stem
                init.openapi_115.create_dir_for_file(selected_path, temp_folder)
                # 移动文件到临时目录
                init.openapi_115.move_file(final_path, f"{selected_path}/{temp_folder}")
                final_path = f"{selected_path}/{temp_folder}"
                resource_name = temp_folder

            try:
                filename_metadata = _filename_metadata_from_resource(resource_name)
                tvdb_metadata = _merge_tvdb_metadata(
                    naming_metadata=naming_metadata,
                    metadata=metadata,
                    filename_metadata=filename_metadata,
                )
                naming_auto_metadata = naming_metadata or (filename_metadata if not metadata and not naming_metadata else None)
                tvdb_result = _attempt_tvdb_ai_episode_rename(
                    final_path,
                    selected_path,
                    resource_name,
                    tvdb_metadata,
                )
                if tvdb_result:
                    message = (
                        f"✅ TVDB 自动整理完成：`{tvdb_result['series_name'] or tvdb_result['target_root'].split('/')[-1]}`\n"
                        f"文件数：{len(tvdb_result['operations'])} 个文件\n\n"
                        f"保存目录：`{tvdb_result['target_root']}`"
                    )
                    if tvdb_result.get("tvdb_series_id"):
                        message += f"\nTVDB：`{tvdb_result['tvdb_series_id']}`"
                    if tvdb_result.get("warnings"):
                        message += f"\n提示：{'; '.join(tvdb_result['warnings'][:2])}"
                    add_task_to_queue(user_id, None, message=message)
                    return

                auto_result = _attempt_media_auto_rename(final_path, selected_path, resource_name, naming_auto_metadata)
                if auto_result:
                    target_path, plan = auto_result
                    message = f"✅ 自动整理完成：`{plan.file_name}`\n\n保存目录：`{target_path}`"
                    add_task_to_queue(user_id, None, message=message)
                    return
            except Exception as e:
                init.logger.warn(f"自动整理失败，移入未整理目录: {e}")

            unorganized_target = _move_to_unorganized(final_path, resource_name)
            if naming_metadata:
                message = f"⚠️ 未自动整理，已移入未整理目录。\n\n保存目录：`{unorganized_target}`"
            else:
                message = f"✅ 离线下载完成，已移入未整理目录。\n\n保存目录：`{unorganized_target}`"
            add_task_to_queue(user_id, None, message=message)
            
        else:
            # 下载超时，后续由 finally 统一清理云端任务。
            init.logger.warn(f"❌ {resource_name} 离线下载超时")
            
            _handle_failed_offline_download(
                user_id,
                link,
                selected_path,
                resource_name,
                "115 离线下载超时",
                progress_percent,
            )
            
    except Exception as e:
        init.logger.error(f"下载任务执行失败: {str(e)}")
        add_task_to_queue(
            user_id,
            None,
            message=f"❌ 下载任务执行失败：{escape_markdown(str(e), version=2)}",
        )
    finally:
        # 清除云端任务，避免重复下载
        if init.openapi_115 is not None and info_hash:
            init.openapi_115.del_offline_task(info_hash, del_source_file=0)


def register_download_handlers(application):
    # 命令形式的下载交互
    download_command_handler = ConversationHandler(
         entry_points=[
            CommandHandler("magnet", magnet_command),
            CommandHandler("m", magnet_command),
        ],
        states={
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)]
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_command_handler)
    
    application.add_handler(CallbackQueryHandler(handle_plex_scan_callback, pattern=r"^plex_scan_(confirm|skip):"))
    init.logger.info("✅ Downloader处理器已注册")

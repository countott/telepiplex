# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
from telegram.helpers import escape_markdown
import init
import re
import time
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
from app.utils.plex_naming import build_plex_naming_plan
from app.utils.tvdb_rename import VIDEO_EXTENSIONS, build_tvdb_rename_plan

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

SELECT_MAIN_CATEGORY, SELECT_SUB_CATEGORY = range(10, 12)

# 全局线程池，用于处理下载任务
download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="Movie_Download")

class DownloadUrlType(Enum):
    ED2K = "ED2K"
    THUNDER = "thunder"
    MAGNET = "magnet"
    UNKNOWN = "unknown"
    
    def __str__(self):
        return self.value


async def start_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END
    magnet_link = update.message.text.strip()
    context.user_data["link"] = magnet_link  # 将用户参数存储起来
    init.logger.info(f"download link: {magnet_link}")
    dl_url_type = is_valid_link(magnet_link)
    # 检查链接格式是否正确
    if dl_url_type == DownloadUrlType.UNKNOWN:
        await update.message.reply_text("⚠️ 下载链接格式不受支持，请检查后重试。")
        return ConversationHandler.END
    # 保存下载类型到context.user_data
    context.user_data["dl_url_type"] = dl_url_type
    # 显示主分类（电影/剧集）
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])] for category in
        init.bot_config['category_folder']
    ]
    # 只在有最后保存路径时才显示该选项
    if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
        last_save_path = init.bot_session['movie_last_save']
        keyboard.append([InlineKeyboardButton(f"📁 上次保存: {last_save_path}", callback_data="last_save_path")])
    keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="📁 请选择保存分类：",
                                   reply_markup=reply_markup)
    return SELECT_MAIN_CATEGORY


async def select_main_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    query_data = query.data
    if query_data == "cancel":
        return await quit_conversation(update, context)
    elif query_data == "last_save_path":
        if hasattr(init, 'bot_session') and "movie_last_save" in init.bot_session:
            last_save_path = init.bot_session["movie_last_save"]
            link = context.user_data["link"]
            user_id = update.effective_user.id
            
            await query.edit_message_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
            
            # 使用全局线程池异步执行下载任务
            download_executor.submit(download_task, link, last_save_path, user_id)
            return ConversationHandler.END
        else:
            await query.edit_message_text("⚠️ 未找到上次保存路径，请重新选择分类。")
            return ConversationHandler.END
    else:
        context.user_data["selected_main_category"] = query_data
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == query_data
        ][0]

        # 创建子分类按钮
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=category['path'])] for category in sub_categories
        ]
        keyboard.append([InlineKeyboardButton("取消", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("📁 请选择保存目录：", reply_markup=reply_markup)

        return SELECT_SUB_CATEGORY


async def select_sub_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 获取用户选择的路径
    selected_path = query.data
    # 保存最后一次选择路径
    if not hasattr(init, 'bot_session'):
        init.bot_session = {}
    init.bot_session['movie_last_save'] = selected_path
    
    if selected_path == "cancel":
        return await quit_conversation(update, context)
    link = context.user_data["link"]
    selected_main_category = context.user_data["selected_main_category"]
    user_id = update.effective_user.id
    
    await query.edit_message_text("✅ 已加入下载队列。\n系统将投递到 115 离线下载，请稍后查看结果。")
    
    # 使用全局线程池异步执行下载任务
    download_executor.submit(download_task, link, selected_path, user_id)
    return ConversationHandler.END


async def handle_retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理重试任务的回调"""
    query = update.callback_query
    await query.answer()
    
    try:
        # 从callback_data中提取task_id
        task_id = query.data.replace("retry_", "")
        
        # 从全局存储中获取任务数据
        if hasattr(init, 'pending_tasks') and task_id in init.pending_tasks:
            task_data = init.pending_tasks[task_id]
            
            # 添加到重试列表
            save_failed_download_to_db(
                task_data["resource_name"], 
                task_data["link"], 
                task_data["selected_path"]
            )
            
            await query.edit_message_text("✅ 已加入重试列表，系统会按计划自动重试。")
            
            # 清理已使用的任务数据
            del init.pending_tasks[task_id]
        else:
            await query.edit_message_text("⚠️ 任务数据已过期，请重新发起下载。")
        
    except Exception as e:
        init.logger.error(f"处理重试回调失败: {e}")
        await query.edit_message_text("❌ 添加到重试列表失败，请稍后再试。")


async def handle_download_failure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理下载失败时的用户选择"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    
    if choice == "cancel_download":
        # 取消下载
        await query.edit_message_text("已取消本次下载，可更换资源后重试。")


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


def _media_value(config: dict, key: str, legacy_key: str = "", default=""):
    value = config.get(key)
    if value is None and legacy_key:
        value = init.bot_config.get(legacy_key)
    return default if value is None else value


def _configured(value, placeholder=""):
    value = str(value or "").strip()
    if not value:
        return False
    if placeholder and value.lower() == placeholder.lower():
        return False
    return not value.lower().startswith("your_")


def _emby_config():
    media = _media_config()
    return media.get("emby") or {}


def _plex_config():
    media = _media_config()
    return media.get("plex") or {}


def _get_unorganized_path():
    media = _media_config()
    return str(media.get("unorganized_path") or "/未整理").rstrip("/") or "/未整理"


def create_strm_file(new_name, file_list):
    emby_config = _emby_config()
    strm_mode = _media_value(emby_config, "strm_mode", "strm_mode", "disable")
    # 检查是否需要创建软链
    if strm_mode == "disable":
        return
    try:
        init.logger.debug(f"Original new_name: {new_name}")

        # 获取根目录
        cd2_mount_root = Path(_media_value(emby_config, "mount_root", "mount_root", "/CloudNAS/115"))
        strm_root = Path(_media_value(emby_config, "strm_root", "strm_root", "/media/115"))

        # 构建目标路径和 .strm 文件的路径
        relative_path = Path(new_name).relative_to(Path(new_name).anchor)
        cd2_mount_path = cd2_mount_root.joinpath(relative_path)
        strm_path = strm_root.joinpath(relative_path)

        # 日志输出以验证路径
        init.logger.debug(f"cd2_mount_root: {cd2_mount_root}")
        init.logger.debug(f"strm_root: {strm_root}")
        init.logger.debug(f"cd2_mount_path: {cd2_mount_path}")
        init.logger.debug(f"strm_path: {strm_path}")

        # 确保 strm_path 路径存在
        if not strm_path.exists():
            strm_path.mkdir(parents=True, exist_ok=True)

        # 遍历文件列表，创建 .strm 文件
        for file in file_list:
            target_file = strm_path / (Path(file).stem + ".strm")
            if strm_mode == "strm_local":
                mkv_file = cd2_mount_path / file
            else:
                mkv_file = Path(_media_value(emby_config, "openlist_root", "openlist_root", "/115")) / relative_path / (Path(file))

            # 日志输出以验证 .strm 文件和目标文件
            init.logger.debug(f"target_file (.strm): {target_file}")
            init.logger.debug(f"mkv_file (.mp4): {mkv_file}")

            # 如果原始文件存在，写入 .strm 文件
            # if mkv_file.exists():
            with target_file.open('w', encoding='utf-8') as f:
                f.write(str(mkv_file))
                init.logger.info(f"strm文件创建成功，{target_file} -> {mkv_file}")
            # else:
            #     init.logger.info(f"原始视频文件[{mkv_file}]不存在！")
    except Exception as e:
        init.logger.info(f"Error creating .strm files: {e}")


def notice_emby_scan_library(path):
    emby_config = _emby_config()
    strm_root = Path(_media_value(emby_config, "strm_root", "strm_root", ""))
    if not strm_root:
        init.logger.warn("未设置strm_root，无法扫库！")
        return False
    relative_path = Path(path).relative_to(Path(path).anchor)
    movie_path_in_emby = strm_root / relative_path
    emby_server = _media_value(emby_config, "base_url", "emby_server", "")
    api_key = _media_value(emby_config, "api_key", "api_key", "")
    if not _configured(api_key):
        init.logger.warn("Emby API Key 未配置，跳过通知Emby扫库")
        return False
    if str(emby_server).endswith("/"):
        emby_server = emby_server[:-1]
    url = f"{emby_server}/Library/Media/Updated"
    headers = {
        "accept": "*/*",
        "X-Emby-Token": api_key,
        "Content-Type": "application/json"
    }
    data = {
        "Updates": [
            {
                "Path": str(movie_path_in_emby),
                "UpdateType": "Created"
            }
        ]
    }
    emby_response = requests.post(url, headers=headers, json=data)
    if emby_response.text == "":
        init.logger.info("通知Emby扫库成功！")
        return True
    else:
        init.logger.error(f"通知Emby扫库失败：{emby_response}")
        return False


def _has_plex_scan_config():
    plex_config = _plex_config()
    return (
        _configured(plex_config.get("base_url"))
        and _configured(plex_config.get("token"))
        and _configured(plex_config.get("library_id"))
    )


def _has_emby_config():
    emby_config = _emby_config()
    return _configured(_media_value(emby_config, "base_url", "emby_server", "")) and _configured(
        _media_value(emby_config, "api_key", "api_key", "")
    )


def notice_plex_scan_library(path):
    if not _has_plex_scan_config():
        return None
    init.logger.info(f"Plex 扫库入口已预留，当前暂未触发实际接口 path={path}")
    return None


def handle_media_library_update(path, file_list=None):
    if _has_plex_scan_config():
        notice_plex_scan_library(path)
        return "plex"

    if _has_emby_config():
        if file_list is None:
            file_list = init.openapi_115.get_files_from_dir(path) if init.openapi_115 else []
        create_strm_file(path, file_list)
        notice_emby_scan_library(path)
        return "emby"

    init.logger.info(f"未配置 Plex/Emby 扫库，跳过媒体库通知 path={path}")
    return None


def save_failed_download_to_db(title, magnet, save_path):
    """保存失败的下载任务到数据库"""
    try:
        with SqlLiteLib() as sqlite:
            # 检查是否已存在相同的任务
            check_sql = "SELECT * FROM offline_task WHERE magnet = ? AND save_path = ? AND title = ?"
            existing = sqlite.query_one(check_sql, (magnet, save_path, title))
            
            if not existing:
                sql = "INSERT INTO offline_task (title, magnet, save_path) VALUES (?, ?, ?)"
                sqlite.execute_sql(sql, (title, magnet, save_path))
                init.logger.info(f"[{title}]已添加到重试列表")
    except Exception as e:
        raise str(e)
    

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

    
def _attempt_plex_auto_rename(final_path, selected_path, resource_name, plex_metadata):
    if not plex_metadata:
        return None

    file_list = init.openapi_115.get_files_from_dir(final_path)
    if not file_list:
        init.logger.warn(f"自动整理跳过：目录中未找到视频文件 {final_path}")
        return None

    original_file_name = file_list[0]
    release_title = plex_metadata.get("release_title") or resource_name
    plan = build_plex_naming_plan(plex_metadata, release_title, original_file_name)
    if not plan:
        init.logger.warn(f"自动整理跳过：豆瓣元数据不足 {plex_metadata}")
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


def _resource_leaf(resource_name: str) -> str:
    return str(resource_name or "").strip("/").split("/")[-1] or "unknown"


def _move_to_unorganized(final_path, resource_name):
    unorganized_path = _get_unorganized_path()
    init.openapi_115.create_dir_recursive(unorganized_path)
    init.openapi_115.move_file(final_path, unorganized_path)
    return f"{unorganized_path}/{_resource_leaf(resource_name)}"


def _ensure_pending_tasks():
    if not hasattr(init, "pending_tasks"):
        init.pending_tasks = {}
    return init.pending_tasks


def _build_retry_keyboard(task_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("加入重试列表", callback_data=f"retry_{task_id}")],
            [InlineKeyboardButton("取消", callback_data="cancel_download")],
        ]
    )


def _queue_retry_choice(user_id, link, selected_path, resource_name, reason):
    retry_task_id = str(int(time.time() * 1000))
    pending_tasks = _ensure_pending_tasks()
    pending_tasks[retry_task_id] = {
        "user_id": user_id,
        "action": "retry_download",
        "selected_path": selected_path,
        "resource_name": resource_name or _resource_leaf(link),
        "link": link,
        "add2retry": True,
    }
    add_task_to_queue(
        user_id,
        None,
        message=f"`{link}`\n\n⚠️ {reason}，可加入重试列表后由系统稍后重试。",
        keyboard=_build_retry_keyboard(retry_task_id),
    )


def download_task(link, selected_path, user_id, plex_metadata=None, metadata=None):
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
            _queue_retry_choice(user_id, link, selected_path, _resource_leaf(link), "115 离线任务创建失败")
            return
            
        # 检查下载状态
        download_success, resource_name, info_hash = init.openapi_115.check_offline_download_success(link)
        
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
                tvdb_result = _attempt_tvdb_ai_episode_rename(
                    final_path,
                    selected_path,
                    resource_name,
                    metadata or plex_metadata,
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

                auto_result = _attempt_plex_auto_rename(final_path, selected_path, resource_name, plex_metadata)
                if auto_result:
                    target_path, plan = auto_result
                    message = f"✅ 自动整理完成：`{plan.file_name}`\n\n保存目录：`{target_path}`"
                    add_task_to_queue(user_id, None, message=message)
                    return
            except Exception as e:
                init.logger.warn(f"自动整理失败，移入未整理目录: {e}")

            unorganized_target = _move_to_unorganized(final_path, resource_name)
            if plex_metadata:
                message = f"⚠️ 未自动整理，已移入未整理目录。\n\n保存目录：`{unorganized_target}`"
            else:
                message = f"✅ 离线下载完成，已移入未整理目录。\n\n保存目录：`{unorganized_target}`"
            add_task_to_queue(user_id, None, message=message)
            
        else:
            # 下载超时，删除任务并提供选择
            init.openapi_115.del_offline_task(info_hash)
            init.logger.warn(f"❌ {resource_name} 离线下载超时")
            
            _queue_retry_choice(user_id, link, selected_path, resource_name, "115 离线下载超时")
            
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
            MessageHandler(
                filters.TEXT & filters.Regex(r'^(magnet:|ed2k://|ED2K://|thunder://)(?!.*\n).+$'),
                start_d_command
            )
        ],
        states={
            SELECT_MAIN_CATEGORY: [CallbackQueryHandler(select_main_category)],
            SELECT_SUB_CATEGORY: [CallbackQueryHandler(select_sub_category)]
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
    )
    application.add_handler(download_command_handler)
    
    # 添加独立的回调处理器处理异步任务的后续操作
    application.add_handler(CallbackQueryHandler(handle_retry_callback, pattern=r"^retry_"))
    application.add_handler(CallbackQueryHandler(handle_download_failure, pattern=r"^cancel_download$"))
    init.logger.info("✅ Downloader处理器已注册")

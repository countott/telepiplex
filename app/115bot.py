# -*- coding: utf-8 -*-

import json
import os
import time
import asyncio
import threading
import signal
from telegram import Update, BotCommand
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes, CommandHandler, Application
from telegram.helpers import escape_markdown

# 导入init模块（此时__init__.py已经设置了模块路径）
import init

from app.utils.message_queue import add_task_to_queue, queue_worker
from app.handlers.auth_handler import register_auth_handlers
from app.handlers.download_handler import register_download_handlers
from app.handlers.search_handler import register_search_handlers
from app.handlers.sync_handler import register_sync_handlers
from app.handlers.video_handler import register_video_handlers
from app.core.scheduler import start_scheduler_in_thread
from app.handlers.offline_task_handler import register_offline_task_handlers
from app.handlers.aria2_handler import register_aria2_handlers

TELEGRAM_API_TIMEOUT = 30


def get_version(md_format=False):
    version = "v3.4.3"
    if md_format:
        return escape_markdown(version, version=2)
    return version


def log_runtime_features():
    revision = os.getenv("TELEPIPLEX_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
    init.logger.info(
        "Telepiplex runtime features: direct_metadata_link_search=enabled, "
        "builtin_douban_title_priority=latin_or_original_first, "
        "external_metadata_douban_reverse_lookup=enabled, prowlarr_indexer_summary=enabled, "
        "metadata_object=enabled, search_command=enabled, magnet_command=enabled, find_command_removed=enabled, "
        "legacy_s_command_removed=enabled, retry_command=enabled, strm_command=enabled, tvdb_adapter=enabled, ai_tvdb_inference=enabled, "
        "tvdb_ai_115_tree_rename=enabled, "
        "revision=%s" % revision
    )

def get_help_info():
    version = get_version()
    help_info = f"""
<b>🍿 Telegram-115Bot {version} 使用手册</b>\n\n
<b>🔧 命令列表</b>\n
<code>/start</code> - 显示帮助信息\n
<code>/auth</code> - <i>115扫码授权 (解除授权后使用)</i>\n
<code>/reload</code> - <i>重载配置</i>\n
<code>/search</code> - 搜索片源并加入 115 离线\n
<code>/magnet</code> - 直接投递已有磁力链接\n
<code>/m</code> - 直接投递磁力链接的短命令\n
<code>/retry</code> - 查看重试列表\n
<code>/r</code> - 查看重试列表的短命令\n
<code>/strm</code> - 同步目录并创建 STRM 文件\n
<code>/q</code> - 取消当前会话\n\n
<b>✨ 功能说明</b>\n
<u>电影下载：</u>
• 输入 <code>"/search 片名"</code>，或直接发送豆瓣/IMDb/TVDB/TMDB 链接搜索片源
• 输入 <code>"/magnet 磁力链接"</code> 或 <code>"/m 磁力链接"</code> 跳过片名搜索，直接选择目录并投递 115 离线
• 下载完成后优先根据实际文件名自动整理；搜索链路中的元数据只作为辅助
• 离线超时后可选择写入重试列表
• 根据媒体服务配置自动整理并通知媒体库\n
<u>重试列表：</u>
• 输入 <code>"/retry"</code> 或 <code>"/r"</code>
• 查看当前重试列表，可根据需要选择是否清空\n
<u>目录同步：</u>
• 输入 <code>"/strm"</code>
• 选择目录后会在对应的目录创建 STRM 文件\n
<u>视频下载：</u>
• 直接转发视频给机器人，选择保存目录即可保存到115
"""
    return help_info

async def send_bot_message_safely(bot, *, chat_id, text, **kwargs):
    timeout_kwargs = {
        "connect_timeout": TELEGRAM_API_TIMEOUT,
        "read_timeout": TELEGRAM_API_TIMEOUT,
        "write_timeout": TELEGRAM_API_TIMEOUT,
        "pool_timeout": TELEGRAM_API_TIMEOUT,
    }
    timeout_kwargs.update(kwargs)
    try:
        await bot.send_message(chat_id=chat_id, text=text, **timeout_kwargs)
        return True
    except NetworkError as e:
        if init.logger:
            init.logger.warn(f"Telegram 消息发送超时/网络异常，消息可能已成功送达: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_info = get_help_info()
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text=help_info,
        parse_mode="html",
        disable_web_page_preview=True,
    )

async def reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init.load_yaml_config()
    init.logger.info("配置已重新加载:")
    init.logger.info(json.dumps(init.bot_config))
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text="✅ 配置已重新加载。",
        parse_mode="html",
    )

def start_async_loop():
    """启动异步事件循环的线程"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init.logger.info("事件循环已启动")
    try:
        token = init.bot_config['bot_token']
        loop.create_task(queue_worker(loop, token))
        loop.run_forever()
    except Exception as e:
        init.logger.error(f"事件循环异常: {e}")
    finally:
        loop.close()
        init.logger.info("事件循环已关闭")

def send_start_message():
    version = get_version()  
    if init.openapi_115 is None:
        return
    
    line1, line2, line3, line4 = init.openapi_115.welcome_message()
    if not line1:
        return
    line5 = escape_markdown(f"✅ Telegram-115Bot {version} 已启动", version=2)
    if line1 and line2 and line3 and line4:
        formatted_message = f"""
{line1}
{line2}
{line3}
{line4}

{line5}

发送 `/start` 查看操作说明"""

        add_task_to_queue(
            init.bot_config['allowed_user'],
            None,
            message=formatted_message
        )


def update_logger_level():
    import logging
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Application').setLevel(logging.WARNING)
    logging.getLogger('telegram.ext.Updater').setLevel(logging.WARNING)
    logging.getLogger('telegram.Bot').setLevel(logging.WARNING)
    
def get_bot_menu():
    return  [
        BotCommand("start", "获取帮助信息"),
        BotCommand("auth", "115扫码授权"),
        BotCommand("reload", "重载配置"),
        BotCommand("search", "搜索片源并加入 115 离线"),
        BotCommand("magnet", "直接投递磁力链接"),
        BotCommand("m", "直接投递磁力链接"),
        BotCommand("retry", "查看重试列表"),
        BotCommand("r", "查看重试列表"),
        BotCommand("strm", "同步指定目录，并创建 STRM 文件"),
        BotCommand("q", "退出当前会话")]
    

async def set_bot_menu(application):
    """异步设置Bot菜单"""
    try:
        await application.bot.set_my_commands(get_bot_menu())
        init.logger.info("Bot菜单命令已设置!")
    except Exception as e:
        init.logger.error(f"设置Bot菜单失败: {e}")

async def post_init(application):
    """应用初始化后的回调"""
    await set_bot_menu(application)


def build_application(token):
    return (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .connect_timeout(TELEGRAM_API_TIMEOUT)
        .read_timeout(TELEGRAM_API_TIMEOUT)
        .write_timeout(TELEGRAM_API_TIMEOUT)
        .pool_timeout(TELEGRAM_API_TIMEOUT)
        .build()
    )


async def initialize_application_with_retry(application, max_retries=5, retry_delay=5):
    for attempt in range(max_retries + 1):
        try:
            await application.initialize()
            return
        except NetworkError as e:
            if attempt >= max_retries:
                raise
            if init.logger:
                init.logger.warn(
                    f"Telegram Bot 初始化超时/网络异常，{retry_delay} 秒后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )
            await asyncio.sleep(retry_delay)


async def run_application_polling(application, after_start=None, stop_event=None, initialize_retry_delay=5):
    """Run PTB with an explicit lifecycle to avoid half-initialized polling startup."""
    if stop_event is None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                break

    try:
        await initialize_application_with_retry(application, retry_delay=initialize_retry_delay)
        if application.post_init:
            await application.post_init(application)
        await application.start()
        if application.updater:
            await application.updater.start_polling(bootstrap_retries=5)
        if after_start:
            after_start()
        await stop_event.wait()
    finally:
        if application.updater and getattr(application.updater, "running", False):
            await application.updater.stop()
        if getattr(application, "running", False):
            await application.stop()
        await application.shutdown()


def start_runtime_services(openapi_ready: bool):
    if not openapi_ready:
        init.logger.warn("115 OpenAPI 未初始化，跳过定时任务和启动账号信息；Bot 将以受限模式继续运行。")
        return

    start_scheduler_in_thread()
    init.logger.info("订阅线程启动成功！")
    time.sleep(3)  # 等待订阅线程启动
    send_start_message()


if __name__ == '__main__':
    init.init()
    # 启动消息队列
    message_thread = threading.Thread(target=start_async_loop, daemon=True)
    message_thread.start()
    # 等待消息队列准备就绪
    import app.utils.message_queue as message_queue
    max_wait = 30  # 最多等待30秒
    wait_count = 0
    while True:
        if message_queue.global_loop is not None:
            init.logger.info("消息队列线程已准备就绪！")
            break
        time.sleep(1)
        wait_count += 1
        if wait_count >= max_wait:
            init.logger.error("消息队列线程未准备就绪，程序将退出。")
            exit(1)
    init.logger.info("Starting bot with configuration:")
    init.logger.info(json.dumps(init.bot_config))
    log_runtime_features()
    # 调整telegram日志级别
    update_logger_level()
    token = init.bot_config['bot_token']
    application = build_application(token)

    # 启动帮助
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    # 重载配置
    reload_handler = CommandHandler('reload', reload)
    application.add_handler(reload_handler)
    
    # 初始化115open对象
    openapi_ready = init.initialize_115open()
    if not openapi_ready:
        init.logger.error("115 OpenAPI客户端初始化失败，离线投递功能暂不可用。")
        add_task_to_queue(
            init.bot_config['allowed_user'],
            None,
            message=(
                "❌ 115 OpenAPI 初始化失败，Bot 已进入受限模式。\n"
                "请检查 `/config/config.yaml` 中的 `115_app_id` 或 `access_token`/`refresh_token`。\n"
                "直连 Token 模式下，`115_app_id` 可以留空，但两个 Token 必须有效。\n"
                "搜索和 `/auth`/`/reload` 仍可使用，离线投递会暂时拒绝。"
            )
        )
        init.logger.warn("115 OpenAPI 初始化失败，Bot 将继续启动以便使用 `/auth`、`/reload` 和搜索功能，避免容器重启反复刷新 Token。")


    # 注册Auth
    register_auth_handlers(application)
    # 注册搜索
    register_search_handlers(application)
    # 注册下载
    register_download_handlers(application)
    # 注册离线任务
    register_offline_task_handlers(application)
    # 注册Aria2
    register_aria2_handlers(application)
    # 注册同步
    register_sync_handlers(application)
    # 注册视频
    register_video_handlers(application)
    
    init.logger.info(f"USER_AGENT: {init.USER_AGENT}")

    # 启动机器人轮询
    try:
        asyncio.run(run_application_polling(application, after_start=lambda: start_runtime_services(openapi_ready)))
    except KeyboardInterrupt:
        init.logger.info("程序已被用户终止（Ctrl+C）。")
    except SystemExit:
        init.logger.info("程序正在退出。")
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()  # 获取完整的异常堆栈信息
        init.logger.error(f"程序遇到错误：{str(e)}\n{error_details}")
    finally:
        init.logger.info("机器人已停止运行。")

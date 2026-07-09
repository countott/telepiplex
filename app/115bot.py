# -*- coding: utf-8 -*-

import asyncio
import json
import os
import signal
import threading
import time

from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.helpers import escape_markdown

import init
from app.handlers.auth_handler import register_auth_handlers
from app.handlers.config_handler import build_config_keyboard, register_config_handlers
from app.handlers.download_handler import register_download_handlers
from app.utils.message_queue import add_task_to_queue, queue_worker


TELEGRAM_API_TIMEOUT = 30
SENSITIVE_CONFIG_KEYWORDS = (
    "token",
    "api_key",
    "secret",
    "password",
    "app_id",
    "api_hash",
    "cookie",
    "authorization",
)


def get_version(md_format=False):
    version = "v3.4.3-115"
    if md_format:
        return escape_markdown(version, version=2)
    return version


def log_runtime_features():
    revision = os.getenv("TELEPIPLEX_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
    init.logger.info(
        "Telepiplex runtime features: feature_115_minimal=enabled, "
        "magnet_command=enabled, custom_top_folder_rename=enabled, "
        "revision=%s" % revision
    )


def _is_sensitive_config_key(key: str) -> bool:
    normalized = str(key or "").lower()
    return any(keyword in normalized for keyword in SENSITIVE_CONFIG_KEYWORDS)


def sanitize_config_for_log(value):
    if isinstance(value, dict):
        return {
            key: "***redacted***" if _is_sensitive_config_key(key) else sanitize_config_for_log(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_config_for_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_config_for_log(item) for item in value)
    return value


def log_config_snapshot(prefix: str):
    init.logger.info(prefix)
    init.logger.info(json.dumps(sanitize_config_for_log(init.bot_config), ensure_ascii=False))


def get_help_info():
    version = get_version()
    return f"""
<b>Telegram-115Bot {version} 使用手册</b>\n\n
<b>命令列表</b>\n
<code>/start</code> - 显示帮助信息\n
<code>/auth</code> - 115 扫码授权\n
<code>/config</code> - 配置 115 OpenAPI 或 Access / Refresh Token\n
<code>/reload</code> - 重载配置\n
<code>/magnet</code> - 直接投递磁力链接到 115 离线\n
<code>/m</code> - /magnet 的短命令\n
<code>/q</code> - 取消当前会话\n\n
<b>使用方式</b>\n
发送 <code>/magnet magnet:?xt=urn:btih:...</code> 或 <code>/m magnet:?xt=urn:btih:...</code>。
Bot 会依次要求选择 115 保存目录、输入完成后的顶层文件夹名，然后投递离线任务。
输入 <code>-</code> 可保留 115 原始文件夹名。
"""


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
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text=get_help_info(),
        parse_mode="html",
        disable_web_page_preview=True,
    )


async def reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init.load_yaml_config()
    log_config_snapshot("配置已重新加载:")
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text="✅ 配置已重新加载。",
        parse_mode="html",
    )


def start_async_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init.logger.info("事件循环已启动")
    try:
        token = init.bot_config["bot_token"]
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
            init.bot_config["allowed_user"],
            None,
            message=formatted_message,
        )


def update_logger_level():
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
    logging.getLogger("telegram.Bot").setLevel(logging.WARNING)


def get_bot_menu():
    return [
        BotCommand("start", "获取帮助信息"),
        BotCommand("auth", "115 扫码授权"),
        BotCommand("config", "配置 115 Token"),
        BotCommand("reload", "重载配置"),
        BotCommand("magnet", "投递磁力链接"),
        BotCommand("m", "投递磁力链接"),
        BotCommand("q", "退出当前会话"),
    ]


async def set_bot_menu(application):
    try:
        await application.bot.set_my_commands(get_bot_menu())
        init.logger.info("Bot菜单命令已设置!")
    except Exception as e:
        init.logger.error(f"设置Bot菜单失败: {e}")


async def post_init(application):
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
        init.logger.warn("115 OpenAPI 未初始化，跳过启动账号信息；Bot 将以受限模式继续运行。")
        return

    time.sleep(3)
    send_start_message()


def queue_115_init_failure_notice():
    raw_message = (
        "❌ 115 OpenAPI 初始化失败，Bot 已进入受限模式。\n"
        "请检查 `/config/config.yaml` 中的 `115_app_id` 或 `access_token`/`refresh_token`。\n"
        "直连 Token 模式下，`115_app_id` 可以留空，但两个 Token 必须有效。\n"
        "可使用 `/config` 在 Telegram 中写入直连 Token。\n"
        "离线投递会暂时拒绝，`/auth` 和 `/reload` 仍可使用。"
    )
    return add_task_to_queue(
        init.bot_config["allowed_user"],
        None,
        message=escape_markdown(raw_message, version=2),
        keyboard=build_config_keyboard(),
    )


def main():
    init.init()
    message_thread = threading.Thread(target=start_async_loop, daemon=True)
    message_thread.start()

    import app.utils.message_queue as message_queue

    max_wait = 30
    wait_count = 0
    while True:
        if message_queue.global_loop is not None:
            init.logger.info("消息队列线程已准备就绪！")
            break
        time.sleep(1)
        wait_count += 1
        if wait_count >= max_wait:
            init.logger.error("消息队列线程未准备就绪，程序将退出。")
            raise SystemExit(1)

    log_config_snapshot("Starting bot with configuration:")
    log_runtime_features()
    update_logger_level()
    application = build_application(init.bot_config["bot_token"])

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reload", reload))

    openapi_ready = init.initialize_115open()
    if not openapi_ready:
        init.logger.error("115 OpenAPI客户端初始化失败，离线投递功能暂不可用。")
        queue_115_init_failure_notice()
        init.logger.warn("115 OpenAPI 初始化失败，Bot 将继续启动以便使用 `/auth` 和 `/reload`。")

    register_config_handlers(application)
    register_auth_handlers(application)
    register_download_handlers(application)

    init.logger.info(f"USER_AGENT: {init.USER_AGENT}")

    try:
        asyncio.run(run_application_polling(application, after_start=lambda: start_runtime_services(openapi_ready)))
    except KeyboardInterrupt:
        init.logger.info("程序已被用户终止（Ctrl+C）。")
    except SystemExit:
        init.logger.info("程序正在退出。")
    except Exception as e:
        import traceback

        error_details = traceback.format_exc()
        init.logger.error(f"程序遇到错误：{str(e)}\n{error_details}")
    finally:
        init.logger.info("机器人已停止运行。")


if __name__ == "__main__":
    main()

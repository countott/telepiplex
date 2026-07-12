# -*- coding: utf-8 -*-

import asyncio
import inspect
import json
import os
import signal
import threading
import time
from pathlib import Path

from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

import init
from app.core.module_loader import load_enabled_modules
from app.core.module_registry import ModuleRegistry
from app.core.capability_router import CapabilityRouter
from app.core.event_journal import EventJournal
from app.core.plugin_manager import PluginManager
from app.core.plugin_store import PluginStore
from app.core.plugin_supervisor import PluginSupervisor
from app.handlers.plugin_handler import (
    dynamic_callback_gateway,
    dynamic_command_gateway,
    plugin_command,
)
try:
    from app.utils.message_queue import add_task_to_queue, queue_worker
except ImportError:
    def add_task_to_queue(*_args, **_kwargs):
        return False

    async def queue_worker(_loop, _token):
        return None


TELEGRAM_API_TIMEOUT = 30
CORE_BOT_COMMANDS = [
    BotCommand("start", "获取核心状态"),
    BotCommand("reload", "重载配置"),
    BotCommand("plugin", "安装和管理 Feature"),
]
MODULE_LABELS = {
    "app.modules.open115": "115 下载",
    "app.modules.media_search": "媒体搜索",
    "app.modules.renaming": "下载后重命名",
}
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
    version = "v3.4.3-core"
    if md_format:
        return escape_markdown(version, version=2)
    return version


def log_runtime_features():
    revision = os.getenv("TELEPIPLEX_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
    module_names = get_enabled_module_names()
    init.logger.info(
        "Telepiplex runtime features: telepiplex_core=enabled, "
        "basic_telegram_runtime=enabled, message_queue=enabled, "
        f"modules={module_names}, revision={revision}"
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


def get_enabled_module_names(config=None):
    config = config or init.bot_config
    modules_config = (config or {}).get("modules") or {}
    enabled = modules_config.get("enabled") or []
    if isinstance(enabled, str):
        enabled = [item.strip() for item in enabled.split(",")]
    return [str(item).strip() for item in enabled if str(item).strip()]


def build_module_registry():
    registry = ModuleRegistry()
    loaded = load_enabled_modules(registry, get_enabled_module_names())
    registry.loaded_module_names = loaded
    init.module_registry = registry
    if init.logger:
        init.logger.info(f"已加载 Telepiplex 模块: {loaded}")
    return registry


def build_plugin_manager(config=None, core_database=None):
    config = config or {}
    plugin_config = config.get("plugins") or {}
    root = Path(str(plugin_config.get("root") or "/config/plugins"))
    if core_database is None:
        core_database = root.parent / "core.db"
    router = CapabilityRouter()
    journal = EventJournal(Path(core_database))
    supervisor = PluginSupervisor(
        startup_timeout=float(plugin_config.get("startup_timeout") or 30),
        restart_limit=int(plugin_config.get("restart_limit") or 3),
        runtime_root=Path(str(plugin_config.get("runtime_root") or "/tmp/telepiplex")),
    )
    manager = PluginManager(
        store=PluginStore(root),
        supervisor=supervisor,
        router=router,
        journal=journal,
        install_timeout=float(plugin_config.get("install_timeout") or 300),
        drain_timeout=float(plugin_config.get("drain_timeout") or 120),
        stabilize_seconds=float(plugin_config.get("stabilize_seconds") or 10),
    )
    return manager


def build_core_startup_notice_text(config=None, registry=None):
    if config is None:
        config = init.bot_config
    running_modules = getattr(registry, "loaded_module_names", None) if registry is not None else None
    if running_modules is None:
        running_modules = get_enabled_module_names(config)

    lines = [
        "✅ Telepiplex 启动完成",
        "",
        "已加载模块",
    ]
    if running_modules:
        for module_name in running_modules:
            label = escape_markdown(MODULE_LABELS.get(module_name, module_name), version=2)
            lines.append(f"✅ {label}")
    else:
        lines.append("✅ Core")
    lines.extend(["", "可使用 /start 查看核心状态"])
    return "\n".join(lines)


def queue_core_startup_notice(registry=None):
    allowed_user = (init.bot_config or {}).get("allowed_user")
    if allowed_user is None or str(allowed_user).strip() == "":
        if init.logger:
            init.logger.warn("未配置 allowed_user，跳过启动完成通知。")
        return False

    return add_task_to_queue(
        allowed_user,
        None,
        message=build_core_startup_notice_text(init.bot_config, registry),
    )


def run_core_startup_hooks(registry, application=None):
    registry.run_startup_hooks(application)
    queue_core_startup_notice(registry)


def get_help_info():
    version = get_version()
    return f"""
<b>Telepiplex Core {version}</b>\n\n
<b>命令列表</b>\n
<code>/start</code> - 显示核心运行层状态\n
<code>/reload</code> - 重载配置\n\n
此分支只包含 Telepiplex 核心运行层，不包含 115 投递、媒体搜索或媒体整理业务能力。
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
    if not init.check_user(update.effective_user.id):
        await send_bot_message_safely(
            context.bot,
            chat_id=update.effective_chat.id,
            text="⚠️ 当前账号无权使用此机器人。",
            parse_mode="html",
        )
        return
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


def update_logger_level():
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
    logging.getLogger("telegram.Bot").setLevel(logging.WARNING)


def get_bot_menu(registry=None):
    commands = list(CORE_BOT_COMMANDS)
    if registry is not None:
        commands.extend(registry.bot_commands())
    return commands


async def set_bot_menu(application):
    try:
        await application.bot.set_my_commands(get_bot_menu(application.bot_data.get("telepiplex_registry")))
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
            result = after_start()
            if inspect.isawaitable(result):
                await result
        await stop_event.wait()
    finally:
        if application.updater and getattr(application.updater, "running", False):
            await application.updater.stop()
        if getattr(application, "running", False):
            await application.stop()
        bot_data = getattr(application, "bot_data", None)
        manager = bot_data.get("telepiplex_plugin_manager") if isinstance(bot_data, dict) else None
        if manager is not None:
            await manager.close()
        await application.shutdown()


def configure_application(application, manager, registry=None):
    application.bot_data["telepiplex_plugin_manager"] = manager
    application.bot_data["telepiplex_plugin_router"] = manager.router
    if registry is not None:
        application.bot_data["telepiplex_registry"] = registry
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reload", reload))
    application.add_handler(CommandHandler("plugin", plugin_command))
    application.add_handler(CallbackQueryHandler(dynamic_callback_gateway))
    application.add_handler(MessageHandler(filters.COMMAND, dynamic_command_gateway))
    if registry is not None:
        registry.register_handlers(application)


async def start_core_runtime(manager, registry, application):
    await manager.restore_active()
    run_core_startup_hooks(registry, application)


if __name__ == "__main__":
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
    registry = build_module_registry()
    plugin_manager = build_plugin_manager(init.bot_config)
    configure_application(application, plugin_manager, registry)

    try:
        asyncio.run(run_application_polling(
            application,
            after_start=lambda: start_core_runtime(plugin_manager, registry, application),
        ))
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

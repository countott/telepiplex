# -*- coding: utf-8 -*-

import asyncio
import copy
import inspect
import json
import os
import signal
import threading
import time
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from app.core.capability_router import CapabilityRouter
from app.core.core_broker import CoreBroker
from app.core.event_dispatcher import EventDispatcher
from app.core.event_journal import EventJournal
from app.core.plugin_catalog import PluginCatalog
from app.core.plugin_manager import PluginManager
from app.core.plugin_store import PluginStore
from app.core.plugin_supervisor import PluginSupervisor
from app.core.plugin_update_monitor import PluginUpdateMonitor
from app.handlers.plugin_handler import (
    dynamic_callback_gateway,
    dynamic_command_gateway,
    dynamic_message_gateway,
    plugin_command,
    plugin_install_callback,
    plugin_update_callback,
)
from app.handlers.config_handler import register_feature_config_handlers
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
    BotCommand("config", "配置 Feature"),
]
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
PLUGIN_UPDATE_TASK_KEY = "telepiplex_plugin_update_task"
DEFAULT_PLUGIN_CATALOG_URL = (
    "https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml"
)


def get_version(md_format=False):
    version = "v3.4.3-core"
    if md_format:
        return escape_markdown(version, version=2)
    return version


def log_runtime_features():
    revision = os.getenv("TELEPIPLEX_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
    init.logger.info(
        "Telepiplex runtime features: telepiplex_core=enabled, "
        "basic_telegram_runtime=enabled, message_queue=enabled, "
        f"plugin_host=enabled, revision={revision}"
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
    if init.logger is None:
        return
    init.logger.info(prefix)
    init.logger.info(json.dumps(sanitize_config_for_log(init.bot_config), ensure_ascii=False))


def resolve_plugin_catalog_source(plugin_config: dict, root: Path) -> str:
    configured = str((plugin_config or {}).get("catalog") or "").strip()
    legacy = Path(root) / "catalog.yaml"
    if not configured:
        return DEFAULT_PLUGIN_CATALOG_URL
    source = Path(configured).expanduser()
    if source.resolve(strict=False) == legacy.expanduser().resolve(strict=False):
        return configured if source.is_file() else DEFAULT_PLUGIN_CATALOG_URL
    return configured


def build_plugin_manager(config=None, core_database=None):
    config = config or {}
    plugin_config = config.get("plugins") or {}
    root = Path(str(plugin_config.get("root") or "/config/plugins"))
    if core_database is None:
        core_database = root.parent / "core.db"
    router = CapabilityRouter()
    journal = EventJournal(Path(core_database))
    runtime_root = Path(str(plugin_config.get("runtime_root") or "/tmp/telepiplex"))
    dispatcher = EventDispatcher(
        router,
        journal,
        retry_interval=float(plugin_config.get("event_retry_interval") or 1),
        delivery_deadline=float(
            plugin_config.get("event_delivery_timeout") or 1800
        ),
        max_attempts=int(plugin_config.get("event_max_attempts") or 5),
    )
    broker = CoreBroker(
        router,
        journal,
        runtime_root / "core.sock",
        dispatcher=dispatcher,
        notification_sink=lambda user_id, text: add_task_to_queue(
            user_id, None, message=text
        ),
    )
    supervisor = PluginSupervisor(
        startup_timeout=float(plugin_config.get("startup_timeout") or 30),
        restart_limit=int(plugin_config.get("restart_limit") or 3),
        runtime_root=runtime_root,
        broker=broker,
    )
    catalog_source = resolve_plugin_catalog_source(plugin_config, root)
    catalog = PluginCatalog(catalog_source, root / ".cache")
    manager = PluginManager(
        store=PluginStore(root),
        supervisor=supervisor,
        router=router,
        journal=journal,
        artifact_resolver=catalog,
        broker=broker,
        install_timeout=float(plugin_config.get("install_timeout") or 300),
        drain_timeout=float(plugin_config.get("drain_timeout") or 120),
        stabilize_seconds=float(plugin_config.get("stabilize_seconds") or 10),
    )
    return manager


def build_core_startup_notice_text():
    return "✅ Telepiplex Core 启动完成\n\n可使用 /plugin 查看并安装 Feature"


def queue_core_startup_notice():
    allowed_user = (init.bot_config or {}).get("allowed_user")
    if allowed_user is None or str(allowed_user).strip() == "":
        if init.logger:
            init.logger.warn("未配置 allowed_user，跳过启动完成通知。")
        return False

    return add_task_to_queue(
        allowed_user,
        None,
        message=build_core_startup_notice_text(),
    )


def get_help_info():
    version = get_version()
    return f"""
<b>Telepiplex Core {version}</b>\n\n
<b>命令列表</b>\n
<code>/start</code> - 显示核心运行层状态\n
<code>/reload</code> - 重载配置\n\n
<code>/plugin</code> - 安装和管理 Feature\n\n
<code>/config</code> - 配置已安装 Feature\n\n
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


async def send_plugin_update_notification(application, update):
    allowed_user = (init.bot_config or {}).get("allowed_user")
    if allowed_user is None or str(allowed_user).strip() == "":
        if init.logger:
            init.logger.warn("未配置 allowed_user，跳过 Feature 更新通知。")
        return False

    confirm_data = f"core-plugin-update:confirm:{update.reference}"
    decline_data = f"core-plugin-update:decline:{update.reference}"
    if max(len(confirm_data.encode("utf-8")), len(decline_data.encode("utf-8"))) > 64:
        if init.logger:
            init.logger.warn("Feature 更新引用过长，无法生成 Telegram 确认按钮。")
        return False
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("确认更新", callback_data=confirm_data),
        InlineKeyboardButton("暂不更新", callback_data=decline_data),
    ]])
    commit = str(getattr(update, "source_commit", "") or "")
    commit_line = f"\n来源提交：{commit[:12]}" if commit else ""
    return await send_bot_message_safely(
        application.bot,
        chat_id=allowed_user,
        text=(
            "🆕 发现兼容的 Feature 更新\n\n"
            f"插件：{update.plugin_id}\n"
            f"当前版本：{update.current_version}\n"
            f"目标版本：{update.target_version}"
            f"{commit_line}\n\n"
            "仅在点击“确认更新”后执行；Core 不会静默更新。"
        ),
        reply_markup=keyboard,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text=get_help_info(),
        parse_mode="html",
        disable_web_page_preview=True,
    )


def apply_hot_runtime_config(manager, previous: dict, current: dict) -> list[str]:
    """Apply settings safe to mutate in-process and name those needing restart."""
    plugin_config = (current or {}).get("plugins") or {}
    if not isinstance(plugin_config, dict):
        raise ValueError("plugins must be a mapping")

    def value(key, default):
        configured = plugin_config.get(key, default)
        return default if configured is None else configured

    install_timeout = float(value("install_timeout", 300))
    drain_timeout = float(value("drain_timeout", 120))
    stabilize_seconds = max(0, float(value("stabilize_seconds", 10)))
    startup_timeout = float(value("startup_timeout", 30))
    restart_limit = max(0, int(value("restart_limit", 3)))
    event_retry_interval = max(0.01, float(value("event_retry_interval", 1)))
    event_delivery_timeout = max(
        0.1, float(value("event_delivery_timeout", 1800))
    )
    event_max_attempts = max(1, int(value("event_max_attempts", 5)))

    manager.install_timeout = install_timeout
    manager.drain_timeout = drain_timeout
    manager.stabilize_seconds = stabilize_seconds
    manager.supervisor.startup_timeout = startup_timeout
    manager.supervisor.restart_limit = restart_limit
    dispatcher = getattr(getattr(manager, "broker", None), "dispatcher", None)
    if dispatcher is not None:
        dispatcher.retry_interval = event_retry_interval
        dispatcher.delivery_deadline = event_delivery_timeout
        dispatcher.max_attempts = event_max_attempts

    import logging

    level_name = str((current or {}).get("log_level") or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    if init.logger is not None:
        init.logger.logger.setLevel(level)
        for handler in init.logger.logger.handlers:
            handler.setLevel(level)

    restart_paths = (
        ("bot_token", lambda config: (config or {}).get("bot_token")),
        (
            "plugins.root",
            lambda config: ((config or {}).get("plugins") or {}).get("root"),
        ),
        (
            "plugins.runtime_root",
            lambda config: ((config or {}).get("plugins") or {}).get("runtime_root"),
        ),
        (
            "plugins.catalog",
            lambda config: ((config or {}).get("plugins") or {}).get("catalog"),
        ),
        (
            "plugins.catalog_refresh_interval",
            lambda config: ((config or {}).get("plugins") or {}).get(
                "catalog_refresh_interval"
            ),
        ),
    )
    return [
        name
        for name, read in restart_paths
        if read(previous) != read(current)
    ]


def _enabled_feature_ids(manager) -> list[str]:
    plugin_ids = {
        item.plugin_id
        for item in manager.store.list_installed()
        if item.active
    }
    enabled = []
    for plugin_id in plugin_ids:
        release = manager.store.active(plugin_id)
        if release is not None and release.enabled:
            enabled.append(plugin_id)
    return sorted(enabled)


async def reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not init.check_user(update.effective_user.id):
        await send_bot_message_safely(
            context.bot,
            chat_id=update.effective_chat.id,
            text="⚠️ 当前账号无权使用此机器人。",
            parse_mode="html",
        )
        return
    previous = copy.deepcopy(init.bot_config or {})
    try:
        init.load_yaml_config(raise_on_error=True)
    except Exception as exc:
        if init.logger:
            init.logger.error(f"Core 配置读取失败: {type(exc).__name__}")
        await send_bot_message_safely(
            context.bot,
            chat_id=update.effective_chat.id,
            text="❌ Core 配置读取失败；继续使用重载前配置，Feature 未重载。",
        )
        return

    bot_data = getattr(context.application, "bot_data", {})
    manager = bot_data.get("telepiplex_plugin_manager")
    if manager is None:
        await send_bot_message_safely(
            context.bot,
            chat_id=update.effective_chat.id,
            text="❌ Core 配置已读取，但 Feature 管理器不可用；请重启 Core。",
        )
        return

    try:
        restart_fields = apply_hot_runtime_config(
            manager, previous, init.bot_config
        )
    except (TypeError, ValueError) as exc:
        init.bot_config = previous
        if init.logger:
            init.logger.error(f"Core 配置值无效: {type(exc).__name__}")
        await send_bot_message_safely(
            context.bot,
            chat_id=update.effective_chat.id,
            text="❌ Core 配置值无效；继续使用重载前配置，Feature 未重载。",
        )
        return
    feature_lines = []
    for plugin_id in _enabled_feature_ids(manager):
        try:
            await manager.reload_config(plugin_id)
        except Exception as exc:
            code = str(getattr(exc, "code", type(exc).__name__))
            message = str(getattr(exc, "message", ""))
            suffix = f"（{message}）" if message else ""
            feature_lines.append(f"❌ {plugin_id}：{code}{suffix}")
            if init.logger:
                init.logger.error(f"Feature 配置重载失败 [{plugin_id}]: {code}")
        else:
            feature_lines.append(f"✅ {plugin_id}")

    log_config_snapshot("配置已重新加载:")
    lines = ["✅ Core 配置已重新读取并应用可热更新项。", "", "Feature 重载结果："]
    lines.extend(feature_lines or ["- 无已启用 Feature"])
    if restart_fields:
        lines.extend([
            "",
            "⚠️ 以下 Core 配置需重启后生效：",
            ", ".join(restart_fields),
        ])
    await send_bot_message_safely(
        context.bot,
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
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


def get_bot_menu():
    return list(CORE_BOT_COMMANDS)


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
        update_task = (
            bot_data.pop(PLUGIN_UPDATE_TASK_KEY, None)
            if isinstance(bot_data, dict)
            else None
        )
        if update_task is not None:
            update_task.cancel()
            try:
                await update_task
            except asyncio.CancelledError:
                pass
        manager = bot_data.get("telepiplex_plugin_manager") if isinstance(bot_data, dict) else None
        if manager is not None:
            await manager.close()
        await application.shutdown()


def configure_application(application, manager):
    application.bot_data["telepiplex_plugin_manager"] = manager
    application.bot_data["telepiplex_plugin_router"] = manager.router
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reload", reload))
    application.add_handler(CommandHandler("plugin", plugin_command))
    application.add_handler(CallbackQueryHandler(
        plugin_install_callback,
        pattern=r"^core-plugin-install:",
    ))
    application.add_handler(CallbackQueryHandler(
        plugin_update_callback,
        pattern=r"^core-plugin-update:",
    ))
    register_feature_config_handlers(application)
    application.add_handler(CallbackQueryHandler(dynamic_callback_gateway))
    application.add_handler(MessageHandler(filters.COMMAND, dynamic_command_gateway))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dynamic_message_gateway))


async def start_core_runtime(application, manager):
    await manager.start()
    queue_core_startup_notice()
    plugin_config = (init.bot_config or {}).get("plugins") or {}
    monitor = PluginUpdateMonitor(
        manager,
        lambda update: send_plugin_update_notification(application, update),
        interval=float(plugin_config.get("catalog_refresh_interval") or 21600),
        logger=init.logger,
    )
    application.bot_data[PLUGIN_UPDATE_TASK_KEY] = asyncio.create_task(
        monitor.run(),
        name="telepiplex-plugin-update-monitor",
    )


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
    plugin_manager = build_plugin_manager(init.bot_config)
    configure_application(application, plugin_manager)

    try:
        asyncio.run(run_application_polling(
            application,
            after_start=lambda: start_core_runtime(application, plugin_manager),
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

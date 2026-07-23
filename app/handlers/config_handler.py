from __future__ import annotations

import re
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler
from telegram.warnings import PTBUserWarning

import init
from app.handlers.plugin_handler import ROUTER_KEY, handle_feature_result


CONFIG_SELECT_PLUGIN = 80
MANAGER_KEY = "telepiplex_plugin_manager"
_SESSION_KEYS = ("host_config_plugins",)
_DIRECT_CALLBACK_RE = re.compile(
    r"^host-config-direct:(?P<plugin_id>[a-z][a-z0-9-]{0,63})$"
)


def _safe_error(value) -> str:
    return re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=***redacted***",
        str(value),
    )[:1000]


def clear_config_session(user_data: dict):
    for key in _SESSION_KEYS:
        user_data.pop(key, None)


def _state_label(state: dict) -> str:
    name = str(state.get("state") or "")
    if name == "configurable":
        return "可配置"
    if name == "invalid_config":
        return "invalid_config"
    if name == "invalid_schema":
        return "invalid_schema"
    if name == "invalid_declaration":
        return "配置入口声明无效"
    if name == "not_configurable":
        return "未提供独立配置向导"
    if name == "route_unavailable":
        missing = "、".join(state.get("missing_capabilities") or [])
        return f"route_unavailable：{missing}" if missing else "route_unavailable"
    return str(state.get("error_code") or name or "unknown")


async def _show_config_menu(update, context, *, edit: bool):
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        text = "❌ Feature 插件管理器尚未初始化。"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.effective_message.reply_text(text)
        return ConversationHandler.END

    plugin_ids = []
    lines = []
    for status in manager.doctor():
        plugin_id = str(status.get("plugin_id") or "")
        if not plugin_id:
            continue
        try:
            state = manager.config_state(plugin_id)
        except Exception as exc:
            state = {
                "plugin_id": plugin_id,
                "version": status.get("version") or "",
                "state": "config_state_failed",
                "configurable": False,
                "error_code": getattr(exc, "code", "config_state_failed"),
            }
        version = str(state.get("version") or status.get("version") or "-")
        lines.append(f"• {plugin_id} {version}（{_state_label(state)}）")
        if state.get("configurable"):
            plugin_ids.append(plugin_id)

    clear_config_session(context.user_data)
    context.user_data["host_config_plugins"] = plugin_ids
    if lines:
        text = "Feature 配置状态：\n\n" + "\n".join(lines)
    else:
        text = "当前没有已安装的 Feature。"

    rows = [
        [InlineKeyboardButton(
            f"配置 {plugin_id}",
            callback_data=f"host-config-plugin:{index}",
        )]
        for index, plugin_id in enumerate(plugin_ids)
    ]
    if rows:
        rows.append([InlineKeyboardButton("取消", callback_data="host-config-cancel")])
    markup = InlineKeyboardMarkup(rows) if rows else None
    kwargs = {"reply_markup": markup} if markup is not None else {}
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, **kwargs)
    else:
        await update.effective_message.reply_text(text, **kwargs)
    return CONFIG_SELECT_PLUGIN if plugin_ids else ConversationHandler.END


async def config_command(update, context):
    if not init.check_user(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    return await _show_config_menu(update, context, edit=False)


async def config_open_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    return await _show_config_menu(update, context, edit=True)


def _callback_index(data: str) -> int:
    try:
        index = int(str(data or "").removeprefix("host-config-plugin:"))
    except ValueError:
        return -1
    return index


async def _dispatch_config(plugin_id: str, update, context):
    manager = context.application.bot_data.get(MANAGER_KEY)
    router = context.application.bot_data.get(ROUTER_KEY)
    try:
        state = manager.config_state(plugin_id)
        route = router.plugin_route(plugin_id) if router is not None else None
        command = str(state.get("command") or "")
        if not state.get("configurable") or route is None or not command:
            raise RuntimeError(state.get("error_code") or "config_unavailable")
        result = await route.client.request(
            "command.dispatch",
            {
                "command": command,
                "args": [],
                "text": "/config",
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "update_id": getattr(update, "update_id", None),
            },
            deadline=30,
            idempotency_key=f"telegram:{getattr(update, 'update_id', '')}:config",
        )
        await handle_feature_result(update, context, route, result)
    except Exception:
        await update.callback_query.edit_message_text(
            "❌ custom_config_failed：Feature 独立配置向导暂时不可用。"
        )
    clear_config_session(context.user_data)
    return ConversationHandler.END


async def select_config_plugin(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    index = _callback_index(query.data)
    plugin_ids = context.user_data.get("host_config_plugins") or []
    if index < 0 or index >= len(plugin_ids):
        await query.edit_message_text("❌ 配置会话已失效，请重新发送 /config。")
        return ConversationHandler.END
    return await _dispatch_config(plugin_ids[index], update, context)


async def direct_config_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    match = _DIRECT_CALLBACK_RE.fullmatch(str(query.data or ""))
    if match is None:
        await query.edit_message_text("❌ 配置入口无效。")
        return ConversationHandler.END
    return await _dispatch_config(match.group("plugin_id"), update, context)


async def quit_config_conversation(update, context):
    clear_config_session(context.user_data)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("已取消 Feature 配置。")
    else:
        await update.effective_message.reply_text("已取消 Feature 配置。")
    return ConversationHandler.END


def register_feature_config_handlers(application):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PTBUserWarning)
        handler = ConversationHandler(
            entry_points=[
                CommandHandler("config", config_command),
                CallbackQueryHandler(config_open_callback, pattern=r"^host-config-open$"),
                CallbackQueryHandler(
                    direct_config_callback,
                    pattern=r"^host-config-direct:[a-z][a-z0-9-]{0,63}$",
                ),
            ],
            states={
                CONFIG_SELECT_PLUGIN: [
                    CallbackQueryHandler(
                        select_config_plugin,
                        pattern=r"^host-config-plugin:\d+$",
                    ),
                    CallbackQueryHandler(
                        quit_config_conversation,
                        pattern=r"^host-config-cancel$",
                    ),
                ],
            },
            fallbacks=[CommandHandler("q", quit_config_conversation)],
            allow_reentry=True,
        )
    application.add_handler(handler)

from __future__ import annotations

import re
from pathlib import Path

import init

from app.core.plugin_manager import PluginOperationError


MANAGER_KEY = "telepiplex_plugin_manager"
ROUTER_KEY = "telepiplex_plugin_router"
_USAGE = (
    "用法：\n"
    "/plugin install <artifact.tpx>\n"
    "/plugin update <artifact.tpx>\n"
    "/plugin enable <plugin_id>\n"
    "/plugin disable <plugin_id>\n"
    "/plugin rollback <plugin_id>\n"
    "/plugin remove <plugin_id>\n"
    "/plugin status <plugin_id>\n"
    "/plugin doctor"
)
_SAFE_ACTIONS = {"send_message", "edit_message"}


def _safe_error(value) -> str:
    text = re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=***redacted***",
        str(value),
    )
    return text[:1000]


async def plugin_command(update, context):
    message = update.effective_message
    if not init.check_user(update.effective_user.id):
        await message.reply_text("⚠️ 当前账号无权管理 Feature 插件。")
        return
    args = list(context.args or [])
    if not args:
        await message.reply_text(_USAGE)
        return
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        await message.reply_text("❌ Feature 插件管理器尚未初始化。")
        return
    command = str(args[0]).lower()
    try:
        if command in {"install", "update", "enable", "disable", "rollback", "remove"}:
            if len(args) != 2:
                await message.reply_text(_USAGE)
                return
            value = Path(args[1]) if command in {"install", "update"} else str(args[1])
            await message.reply_text(f"⏳ Feature {command} 处理中：{args[1]}")
            result = await getattr(manager, command)(value)
            await message.reply_text(
                f"✅ {result.message}\n"
                f"插件：{result.plugin_id}\n"
                f"版本：{result.version}\n"
                f"状态：{result.state}"
            )
            return
        if command == "status" and len(args) == 2:
            await message.reply_text(_format_status(manager.status(str(args[1]))))
            return
        if command == "doctor" and len(args) == 1:
            statuses = manager.doctor()
            if not statuses:
                await message.reply_text("当前没有已安装的 Feature。")
            else:
                await message.reply_text("\n\n".join(_format_status(item) for item in statuses))
            return
        await message.reply_text(_USAGE)
    except PluginOperationError as exc:
        await message.reply_text(f"❌ {exc.code}：{_safe_error(exc)}")
    except Exception as exc:
        await message.reply_text(f"❌ plugin_operation_failed：{type(exc).__name__}")


def _format_status(status: dict) -> str:
    plugin_id = str(status.get("plugin_id") or "unknown")
    state = str(status.get("state") or "unknown")
    version = str(status.get("version") or "-")
    lines = [f"Feature：{plugin_id}", f"版本：{version}", f"状态：{state}"]
    missing = status.get("missing_capabilities") or []
    if missing:
        lines.append("缺少能力：" + "、".join(str(item) for item in missing))
    return "\n".join(lines)


async def dynamic_command_gateway(update, context):
    if not init.check_user(update.effective_user.id):
        return
    text = str(update.effective_message.text or "")
    first, *args = text.split()
    command = first.lstrip("/").split("@", 1)[0].lower()
    if not command or command == "plugin":
        return
    router = context.application.bot_data.get(ROUTER_KEY)
    route = router.command_route(command) if router is not None else None
    if route is None:
        return
    try:
        result = await route.client.request(
            "command.dispatch",
            {
                "command": command,
                "args": args,
                "text": text,
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "update_id": getattr(update, "update_id", None),
            },
            deadline=30,
            idempotency_key=f"telegram:{getattr(update, 'update_id', '')}",
        )
        await _render_actions(update, context, result)
    except Exception as exc:
        code = getattr(exc, "code", "feature_command_failed")
        await update.effective_message.reply_text(f"❌ {code}：{_safe_error(exc)}")


async def dynamic_callback_gateway(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        return
    data = str(query.data or "")
    namespace, separator, payload = data.partition(":")
    if not separator:
        return
    router = context.application.bot_data.get(ROUTER_KEY)
    route = router.callback_route(namespace) if router is not None else None
    if route is None:
        return
    try:
        result = await route.client.request(
            "callback.dispatch",
            {
                "namespace": namespace,
                "payload": payload,
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "update_id": getattr(update, "update_id", None),
            },
            deadline=30,
            idempotency_key=f"telegram:{getattr(update, 'update_id', '')}",
        )
        await _render_actions(update, context, result)
    except Exception as exc:
        code = getattr(exc, "code", "feature_callback_failed")
        await update.effective_message.reply_text(f"❌ {code}：{_safe_error(exc)}")


async def _render_actions(update, context, result: dict):
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list) or len(actions) > 20:
        await update.effective_message.reply_text("❌ Feature 返回了无效响应。")
        return
    for action in actions:
        if not isinstance(action, dict) or action.get("kind") not in _SAFE_ACTIONS:
            await update.effective_message.reply_text("❌ Feature 返回了无效响应。")
            return
        text = str(action.get("text") or "")
        if not text:
            await update.effective_message.reply_text("❌ Feature 返回了无效响应。")
            return
        if len(text) > 4096:
            text = text[:4075].rstrip() + "\n…内容已截断"
        parse_mode = action.get("parse_mode")
        if parse_mode not in {None, "HTML", "MarkdownV2"}:
            parse_mode = None
        kwargs = {"parse_mode": parse_mode} if parse_mode else {}
        if action["kind"] == "send_message":
            await update.effective_message.reply_text(text, **kwargs)
        else:
            await update.effective_message.edit_text(text, **kwargs)

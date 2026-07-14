from __future__ import annotations

from copy import deepcopy
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import init

from app.core.plugin_manager import PluginOperationError


MANAGER_KEY = "telepiplex_plugin_manager"
ROUTER_KEY = "telepiplex_plugin_router"
SESSION_KEY = "telepiplex_plugin_sessions"
SESSION_TTL_SECONDS = 30 * 60
_USAGE = (
    "用法：\n"
    "/plugin install <name@version|artifact.tpx>\n"
    "/plugin update <name@version|artifact.tpx>\n"
    "/plugin enable <plugin_id>\n"
    "/plugin disable <plugin_id>\n"
    "/plugin rollback <plugin_id>\n"
    "/plugin remove <plugin_id>\n"
    "/plugin status <plugin_id>\n"
    "/plugin doctor"
)
_SAFE_ACTIONS = {"send_message", "edit_message"}
_CORE_UPDATE_CALLBACK_RE = re.compile(
    r"^core-plugin-update:(?P<action>confirm|decline):"
    r"(?P<reference>[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$"
)
_CORE_INSTALL_CALLBACK_RE = re.compile(
    r"^core-plugin-install:confirm:"
    r"(?P<reference>[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$"
)


def _config_markup(manager, plugin_id: str):
    try:
        state = manager.config_state(plugin_id)
    except Exception:
        return None
    if not state.get("configurable"):
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"配置 {plugin_id}",
            callback_data=f"core-config-direct:{plugin_id}",
        )
    ]])


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
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        await message.reply_text("❌ Feature 插件管理器尚未初始化。")
        return
    try:
        if not args:
            await _show_feature_overview(message, manager)
            return
        command = str(args[0]).lower()
        if command in {"install", "update", "enable", "disable", "rollback", "remove"}:
            if len(args) != 2:
                await message.reply_text(_USAGE)
                return
            value = str(args[1])
            await message.reply_text(f"⏳ Feature {command} 处理中：{args[1]}")
            result = await getattr(manager, command)(value)
            if command in {
                "install", "update", "enable", "disable", "rollback", "remove"
            }:
                _clear_plugin_sessions(context.application.bot_data, result.plugin_id)
                _clear_config_user_data(context.user_data)
            kwargs = {}
            if command in {"install", "update", "enable", "rollback"}:
                markup = _config_markup(manager, result.plugin_id)
                if markup is not None:
                    kwargs["reply_markup"] = markup
            await message.reply_text(
                f"✅ {result.message}\n"
                f"插件：{result.plugin_id}\n"
                f"版本：{result.version}\n"
                f"状态：{result.state}",
                **kwargs,
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


async def _show_feature_overview(message, manager):
    statuses = manager.doctor()
    rows = []
    updates = []
    candidates = []
    catalog_errors = []
    if statuses:
        try:
            updates = await manager.available_updates()
        except Exception as exc:
            catalog_errors.append(str(
                getattr(exc, "code", "catalog_unavailable")
            ))
    try:
        candidates = await manager.available_plugins()
    except Exception as exc:
        catalog_errors.append(str(
            getattr(exc, "code", "catalog_unavailable")
        ))

    lines = ["Feature 管理"]
    if statuses:
        lines.append("\n已安装：")
        for status in statuses:
            lines.append(
                f"• {status.get('plugin_id', 'unknown')} "
                f"{status.get('version', '-')}（{status.get('state', 'unknown')}）"
            )
        rows.append([InlineKeyboardButton(
            "配置 Feature",
            callback_data="core-config-open",
        )])
    else:
        lines.append("\n已安装：无")

    if updates:
        lines.append("\n可更新：")
        for item in updates:
            lines.append(
                f"• {item.plugin_id} {item.current_version} → "
                f"{item.target_version}"
            )
            callback_data = (
                f"core-plugin-update:confirm:{item.reference}"
            )
            if len(callback_data.encode("utf-8")) <= 64:
                rows.append([InlineKeyboardButton(
                    f"更新 {item.plugin_id} 到 {item.target_version}",
                    callback_data=callback_data,
                )])

    if candidates:
        lines.append("\n可安装：")
        for candidate in candidates:
            if candidate.ready:
                lines.append(
                    f"• {candidate.plugin_id} {candidate.target_version}（可安装）"
                )
                callback_data = (
                    f"core-plugin-install:confirm:{candidate.reference}"
                )
                if len(callback_data.encode("utf-8")) <= 64:
                    rows.append([InlineKeyboardButton(
                        f"安装 {candidate.plugin_id} {candidate.target_version}",
                        callback_data=callback_data,
                    )])
            elif candidate.dependency_plugins:
                lines.append(
                    f"• {candidate.plugin_id} {candidate.target_version}"
                    f"（先安装：{'、'.join(candidate.dependency_plugins)}）"
                )
            else:
                lines.append(
                    f"• {candidate.plugin_id} {candidate.target_version}"
                    f"（缺少能力：{'、'.join(candidate.missing_capabilities)}）"
                )
    elif not catalog_errors:
        lines.append("\n当前没有可安装的兼容稳定版本。")

    if catalog_errors:
        safe_codes = "、".join(dict.fromkeys(
            _safe_error(code) for code in catalog_errors
        ))
        lines.append(f"\n发布目录部分不可用：{safe_codes}")

    lines.append(
        "\n手动入口：/plugin install <name@version|artifact.tpx>"
    )
    kwargs = {}
    if rows:
        kwargs["reply_markup"] = InlineKeyboardMarkup(rows)
    await message.reply_text("\n".join(lines), **kwargs)


async def plugin_install_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权管理 Feature 插件。")
        return

    match = _CORE_INSTALL_CALLBACK_RE.fullmatch(str(query.data or ""))
    if match is None:
        await query.edit_message_text("❌ invalid_install_callback：安装请求无效。")
        return
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        await query.edit_message_text("❌ Feature 插件管理器尚未初始化。")
        return

    reference = match.group("reference")
    try:
        await query.edit_message_text(f"⏳ Feature 安装处理中：{reference}")
        result = await manager.install(reference)
        _clear_plugin_sessions(context.application.bot_data, result.plugin_id)
        _clear_config_user_data(context.user_data)
        kwargs = {}
        markup = _config_markup(manager, result.plugin_id)
        if markup is not None:
            kwargs["reply_markup"] = markup
        await query.edit_message_text(
            f"✅ {result.message}\n"
            f"插件：{result.plugin_id}\n"
            f"版本：{result.version}\n"
            f"状态：{result.state}\n\n"
            "发送 /plugin 继续安装其他 Feature。",
            **kwargs,
        )
    except PluginOperationError as exc:
        await query.edit_message_text(f"❌ {exc.code}：{_safe_error(exc)}")
    except Exception as exc:
        await query.edit_message_text(
            f"❌ plugin_operation_failed：{type(exc).__name__}"
        )


async def plugin_update_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权管理 Feature 插件。")
        return

    match = _CORE_UPDATE_CALLBACK_RE.fullmatch(str(query.data or ""))
    if match is None:
        await query.edit_message_text("❌ invalid_update_callback：更新请求无效。")
        return

    reference = match.group("reference")
    if match.group("action") == "decline":
        await query.edit_message_text(f"已暂不更新 Feature：{reference}")
        return

    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        await query.edit_message_text("❌ Feature 插件管理器尚未初始化。")
        return

    try:
        await query.edit_message_text(f"⏳ Feature 更新处理中：{reference}")
        result = await manager.update(reference)
        _clear_plugin_sessions(context.application.bot_data, result.plugin_id)
        _clear_config_user_data(context.user_data)
        kwargs = {}
        markup = _config_markup(manager, result.plugin_id)
        if markup is not None:
            kwargs["reply_markup"] = markup
        await query.edit_message_text(
            f"✅ {result.message}\n"
            f"插件：{result.plugin_id}\n"
            f"版本：{result.version}\n"
            f"状态：{result.state}",
            **kwargs,
        )
    except PluginOperationError as exc:
        await query.edit_message_text(f"❌ {exc.code}：{_safe_error(exc)}")
    except Exception as exc:
        await query.edit_message_text(
            f"❌ plugin_operation_failed：{type(exc).__name__}"
        )


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
        await handle_feature_result(update, context, route, result)
    except Exception as exc:
        code = getattr(exc, "code", "feature_command_failed")
        await update.effective_message.reply_text(f"❌ {code}：{_safe_error(exc)}")


async def dynamic_callback_gateway(update, context):
    query = update.callback_query
    await query.answer(text="处理中...")
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
        await handle_feature_result(update, context, route, result)
    except Exception as exc:
        code = getattr(exc, "code", "feature_callback_failed")
        await _feature_feedback(
            update,
            f"❌ {code}：{_safe_error(exc)}",
            prefer_edit=True,
        )


async def dynamic_message_gateway(update, context):
    if not init.check_user(update.effective_user.id):
        return
    bot_data = context.application.bot_data
    sessions = bot_data.get(SESSION_KEY)
    if not isinstance(sessions, dict):
        return
    key = _session_key(update)
    session = sessions.get(key)
    if not isinstance(session, dict):
        return
    if float(session.get("expires_at") or 0) <= time.time():
        _drop_session(bot_data, key)
        await update.effective_message.reply_text("⚠️ Feature 会话已超时，请重新发起命令。")
        return
    router = bot_data.get(ROUTER_KEY)
    route = router.plugin_route(str(session.get("plugin_id") or "")) if router is not None else None
    if route is None:
        _drop_session(bot_data, key)
        await update.effective_message.reply_text("⚠️ Feature 已停用或更新，本次会话已结束。")
        return
    try:
        result = await route.client.request(
            "message.dispatch",
            {
                "text": str(update.effective_message.text or ""),
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "update_id": getattr(update, "update_id", None),
            },
            deadline=30,
            idempotency_key=f"telegram:{getattr(update, 'update_id', '')}",
        )
        await handle_feature_result(update, context, route, result)
    except Exception as exc:
        code = getattr(exc, "code", "feature_message_failed")
        await update.effective_message.reply_text(f"❌ {code}：{_safe_error(exc)}")


async def handle_feature_result(update, context, route, result: dict):
    if isinstance(result, dict) and "config_patch" in result:
        await _apply_feature_config_patch(update, context, route, result)
        return
    if not await _render_actions(update, context, route, result):
        return
    session = result.get("session") if isinstance(result, dict) else None
    if session is None:
        return
    if not isinstance(session, dict) or session.get("state") not in {"open", "close"}:
        await _feature_feedback(
            update,
            "❌ Feature 返回了无效会话状态。",
            prefer_edit=bool(getattr(update, "callback_query", None)),
        )
        return
    key = _session_key(update)
    if session["state"] == "open":
        sessions = context.application.bot_data.setdefault(SESSION_KEY, {})
        sessions[key] = {
            "plugin_id": route.plugin_id,
            "expires_at": time.time() + SESSION_TTL_SECONDS,
        }
    else:
        _drop_session(context.application.bot_data, key)


def merge_nested_patch(current: dict, patch: dict) -> dict:
    result = deepcopy(current if isinstance(current, dict) else {})
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_nested_patch(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


async def _apply_feature_config_patch(update, context, route, result: dict):
    patch = result.get("config_patch")
    prefer_edit = bool(getattr(update, "callback_query", None))
    if not isinstance(patch, dict) or not patch:
        await _feature_feedback(
            update,
            "❌ invalid_config_patch：Feature 配置补丁无效。",
            prefer_edit=prefer_edit,
        )
        return
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        await _feature_feedback(
            update,
            "❌ config_manager_unavailable：Feature 插件管理器尚未初始化。",
            prefer_edit=prefer_edit,
        )
        return
    await _feature_feedback(
        update,
        f"⏳ 正在保存并重新加载 {route.plugin_id} 配置...",
        prefer_edit=prefer_edit,
    )
    try:
        view = manager.config(route.plugin_id)
        configured = merge_nested_patch(view.get("config") or {}, patch)
        outcome = await manager.configure(route.plugin_id, configured)
    except PluginOperationError as exc:
        await _feature_feedback(
            update,
            f"❌ {exc.code}：配置未写入或重新加载失败。",
            prefer_edit=prefer_edit,
        )
        return
    except Exception as exc:
        await _feature_feedback(
            update,
            f"❌ config_failed：{type(exc).__name__}",
            prefer_edit=prefer_edit,
        )
        return
    _drop_session(context.application.bot_data, _session_key(update))
    await _feature_feedback(
        update,
        f"✅ {outcome.plugin_id} 配置已写入并重新加载。",
        prefer_edit=prefer_edit,
    )


async def _render_actions(update, context, route, result: dict) -> bool:
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list) or len(actions) > 20:
        await _feature_feedback(
            update,
            "❌ Feature 返回了无效响应。",
            prefer_edit=bool(getattr(update, "callback_query", None)),
        )
        return False
    for action in actions:
        if not isinstance(action, dict) or action.get("kind") not in _SAFE_ACTIONS:
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False
        text = str(action.get("text") or "")
        if not text:
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False
        if len(text) > 4096:
            text = text[:4075].rstrip() + "\n…内容已截断"
        parse_mode = action.get("parse_mode")
        if parse_mode not in {None, "HTML", "MarkdownV2"}:
            parse_mode = None
        kwargs = {"parse_mode": parse_mode} if parse_mode else {}
        reply_markup = _keyboard_markup(route, action.get("data"))
        if reply_markup is False:
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if action["kind"] == "send_message":
            await update.effective_message.reply_text(text, **kwargs)
        else:
            await update.effective_message.edit_text(text, **kwargs)
    return True


def _keyboard_markup(route, data):
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) - {"keyboard"}:
        return False
    keyboard = data.get("keyboard")
    if not isinstance(keyboard, list) or not keyboard or len(keyboard) > 10:
        return False
    namespaces = set(getattr(getattr(route, "manifest", None), "callbacks", ()))
    rows = []
    for row in keyboard:
        if not isinstance(row, list) or not row or len(row) > 8:
            return False
        buttons = []
        for button in row:
            if not isinstance(button, dict) or set(button) != {"text", "callback_data"}:
                return False
            text = str(button.get("text") or "")
            callback_data = str(button.get("callback_data") or "")
            namespace, separator, _payload = callback_data.partition(":")
            if (
                not text
                or not separator
                or namespace not in namespaces
                or len(callback_data.encode("utf-8")) > 64
            ):
                return False
            buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        rows.append(buttons)
    return InlineKeyboardMarkup(rows)


async def _feature_feedback(update, text: str, *, prefer_edit: bool = False):
    query = getattr(update, "callback_query", None)
    if prefer_edit and query is not None and hasattr(query, "edit_message_text"):
        await query.edit_message_text(text)
        return
    await update.effective_message.reply_text(text)


def _session_key(update):
    return (int(update.effective_chat.id), int(update.effective_user.id))


def _drop_session(bot_data: dict, key):
    sessions = bot_data.get(SESSION_KEY)
    if not isinstance(sessions, dict):
        return
    sessions.pop(key, None)
    if not sessions:
        bot_data.pop(SESSION_KEY, None)


def _clear_plugin_sessions(bot_data: dict, plugin_id: str):
    sessions = bot_data.get(SESSION_KEY)
    if not isinstance(sessions, dict):
        return
    for key, session in list(sessions.items()):
        if isinstance(session, dict) and session.get("plugin_id") == str(plugin_id):
            sessions.pop(key, None)
    if not sessions:
        bot_data.pop(SESSION_KEY, None)


def _clear_config_user_data(user_data: dict):
    for key in list(user_data):
        if str(key).startswith("core_config_"):
            user_data.pop(key, None)

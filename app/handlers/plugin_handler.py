from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import threading
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init

from app.runtime.plugin_manager import PluginOperationError
from app.runtime.interaction_coordinator import TERMINAL_STATES
from app.runtime.command_catalog import sync_bot_commands
from app.runtime.poster_grid import build_poster_grid
from app.runtime.telegram_text import bounded_photo_caption
from app.utils.log_sanitizer import sanitize_log_value
from app.handlers.interaction_handler import (
    CONFIG_OPERATION_TASKS_KEY,
    COORDINATOR_KEY,
    callback_dispatch_data,
    deduplicate_terminal_controls,
    operation_markup,
    operation_accepts_text,
    operation_render_lock,
    release_callback_dispatch,
    render_operation,
)


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
_SAFE_ACTIONS = {
    "send_message",
    "edit_message",
    "send_photo",
    "edit_photo",
    "send_photo_grid",
}
_HOST_UPDATE_CALLBACK_RE = re.compile(
    r"^host-plugin-update:(?P<action>confirm|decline):"
    r"(?P<reference>[a-z][a-z0-9-]{0,63}@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$"
)
_HOST_INSTALL_CALLBACK_RE = re.compile(
    r"^host-plugin-install:confirm:"
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
            callback_data=f"host-config-direct:{plugin_id}",
        )
    ]])


def _safe_error(value) -> str:
    text = re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=***redacted***",
        str(value),
    )
    return text[:1000]


def _log_feature_event(
    level: str,
    event: str,
    update,
    route=None,
    **fields,
) -> None:
    logger = getattr(init, "logger", None)
    if logger is None:
        return
    method = getattr(logger, level, None)
    if not callable(method) and level == "warning":
        method = getattr(logger, "warn", None)
    if not callable(method):
        method = getattr(logger, "info", None)
    if not callable(method):
        return
    base_fields = {
        "plugin_id": str(getattr(route, "plugin_id", "") or ""),
        "update_id": getattr(update, "update_id", None),
        "chat_id": getattr(
            getattr(update, "effective_chat", None),
            "id",
            None,
        ),
        "user_id": getattr(
            getattr(update, "effective_user", None),
            "id",
            None,
        ),
    }
    base_fields.update(fields)
    parts = [f"event={sanitize_log_value(event, max_chars=120)}"]
    for key in sorted(base_fields):
        value = base_fields[key]
        if value is None or value == "":
            continue
        parts.append(
            f"{key}={sanitize_log_value(value, max_chars=500)}"
        )
    method(" ".join(parts))


def _log_invalid_feature_response(
    update,
    route,
    reason: str,
    *,
    action_index: int | None = None,
    operation_record=None,
) -> None:
    _log_feature_event(
        "warning",
        "feature.response_invalid",
        update,
        route,
        reason=reason,
        action_index=action_index,
        operation_id=str(
            getattr(operation_record, "operation_id", "") or ""
        ),
    )


def _log_delivered_feature_action(
    update,
    route,
    *,
    operation_record,
    action_index: int,
    requested_action: str,
    delivered_action: str,
    text: str,
    parse_mode: str | None,
    action_data,
    message_id: int | None,
    message_kind: str,
) -> None:
    logger = getattr(init, "logger", None)
    method = getattr(logger, "info", None) if logger is not None else None
    if not callable(method):
        return
    method(
        "Feature 前台消息已送达",
        event_name="telegram.feature_action.delivered",
        diagnostic_fields={
            "stage": "telegram_delivery",
            "status": "completed",
            "input": {
                "plugin_id": str(getattr(route, "plugin_id", "") or ""),
                "action_index": int(action_index),
                "requested_action": str(requested_action),
                "update_id": getattr(update, "update_id", None),
                "chat_id": getattr(
                    getattr(update, "effective_chat", None), "id", None
                ),
                "user_id": getattr(
                    getattr(update, "effective_user", None), "id", None
                ),
                "operation_id": str(
                    getattr(operation_record, "operation_id", "") or ""
                ) or None,
            },
            "user_surface": {
                "direction": "outgoing",
                "action": str(delivered_action),
                "text": str(text),
                "parse_mode": parse_mode,
                "data": deepcopy(action_data),
            },
            "output": {
                "message_id": message_id,
                "message_kind": str(message_kind),
            },
        },
    )


def _config_migration_suffix(result) -> str:
    details = getattr(result, "details", {}) or {}
    keys = details.get("config_added_keys") or []
    safe_keys = [str(key)[:100] for key in keys if str(key).strip()][:20]
    removed = details.get("config_removed_keys") or []
    safe_removed = [str(key)[:100] for key in removed if str(key).strip()][:20]
    lines = []
    if safe_keys:
        lines.append("新增配置项：" + "、".join(safe_keys))
    if safe_removed:
        lines.append("已移除过期配置项：" + "、".join(safe_removed))
    return ("\n" + "\n".join(lines)) if lines else ""


def _config_error_suffix(error) -> str:
    details = getattr(error, "details", {}) or {}
    paths = details.get("config_error_paths") or []
    safe_paths = []
    for path in paths:
        text = str(path).strip()
        if (
            text not in safe_paths
            and re.fullmatch(r"[A-Za-z0-9_.\-\[\]]{1,100}", text)
        ):
            safe_paths.append(text)
        if len(safe_paths) >= 20:
            break
    if not safe_paths:
        return ""
    return "\n请检查配置项：" + "、".join(safe_paths)


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
            if command in {"disable", "remove"}:
                await _interrupt_unowned_operations(context)
            menu_suffix = await _sync_command_menu(context)
            kwargs = {}
            if command in {"install", "update", "enable", "rollback"}:
                markup = _config_markup(manager, result.plugin_id)
                if markup is not None:
                    kwargs["reply_markup"] = markup
            await message.reply_text(
                f"✅ {result.message}\n"
                f"插件：{result.plugin_id}\n"
                f"版本：{result.version}\n"
                f"状态：{result.state}"
                f"{_config_migration_suffix(result)}"
                f"{menu_suffix}",
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
        await message.reply_text(
            f"❌ {exc.code}：{_safe_error(exc)}{_config_error_suffix(exc)}"
        )
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
            callback_data="host-config-open",
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
                f"host-plugin-update:confirm:{item.reference}"
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
                    f"host-plugin-install:confirm:{candidate.reference}"
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

    match = _HOST_INSTALL_CALLBACK_RE.fullmatch(str(query.data or ""))
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
        menu_suffix = await _sync_command_menu(context)
        kwargs = {}
        markup = _config_markup(manager, result.plugin_id)
        if markup is not None:
            kwargs["reply_markup"] = markup
        await query.edit_message_text(
            f"✅ {result.message}\n"
            f"插件：{result.plugin_id}\n"
            f"版本：{result.version}\n"
            f"状态：{result.state}\n\n"
            "发送 /plugin 继续安装其他 Feature。"
            f"{menu_suffix}",
            **kwargs,
        )
    except PluginOperationError as exc:
        await query.edit_message_text(
            f"❌ {exc.code}：{_safe_error(exc)}{_config_error_suffix(exc)}"
        )
    except Exception as exc:
        await query.edit_message_text(
            f"❌ plugin_operation_failed：{type(exc).__name__}"
        )


async def plugin_update_callback(update, context):
    query = update.callback_query
    try:
        await query.answer()
    except Exception as exc:
        _log_feature_event(
            "warning",
            "feature.update_callback_answer_failed",
            update,
            None,
            error_code=type(exc).__name__,
        )
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权管理 Feature 插件。")
        return

    match = _HOST_UPDATE_CALLBACK_RE.fullmatch(str(query.data or ""))
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
        menu_suffix = await _sync_command_menu(context)
        kwargs = {}
        markup = _config_markup(manager, result.plugin_id)
        if markup is not None:
            kwargs["reply_markup"] = markup
        await query.edit_message_text(
            f"✅ {result.message}\n"
            f"插件：{result.plugin_id}\n"
            f"版本：{result.version}\n"
            f"状态：{result.state}"
            f"{_config_migration_suffix(result)}"
            f"{menu_suffix}",
            **kwargs,
        )
    except PluginOperationError as exc:
        await query.edit_message_text(
            f"❌ {exc.code}：{_safe_error(exc)}{_config_error_suffix(exc)}"
        )
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


async def _sync_command_menu(context) -> str:
    router = context.application.bot_data.get(ROUTER_KEY)
    if router is None:
        return ""
    if await sync_bot_commands(context.application, router):
        return ""
    return (
        "\n\n⚠️ Telegram 命令列表同步失败；Feature 操作已完成且不会回滚，"
        "Host 会在下次生命周期变更或重启时重试。"
    )


async def _interrupt_unowned_operations(context):
    bot_data = context.application.bot_data
    coordinator = bot_data.get(COORDINATOR_KEY)
    router = bot_data.get(ROUTER_KEY)
    if coordinator is None or router is None:
        return
    snapshot = getattr(router, "snapshot", None)
    plugin_ids = getattr(snapshot, "plugin_ids", ())
    active_plugin_ids = {
        str(plugin_id)
        for plugin_id in plugin_ids
        if str(plugin_id).strip()
    }
    for record in coordinator.interrupt_unowned(active_plugin_ids):
        await render_operation(context.application, router, record)


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
    coordinator = context.application.bot_data.get(COORDINATOR_KEY)
    encoded_data = str(getattr(query, "data", "") or "")
    release_task = None
    release_completed = False
    released_segment = None

    async def release_claim():
        nonlocal release_task, release_completed, released_segment
        if release_completed:
            return released_segment
        if release_task is None:
            release_task = asyncio.create_task(
                release_callback_dispatch(
                    update,
                    context.application,
                    coordinator,
                ),
                name=(
                    "telepiplex-callback-release-"
                    f"{getattr(update, 'update_id', '')}"
                ),
            )
        released_segment = await asyncio.shield(release_task)
        release_completed = True
        return released_segment

    async def rerender_released_segment():
        if released_segment is None or coordinator is None:
            return
        current = coordinator.get(released_segment.operation_id)
        if (
            current is None
            or current.active_segment_id != released_segment.segment_id
        ):
            return
        await render_operation(
            context.application,
            context.application.bot_data.get(ROUTER_KEY),
            current,
        )

    try:
        if not init.check_user(update.effective_user.id):
            return
        data = callback_dispatch_data(
            update,
            coordinator,
        )
        if data is None:
            return
        if data == encoded_data:
            try:
                await query.answer(text="处理中...")
            except Exception:
                pass

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
            await release_claim()
            await handle_feature_result(update, context, route, result)
        except Exception as exc:
            await release_claim()
            code = getattr(exc, "code", "feature_callback_failed")
            await _feature_feedback(
                update,
                f"❌ {code}：{_safe_error(exc)}",
                prefer_edit=True,
            )
    finally:
        await release_claim()
        await rerender_released_segment()


async def dynamic_message_gateway(update, context):
    if not init.check_user(update.effective_user.id):
        return
    bot_data = context.application.bot_data
    sessions = bot_data.get(SESSION_KEY)
    key = _session_key(update)
    router = bot_data.get(ROUTER_KEY)
    session = sessions.get(key) if isinstance(sessions, dict) else None
    coordinator = bot_data.get(COORDINATOR_KEY)
    active = (
        coordinator.active(*key)
        if coordinator is not None
        else None
    )
    if active is not None and not operation_accepts_text(
        bot_data,
        active,
        *key,
    ):
        await update.effective_message.reply_text(
            "当前任务未结束，请先完成或取消。"
        )
        return
    if isinstance(session, dict):
        if float(session.get("expires_at") or 0) <= time.time():
            _drop_session(bot_data, key)
            await update.effective_message.reply_text(
                "会话已超时，请重新开始。"
            )
            return
        route = (
            router.plugin_route(str(session.get("plugin_id") or ""))
            if router is not None
            else None
        )
        if route is None:
            _drop_session(bot_data, key)
            await update.effective_message.reply_text(
                "本次会话已结束，请重新开始。"
            )
            return
    else:
        route = (
            router.direct_message_route(
                str(update.effective_message.text or "")
            )
            if router is not None
            else None
        )
        if route is None:
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
        _log_feature_event(
            "warning",
            "feature.message_dispatch_failed",
            update,
            route,
            operation_id=str(
                getattr(active, "operation_id", "") or ""
            ),
            error_code=code,
            error_type=type(exc).__name__,
            error_message=_safe_error(exc),
        )
        await update.effective_message.reply_text(f"❌ {code}：{_safe_error(exc)}")


def _is_stale_operation_snapshot(operation, active) -> bool:
    if not isinstance(operation, dict) or active is None:
        return False
    if str(operation.get("operation_id") or "") != active.operation_id:
        return False
    try:
        revision = int(operation.get("revision"))
    except (TypeError, ValueError):
        return False
    return revision < active.revision


async def handle_feature_result(update, context, route, result: dict):
    coordinator = context.application.bot_data.get(COORDINATOR_KEY)
    operation_record = None
    operation_segment = None
    stale_operation_snapshot = False
    suppress_operation_projection = False
    operation = result.get("operation") if isinstance(result, dict) else None
    if operation is not None:
        if coordinator is None or not isinstance(operation, dict):
            _log_invalid_feature_response(
                update,
                route,
                "operation_state_invalid",
            )
            await _feature_feedback(
                update,
                "任务状态无效，请重新开始。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return
        try:
            normalized_operation = _with_rendered_keyboard(
                route,
                result,
                operation,
            )
            operation_id = str(
                normalized_operation.get("operation_id") or ""
            )
            has_segment = normalized_operation.get("segment") is not None
            needs_adoption_lock = (
                has_segment
                and not coordinator.has_nonlegacy_message_segments(
                    operation_id
                )
            )
            if needs_adoption_lock:
                async with operation_render_lock(
                    context.application,
                    operation_id,
                ):
                    operation_record, operation_segment = (
                        coordinator.accept_segment_report(
                            route.plugin_id,
                            normalized_operation,
                        )
                    )
            elif has_segment:
                operation_record, operation_segment = (
                    coordinator.accept_segment_report(
                        route.plugin_id,
                        normalized_operation,
                    )
                )
            else:
                operation_record = coordinator.report(
                    route.plugin_id,
                    normalized_operation,
                )
            stale_operation_snapshot = _is_stale_operation_snapshot(
                operation,
                operation_record,
            )
        except Exception as exc:
            active = None
            try:
                active = coordinator.active(
                    int(update.effective_chat.id),
                    int(update.effective_user.id),
                )
            except Exception:
                pass
            _log_feature_event(
                "warning",
                "feature.operation_report_rejected",
                update,
                route,
                operation_id=str(
                    operation.get("operation_id") or ""
                ),
                submitted_state=str(
                    operation.get("state") or ""
                ),
                submitted_stage=str(
                    operation.get("stage") or ""
                ),
                submitted_revision=operation.get("revision"),
                active_operation_id=str(
                    getattr(active, "operation_id", "") or ""
                ),
                active_state=str(
                    getattr(active, "state", "") or ""
                ),
                active_revision=getattr(active, "revision", None),
                error_code=str(
                    getattr(exc, "code", "")
                    or type(exc).__name__
                ),
                error_type=type(exc).__name__,
                error_message=_safe_error(exc),
            )
            await _feature_feedback(
                update,
                "任务状态未更新，请稍后重试。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return
    if isinstance(result, dict) and "config_patch" in result:
        await _apply_feature_config_patch(update, context, route, result)
        return
    if (
        operation_record is not None
        and stale_operation_snapshot
        and coordinator.has_nonlegacy_message_segments(
            operation_record.operation_id
        )
    ):
        return
    if operation_record is not None and stale_operation_snapshot:
        message_id = await render_operation(
            context.application,
            context.application.bot_data.get(ROUTER_KEY),
            operation_record,
        )
        message_kind = (
            operation_record.message_kind
            if message_id is not None
            else None
        )
        rendered = True
    elif operation_record is not None and operation_segment is not None:
        message_id = await render_operation(
            context.application,
            context.application.bot_data.get(ROUTER_KEY),
            operation_record,
        )
        message_kind = (
            operation_segment.presentation_kind
            if message_id is not None
            else None
        )
        rendered = True
    elif operation_record is not None:
        async with operation_render_lock(
            context.application,
            operation_record.operation_id,
        ):
            current = coordinator.get(operation_record.operation_id)
            if current is not None:
                operation_record = current
            if coordinator.has_nonlegacy_message_segments(
                operation_record.operation_id
            ):
                message_id = None
                message_kind = None
                rendered = True
                suppress_operation_projection = True
            else:
                rendered, message_id, message_kind = await _render_actions(
                    update,
                    context,
                    route,
                    result,
                    operation_record=operation_record,
                )
                if rendered and message_id is not None:
                    operation_record = coordinator.set_message_id(
                        operation_record.operation_id,
                        message_id,
                        message_kind,
                    )
    else:
        rendered, message_id, message_kind = await _render_actions(
            update,
            context,
            route,
            result,
        )
    if not rendered:
        return
    session = result.get("session") if isinstance(result, dict) else None
    if session is None:
        if (
            operation_record is not None
            and message_id is None
            and not suppress_operation_projection
        ):
            await render_operation(context.application, None, operation_record)
        return
    if not isinstance(session, dict) or session.get("state") not in {"open", "close"}:
        _log_invalid_feature_response(
            update,
            route,
            "session_state_invalid",
            operation_record=operation_record,
        )
        await _feature_feedback(
            update,
            "会话状态无效，请重新开始。",
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
        active = coordinator.active(*key) if coordinator is not None else None
        if (
            active is not None
            and active.plugin_id == route.plugin_id
            and active.state == "awaiting_input"
            and not _is_stale_operation_snapshot(operation, active)
        ):
            operation_record = coordinator.report(route.plugin_id, {
                "operation_id": active.operation_id,
                "chat_id": active.chat_id,
                "user_id": active.user_id,
                "state": "cancelled",
                "stage": active.stage,
                "status_text": "已退出。",
                "control": "",
                "revision": active.revision + 1,
                "details": dict(active.details),
            })
    if operation_record is not None and message_id is None:
        await render_operation(context.application, None, operation_record)


def _with_rendered_keyboard(route, result: dict, operation: dict) -> dict:
    normalized = deepcopy(operation)
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list):
        return normalized
    for action in reversed(actions):
        if not isinstance(action, dict):
            continue
        data = action.get("data")
        details = dict(normalized.get("details") or {})
        if isinstance(data, dict) and "keyboard" in data:
            if _keyboard_markup(route, data) is False:
                return normalized
            details["keyboard"] = deepcopy(data["keyboard"])
        else:
            details.pop("keyboard", None)
        if action.get("kind") in {"send_photo", "edit_photo"}:
            photo_url = _photo_url(data)
            if photo_url is False:
                return normalized
            details["photo_url"] = photo_url
            details.pop("poster_items", None)
        elif action.get("kind") == "send_photo_grid":
            poster_items = _poster_items(data)
            if poster_items is False:
                return normalized
            details["poster_items"] = poster_items
            details.pop("photo_url", None)
        else:
            details.pop("photo_url", None)
            details.pop("poster_items", None)
        parse_mode = (
            data.get("parse_mode")
            if isinstance(data, dict)
            else None
        )
        if parse_mode in {"HTML", "MarkdownV2"}:
            details["parse_mode"] = parse_mode
        else:
            details.pop("parse_mode", None)
        normalized["details"] = details
        return normalized
    return normalized


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
        _finish_feature_config_operation(
            context, route, result,
            state="failed",
            status_text="Feature 配置补丁无效，配置未写入。",
        )
        await _feature_feedback(
            update,
            "配置内容无效。",
            prefer_edit=prefer_edit,
        )
        return
    manager = context.application.bot_data.get(MANAGER_KEY)
    if manager is None:
        _finish_feature_config_operation(
            context, route, result,
            state="failed",
            status_text="配置不可用。",
        )
        await _feature_feedback(
            update,
            "配置不可用。",
            prefer_edit=prefer_edit,
        )
        return
    try:
        previous = manager.config(route.plugin_id).get("config") or {}
        configured = merge_nested_patch(previous, patch)
    except Exception as exc:
        _finish_feature_config_operation(
            context, route, result,
            state="failed",
            status_text="读取配置失败。",
        )
        await _feature_feedback(
            update,
            "读取配置失败。",
            prefer_edit=prefer_edit,
        )
        return
    coordinator = context.application.bot_data.get(COORDINATOR_KEY)
    operation = result.get("operation") if isinstance(result, dict) else None
    operation_id = str(
        operation.get("operation_id")
        if isinstance(operation, dict)
        else ""
    )
    cancel_event = threading.Event()
    if coordinator is not None and operation_id:
        record = coordinator.get(operation_id)
        if record is not None and record.state not in TERMINAL_STATES:
            coordinator.report(route.plugin_id, {
                "operation_id": record.operation_id,
                "chat_id": record.chat_id,
                "user_id": record.user_id,
                "state": "running",
                "stage": "config_apply",
                "status_text": "正在保存配置。",
                "control": "rollback",
                "revision": record.revision + 1,
                "details": {
                    **dict(record.details),
                    "rollback_scope": "feature_config_and_route",
                },
            })
            tasks = context.application.bot_data.setdefault(
                CONFIG_OPERATION_TASKS_KEY, {}
            )
            tasks[operation_id] = {
                "cancel_event": cancel_event,
                "plugin_id": route.plugin_id,
            }
    await _feature_feedback(
        update,
        "正在保存配置。",
        prefer_edit=prefer_edit,
    )
    try:
        outcome = await manager.configure(
            route.plugin_id,
            configured,
            should_cancel=cancel_event.is_set if operation_id else None,
        )
    except PluginOperationError as exc:
        cancelled = cancel_event.is_set() or exc.code == "config_cancelled"
        rollback_verified = exc.code == "config_cancelled"
        _finish_feature_config_operation(
            context, route, result,
            state=(
                "rolled_back"
                if rollback_verified
                else "partially_rolled_back" if cancelled else "failed"
            ),
            status_text=(
                "配置切换已取消，原配置和原 Feature 路由已恢复。"
                if rollback_verified
                else "配置回滚未能完整验证，请人工检查当前配置和路由。"
                if cancelled
                else "配置保存失败。"
            ),
        )
        await _feature_feedback(
            update,
            (
                "配置已取消，原配置已恢复。"
                if rollback_verified
                else "配置保存失败，请检查配置。"
            ),
            prefer_edit=prefer_edit,
        )
        return
    except Exception as exc:
        _finish_feature_config_operation(
            context, route, result,
            state="failed",
            status_text="配置保存失败。",
        )
        await _feature_feedback(
            update,
            "配置保存失败，请检查配置。",
            prefer_edit=prefer_edit,
        )
        return
    finally:
        tasks = context.application.bot_data.get(CONFIG_OPERATION_TASKS_KEY)
        if isinstance(tasks, dict):
            tasks.pop(operation_id, None)
            if not tasks:
                context.application.bot_data.pop(
                    CONFIG_OPERATION_TASKS_KEY, None
                )
    if cancel_event.is_set():
        try:
            await manager.configure(route.plugin_id, previous)
        except Exception as exc:
            _finish_feature_config_operation(
                context, route, result,
                state="partially_rolled_back",
                status_text="配置回滚失败，请检查配置。",
            )
            await _feature_feedback(
                update,
                "配置回滚失败，请检查配置。",
                prefer_edit=prefer_edit,
            )
            return
        _finish_feature_config_operation(
            context, route, result,
            state="rolled_back",
            status_text="配置已取消，原配置已恢复。",
        )
        await _feature_feedback(
            update,
            "配置已取消，原配置已恢复。",
            prefer_edit=prefer_edit,
        )
        return
    _drop_session(context.application.bot_data, _session_key(update))
    _finish_feature_config_operation(
        context, route, result,
        state="completed",
        status_text="配置已更新。",
    )
    await _feature_feedback(
        update,
        "配置已更新。",
        prefer_edit=prefer_edit,
    )


def _finish_feature_config_operation(
    context, route, result: dict, *, state: str, status_text: str
):
    coordinator = context.application.bot_data.get(COORDINATOR_KEY)
    operation = result.get("operation") if isinstance(result, dict) else None
    if coordinator is None or not isinstance(operation, dict):
        return None
    operation_id = str(operation.get("operation_id") or "")
    record = coordinator.get(operation_id) if operation_id else None
    if (
        record is None
        or record.plugin_id != route.plugin_id
        or record.state in TERMINAL_STATES
    ):
        return record
    return coordinator.report(route.plugin_id, {
        "operation_id": record.operation_id,
        "chat_id": record.chat_id,
        "user_id": record.user_id,
        "state": state,
        "stage": record.stage,
        "status_text": status_text,
        "control": "",
        "revision": record.revision + 1,
        "details": dict(record.details),
    })


async def _render_actions(
    update,
    context,
    route,
    result: dict,
    *,
    operation_record=None,
) -> tuple[bool, int | None, str | None]:
    actions = result.get("actions") if isinstance(result, dict) else None
    if not isinstance(actions, list) or len(actions) > 20:
        _log_invalid_feature_response(
            update,
            route,
            (
                "actions_invalid"
                if not isinstance(actions, list)
                else "actions_limit_exceeded"
            ),
            operation_record=operation_record,
        )
        await _feature_feedback(
            update,
            "❌ Feature 返回了无效响应。",
            prefer_edit=bool(getattr(update, "callback_query", None)),
        )
        return False, None, None
    last_message_id = None
    last_message_kind = None
    source_keyboard_resolved = False
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get("kind") not in _SAFE_ACTIONS:
            _log_invalid_feature_response(
                update,
                route,
                (
                    "action_invalid"
                    if not isinstance(action, dict)
                    else "action_kind_invalid"
                ),
                action_index=index,
                operation_record=operation_record,
            )
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False, None, None
        text = str(action.get("text") or "")
        if not text:
            _log_invalid_feature_response(
                update,
                route,
                "action_text_missing",
                action_index=index,
                operation_record=operation_record,
            )
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False, None, None
        if len(text) > 4096:
            text = text[:4075].rstrip() + "\n…内容已截断"
        parse_mode = action.get("parse_mode")
        if parse_mode not in {None, "HTML", "MarkdownV2"}:
            parse_mode = None
        kwargs = {"parse_mode": parse_mode} if parse_mode else {}
        action_data = action.get("data")
        reply_markup = _keyboard_markup(route, action_data)
        if reply_markup is False:
            _log_invalid_feature_response(
                update,
                route,
                "action_data_invalid",
                action_index=index,
                operation_record=operation_record,
            )
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False, None, None
        if index == len(actions) - 1 and operation_record is not None:
            control_markup = operation_markup(operation_record)
            if control_markup is not None and not _has_explicit_control(action_data):
                rows = list(reply_markup.inline_keyboard) if reply_markup is not None else []
                rows.extend(control_markup.inline_keyboard)
                reply_markup = InlineKeyboardMarkup(rows)
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        photo_action = action["kind"] in {"send_photo", "edit_photo"}
        grid_action = action["kind"] == "send_photo_grid"
        photo_url = _photo_url(action_data) if photo_action else None
        poster_items = _poster_items(action_data) if grid_action else None
        if photo_url is False or (
            not photo_action
            and not grid_action
            and isinstance(action_data, dict)
            and "photo_url" in action_data
        ) or poster_items is False:
            _log_invalid_feature_response(
                update,
                route,
                "photo_data_invalid",
                action_index=index,
                operation_record=operation_record,
            )
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False, None, None
        if (
            not grid_action
            and isinstance(action_data, dict)
            and "poster_items" in action_data
        ):
            _log_invalid_feature_response(
                update,
                route,
                "poster_data_invalid",
                action_index=index,
                operation_record=operation_record,
            )
            await _feature_feedback(
                update,
                "❌ Feature 返回了无效响应。",
                prefer_edit=bool(getattr(update, "callback_query", None)),
            )
            return False, None, None
        delivered_action = str(action["kind"])
        if action["kind"] == "send_message":
            sent = await update.effective_message.reply_text(text, **kwargs)
            rendered_kind = "text"
            edited_source = False
        elif action["kind"] == "edit_message":
            if _message_has_photo(update.effective_message):
                try:
                    await update.effective_message.edit_reply_markup(
                        reply_markup=None
                    )
                except Exception:
                    pass
                sent = await update.effective_message.reply_text(text, **kwargs)
                edited_source = False
                delivered_action = "send_message"
            else:
                sent = await update.effective_message.edit_text(text, **kwargs)
                edited_source = True
            rendered_kind = "text"
        else:
            caption, caption_parse_mode = bounded_photo_caption(
                text,
                parse_mode,
            )
            media_kwargs = dict(kwargs)
            media_kwargs.pop("parse_mode", None)
            try:
                if grid_action:
                    photo_grid = await asyncio.to_thread(
                        build_poster_grid,
                        poster_items,
                    )
                    if caption_parse_mode:
                        media_kwargs["parse_mode"] = caption_parse_mode
                    sent = await update.effective_message.reply_photo(
                        photo=photo_grid,
                        caption=caption,
                        **media_kwargs,
                    )
                    edited_source = False
                elif action["kind"] == "send_photo":
                    if caption_parse_mode:
                        media_kwargs["parse_mode"] = caption_parse_mode
                    sent = await update.effective_message.reply_photo(
                        photo=photo_url,
                        caption=caption,
                        **media_kwargs,
                    )
                    edited_source = False
                else:
                    sent = await update.effective_message.edit_media(
                        media=InputMediaPhoto(
                            media=photo_url,
                            caption=caption,
                            parse_mode=caption_parse_mode,
                        ),
                        **media_kwargs,
                    )
                    edited_source = True
                rendered_kind = "photo"
            except Exception as exc:
                if grid_action:
                    _log_feature_event(
                        "warning",
                        "poster_grid_unavailable",
                        update,
                        route,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                sent = await update.effective_message.reply_text(text, **kwargs)
                rendered_kind = "text"
                edited_source = False
                delivered_action = "send_message"
        if (
            getattr(update, "callback_query", None) is not None
            and not source_keyboard_resolved
        ):
            if not edited_source or reply_markup is None:
                await _clear_callback_keyboard(update)
            source_keyboard_resolved = True
        candidate = getattr(sent, "message_id", None)
        if not isinstance(candidate, int) and action["kind"] in {
            "edit_message", "edit_photo",
        }:
            candidate = getattr(update.effective_message, "message_id", None)
        if isinstance(candidate, int) and candidate > 0:
            last_message_id = candidate
            last_message_kind = rendered_kind
        _log_delivered_feature_action(
            update,
            route,
            operation_record=operation_record,
            action_index=index,
            requested_action=str(action["kind"]),
            delivered_action=delivered_action,
            text=text,
            parse_mode=parse_mode,
            action_data=action_data,
            message_id=candidate if isinstance(candidate, int) and candidate > 0 else None,
            message_kind=rendered_kind,
        )
    return True, last_message_id, last_message_kind


async def _clear_callback_keyboard(update):
    query = getattr(update, "callback_query", None)
    editor = getattr(query, "edit_message_reply_markup", None)
    if not callable(editor):
        return
    try:
        await editor(reply_markup=None)
    except Exception:
        pass


def _message_has_photo(message) -> bool:
    photo = getattr(message, "photo", None)
    return isinstance(photo, (list, tuple)) and bool(photo)


def _has_explicit_control(data) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("keyboard"), list):
        return False
    labels = {"退出", "取消", "取消任务", "取消并回滚", "结束", "中断任务"}
    return any(
        isinstance(button, dict) and str(button.get("text") or "").strip() in labels
        for row in data["keyboard"]
        if isinstance(row, list)
        for button in row
    )


def _keyboard_markup(route, data):
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) - {
        "keyboard",
        "photo_url",
        "poster_items",
        "parse_mode",
    }:
        return False
    keyboard = data.get("keyboard")
    if keyboard is None:
        return None
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
    return InlineKeyboardMarkup(
        deduplicate_terminal_controls(rows)
    )


def _photo_url(data):
    if not isinstance(data, dict):
        return False
    photo_url = str(data.get("photo_url") or "").strip()
    if (
        not photo_url.startswith("https://")
        or len(photo_url) > 2048
        or any(character.isspace() for character in photo_url)
    ):
        return False
    return photo_url


def _poster_items(data):
    if not isinstance(data, dict):
        return False
    raw_items = data.get("poster_items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 6:
        return False
    result = []
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            return False
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            return False
        title = " ".join(str(item.get("title") or "").split())
        poster_url = str(item.get("poster_url") or "").strip()
        if (
            number != index
            or not title
            or len(title) > 200
            or (
                poster_url
                and (
                    not poster_url.startswith("https://")
                    or len(poster_url) > 2048
                    or any(
                        character.isspace()
                        for character in poster_url
                    )
                )
            )
        ):
            return False
        result.append({
            "number": number,
            "title": title,
            "poster_url": poster_url,
        })
    return result


async def _feature_feedback(update, text: str, *, prefer_edit: bool = False):
    query = getattr(update, "callback_query", None)
    action = "send_message"
    if (
        prefer_edit
        and query is not None
        and hasattr(query, "edit_message_text")
        and not _message_has_photo(update.effective_message)
    ):
        delivered = await query.edit_message_text(text)
        action = "edit_message"
    else:
        delivered = await update.effective_message.reply_text(text)
    logger = getattr(init, "logger", None)
    method = getattr(logger, "info", None) if logger is not None else None
    if callable(method):
        message = getattr(query, "message", None) if action == "edit_message" else delivered
        method(
            "Telegram 提示消息已送达",
            event_name="telegram.feedback.delivered",
            diagnostic_fields={
                "stage": "telegram_delivery",
                "status": "completed",
                "input": {
                    "update_id": getattr(update, "update_id", None),
                    "chat_id": getattr(
                        getattr(update, "effective_chat", None), "id", None
                    ),
                    "user_id": getattr(
                        getattr(update, "effective_user", None), "id", None
                    ),
                },
                "user_surface": {
                    "direction": "outgoing",
                    "action": action,
                    "text": str(text),
                },
                "output": {
                    "message_id": getattr(message, "message_id", None),
                },
            },
        )


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
        if str(key).startswith("host_config_"):
            user_data.pop(key, None)

from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

try:
    import init
except ModuleNotFoundError:
    from app import init


CONFIG_SHARED = 81
SESSION_KEY = "host_shared_config"
_PREFIX = "host-config-shared:"
_LABELS = {
    "live_action_movie": "真人电影",
    "animated_movie": "动画电影",
    "live_action_series": "真人剧集",
    "animated_series": "动画剧集",
}


def _keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=_PREFIX + action) for label, action in row]
        for row in rows
    ])


async def _show(update, text, rows=()):
    kwargs = {"reply_markup": _keyboard(rows)} if rows else {}
    if update.callback_query is not None:
        await update.callback_query.edit_message_text(text, **kwargs)
    else:
        await update.effective_message.reply_text(text, **kwargs)


def _owner(update):
    return update.effective_chat.id, update.effective_user.id


def _session(update, context):
    value = context.user_data.get(SESSION_KEY)
    if not value or value.get("owner") != _owner(update) or value.get("expires", 0) <= time.monotonic():
        context.user_data.pop(SESSION_KEY, None)
        return None
    return value


def _shared(manager):
    backend = manager.store.central_config
    shared = deepcopy(backend.read().get("shared") or {})
    if "category_folder" not in shared:
        shared["category_folder"] = backend.effective("search", {}).get("category_folder") or []
    return shared


def central_config_saved_feedback(outcome):
    affected = (getattr(outcome, "details", None) or {}).get("affected_plugins") or []
    reload_status = (
        "已重新加载：" + "、".join(affected) + "。"
        if affected else "没有运行中的插件切换配置。"
    )
    return "已保存到顶层 config.yaml。\n" + reload_status


async def shared_config_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await _show(update, "无权配置。")
        return ConversationHandler.END
    manager = context.application.bot_data.get("telepiplex_plugin_manager")
    if manager is None or getattr(manager, "central_config_enabled", False) is not True:
        await _show(update, "集中配置暂时不可用。")
        return ConversationHandler.END
    action = str(query.data or "").removeprefix(_PREFIX)
    if action == "open":
        context.user_data[SESSION_KEY] = {
            "owner": _owner(update), "expires": time.monotonic() + 1800, "stage": "choose",
        }
        await _show(update, "公共配置\n\n保存到顶层 config.yaml，相关插件会一起重新加载。", [
            [("分类目录与 Plex 库", "routes")], [("TMDB 凭据", "tmdb")], [("退出", "cancel")],
        ])
        return CONFIG_SHARED
    session = _session(update, context)
    if session is None:
        await _show(update, "配置会话已失效，请重新打开 /config。")
        return ConversationHandler.END
    if action == "cancel":
        context.user_data.pop(SESSION_KEY, None)
        await _show(update, "已退出公共配置。")
        return ConversationHandler.END
    if action == "confirm" and session.get("stage") == "confirm":
        # Consume before awaiting so repeated callbacks cannot submit twice.
        session["stage"] = "saving"
        try:
            outcome = await manager.configure_shared(
                deepcopy(session["patch"]), expected=deepcopy(session["expected"]),
            )
        except Exception as exc:
            if getattr(exc, "code", "") == "config_conflict":
                context.user_data.pop(SESSION_KEY, None)
                await _show(update, "公共配置已变化，本次修改未写入。请重新打开 /config。")
                return ConversationHandler.END
            session["stage"] = "confirm"
            await _show(update, "配置保存失败；请检查主配置或稍后重试。", [
                [("重试保存", "confirm")], [("退出", "cancel")],
            ])
            return CONFIG_SHARED
        context.user_data.pop(SESSION_KEY, None)
        await _show(update, central_config_saved_feedback(outcome))
        return ConversationHandler.END
    if session.get("stage") not in {"choose", "routes"}:
        await _show(update, "请完成当前输入，或退出后重新开始。", [[("退出", "cancel")]])
        return CONFIG_SHARED
    try:
        shared = _shared(manager)
    except Exception:
        await _show(update, "读取主配置失败，请先检查 config.yaml。")
        return ConversationHandler.END
    if action == "routes":
        session["stage"] = "routes"
        await _show(update, "选择要修改的分类。", [
            [(label, "route:" + kind)] for kind, label in _LABELS.items()
        ] + [[("退出", "cancel")]])
    elif action == "tmdb":
        session.update(
            stage="tmdb", current=deepcopy(shared.get("tmdb") or {}),
            expected={"tmdb": {"api_key": (shared.get("tmdb") or {}).get("api_key", "")}},
        )
        state = "已配置" if session["current"].get("api_key") else "未配置"
        await _show(update, f"TMDB：{state}\n请输入 API Read Access Token；- 保留，clear 清空。", [[("退出", "cancel")]])
    elif action.startswith("route:") and action.split(":", 1)[1] in _LABELS:
        kind = action.split(":", 1)[1]
        routes = deepcopy(shared.get("category_folder") or [])
        route = next((item for item in routes if item.get("kind") == kind), None)
        if route is None:
            await _show(update, "该分类尚未配置，请检查主配置中的四分类目录。")
            return ConversationHandler.END
        session.update(
            stage="route_path", kind=kind, routes=routes,
            expected={"category_folder": deepcopy(routes)},
        )
        await _show(update, f"{_LABELS[kind]}\n当前目录：{route['path']}\n请输入 115 绝对路径；- 保留。", [[("退出", "cancel")]])
    else:
        await _show(update, "配置操作已失效，请重新打开 /config。")
        return ConversationHandler.END
    return CONFIG_SHARED


async def shared_config_message(update, context):
    if not init.check_user(update.effective_user.id):
        return ConversationHandler.END
    session = _session(update, context)
    if session is None:
        await _show(update, "配置会话已失效，请重新打开 /config。")
        return ConversationHandler.END
    raw = str(update.effective_message.text or "").strip()
    stage = session.get("stage")
    try:
        if stage == "tmdb":
            if not raw or any(char.isspace() for char in raw):
                raise ValueError
            if raw == "-":
                context.user_data.pop(SESSION_KEY, None)
                await _show(update, "已保留当前 TMDB 凭据，主配置未修改。")
                return ConversationHandler.END
            token = "" if raw.lower() == "clear" else raw
            session["patch"] = {"tmdb": {"api_key": token}}
        elif stage in {"route_path", "route_library"}:
            route = next(item for item in session["routes"] if item["kind"] == session["kind"])
            if stage == "route_path":
                value = route["path"] if raw == "-" else raw
                if (not value.startswith("/") or value == "/" or ".." in value.split("/")
                        or "\\" in value or any(ord(char) < 32 for char in value)):
                    raise ValueError
                route["path"] = str(PurePosixPath(value))
                session["stage"] = "route_library"
                await _show(update, f"当前 Plex 库 ID：{route.get('plex_library_id') or '未配置'}\n请输入库 ID；- 保留，clear 清空。", [[("退出", "cancel")]])
                return CONFIG_SHARED
            if raw != "-":
                if raw.lower() != "clear" and (not raw.isascii() or not raw.isdigit() or int(raw) < 1):
                    raise ValueError
                route["plex_library_id"] = "" if raw.lower() == "clear" else raw
            if session["routes"] == session["expected"]["category_folder"]:
                context.user_data.pop(SESSION_KEY, None)
                await _show(update, "已保留当前分类配置，主配置未修改。")
                return ConversationHandler.END
            session["patch"] = {"category_folder": deepcopy(session["routes"])}
        else:
            raise ValueError
    except (ValueError, StopIteration, KeyError):
        await _show(update, "输入无效，请按当前提示重新输入。", [[("退出", "cancel")]])
        return CONFIG_SHARED
    session["stage"] = "confirm"
    await _show(update, "修改已暂存，敏感值不会回显。确认保存到主配置并重新加载相关插件？", [
        [("确认保存", "confirm")], [("退出", "cancel")],
    ])
    return CONFIG_SHARED

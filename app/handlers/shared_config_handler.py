from __future__ import annotations

from copy import deepcopy
import time
import secrets

from telepiplex_plugin_sdk.storage_paths import normalize_115_directory

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


async def _show(update, text, rows=(), *, fresh=False):
    kwargs = {"reply_markup": _keyboard(rows)} if rows else {}
    if update.callback_query is not None and not fresh:
        await update.callback_query.edit_message_text(text, **kwargs)
    else:
        await update.effective_message.reply_text(text, **kwargs)


def _owner(update):
    return update.effective_chat.id, update.effective_user.id


def host_draft(update, context):
    user_data = getattr(context, 'user_data', None)
    if isinstance(user_data, dict):
        return user_data.get(SESSION_KEY, {}).get(_owner(update))
    return None


def set_host_draft(update, context, draft):
    context.user_data.setdefault(SESSION_KEY, {})[_owner(update)] = draft


def clear_host_draft(update, context):
    drafts = context.user_data.get(SESSION_KEY, {})
    drafts.pop(_owner(update), None)
    if not drafts:
        context.user_data.pop(SESSION_KEY, None)


def _session(update, context):
    draft = host_draft(update, context)
    if draft is not None and draft.get('expires', 0) <= time.monotonic():
        clear_host_draft(update, context)
        return None
    return draft


def _shared(manager, *, include_categories=True):
    from app.runtime.config_projection import shared_view
    return shared_view(manager.store.central_config.read(), include_categories=include_categories)


def central_config_saved_feedback(outcome):
    affected = (getattr(outcome, "details", None) or {}).get("affected_plugins") or []
    reload_status = (
        "已重新加载：" + "、".join(affected) + "。"
        if affected else "没有运行中的插件切换配置。"
    )
    return "已保存到顶层 config.yaml。\n" + reload_status


async def shared_config_callback(update, context, *, _action=None):
    query = update.callback_query
    if _action is None:
        await query.answer()
    if not init.check_user(update.effective_user.id):
        await _show(update, "无权配置。")
        return ConversationHandler.END
    manager = context.application.bot_data.get("telepiplex_plugin_manager")
    if manager is None or getattr(manager, "central_config_enabled", False) is not True:
        await _show(update, "集中配置暂时不可用。")
        return ConversationHandler.END
    action = _action or str(query.data or "").removeprefix(_PREFIX)
    from app.handlers.settings_editor import open_section, editor_action
    from app.runtime.config_settings import LABELS, RECOVERY
    if action.startswith('go:') and action[3:] in {*LABELS,*RECOVERY,'migration','recovery'}:
        return await open_section(update, context, manager, action[3:])
    if action.startswith('e:'):
        return await editor_action(update,context,manager,action)
    if action == "open":
        set_host_draft(update, context, {
            "owner": _owner(update), "expires": time.monotonic() + 1800, "stage": "choose",
        })
        await _show(update, "基础配置 · 未安装的 Feature 也可以预先配置。", [
            [(label,'go:'+target)] for target,label in LABELS.items()
        ]+[[('迁移诊断与修复','go:migration')],[('退出','cancel')]])
        return CONFIG_SHARED
    session = _session(update, context)
    if session is None:
        await _show(update, "配置会话已失效，请重新打开 /config。")
        return ConversationHandler.END
    if action == "cancel":
        clear_host_draft(update, context)
        await _show(update, "已退出公共配置。")
        return ConversationHandler.END
    if action.startswith("confirm:") and action == "confirm:"+str(session.get("confirm_id")) and session.get("stage") == "confirm":
        # Consume before awaiting so repeated callbacks cannot submit twice.
        session["stage"] = "saving"
        try:
            outcome = await manager.configure_shared(
                deepcopy(session["patch"]), expected=deepcopy(session["expected"]),
            )
        except Exception as exc:
            if getattr(exc, "code", "") == "config_conflict":
                clear_host_draft(update, context)
                await _show(update, "公共配置已变化，本次修改未写入。请重新打开 /config。")
                return ConversationHandler.END
            session["stage"] = "confirm"
            await _show(update, "配置保存失败；请检查主配置或稍后重试。", [
                [("重试保存", action)], [("退出", "cancel")],
            ])
            return CONFIG_SHARED
        clear_host_draft(update, context)
        await _show(update, central_config_saved_feedback(outcome))
        return ConversationHandler.END
    if session.get("stage") not in {"choose", "routes"}:
        await _show(update, "请完成当前输入，或退出后重新开始。", [[("退出", "cancel")]])
        return CONFIG_SHARED
    try:
        if action == 'routes' or action.startswith('route:'):
            version = manager.store.central_config.read().get('config_version')
            if version in (2,3):
                return await open_section(update,context,manager,'directories' if version==3 else 'repair_categories')
        shared = _shared(manager, include_categories=action != 'tmdb')
    except Exception:
        await _show(update, "读取主配置失败，请先检查 config.yaml。")
        return ConversationHandler.END
    if action == "routes":
        session["stage"] = "routes"
        await _show(update, "选择要修改的分类。", [
            [(label, "route:" + kind)] for kind, label in _LABELS.items()
        ] + [[("退出", "cancel")]])
    elif action == "tmdb":
        tmdb = shared.get('tmdb')
        current = tmdb if isinstance(tmdb, dict) else {}
        expected = {'api_key': current.get('api_key', '')} if isinstance(tmdb, dict) else tmdb
        session.update(stage='tmdb', current=deepcopy(current), expected={'tmdb': expected})
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
    raw = str(update.effective_message.text or "")
    stage = session.get("stage")
    if stage in ('edit','rows','editor_confirm','saving') and session.get('target'):
        from app.handlers.settings_editor import editor_message
        return await editor_message(update,session)
    if stage != "route_path":
        raw = raw.strip()
    try:
        if stage == "tmdb":
            if not raw or any(char.isspace() for char in raw):
                raise ValueError
            if raw == "-":
                clear_host_draft(update, context)
                await _show(update, "已保留当前 TMDB 凭据，主配置未修改。")
                return ConversationHandler.END
            token = "" if raw.lower() == "clear" else raw
            session["patch"] = {"tmdb": {"api_key": token}}
        elif stage in {"route_path", "route_library"}:
            route = next(item for item in session["routes"] if item["kind"] == session["kind"])
            if stage == "route_path":
                value = route["path"] if raw == "-" else raw
                route["path"] = normalize_115_directory(value, allow_relative=True, allow_root=False)
                session["stage"] = "route_library"
                await _show(update, f"当前 Plex 库 ID：{route.get('plex_library_id') or '未配置'}\n请输入库 ID；- 保留，clear 清空。", [[("退出", "cancel")]])
                return CONFIG_SHARED
            if raw != "-":
                if raw.lower() != "clear" and (not raw.isascii() or not raw.isdigit() or int(raw) < 1):
                    raise ValueError
                route["plex_library_id"] = "" if raw.lower() == "clear" else raw
            if session["routes"] == session["expected"]["category_folder"]:
                clear_host_draft(update, context)
                await _show(update, "已保留当前分类配置，主配置未修改。")
                return ConversationHandler.END
            session["patch"] = {"category_folder": deepcopy(session["routes"])}
        else:
            raise ValueError
    except (ValueError, StopIteration, KeyError):
        await _show(update, "输入无效，请按当前提示重新输入。", [[("退出", "cancel")]])
        return CONFIG_SHARED
    session["stage"] = "confirm"
    session["confirm_id"] = secrets.token_hex(6)
    await _show(update, "修改已暂存，敏感值不会回显。确认保存到主配置并重新加载相关插件？", [
        [("确认保存", "confirm:"+session["confirm_id"])], [("退出", "cancel")],
    ])
    return CONFIG_SHARED

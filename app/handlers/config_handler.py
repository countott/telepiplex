from __future__ import annotations

import re
import warnings
from copy import deepcopy
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

import init
from app.core.plugin_manager import PluginOperationError


CONFIG_SELECT_PLUGIN, CONFIG_SELECT_SECTION, CONFIG_INPUT = range(80, 83)
MANAGER_KEY = "telepiplex_plugin_manager"
_SESSION_KEYS = (
    "core_config_plugins",
    "core_config_plugin",
    "core_config_sections",
    "core_config_path",
)
_SCALAR_TYPES = {"boolean", "integer", "number", "string"}
_SECRET_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:token|secret|password|api_?key|authorization)(?:$|_)"
)


class ConfigInputError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigField:
    name: str
    title: str
    schema: dict
    value: object
    secret: bool


@dataclass(frozen=True)
class ConfigSection:
    path: tuple[str, ...]
    title: str
    fields: tuple[ConfigField, ...]


def _resolve_schema(root: dict, schema: dict) -> dict:
    resolved = dict(schema or {})
    seen = set()
    while isinstance(resolved.get("$ref"), str):
        reference = resolved["$ref"]
        if not reference.startswith("#/") or reference in seen:
            break
        seen.add(reference)
        target = root
        try:
            for raw in reference[2:].split("/"):
                key = raw.replace("~1", "/").replace("~0", "~")
                target = target[key]
        except (KeyError, TypeError):
            break
        if not isinstance(target, dict):
            break
        siblings = {key: value for key, value in resolved.items() if key != "$ref"}
        resolved = {**target, **siblings}
    return resolved


def _mapping_at(value: dict, path: tuple[str, ...]) -> dict:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def discover_config_sections(schema: dict, current: dict) -> list[ConfigSection]:
    root = schema if isinstance(schema, dict) else {}
    source = current if isinstance(current, dict) else {}
    sections: list[ConfigSection] = []

    def walk(node: dict, path: tuple[str, ...]):
        resolved = _resolve_schema(root, node)
        properties = resolved.get("properties")
        if not isinstance(properties, dict):
            return
        current_section = _mapping_at(source, path)
        fields = []
        nested = []
        for name, raw_child in properties.items():
            if not isinstance(name, str) or not isinstance(raw_child, dict):
                continue
            child = _resolve_schema(root, raw_child)
            field_type = child.get("type")
            if field_type in _SCALAR_TYPES:
                fields.append(ConfigField(
                    name=name,
                    title=str(child.get("title") or name),
                    schema=deepcopy(child),
                    value=deepcopy(current_section.get(name)),
                    secret=bool(
                        child.get("writeOnly") is True
                        or child.get("format") == "password"
                        or _SECRET_NAME_RE.search(name)
                    ),
                ))
            elif field_type == "object" or isinstance(child.get("properties"), dict):
                nested.append((name, child))

        if path and fields:
            sections.append(ConfigSection(
                path=path,
                title=str(resolved.get("title") or ".".join(path)),
                fields=tuple(fields),
            ))
        for name, child in nested:
            walk(child, (*path, name))

    walk(root, ())
    return sections


def _display_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).replace("\n", " ")[:300]


def format_section_prompt(plugin_id: str, section: ConfigSection) -> str:
    current_lines = []
    examples = []
    for field in section.fields:
        if field.secret:
            status = "已配置" if str(field.value or "").strip() else "未配置"
            current_lines.append(f"{field.name}=<{status}>")
            examples.append(f"{field.name}=<不会回显的敏感值>")
        else:
            current_lines.append(f"{field.name}={_display_value(field.value)}")
            examples.append(f"{field.name}={_display_value(field.value)}")
    return (
        f"配置 Feature：{plugin_id}\n"
        f"区块：{section.title}（{'.'.join(section.path)}）\n\n"
        "当前配置：\n"
        + "\n".join(current_lines)
        + "\n\n请按 key=value 逐行发送需要修改的字段；未发送字段保持不变。"
        "\n字符串字段可用 key= 清空。\n\n"
        "示例：\n"
        + "\n".join(examples)
        + "\n\n发送 /q 取消。"
    )


def _coerce_value(raw: str, field: ConfigField):
    field_type = field.schema.get("type")
    value = str(raw).strip().strip('"').strip("'")
    try:
        if field_type == "boolean":
            normalized = value.lower()
            if normalized in {"true", "1", "yes", "on", "是"}:
                coerced = True
            elif normalized in {"false", "0", "no", "off", "否"}:
                coerced = False
            else:
                raise ValueError
        elif field_type == "integer":
            if not re.fullmatch(r"[+-]?\d+", value):
                raise ValueError
            coerced = int(value)
        elif field_type == "number":
            coerced = float(value)
        else:
            coerced = value
    except ValueError:
        raise ConfigInputError(
            f"字段 {field.name} 需要 {field_type} 类型"
        ) from None
    enum = field.schema.get("enum")
    if isinstance(enum, list) and coerced not in enum:
        raise ConfigInputError(
            f"字段 {field.name} 只能是：{', '.join(str(item) for item in enum)}"
        )
    return coerced


def parse_config_patch(text: str, section: ConfigSection) -> dict:
    fields = {field.name: field for field in section.fields}
    patch = {}
    for raw_line in str(text or "").replace("`", "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        separator = "=" if "=" in line else ":" if ":" in line else ""
        if not separator:
            raise ConfigInputError(f"无法识别配置行：{line[:80]}")
        raw_name, raw_value = line.split(separator, 1)
        name = re.sub(r"[\s-]+", "_", raw_name.strip().lower())
        field = fields.get(name)
        if field is None:
            raise ConfigInputError(f"未知字段：{name}")
        patch[name] = _coerce_value(raw_value, field)
    if not patch:
        raise ConfigInputError("没有收到可写入的配置字段")
    return patch


def merge_config_patch(
    current: dict,
    path: tuple[str, ...],
    patch: dict,
) -> dict:
    result = deepcopy(current if isinstance(current, dict) else {})
    target = result
    for key in path:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target.update(deepcopy(patch))
    return result


def _safe_error(value) -> str:
    return re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=***redacted***",
        str(value),
    )[:1000]


def _clear_session(user_data: dict):
    for key in _SESSION_KEYS:
        user_data.pop(key, None)


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
    for status in manager.doctor():
        plugin_id = str(status.get("plugin_id") or "")
        if not plugin_id:
            continue
        try:
            view = manager.config(plugin_id)
            if discover_config_sections(view.get("schema"), view.get("config")):
                plugin_ids.append(plugin_id)
        except Exception:
            continue
    _clear_session(context.user_data)
    context.user_data["core_config_plugins"] = plugin_ids

    if plugin_ids:
        text = (
            "请选择要配置的 Feature。敏感字段只显示是否已配置，不会回显真实值。"
            "\n\n" + "\n".join(f"• {plugin_id}" for plugin_id in plugin_ids)
        )
        rows = [
            [InlineKeyboardButton(
                plugin_id,
                callback_data=f"core-config-plugin:{index}",
            )]
            for index, plugin_id in enumerate(plugin_ids)
        ]
        rows.append([InlineKeyboardButton("取消", callback_data="core-config-cancel")])
        markup = InlineKeyboardMarkup(rows)
    else:
        text = "当前没有已安装且包含可视化标量配置的 Feature。"
        markup = None
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


def _callback_index(data: str, prefix: str) -> int:
    try:
        index = int(str(data or "").removeprefix(prefix))
    except ValueError:
        raise ConfigInputError("配置会话索引无效") from None
    if index < 0:
        raise ConfigInputError("配置会话索引无效")
    return index


async def select_config_plugin(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    try:
        index = _callback_index(query.data, "core-config-plugin:")
        plugin_id = context.user_data["core_config_plugins"][index]
        manager = context.application.bot_data[MANAGER_KEY]
        view = manager.config(plugin_id)
        sections = discover_config_sections(view["schema"], view["config"])
        if not sections:
            raise ConfigInputError("该 Feature 没有可视化配置区块")
    except (ConfigInputError, KeyError, IndexError, TypeError) as exc:
        await query.edit_message_text(f"❌ 配置会话已失效：{_safe_error(exc)}")
        return ConversationHandler.END

    context.user_data["core_config_plugin"] = plugin_id
    context.user_data["core_config_sections"] = [section.path for section in sections]
    rows = [
        [InlineKeyboardButton(
            section.title,
            callback_data=f"core-config-section:{index}",
        )]
        for index, section in enumerate(sections)
    ]
    rows.extend([
        [InlineKeyboardButton("返回", callback_data="core-config-back")],
        [InlineKeyboardButton("取消", callback_data="core-config-cancel")],
    ])
    await query.edit_message_text(
        f"请选择 {plugin_id} 的配置区块。",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return CONFIG_SELECT_SECTION


async def select_config_section(update, context):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    try:
        index = _callback_index(query.data, "core-config-section:")
        plugin_id = context.user_data["core_config_plugin"]
        expected_path = tuple(context.user_data["core_config_sections"][index])
        manager = context.application.bot_data[MANAGER_KEY]
        view = manager.config(plugin_id)
        section = next(
            item for item in discover_config_sections(view["schema"], view["config"])
            if item.path == expected_path
        )
    except (ConfigInputError, KeyError, IndexError, StopIteration, TypeError) as exc:
        await query.edit_message_text(f"❌ 配置会话已失效：{_safe_error(exc)}")
        return ConversationHandler.END
    context.user_data["core_config_path"] = section.path
    await query.edit_message_text(format_section_prompt(plugin_id, section))
    return CONFIG_INPUT


async def receive_config_input(update, context):
    if not init.check_user(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ 当前账号无权配置 Feature。")
        return ConversationHandler.END
    try:
        plugin_id = context.user_data["core_config_plugin"]
        path = tuple(context.user_data["core_config_path"])
        manager = context.application.bot_data[MANAGER_KEY]
        view = manager.config(plugin_id)
        section = next(
            item for item in discover_config_sections(view["schema"], view["config"])
            if item.path == path
        )
        patch = parse_config_patch(update.effective_message.text or "", section)
        configured = merge_config_patch(view["config"], path, patch)
        result = await manager.configure(plugin_id, configured)
    except ConfigInputError as exc:
        await update.effective_message.reply_text(f"❌ 输入无效：{_safe_error(exc)}")
        return CONFIG_INPUT
    except PluginOperationError as exc:
        await update.effective_message.reply_text(
            f"❌ {exc.code}：配置未写入或重新加载失败；未回显错误详情以保护敏感值。"
        )
        return CONFIG_INPUT
    except (KeyError, StopIteration, TypeError):
        await update.effective_message.reply_text(
            "❌ 配置会话已失效，请重新发送 /config。"
        )
        return ConversationHandler.END
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ config_failed：{type(exc).__name__}"
        )
        return CONFIG_INPUT

    _clear_session(context.user_data)
    if result.details.get("restarted"):
        text = f"✅ {plugin_id} 配置已写入并重新加载。"
    else:
        text = f"✅ {plugin_id} 配置已写入；Feature 下次启动时生效。"
    await update.effective_message.reply_text(text)
    return ConversationHandler.END


async def config_back_callback(update, context):
    query = update.callback_query
    await query.answer()
    return await _show_config_menu(update, context, edit=True)


async def quit_config_conversation(update, context):
    _clear_session(context.user_data)
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
                CallbackQueryHandler(
                    config_open_callback,
                    pattern=r"^core-config-open$",
                ),
            ],
            states={
                CONFIG_SELECT_PLUGIN: [
                    CallbackQueryHandler(
                        select_config_plugin,
                        pattern=r"^core-config-plugin:\d+$",
                    ),
                    CallbackQueryHandler(
                        quit_config_conversation,
                        pattern=r"^core-config-cancel$",
                    ),
                ],
                CONFIG_SELECT_SECTION: [
                    CallbackQueryHandler(
                        select_config_section,
                        pattern=r"^core-config-section:\d+$",
                    ),
                    CallbackQueryHandler(
                        config_back_callback,
                        pattern=r"^core-config-back$",
                    ),
                    CallbackQueryHandler(
                        quit_config_conversation,
                        pattern=r"^core-config-cancel$",
                    ),
                ],
                CONFIG_INPUT: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        receive_config_input,
                    )
                ],
            },
            fallbacks=[CommandHandler("q", quit_config_conversation)],
            allow_reentry=True,
        )
    application.add_handler(handler)

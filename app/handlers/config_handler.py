# -*- coding: utf-8 -*-
import json
import os
import re
import warnings

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, ContextTypes, MessageHandler, filters
from telegram.warnings import PTBUserWarning

import init


(
    CONFIG_SELECT,
    CONFIG_SELECT_115_MODE,
    CONFIG_INPUT_115_OPENAPI,
    CONFIG_INPUT_115_ACCESS,
    CONFIG_INPUT_115_REFRESH,
    CONFIG_SELECT_OPTIONAL_ITEM,
    CONFIG_INPUT_OPTIONAL,
) = range(60, 67)


OPTIONAL_CONFIG_ITEMS = {
    "prowlarr": {
        "label": "Prowlarr",
        "path": ("search", "prowlarr"),
        "fields": ("base_url", "api_key"),
        "required": ("base_url", "api_key"),
        "enable_path": ("search", "enable"),
        "prompt": (
            "请发送 Prowlarr 配置。\n\n"
            "示例：\n"
            "base_url=http://192.168.1.2:9696\n"
            "api_key=xxxx\n\n"
            "发送 /q 可取消。"
        ),
    },
    "plex": {
        "label": "Plex",
        "path": ("media", "plex"),
        "fields": ("base_url", "token"),
        "required": ("base_url", "token"),
        "prompt": (
            "请发送 Plex 配置。\n\n"
            "示例：\n"
            "base_url=http://192.168.1.2:32400\n"
            "token=xxxx\n\n"
            "发送 /q 可取消。"
        ),
    },
    "tmdb": {
        "label": "TMDB",
        "path": ("metadata", "tmdb"),
        "fields": ("api_key", "timeout"),
        "required": ("api_key",),
        "prompt": (
            "请发送 TMDB 配置。\n\n"
            "示例：\n"
            "api_key=xxxx\n"
            "timeout=15\n\n"
            "发送 /q 可取消。"
        ),
    },
    "fanart": {
        "label": "Fanart.tv",
        "path": ("artwork", "fanart"),
        "fields": ("api_key", "timeout"),
        "required": ("api_key",),
        "prompt": (
            "请发送 Fanart.tv 配置。\n\n"
            "示例：\n"
            "api_key=xxxx\n"
            "timeout=15\n\n"
            "发送 /q 可取消。"
        ),
    },
    "tvdb": {
        "label": "TVDB",
        "path": ("metadata", "tvdb"),
        "fields": ("api_key", "subscriber_pin"),
        "required": ("api_key",),
        "enable_path": ("metadata", "tvdb", "enable"),
        "prompt": (
            "请发送 TVDB 配置。\n\n"
            "示例：\n"
            "api_key=xxxx\n"
            "subscriber_pin=\n\n"
            "发送 /q 可取消。"
        ),
    },
    "ai": {
        "label": "AI",
        "path": ("ai",),
        "fields": ("api_url", "api_key", "model"),
        "required": ("api_key",),
        "enable_path": ("ai", "enable"),
        "prompt": (
            "请发送 AI 配置。\n\n"
            "示例：\n"
            "api_url=https://api.deepseek.com\n"
            "api_key=xxxx\n"
            "model=deepseek-chat\n\n"
            "发送 /q 可取消。"
        ),
    },
}


def parse_key_value_lines(text: str) -> dict:
    values = {}
    for raw_line in str(text or "").replace("`", "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        separator = "=" if "=" in line else ":" if ":" in line else ""
        if not separator:
            continue
        key, value = line.split(separator, 1)
        key = re.sub(r"[\s-]+", "_", key.strip().lower())
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _load_config_file() -> dict:
    if os.path.exists(init.CONFIG_FILE):
        with open(init.CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
    return {}


def _write_config_file(config: dict):
    os.makedirs(os.path.dirname(init.CONFIG_FILE) or ".", exist_ok=True)
    with open(init.CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(config or {}, f, allow_unicode=True, sort_keys=False)


def _ensure_nested_mapping(config: dict, path: tuple[str, ...]) -> dict:
    current = config
    for key in path:
        value = current.get(key)
        if not isinstance(value, dict):
            value = {}
            current[key] = value
        current = value
    return current


def _set_nested_value(config: dict, path: tuple[str, ...], value):
    if not path:
        return
    parent = _ensure_nested_mapping(config, path[:-1])
    parent[path[-1]] = value


def _require_values(values: dict, required: tuple[str, ...]):
    missing = [key for key in required if not values.get(key) or values.get(key, "").lower().startswith("your_")]
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")


def _single_line_value(text: str) -> str:
    text = str(text or "").strip().strip("`").strip('"').strip("'")
    if "\n" in text:
        text = text.splitlines()[0].strip()
    values = parse_key_value_lines(text)
    if len(values) == 1:
        key, value = next(iter(values.items()))
        if key not in {"http", "https"}:
            return value.strip()
    return text


def _require_single_value(text: str, label: str) -> str:
    value = _single_line_value(text)
    if not value or value.lower().startswith("your_"):
        raise ValueError(f"{label} 不能为空")
    return value


def apply_115_token_payload(text: str) -> dict:
    values = parse_key_value_lines(text)
    _require_values(values, ("access_token", "refresh_token"))
    return apply_115_token_values(values["access_token"], values["refresh_token"])


def apply_115_token_values(access_token: str, refresh_token: str) -> dict:
    access_token = _require_single_value(access_token, "access_token")
    refresh_token = _require_single_value(refresh_token, "refresh_token")
    config = _load_config_file()
    config["115_app_id"] = None
    config["access_token"] = access_token
    config["refresh_token"] = refresh_token
    _write_config_file(config)

    os.makedirs(os.path.dirname(init.TOKEN_FILE) or ".", exist_ok=True)
    with open(init.TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"access_token": access_token, "refresh_token": refresh_token},
            f,
            ensure_ascii=False,
        )

    init.load_yaml_config()
    ready = init.initialize_115open()
    return {"ready": bool(ready), "config_file": init.CONFIG_FILE, "token_file": init.TOKEN_FILE}


def apply_115_openapi_payload(app_id: str) -> dict:
    app_id = _require_single_value(app_id, "115_app_id")
    config = _load_config_file()
    config["115_app_id"] = app_id
    config["access_token"] = ""
    config["refresh_token"] = ""
    _write_config_file(config)

    if os.path.exists(init.TOKEN_FILE):
        os.remove(init.TOKEN_FILE)

    init.load_yaml_config()
    init.openapi_115 = None
    return {"ready": True, "config_file": init.CONFIG_FILE, "token_file": init.TOKEN_FILE}


def apply_optional_config_payload(kind: str, text: str) -> dict:
    item = OPTIONAL_CONFIG_ITEMS.get(kind)
    if not item:
        raise ValueError("未知配置项")

    values = parse_key_value_lines(text)
    _require_values(values, item["required"])

    config = _load_config_file()
    section = _ensure_nested_mapping(config, item["path"])
    for field in item["fields"]:
        if field in values:
            section[field] = values[field]
    if item.get("enable_path"):
        _set_nested_value(config, item["enable_path"], True)

    _write_config_file(config)
    init.load_yaml_config()
    return {"ready": True, "config_file": init.CONFIG_FILE}


def build_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("配置 115", callback_data="config_select:115")],
            [InlineKeyboardButton("可选服务配置", callback_data="config_select:optional")],
            [InlineKeyboardButton("取消", callback_data="config_cancel")],
        ]
    )


def build_optional_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Prowlarr", callback_data="config_optional:prowlarr")],
            [InlineKeyboardButton("Plex", callback_data="config_optional:plex")],
            [InlineKeyboardButton("TMDB", callback_data="config_optional:tmdb")],
            [InlineKeyboardButton("Fanart.tv", callback_data="config_optional:fanart")],
            [InlineKeyboardButton("TVDB", callback_data="config_optional:tvdb")],
            [InlineKeyboardButton("AI", callback_data="config_optional:ai")],
            [InlineKeyboardButton("返回", callback_data="config_back")],
            [InlineKeyboardButton("取消", callback_data="config_cancel")],
        ]
    )


def build_115_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("115 OpenAPI 扫码授权", callback_data="config_115_mode:openapi")],
            [InlineKeyboardButton("Access / Refresh Token", callback_data="config_115_mode:tokens")],
            [InlineKeyboardButton("取消", callback_data="config_cancel")],
        ]
    )


async def _show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit=False):
    text = "请选择要配置的项目。\n115 是唯一必需配置，可选服务按需填写。"
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=build_config_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=build_config_keyboard())
    return CONFIG_SELECT


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END
    return await _show_config_menu(update, context)


async def config_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END
    return await _show_config_menu(update, context, edit=True)


async def select_config_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    data = query.data or ""
    if data == "config_cancel":
        await query.edit_message_text("已取消配置。")
        return ConversationHandler.END
    if data == "config_back":
        return await _show_config_menu(update, context, edit=True)

    kind = data.split(":", 1)[1]
    context.user_data["config_kind"] = kind
    if kind == "115":
        await query.edit_message_text(
            "请选择 115 授权方式。\n\n"
            "OpenAPI 适合继续使用 /auth 扫码授权；Access/Refresh Token 适合直接从 api.oplist.org 获取 token 后写入配置。",
            reply_markup=build_115_mode_keyboard(),
        )
        return CONFIG_SELECT_115_MODE
    if kind == "optional":
        await query.edit_message_text(
            "请选择要配置的可选服务。",
            reply_markup=build_optional_config_keyboard(),
        )
        return CONFIG_SELECT_OPTIONAL_ITEM

    await query.edit_message_text("⚠️ 未知配置项。")
    return ConversationHandler.END


async def select_optional_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    data = query.data or ""
    if data == "config_cancel":
        await query.edit_message_text("已取消配置。")
        return ConversationHandler.END
    if data == "config_back":
        return await _show_config_menu(update, context, edit=True)

    kind = data.split(":", 1)[1] if ":" in data else ""
    item = OPTIONAL_CONFIG_ITEMS.get(kind)
    if not item:
        await query.edit_message_text("⚠️ 未知可选配置项。")
        return ConversationHandler.END

    context.user_data["config_optional_item"] = kind
    await query.edit_message_text(item["prompt"])
    return CONFIG_INPUT_OPTIONAL


async def select_115_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    data = query.data or ""
    mode = data.split(":", 1)[1] if ":" in data else ""
    if mode == "openapi":
        context.user_data["config_115_mode"] = "openapi"
        await query.edit_message_text(
            "请发送 115_app_id。\n\n"
            "写入后需要继续使用 /auth 发起扫码授权。\n"
            "发送 /q 可取消。"
        )
        return CONFIG_INPUT_115_OPENAPI

    if mode == "tokens":
        context.user_data["config_115_mode"] = "tokens"
        await query.edit_message_text(
            "请到 api.oplist.org 获取 Access token 和 Refresh token。\n\n"
            "第一步：请先发送 Access token。\n"
            "发送 /q 可取消。"
        )
        return CONFIG_INPUT_115_ACCESS

    await query.edit_message_text("⚠️ 未知 115 授权方式。")
    return ConversationHandler.END


async def receive_optional_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = context.user_data.get("config_optional_item")
    item = OPTIONAL_CONFIG_ITEMS.get(kind)
    if not item:
        await update.message.reply_text("❌ 未找到配置项，请重新使用 /config 开始配置。")
        return ConversationHandler.END

    try:
        result = apply_optional_config_payload(kind, update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ {item['label']} 配置写入失败：{e}")
        return CONFIG_INPUT_OPTIONAL

    context.user_data.pop("config_optional_item", None)
    await update.message.reply_text(f"✅ {item['label']} 配置已写入并重新加载。")
    return ConversationHandler.END


async def receive_115_openapi_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        apply_115_openapi_payload(update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ 115_app_id 写入失败：{e}")
        return CONFIG_INPUT_115_OPENAPI

    await update.message.reply_text("✅ 115_app_id 已写入。请继续使用 /auth 发起扫码授权。")
    return ConversationHandler.END


async def receive_115_access_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        access_token = _require_single_value(update.message.text or "", "access_token")
    except Exception as e:
        await update.message.reply_text(f"❌ Access token 无效：{e}")
        return CONFIG_INPUT_115_ACCESS

    context.user_data["config_115_access_token"] = access_token
    await update.message.reply_text(
        "已收到 Access token。\n\n"
        "第二步：请发送 Refresh token。\n"
        "发送 /q 可取消。"
    )
    return CONFIG_INPUT_115_REFRESH


async def receive_115_refresh_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access_token = context.user_data.get("config_115_access_token")
    if not access_token:
        await update.message.reply_text("❌ 未找到上一步的 Access token，请重新使用 /config 开始配置。")
        return ConversationHandler.END

    try:
        result = apply_115_token_values(access_token, update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ 115 Token 写入失败：{e}")
        return CONFIG_INPUT_115_REFRESH

    context.user_data.pop("config_115_access_token", None)
    if result["ready"]:
        await update.message.reply_text("✅ 115 Token 已写入并重新初始化完成。")
    else:
        await update.message.reply_text("⚠️ 115 Token 已写入，但 OpenAPI 初始化仍失败，请检查 Token 是否有效。")
    return ConversationHandler.END


async def quit_config_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text("已取消配置。")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已取消配置。")
    return ConversationHandler.END


def register_config_handlers(application):
    top_level_pattern = r"^config_(select:(115|optional)|cancel|back)$"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PTBUserWarning)
        config_handler = ConversationHandler(
            entry_points=[
                CommandHandler("config", config_command),
                CallbackQueryHandler(config_open_callback, pattern=r"^config_open$"),
                CallbackQueryHandler(select_config_item, pattern=top_level_pattern),
            ],
            states={
                CONFIG_SELECT: [CallbackQueryHandler(select_config_item, pattern=top_level_pattern)],
                CONFIG_SELECT_OPTIONAL_ITEM: [
                    CallbackQueryHandler(select_optional_item, pattern=r"^config_optional:(prowlarr|plex|tmdb|fanart|tvdb|ai)$"),
                    CallbackQueryHandler(select_optional_item, pattern=r"^config_(back|cancel)$"),
                ],
                CONFIG_SELECT_115_MODE: [
                    CallbackQueryHandler(select_115_mode, pattern=r"^config_115_mode:(openapi|tokens)$"),
                    CallbackQueryHandler(select_config_item, pattern=r"^config_cancel$"),
                ],
                CONFIG_INPUT_115_OPENAPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_openapi_id)],
                CONFIG_INPUT_115_ACCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_access_token)],
                CONFIG_INPUT_115_REFRESH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_refresh_token)],
                CONFIG_INPUT_OPTIONAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_optional_config_input)],
            },
            fallbacks=[CommandHandler("q", quit_config_conversation)],
        )
    application.add_handler(config_handler)
    init.logger.info("✅ Config处理器已注册")

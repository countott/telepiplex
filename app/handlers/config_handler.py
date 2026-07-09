# -*- coding: utf-8 -*-
import json
import os
import re
import warnings

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown
from telegram.warnings import PTBUserWarning

import init
from app.utils.message_queue import add_task_to_queue


(
    CONFIG_SELECT,
    CONFIG_SELECT_115_MODE,
    CONFIG_INPUT_115_OPENAPI,
    CONFIG_INPUT_115_ACCESS,
    CONFIG_INPUT_115_REFRESH,
    CONFIG_INPUT_TVDB_API,
    CONFIG_SELECT_TVDB_PIN,
    CONFIG_INPUT_TVDB_PIN,
    CONFIG_INPUT_PLEX_BASE_URL,
    CONFIG_INPUT_PLEX_TOKEN,
) = range(60, 70)


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


def _int_value(values: dict, key: str, default: int, label: str) -> int:
    raw_value = values.get(key)
    if raw_value in (None, ""):
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label} 必须是整数秒") from e


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


def apply_tvdb_values(api_key: str, subscriber_pin: str = "", timeout: int = 15) -> dict:
    api_key = _require_single_value(api_key, "TVDB API Key")
    config = _load_config_file()
    metadata = config.setdefault("metadata", {})
    metadata["tvdb"] = {
        "enable": True,
        "base_url": "https://api4.thetvdb.com/v4",
        "api_key": api_key,
        "subscriber_pin": _single_line_value(subscriber_pin),
        "timeout": int(timeout),
    }
    _write_config_file(config)
    init.load_yaml_config()
    return {"ready": True, "config_file": init.CONFIG_FILE}


def apply_plex_values(base_url: str, token: str) -> dict:
    base_url = _require_single_value(base_url, "Plex base_url").rstrip("/")
    token = _require_single_value(token, "Plex token")
    config = _load_config_file()
    media = config.setdefault("media", {})
    media["plex"] = {
        "base_url": base_url,
        "token": token,
    }
    _write_config_file(config)
    init.load_yaml_config()
    return {"ready": True, "config_file": init.CONFIG_FILE}


def apply_optional_token_payload(kind: str, text: str) -> dict:
    values = parse_key_value_lines(text)

    if kind == "tvdb":
        _require_values(values, ("api_key",))
        return apply_tvdb_values(
            values["api_key"],
            subscriber_pin=values.get("subscriber_pin", ""),
            timeout=_int_value(values, "timeout", 15, "TVDB timeout"),
        )
    elif kind == "plex":
        _require_values(values, ("base_url", "token"))
        return apply_plex_values(values["base_url"], values["token"])
    else:
        raise ValueError(f"未知配置类型: {kind}")


def missing_optional_config_labels(config=None) -> list[str]:
    config = config if isinstance(config, dict) else (init.bot_config or {})
    labels = []
    tvdb = ((config.get("metadata") or {}).get("tvdb") or {})
    if not tvdb.get("enable") or not str(tvdb.get("api_key") or "").strip():
        labels.append("TVDB")

    plex = ((config.get("media") or {}).get("plex") or {})
    if not str(plex.get("base_url") or "").strip() or not str(plex.get("token") or "").strip():
        labels.append("Plex")
    return labels


def build_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("配置 115", callback_data="config_select:115")],
            [InlineKeyboardButton("配置 TVDB", callback_data="config_select:tvdb")],
            [InlineKeyboardButton("配置 Plex", callback_data="config_select:plex")],
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


def build_tvdb_pin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("有 subscriber pin", callback_data="config_tvdb_pin:has")],
            [InlineKeyboardButton("没有 subscriber pin", callback_data="config_tvdb_pin:none")],
            [InlineKeyboardButton("取消", callback_data="config_cancel")],
        ]
    )


def queue_optional_config_notice():
    missing = missing_optional_config_labels()
    if not missing:
        return False
    user_id = (init.bot_config or {}).get("allowed_user")
    if not user_id:
        return False
    message = escape_markdown(
        f"可选配置未完成：{'、'.join(missing)}。可使用 /config 配置，也可以取消忽略。",
        version=2,
    )
    return add_task_to_queue(user_id, None, message=message, keyboard=build_config_keyboard())


async def _show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit=False):
    text = (
        "请选择要配置的项目。\n"
        "115 可选择 OpenAPI 扫码授权或 Access/Refresh Token；TVDB 和 Plex 是可选项，可随时取消。"
    )
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

    kind = data.split(":", 1)[1]
    context.user_data["config_kind"] = kind
    if kind == "115":
        await query.edit_message_text(
            "请选择 115 授权方式。\n\n"
            "OpenAPI 适合继续使用 /auth 扫码授权；Access/Refresh Token 适合直接从 api.oplist.org 获取 token 后写入配置。",
            reply_markup=build_115_mode_keyboard(),
        )
        return CONFIG_SELECT_115_MODE
    if kind == "tvdb":
        await query.edit_message_text(
            "请发送 TVDB API Key。\n"
            "发送 /q 可取消。"
        )
        return CONFIG_INPUT_TVDB_API
    if kind == "plex":
        await query.edit_message_text(
            "请发送 Plex 地址，例如：\n"
            "http://你的Plex地址:32400\n\n"
            "发送 /q 可取消。"
        )
        return CONFIG_INPUT_PLEX_BASE_URL

    await query.edit_message_text("⚠️ 未知配置项。")
    return ConversationHandler.END


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


async def receive_115_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = apply_115_token_payload(update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ 115 Token 写入失败：{e}")
        return CONFIG_INPUT_115_ACCESS

    if result["ready"]:
        await update.message.reply_text("✅ 115 Token 已写入并重新初始化完成。")
    else:
        await update.message.reply_text("⚠️ 115 Token 已写入，但 OpenAPI 初始化仍失败，请检查 Token 是否有效。")
    return ConversationHandler.END


async def receive_tvdb_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        api_key = _require_single_value(update.message.text or "", "TVDB API Key")
    except Exception as e:
        await update.message.reply_text(f"❌ TVDB API Key 无效：{e}")
        return CONFIG_INPUT_TVDB_API

    context.user_data["config_tvdb_api_key"] = api_key
    await update.message.reply_text(
        "已收到 TVDB API Key。\n\n"
        "是否有 subscriber pin？",
        reply_markup=build_tvdb_pin_keyboard(),
    )
    return CONFIG_SELECT_TVDB_PIN


async def select_tvdb_pin_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return ConversationHandler.END

    api_key = context.user_data.get("config_tvdb_api_key")
    if not api_key:
        await query.edit_message_text("❌ 未找到上一步的 TVDB API Key，请重新使用 /config 开始配置。")
        return ConversationHandler.END

    data = query.data or ""
    option = data.split(":", 1)[1] if ":" in data else ""
    if option == "has":
        await query.edit_message_text(
            "请发送 TVDB subscriber pin。\n"
            "发送 /q 可取消。"
        )
        return CONFIG_INPUT_TVDB_PIN

    if option == "none":
        try:
            apply_tvdb_values(api_key, subscriber_pin="")
        except Exception as e:
            await query.edit_message_text(f"❌ TVDB 配置写入失败：{e}")
            return CONFIG_SELECT_TVDB_PIN
        context.user_data.pop("config_tvdb_api_key", None)
        await query.edit_message_text("✅ TVDB 配置已写入。")
        return ConversationHandler.END

    await query.edit_message_text("⚠️ 未知 TVDB pin 选项。")
    return ConversationHandler.END


async def receive_tvdb_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = context.user_data.get("config_tvdb_api_key")
    if not api_key:
        await update.message.reply_text("❌ 未找到上一步的 TVDB API Key，请重新使用 /config 开始配置。")
        return ConversationHandler.END

    try:
        apply_tvdb_values(api_key, subscriber_pin=update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ TVDB 配置写入失败：{e}")
        return CONFIG_INPUT_TVDB_PIN

    context.user_data.pop("config_tvdb_api_key", None)
    await update.message.reply_text("✅ TVDB 配置已写入。")
    return ConversationHandler.END


async def receive_plex_base_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        base_url = _require_single_value(update.message.text or "", "Plex 地址").rstrip("/")
    except Exception as e:
        await update.message.reply_text(f"❌ Plex 地址无效：{e}")
        return CONFIG_INPUT_PLEX_BASE_URL

    context.user_data["config_plex_base_url"] = base_url
    await update.message.reply_text(
        "已收到 Plex 地址。\n\n"
        "第二步：请发送 Plex Token。\n"
        "发送 /q 可取消。"
    )
    return CONFIG_INPUT_PLEX_TOKEN


async def receive_plex_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    base_url = context.user_data.get("config_plex_base_url")
    if not base_url:
        await update.message.reply_text("❌ 未找到上一步的 Plex 地址，请重新使用 /config 开始配置。")
        return ConversationHandler.END

    try:
        apply_plex_values(base_url, update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ Plex 配置写入失败：{e}")
        return CONFIG_INPUT_PLEX_TOKEN

    context.user_data.pop("config_plex_base_url", None)
    await update.message.reply_text("✅ Plex 配置已写入。")
    return ConversationHandler.END


async def receive_optional_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = context.user_data.get("config_kind")
    try:
        apply_optional_token_payload(kind, update.message.text or "")
    except Exception as e:
        await update.message.reply_text(f"❌ 配置写入失败：{e}")
        return CONFIG_INPUT_TVDB_API if kind == "tvdb" else CONFIG_INPUT_PLEX_BASE_URL

    label = "TVDB" if kind == "tvdb" else "Plex"
    await update.message.reply_text(f"✅ {label} 配置已写入。")
    return ConversationHandler.END


async def quit_config_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text("已取消配置。")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="已取消配置。")
    return ConversationHandler.END


def register_config_handlers(application):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PTBUserWarning)
        config_handler = ConversationHandler(
            entry_points=[
                CommandHandler("config", config_command),
                CallbackQueryHandler(config_open_callback, pattern=r"^config_open$"),
                CallbackQueryHandler(select_config_item, pattern=r"^config_(select:(115|tvdb|plex)|cancel)$"),
            ],
            states={
                CONFIG_SELECT: [
                    CallbackQueryHandler(select_config_item, pattern=r"^config_(select:(115|tvdb|plex)|cancel)$")
                ],
                CONFIG_SELECT_115_MODE: [
                    CallbackQueryHandler(select_115_mode, pattern=r"^config_115_mode:(openapi|tokens)$"),
                    CallbackQueryHandler(select_config_item, pattern=r"^config_cancel$"),
                ],
                CONFIG_INPUT_115_OPENAPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_openapi_id)],
                CONFIG_INPUT_115_ACCESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_access_token)],
                CONFIG_INPUT_115_REFRESH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_115_refresh_token)],
                CONFIG_INPUT_TVDB_API: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_tvdb_api_key)],
                CONFIG_SELECT_TVDB_PIN: [
                    CallbackQueryHandler(select_tvdb_pin_option, pattern=r"^config_tvdb_pin:(has|none)$"),
                    CallbackQueryHandler(select_config_item, pattern=r"^config_cancel$"),
                ],
                CONFIG_INPUT_TVDB_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_tvdb_pin)],
                CONFIG_INPUT_PLEX_BASE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plex_base_url)],
                CONFIG_INPUT_PLEX_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plex_token)],
            },
            fallbacks=[CommandHandler("q", quit_config_conversation)],
        )
    application.add_handler(config_handler)
    init.logger.info("✅ Config处理器已注册")

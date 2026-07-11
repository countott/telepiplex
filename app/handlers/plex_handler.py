# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler

import init


def format_job_status(job):
    if not job:
        return "未找到 Plex 任务。"
    text = f"Plex 任务 {job['id']}\n状态：{job['state']}"
    if job.get("rating_key"):
        text += f"\nRating key：{job['rating_key']}"
    if job.get("error"):
        text += f"\n错误：{job['error']}"
    return text


async def plex_command(update, context):
    if not init.check_user(update.effective_user.id):
        await update.effective_message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return
    from app.modules.plex_management import get_plex_management_service

    service = get_plex_management_service()
    if service is None:
        await update.effective_message.reply_text("Plex 管理未启用或缺少 base_url/token。")
        return
    if not getattr(service, "ai_enabled", False) or service.ai is None:
        await update.effective_message.reply_text("Plex AI 管理未启用。")
        return
    request_text = " ".join(context.args or []).strip()
    if not request_text:
        await update.effective_message.reply_text("请在 /plex 后描述要查询或管理的 Plex 内容。")
        return
    result = await asyncio.to_thread(service.ai.run, request_text)
    confirmation = result.get("confirmation") or {}
    reply_markup = None
    token = confirmation.get("confirmation_token")
    if token:
        context.user_data[f"plex_write:{token}"] = confirmation.get("action") or ""
        context.user_data[f"plex_write_payload:{token}"] = confirmation.get("payload") or {}
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("确认执行", callback_data=f"plex_write_confirm:{token}")
        ]])
    await update.effective_message.reply_text(
        result.get("message") or "Plex AI 未返回内容。",
        reply_markup=reply_markup,
    )


async def handle_plex_match_confirmation(update, context):
    query = update.callback_query
    if not init.check_user(update.effective_user.id):
        await query.answer("无权操作", show_alert=True)
        return
    _, job_id, selection = query.data.split(":", 2)
    from app.modules.plex_management import get_plex_management_service

    service = get_plex_management_service()
    job = service.get_job(int(job_id))
    if selection.isdigit() and job:
        waiting = next(
            (
                result
                for result in (job.get("step_results") or {}).values()
                if isinstance(result, dict) and result.get("status") == "waiting"
            ),
            None,
        )
        candidates = (waiting or {}).get("candidates") or []
        index = int(selection)
        if index >= len(candidates):
            await query.answer("候选已失效", show_alert=True)
            return
        candidate = candidates[index]
        selection = (
            candidate.get("rating_key")
            if waiting.get("kind") == "location"
            else candidate.get("guid")
        )
    result = await asyncio.to_thread(
        service.confirm_match,
        int(job_id),
        selection,
    )
    await query.answer()
    await query.edit_message_text(format_job_status(result))


async def handle_plex_write_confirmation(update, context):
    query = update.callback_query
    if not init.check_user(update.effective_user.id):
        await query.answer("无权操作", show_alert=True)
        return
    token = query.data.split(":", 1)[1]
    action = context.user_data.pop(f"plex_write:{token}", "")
    payload = context.user_data.pop(f"plex_write_payload:{token}", {})
    from app.modules.plex_management import get_plex_management_service

    try:
        result = await asyncio.to_thread(
            get_plex_management_service().apply_operation,
            action,
            payload,
            token,
        )
        text = f"Plex 操作已执行：{result['action']}"
    except ValueError as exc:
        text = str(exc)
    await query.answer()
    await query.edit_message_text(text)


def register_plex_handlers(application):
    application.add_handler(CommandHandler("plex", plex_command))
    application.add_handler(
        CallbackQueryHandler(handle_plex_match_confirmation, pattern=r"^plex_match_confirm:")
    )
    application.add_handler(
        CallbackQueryHandler(handle_plex_write_confirmation, pattern=r"^plex_write_confirm:")
    )

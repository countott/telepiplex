# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio

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
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return
    from app.modules.plex_management import get_plex_management_service

    service = get_plex_management_service()
    if service is None:
        await update.message.reply_text("Plex 管理未启用或缺少 base_url/token。")
        return
    jobs = await asyncio.to_thread(service.list_jobs, 5)
    status = await asyncio.to_thread(service.server_status)
    lines = [f"Plex：{status.get('name') or 'online'}", f"最近任务：{len(jobs)}"]
    lines.extend(f"#{job['id']} {job['state']}" for job in jobs)
    await update.message.reply_text("\n".join(lines))


async def handle_plex_match_confirmation(update, context):
    query = update.callback_query
    if not init.check_user(update.effective_user.id):
        await query.answer("无权操作", show_alert=True)
        return
    _, job_id, selection = query.data.split(":", 2)
    from app.modules.plex_management import get_plex_management_service

    result = await asyncio.to_thread(
        get_plex_management_service().confirm_match,
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

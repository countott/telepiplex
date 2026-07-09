# -*- coding: utf-8 -*-

import init
from app.utils.sqlitelib import *
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


def get_failed_tasks():
    """获取所有失败的下载任务"""
    with SqlLiteLib() as sqlite:
        sql = "SELECT * FROM offline_task WHERE is_download = 0 ORDER BY created_at DESC"
        return sqlite.query_all(sql)


def get_retry_task(task_id: int):
    with SqlLiteLib() as sqlite:
        rows = sqlite.query_all("SELECT * FROM offline_task WHERE id = ? AND is_download = 0", (task_id,))
        return rows[0] if rows else None

def mark_task_as_completed(task_id: int):
    """标记任务为已完成"""
    with SqlLiteLib() as sqlite:
        sql = "UPDATE offline_task SET is_download = 1, completed_at = datetime('now') WHERE id = ?"
        sqlite.execute_sql(sql, (task_id,))


def remove_task_from_retry_list(task_id: int):
    """重试开始时从待重试列表移除，避免同一任务被重复调度。"""
    with SqlLiteLib() as sqlite:
        sql = "DELETE FROM offline_task WHERE id = ?"
        sqlite.execute_sql(sql, (task_id,))


def clear_failed_tasks():
    """清空所有失败的重试任务"""
    with SqlLiteLib() as sqlite:
        sql = "DELETE FROM offline_task WHERE is_download = 0"
        sqlite.execute_sql(sql, ())


def _progress_percent(value) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = 0.0
    return max(0.0, min(percent, 100.0))


def _progress_bar(percent) -> str:
    percent = _progress_percent(percent)
    filled = int(percent // 5)
    return "█" * filled + "░" * (20 - filled) + f" {percent:.1f}%"


def _retry_list_keyboard(tasks: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(f"重试 {index}", callback_data=f"retry_task:{task.get('id')}"),
            InlineKeyboardButton(f"丢弃 {index}", callback_data=f"drop_retry:{task.get('id')}"),
        ]
        for index, task in enumerate(tasks, start=1)
        if task.get("id")
    ]
    keyboard.append([InlineKeyboardButton("清空所有", callback_data="clear_all")])
    keyboard.append([InlineKeyboardButton("返回", callback_data="return")])
    return InlineKeyboardMarkup(keyboard)


async def view_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看重试任务列表"""
    user_id = update.message.from_user.id
    if not init.check_user(user_id):
        await update.message.reply_text("⚠️ 当前账号无权使用此机器人。")
        return

    retry_list = [
        task for task in get_failed_tasks()
        if (task.get("retry_category") or "partial") == "partial"
    ]
    if not retry_list:
        await update.message.reply_text("🈳当前重试列表为空")
        return

    retry_text = "重试列表：\n\n"
    for i, task in enumerate(retry_list):
        progress = _progress_bar(task.get("progress_percent"))
        reason = task.get("last_error") or "下载未完成"
        retry_text += f"{i + 1}. {task['title']}\n"
        retry_text += f"进度：{progress}\n"
        retry_text += f"原因：{reason}\n\n"

    await update.message.reply_text(retry_text, reply_markup=_retry_list_keyboard(retry_list))
    
    
async def handle_clear_retry_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理清空重试列表的回调"""
    query = update.callback_query
    await query.answer()
    if not init.check_user(update.effective_user.id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return

    callback_data = query.data
    
    if callback_data == "clear_all":
        clear_failed_tasks()
        await query.edit_message_text("✅ 重试列表已清空！")
    elif callback_data == "return":
        await query.edit_message_text("操作已取消")


async def handle_single_retry_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not init.check_user(user_id):
        await query.edit_message_text("⚠️ 当前账号无权使用此机器人。")
        return

    data = query.data or ""
    action, task_id_text = data.split(":", 1)
    try:
        task_id = int(task_id_text)
    except ValueError:
        await query.edit_message_text("⚠️ 重试任务无效。")
        return

    task = get_retry_task(task_id)
    if not task:
        await query.edit_message_text("⚠️ 重试任务已不存在。")
        return

    if action == "drop_retry":
        remove_task_from_retry_list(task_id)
        await query.edit_message_text(f"已丢弃重试任务：{task.get('title') or '未知任务'}")
        return

    link = str(task.get("magnet") or "").strip()
    save_path = str(task.get("save_path") or "").strip()
    if not link or not save_path:
        await query.edit_message_text("⚠️ 重试任务缺少下载链接或保存目录，已停止。")
        return

    from app.handlers.download_handler import download_executor, download_task

    remove_task_from_retry_list(task_id)
    download_executor.submit(download_task, link, save_path, user_id)
    await query.edit_message_text(
        f"已开始重试：{task.get('title') or '未知任务'}\n"
        f"上次进度：{_progress_bar(task.get('progress_percent'))}"
    )


def register_offline_task_handlers(application):
    """注册离线任务处理器"""
    # 添加独立的命令处理器用于查看重试列表
    application.add_handler(CommandHandler("retry", view_retry_list))
    application.add_handler(CommandHandler("r", view_retry_list))

    # 添加独立的清空重试列表处理器
    application.add_handler(CallbackQueryHandler(handle_clear_retry_list, pattern="^(clear_all|return)$"))
    application.add_handler(CallbackQueryHandler(handle_single_retry_action, pattern=r"^(retry_task|drop_retry):"))
    init.logger.info("✅ Offline Task处理器已注册")

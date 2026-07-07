# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageOriginChannel
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, \
    MessageHandler, filters, CallbackQueryHandler
import init
import os
import uuid
from datetime import datetime
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning
from app.core.video_downloader import video_manager
from app.utils.directory_config import get_save_directories

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)
# 过滤 Telethon 的异步会话实验性功能警告
filterwarnings(action="ignore", message="Using async sessions support is an experimental feature")


async def save_video2115(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return
    
    if not init.tg_user_client:
        message = "⚠️ Telegram 用户客户端初始化失败，配置方法请参考\nhttps://github.com/qiqiandfei/Telegram-115bot/wiki/VideoDownload"
        await update.message.reply_text(message)
        return

    # 检查和建立 Telegram 用户客户端连接
    try:
        if not init.tg_user_client.is_connected():
            init.logger.info("🔄 正在验证 Telegram 用户客户端连接...")
            await init.tg_user_client.connect()
        
        if not await init.tg_user_client.is_user_authorized():
            await update.message.reply_text("❌ Telegram 用户客户端未授权！")
            return
            
    except Exception as e:
        init.logger.error(f"Telegram 用户客户端连接失败: {e}")
        await update.message.reply_text(f"❌ 连接失败: {str(e)}")
        return

    if update.message and update.message.video:
        video = update.message.video
        file_name = video.file_name if video.file_name else f"{datetime.now().strftime('%Y%m%d%H%M%S')}.mp4"
        
        # 获取扩展名
        _, file_ext = os.path.splitext(file_name)
        if not file_ext:
            file_ext = ".mp4"

        # 生成唯一任务ID
        task_id = str(uuid.uuid4())[:8]

        # 立即预获取 Telethon 消息对象
        tg_message = None
        fetch_error = None
        try:
            forward_origin = getattr(update.message, 'forward_origin', None)
            if isinstance(forward_origin, MessageOriginChannel):
                # 从频道转发：直接去原始频道按原始消息 ID 取，避免 bot 会话中找不到
                entity = forward_origin.chat.id
                lookup_msg_id = forward_origin.message_id
                init.logger.info(f"频道转发消息，entity={entity}, message_id={lookup_msg_id}")
            else:
                # 直接发送或非频道转发：在当前会话中按消息 ID 查找
                if update.effective_chat.id == update.effective_user.id:
                    # 私聊：在用户与 Bot 的对话中查找
                    bot_info = await context.bot.get_me()
                    entity = f"@{bot_info.username}" if bot_info.username else bot_info.id
                else:
                    # 群组：在群组中查找
                    entity = update.effective_chat.id
                lookup_msg_id = update.message.message_id
                init.logger.info(f"预获取消息 entity={entity}, message_id={lookup_msg_id}")

            tg_message = await init.tg_user_client.get_messages(entity, ids=lookup_msg_id)
            if tg_message:
                init.logger.info(f"找到视频消息 (ID: {tg_message.id})")
            else:
                fetch_error = f"未找到消息 ID={lookup_msg_id}，实体={entity}"
                init.logger.error(fetch_error)
        except Exception as e:
            fetch_error = str(e)
            init.logger.error(f"预获取 Telethon 消息异常: {e}")

        # 预获取失败则直接报错，不继续
        if fetch_error and not tg_message:
            await update.message.reply_text(
                f"❌ 无法通过 Telethon 用户客户端获取视频消息\n\n"
                f"原因: {fetch_error}\n\n"
                f"请确认：\n"
                f"1. Telethon session 账号与发送视频的 Telegram 账号一致\n"
                f"2. 若在群组中使用，该账号已加入群组\n"
                f"3. 尝试重新发送 /start 后再试"
            )
            return

        # 暂存视频信息到 context.user_data，使用 task_id 作为 key
        context.user_data[f"video_{task_id}"] = {
            "file_name": file_name,
            "file_ext": file_ext,
            "file_size": video.file_size,
            "message_id": update.message.message_id,
            "chat_id": update.effective_chat.id,
            "tg_message": tg_message
        }

        # 询问是否重命名
        keyboard = [
            [InlineKeyboardButton("使用默认名称", callback_data=f"video_rename_default_{task_id}")],
            [InlineKeyboardButton("自定义名称", callback_data=f"video_rename_custom_{task_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=f"📹 收到视频: {file_name}\n❓是否需要重命名？",
            reply_markup=reply_markup,
            reply_to_message_id=update.message.message_id
        )

async def show_directory_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, edit_message: bool = False):
    """显示目录选择界面"""
    video_info = context.user_data.get(f"video_{task_id}")
    if not video_info:
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text("❌ 任务已过期")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ 任务已过期")
        return

    file_name = video_info['file_name']
    
    # 显示主分类
    keyboard = []
    
    # 添加上次保存路径按钮
    last_path = context.user_data.get('last_video_save_path')
    if last_path:
        keyboard.append([InlineKeyboardButton(f"🚀 上次保存: {last_path}", callback_data=f"quick_last_{task_id}")])
        
    keyboard.extend([
        [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"subidx_{index}_{task_id}")]
        for index, category in enumerate(get_save_directories())
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"📹 视频文件: {file_name}\n❓请选择保存目录："
    
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=text,
            reply_markup=reply_markup,
            reply_to_message_id=update.message.message_id
        )

async def handle_rename_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理重命名输入"""
    task_id = context.user_data.get('video_rename_task_id')
    if not task_id:
        return

    new_name = update.message.text.strip()
    
    video_info = context.user_data.get(f"video_{task_id}")
    if video_info:
        # 如果新名字没有扩展名，且我们有原扩展名
        if not os.path.splitext(new_name)[1]:
             file_ext = video_info.get('file_ext', '.mp4')
             new_name += file_ext
             
        video_info['file_name'] = new_name
        # 清除等待状态
        del context.user_data['video_rename_task_id']
        
        # 显示目录选择
        await show_directory_selection(update, context, task_id)



async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        # 忽略 "Query is too old" 错误，这通常发生在点击很久之前的按钮时
        init.logger.debug(f"Callback query answer failed: {e}")
    
    data = query.data
    parts = data.split('_')
    action = parts[0]
    
    if action == "video" and len(parts) > 1 and parts[1] == "rename":
        # 处理重命名选择: video_rename_default_taskId 或 video_rename_custom_taskId
        # parts: ['video', 'rename', 'sub_action', 'task_id']
        if len(parts) < 4:
             return
             
        sub_action = parts[2]
        task_id = parts[3]
        
        if sub_action == "default":
            # 使用默认名称，直接显示目录选择
            await show_directory_selection(update, context, task_id, edit_message=True)
            
        elif sub_action == "custom":
            # 自定义名称，提示输入
            context.user_data['video_rename_task_id'] = task_id
            await query.edit_message_text("⌨️ 请输入新的文件名（无需后缀）：")

    elif action == "main":
        # 兼容旧按钮：直接回到保存目录列表
        task_id = parts[-1]
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"subidx_{index}_{task_id}")]
            for index, category in enumerate(get_save_directories())
        ]
        keyboard.append([InlineKeyboardButton("返回", callback_data=f"back_{task_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("❓请选择保存目录：", reply_markup=reply_markup)
        
    elif action in {"sub", "subidx", "quick"}:
        # 选择目录: sub_path_taskId、subidx_index_taskId 或 quick_last_taskId
        save_path = None
        task_id = None
        
        if action == "sub":
            task_id = parts[-1]
            save_path = "_".join(parts[1:-1])
            # 记录本次保存路径
            context.user_data['last_video_save_path'] = save_path
        elif action == "subidx":
            task_id = parts[2]
            try:
                save_path = get_save_directories()[int(parts[1])]["path"]
            except (IndexError, KeyError, TypeError, ValueError):
                await query.answer("保存目录不可用，请重新选择", show_alert=True)
                return
            context.user_data['last_video_save_path'] = save_path
        elif action == "quick":
            task_id = parts[2]
            save_path = context.user_data.get('last_video_save_path')
            if not save_path:
                await query.answer("上次保存路径已失效，请重新选择", show_alert=True)
                return
        
        video_info = context.user_data.get(f"video_{task_id}")
        if not video_info:
            await query.edit_message_text("❌ 任务信息已过期")
            return

        # 获取原始消息对象
        try:
            # 优先使用收到视频时预获取的消息对象
            target_msg = video_info.get('tg_message')

            if target_msg:
                init.logger.info(f"使用预获取消息 (ID: {target_msg.id})")
            else:
                # 预获取失败，实时重新查找
                init.logger.info(f"预获取消息不可用，重新查找 (msg_id={video_info['message_id']})")
                entity = None
                if video_info['chat_id'] == update.effective_user.id:
                    try:
                        bot_info = await context.bot.get_me()
                        entity = f"@{bot_info.username}" if bot_info.username else bot_info.id
                    except Exception as e:
                        init.logger.error(f"获取Bot信息失败: {e}")
                        entity = init.bot_config.get('bot_name')
                else:
                    entity = video_info['chat_id']

                if not entity:
                    await query.edit_message_text("❌ 无法确定消息来源 (Entity unknown)")
                    return

                # 方法1: 精确 ID 获取
                try:
                    msg = await init.tg_user_client.get_messages(entity, ids=video_info['message_id'])
                    if msg:
                        target_msg = msg
                        init.logger.info(f"精确获取消息成功 (ID: {msg.id})")
                except Exception as e:
                    init.logger.warning(f"精确获取消息失败: {e}")

                # 方法2: 遍历最近消息
                if not target_msg:
                    try:
                        recent_msgs = await init.tg_user_client.get_messages(entity, limit=100)
                        for msg in recent_msgs:
                            if msg.id == video_info['message_id']:
                                target_msg = msg
                                init.logger.info(f"遍历找到消息 (ID: {msg.id})")
                                break
                        if not target_msg:
                            init.logger.warning(f"遍历 100 条消息仍未找到 ID={video_info['message_id']}，实体={entity}")
                    except Exception as e:
                        init.logger.error(f"遍历消息失败: {e}")

            if not target_msg:
                await query.edit_message_text(
                    f"❌ 无法获取原始视频消息\n"
                    f"消息 ID: {video_info['message_id']}\n"
                    f"请检查 Telethon 用户客户端是否与发送视频的账号一致"
                )
                return
                
            # 提交任务到管理器
            task_info = {
                "task_id": task_id,
                "file_name": video_info['file_name'],
                "file_size": video_info['file_size'],
                "save_path": save_path,
                "message": target_msg,
                "context": context,
                "chat_id": update.effective_chat.id,
                "message_id": query.message.message_id  # 更新这条消息的状态
            }
            
            await video_manager.add_task(task_info)
            
            # 清理 user_data
            del context.user_data[f"video_{task_id}"]
            
        except Exception as e:
            init.logger.error(f"提交任务失败: {e}")
            await query.edit_message_text(f"❌ 提交任务失败: {e}")

    elif action == "back":
        task_id = parts[1]
        keyboard = []
        
        # 添加上次保存路径按钮
        last_path = context.user_data.get('last_video_save_path')
        if last_path:
            keyboard.append([InlineKeyboardButton(f"🚀 上次保存: {last_path}", callback_data=f"quick_last_{task_id}")])
            
        keyboard.extend([
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"subidx_{index}_{task_id}")]
            for index, category in enumerate(get_save_directories())
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("❓请选择保存目录：", reply_markup=reply_markup)

    elif action == "v" and parts[1] == "cancel":
        # 取消下载: v_cancel_taskId
        task_id = parts[2]
        success = await video_manager.cancel_task(task_id)
        if success:
            await query.edit_message_text("🛑 正在取消任务...")
        else:
            await query.answer("任务无法取消或已完成", show_alert=True)

    elif action == "cancel":
        # 保留旧逻辑以防万一，或者直接移除
        if len(parts) > 2 and parts[1] == "dl":
            task_id = parts[2]
            success = await video_manager.cancel_task(task_id)
            if success:
                await query.edit_message_text("🛑 正在取消任务...")


def register_video_handlers(application):
    # 注册视频消息处理器
    application.add_handler(MessageHandler(filters.VIDEO, save_video2115))
    
    # 注册重命名输入处理器 (只处理文本，且非命令)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rename_input))
    
    # 注册回调处理器
    # 添加 v_ 前缀支持，添加 rename 前缀支持
    application.add_handler(CallbackQueryHandler(handle_category_selection, pattern="^(main|sub|subidx|back|cancel|quick|v|video_rename)_"))
    
    init.logger.info("✅ Video处理器已注册")
    

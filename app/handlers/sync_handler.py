# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, CallbackQueryHandler
import init
import shutil
from pathlib import Path
from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)


SELECT_MAIN_CATEGORY_SYNC, SELECT_SUB_CATEGORY_SYNC = range(30, 32)


async def sync_strm_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usr_id = update.message.from_user.id
    if not init.check_user(usr_id):
        await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return ConversationHandler.END

    # 显示主分类（电影/剧集）
    keyboard = [
        [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])] for category in
        init.bot_config['category_folder']
    ]
    # 添加退出按钮
    keyboard.append([InlineKeyboardButton("退出", callback_data="quit")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❓请选择要同步的分类：",
                                   reply_markup=reply_markup)
    return SELECT_MAIN_CATEGORY_SYNC


async def select_main_category_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_main_category = query.data
    if selected_main_category == "return":
        # 显示主分类
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['display_name']}", callback_data=category['name'])]
            for category in init.bot_config['category_folder']
        ]
        keyboard.append([InlineKeyboardButton("退出", callback_data="quit")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="❓请选择要同步的分类：",
                                       reply_markup=reply_markup)
        return SELECT_MAIN_CATEGORY_SYNC
    elif selected_main_category == "quit":
        # 直接退出会话
        return await quit_conversation(update, context)
    else:
        context.user_data["selected_main_category"] = selected_main_category
        sub_categories = [
            item['path_map'] for item in init.bot_config["category_folder"] if item['name'] == selected_main_category
        ][0]

        # 创建子分类按钮
        keyboard = [
            [InlineKeyboardButton(f"📁 {category['name']}", callback_data=category['path'])] for category in sub_categories
        ]
        keyboard.append([InlineKeyboardButton("退出", callback_data="quit")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("❓请选择要同步的目录：", reply_markup=reply_markup)
        return SELECT_SUB_CATEGORY_SYNC
    

async def select_sub_category_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # 获取用户选择的路径 "/影视/电影/外语电影/"
    selected_path = query.data
    if selected_path == "quit":
        return await quit_conversation(update, context)
    mount_root = Path(init.bot_config['mount_root'], "/CloudNAS/115")
    strm_root = Path(init.bot_config['strm_root'], "/media/115")
    openlist_root = Path(init.bot_config['openlist_root'], "/115")
    init.logger.debug(f"selected_path: {selected_path}")
    try:
        strm_mode = init.bot_config.get("strm_mode", "disable")
        if strm_mode == "disable":
            await query.edit_message_text(text="⚠️ 当前strm同步功能已禁用！")
            return ConversationHandler.END
        # 递归删除所有
        sync_path = strm_root / Path(selected_path).relative_to("/")
        if sync_path.exists() and sync_path.is_dir():
            shutil.rmtree(str(sync_path))

        await query.edit_message_text(text=f"🔄[{selected_path}]正在同步strm文件，请稍后...")
        # 获取视频文件列表
        video_files = init.openapi_115.get_sync_dir(selected_path)
        for file in video_files:
            try:
                # file = "FC2-PPV-4750727/hhd800.com@FC2-PPV-4750727.mp4"
                # file 现在包含子目录路径，需要构建完整路径
                full_file_path = f"{selected_path}/{file}"
                file_path = Path(full_file_path)
                if strm_mode == "strm_local":
                    video_path = mount_root / file_path.relative_to("/")
                    strm_path = strm_root / file_path.parent.relative_to("/")
                    if not strm_path.exists():
                        strm_path.mkdir(parents=True, exist_ok=True)
                    strm_content = str(video_path)
                    # 使用实际文件名（不含路径）来生成strm文件名
                    actual_filename = Path(file).name  # 获取真正的文件名
                    strm_file = strm_path / (Path(actual_filename).stem + ".strm")
                else:
                    movie_dir = create_movie_directory(sync_path, file)
                    if not movie_dir.exists():
                        movie_dir.mkdir(parents=True, exist_ok=True)
                    strm_file = movie_dir / (Path(file).stem + ".strm")
                    strm_content = f"{openlist_root}/{file_path.relative_to('/')}"
                
                with open(strm_file, 'w') as f:
                    f.write(strm_content)
                init.logger.info(f"成功创建 strm 文件: {strm_file}")
            except Exception as file_error:
                init.logger.error(f"处理文件 {file} 时出错: {str(file_error)}")
                continue
        await query.edit_message_text(text=f"✅ [{selected_path}]strm文件同步完成！")
        return ConversationHandler.END
    except Exception as e:
        await query.edit_message_text(text=f"❌ 同步strm文件失败：{str(e)}！")
        return ConversationHandler.END
    

async def quit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 检查是否是回调查询
    if update.callback_query:
        await update.callback_query.edit_message_text(text="🚪用户退出本次会话")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🚪用户退出本次会话")
    return ConversationHandler.END

def create_movie_directory(sync_path, movie_path):
    if movie_path.startswith("/"):
        movie_path = movie_path[1:]
    
    parts = Path(movie_path).parts
    if len(parts) < 2:
        return sync_path

    folder_name = parts[0]
    movie_name = parts[1]
    # 如果文件名中不包含文件夹名称，则创建一个以电影名称命名的文件夹，并将strm文件放入其中
    if not folder_name in movie_name:
        movie_dir = sync_path / Path(folder_name) / Path(movie_name).stem
        if not movie_dir.exists():
            movie_dir.mkdir(parents=True, exist_ok=True)
            return movie_dir
    return sync_path


def register_sync_handlers(application):
    # 同步strm软链
    sync_handler = ConversationHandler(
        entry_points=[CommandHandler("strm", sync_strm_files)],
        states={
            SELECT_MAIN_CATEGORY_SYNC: [CallbackQueryHandler(select_main_category_sync)],
            SELECT_SUB_CATEGORY_SYNC: [CallbackQueryHandler(select_sub_category_sync)],
        },
        fallbacks=[CommandHandler("q", quit_conversation)],
        per_chat=True
    )
    application.add_handler(sync_handler)
    init.logger.info("✅ Sync处理器已注册")

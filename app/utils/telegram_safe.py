# -*- coding: utf-8 -*-


async def safe_reply_text(message, text: str, logger=None, **kwargs) -> bool:
    try:
        await message.reply_text(text, **kwargs)
        return True
    except Exception as e:
        if logger:
            logger.warn(f"Telegram进度消息发送失败，继续处理: {e}")
        return False

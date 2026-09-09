from __future__ import annotations

from collections.abc import Mapping

from telegram import Bot
from telegram.ext import ExtBot
from telegram.request import HTTPXRequest

from app.utils.log_sanitizer import REDACTED, sanitize_log_text

try:
    import init
except ModuleNotFoundError:  # pragma: no cover - package-imported test/runtime fallback
    from app import init


def _message_id(result, target=None) -> int | None:
    value = getattr(result, "message_id", None)
    if not isinstance(value, int) or isinstance(value, bool):
        value = target
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _reply_markup_data(reply_markup) -> object | None:
    if reply_markup is None:
        return None
    to_dict = getattr(reply_markup, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(reply_markup, Mapping):
        return dict(reply_markup)
    return str(reply_markup)


def _keyboard_intent(reply_markup) -> dict:
    markup = _reply_markup_data(reply_markup)
    rows = markup.get("inline_keyboard", ()) if isinstance(markup, Mapping) else ()
    count = sum(len(row) for row in rows)
    return {"intent": "replace" if count else "clear", "button_count": count}


def _record_delivery(
    *,
    action: str,
    text: str | None,
    chat_id,
    result,
    reply_markup=None,
    message_id=None,
    keyboard=None,
) -> None:
    if keyboard is not None and result is False:
        _record_cleanup_failure(
            action=action,
            chat_id=chat_id,
            message_id=message_id,
            keyboard=keyboard,
            error=None,
            token="",
        )
        return
    logger = getattr(init, "logger", None)
    method = getattr(logger, "info", None) if logger is not None else None
    if not callable(method):
        return
    user_surface = {
        "direction": "outgoing",
        "action": str(action),
        "text": None if text is None else str(text),
    }
    markup = _reply_markup_data(reply_markup)
    if markup not in (None, {}, []):
        user_surface["data"] = {"reply_markup": markup}
    input_fields = {"chat_id": chat_id}
    if message_id is not None:
        input_fields["message_id"] = message_id
    if keyboard is not None:
        input_fields["keyboard"] = keyboard
    method(
        "Telegram API 内容已送达",
        event_name="telegram.api.delivered",
        diagnostic_fields={
            "stage": "telegram_delivery",
            "status": "completed",
            "input": input_fields,
            "user_surface": user_surface,
            "output": {"message_id": _message_id(result, message_id), "success": True},
        },
    )


def _record_cleanup_failure(*, action, chat_id, message_id, keyboard, error, token):
    logger = getattr(init, "logger", None)
    method = getattr(logger, "warning", None) if logger is not None else None
    if not callable(method):
        return
    message = str(error) if error is not None else "Telegram API returned false."
    if token:
        message = message.replace(token, REDACTED)
    method(
        "Telegram API 消息清理失败",
        event_name="telegram.api.failed",
        diagnostic_fields={
            "stage": "telegram_delivery",
            "status": "failed",
            "input": {
                "chat_id": chat_id,
                "message_id": message_id,
                "keyboard": keyboard,
            },
            "user_surface": {"direction": "outgoing", "action": action, "text": None},
            "output": {
                "message_id": message_id,
                "success": False,
                "error_type": type(error).__name__ if error is not None else None,
                "error_message": sanitize_log_text(message),
            },
        },
    )


class _DiagnosticBotMixin:
    async def send_message(self, chat_id, text, *args, **kwargs):
        result = await super().send_message(chat_id, text, *args, **kwargs)
        _record_delivery(
            action="send_message",
            text=text,
            chat_id=chat_id,
            result=result,
            reply_markup=kwargs.get("reply_markup"),
        )
        return result

    async def send_photo(self, chat_id, photo, caption=None, *args, **kwargs):
        result = await super().send_photo(
            chat_id,
            photo,
            caption,
            *args,
            **kwargs,
        )
        _record_delivery(
            action="send_photo",
            text=caption,
            chat_id=chat_id,
            result=result,
            reply_markup=kwargs.get("reply_markup"),
        )
        return result

    async def edit_message_text(
        self,
        text,
        chat_id=None,
        message_id=None,
        *args,
        **kwargs,
    ):
        result = await super().edit_message_text(
            text,
            chat_id,
            message_id,
            *args,
            **kwargs,
        )
        _record_delivery(
            action="edit_message",
            text=text,
            chat_id=chat_id,
            result=result,
            message_id=message_id,
            reply_markup=kwargs.get("reply_markup"),
        )
        return result

    async def edit_message_caption(
        self,
        chat_id=None,
        message_id=None,
        inline_message_id=None,
        caption=None,
        *args,
        **kwargs,
    ):
        result = await super().edit_message_caption(
            chat_id,
            message_id,
            inline_message_id,
            caption,
            *args,
            **kwargs,
        )
        _record_delivery(
            action="edit_message",
            text=caption,
            chat_id=chat_id,
            result=result,
            message_id=message_id,
            reply_markup=kwargs.get("reply_markup"),
        )
        return result

    async def edit_message_media(
        self,
        media,
        chat_id=None,
        message_id=None,
        *args,
        **kwargs,
    ):
        result = await super().edit_message_media(
            media,
            chat_id,
            message_id,
            *args,
            **kwargs,
        )
        _record_delivery(
            action="edit_photo",
            text=getattr(media, "caption", None),
            chat_id=chat_id,
            result=result,
            message_id=message_id,
            reply_markup=kwargs.get("reply_markup"),
        )
        return result

    async def edit_message_reply_markup(
        self,
        chat_id=None,
        message_id=None,
        inline_message_id=None,
        reply_markup=None,
        *args,
        **kwargs,
    ):
        keyboard = _keyboard_intent(reply_markup)
        try:
            result = await super().edit_message_reply_markup(
                chat_id,
                message_id,
                inline_message_id,
                reply_markup,
                *args,
                **kwargs,
            )
        except Exception as exc:
            _record_cleanup_failure(
                action="edit_message_reply_markup",
                chat_id=chat_id,
                message_id=message_id,
                keyboard=keyboard,
                error=exc,
                token=self.token,
            )
            raise
        _record_delivery(
            action="edit_message_reply_markup",
            text=None,
            chat_id=chat_id,
            message_id=message_id,
            result=result,
            reply_markup=reply_markup,
            keyboard=keyboard,
        )
        return result

    async def delete_message(self, chat_id, message_id, *args, **kwargs):
        keyboard = {"intent": "delete_message", "button_count": 0}
        try:
            result = await super().delete_message(chat_id, message_id, *args, **kwargs)
        except Exception as exc:
            _record_cleanup_failure(
                action="delete_message",
                chat_id=chat_id,
                message_id=message_id,
                keyboard=keyboard,
                error=exc,
                token=self.token,
            )
            raise
        _record_delivery(
            action="delete_message",
            text=None,
            chat_id=chat_id,
            message_id=message_id,
            result=result,
            keyboard=keyboard,
        )
        return result

    async def answer_callback_query(
        self,
        callback_query_id,
        text=None,
        *args,
        **kwargs,
    ):
        result = await super().answer_callback_query(
            callback_query_id,
            text,
            *args,
            **kwargs,
        )
        if text not in (None, ""):
            _record_delivery(
                action="answer_callback",
                text=text,
                chat_id=None,
                result=result,
            )
        return result


class DiagnosticBot(_DiagnosticBotMixin, Bot):
    pass


class DiagnosticExtBot(_DiagnosticBotMixin, ExtBot):
    pass


def build_diagnostic_ext_bot(token: str, *, timeout: float) -> DiagnosticExtBot:
    request_options = {
        "read_timeout": timeout,
        "write_timeout": timeout,
        "connect_timeout": timeout,
        "pool_timeout": timeout,
    }
    return DiagnosticExtBot(
        token=token,
        request=HTTPXRequest(connection_pool_size=256, **request_options),
        get_updates_request=HTTPXRequest(connection_pool_size=1, **request_options),
    )

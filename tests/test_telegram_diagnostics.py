import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ExtBot


class TelegramDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_ext_bot_records_the_exact_successful_reply(self):
        from app.runtime import telegram_diagnostics
        from app.runtime.telegram_diagnostics import DiagnosticExtBot

        logger = Mock()
        bot = DiagnosticExtBot("123456:test-token")
        delivered = SimpleNamespace(message_id=912)

        with (
            patch.object(telegram_diagnostics.init, "logger", logger),
            patch.object(ExtBot, "send_message", AsyncMock(return_value=delivered)),
        ):
            result = await bot.send_message(chat_id=10, text="完整前台回复")

        self.assertIs(result, delivered)
        call = logger.info.call_args
        self.assertEqual(call.kwargs["event_name"], "telegram.api.delivered")
        self.assertEqual(call.kwargs["diagnostic_fields"]["user_surface"], {
            "direction": "outgoing",
            "action": "send_message",
            "text": "完整前台回复",
        })
        self.assertEqual(
            call.kwargs["diagnostic_fields"]["output"]["message_id"],
            912,
        )

    async def test_diagnostic_ext_bot_does_not_claim_a_failed_reply_was_delivered(self):
        from app.runtime import telegram_diagnostics
        from app.runtime.telegram_diagnostics import DiagnosticExtBot

        logger = Mock()
        bot = DiagnosticExtBot("123456:test-token")

        with (
            patch.object(telegram_diagnostics.init, "logger", logger),
            patch.object(
                ExtBot,
                "send_message",
                AsyncMock(side_effect=RuntimeError("Telegram unavailable")),
            ),
            self.assertRaisesRegex(RuntimeError, "Telegram unavailable"),
        ):
            await bot.send_message(chat_id=10, text="不会成功的回复")

        logger.info.assert_not_called()

    async def test_markup_delivery_records_the_target_and_keyboard_intent(self):
        from app.runtime import telegram_diagnostics

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("选择", callback_data="choose:1")],
            [InlineKeyboardButton("退出", callback_data="exit")],
        ])
        for bot_class, base_class in (
            (telegram_diagnostics.DiagnosticBot, Bot),
            (telegram_diagnostics.DiagnosticExtBot, ExtBot),
        ):
            for markup, expected_keyboard in (
                (None, {"intent": "clear", "button_count": 0}),
                (InlineKeyboardMarkup([]), {"intent": "clear", "button_count": 0}),
                (keyboard, {"intent": "replace", "button_count": 2}),
            ):
                with self.subTest(bot=bot_class.__name__, markup=markup):
                    logger = Mock()
                    bot = bot_class("123456:test-token")
                    with (
                        patch.object(telegram_diagnostics.init, "logger", logger),
                        patch.object(base_class, "edit_message_reply_markup", AsyncMock(return_value=True)),
                    ):
                        result = await bot.edit_message_reply_markup(10, 912, None, markup)

                    self.assertIs(result, True)
                    self.assertIsNotNone(logger.info.call_args)
                    event = logger.info.call_args.kwargs
                    self.assertEqual(event["event_name"], "telegram.api.delivered")
                    fields = event["diagnostic_fields"]
                    self.assertEqual(fields["input"]["message_id"], 912)
                    self.assertEqual(fields["input"]["keyboard"], expected_keyboard)
                    self.assertEqual(fields["output"], {"message_id": 912, "success": True})
                    self.assertEqual(fields["user_surface"]["action"], "edit_message_reply_markup")

    async def test_delete_delivery_keeps_the_target_when_telegram_returns_true(self):
        from app.runtime import telegram_diagnostics

        logger = Mock()
        bot = telegram_diagnostics.DiagnosticExtBot("123456:test-token")
        with (
            patch.object(telegram_diagnostics.init, "logger", logger),
            patch.object(ExtBot, "delete_message", AsyncMock(return_value=True)),
        ):
            result = await bot.delete_message(chat_id=10, message_id=913)

        self.assertIs(result, True)
        self.assertIsNotNone(logger.info.call_args)
        fields = logger.info.call_args.kwargs["diagnostic_fields"]
        self.assertEqual(fields["user_surface"]["action"], "delete_message")
        self.assertEqual(fields["input"]["message_id"], 913)
        self.assertEqual(fields["input"]["keyboard"], {"intent": "delete_message", "button_count": 0})
        self.assertEqual(fields["output"], {"message_id": 913, "success": True})

    async def test_cleanup_failure_records_safe_error_and_preserves_the_exception(self):
        from app.runtime import telegram_diagnostics

        token = "123456:test-token"
        for action, kwargs, expected_keyboard in (
            ("edit_message_reply_markup", {"reply_markup": None}, {"intent": "clear", "button_count": 0}),
            ("delete_message", {}, {"intent": "delete_message", "button_count": 0}),
        ):
            with self.subTest(action=action):
                logger = Mock()
                bot = telegram_diagnostics.DiagnosticExtBot(token)
                failure = RuntimeError(f"failed token {token} https://api.telegram.org/bot{token}/{action}?token=other-secret")
                with (
                    patch.object(telegram_diagnostics.init, "logger", logger),
                    patch.object(ExtBot, action, AsyncMock(side_effect=failure)),
                    self.assertRaises(RuntimeError) as raised,
                ):
                    await getattr(bot, action)(chat_id=10, message_id=914, **kwargs)

                self.assertIs(raised.exception, failure)
                logger.info.assert_not_called()
                self.assertIsNotNone(logger.warning.call_args)
                event = logger.warning.call_args.kwargs
                self.assertEqual(event["event_name"], "telegram.api.failed")
                fields = event["diagnostic_fields"]
                self.assertEqual(fields["status"], "failed")
                self.assertEqual(fields["user_surface"]["action"], action)
                self.assertEqual(fields["input"]["message_id"], 914)
                self.assertEqual(fields["input"]["keyboard"], expected_keyboard)
                self.assertEqual(fields["output"]["success"], False)
                self.assertEqual(fields["output"]["error_type"], "RuntimeError")
                serialized = json.dumps(event)
                self.assertNotIn(token, serialized)
                self.assertNotIn("other-secret", serialized)
                self.assertIn("redacted", serialized)

    async def test_existing_text_edit_delivery_keeps_the_target_on_bool_result(self):
        from app.runtime import telegram_diagnostics

        logger = Mock()
        bot = telegram_diagnostics.DiagnosticExtBot("123456:test-token")
        with (
            patch.object(telegram_diagnostics.init, "logger", logger),
            patch.object(ExtBot, "edit_message_text", AsyncMock(return_value=True)),
        ):
            await bot.edit_message_text("完成", chat_id=10, message_id=915)

        fields = logger.info.call_args.kwargs["diagnostic_fields"]
        self.assertEqual(fields["output"]["message_id"], 915)

    async def test_cleanup_false_result_is_not_reported_as_delivered(self):
        from app.runtime import telegram_diagnostics

        for action in ("edit_message_reply_markup", "delete_message"):
            with self.subTest(action=action):
                logger = Mock()
                bot = telegram_diagnostics.DiagnosticExtBot("123456:test-token")
                with (
                    patch.object(telegram_diagnostics.init, "logger", logger),
                    patch.object(ExtBot, action, AsyncMock(return_value=False)),
                ):
                    result = await getattr(bot, action)(chat_id=10, message_id=916)

                self.assertIs(result, False)
                logger.info.assert_not_called()
                self.assertIsNotNone(logger.warning.call_args)
                event = logger.warning.call_args.kwargs
                self.assertEqual(event["event_name"], "telegram.api.failed")
                self.assertEqual(event["diagnostic_fields"]["output"]["success"], False)
                self.assertEqual(event["diagnostic_fields"]["output"]["message_id"], 916)


def test_host_application_uses_the_diagnostic_bot_for_all_replies():
    from app.runtime.telegram_diagnostics import DiagnosticExtBot
    from tests.test_bot_runtime_startup import load_bot_module

    bot_module = load_bot_module()
    application = bot_module.build_application("123456:test-token")

    assert isinstance(application.bot, DiagnosticExtBot)

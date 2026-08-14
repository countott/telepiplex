import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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


def test_host_application_uses_the_diagnostic_bot_for_all_replies():
    from app.runtime.telegram_diagnostics import DiagnosticExtBot
    from tests.test_bot_runtime_startup import load_bot_module

    bot_module = load_bot_module()
    application = bot_module.build_application("123456:test-token")

    assert isinstance(application.bot, DiagnosticExtBot)

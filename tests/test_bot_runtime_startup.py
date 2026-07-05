import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "app" / "core"))


def load_bot_module():
    spec = importlib.util.spec_from_file_location("telepiplex_bot_entry", ROOT / "app" / "115bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotRuntimeStartupTest(unittest.TestCase):
    def test_start_treats_telegram_timeout_as_possible_delivery(self):
        bot_module = load_bot_module()

        update = Mock()
        update.effective_chat.id = 472943219
        context = Mock()
        context.bot.send_message = AsyncMock(side_effect=bot_module.TimedOut("Timed out"))

        asyncio.run(bot_module.start(update, context))

        context.bot.send_message.assert_awaited_once()

    def test_build_application_uses_longer_telegram_timeouts(self):
        bot_module = load_bot_module()
        calls = []

        class FakeBuilder:
            def token(self, token):
                calls.append(("token", token))
                return self

            def post_init(self, callback):
                calls.append(("post_init", callback))
                return self

            def connect_timeout(self, value):
                calls.append(("connect_timeout", value))
                return self

            def read_timeout(self, value):
                calls.append(("read_timeout", value))
                return self

            def write_timeout(self, value):
                calls.append(("write_timeout", value))
                return self

            def pool_timeout(self, value):
                calls.append(("pool_timeout", value))
                return self

            def build(self):
                calls.append(("build", None))
                return "application"

        original_builder = bot_module.Application.builder
        bot_module.Application.builder = Mock(return_value=FakeBuilder())
        try:
            application = bot_module.build_application("token")
        finally:
            bot_module.Application.builder = original_builder

        self.assertEqual(application, "application")
        self.assertIn(("connect_timeout", 30), calls)
        self.assertIn(("read_timeout", 30), calls)
        self.assertIn(("write_timeout", 30), calls)
        self.assertIn(("pool_timeout", 30), calls)

    def test_run_application_polling_starts_application_before_updater_polling(self):
        bot_module = load_bot_module()
        calls = []
        stop_event = asyncio.Event()
        stop_event.set()

        class FakeUpdater:
            running = False

            async def start_polling(self, **kwargs):
                calls.append(("updater.start_polling", kwargs))
                self.running = True

            async def stop(self):
                calls.append(("updater.stop", {}))
                self.running = False

        class FakeApplication:
            def __init__(self):
                self.updater = FakeUpdater()
                self.post_init = AsyncMock(side_effect=lambda app: calls.append(("post_init", {})))
                self.running = False

            async def initialize(self):
                calls.append(("initialize", {}))

            async def start(self):
                calls.append(("start", {}))
                self.running = True

            async def stop(self):
                calls.append(("stop", {}))
                self.running = False

            async def shutdown(self):
                calls.append(("shutdown", {}))

        application = FakeApplication()

        asyncio.run(bot_module.run_application_polling(application, stop_event=stop_event))

        self.assertEqual(
            [name for name, _ in calls],
            ["initialize", "post_init", "start", "updater.start_polling", "updater.stop", "stop", "shutdown"],
        )
        self.assertEqual(calls[3][1]["bootstrap_retries"], 5)

    def test_run_application_polling_retries_transient_initialize_timeout(self):
        bot_module = load_bot_module()
        calls = []
        stop_event = asyncio.Event()
        stop_event.set()

        class FakeUpdater:
            running = False

            async def start_polling(self, **kwargs):
                calls.append(("updater.start_polling", kwargs))
                self.running = True

            async def stop(self):
                calls.append(("updater.stop", {}))
                self.running = False

        class FakeApplication:
            def __init__(self):
                self.updater = FakeUpdater()
                self.post_init = AsyncMock(side_effect=lambda app: calls.append(("post_init", {})))
                self.running = False
                self.initialize_attempts = 0

            async def initialize(self):
                self.initialize_attempts += 1
                calls.append(("initialize", {"attempt": self.initialize_attempts}))
                if self.initialize_attempts < 3:
                    raise bot_module.TimedOut("Timed out")

            async def start(self):
                calls.append(("start", {}))
                self.running = True

            async def stop(self):
                calls.append(("stop", {}))
                self.running = False

            async def shutdown(self):
                calls.append(("shutdown", {}))

        application = FakeApplication()

        asyncio.run(
            bot_module.run_application_polling(
                application,
                stop_event=stop_event,
                initialize_retry_delay=0,
            )
        )

        self.assertEqual(
            [name for name, _ in calls],
            [
                "initialize",
                "initialize",
                "initialize",
                "post_init",
                "start",
                "updater.start_polling",
                "updater.stop",
                "stop",
                "shutdown",
            ],
        )


if __name__ == "__main__":
    unittest.main()

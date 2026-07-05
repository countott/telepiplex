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


if __name__ == "__main__":
    unittest.main()

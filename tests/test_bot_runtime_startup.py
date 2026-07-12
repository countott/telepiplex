import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def load_bot_module():
    spec = importlib.util.spec_from_file_location(
        "telepiplex_plugin_bot_entry",
        ROOT / "app/115bot.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotPluginRuntimeStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_after_start_is_awaited_before_polling_wait(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        application = Mock()
        application.initialize = AsyncMock()
        application.start = AsyncMock()
        application.stop = AsyncMock()
        application.shutdown = AsyncMock()
        application.post_init = None
        application.updater = None
        stop_event = asyncio.Event()
        calls = []

        async def after_start():
            calls.append("restored")
            stop_event.set()

        await bot_module.run_application_polling(
            application,
            after_start=after_start,
            stop_event=stop_event,
            initialize_retry_delay=0,
        )

        self.assertEqual(calls, ["restored"])
        application.shutdown.assert_awaited_once()

    async def test_build_plugin_manager_uses_core_config_paths(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = bot_module.build_plugin_manager({
                "plugins": {
                    "root": str(root / "plugins"),
                    "runtime_root": str(root / "plugins" / ".runtime"),
                    "startup_timeout": 1,
                    "restart_limit": 2,
                    "event_delivery_timeout": 777,
                }
            }, core_database=root / "core.db")
            self.addAsyncCleanup(manager.close)

            self.assertEqual(manager.store.root, (root / "plugins").resolve())
            self.assertEqual(manager.journal.database_path, root / "core.db")
            self.assertEqual(manager.supervisor.restart_limit, 2)
            self.assertEqual(manager.broker.dispatcher.delivery_deadline, 777)
            self.assertEqual(manager.broker.socket_path, root / "plugins" / ".runtime/core.sock")

            await manager.start()
            self.assertTrue(manager.broker.socket_path.exists())

    async def test_shutdown_stops_telegram_intake_before_feature_manager(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        events = []
        manager = Mock()
        manager.close = AsyncMock(side_effect=lambda: events.append("manager.close"))
        updater = Mock(running=True)
        updater.start_polling = AsyncMock()
        updater.stop = AsyncMock(side_effect=lambda: events.append("updater.stop"))
        application = Mock(running=True)
        application.bot_data = {"telepiplex_plugin_manager": manager}
        application.initialize = AsyncMock()
        application.start = AsyncMock()
        application.stop = AsyncMock(side_effect=lambda: events.append("application.stop"))
        application.shutdown = AsyncMock(side_effect=lambda: events.append("application.shutdown"))
        application.post_init = None
        application.updater = updater
        stop_event = asyncio.Event()
        stop_event.set()

        await bot_module.run_application_polling(
            application,
            stop_event=stop_event,
            initialize_retry_delay=0,
        )

        self.assertEqual(events, [
            "updater.stop",
            "application.stop",
            "manager.close",
            "application.shutdown",
        ])


if __name__ == "__main__":
    unittest.main()

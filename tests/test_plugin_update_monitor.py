import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class PluginUpdateMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_notifies_each_transition_once(self):
        from app.core.plugin_update_monitor import PluginUpdateMonitor

        update = SimpleNamespace(
            plugin_id="echo",
            current_version="1.0.0",
            target_version="1.1.0",
            reference="echo@1.1.0",
            source_commit="b" * 40,
        )
        manager = SimpleNamespace(
            available_updates=AsyncMock(return_value=[update])
        )
        notify = AsyncMock(return_value=True)
        monitor = PluginUpdateMonitor(manager, notify, interval=300)

        await monitor.run_once()
        await monitor.run_once()

        notify.assert_awaited_once_with(update)
        self.assertEqual(manager.available_updates.await_count, 2)

    async def test_catalog_failure_is_soft_and_run_is_cancellable(self):
        from app.core.plugin_update_monitor import PluginUpdateMonitor

        manager = SimpleNamespace(
            available_updates=AsyncMock(side_effect=RuntimeError("network down"))
        )
        monitor = PluginUpdateMonitor(
            manager,
            AsyncMock(),
            interval=300,
            logger=SimpleNamespace(warn=lambda _message: None),
        )

        self.assertEqual(await monitor.run_once(), [])

        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_catalog_failure_warning_uses_stable_code_without_details(self):
        from app.core.plugin_catalog import CatalogError
        from app.core.plugin_update_monitor import PluginUpdateMonitor

        warnings = []
        manager = SimpleNamespace(
            available_updates=AsyncMock(
                side_effect=CatalogError(
                    "catalog_unavailable",
                    "token=secret",
                )
            )
        )
        monitor = PluginUpdateMonitor(
            manager,
            AsyncMock(),
            interval=300,
            logger=SimpleNamespace(warn=warnings.append),
        )

        await monitor.run_once()

        self.assertEqual(len(warnings), 1)
        self.assertIn("catalog_unavailable", warnings[0])
        self.assertNotIn("CatalogError", warnings[0])
        self.assertNotIn("secret", warnings[0])


if __name__ == "__main__":
    unittest.main()

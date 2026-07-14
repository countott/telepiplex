import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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
    async def test_missing_legacy_catalog_uses_official_release_catalog(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plugins_root = root / "plugins"

            source = bot_module.resolve_plugin_catalog_source({}, plugins_root)
            self.assertEqual(source, bot_module.DEFAULT_PLUGIN_CATALOG_URL)

            legacy = plugins_root / "catalog.yaml"
            source = bot_module.resolve_plugin_catalog_source(
                {"catalog": str(legacy)},
                plugins_root,
            )
            self.assertEqual(source, bot_module.DEFAULT_PLUGIN_CATALOG_URL)

    async def test_existing_or_custom_local_catalog_is_preserved(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            plugins_root = root / "plugins"
            plugins_root.mkdir()
            legacy = plugins_root / "catalog.yaml"
            legacy.touch()

            source = bot_module.resolve_plugin_catalog_source(
                {"catalog": str(legacy)},
                plugins_root,
            )
            self.assertEqual(source, str(legacy))

            custom = root / "custom" / "catalog.yaml"
            source = bot_module.resolve_plugin_catalog_source(
                {"catalog": str(custom)},
                plugins_root,
            )
            self.assertEqual(source, str(custom))

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

    async def test_build_plugin_manager_preserves_remote_catalog_url(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        remote = (
            "https://github.com/countott/telepiplex/releases/latest/"
            "download/catalog.yaml"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = bot_module.build_plugin_manager({
                "plugins": {
                    "root": str(root / "plugins"),
                    "catalog": remote,
                }
            }, core_database=root / "core.db")
            self.addAsyncCleanup(manager.close)

            self.assertEqual(manager._artifact_resolver.catalog_url, remote)
            self.assertEqual(
                manager._artifact_resolver.catalog_path,
                root / "plugins" / ".cache/catalog.yaml",
            )

    async def test_shutdown_stops_telegram_intake_before_feature_manager(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        events = []

        async def monitor():
            try:
                await asyncio.Event().wait()
            finally:
                events.append("monitor.cancel")

        monitor_task = asyncio.create_task(monitor())
        await asyncio.sleep(0)
        manager = Mock()
        manager.close = AsyncMock(side_effect=lambda: events.append("manager.close"))
        updater = Mock(running=True)
        updater.start_polling = AsyncMock()
        updater.stop = AsyncMock(side_effect=lambda: events.append("updater.stop"))
        application = Mock(running=True)
        application.bot_data = {
            "telepiplex_plugin_manager": manager,
            "telepiplex_plugin_update_task": monitor_task,
        }
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
            "monitor.cancel",
            "manager.close",
            "application.shutdown",
        ])

    async def test_update_notification_contains_one_click_and_decline_buttons(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        application = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        update = SimpleNamespace(
            plugin_id="echo",
            current_version="1.0.0",
            target_version="1.1.0",
            reference="echo@1.1.0",
            source_commit="b" * 40,
        )

        with patch.object(bot_module.init, "bot_config", {"allowed_user": 42}):
            sent = await bot_module.send_plugin_update_notification(
                application, update
            )

        self.assertTrue(sent)
        kwargs = application.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 42)
        self.assertIn("echo", kwargs["text"])
        buttons = kwargs["reply_markup"].inline_keyboard
        self.assertEqual(
            buttons[0][0].callback_data,
            "core-plugin-update:confirm:echo@1.1.0",
        )
        self.assertEqual(
            buttons[0][1].callback_data,
            "core-plugin-update:decline:echo@1.1.0",
        )

    async def test_start_core_runtime_starts_cancellable_update_monitor(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        manager = SimpleNamespace(
            start=AsyncMock(),
            available_updates=AsyncMock(return_value=[]),
        )
        application = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock()),
            bot_data={},
        )
        config = {
            "allowed_user": 42,
            "plugins": {"catalog_refresh_interval": 300},
        }

        with (
            patch.object(bot_module.init, "bot_config", config),
            patch.object(bot_module, "queue_core_startup_notice"),
        ):
            await bot_module.start_core_runtime(application, manager)
            task = application.bot_data["telepiplex_plugin_update_task"]
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        manager.start.assert_awaited_once()

    async def test_hot_runtime_config_updates_safe_fields_and_reports_restart_fields(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        dispatcher = SimpleNamespace(
            retry_interval=1,
            delivery_deadline=30,
            max_attempts=5,
        )
        manager = SimpleNamespace(
            install_timeout=300,
            drain_timeout=120,
            stabilize_seconds=10,
            supervisor=SimpleNamespace(startup_timeout=30, restart_limit=3),
            broker=SimpleNamespace(dispatcher=dispatcher),
        )
        old = {
            "bot_token": "old",
            "allowed_user": 1,
            "plugins": {"root": "/old", "catalog": "old-catalog"},
        }
        new = {
            "bot_token": "new",
            "allowed_user": 2,
            "plugins": {
                "root": "/new",
                "catalog": "new-catalog",
                "install_timeout": 11,
                "startup_timeout": 12,
                "drain_timeout": 13,
                "stabilize_seconds": 0,
                "restart_limit": 4,
                "event_retry_interval": 2,
                "event_delivery_timeout": 99,
                "event_max_attempts": 7,
            },
        }

        restart_fields = bot_module.apply_hot_runtime_config(manager, old, new)

        self.assertEqual(manager.install_timeout, 11)
        self.assertEqual(manager.drain_timeout, 13)
        self.assertEqual(manager.stabilize_seconds, 0)
        self.assertEqual(manager.supervisor.startup_timeout, 12)
        self.assertEqual(manager.supervisor.restart_limit, 4)
        self.assertEqual(dispatcher.retry_interval, 2)
        self.assertEqual(dispatcher.delivery_deadline, 99)
        self.assertEqual(dispatcher.max_attempts, 7)
        self.assertEqual(
            restart_fields,
            ["bot_token", "plugins.root", "plugins.catalog"],
        )

    async def test_reload_reports_each_feature_and_continues_after_failure(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        from app.core.plugin_manager import PluginOperationError

        releases = {
            "good": SimpleNamespace(enabled=True),
            "bad": SimpleNamespace(enabled=True),
            "off": SimpleNamespace(enabled=False),
        }
        store = SimpleNamespace(
            list_installed=Mock(return_value=[
                SimpleNamespace(plugin_id="good", active=True),
                SimpleNamespace(plugin_id="bad", active=True),
                SimpleNamespace(plugin_id="off", active=True),
            ]),
            active=Mock(side_effect=lambda plugin_id: releases[plugin_id]),
        )
        async def reload_feature(plugin_id):
            if plugin_id == "bad":
                raise PluginOperationError("invalid_config", "schema mismatch")
            return SimpleNamespace(state="active")

        manager = SimpleNamespace(
            store=store,
            reload_config=AsyncMock(side_effect=reload_feature),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            bot=bot,
            application=SimpleNamespace(
                bot_data={"telepiplex_plugin_manager": manager}
            ),
        )
        old_config = {"allowed_user": 42, "bot_token": "same"}
        new_config = {"allowed_user": 42, "bot_token": "changed"}

        def load_config(*, raise_on_error=False):
            bot_module.init.bot_config = new_config
            return new_config

        with (
            patch.object(bot_module.init, "bot_config", old_config),
            patch.object(bot_module.init, "check_user", return_value=True),
            patch.object(bot_module.init, "load_yaml_config", side_effect=load_config),
            patch.object(
                bot_module,
                "apply_hot_runtime_config",
                return_value=["bot_token"],
            ),
        ):
            await bot_module.reload(update, context)

        self.assertEqual(
            [call.args[0] for call in manager.reload_config.await_args_list],
            ["bad", "good"],
        )
        text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("✅ good", text)
        self.assertIn("❌ bad：invalid_config", text)
        self.assertIn("bot_token", text)

    async def test_reload_rejects_invalid_core_yaml_without_touching_features(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        manager = SimpleNamespace(reload_config=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        context = SimpleNamespace(
            bot=bot,
            application=SimpleNamespace(
                bot_data={"telepiplex_plugin_manager": manager}
            ),
        )

        with (
            patch.object(bot_module.init, "bot_config", {"allowed_user": 42}),
            patch.object(bot_module.init, "check_user", return_value=True),
            patch.object(
                bot_module.init,
                "load_yaml_config",
                side_effect=ValueError("bad yaml"),
            ),
        ):
            await bot_module.reload(update, context)

        manager.reload_config.assert_not_awaited()
        self.assertIn("Core 配置读取失败", bot.send_message.await_args.kwargs["text"])

    async def test_core_install_callback_is_reserved_before_feature_callbacks(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        application = SimpleNamespace(bot_data={}, add_handler=Mock())
        manager = SimpleNamespace(router=Mock())

        bot_module.configure_application(application, manager)

        callback_patterns = [
            handler.pattern.pattern if handler.pattern is not None else None
            for call in application.add_handler.call_args_list
            for handler in (call.args[0],)
            if handler.__class__.__name__ == "CallbackQueryHandler"
        ]
        self.assertEqual(callback_patterns, [
            "^core-plugin-install:",
            "^core-plugin-update:",
            None,
        ])

        handler_names = [
            call.args[0].__class__.__name__
            for call in application.add_handler.call_args_list
        ]
        self.assertIn("ConversationHandler", handler_names)
        self.assertLess(
            handler_names.index("ConversationHandler"),
            handler_names.index("MessageHandler"),
        )
        self.assertIn(
            ("config", "配置 Feature"),
            [(item.command, item.description) for item in bot_module.CORE_BOT_COMMANDS],
        )


if __name__ == "__main__":
    unittest.main()

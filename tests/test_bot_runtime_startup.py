import asyncio
import importlib.util
import json
import logging
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
    async def test_core_runtime_version_is_v3_6_7_host(self):
        bot_module = await asyncio.to_thread(load_bot_module)

        self.assertEqual(bot_module.get_version(), "v3.6.7-host")

    async def test_uncaught_telegram_error_uses_the_same_sanitized_incident_in_frontend_and_machine_log(self):
        from app.utils.logger import Logger

        bot_module = await asyncio.to_thread(load_bot_module)
        with tempfile.TemporaryDirectory() as tmpdir:
            wrapper = Logger(config_root=tmpdir, session_id="BOT-ERROR")
            original_logger = bot_module.init.logger
            bot_module.init.logger = wrapper
            reply_text = AsyncMock()
            update = SimpleNamespace(
                update_id=991,
                effective_chat=SimpleNamespace(id=1001),
                effective_user=SimpleNamespace(id=2002),
                effective_message=SimpleNamespace(message_id=3003, reply_text=reply_text),
            )
            try:
                raise RuntimeError("api_key=secret-value")
            except RuntimeError as exc:
                context = SimpleNamespace(error=exc)
                await bot_module.telepiplex_error_handler(update, context)
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, "_telepiplex_handler_kind", ""):
                    logging.getLogger().removeHandler(handler)
                    handler.close()
            bot_module.init.logger = original_logger

            frontend = reply_text.await_args.kwargs["text"]
            events = [
                json.loads(line)
                for line in wrapper.session.machine_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            event = next(
                item for item in events
                if item["event"]["name"] == "telegram.update.failed"
            )
            incident_id = event["identity"]["incident_id"]
            self.assertTrue(incident_id.startswith("INC-"))
            self.assertIn(incident_id, frontend)
            self.assertEqual(event["facts"]["user_surface"]["text"], frontend)
            self.assertEqual(event["identity"]["trace_id"], "TG-991")
            self.assertIn("Traceback", event["error"]["stack"])
            self.assertNotIn("secret-value", frontend)
            self.assertNotIn(
                "secret-value",
                wrapper.session.machine_path.read_text(encoding="utf-8"),
            )

    async def test_missing_legacy_catalog_uses_official_catalog_branch(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        self.assertEqual(
            bot_module.DEFAULT_PLUGIN_CATALOG_URL,
            "https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml",
        )
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

    async def test_build_plugin_manager_uses_host_config_paths(self):
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
            }, host_database=root / "host.db")
            self.addAsyncCleanup(manager.close)

            self.assertEqual(manager.store.root, (root / "plugins").resolve())
            self.assertEqual(manager.journal.database_path, root / "host.db")
            self.assertEqual(
                manager.interaction_coordinator.database_path,
                root / "host.db",
            )
            self.assertIsNotNone(manager.broker.operation_sink)
            self.assertIsNotNone(manager.broker.milestone_sink)
            self.assertIs(
                manager.broker.projection_lifecycle.operation_sink,
                manager.broker.operation_sink,
            )
            self.assertIs(
                manager.broker.projection_lifecycle.milestone_sink,
                manager.broker.milestone_sink,
            )
            self.assertIs(
                manager.broker.operation_coordinator,
                manager.interaction_coordinator,
            )
            self.assertIs(
                manager.broker.dispatcher.operation_coordinator,
                manager.interaction_coordinator,
            )
            self.assertEqual(manager.supervisor.restart_limit, 2)
            self.assertEqual(manager.broker.dispatcher.delivery_deadline, 777)
            self.assertEqual(manager.broker.socket_path, root / "plugins" / ".runtime/host.sock")

            await manager.start()
            self.assertTrue(manager.broker.socket_path.exists())

    async def test_configured_milestone_sink_uses_host_operation_render_lock(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = bot_module.build_plugin_manager({
                "plugins": {
                    "root": str(root / "plugins"),
                    "runtime_root": str(root / "plugins" / ".runtime"),
                }
            }, host_database=root / "host.db")
            self.addAsyncCleanup(manager.close)
            application = SimpleNamespace(
                bot_data={},
                add_handler=Mock(),
                add_error_handler=Mock(),
            )
            attach = manager.broker.projection_lifecycle.attach
            manager.broker.projection_lifecycle.attach = Mock(wraps=attach)

            bot_module.configure_application(application, manager)
            manager.broker.projection_lifecycle.attach.assert_called_once()
            first_lock = manager.broker.milestone_sink.lock_factory("op-lock")
            second_lock = manager.broker.milestone_sink.lock_factory("op-lock")

            self.assertIsInstance(first_lock, asyncio.Lock)
            self.assertIs(first_lock, second_lock)
            self.assertFalse(manager.broker.milestone_sink._started)
            self.assertEqual(manager.broker.milestone_sink._tasks, set())

    async def test_start_host_runtime_starts_projection_lifecycle_on_running_loop(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        calls = []

        class ProjectionLifecycle:
            async def start(self):
                calls.append(("projection", asyncio.get_running_loop()))

        class MilestoneSink:
            async def start(self):
                calls.append(("legacy-milestone", asyncio.get_running_loop()))

        manager = SimpleNamespace(
            broker=SimpleNamespace(
                projection_lifecycle=ProjectionLifecycle(),
                milestone_sink=MilestoneSink(),
            ),
            start=AsyncMock(side_effect=lambda: calls.append(("manager", None))),
            available_updates=AsyncMock(return_value=[]),
            interaction_coordinator=None,
        )
        application = SimpleNamespace(
            bot=SimpleNamespace(
                send_message=AsyncMock(),
                set_my_commands=AsyncMock(),
            ),
            bot_data={},
        )

        with (
            patch.object(bot_module.init, "bot_config", {
                "allowed_user": 42,
                "plugins": {"catalog_refresh_interval": 300},
            }),
            patch.object(bot_module, "queue_host_startup_notice"),
        ):
            await bot_module.start_host_runtime(application, manager)
            monitor = application.bot_data["telepiplex_plugin_update_task"]
            monitor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await monitor

        self.assertEqual([item[0] for item in calls], ["projection", "manager"])
        self.assertIs(calls[0][1], asyncio.get_running_loop())

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
            }, host_database=root / "host.db")
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
        manager.drain_timeout = 13
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

        feedback_drain = AsyncMock(
            side_effect=lambda *_args, **_kwargs: events.append(
                "feedback.drain"
            ) or True
        )
        with patch.object(
            bot_module,
            "drain_callback_feedback",
            feedback_drain,
            create=True,
        ):
            await bot_module.run_application_polling(
                application,
                stop_event=stop_event,
                initialize_retry_delay=0,
            )

        self.assertEqual(events, [
            "updater.stop",
            "application.stop",
            "monitor.cancel",
            "feedback.drain",
            "manager.close",
            "application.shutdown",
        ])
        feedback_drain.assert_awaited_once_with(application, timeout=13)

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
            "host-plugin-update:confirm:echo@1.1.0",
        )
        self.assertEqual(
            buttons[0][1].callback_data,
            "host-plugin-update:decline:echo@1.1.0",
        )

    async def test_start_host_runtime_starts_cancellable_update_monitor(self):
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
            patch.object(bot_module, "queue_host_startup_notice"),
        ):
            await bot_module.start_host_runtime(application, manager)
            task = application.bot_data["telepiplex_plugin_update_task"]
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        manager.start.assert_awaited_once()

    async def test_start_host_runtime_syncs_live_feature_commands(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        from app.runtime.capability_router import CapabilityRouter

        router = CapabilityRouter()
        manager = SimpleNamespace(
            start=AsyncMock(),
            available_updates=AsyncMock(return_value=[]),
            router=router,
            interaction_coordinator=None,
        )
        application = SimpleNamespace(
            bot=SimpleNamespace(
                send_message=AsyncMock(),
                set_my_commands=AsyncMock(),
            ),
            bot_data={},
        )
        config = {
            "allowed_user": 42,
            "plugins": {"catalog_refresh_interval": 300},
        }

        with (
            patch.object(bot_module.init, "bot_config", config),
            patch.object(bot_module, "queue_host_startup_notice"),
        ):
            await bot_module.start_host_runtime(application, manager)
            task = application.bot_data["telepiplex_plugin_update_task"]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        application.bot.set_my_commands.assert_awaited_once()
        names = [
            item.command
            for item in application.bot.set_my_commands.await_args.args[0]
        ]
        self.assertEqual(names, ["start", "reload", "plugin", "config"])

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
        self.assertEqual(
            bot_module.hot_runtime_changed_fields(old, new),
            [
                "allowed_user",
                "plugins.install_timeout",
                "plugins.startup_timeout",
                "plugins.drain_timeout",
                "plugins.stabilize_seconds",
                "plugins.restart_limit",
                "plugins.event_retry_interval",
                "plugins.event_delivery_timeout",
                "plugins.event_max_attempts",
            ],
        )

    async def test_reload_reports_each_feature_and_continues_after_failure(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        from app.runtime.plugin_manager import PluginOperationError

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
                raise PluginOperationError(
                    "invalid_config", "api_key=top-secret-value"
                )
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
        self.assertIn("配置重载部分失败", text)
        self.assertIn("Host 已应用：无变更", text)
        self.assertIn("✅ good", text)
        self.assertIn("❌ bad：invalid_config", text)
        self.assertNotIn("top-secret-value", text)
        self.assertIn("bot_token", text)

    async def test_reload_rejects_invalid_host_yaml_without_touching_features(self):
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
        self.assertIn("Host 配置读取失败", bot.send_message.await_args.kwargs["text"])

    async def test_host_install_callback_is_reserved_before_feature_callbacks(self):
        bot_module = await asyncio.to_thread(load_bot_module)
        application = SimpleNamespace(
            bot_data={},
            add_handler=Mock(),
            add_error_handler=Mock(),
        )
        manager = SimpleNamespace(router=Mock())

        bot_module.configure_application(application, manager)

        application.add_error_handler.assert_called_once_with(
            bot_module.telepiplex_error_handler
        )

        callback_patterns = [
            handler.pattern.pattern if handler.pattern is not None else None
            for call in application.add_handler.call_args_list
            for handler in (call.args[0],)
            if handler.__class__.__name__ == "CallbackQueryHandler"
        ]
        self.assertEqual(callback_patterns, [
            "^host-operation:",
            "^host-plugin-install:",
            "^host-plugin-update:",
            None,
        ])

        gate_calls = [
            call for call in application.add_handler.call_args_list
            if call.kwargs.get("group") == -100
        ]
        self.assertEqual(len(gate_calls), 1)
        self.assertEqual(gate_calls[0].args[0].__class__.__name__, "TypeHandler")

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
            [(item.command, item.description) for item in bot_module.HOST_BOT_COMMANDS],
        )


if __name__ == "__main__":
    unittest.main()

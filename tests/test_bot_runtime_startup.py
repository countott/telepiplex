import asyncio
import importlib.util
import json
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
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_config_log_snapshot_redacts_nested_secrets(self):
        bot_module = load_bot_module()

        config = {
            "bot_token": "123456:telegram-secret",
            "allowed_user": 472943219,
            "media": {
                "plex": {
                    "base_url": "http://plex.example",
                    "token": "plex-secret",
                }
            },
            "ai": {"api_key": "api-secret"},
            "nested": [{"auth_token": "mcp-secret"}],
        }

        redacted = bot_module.sanitize_config_for_log(config)
        dumped = json.dumps(redacted, ensure_ascii=False)

        self.assertIn("http://plex.example", dumped)
        self.assertIn("472943219", dumped)
        for secret in (
            "telegram-secret",
            "plex-secret",
            "api-secret",
            "mcp-secret",
        ):
            self.assertNotIn(secret, dumped)
        self.assertEqual(redacted["bot_token"], "***redacted***")
        self.assertEqual(redacted["media"]["plex"]["token"], "***redacted***")

    def test_start_treats_telegram_timeout_as_possible_delivery(self):
        bot_module = load_bot_module()

        update = Mock()
        update.effective_chat.id = 472943219
        context = Mock()
        context.bot.send_message = AsyncMock(side_effect=bot_module.TimedOut("Timed out"))

        asyncio.run(bot_module.start(update, context))

        context.bot.send_message.assert_awaited_once()

    def test_reload_rejects_unauthorized_user_without_reloading_config(self):
        bot_module = load_bot_module()
        update = Mock()
        update.effective_user.id = 999
        update.effective_chat.id = 999
        context = Mock()
        context.bot.send_message = AsyncMock()
        original_check_user = bot_module.init.check_user
        original_load_yaml_config = bot_module.init.load_yaml_config
        original_logger = bot_module.init.logger
        bot_module.init.check_user = Mock(return_value=False)
        bot_module.init.load_yaml_config = Mock()
        bot_module.init.logger = Mock()
        try:
            asyncio.run(bot_module.reload(update, context))
        finally:
            bot_module.init.check_user = original_check_user
            load_yaml_config = bot_module.init.load_yaml_config
            bot_module.init.load_yaml_config = original_load_yaml_config
            bot_module.init.logger = original_logger

        load_yaml_config.assert_not_called()
        self.assertIn("无权", context.bot.send_message.await_args.kwargs["text"])

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

    def test_default_enabled_modules_load_only_plex(self):
        bot_module = load_bot_module()

        self.assertEqual(
            bot_module.get_enabled_module_names({}),
            ["app.modules.plex_management"],
        )

    def test_disabled_plex_module_is_removed_from_default(self):
        bot_module = load_bot_module()

        self.assertEqual(
            bot_module.get_enabled_module_names(
                {
                    "modules": {
                        "enabled": "all",
                        "disabled": ["app.modules.plex_management"],
                    }
                }
            ),
            [],
        )

    def test_explicit_empty_config_uses_default_modules_not_global_config(self):
        bot_module = load_bot_module()
        original_config = bot_module.init.bot_config
        bot_module.init.bot_config = {
            "modules": {"disabled": ["app.modules.plex_management"]}
        }
        try:
            self.assertEqual(
                bot_module.get_enabled_module_names({}),
                ["app.modules.plex_management"],
            )
        finally:
            bot_module.init.bot_config = original_config

    def test_modules_status_text_reports_default_modules_and_restart_boundary(self):
        bot_module = load_bot_module()

        text = bot_module.build_modules_status_text({})

        self.assertIn("Plex 管理", text)
        self.assertNotIn("115 下载", text)
        self.assertNotIn("媒体搜索", text)
        self.assertNotIn("下载后重命名", text)
        self.assertIn("重启容器后生效", text)

    def test_core_startup_notice_reports_loaded_modules(self):
        bot_module = load_bot_module()
        registry = Mock()
        registry.loaded_module_names = ["app.modules.plex_management"]

        text = bot_module.build_core_startup_notice_text({}, registry)

        self.assertIn("Telepiplex 启动完成", text)
        self.assertIn("Plex 管理", text)
        self.assertNotIn("115 下载", text)
        self.assertNotIn("媒体搜索", text)
        self.assertNotIn("下载后重命名", text)

    def test_core_startup_notice_is_queued_for_allowed_user(self):
        bot_module = load_bot_module()
        registry = Mock()
        registry.loaded_module_names = ["app.modules.plex_management"]
        bot_module.init.bot_config = {"allowed_user": "472943219"}
        bot_module.add_task_to_queue = Mock(return_value=True)

        bot_module.queue_core_startup_notice(registry)

        bot_module.add_task_to_queue.assert_called_once()
        args, kwargs = bot_module.add_task_to_queue.call_args
        self.assertEqual(args[0], "472943219")
        self.assertIsNone(args[1])
        self.assertIn("Telepiplex 启动完成", kwargs["message"])
        self.assertIn("Plex 管理", kwargs["message"])

    def test_bot_menu_includes_plex_command(self):
        bot_module = load_bot_module()
        from app.core.module_registry import ModuleRegistry
        from app.modules.plex_management import register_module

        registry = ModuleRegistry()
        register_module(registry)
        commands = [item.command for item in bot_module.get_bot_menu(registry)]

        self.assertIn("plex", commands)

    def test_plex_module_registers_startup_hook(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.plex_management import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(len(registry.startup_hooks), 1)

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

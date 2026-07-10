import asyncio
import sys
import tempfile
import textwrap
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def load_bot_module():
    spec = importlib.util.spec_from_file_location("telepiplex_bot_entry", ROOT / "app" / "115bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ComposableCoreTest(unittest.TestCase):
    def test_registry_orders_commands_and_processors(self):
        from app.core.module_registry import ModuleRegistry, PostDownloadResult

        registry = ModuleRegistry()
        registry.add_commands([("search", "搜索片源")])
        registry.add_post_download_processor(lambda event: PostDownloadResult(False), priority=200, name="late")
        registry.add_post_download_processor(lambda event: PostDownloadResult(False), priority=100, name="early")

        self.assertEqual([command.command for command in registry.bot_commands()], ["search"])
        self.assertEqual([item.name for item in registry.post_download_processors], ["early", "late"])

    def test_dispatch_download_requires_provider(self):
        from app.core.module_registry import DownloadProviderUnavailable, DownloadRequest, ModuleRegistry

        registry = ModuleRegistry()

        with self.assertRaises(DownloadProviderUnavailable):
            registry.dispatch_download(DownloadRequest(link="magnet:?xt=urn:btih:" + "a" * 40, selected_path="/电影", user_id=1))

    def test_pipeline_stops_on_terminal_result(self):
        from app.core.module_registry import DownloadCompletedEvent, ModuleRegistry, PostDownloadResult

        calls = []
        registry = ModuleRegistry()
        registry.add_post_download_processor(
            lambda event: calls.append("first") or PostDownloadResult(True, final_path="/整理后", should_stop=True),
            priority=100,
            name="first",
        )
        registry.add_post_download_processor(
            lambda event: calls.append("second") or PostDownloadResult(True),
            priority=200,
            name="second",
        )

        result = registry.run_post_download_pipeline(
            DownloadCompletedEvent(
                link="magnet:?xt=urn:btih:" + "b" * 40,
                selected_path="/电影",
                user_id=1,
                final_path="/下载",
                resource_name="Release",
            )
        )

        self.assertEqual(calls, ["first"])
        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/整理后")

    def test_module_loader_calls_register_module(self):
        from app.core.module_loader import load_enabled_modules
        from app.core.module_registry import ModuleRegistry

        with tempfile.TemporaryDirectory() as tmp_dir:
            package_dir = Path(tmp_dir) / "samplepkg"
            package_dir.mkdir()
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "mod.py").write_text(
                textwrap.dedent(
                    """
                    def register_module(registry):
                        registry.add_config_sections(["sample"])
                    """
                ),
                encoding="utf-8",
            )
            sys.path.insert(0, tmp_dir)
            try:
                registry = ModuleRegistry()
                loaded = load_enabled_modules(registry, ["samplepkg.mod"])
            finally:
                sys.path.remove(tmp_dir)

        self.assertEqual(loaded, ["samplepkg.mod"])
        self.assertEqual(registry.config_sections, ["sample"])

    def test_core_startup_notice_reports_loaded_modules(self):
        bot_module = load_bot_module()
        registry = Mock()
        registry.loaded_module_names = [
            "app.modules.open115",
            "app.modules.media_search",
        ]

        text = bot_module.build_core_startup_notice_text({}, registry)

        self.assertIn("Telepiplex 启动完成", text)
        self.assertIn("115 下载", text)
        self.assertIn("媒体搜索", text)

    def test_core_startup_notice_is_queued_for_allowed_user(self):
        bot_module = load_bot_module()
        registry = Mock()
        registry.loaded_module_names = ["app.modules.open115"]
        bot_module.init.bot_config = {"allowed_user": "472943219"}
        bot_module.add_task_to_queue = Mock(return_value=True)

        bot_module.queue_core_startup_notice(registry)

        bot_module.add_task_to_queue.assert_called_once()
        args, kwargs = bot_module.add_task_to_queue.call_args
        self.assertEqual(args[0], "472943219")
        self.assertIsNone(args[1])
        self.assertIn("Telepiplex 启动完成", kwargs["message"])
        self.assertIn("115 下载", kwargs["message"])

    def test_startup_hooks_support_zero_or_one_application_argument(self):
        from app.core.module_registry import ModuleRegistry

        calls = []
        application = object()
        registry = ModuleRegistry()
        registry.add_startup_hook(lambda: calls.append(("zero", None)))
        registry.add_startup_hook(lambda app: calls.append(("one", app)))

        registry.run_startup_hooks(application)

        self.assertEqual(calls, [("zero", None), ("one", application)])

    def test_startup_hook_internal_type_error_is_not_retried(self):
        from app.core.module_registry import ModuleRegistry

        application = object()
        calls = []

        def broken_hook(app):
            calls.append(app)
            raise TypeError("hook implementation failed")

        registry = ModuleRegistry()
        registry.add_startup_hook(broken_hook)

        with self.assertRaisesRegex(TypeError, "hook implementation failed"):
            registry.run_startup_hooks(application)

        self.assertEqual(calls, [application])

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


if __name__ == "__main__":
    unittest.main()

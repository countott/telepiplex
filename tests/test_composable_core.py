import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


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


if __name__ == "__main__":
    unittest.main()

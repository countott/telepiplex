import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda stream: {}))


class MediaSearchSurfaceTest(unittest.TestCase):
    def test_bot_exposes_media_search_commands_only(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module

        registry = ModuleRegistry()
        register_module(registry)
        self.assertEqual([command.command for command in registry.bot_commands()], ["search", "s"])

        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        for symbol in (
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "register_video_handlers",
            "register_offline_task_handlers",
            "register_aria2_handlers",
            "initialize_115open",
            "start_scheduler_in_thread",
        ):
            self.assertNotIn(symbol, source)

    def test_business_modules_outside_media_search_are_absent(self):
        source = (ROOT / "app" / "modules" / "media_search.py").read_text(encoding="utf-8")
        for symbol in (
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "open_115",
            "media_naming",
            "tvdb_rename",
        ):
            self.assertNotIn(symbol, source)

    def test_search_uses_core_download_request_contract(self):
        source = (ROOT / "app" / "handlers" / "search_handler.py").read_text(encoding="utf-8")

        self.assertIn("DownloadRequest", source)
        self.assertIn("registry.dispatch_download", source)
        self.assertNotIn("app.handlers.download_handler", source)

    def test_config_exposes_search_not_delivery_or_organization(self):
        for config_path in (ROOT / "config" / "modules" / "media-search.yaml.example",):
            source = config_path.read_text(encoding="utf-8")
            self.assertIn("search:", source)
            self.assertIn("prowlarr:", source)
            self.assertIn("metadata:", source)
            self.assertIn("ai:", source)
            for term in (
                "115_app_id",
                "access_token",
                "refresh_token",
                "media:",
                "plex:",
                "aria2:",
            ):
                self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()

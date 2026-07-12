import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TelepiplexCoreSurfaceTest(unittest.TestCase):
    def test_core_bot_exposes_only_runtime_commands(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        commands = re.findall(r'BotCommand\("([^"]+)"', source)
        self.assertEqual(commands, ["start", "reload"])

        removed_symbols = [
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "register_search_handlers",
            "register_video_handlers",
            "register_offline_task_handlers",
            "register_aria2_handlers",
            "initialize_115open",
            "start_scheduler_in_thread",
        ]
        for symbol in removed_symbols:
            self.assertNotIn(symbol, source)

    def test_core_branch_has_no_business_modules(self):
        modules = sorted(
            path.name
            for path in (ROOT / "app" / "modules").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(modules, [])
        self.assertTrue((ROOT / "app" / "core" / "media_metadata.py").is_file())

        removed_paths = [
            ROOT / "app" / "core" / "open_115.py",
            ROOT / "app" / "handlers" / "download_handler.py",
            ROOT / "app" / "handlers" / "search_handler.py",
            ROOT / "app" / "adapters" / "prowlarr.py",
            ROOT / "app" / "adapters" / "tvdb.py",
            ROOT / "app" / "utils" / "ai.py",
            ROOT / "app" / "utils" / "media_naming.py",
            ROOT / "app" / "utils" / "search_resolution.py",
            ROOT / "app" / "utils" / "tvdb_rename.py",
        ]
        for path in removed_paths:
            self.assertFalse(path.exists(), str(path))

    def test_core_config_excludes_business_sections(self):
        for config_path in (
            ROOT / "config" / "config.yaml.example",
            ROOT / "app" / "config.yaml.example",
            ROOT / "config" / "modules" / "core.yaml.example",
        ):
            source = config_path.read_text(encoding="utf-8")
            self.assertIn("category_folder:", source)
            for term in (
                "115_app_id",
                "access_token",
                "refresh_token",
                "search:",
                "prowlarr",
                "media:",
                "metadata:",
                "tvdb",
                "aria2:",
                "ai:",
            ):
                self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()

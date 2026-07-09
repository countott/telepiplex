import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Feature115SurfaceTest(unittest.TestCase):
    def test_only_minimal_115_commands_are_public(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        commands = re.findall(r'BotCommand\("([^"]+)"', bot_source)
        self.assertEqual(commands, ["start", "auth", "config", "reload", "magnet", "m", "q"])

        for command in ("search", "s", "retry", "r", "strm", "find"):
            self.assertNotIn(f"<code>/{command}</code>", bot_source)
            self.assertNotIn(f'BotCommand("{command}"', bot_source)
            self.assertNotIn(f'CommandHandler("{command}"', bot_source)
            self.assertNotIn(f"CommandHandler('{command}'", bot_source)

    def test_removed_handlers_are_not_imported_or_registered(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        removed_symbols = [
            "register_search_handlers",
            "register_video_handlers",
            "register_offline_task_handlers",
            "register_aria2_handlers",
            "start_scheduler_in_thread",
            "queue_optional_config_notice",
            "missing_optional_config_labels",
        ]
        for symbol in removed_symbols:
            self.assertNotIn(symbol, bot_source)

        for path in [
            ROOT / "app" / "handlers" / "search_handler.py",
            ROOT / "app" / "handlers" / "video_handler.py",
            ROOT / "app" / "handlers" / "offline_task_handler.py",
            ROOT / "app" / "handlers" / "aria2_handler.py",
            ROOT / "app" / "adapters" / "prowlarr.py",
            ROOT / "app" / "adapters" / "tvdb.py",
            ROOT / "app" / "core" / "scheduler.py",
            ROOT / "app" / "core" / "selenium_browser.py",
            ROOT / "app" / "core" / "video_downloader.py",
            ROOT / "app" / "utils" / "ai.py",
            ROOT / "app" / "utils" / "aria2.py",
            ROOT / "app" / "utils" / "fast_telethon.py",
            ROOT / "app" / "utils" / "media_naming.py",
            ROOT / "app" / "utils" / "search_query.py",
            ROOT / "app" / "utils" / "search_resolution.py",
            ROOT / "app" / "utils" / "tvdb_rename.py",
        ]:
            self.assertFalse(path.exists(), str(path))

    def test_config_templates_only_expose_minimal_115_settings(self):
        for config_path in (ROOT / "config" / "config.yaml.example", ROOT / "app" / "config.yaml.example"):
            source = config_path.read_text(encoding="utf-8")
            self.assertIn("category_folder:", source)
            self.assertIn("115_app_id:", source)
            self.assertIn("access_token:", source)
            self.assertIn("refresh_token:", source)

            removed_terms = [
                "search:",
                "prowlarr",
                "metadata:",
                "tvdb",
                "media:",
            "aria2:",
                "selenium",
                "plex_library_id",
                "tg_api_id",
                "tg_api_hash",
                "bot_name",
            ]
            for term in removed_terms:
                self.assertNotIn(term, source)

    def test_docs_and_dependencies_do_not_advertise_removed_services(self):
        scanned_files = [
            ROOT / "README.md",
            ROOT / "README_EN.md",
            ROOT / "requirements.txt",
            ROOT / "Dockerfile",
            ROOT / "Dockerfile.local",
        ]
        removed_terms = [
            "/search",
            "`/s`",
            "Prowlarr",
            "prowlarr",
            "豆瓣",
            "Douban",
            "TVDB",
            "tvdb",
            "Plex",
            "Aria2",
            "aria2",
            "Telethon",
            "selenium",
            "Chrome",
            "Pillow",
            "alibabacloud",
            "beautifulsoup",
        ]
        for path in scanned_files:
            source = path.read_text(encoding="utf-8")
            for term in removed_terms:
                self.assertNotIn(term, source, f"{term} in {path}")


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TelepiplexCoreSurfaceTest(unittest.TestCase):
    def test_deployable_bot_exposes_core_status_commands(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        commands = re.findall(r'BotCommand\("([^"]+)"', source)
        self.assertEqual(commands, ["start", "reload", "modules"])

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

    def test_deployable_main_contains_composable_business_modules(self):
        required_paths = [
            ROOT / "app" / "modules" / "open115.py",
            ROOT / "app" / "modules" / "media_search.py",
            ROOT / "app" / "modules" / "renaming.py",
            ROOT / "app" / "core" / "module_registry.py",
            ROOT / "app" / "core" / "module_loader.py",
        ]
        for path in required_paths:
            self.assertTrue(path.exists(), str(path))

    def test_deployable_config_template_contains_full_runtime_contract(self):
        for config_path in (ROOT / "config" / "config.yaml.example", ROOT / "app" / "config.yaml.example"):
            source = config_path.read_text(encoding="utf-8")
            for term in (
                "bot_token:",
                "allowed_user:",
                "modules:",
                "enabled: all",
                "115_app_id:",
                "access_token:",
                "refresh_token:",
                "open115:",
                "clean_policy:",
                "category_folder:",
                "search:",
                "prowlarr:",
                "scoring:",
                "media:",
                "metadata:",
                "tvdb:",
                "ai:",
                "api_url:",
            ):
                self.assertIn(term, source)

            for legacy_term in ("aria2:", "selenium_timeout", "offline_path:", "ai:\n  enable: false\n  api_key: \"\"\n  base_url: \"\""):
                self.assertNotIn(legacy_term, source)


if __name__ == "__main__":
    unittest.main()

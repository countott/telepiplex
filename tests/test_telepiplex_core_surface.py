import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class TelepiplexCoreSurfaceTest(unittest.TestCase):
    def test_deployable_bot_exposes_core_status_commands(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        commands = re.findall(r'BotCommand\("([^"]+)"', source)
        self.assertEqual(commands, ["start", "reload", "modules"])
        for removed_symbol in (
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "register_search_handlers",
            "initialize_115open",
        ):
            self.assertNotIn(removed_symbol, source)

    def test_deployable_runtime_contains_core_plus_plex_only(self):
        required = (
            "app/core/module_registry.py",
            "app/core/module_loader.py",
            "app/core/media_metadata.py",
            "app/modules/plex_management.py",
        )
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

        modules = sorted(
            path.name
            for path in (ROOT / "app" / "modules").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(modules, ["plex_management.py"])

    def test_deployable_full_templates_are_core_runtime_only(self):
        for config_path in (
            ROOT / "config" / "config.yaml.example",
            ROOT / "app" / "config.yaml.example",
        ):
            parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(parsed),
                {"log_level", "bot_token", "allowed_user", "category_folder"},
            )
            self.assertEqual(len(parsed["category_folder"]), 4)


if __name__ == "__main__":
    unittest.main()

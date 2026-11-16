import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class HostSurfaceTest(unittest.TestCase):
    def test_host_bot_delegates_command_menu_to_dynamic_catalog(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        self.assertIn("from app.runtime.command_catalog import (", source)
        self.assertIn("return build_bot_commands(router)", source)
        self.assertIn("await sync_bot_commands(application, router)", source)
        self.assertNotIn('BotCommand("', source)

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

        for legacy_symbol in (
            "ModuleRegistry",
            "load_enabled_modules",
            "build_module_registry",
            "modules_config",
        ):
            self.assertNotIn(legacy_symbol, source)

    def test_host_branch_has_no_business_modules(self):
        modules = sorted(
            path.name
            for path in (ROOT / "app" / "modules").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(modules, [])
        self.assertTrue((ROOT / "app" / "runtime" / "media_metadata.py").is_file())

        removed_paths = [
            ROOT / "app" / "runtime" / "open_115.py",
            ROOT / "app" / "handlers" / "download_handler.py",
            ROOT / "app" / "handlers" / "search_handler.py",
            ROOT / "app" / "adapters" / "prowlarr.py",
            ROOT / "app" / "adapters" / "tvdb.py",
            ROOT / "app" / "utils" / "ai.py",
            ROOT / "app" / "utils" / "media_naming.py",
            ROOT / "app" / "utils" / "search_resolution.py",
            ROOT / "app" / "utils" / "tvdb_rename.py",
            ROOT / "app" / "utils" / "directory_config.py",
            ROOT / "app" / "runtime" / "module_loader.py",
            ROOT / "app" / "runtime" / "module_registry.py",
        ]
        for path in removed_paths:
            self.assertFalse(path.exists(), str(path))

    def test_host_config_exposes_central_shared_and_feature_sections(self):
        for config_path in (
            ROOT / "config" / "config.yaml.example",
            ROOT / "app" / "config.yaml.example",
        ):
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertIsInstance(config["plugins"], dict)
            self.assertEqual(config["config_version"], 3)
            self.assertEqual(set(config["shared"]), {"directories", "directory_roles", "tmdb"})
            self.assertEqual(set(config["features"]), {"download", "search", "rename", "sync", "caption"})
            for legacy in ("modules", "aria2"):
                self.assertNotIn(legacy, config)
                self.assertNotIn(legacy, config["features"])


if __name__ == "__main__":
    unittest.main()

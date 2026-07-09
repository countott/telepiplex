import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class Composable115ModuleTest(unittest.TestCase):
    def test_open115_module_registers_provider_and_commands(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.open115 import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q"],
        )
        self.assertIsNotNone(registry.download_provider)
        self.assertIsNotNone(registry.storage_provider)
        self.assertEqual(registry.config_sections, ["115", "open115"])

    def test_core_entrypoint_does_not_import_115_handlers_directly(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        for symbol in (
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "initialize_115open",
        ):
            self.assertNotIn(symbol, source)


if __name__ == "__main__":
    unittest.main()

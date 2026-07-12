import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROUTES = (
    ("live_action_movie", "/真人电影"),
    ("animated_movie", "/动画电影"),
    ("live_action_series", "/真人剧集"),
    ("animated_series", "/动画剧集"),
)


class RenamingFeatureSurfaceTest(unittest.TestCase):
    def test_only_renaming_business_module_is_present(self):
        modules = sorted(
            path.name
            for path in (ROOT / "app/modules").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(modules, ["renaming.py"])

    def test_other_business_surfaces_are_absent(self):
        for relative in (
            "app/core/open_115.py",
            "app/handlers/search_handler.py",
            "app/handlers/plex_handler.py",
            "app/adapters/prowlarr.py",
            "app/adapters/plex.py",
            "app/services/search_planner.py",
            "app/services/plex_management.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_renaming_imports_core_contract_not_other_modules(self):
        source = (ROOT / "app/modules/renaming.py").read_text(encoding="utf-8")
        self.assertIn("app.core.media_metadata", source)
        self.assertNotIn("app.modules.media_search", source)
        self.assertNotIn("app.modules.plex_management", source)

    def test_retired_contract_reader_and_public_key_are_absent(self):
        retired_key = "_".join(("download", "plan"))
        retired_reader = "extract_confirmed_" + retired_key
        retired_file = "confirmed_" + retired_key + ".py"
        self.assertFalse((ROOT / "app/utils" / retired_file).exists())
        self.assertFalse((ROOT / "tests" / ("test_" + retired_file)).exists())

        for root_name in ("app", "tests", "config"):
            for path in (ROOT / root_name).rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".yaml", ".example"}:
                    continue
                source = path.read_text(encoding="utf-8")
                self.assertNotIn(retired_key, source, str(path.relative_to(ROOT)))
                self.assertNotIn(retired_reader, source, str(path.relative_to(ROOT)))

    def test_module_config_contains_only_renaming_contract(self):
        path = ROOT / "config/modules/renaming.yaml.example"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))

        self.assertEqual(set(config), {"category_folder", "media", "metadata", "ai"})
        routes = config["category_folder"]
        self.assertEqual(
            tuple((route["kind"], route["path"]) for route in routes),
            EXPECTED_ROUTES,
        )
        for route in routes:
            self.assertEqual(
                set(route),
                {"kind", "name", "path", "plex_library_id"},
            )
            self.assertEqual(route["plex_library_id"], "")

        self.assertEqual(config["media"], {"unorganized_path": "/未整理"})
        self.assertEqual(
            config["metadata"],
            {
                "tvdb": {
                    "enable": True,
                    "api_key": "",
                    "base_url": "https://api4.thetvdb.com/v4",
                    "timeout": 15,
                }
            },
        )
        self.assertEqual(
            config["ai"],
            {
                "enable": True,
                "api_url": "",
                "api_key": "",
                "model": "",
            },
        )


if __name__ == "__main__":
    unittest.main()

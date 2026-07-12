import ast
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PlexFeatureSurfaceTest(unittest.TestCase):
    def test_only_plex_business_module_is_present(self):
        modules = sorted(
            path.name
            for path in (ROOT / "app" / "modules").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(modules, ["plex_management.py"])

    def test_other_business_surfaces_are_absent(self):
        for relative in (
            "app/core/open_115.py",
            "app/handlers/auth_handler.py",
            "app/handlers/config_handler.py",
            "app/handlers/download_handler.py",
            "app/handlers/search_handler.py",
            "app/modules/media_search.py",
            "app/modules/open115.py",
            "app/modules/renaming.py",
            "app/adapters/prowlarr.py",
            "app/adapters/tvdb.py",
            "app/utils/media_metadata.py",
            "app/utils/media_naming.py",
            "app/utils/release_score.py",
            "app/utils/search_query.py",
            "app/utils/search_resolution.py",
            "app/utils/tvdb_rename.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_required_core_and_plex_surfaces_remain(self):
        for relative in (
            "app/core/media_metadata.py",
            "app/core/module_loader.py",
            "app/core/module_registry.py",
            "app/modules/plex_management.py",
            "app/handlers/plex_handler.py",
            "app/adapters/plex.py",
            "app/adapters/tmdb.py",
            "app/adapters/fanart.py",
            "app/services/plex_management.py",
            "app/services/plex_rules.py",
            "app/services/plex_ai.py",
            "app/repositories/plex_jobs.py",
            "app/plex_mcp/server.py",
            "app/utils/message_queue.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_runtime_defaults_and_catalog_name_only_plex(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"DEFAULT_ENABLED_MODULES", "MODULE_CATALOG"}
        }
        self.assertEqual(
            assignments["DEFAULT_ENABLED_MODULES"],
            ("app.modules.plex_management",),
        )
        self.assertEqual(
            set(assignments["MODULE_CATALOG"]),
            {"app.modules.plex_management"},
        )

    def test_plex_runtime_has_no_other_business_imports(self):
        checked = (
            "app/115bot.py",
            "app/init.py",
            "app/modules/plex_management.py",
            "app/handlers/plex_handler.py",
            "app/services/plex_management.py",
            "app/services/plex_ai.py",
            "app/plex_mcp/server.py",
        )
        forbidden = (
            "app.core.open_115",
            "app.modules.open115",
            "app.modules.media_search",
            "app.modules.renaming",
            "app.handlers.search_handler",
            "app.utils.tvdb_rename",
        )
        combined = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in checked
        )
        for import_name in forbidden:
            self.assertNotIn(import_name, combined)

    def test_plex_module_config_has_only_approved_business_sections(self):
        snippet_path = ROOT / "config" / "modules" / "plex-management.yaml.example"
        parsed = yaml.safe_load(snippet_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(parsed),
            {"category_folder", "media", "metadata", "artwork", "ai"},
        )
        routes = parsed["category_folder"]
        self.assertEqual(
            {route["kind"] for route in routes},
            {
                "live_action_series",
                "live_action_movie",
                "animated_movie",
                "animated_series",
            },
        )
        self.assertTrue(all(route.get("path") for route in routes))
        self.assertTrue(all("plex_library_id" in route for route in routes))
        self.assertEqual(set(parsed["media"]), {"plex"})
        self.assertEqual(set(parsed["metadata"]), {"tmdb"})
        self.assertEqual(set(parsed["artwork"]), {"fanart"})
        rendered = snippet_path.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "prowlarr",
            "tvdb",
            "115_app_id",
            "access_token",
            "refresh_token",
            "open115",
            "unorganized_path",
            "renaming",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_full_templates_are_identical_core_runtime_only(self):
        app_template = yaml.safe_load(
            (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")
        )
        root_template = yaml.safe_load(
            (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        )
        self.assertEqual(app_template, root_template)
        self.assertEqual(
            set(root_template),
            {"log_level", "bot_token", "allowed_user", "category_folder"},
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.runtime.media_metadata import require_complete_category_routes


CATEGORY_KINDS = (
    "live_action_series",
    "live_action_movie",
    "animated_movie",
    "animated_series",
)


def complete_routes():
    return [
        {
            "kind": kind,
            "name": "任意显示名",
            "path": f"/{kind}",
            "plex_library_id": "",
        }
        for kind in CATEGORY_KINDS
    ]


class CategoryRouteStartupTest(unittest.TestCase):
    def test_pre_kind_live_config_is_rejected_with_migration_message(self):
        config = {"category_folder": [
            {"name": "真人剧集", "path": "/真人剧集", "plex_library_id": "11"},
            {"name": "真人电影", "path": "/真人电影", "plex_library_id": "12"},
            {"name": "动画电影", "path": "/动画电影", "plex_library_id": "13"},
            {"name": "动画剧集", "path": "/动画剧集", "plex_library_id": "14"},
        ]}

        with self.assertRaisesRegex(ValueError, "category_folder.*kind"):
            require_complete_category_routes(config)

    def test_complete_routes_pass_without_using_display_names(self):
        require_complete_category_routes({"category_folder": complete_routes()})

    def test_invalid_route_shape_is_rejected(self):
        invalid_routes = {
            "duplicate kind": complete_routes(),
            "blank path": complete_routes(),
            "missing plex key": complete_routes(),
            "unknown kind": complete_routes(),
        }
        invalid_routes["duplicate kind"][-1]["kind"] = CATEGORY_KINDS[0]
        invalid_routes["blank path"][0]["path"] = "/"
        del invalid_routes["missing plex key"][0]["plex_library_id"]
        invalid_routes["unknown kind"][0]["kind"] = "documentary"

        for case_name, routes in invalid_routes.items():
            with self.subTest(case_name=case_name):
                with self.assertRaisesRegex(ValueError, "category_folder"):
                    require_complete_category_routes({"category_folder": routes})

    def test_host_config_loads_without_business_category_routes(self):
        host_config = {
            "bot_token": "token",
            "allowed_user": 1,
            "plugins": {"root": "/config/plugins"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            app_dir = root / "app"
            app_dir.mkdir()
            live_path = root / "config.yaml"
            copied_example_path = root / "config.yaml.example"
            live_text = yaml.safe_dump(host_config, allow_unicode=True, sort_keys=False)
            live_path.write_text(live_text, encoding="utf-8")
            (app_dir / "config.yaml.example").write_text(
                yaml.safe_dump(
                    host_config,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            old_config = init.bot_config
            self.addCleanup(setattr, init, "bot_config", old_config)
            init.bot_config = {"allowed_user": 42}
            with patch.multiple(
                init,
                APP=str(app_dir),
                CONFIG_FILE=str(live_path),
                CONFIG_FILE_EXAMPLE=str(copied_example_path),
            ):
                init.load_yaml_config()

            self.assertEqual(live_path.read_text(encoding="utf-8"), live_text)

    def test_yaml_parse_error_remains_distinct_from_route_migration_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            app_dir = root / "app"
            app_dir.mkdir()
            live_path = root / "config.yaml"
            copied_example_path = root / "config.yaml.example"
            live_path.write_text("category_folder: [", encoding="utf-8")
            (app_dir / "config.yaml.example").write_text(
                yaml.safe_dump(
                    {"plugins": {"root": "/config/plugins"}},
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            old_config = init.bot_config
            self.addCleanup(setattr, init, "bot_config", old_config)
            init.bot_config = {"allowed_user": 42}
            with patch.multiple(
                init,
                APP=str(app_dir),
                CONFIG_FILE=str(live_path),
                CONFIG_FILE_EXAMPLE=str(copied_example_path),
            ), patch("builtins.print") as print_mock:
                init.load_yaml_config()

            self.assertEqual(init.bot_config, {"allowed_user": 42})
            self.assertTrue(any(
                "格式有误" in str(call.args[0])
                for call in print_mock.call_args_list
                if call.args
            ))


if __name__ == "__main__":
    unittest.main()

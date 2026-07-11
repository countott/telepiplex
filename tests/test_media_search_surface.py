import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda stream: {}))


class MediaSearchSurfaceTest(unittest.TestCase):
    def test_core_metadata_contract_survives_download_request_handoff(self):
        from app.core.media_metadata import attach_media_metadata
        from app.core.module_registry import DownloadRequest

        contract = {
            "schema_version": 1,
            "metadata_id": "metadata-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "external_ids": {},
            },
            "relation": {
                "type": "sequel",
                "target_series": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {},
                },
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
                "tvdb_episode_id": "",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }
        request = DownloadRequest(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/真人剧集",
            user_id=1,
            metadata=attach_media_metadata({}, contract),
        )

        self.assertEqual(
            request.metadata["media_metadata"]["metadata_id"],
            "metadata-a",
        )

    def test_removed_search_plan_api_names_are_absent(self):
        forbidden = (
            "_".join(("download", "plan")),
            "_".join(("attach", "download", "plan")),
            "_".join(("confirm", "download", "plan")),
            "_".join(("finalize", "download", "plan")),
        )
        for root_name in ("app", "config"):
            for path in (ROOT / root_name).rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".yaml", ".example"}:
                    continue
                source = path.read_text(encoding="utf-8")
                for name in forbidden:
                    self.assertNotIn(name, source, str(path))

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
        for path in (
            ROOT / "app" / "core" / "open_115.py",
            ROOT / "app" / "handlers" / "auth_handler.py",
            ROOT / "app" / "handlers" / "config_handler.py",
            ROOT / "app" / "handlers" / "offline_task_handler.py",
            ROOT / "app" / "handlers" / "video_handler.py",
            ROOT / "app" / "utils" / "aria2.py",
            ROOT / "app" / "utils" / "media_naming.py",
            ROOT / "app" / "utils" / "tvdb_rename.py",
        ):
            self.assertFalse(path.exists(), str(path))

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

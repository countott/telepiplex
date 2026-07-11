import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableIntegrationTest(unittest.TestCase):
    def test_confirmed_media_metadata_survives_request_event_pipeline(self):
        from app.core.media_metadata import attach_media_metadata
        from app.core.module_registry import (
            DownloadCompletedEvent,
            DownloadRequest,
            ModuleRegistry,
        )

        contract = {
            "schema_version": 1,
            "metadata_id": "plan-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "summary": "电影版延续电视剧故事。",
                "original_release_date": "2022-12-24",
                "poster_url": "https://image.example/poster.jpg",
                "poster_source": "douban",
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
                "provider": "wikipedia",
                "verification": "verified",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }
        request = DownloadRequest(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/真人剧集",
            user_id=1,
            metadata=attach_media_metadata({"source": "confirmed"}, contract),
        )
        event = DownloadCompletedEvent(
            link=request.link,
            selected_path=request.selected_path,
            user_id=request.user_id,
            final_path="/真人剧集/Raw.Release",
            resource_name="Raw.Release",
            metadata=request.metadata,
        )
        seen = []
        registry = ModuleRegistry()
        registry.add_post_download_processor(
            lambda current: seen.append(current.metadata["media_metadata"]) or None,
            priority=100,
            name="test.capture_metadata",
        )
        registry.run_post_download_pipeline(event)
        self.assertEqual(seen[0]["placement"]["episode_number"], 100)
        self.assertTrue(seen[0]["confirmed"])

    def test_all_modules_register_without_rewriting_core_entrypoint(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module as register_media_search
        from app.modules.open115 import register_module as register_open115
        from app.modules.renaming import register_module as register_renaming
        from app.modules.plex_management import register_module as register_plex_management

        registry = ModuleRegistry()
        for register in (register_open115, register_media_search, register_renaming, register_plex_management):
            register(registry)

        self.assertIsNotNone(registry.download_provider)
        self.assertIsNotNone(registry.storage_provider)
        self.assertEqual(
            [item.name for item in registry.post_download_processors],
            ["renaming.tvdb_episode", "renaming.generic_media", "open115.unorganized_fallback"],
        )
        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q", "search", "s", "plex"],
        )
        self.assertEqual(registry.download_completion_hooks[0][0], "plex.management")

    def test_terminal_processor_prevents_unorganized_fallback(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent, ModuleRegistry, PostDownloadResult
        from app.modules.open115 import register_module as register_open115

        init.bot_config = {"media": {"unorganized_path": "/未整理"}}
        storage = Mock()
        registry = ModuleRegistry()
        register_open115(registry)
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/已整理", should_stop=True),
            priority=100,
            name="test.terminal",
        )

        result = registry.run_post_download_pipeline(
            DownloadCompletedEvent(
                link="magnet:?xt=urn:btih:" + "b" * 40,
                selected_path="/电影",
                user_id=1,
                final_path="/电影/Raw.Release",
                resource_name="Raw.Release",
                storage=storage,
            )
        )

        self.assertEqual(result.final_path, "/已整理")
        storage.move_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()

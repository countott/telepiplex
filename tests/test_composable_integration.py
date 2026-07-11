import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableIntegrationTest(unittest.TestCase):
    def test_confirmed_download_plan_survives_request_event_and_renaming_pipeline(self):
        from app.core.module_registry import (
            DownloadCompletedEvent,
            DownloadRequest,
            ModuleRegistry,
        )
        from app.utils.search_plan import attach_download_plan, confirm_download_plan

        plan = confirm_download_plan(
            {
                "schema_version": 1,
                "plan_id": "plan-a",
                "display_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_identity": "extension_movie",
                "relation": {
                    "type": "sequel",
                    "target_series_title": "Someday or One Day",
                    "target_series_year": "2019",
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": 0,
                    "episode_number": 100,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    "provider": "wikipedia",
                    "availability": "ok",
                    "verification": "verified",
                },
                "prowlarr_queries": ["Someday or One Day The Movie 2022"],
                "evidence": {},
                "warnings": [],
                "confirmed": False,
            }
        )
        request = DownloadRequest(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/真人剧集",
            user_id=1,
            metadata=attach_download_plan({"source": "confirmed"}, plan),
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
            lambda current: seen.append(current.metadata["download_plan"]) or None,
            priority=100,
            name="test.capture_plan",
        )
        registry.run_post_download_pipeline(event)
        self.assertEqual(seen[0]["placement"]["episode_number"], 100)
        self.assertTrue(seen[0]["confirmed"])

    def test_all_modules_register_without_rewriting_core_entrypoint(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module as register_media_search
        from app.modules.open115 import register_module as register_open115
        from app.modules.renaming import register_module as register_renaming

        registry = ModuleRegistry()
        for register in (register_open115, register_media_search, register_renaming):
            register(registry)

        self.assertIsNotNone(registry.download_provider)
        self.assertIsNotNone(registry.storage_provider)
        self.assertEqual(
            [item.name for item in registry.post_download_processors],
            ["renaming.tvdb_episode", "renaming.generic_media", "open115.unorganized_fallback"],
        )
        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q", "search", "s"],
        )

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

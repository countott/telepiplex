from copy import deepcopy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class CopyingDownloadProvider:
    def __init__(self, *, final_path, resource_name, storage):
        self.final_path = final_path
        self.resource_name = resource_name
        self.storage = storage

    def submit(self, request):
        from app.core.module_registry import DownloadCompletedEvent

        return DownloadCompletedEvent(
            link=request.link,
            selected_path=request.selected_path,
            user_id=request.user_id,
            final_path=self.final_path,
            resource_name=self.resource_name,
            naming_metadata=deepcopy(request.naming_metadata),
            metadata=deepcopy(request.metadata),
            provider="115",
            storage=self.storage,
        )


class FakeRenameStorage:
    def get_file_info(self, _path):
        return None

    def create_dir_recursive(self, _path):
        return True

    def rename(self, _path, _new_name):
        return True

    def move_file(self, _path, _target):
        return True

    def delete_single_file(self, _path):
        return True


class IntegrationPlexForMediaMetadata:
    def __init__(self, *, title="想见你", year=2022, media_type="episode"):
        self.title = title
        self.year = year
        self.media_type = media_type
        self.custom_title = None
        self.poster_url = None
        self.special_lookup = None
        self.expected_final_paths = []

    def _item(self, rating_key="42"):
        return {
            "rating_key": str(rating_key),
            "title": self.custom_title or self.title,
            "year": self.year,
            "media_type": self.media_type,
            "summary": "",
            "guids": [],
        }

    def snapshot_recent(self, _library_id):
        return {"41"}

    def scan_library(self, _library_id):
        return None

    def find_series_episode(
        self,
        _library_id,
        *,
        tvdb_series_id="",
        title="",
        year="",
        season_number=0,
        episode_number=0,
        expected_final_paths=(),
    ):
        self.special_lookup = (int(season_number), int(episode_number))
        self.expected_final_paths = list(expected_final_paths)
        return self._item()

    def locate_candidates(self, _library_id, _before_rating_keys):
        return [self._item("43")]

    def get_item(self, rating_key):
        return self._item(rating_key)

    def edit_custom_episode_metadata(
        self,
        rating_key,
        *,
        title="",
        summary="",
        original_release_date="",
        year="",
    ):
        self.custom_title = title
        return self._item(rating_key)

    def refresh_zh_cn(self, rating_key):
        return self._item(rating_key)

    def set_poster_url(self, rating_key, url):
        self.poster_url = url
        return self._item(rating_key)

    def list_match_candidates(self, *_args, **_kwargs):
        return []

    def fix_match(self, *_args, **_kwargs):
        raise AssertionError("contract pipeline must not reclassify this item")

    def list_posters(self, _rating_key):
        return []

    def list_streams(self, _rating_key):
        return []


def make_four_category_routes():
    return [
        {"kind": "live_action_series", "path": "/真人剧集", "plex_library_id": "11"},
        {"kind": "live_action_movie", "path": "/真人电影", "plex_library_id": "12"},
        {"kind": "animated_movie", "path": "/动画电影", "plex_library_id": "13"},
        {"kind": "animated_series", "path": "/动画剧集", "plex_library_id": "14"},
    ]


class ComposableIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.temp_path = Path(self._tempdir.name)

    @staticmethod
    def _hypotheses():
        return {
            "status": "ok",
            "hypotheses": [{
                "title": "想见你",
                "year": "2022",
                "content_identity": "extension_movie",
                "scope": "movie",
                "possible_related_series": ["Someday or One Day"],
            }],
            "source_queries": {
                "wikipedia": ["想见你 电影"],
                "douban": ["想见你 2022"],
                "tvdb": ["Someday or One Day"],
            },
            "warnings": [],
        }

    @staticmethod
    def _temporary_draft():
        source_url = "https://zh.wikipedia.org/wiki/想見你_(電影)"
        return {
            "plan_id": "ignored-by-service",
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
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
                    "episode_number": None,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": source_url,
                    "provider": "wikipedia",
                    "availability": "ok",
                    "verification": "verified",
                },
                "items": [],
                "evidence": {},
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    @staticmethod
    def _primary_series_draft():
        return {
            "plan_id": "ignored-by-service",
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
                "identity": {
                    "chinese_title": "测试剧",
                    "english_title": "Test Show",
                    "year": "2024",
                    "content_kind": "series",
                    "external_ids": {},
                },
                "relation": {
                    "type": "primary",
                    "target_series": {},
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": None,
                    "episode_number": None,
                    "mapping_kind": "standalone",
                    "mapping_source": "ai",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "测试剧",
                    "url": "https://zh.wikipedia.org/wiki/测试剧",
                    "provider": "wikipedia",
                    "availability": "ok",
                    "verification": "verified",
                },
                "items": [{
                    "item_id": "main-1",
                    "content_role": "main_episode",
                    "season_number": 1,
                    "episode_number": 1,
                }],
                "evidence": {},
                "warnings": [],
            },
            "prowlarr_queries": ["Test Show S01E01 2024"],
        }

    @staticmethod
    def _providers():
        source_url = "https://zh.wikipedia.org/wiki/想見你_(電影)"
        return {
            "wikipedia": lambda _hypotheses: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{"title": "想见你"}],
                "source_urls": [source_url],
                "error": "",
            },
            "douban": lambda _hypotheses: {
                "source": "douban",
                "status": "ok",
                "facts": [{"title": "想见你"}],
                "source_urls": ["https://movie.douban.com/subject/35269113/"],
                "error": "",
            },
            "tvdb": lambda _hypotheses: {
                "source": "tvdb",
                "status": "not_found",
                "facts": [],
                "source_urls": [],
                "error": "",
            },
        }

    async def _produce_contract(self, metadata_id, draft):
        from app.services.search_planner import build_confirmable_search_plan
        from app.utils.search_plan import TemporarySpecialAllocator, confirm_media_metadata

        with patch(
            "app.services.search_planner.infer_search_hypotheses_with_ai",
            return_value=self._hypotheses(),
        ) as hypothesis_ai, patch(
            "app.services.search_planner.infer_media_metadata_draft_with_ai",
            return_value=draft,
        ) as metadata_ai:
            plan = await build_confirmable_search_plan(
                "想见你" if metadata_id == "metadata-a" else "测试剧 S01E01",
                metadata_id,
                providers=self._providers(),
                occupied_loader=lambda _contract: set(),
                allocator=TemporarySpecialAllocator(),
            )

        hypothesis_ai.assert_called_once()
        metadata_ai.assert_called_once()
        return confirm_media_metadata(plan)

    def _run_real_consumers(
        self,
        *,
        contract,
        selected_path,
        resource_name,
        source_file,
        ai_season,
        ai_episode,
        plex,
        database_name,
    ):
        import init
        from app.core.media_metadata import attach_media_metadata
        from app.core.module_registry import (
            DownloadRequest,
            ModuleRegistry,
        )
        from app.modules import plex_management, renaming
        from app.repositories.plex_jobs import PlexJobRepository
        from app.services.plex_management import PlexManagementService

        request = DownloadRequest(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path=selected_path,
            user_id=1,
            metadata=attach_media_metadata({"source": "confirmed"}, contract),
        )
        storage = FakeRenameStorage()
        provider = CopyingDownloadProvider(
            final_path=f"{selected_path}/Raw.Release",
            resource_name=resource_name,
            storage=storage,
        )
        registry = ModuleRegistry()
        registry.set_download_provider(provider)
        event = registry.dispatch_download(request)
        self.assertEqual(
            event.metadata["media_metadata"]["metadata_id"],
            contract["metadata_id"],
        )

        renaming.register_module(registry)
        repository = PlexJobRepository(self.temp_path / database_name)
        service = PlexManagementService(
            repository,
            plex,
            category_folders=make_four_category_routes(),
            scan_poll_interval=0,
            scan_timeout=0,
            sleeper=lambda _seconds: None,
        )
        service.enabled = True
        original_service = plex_management._service
        plex_management._service = service
        plex_management.register_module(registry)
        try:
            with patch.object(
                init,
                "bot_config",
                {
                    "ai": {
                        "api_url": "https://ai.example",
                        "api_key": "key",
                        "model": "model",
                    },
                    "media": {"unorganized_path": "/未整理"},
                },
            ), patch.object(
                renaming,
                "collect_storage_file_tree",
                return_value=[{
                    "name": source_file,
                    "relative_path": source_file,
                    "is_dir": False,
                }],
            ), patch.object(
                renaming,
                "infer_tvdb_episode_plan_with_ai",
                return_value={
                    "episode_map": [{
                        "source_file": source_file,
                        "season_number": ai_season,
                        "episode_number": ai_episode,
                    }]
                },
            ), patch.object(
                renaming,
                "_get_tvdb_candidates_and_episodes",
                return_value=([], []),
            ), patch.object(
                plex_management.plex_executor,
                "submit",
                side_effect=lambda function, *args: function(*args),
            ):
                result = registry.run_post_download_pipeline(event)
        finally:
            plex_management._service = original_service

        return result, repository

    async def test_one_media_metadata_id_survives_real_producer_rename_and_plex_job(self):
        contract = await self._produce_contract("metadata-a", self._temporary_draft())
        plex = IntegrationPlexForMediaMetadata()

        result, repository = self._run_real_consumers(
            contract=contract,
            selected_path="/真人剧集",
            resource_name="Raw.Release",
            source_file="Movie.mkv",
            ai_season=0,
            ai_episode=100,
            plex=plex,
            database_name="temporary.db",
        )

        jobs = repository.list(10)
        self.assertEqual(len(jobs), 1)
        persisted = jobs[0]["payload"]["metadata"]["media_metadata"]
        self.assertEqual(result.metadata["media_metadata"]["metadata_id"], "metadata-a")
        self.assertEqual(persisted["metadata_id"], "metadata-a")
        self.assertTrue(persisted["items"][0]["final_path"].endswith("S00E100.mkv"))
        self.assertEqual(plex.custom_title, "想见你")
        self.assertEqual(plex.special_lookup, (0, 100))
        self.assertEqual(
            plex.expected_final_paths,
            [persisted["items"][0]["final_path"]],
        )
        self.assertEqual(plex.poster_url, "https://image.example/poster.jpg")

    async def test_primary_series_lock_survives_pipeline_and_rejects_ai_renumbering(self):
        from app.utils.tvdb_rename import build_confirmed_rename_plan

        contract = await self._produce_contract(
            "metadata-series-a",
            self._primary_series_draft(),
        )
        wrong = build_confirmed_rename_plan(
            final_path="/真人剧集/Raw.Release",
            selected_path="/真人剧集",
            metadata={},
            media_metadata=contract,
            ai_plan={
                "episode_map": [{
                    "source_file": "Episode.mkv",
                    "season_number": 1,
                    "episode_number": 2,
                }]
            },
            file_tree=[{
                "name": "Episode.mkv",
                "relative_path": "Episode.mkv",
                "is_dir": False,
            }],
        )
        self.assertIsNone(wrong)

        plex = IntegrationPlexForMediaMetadata(
            title="测试剧",
            year=2024,
            media_type="show",
        )
        result, repository = self._run_real_consumers(
            contract=contract,
            selected_path="/真人剧集",
            resource_name="Test.Show.S01E01",
            source_file="Episode.mkv",
            ai_season=1,
            ai_episode=1,
            plex=plex,
            database_name="primary-series.db",
        )

        jobs = repository.list(10)
        self.assertEqual(len(jobs), 1)
        persisted = jobs[0]["payload"]["metadata"]["media_metadata"]
        self.assertEqual(result.metadata["media_metadata"]["metadata_id"], "metadata-series-a")
        self.assertEqual(persisted["metadata_id"], "metadata-series-a")
        self.assertEqual(
            (
                persisted["items"][0]["season_number"],
                persisted["items"][0]["episode_number"],
            ),
            (1, 1),
        )
        self.assertTrue(persisted["items"][0]["final_path"].endswith("S01E01.mkv"))

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

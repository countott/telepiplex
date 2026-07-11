import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


SPECIAL_FINAL_PATH = (
    "/真人剧集/想见你 (Someday or One Day)/"
    "Someday or One Day Season 00/Someday or One Day S00E100.mkv"
)


def make_resolved_special_metadata():
    from app.core.media_metadata import attach_media_metadata

    return attach_media_metadata({}, {
        "schema_version": 1,
        "metadata_id": "metadata-integration",
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
                "external_ids": {"tvdb": "series-1"},
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
            "availability": "ok",
            "verification": "verified",
        },
        "items": [{
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 100,
            "source_relative_path": "Movie.mkv",
            "final_path": SPECIAL_FINAL_PATH,
        }],
        "evidence": {},
        "warnings": [],
    })


class IntegrationPlex:
    def __init__(self):
        self.selected_audio = None
        self.selected_subtitle = None
        self.poster_url = None
        self.custom_title = None
        self.custom_summary = None
        self.custom_release_date = None
        self.special_lookup = None
        self.special_library_id = None
        self.special_expected_paths = ()

    def snapshot_recent(self, library_id):
        return {"41"}

    def scan_library(self, library_id):
        return None

    def locate_candidates(self, library_id, before_rating_keys):
        return [{
            "rating_key": "42", "title": "千与千寻", "year": 2001,
            "media_type": "movie",
        }]

    def find_series_episode(
        self,
        library_id,
        *,
        tvdb_series_id="",
        title="",
        year="",
        season_number=0,
        episode_number=0,
        expected_final_paths=(),
    ):
        self.special_library_id = str(library_id)
        self.special_lookup = (int(season_number), int(episode_number))
        self.special_expected_paths = tuple(expected_final_paths)
        if self.special_expected_paths != (SPECIAL_FINAL_PATH,):
            return None
        return {
            "rating_key": "100",
            "title": "Episode 100",
            "year": 2022,
            "media_type": "episode",
            "summary": "",
            "guids": [],
        }

    def get_item(self, rating_key):
        if self.special_lookup is not None:
            return {
                "rating_key": "100",
                "title": "Episode 100",
                "year": 2022,
                "media_type": "episode",
                "summary": "",
                "guids": [],
            }
        return {
            "rating_key": "42", "title": "千与千寻", "year": 2001,
            "media_type": "movie", "summary": "少女进入神灵世界的故事。",
            "guids": ["tmdb://129"],
        }

    def refresh_zh_cn(self, rating_key):
        return self.get_item(rating_key)

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
        self.custom_summary = summary
        self.custom_release_date = original_release_date
        return self.get_item(rating_key)

    def set_poster_url(self, rating_key, url):
        self.poster_url = url
        return self.get_item(rating_key)

    def list_streams(self, rating_key):
        return [{
            "id": 11,
            "audio_streams": [
                {"id": 20, "language_code": "eng", "codec": "truehd", "channels": 8, "bitrate": 5000},
                {"id": 21, "language_code": "jpn", "codec": "truehd", "channels": 8, "bitrate": 4000},
            ],
            "subtitle_streams": [
                {"id": 30, "language_code": "chi", "external": False, "selected": False},
                {"id": 31, "language_code": "chi", "external": True, "transient": False, "selected": False},
            ],
        }]

    def select_audio(self, rating_key, part_id, stream_id):
        self.selected_audio = stream_id

    def select_subtitle(self, rating_key, part_id, stream_id):
        self.selected_subtitle = stream_id


class IntegrationTmdb:
    def details(self, media_type, tmdb_id):
        return {"original_language": "ja"}

    def textless_posters(self, media_type, tmdb_id):
        return [{
            "url": "https://image.tmdb.org/t/p/original/textless.jpg",
            "file_path": "/textless.jpg", "iso_639_1": None,
            "vote_count": 10, "vote_average": 8.5, "width": 2000, "height": 3000,
        }]


class PlexManagementIntegrationTest(unittest.TestCase):
    def test_renaming_completion_runs_full_plex_pipeline(self):
        from app.core.module_registry import DownloadCompletedEvent, ModuleRegistry, PostDownloadResult
        from app.modules import plex_management as module
        from app.repositories.plex_jobs import PlexJobRepository
        from app.services.plex_management import PlexManagementService

        notifications = []
        fake_plex = IntegrationPlex()
        with tempfile.TemporaryDirectory() as tempdir:
            service = PlexManagementService(
                PlexJobRepository(Path(tempdir) / "plex.db"),
                fake_plex,
                tmdb=IntegrationTmdb(),
                notifier=lambda user_id, message, *extra: notifications.append(message),
                category_folders=[{"path": "/真人电影", "plex_library_id": "12"}],
                scan_poll_interval=0,
                scan_timeout=0,
            )
            service.enabled = True
            original_service = module._service
            module._service = service
            registry = ModuleRegistry()
            registry.add_post_download_processor(
                lambda event: PostDownloadResult(
                    True,
                    final_path="/真人电影/千与千寻 (Spirited Away)",
                    should_stop=True,
                ),
                priority=100,
                name="renaming.generic_media",
            )
            module.register_module(registry)
            event = DownloadCompletedEvent(
                link="magnet:?xt=urn:btih:" + "a" * 40,
                selected_path="/真人电影",
                user_id=1,
                final_path="/真人电影/Raw",
                resource_name="Spirited.Away.2001.2160p",
                naming_metadata={
                    "media_type": "movie",
                    "title": "千与千寻",
                    "original_title": "Spirited Away",
                    "year": 2001,
                    "external_ids": {"tmdb": "129"},
                },
            )
            try:
                with patch.object(
                    module.plex_executor,
                    "submit",
                    side_effect=lambda function, *args: function(*args),
                ):
                    result = registry.run_post_download_pipeline(event)
            finally:
                module._service = original_service

        self.assertEqual(result.final_path, "/真人电影/千与千寻 (Spirited Away)")
        self.assertEqual(fake_plex.selected_audio, 21)
        self.assertEqual(fake_plex.selected_subtitle, 31)
        self.assertEqual(
            fake_plex.poster_url,
            "https://image.tmdb.org/t/p/original/textless.jpg",
        )
        self.assertIn("Plex 管理", notifications[-1])

    def test_resolved_contract_completion_runs_exact_temporary_special_pipeline(self):
        from app.core.module_registry import (
            DownloadCompletedEvent,
            ModuleRegistry,
            PostDownloadResult,
        )
        from app.modules import plex_management as module
        from app.repositories.plex_jobs import PlexJobRepository
        from app.services.plex_management import PlexManagementService

        notifications = []
        fake_plex = IntegrationPlex()
        metadata = make_resolved_special_metadata()
        with tempfile.TemporaryDirectory() as tempdir:
            service = PlexManagementService(
                PlexJobRepository(Path(tempdir) / "plex.db"),
                fake_plex,
                tmdb=IntegrationTmdb(),
                notifier=lambda user_id, message, *extra: notifications.append(message),
                category_folders=[{
                    "kind": "live_action_series",
                    "path": "/真人剧集",
                    "plex_library_id": "11",
                }],
                scan_poll_interval=0,
                scan_timeout=0,
            )
            service.enabled = True
            original_service = module._service
            module._service = service
            registry = ModuleRegistry()
            registry.add_post_download_processor(
                lambda event: PostDownloadResult(
                    True,
                    final_path=SPECIAL_FINAL_PATH.rsplit("/", 1)[0],
                    should_stop=True,
                    metadata=metadata,
                ),
                priority=100,
                name="renaming.media_metadata",
            )
            module.register_module(registry)
            event = DownloadCompletedEvent(
                link="magnet:?xt=urn:btih:" + "a" * 40,
                selected_path="/真人剧集",
                user_id=1,
                final_path="/真人剧集/Raw",
                resource_name="Someday.Or.One.Day.The.Movie.2022",
                metadata=metadata,
            )
            try:
                with patch.object(
                    module.plex_executor,
                    "submit",
                    side_effect=lambda function, *args: function(*args),
                ):
                    result = registry.run_post_download_pipeline(event)
            finally:
                module._service = original_service
            persisted_job = service.jobs.list(1)[0]

        self.assertEqual(
            result.final_path,
            SPECIAL_FINAL_PATH.rsplit("/", 1)[0],
        )
        self.assertEqual(fake_plex.special_library_id, "11")
        self.assertEqual(fake_plex.special_lookup, (0, 100))
        self.assertEqual(fake_plex.special_expected_paths, (SPECIAL_FINAL_PATH,))
        self.assertEqual(fake_plex.custom_title, "想见你")
        self.assertEqual(fake_plex.custom_summary, "电影版延续电视剧故事。")
        self.assertEqual(fake_plex.custom_release_date, "2022-12-24")
        self.assertEqual(fake_plex.poster_url, "https://image.example/poster.jpg")
        self.assertEqual(
            persisted_job["payload"]["metadata"]["media_metadata"]["metadata_id"],
            "metadata-integration",
        )
        self.assertIn("Plex 管理", notifications[-1])


if __name__ == "__main__":
    unittest.main()

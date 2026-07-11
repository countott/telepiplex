import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class IntegrationPlex:
    def __init__(self):
        self.selected_audio = None
        self.selected_subtitle = None
        self.poster_url = None

    def snapshot_recent(self, library_id):
        return {"41"}

    def scan_library(self, library_id):
        return None

    def locate_candidates(self, library_id, before_rating_keys):
        return [{
            "rating_key": "42", "title": "千与千寻", "year": 2001,
            "media_type": "movie",
        }]

    def get_item(self, rating_key):
        return {
            "rating_key": "42", "title": "千与千寻", "year": 2001,
            "media_type": "movie", "summary": "少女进入神灵世界的故事。",
            "guids": ["tmdb://129"],
        }

    def refresh_zh_cn(self, rating_key):
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


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def make_completion():
    from app.core.module_registry import (
        DownloadCompletedEvent,
        DownloadPipelineCompletion,
        PostDownloadResult,
    )

    event = DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path="/电影",
        user_id=7,
        final_path="/电影/电影 (Movie)",
        resource_name="Movie.2024.2160p",
        naming_metadata={
            "media_type": "movie",
            "title": "电影",
            "original_title": "Movie",
            "year": 2024,
            "external_ids": {"tmdb": "20"},
        },
        provider="115",
    )
    return DownloadPipelineCompletion(
        event=event,
        result=PostDownloadResult(True, final_path=event.final_path, should_stop=True),
        terminal_processor="renaming.generic_media",
    )


class FakePlex:
    def __init__(self, *, wrong_match=False, poster_error=None, match_candidates=None):
        self.calls = []
        self.wrong_match = wrong_match
        self.poster_error = poster_error
        self.match_candidates = match_candidates

    def snapshot_recent(self, library_id):
        self.calls.append("snapshot_recent")
        return {"41"}

    def scan_library(self, library_id):
        self.calls.append("scan_library")

    def locate_candidates(self, library_id, before_rating_keys):
        self.calls.append("locate_candidates")
        return [{"rating_key": "42", "title": "电影", "year": 2024, "media_type": "movie"}]

    def get_item(self, rating_key):
        self.calls.append("get_item")
        return {
            "rating_key": "42",
            "title": "电影",
            "year": 2024,
            "media_type": "movie",
            "summary": "中文简介",
            "guids": ["tmdb://999" if self.wrong_match else "tmdb://20"],
        }

    def list_match_candidates(self, rating_key, title=None, year=None):
        self.calls.append("list_match_candidates")
        return self.match_candidates or [
            {"guid": "tmdb://20", "guids": ["tmdb://20"], "title": "电影", "year": 2024},
            {"guid": "tmdb://21", "guids": ["tmdb://21"], "title": "电影", "year": 2024},
        ]

    def fix_match(self, rating_key, candidate_guid):
        self.calls.append("fix_match")
        self.wrong_match = False
        return self.get_item(rating_key)

    def refresh_zh_cn(self, rating_key):
        self.calls.append("refresh_zh_cn")
        return {"rating_key": rating_key, "title": "电影", "summary": "中文简介"}

    def list_posters(self, rating_key):
        return []

    def set_poster_url(self, rating_key, url):
        self.calls.append("set_poster_url")
        if self.poster_error:
            raise self.poster_error
        return {"rating_key": rating_key}

    def list_streams(self, rating_key):
        self.calls.append("list_streams")
        return [{
            "id": 11,
            "audio_streams": [{
                "id": 21, "language_code": "jpn", "codec": "truehd",
                "channels": 8, "bitrate": 4000, "selected": False,
            }],
            "subtitle_streams": [{
                "id": 31, "language_code": "chi", "external": True,
                "transient": False, "selected": False,
            }],
        }]

    def select_audio(self, rating_key, part_id, stream_id):
        self.calls.append("select_audio")

    def select_subtitle(self, rating_key, part_id, stream_id):
        self.calls.append("select_subtitle")

    def server_status(self):
        return {"online": True}

    def list_libraries(self):
        return [{"id": "12", "title": "电影"}]


class FakeTmdb:
    def __init__(self, error=None):
        self.error = error

    def details(self, media_type, tmdb_id):
        if self.error:
            raise self.error
        return {"id": 20, "original_language": "ja"}

    def textless_posters(self, media_type, tmdb_id):
        if self.error:
            raise self.error
        return [{
            "file_path": "/poster.jpg", "url": "https://tmdb/poster.jpg",
            "iso_639_1": None, "vote_count": 2, "vote_average": 8,
            "width": 1000, "height": 1500,
        }]


class PlexManagementServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        from app.repositories.plex_jobs import PlexJobRepository

        self.jobs = PlexJobRepository(Path(self.tempdir.name) / "plex.db")

    def tearDown(self):
        self.tempdir.cleanup()

    def make_service(self, *, plex=None, tmdb=None, notifier=None):
        from app.services.plex_management import PlexManagementService

        return PlexManagementService(
            self.jobs,
            plex or FakePlex(),
            tmdb=tmdb or FakeTmdb(),
            category_folders=[{"path": "/电影", "plex_library_id": "12"}],
            scan_poll_interval=0,
            scan_timeout=0,
            sleeper=lambda _: None,
            notifier=notifier,
        )

    def test_run_job_executes_steps_in_order(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(make_completion())
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(plex.calls, [
            "snapshot_recent", "scan_library", "locate_candidates", "get_item",
            "refresh_zh_cn", "set_poster_url", "list_streams",
            "select_audio", "select_subtitle",
        ])
        self.assertEqual(result["step_results"]["streams"]["subtitle"]["source"], "external")

    def test_artwork_failure_does_not_block_stream_selection(self):
        plex = FakePlex()
        service = self.make_service(plex=plex, tmdb=FakeTmdb(RuntimeError("tmdb down")))

        result = service.run_job(service.enqueue_completion(make_completion())["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["step_results"]["artwork"]["status"], "warning")
        self.assertEqual(result["step_results"]["streams"]["status"], "warning")
        self.assertNotIn("set_poster_url", plex.calls)
        self.assertIn("select_subtitle", plex.calls)

    def test_wrong_match_with_multiple_candidates_waits_for_confirmation(self):
        plex = FakePlex(wrong_match=True, match_candidates=[
            {"guid": "tmdb://20", "guids": ["tmdb://20"], "title": "电影 A", "year": 2024},
            {"guid": "tmdb://20", "guids": ["tmdb://20"], "title": "电影 B", "year": 2024},
        ])
        service = self.make_service(plex=plex)

        result = service.run_job(service.enqueue_completion(make_completion())["id"])

        self.assertEqual(result["state"], "waiting_match_confirmation")
        candidates = result["step_results"]["matching"]["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertNotIn("refresh_zh_cn", plex.calls)

    def test_unique_exact_match_is_fixed_automatically(self):
        plex = FakePlex(wrong_match=True)
        service = self.make_service(plex=plex)

        result = service.run_job(service.enqueue_completion(make_completion())["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["step_results"]["matching"]["action"], "fixed")
        self.assertIn("fix_match", plex.calls)

    def test_location_confirmation_resumes_without_rescanning(self):
        plex = FakePlex()
        plex.locate_candidates = lambda library_id, before: [
            {"rating_key": "42", "title": "候选一", "year": 2024, "media_type": "movie"},
            {"rating_key": "43", "title": "候选二", "year": 2024, "media_type": "movie"},
        ]
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(make_completion())
        waiting = service.run_job(job["id"])

        result = service.confirm_match(waiting["id"], "42")

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["rating_key"], "42")
        self.assertEqual(plex.calls.count("scan_library"), 1)

    def test_enqueue_is_idempotent_and_ignores_non_renaming_completion(self):
        service = self.make_service()
        completion = make_completion()

        first = service.enqueue_completion(completion)
        second = service.enqueue_completion(completion)
        object.__setattr__(completion, "terminal_processor", "open115.unorganized_fallback")

        self.assertEqual(first["id"], second["id"])
        self.assertIsNone(service.enqueue_completion(completion))

    def test_prepare_and_apply_operation_requires_single_use_token(self):
        plex = FakePlex(wrong_match=True)
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(make_completion())

        preview = service.prepare_operation(
            "fix_match", {"job_id": job["id"], "rating_key": "42", "candidate_guid": "tmdb://20"}
        )
        applied = service.apply_operation("fix_match", preview["payload"], preview["confirmation_token"])

        self.assertEqual(applied["status"], "applied")
        with self.assertRaises(ValueError):
            service.apply_operation("fix_match", preview["payload"], preview["confirmation_token"])


if __name__ == "__main__":
    unittest.main()

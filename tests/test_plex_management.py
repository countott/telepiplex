import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


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


def make_media_metadata_completion(mapping_kind):
    from app.core.media_metadata import attach_media_metadata
    from app.core.module_registry import (
        DownloadCompletedEvent,
        DownloadPipelineCompletion,
        PostDownloadResult,
    )

    episode = 100 if mapping_kind == "temporary_related_special" else 5
    episode_marker = f"E{episode:03d}" if episode >= 100 else f"E{episode:02d}"
    final_file = (
        "/真人剧集/想见你 (Someday or One Day)/"
        "Someday or One Day Season 00/"
        f"Someday or One Day S00{episode_marker}.mkv"
    )
    contract = {
        "schema_version": 1,
        "metadata_id": "metadata-a",
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
            "episode_number": episode,
            "mapping_kind": mapping_kind,
            "mapping_source": "tvdb" if mapping_kind == "tvdb_official" else "ai",
            "tvdb_episode_id": "episode-5" if mapping_kind == "tvdb_official" else "",
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
            "episode_number": episode,
            "final_path": final_file,
        }],
        "evidence": {},
        "warnings": ["TVDB编号尚未实时验证"] if mapping_kind == "ai_inferred_tvdb" else [],
    }
    if mapping_kind == "standalone":
        contract["identity"]["content_kind"] = "movie"
        contract["relation"]["target_series"] = {}
        contract["placement"].update({
            "library_type": "movie",
            "category_kind": "live_action_movie",
            "season_number": None,
            "episode_number": None,
        })
        contract["items"] = []
    metadata = attach_media_metadata({}, contract)
    selected_path = (
        "/真人剧集"
        if contract["placement"]["library_type"] == "series"
        else "/真人电影"
    )
    event = DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path=selected_path,
        user_id=7,
        final_path="/download/raw",
        resource_name="Media.Release",
        provider="115",
        metadata=metadata,
    )
    result = PostDownloadResult(
        True,
        final_path=(
            final_file.rsplit("/", 1)[0]
            if contract["items"]
            else "/真人电影/想见你"
        ),
        should_stop=True,
        metadata=metadata,
    )
    return DownloadPipelineCompletion(
        event=event,
        result=result,
        terminal_processor="renaming.media_metadata",
    )


def make_four_category_routes():
    return [
        {"kind": "live_action_series", "path": "/真人剧集", "plex_library_id": "11"},
        {"kind": "live_action_movie", "path": "/真人电影", "plex_library_id": "12"},
        {"kind": "animated_movie", "path": "/动画电影", "plex_library_id": "13"},
        {"kind": "animated_series", "path": "/动画剧集", "plex_library_id": "14"},
    ]


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
        self.calls.append("find_series_episode")
        return {
            "rating_key": "42",
            "title": f"Episode {episode_number}",
            "year": 2022,
            "media_type": "episode",
            "summary": "",
            "guids": [],
        }

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

    def edit_custom_episode_metadata(
        self,
        rating_key,
        *,
        title="",
        summary="",
        original_release_date="",
        year="",
    ):
        self.calls.append("edit_custom_episode_metadata")
        return {
            "rating_key": str(rating_key),
            "title": title,
            "summary": summary,
            "year": int(year or 0),
            "guids": [],
        }

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
    def test_safe_error_redacts_tokens_from_upstream_urls(self):
        from app.services.plex_management import PlexManagementService

        message = PlexManagementService._safe_error(RuntimeError(
            "GET /library?X-Plex-Token=plex-secret&api_key=fanart-secret Authorization: Bearer ai-secret"
        ))

        self.assertNotIn("plex-secret", message)
        self.assertNotIn("fanart-secret", message)
        self.assertNotIn("ai-secret", message)
        self.assertIn("X-Plex-Token=***", message)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        from app.repositories.plex_jobs import PlexJobRepository

        self.jobs = PlexJobRepository(Path(self.tempdir.name) / "plex.db")

    def tearDown(self):
        self.tempdir.cleanup()

    def make_service(
        self,
        *,
        plex=None,
        tmdb=None,
        notifier=None,
        category_folders=None,
        scan_poll_interval=0,
        scan_timeout=0,
    ):
        from app.services.plex_management import PlexManagementService

        return PlexManagementService(
            self.jobs,
            plex or FakePlex(),
            tmdb=tmdb or FakeTmdb(),
            category_folders=category_folders or (
                [{"path": "/电影", "plex_library_id": "12"}]
                + make_four_category_routes()
            ),
            scan_poll_interval=scan_poll_interval,
            scan_timeout=scan_timeout,
            sleeper=lambda _: None,
            notifier=notifier,
        )

    def test_temporary_special_routes_by_kind_and_writes_confirmed_metadata(self):
        plex = FakePlex()
        plex.find_series_episode = Mock(return_value={
            "rating_key": "42",
            "title": "Episode 100",
            "guids": [],
            "media_type": "episode",
        })
        plex.edit_custom_episode_metadata = Mock(return_value={"rating_key": "42"})
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("temporary_related_special")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        lookup = plex.find_series_episode.call_args
        self.assertEqual(lookup.args[0], "11")
        self.assertEqual(lookup.kwargs["season_number"], 0)
        self.assertEqual(lookup.kwargs["episode_number"], 100)
        self.assertEqual(
            lookup.kwargs["expected_final_paths"],
            [
                "/真人剧集/想见你 (Someday or One Day)/"
                "Someday or One Day Season 00/Someday or One Day S00E100.mkv"
            ],
        )
        plex.edit_custom_episode_metadata.assert_called_once_with(
            "42",
            title="想见你",
            summary="电影版延续电视剧故事。",
            original_release_date="2022-12-24",
            year="2022",
        )
        self.assertIn("set_poster_url", plex.calls)
        self.assertNotIn("list_match_candidates", plex.calls)
        self.assertNotIn("fix_match", plex.calls)
        self.assertNotIn("refresh_zh_cn", plex.calls)

    def test_temporary_special_wrong_final_path_fails_before_any_write(self):
        plex = FakePlex()
        plex.find_series_episode = Mock(return_value=None)
        plex.edit_custom_episode_metadata = Mock()
        plex.refresh_zh_cn = Mock()
        plex.set_poster_url = Mock()
        plex.fix_match = Mock()
        service = self.make_service(plex=plex, scan_timeout=0)

        job = service.enqueue_completion(
            make_media_metadata_completion("temporary_related_special")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "failed")
        plex.edit_custom_episode_metadata.assert_not_called()
        plex.refresh_zh_cn.assert_not_called()
        plex.set_poster_url.assert_not_called()
        plex.fix_match.assert_not_called()

    def test_official_special_only_verifies_tvdb_episode(self):
        plex = FakePlex()
        official_item = {
            "rating_key": "42",
            "title": "Official",
            "guids": ["tvdb://episode-5"],
            "media_type": "episode",
        }
        plex.find_series_episode = Mock(return_value=official_item)
        plex.get_item = Mock(return_value=official_item)
        plex.edit_custom_episode_metadata = Mock()
        plex.refresh_zh_cn = Mock()
        plex.set_poster_url = Mock()
        plex.fix_match = Mock()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("tvdb_official")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        plex.edit_custom_episode_metadata.assert_not_called()
        plex.refresh_zh_cn.assert_not_called()
        plex.set_poster_url.assert_not_called()
        plex.fix_match.assert_not_called()

    def test_ai_inferred_special_fails_without_tvdb_guid_and_never_renumbers(self):
        plex = FakePlex()
        unverified = {
            "rating_key": "42",
            "title": "Unverified",
            "guids": [],
            "media_type": "episode",
        }
        plex.find_series_episode = Mock(return_value=unverified)
        plex.get_item = Mock(return_value=unverified)
        plex.edit_custom_episode_metadata = Mock()
        plex.refresh_zh_cn = Mock()
        plex.set_poster_url = Mock()
        plex.fix_match = Mock()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("ai_inferred_tvdb")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "failed")
        self.assertEqual(
            job["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"],
            5,
        )
        plex.edit_custom_episode_metadata.assert_not_called()
        plex.refresh_zh_cn.assert_not_called()
        plex.set_poster_url.assert_not_called()
        plex.fix_match.assert_not_called()

    def test_ai_inferred_special_retry_preserves_path_and_locked_number(self):
        plex = FakePlex()
        unverified = {
            "rating_key": "42",
            "title": "Unverified",
            "guids": [],
            "media_type": "episode",
        }
        plex.find_series_episode = Mock(return_value=unverified)
        plex.get_item = Mock(return_value=unverified)
        plex.edit_custom_episode_metadata = Mock()
        plex.refresh_zh_cn = Mock()
        plex.set_poster_url = Mock()
        plex.fix_match = Mock()
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(
            make_media_metadata_completion("ai_inferred_tvdb")
        )
        original_path = job["payload"]["final_path"]
        self.assertEqual(service.run_job(job["id"])["state"], "failed")
        plex.get_item.return_value = {
            "rating_key": "42",
            "title": "Verified",
            "guids": ["tvdb://episode-5"],
            "media_type": "episode",
        }

        retried = service.retry_job(job["id"])

        self.assertEqual(retried["state"], "completed")
        self.assertEqual(retried["payload"]["final_path"], original_path)
        self.assertEqual(
            retried["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"],
            5,
        )
        plex.edit_custom_episode_metadata.assert_not_called()
        plex.refresh_zh_cn.assert_not_called()
        plex.set_poster_url.assert_not_called()
        plex.fix_match.assert_not_called()

    def test_all_four_contract_categories_route_without_reclassification(self):
        routes = {
            "live_action_series": ("series", "11"),
            "live_action_movie": ("movie", "12"),
            "animated_movie": ("movie", "13"),
            "animated_series": ("series", "14"),
        }
        service = self.make_service(category_folders=make_four_category_routes())
        for category_kind, (library_type, library_id) in routes.items():
            with self.subTest(category_kind=category_kind):
                completion = make_media_metadata_completion("standalone")
                contract = completion.event.metadata["media_metadata"]
                contract["placement"].update({
                    "category_kind": category_kind,
                    "library_type": library_type,
                })
                if library_type == "series":
                    contract["identity"]["content_kind"] = "series"
                    contract["items"] = [{
                        "content_role": "main_episode",
                        "season_number": 1,
                        "episode_number": 1,
                        "final_path": "/Series/Series S01E01.mkv",
                    }]
                else:
                    contract["identity"]["content_kind"] = "movie"
                    contract["items"] = []
                job = {
                    "payload": {
                        "selected_path": "/故意错误的旧路径",
                        "metadata": completion.event.metadata,
                    }
                }
                self.assertEqual(service._route_library(job), library_id)

    def test_special_location_polls_until_scan_exposes_episode(self):
        plex = FakePlex()
        plex.find_series_episode = Mock(side_effect=[None, {
            "rating_key": "42",
            "title": "Episode 100",
            "guids": [],
            "media_type": "episode",
        }])
        service = self.make_service(
            plex=plex,
            scan_poll_interval=0,
            scan_timeout=1,
        )

        job = service.enqueue_completion(
            make_media_metadata_completion("temporary_related_special")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(plex.find_series_episode.call_count, 2)

    def test_standalone_ignores_non_plex_ids_and_verifies_title_year(self):
        completion = make_media_metadata_completion("standalone")
        identity = completion.event.metadata["media_metadata"]["identity"]
        identity.update({
            "chinese_title": "电影",
            "english_title": "Movie",
            "year": "2024",
            "external_ids": {"douban_subject": "123"},
        })
        service = self.make_service()

        job = service.enqueue_completion(completion)
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(
            result["step_results"]["matching"]["action"],
            "verified_by_title_year",
        )

    def test_contract_location_ambiguity_fails_without_confirmation(self):
        plex = FakePlex()
        plex.locate_candidates = Mock(return_value=[
            {"rating_key": "42", "title": "候选一", "year": 2024, "media_type": "movie"},
            {"rating_key": "43", "title": "候选二", "year": 2024, "media_type": "movie"},
        ])
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(make_media_metadata_completion("standalone"))
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "failed")
        self.assertNotEqual(result["state"], "waiting_match_confirmation")

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

    def test_contract_completion_persists_metadata_id_and_resolved_items(self):
        completion = make_media_metadata_completion("temporary_related_special")

        job = self.make_service().enqueue_completion(completion)

        contract = job["payload"]["metadata"]["media_metadata"]
        self.assertEqual(contract["metadata_id"], "metadata-a")
        self.assertTrue(contract["items"][0]["final_path"].endswith("S00E100.mkv"))

    def test_unresolved_locked_special_is_not_enqueued(self):
        completion = make_media_metadata_completion("temporary_related_special")
        completion.event.metadata["media_metadata"]["items"] = []
        completion.result.metadata = completion.event.metadata

        self.assertIsNone(self.make_service().enqueue_completion(completion))

    def test_unrelated_resolved_episode_cannot_satisfy_locked_special(self):
        completion = make_media_metadata_completion("temporary_related_special")
        completion.event.metadata["media_metadata"]["items"] = [{
            "content_role": "extension_movie",
            "season_number": 0,
            "episode_number": 101,
            "final_path": "/真人剧集/Series/Season 00/Series S00E101.mkv",
        }]
        completion.result.metadata = completion.event.metadata

        self.assertIsNone(self.make_service().enqueue_completion(completion))

    def test_standalone_contract_uses_terminal_path_without_inventing_items(self):
        completion = make_media_metadata_completion("standalone")

        job = self.make_service().enqueue_completion(completion)

        self.assertIsNotNone(job)
        contract = job["payload"]["metadata"]["media_metadata"]
        self.assertEqual(contract["metadata_id"], "metadata-a")
        self.assertEqual(contract["items"], [])
        self.assertEqual(job["payload"]["final_path"], "/真人电影/想见你")

    def test_present_but_invalid_contract_never_falls_back_to_legacy_job(self):
        completion = make_media_metadata_completion("standalone")
        completion.event.metadata["media_metadata"]["schema_version"] = 999
        completion.result.metadata = completion.event.metadata

        self.assertIsNone(self.make_service().enqueue_completion(completion))

    def test_same_metadata_id_is_idempotent_when_terminal_path_changes(self):
        service = self.make_service()
        first = make_media_metadata_completion("temporary_related_special")
        second = make_media_metadata_completion("temporary_related_special")
        object.__setattr__(second.result, "final_path", "/retry/changed/path")

        first_job = service.enqueue_completion(first)
        second_job = service.enqueue_completion(second)

        self.assertEqual(first_job["id"], second_job["id"])
        self.assertEqual(
            first_job["idempotency_key"],
            second_job["idempotency_key"],
        )
        self.assertNotEqual(first_job["payload"]["final_path"], "/retry/changed/path")

    def test_completion_payload_is_deep_copied_from_terminal_metadata(self):
        completion = make_media_metadata_completion("temporary_related_special")
        service = self.make_service()

        payload = service._completion_payload(completion)
        completion.result.metadata["media_metadata"]["identity"]["chinese_title"] = "已篡改"

        self.assertEqual(
            payload["metadata"]["media_metadata"]["identity"]["chinese_title"],
            "想见你",
        )

    def test_event_metadata_overrides_stale_naming_metadata(self):
        completion = make_media_metadata_completion("temporary_related_special")
        completion.event.naming_metadata = {
            "source": "stale-naming",
            "nested": {"value": "stale"},
        }
        completion.event.metadata["source"] = "confirmed-event"
        completion.result.metadata = None

        payload = self.make_service()._completion_payload(completion)

        self.assertEqual(payload["metadata"]["source"], "confirmed-event")

    def test_restart_reuses_persisted_pre_scan_snapshot(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(make_completion())
        self.jobs.update(
            job["id"],
            state="scanning",
            step_results={
                "scanning": {
                    "status": "started",
                    "library_id": "12",
                    "before_rating_keys": ["41"],
                }
            },
        )
        plex.snapshot_recent = Mock(side_effect=AssertionError("snapshot must be reused"))

        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        plex.snapshot_recent.assert_not_called()

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

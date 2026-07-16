import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


@dataclass
class DownloadCompletedEvent:
    link: str
    selected_path: str
    user_id: int
    final_path: str
    resource_name: str
    naming_metadata: dict | None = None
    metadata: dict | None = None
    provider: str = "115"
    storage: Any = None


@dataclass
class PostDownloadResult:
    handled: bool
    final_path: str | None = None
    message: str | None = None
    should_stop: bool = False
    metadata: dict | None = None


@dataclass(frozen=True)
class DownloadPipelineCompletion:
    event: DownloadCompletedEvent
    result: PostDownloadResult
    terminal_processor: str | None = None


def make_completion():
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
    from telepiplex_plugin_sdk.media_metadata import attach_media_metadata

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


def make_unresolved_standalone_series_completion():
    completion = make_media_metadata_completion("standalone")
    contract = completion.event.metadata["media_metadata"]
    contract["identity"]["content_kind"] = "series"
    contract["placement"].update({
        "library_type": "series",
        "category_kind": "live_action_series",
    })
    contract["items"] = [{
        "item_id": "episode-1",
        "content_role": "main_episode",
        "season_number": 1,
        "episode_number": 1,
    }]
    completion.event.selected_path = "/真人剧集"
    completion.result.final_path = "/未整理/Test.Show.S01E01"
    completion.result.metadata = completion.event.metadata
    return completion


def make_four_category_routes():
    return [
        {"kind": "live_action_series", "path": "/真人剧集", "plex_library_id": "11"},
        {"kind": "live_action_movie", "path": "/真人电影", "plex_library_id": "12"},
        {"kind": "animated_movie", "path": "/动画电影", "plex_library_id": "13"},
        {"kind": "animated_series", "path": "/动画剧集", "plex_library_id": "14"},
    ]


class FakePlex:
    def __init__(
        self,
        *,
        wrong_match=False,
        poster_error=None,
        match_candidates=None,
        missing_paths=None,
    ):
        self.calls = []
        self.wrong_match = wrong_match
        self.poster_error = poster_error
        self.match_candidates = match_candidates
        self.missing_paths = set(missing_paths or [])
        self.find_paths = []
        self.get_item_keys = []
        self.stream_rating_keys = []

    def snapshot_recent(self, library_id):
        self.calls.append("snapshot_recent")
        return {"41"}

    def scan_library(self, library_id):
        self.calls.append("scan_library")

    def find_item_by_path(self, library_id, final_path):
        self.calls.append("find_item_by_path")
        self.find_paths.append(str(final_path))
        if str(final_path) in self.missing_paths:
            return None
        rating_key = "43" if "E02" in str(final_path) else "42"
        return {
            "rating_key": rating_key,
            "title": "电影",
            "year": 2024,
            "media_type": "episode" if "Season" in str(final_path) else "movie",
            "summary": "中文简介",
            "guids": ["tmdb://20"],
        }

    def locate_candidates(self, library_id, before_rating_keys):
        self.calls.append("locate_candidates")
        return [{"rating_key": "42", "title": "电影", "year": 2024, "media_type": "movie"}]

    def find_movie(self, library_id, **_kwargs):
        self.calls.append("find_movie")
        return {"rating_key": "42", "title": "电影", "year": 2024, "media_type": "movie"}

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
        self.get_item_keys.append(str(rating_key))
        return {
            "rating_key": str(rating_key),
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
        self.stream_rating_keys.append(str(rating_key))
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
        from telepiplex_plex.management import PlexManagementService

        message = PlexManagementService._safe_error(RuntimeError(
            "GET /library?X-Plex-Token=plex-secret&api_key=fanart-secret Authorization: Bearer ai-secret"
        ))

        self.assertNotIn("plex-secret", message)
        self.assertNotIn("fanart-secret", message)
        self.assertNotIn("ai-secret", message)
        self.assertIn("X-Plex-Token=***", message)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        from telepiplex_plex.jobs import PlexJobRepository

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
        from telepiplex_plex.management import PlexManagementService

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

    def test_temporary_special_routes_by_kind_and_runs_only_enhancements(self):
        plex = FakePlex()
        plex.edit_custom_episode_metadata = Mock()
        plex.refresh_zh_cn = Mock()
        plex.fix_match = Mock()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("temporary_related_special")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        target = result["step_results"]["scanning"]["targets"]["S00E100"]
        self.assertEqual(target["library_id"], "11")
        self.assertEqual(target["rating_key"], "42")
        self.assertIn("set_poster_url", plex.calls)
        plex.edit_custom_episode_metadata.assert_not_called()
        plex.refresh_zh_cn.assert_not_called()
        plex.fix_match.assert_not_called()

    def test_temporary_special_wrong_final_path_fails_before_enhancements(self):
        completion = make_media_metadata_completion("temporary_related_special")
        final_path = completion.event.metadata["media_metadata"]["items"][0]["final_path"]
        plex = FakePlex(missing_paths={final_path})
        service = self.make_service(plex=plex, scan_timeout=0)

        job = service.enqueue_completion(completion)
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "failed")
        self.assertNotIn("artwork", result["step_results"])
        self.assertNotIn("list_streams", plex.calls)

    def test_official_special_preserves_artwork_and_runs_stream_enhancements(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("tvdb_official")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        artwork = result["step_results"]["artwork"]["targets"]["S00E005"]
        self.assertEqual(artwork["action"], "official_artwork_preserved")
        self.assertNotIn("set_poster_url", plex.calls)
        self.assertEqual(plex.stream_rating_keys, ["42", "42"])

    def test_ai_inferred_special_no_longer_requires_match_verification(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)

        job = service.enqueue_completion(
            make_media_metadata_completion("ai_inferred_tvdb")
        )
        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(
            job["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"],
            5,
        )
        self.assertNotIn("matching", result["step_results"])
        self.assertNotIn("localizing", result["step_results"])

    def test_failed_scan_retry_preserves_path_and_locked_number(self):
        completion = make_media_metadata_completion("ai_inferred_tvdb")
        final_path = completion.event.metadata["media_metadata"]["items"][0]["final_path"]
        plex = FakePlex(missing_paths={final_path})
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(completion)
        original_path = job["payload"]["final_path"]
        self.assertEqual(service.run_job(job["id"])["state"], "failed")
        plex.missing_paths.clear()

        retried = service.retry_job(job["id"])

        self.assertEqual(retried["state"], "completed")
        self.assertEqual(retried["payload"]["final_path"], original_path)
        self.assertEqual(
            retried["payload"]["metadata"]["media_metadata"]["placement"]["episode_number"],
            5,
        )

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
                target = {"category_kind": category_kind}
                job = {
                    "payload": {
                        "selected_path": "/故意错误的旧路径",
                        "metadata": completion.event.metadata,
                    }
                }
                self.assertEqual(service._route_library(job, target), library_id)

    def test_special_location_polls_until_scan_exposes_final_path(self):
        plex = FakePlex()
        plex.find_item_by_path = Mock(side_effect=[None, {
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
        self.assertEqual(plex.find_item_by_path.call_count, 2)

    def test_standalone_ignores_non_plex_ids_without_matching_or_localizing(self):
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
        self.assertNotIn("matching", result["step_results"])
        self.assertNotIn("localizing", result["step_results"])

    def test_organized_series_creates_one_job_with_all_resolved_targets(self):
        completion = make_unresolved_standalone_series_completion()
        contract = completion.result.metadata["media_metadata"]
        contract["items"][0]["final_path"] = "/Series/Show/Season 01/Show S01E01.mkv"
        contract["items"].append({
            "item_id": "episode-2", "content_role": "main_episode",
            "season_number": 1, "episode_number": 2,
            "final_path": "/Series/Show/Season 01/Show S01E02.mkv",
        })

        service = self.make_service()
        job = service.enqueue_organized_event({
            "resource_name": "Show",
            "final_path": "/Series/Test",
            "selected_path": "/Series",
            "chat_id": 10,
            "user_id": 123,
            "operation_id": "op-series",
            "operation_revision": 7,
            "media_metadata": contract,
        })

        self.assertEqual(len(self.jobs.list()), 1)
        self.assertEqual(
            [target["episode_number"] for target in job["payload"]["targets"]],
            [1, 2],
        )
        self.assertEqual(job["payload"]["operation_id"], "op-series")
        self.assertEqual(job["payload"]["operation_revision"], 7)

    def test_one_organized_job_scans_once_then_locates_each_final_path(self):
        completion = make_unresolved_standalone_series_completion()
        contract = completion.result.metadata["media_metadata"]
        contract["items"][0]["final_path"] = "/Series/Show/Season 01/Show S01E01.mkv"
        contract["items"].append({
            "item_id": "episode-2", "content_role": "main_episode",
            "season_number": 1, "episode_number": 2,
            "final_path": "/Series/Show/Season 01/Show S01E02.mkv",
        })
        plex = FakePlex()
        service = self.make_service(plex=plex)
        job = service.enqueue_organized_event({
            "resource_name": "Show",
            "final_path": "/Series/Show",
            "selected_path": "/Series",
            "media_metadata": contract,
        })

        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(plex.calls.count("scan_library"), 1)
        self.assertEqual(plex.find_paths, [
            "/Series/Show/Season 01/Show S01E01.mkv",
            "/Series/Show/Season 01/Show S01E02.mkv",
        ])
        scanning = result["step_results"]["scanning"]
        self.assertEqual(scanning["status"], "success")
        self.assertEqual(list(scanning["libraries"]), ["11"])
        self.assertEqual(list(scanning["targets"]), ["episode-1", "episode-2"])

    def test_partial_location_warns_and_enhances_only_located_targets(self):
        completion = make_unresolved_standalone_series_completion()
        contract = completion.result.metadata["media_metadata"]
        first_path = "/Series/Show/Season 01/Show S01E01.mkv"
        second_path = "/Series/Show/Season 01/Show S01E02.mkv"
        contract["items"] = [
            {
                "item_id": "episode-1",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": 1,
                "final_path": first_path,
            },
            {
                "item_id": "episode-2",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": 2,
                "final_path": second_path,
            },
        ]
        plex = FakePlex(missing_paths={second_path})
        service = self.make_service(plex=plex)
        job = service.enqueue_organized_event({
            "resource_name": "Show",
            "final_path": "/Series/Show",
            "media_metadata": contract,
        })

        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        scanning = result["step_results"]["scanning"]
        self.assertEqual(scanning["status"], "warning")
        self.assertEqual(scanning["targets"]["episode-1"]["status"], "success")
        self.assertEqual(scanning["targets"]["episode-2"]["status"], "warning")
        for stage in ("artwork", "audio", "subtitle"):
            self.assertEqual(
                list(result["step_results"][stage]["targets"]),
                ["episode-1"],
            )
        self.assertEqual(plex.get_item_keys, ["42"])
        self.assertEqual(plex.stream_rating_keys, ["42", "42"])

    def test_run_job_executes_steps_in_order(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)
        stages = []

        job = service.enqueue_completion(make_completion())
        result = service.run_job(
            job["id"],
            on_stage=lambda stage, _job: stages.append(stage),
        )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(plex.calls, [
            "scan_library", "find_item_by_path", "get_item", "set_poster_url",
            "list_streams", "select_audio", "list_streams", "select_subtitle",
        ])
        self.assertEqual(stages, ["scanning", "artwork", "audio", "subtitle"])
        self.assertEqual(
            result["step_results"]["subtitle"]["targets"]["legacy"]["source"],
            "external",
        )
        self.assertTrue(
            result["step_results"]["artwork"]["targets"]["legacy"]["attempted"]
        )

    def test_run_job_stops_before_next_step_after_cancel(self):
        from telepiplex_plex.management import PlexOperationCancelled

        plex = FakePlex()
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(make_completion())
        cancelled = False
        stages = []

        def on_stage(stage, _job):
            nonlocal cancelled
            stages.append(stage)
            if stage == "scanning":
                cancelled = True

        with self.assertRaises(PlexOperationCancelled):
            service.run_job(
                job["id"],
                should_cancel=lambda: cancelled,
                on_stage=on_stage,
            )

        self.assertEqual(stages, ["scanning"])
        self.assertEqual(plex.calls, [])

    def test_cancel_while_scanning_for_paths_propagates_as_cancel_not_failure(self):
        from telepiplex_plex.management import PlexOperationCancelled

        plex = FakePlex()
        plex.find_item_by_path = Mock(return_value=None)
        service = self.make_service(
            plex=plex,
            scan_poll_interval=0,
            scan_timeout=30,
        )
        job = service.enqueue_completion(make_completion())
        checks = 0

        def should_cancel():
            nonlocal checks
            checks += 1
            return checks >= 3

        with self.assertRaises(PlexOperationCancelled):
            service.run_job(job["id"], should_cancel=should_cancel)

        self.assertNotEqual(self.jobs.get(job["id"])["state"], "failed")

    def test_artwork_failure_does_not_block_stream_selection(self):
        plex = FakePlex(poster_error=RuntimeError("poster down"))
        service = self.make_service(plex=plex)

        result = service.run_job(service.enqueue_completion(make_completion())["id"])

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["step_results"]["artwork"]["status"], "warning")
        self.assertEqual(result["step_results"]["audio"]["status"], "success")
        self.assertEqual(result["step_results"]["subtitle"]["status"], "success")
        self.assertIn("select_subtitle", plex.calls)

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

    def test_unresolved_standalone_series_is_not_enqueued_from_terminal_path(self):
        from telepiplex_plugin_sdk.media_metadata import extract_confirmed_media_metadata

        completion = make_unresolved_standalone_series_completion()
        self.assertIsNotNone(
            extract_confirmed_media_metadata(completion.result.metadata)
        )

        self.assertIsNone(self.make_service().enqueue_completion(completion))

    def test_resolved_standalone_series_enqueues_confirmed_item(self):
        completion = make_unresolved_standalone_series_completion()
        completion.result.metadata["media_metadata"]["items"][0]["final_path"] = (
            "/真人剧集/Test Show/Test Show Season 01/Test Show S01E01.mkv"
        )

        job = self.make_service().enqueue_completion(completion)

        self.assertIsNotNone(job)
        self.assertEqual(
            job["payload"]["metadata"]["media_metadata"]["items"][0]["episode_number"],
            1,
        )

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

    def test_restart_reuses_completed_scan_results_without_rescanning(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)
        job = service.enqueue_completion(make_completion())
        self.jobs.update(
            job["id"],
            state="scanning",
            step_results={
                "scanning": {
                    "status": "success",
                    "libraries": {
                        "12": {"status": "success", "target_ids": ["legacy"]},
                    },
                    "targets": {
                        "legacy": {
                            "status": "success",
                            "library_id": "12",
                            "rating_key": "42",
                            "final_path": "/电影/电影 (Movie)",
                        },
                    },
                }
            },
        )
        plex.scan_library = Mock(side_effect=AssertionError("scan must be reused"))

        result = service.run_job(job["id"])

        self.assertEqual(result["state"], "completed")
        plex.scan_library.assert_not_called()

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

    def test_metadata_changes_share_one_confirmation_token(self):
        plex = FakePlex()
        service = self.make_service(plex=plex)
        changes = [
            {
                "action": "refresh_chinese_metadata",
                "payload": {"rating_key": "42"},
            },
            {
                "action": "set_textless_poster",
                "payload": {"rating_key": "42", "url": "https://image/poster.jpg"},
            },
        ]

        preview = service.prepare_operation("metadata_batch", {"changes": changes})
        applied = service.apply_operation(
            "metadata_batch", preview["payload"], preview["confirmation_token"]
        )

        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(applied["result"]["results"]), 2)
        self.assertIn("refresh_zh_cn", plex.calls)
        self.assertIn("set_poster_url", plex.calls)
        with self.assertRaises(ValueError):
            service.prepare_operation("metadata_batch", {"changes": [{
                "action": "scan_library", "payload": {"library_id": "12"},
            }]})


if __name__ == "__main__":
    unittest.main()

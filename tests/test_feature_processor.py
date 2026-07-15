import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telepiplex_plugin_sdk.media_metadata import attach_media_metadata

from telepiplex_renaming.models import DownloadCompletedEvent
from telepiplex_renaming.processor import process_generic_media, process_tvdb_episode
from telepiplex_renaming.service import RenamingFeature


ROOT = Path(__file__).resolve().parents[1]


class FakeStorage:
    def __init__(self, items):
        self.items = items
        self.renamed = []
        self.moved = []
        self.deleted = []
        self.created = []

    def get_file_info(self, path):
        if path in {"/Downloads/Release", "/Downloads/Series.Release"}:
            return {"file_id": "root", "file_category": "0"}
        return None

    def get_file_list(self, params):
        return self.items if params.get("cid") == "root" else []

    def create_dir_recursive(self, path):
        self.created.append(path)
        return {"file_id": "target"}

    def rename(self, path, name):
        self.renamed.append((path, name))
        return True

    def move_file(self, source, target):
        self.moved.append((source, target))
        return True

    def move_file_detailed(self, source, target):
        moved = self.move_file(source, target)
        return {"state": "moved" if moved else "copy_failed", "copied": moved,
                "source_deleted": moved, "source_path": source, "target_path": target}

    def delete_single_file(self, path):
        self.deleted.append(path)
        return True


class CleanupFailureStorage(FakeStorage):
    def delete_single_file(self, path):
        self.deleted.append(path)
        return path != "/Downloads/Release"


class SecondMoveFailureStorage(FakeStorage):
    def move_file(self, source, target):
        self.moved.append((source, target))
        return len(self.moved) < 2


class ExtraVideoDeleteFailureStorage(FakeStorage):
    def delete_single_file(self, path):
        self.deleted.append(path)
        return not path.endswith("sample.mp4")


class TargetConflictStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/中文电影 (English Movie)/English Movie.mkv"):
            return {"file_id": "existing", "file_category": "1"}
        return super().get_file_info(path)


class SeriesTargetConflictStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/English Series Season 01/English Series S01E01.mkv"):
            return {"file_id": "existing", "file_category": "1"}
        return super().get_file_info(path)


def movie_contract():
    return {
        "schema_version": 1,
        "metadata_id": "movie-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文电影",
            "english_title": "English Movie",
            "year": "2024",
            "content_kind": "movie",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_movie",
            "library_type": "movie",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [], "items": [],
    }


def series_contract():
    return {
        "schema_version": 1,
        "metadata_id": "series-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文剧集",
            "english_title": "English Series",
            "year": "2024",
            "content_kind": "main_episode",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_series",
            "library_type": "series",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [],
        "items": [{
            "item_id": "e1", "content_role": "main_episode",
            "season_number": 1, "episode_number": 1,
        }],
    }


class RenamingProcessorTest(unittest.TestCase):
    def setUp(self):
        from telepiplex_renaming.context import runtime_context

        runtime_context.configure({
            "media": {"unorganized_path": "/Unorganized"},
            "selection": {
                "movie_size_fallback_ratio": 1.5,
                "unmatched_large_ratio": 0.25,
                "unmatched_large_min_bytes": 300_000_000,
            },
            "ai": {},
            "metadata": {},
        })

    def test_ordinary_movie_keeps_largest_video_and_deletes_everything_else(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "subtitle.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024.1080p",
            naming_metadata=None,
            metadata=attach_media_metadata({}, movie_contract()),
            storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Movies/中文电影 (English Movie)")
        self.assertIn("/Downloads/Release", storage.deleted)
        self.assertNotIn("/Downloads/Release/Movie.2024.1080p.mkv", storage.deleted)
        self.assertEqual(storage.moved[-1][1], "/Movies/中文电影 (English Movie)")

    def test_source_cleanup_failure_is_reported_as_incomplete(self):
        storage = CleanupFailureStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("源目录清理未完成", result.message)

    @patch(
        "telepiplex_renaming.processor.infer_movie_cleanup_plan_with_ai",
        create=True,
    )
    def test_movie_release_filename_precedes_ai_and_size(self, ai_mock):
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 2_000},
            {"fn": "Movie.2024.720p.mkv", "fid": "2", "fc": "1", "fs": 8_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()),
            release={"title": "Movie.2024.1080p"}, storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        ai_mock.assert_not_called()
        self.assertEqual(storage.renamed[0][0], "/Downloads/Release/Movie.2024.1080p.mkv")

    @patch(
        "telepiplex_renaming.processor.infer_movie_cleanup_plan_with_ai",
        create=True,
    )
    def test_ambiguous_large_movie_candidates_are_decided_by_ai(self, ai_mock):
        from telepiplex_renaming.context import runtime_context

        runtime_context.config["ai"] = {
            "enable": True,
            "api_url": "https://ai.example/v1",
            "api_key": "key",
            "model": "model",
        }
        ai_mock.return_value = {
            "main_video": "Movie.2024.1080p.mkv",
            "discard_files": ["Movie.2024.720p.mkv"],
            "reason": "release and resolution evidence",
        }
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 2_000},
            {"fn": "Movie.2024.720p.mkv", "fid": "2", "fc": "1", "fs": 1_500},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()),
            release={"title": "Movie.2024.MULTI"}, storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        ai_mock.assert_called_once()
        context = ai_mock.call_args.args[0]
        self.assertEqual(context["release"]["title"], "Movie.2024.MULTI")
        self.assertEqual(storage.renamed[0][0], "/Downloads/Release/Movie.2024.1080p.mkv")

    def test_movie_target_conflict_moves_whole_release_to_unorganized(self):
        storage = TargetConflictStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 2_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Unorganized/Release")
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [("/Downloads/Release", "/Unorganized")])
        self.assertIn("冲突", result.message)

    @patch("telepiplex_renaming.processor.infer_tvdb_episode_plan_with_ai")
    def test_normal_series_filename_mapping_precedes_ai_and_deletes_extra_video(self, ai_mock):
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.S00E99.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "English.Series.S01E01.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01E01",
            naming_metadata={
                "source": "confirmed", "chinese_title": "中文剧集",
                "english_title": "English Series", "release_title": "English.Series.S01E01",
            },
            metadata=attach_media_metadata({}, series_contract()),
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Series/中文剧集 (English Series)")
        ai_mock.assert_not_called()
        self.assertIn("/Downloads/Series.Release/sample.S00E99.mp4", storage.deleted)
        self.assertIn("/Downloads/Series.Release", storage.deleted)
        self.assertTrue(storage.moved[-1][1].endswith("English Series Season 01"))

    @patch("telepiplex_renaming.processor.infer_tvdb_episode_plan_with_ai")
    def test_series_mid_batch_failure_becomes_partial_business_result(self, ai_mock):
        contract = series_contract()
        contract["items"].append({
            "item_id": "e2", "content_role": "main_episode",
            "season_number": 1, "episode_number": 2,
        })
        storage = SecondMoveFailureStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E02.mkv", "fid": "2", "fc": "1", "fs": 1000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, contract), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("部分完成（1/2）", result.message)
        ai_mock.assert_not_called()

    def test_series_extra_video_cleanup_failure_is_not_reported_as_success(self):
        storage = ExtraVideoDeleteFailureStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 10},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("部分完成（1/2）", result.message)

    @patch("telepiplex_renaming.processor.infer_tvdb_episode_plan_with_ai")
    def test_unmatched_large_series_video_requires_explicit_ai_discard(self, ai_mock):
        from telepiplex_renaming.context import runtime_context

        runtime_context.config["selection"].update({
            "unmatched_large_ratio": 0.25,
            "unmatched_large_min_bytes": 0,
        })
        ai_mock.return_value = {
            "episode_map": [{
                "source_file": "English.Series.S01E01.mkv",
                "season_number": 1,
                "episode_number": 1,
            }],
            "discard_files": ["English.Series.S01E01.720p.mkv"],
            "warnings": [],
        }
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E01.720p.mkv", "fid": "2", "fc": "1", "fs": 800},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            release={"title": "English.Series.S01E01.MULTI"},
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("✅"))
        ai_mock.assert_called_once()
        self.assertIn(
            "/Downloads/Series.Release/English.Series.S01E01.720p.mkv",
            storage.deleted,
        )

    def test_series_target_conflict_moves_whole_release_before_any_rename(self):
        storage = SeriesTargetConflictStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertEqual(result.final_path, "/Unorganized/Series.Release")
        self.assertEqual(storage.renamed, [])
        self.assertEqual(
            storage.moved,
            [("/Downloads/Series.Release", "/Unorganized")],
        )

    def test_single_file_download_root_uses_absolute_tree_path_without_false_cleanup_failure(self):
        storage = FakeStorage([])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/English.Series.S01E01.mkv",
            download_root="/Downloads/English.Series.S01E01.mkv",
            resource_name="English.Series.S01E01.mkv",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            file_tree=[{
                "name": "English.Series.S01E01.mkv",
                "relative_path": "English.Series.S01E01.mkv",
                "path": "/Downloads/English.Series.S01E01.mkv",
                "is_dir": False,
                "size": 1000,
            }],
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("✅"))
        self.assertNotIn(
            "/Downloads/English.Series.S01E01.mkv",
            storage.deleted,
        )


class FakeCore:
    def __init__(self, storage=None):
        self.storage = storage or FakeStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1},
        ])
        self.events = []
        self.notifications = []
        self.reports = []
        self.fail_notification = False

    async def call_capability(self, capability, method, payload, **_kwargs):
        self.assert_capability = capability
        if capability == "media.search":
            self.metadata_query = payload["query"]
            return {
                "media_metadata": movie_contract(),
                "naming_metadata": {
                    "source": "media-search",
                    "media_type": "movie",
                    "chinese_title": "中文电影",
                    "english_title": "English Movie",
                    "year": "2024",
                },
            }
        value = getattr(self.storage, method)(*(payload.get("args") or []), **(payload.get("kwargs") or {}))
        return {"value": value}

    async def publish_event(self, event_type, payload, **kwargs):
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "organized-1"}

    async def notify_user(self, user_id, text, **kwargs):
        if self.fail_notification:
            raise RuntimeError("notification unavailable")
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}

    async def report_operation(self, operation):
        self.reports.append(operation)
        return {"accepted": True, "revision": operation["revision"]}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        task = asyncio.create_task(awaitable, name=task_id)
        self.tasks[task_id] = task
        return task

    async def wait(self):
        tasks = list(self.tasks.values())
        self.tasks.clear()
        if tasks:
            await asyncio.gather(*tasks)


class RenamingFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def test_rollback_is_reported_and_compensation_runs_once(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingJournal:
            can_rollback = True
            inverses = [SimpleNamespace(target_path="/Downloads/renamed.mkv")]
            calls = 0

            async def rollback(self, _core, *, deadline):
                self.calls += 1
                entered.set()
                await release.wait()
                return {
                    "state": "rolled_back",
                    "restored": ["/Downloads/original.mkv"],
                    "remaining": [],
                }

        core = FakeCore()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        journal = BlockingJournal()
        feature.operations["op-rollback"] = {
            "operation_id": "op-rollback",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "renaming",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": journal,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        first = await feature.operation_control({
            "operation_id": "op-rollback",
            "action": "rollback",
            "revision": 3,
        })
        await entered.wait()
        repeated = await feature.operation_control({
            "operation_id": "op-rollback",
            "action": "rollback",
            "revision": 4,
        })
        release.set()
        await runtime.wait()

        self.assertEqual(repeated["operation"]["state"], "rolling_back")
        self.assertEqual(first["operation"]["state"], "rolling_back")
        self.assertEqual(
            feature.operations["op-rollback"]["state"], "rolled_back"
        )
        self.assertEqual(journal.calls, 1)
        self.assertEqual(
            [report["state"] for report in core.reports],
            ["rolling_back", "rolled_back"],
        )

    async def test_rollback_waits_for_forward_task_safe_stop(self):
        forward_release = asyncio.Event()
        rollback_started = asyncio.Event()

        class Journal:
            can_rollback = True
            inverses = []

            async def rollback(self, _core, *, deadline):
                rollback_started.set()
                return {"state": "rolled_back", "restored": [], "remaining": []}

        async def forward():
            await forward_release.wait()

        core = FakeCore()
        runtime = FakeRuntime()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        feature.bind_runtime(runtime)
        forward_task = asyncio.create_task(forward())
        feature.operations["op-forward-stop"] = {
            "operation_id": "op-forward-stop",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "renaming",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": Journal(),
            "task": forward_task,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        accepted = await feature.operation_control({
            "operation_id": "op-forward-stop",
            "action": "rollback",
            "revision": 3,
        })
        await asyncio.sleep(0)

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertFalse(rollback_started.is_set())
        forward_release.set()
        await runtime.wait()
        self.assertTrue(rollback_started.is_set())
        self.assertEqual(feature.operations["op-forward-stop"]["state"], "rolled_back")

    async def test_runtime_shutdown_does_not_start_pending_compensation(self):
        forward_release = asyncio.Event()

        class Journal:
            can_rollback = True
            inverses = []
            calls = 0

            async def rollback(self, _core, *, deadline):
                self.calls += 1
                return {"state": "rolled_back", "restored": [], "remaining": []}

        async def forward():
            await forward_release.wait()

        core = FakeCore()
        runtime = FakeRuntime()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        feature.bind_runtime(runtime)
        journal = Journal()
        forward_task = asyncio.create_task(forward())
        feature.operations["op-shutdown"] = {
            "operation_id": "op-shutdown",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "renaming",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": journal,
            "task": forward_task,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        await feature.operation_control({
            "operation_id": "op-shutdown",
            "action": "rollback",
            "revision": 3,
        })
        rollback_task = runtime.tasks.pop("renaming-rollback-op-shutdown")
        rollback_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await rollback_task

        self.assertEqual(journal.calls, 0)
        forward_release.set()
        await forward_task

    async def test_download_event_accepts_handoff_and_runs_in_background(self):
        core = FakeCore()
        runtime = FakeRuntime()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        feature.bind_runtime(runtime)

        accepted = await feature.download_completed({
            "event_id": "event-operation",
            "payload": {
                "job_id": "job-operation",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-chain",
                "operation_revision": 8,
            },
        })

        self.assertEqual(accepted["operation"]["state"], "running")
        self.assertEqual(core.storage.moved, [])
        await runtime.wait()

        self.assertEqual(core.reports[0]["operation_id"], "op-chain")
        self.assertEqual(core.reports[0]["revision"], 9)
        stages = {item["stage"] for item in core.reports}
        self.assertTrue({
            "organizing", "conflict_validation", "directory_preparation",
            "renaming", "moving", "cleanup",
        }.issubset(stages))
        self.assertEqual(core.reports[-1]["state"], "handed_off")
        self.assertEqual(core.reports[-1]["next_plugin_id"], "plex-management")
        self.assertEqual(core.events[0][1]["operation_id"], "op-chain")
        self.assertEqual(
            core.events[0][1]["operation_revision"],
            core.reports[-1]["revision"],
        )

        cancelled = await feature.operation_control({
            "operation_id": "op-chain",
            "action": "cancel",
            "revision": core.reports[-1]["revision"],
        })
        self.assertEqual(cancelled["operation"]["state"], "cancelled")
        self.assertIn("后续 Plex", cancelled["operation"]["status_text"])

    async def test_cancel_during_metadata_stops_later_pipeline(self):
        entered = asyncio.Event()

        class BlockingCore(FakeCore):
            async def call_capability(self, capability, method, payload, **kwargs):
                if capability == "media.search":
                    entered.set()
                    await asyncio.Event().wait()
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        core = BlockingCore()
        runtime = FakeRuntime()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        feature.bind_runtime(runtime)
        accepted = await feature.download_completed({
            "event_id": "event-cancel",
            "payload": {
                "job_id": "job-cancel", "selected_path": "/Movies",
                "user_id": 123, "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Unknown.Release",
                "operation_id": "op-cancel", "operation_revision": 2,
            },
        })
        await entered.wait()

        result = await feature.operation_control({
            "operation_id": "op-cancel",
            "action": "cancel",
            "revision": accepted["operation"]["revision"],
        })
        await runtime.wait()

        self.assertEqual(result["operation"]["state"], "cancelling")
        self.assertEqual(core.reports[-1]["state"], "cancelled")
        self.assertEqual(core.storage.moved, [])
        self.assertEqual(core.events, [])

    async def test_direct_magnet_requeries_media_search_with_release_and_file_tree(self):
        core = FakeCore()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_completed({
            "event_id": "event-direct",
            "payload": {
                "job_id": "job-direct", "selected_path": "/Movies",
                "user_id": 123,
                "download_root": "/Downloads/Movie.2024.mkv",
                "final_path": "/Downloads/Movie.2024.mkv",
                "resource_name": "Movie.2024.mkv",
                "release": {"title": "Movie.2024.1080p.WEB-DL"},
                "file_tree": [{
                    "name": "Movie.2024.mkv",
                    "relative_path": "Movie.2024.mkv",
                    "path": "/Downloads/Movie.2024.mkv",
                    "is_dir": False,
                    "size": 1000,
                }],
            },
        })
        await runtime.wait()

        self.assertIn("Movie.2024.1080p.WEB-DL", core.metadata_query)
        self.assertIn("Movie.2024.mkv", core.metadata_query)
        self.assertEqual(
            core.storage.renamed[0][0],
            "/Downloads/Movie.2024.mkv",
        )

    async def test_download_event_calls_storage_rpc_and_publishes_media_organized(self):
        core = FakeCore()
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_completed({
            "event_id": "event-1",
            "payload": {
                "job_id": "job-1",
                "link": "magnet:?x",
                "selected_path": "/Movies",
                "user_id": 123,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "provider": "open115",
                "media_metadata": movie_contract(),
            },
        })
        await runtime.wait()

        self.assertEqual(core.assert_capability, "storage.provider")
        self.assertEqual(core.events[0][0], "media.organized")
        self.assertEqual(core.events[0][1]["job_id"], "job-1")
        self.assertEqual(core.events[0][1]["final_path"], "/Movies/中文电影 (English Movie)")
        self.assertIn("整理完成", core.notifications[0][1])

    async def test_incomplete_cleanup_notifies_without_publishing_organized(self):
        core = FakeCore(CleanupFailureStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1000},
        ]))
        feature = RenamingFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            core=core,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-cleanup-failed",
            "payload": {
                "job_id": "job-cleanup-failed", "selected_path": "/Movies",
                "user_id": 123, "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024", "media_metadata": movie_contract(),
            },
        })
        await runtime.wait()

        self.assertEqual(core.events, [])
        self.assertIn("源目录清理未完成", core.notifications[0][1])

    async def test_delivery_replay_does_not_repeat_destructive_storage_operations(self):
        from telepiplex_renaming.jobs import RenamingJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            core = FakeCore()
            feature = RenamingFeature(
                config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
                core=core, jobs=RenamingJobStore(Path(tmpdir) / "jobs.db"),
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            request = {"event_id": "event-replay", "payload": {
                "job_id": "job-replay", "selected_path": "/Movies", "user_id": 123,
                "final_path": "/Downloads/Release", "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
            }}
            core.fail_notification = True
            with self.assertRaises(RuntimeError):
                await feature.download_completed(request)
                await runtime.wait()
            moved_count = len(core.storage.moved)
            core.fail_notification = False

            replay = await feature.download_completed(request)

            self.assertEqual(len(core.storage.moved), moved_count)
            self.assertTrue(replay["organized"])


class FeatureSourceContractTest(unittest.TestCase):
    def test_readme_build_example_uses_current_version(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("dist/renaming-1.1.0.tpx", source)
        self.assertNotIn("dist/renaming-1.0.0.tpx", source)

    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                forbidden.extend(name for name in names if name.split(".", 1)[0] in {"app", "init", "telegram"})
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()

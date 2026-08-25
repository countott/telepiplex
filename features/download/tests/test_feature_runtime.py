import asyncio
import ast
import copy
import re
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class Open115ClientCacheTest(unittest.TestCase):
    def test_successful_copy_invalidates_file_info_cache(self):
        from telepiplex_download.client import Open115Client

        class SuccessResponse:
            status_code = 200
            headers = {}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"state": True, "code": 0}

        class SuccessSession:
            def __init__(self):
                self.requests = []

            def request(self, method, url, **kwargs):
                self.requests.append((method, url, kwargs.get("data")))
                return SuccessResponse()

        session = SuccessSession()
        client = Open115Client({"access_token": "test"}, session=session)
        client._file_cache = {
            "/source.mkv": {"file_id": "source"},
            "/target": {"file_id": "target"},
            "/target/source.mkv": None,
        }

        copied = client.copy_file("/source.mkv", "/target")

        self.assertTrue(copied)
        self.assertEqual(client._file_cache, {})
        self.assertEqual(len(session.requests), 1)
        self.assertTrue(session.requests[0][1].endswith("/open/ufile/copy"))


class _PollingClock:
    def __init__(self):
        self.value = 0.0
        self.delays = []

    def monotonic(self):
        return self.value


class _AdvancingCancelEvent:
    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, delay):
        delay = float(delay)
        self.clock.delays.append(delay)
        self.clock.value += delay
        return False


class Open115AdaptivePollingTest(unittest.TestCase):
    @staticmethod
    def _task(*, info_hash="a" * 40, name="Show", status=1, progress=10, **extra):
        return {
            "url": "magnet:?xt=urn:btih:" + "a" * 40,
            "info_hash": info_hash,
            "name": name,
            "status": status,
            "percentDone": progress,
            **extra,
        }

    def test_unchanged_business_tuple_backs_off_and_ignores_volatile_fields(self):
        from telepiplex_download.client import Open115Client

        client = Open115Client({})
        snapshots = iter([
            [self._task(provider_timestamp=index, unrelated={"revision": index})]
            for index in range(4)
        ] + [[self._task(status=2, progress=100, provider_timestamp=5)]])
        client.get_offline_tasks = lambda: next(snapshots)
        clock = _PollingClock()
        cancel_event = _AdvancingCancelEvent(clock)

        with patch(
            "telepiplex_download.client.time.monotonic",
            side_effect=clock.monotonic,
        ):
            completed = client.wait_for_download(
                "magnet:?xt=urn:btih:" + "a" * 40,
                timeout=100,
                poll_initial_interval=2,
                poll_max_interval=30,
                poll_backoff_factor=1.7,
                cancel_event=cancel_event,
            )

        self.assertEqual(completed["resource_name"], "Show")
        self.assertEqual(len(clock.delays), 4)
        for actual, expected in zip(clock.delays, [2.0, 3.4, 5.78, 9.826]):
            self.assertAlmostEqual(actual, expected)

    def test_only_hash_name_status_progress_and_presence_reset_backoff(self):
        from telepiplex_download.client import Open115Client

        client = Open115Client({})
        same = self._task()
        changed_hash = self._task(info_hash="b" * 40)
        changed_name = self._task(info_hash="b" * 40, name="Show Renamed")
        changed_status = self._task(
            info_hash="b" * 40,
            name="Show Renamed",
            status=0,
        )
        changed_progress = self._task(
            info_hash="b" * 40,
            name="Show Renamed",
            status=0,
            progress=20,
        )
        snapshots = iter([
            [],
            [],
            [same],
            [same],
            [changed_hash],
            [changed_hash],
            [changed_name],
            [changed_name],
            [changed_status],
            [changed_status],
            [changed_progress],
            [],
            [],
            [self._task(status=2, progress=100)],
        ])
        client.get_offline_tasks = lambda: next(snapshots)
        clock = _PollingClock()
        cancel_event = _AdvancingCancelEvent(clock)

        with patch(
            "telepiplex_download.client.time.monotonic",
            side_effect=clock.monotonic,
        ):
            client.wait_for_download(
                "magnet:?xt=urn:btih:" + "a" * 40,
                timeout=200,
                poll_initial_interval=2,
                poll_max_interval=30,
                poll_backoff_factor=1.7,
                cancel_event=cancel_event,
            )

        self.assertEqual(clock.delays, [
            2.0,
            3.4,
            2.0,
            3.4,
            2.0,
            3.4,
            2.0,
            3.4,
            2.0,
            3.4,
            2.0,
            2.0,
            3.4,
        ])

    def test_completion_on_current_poll_has_no_trailing_wait(self):
        from telepiplex_download.client import Open115Client

        client = Open115Client({})
        client.get_offline_tasks = lambda: [self._task(status=2, progress=100)]
        clock = _PollingClock()
        cancel_event = _AdvancingCancelEvent(clock)

        completed = client.wait_for_download(
            "magnet:?xt=urn:btih:" + "a" * 40,
            timeout=100,
            poll_initial_interval=2,
            poll_max_interval=30,
            poll_backoff_factor=1.7,
            cancel_event=cancel_event,
        )

        self.assertEqual(completed["progress"], 100)
        self.assertEqual(clock.delays, [])

    def test_unchanged_30_minute_task_uses_exactly_64_list_calls(self):
        from telepiplex_download.client import Open115Client, Open115Error

        client = Open115Client({})
        calls = 0

        def tasks():
            nonlocal calls
            calls += 1
            return [self._task()]

        client.get_offline_tasks = tasks
        clock = _PollingClock()
        cancel_event = _AdvancingCancelEvent(clock)

        with patch(
            "telepiplex_download.client.time.monotonic",
            side_effect=clock.monotonic,
        ), self.assertRaisesRegex(Open115Error, "timed out"):
            client.wait_for_download(
                "magnet:?xt=urn:btih:" + "a" * 40,
                timeout=1800,
                poll_initial_interval=2,
                poll_max_interval=30,
                poll_backoff_factor=1.7,
                cancel_event=cancel_event,
            )

        self.assertEqual(calls, 64)
        self.assertLess(calls, 90)
        self.assertEqual(clock.value, 1800)
        self.assertLessEqual(max(clock.delays), 30)

    def test_real_cancel_event_interrupts_a_thirty_second_wait(self):
        from telepiplex_download.client import Open115Client, Open115Error

        client = Open115Client({})
        client.get_offline_tasks = lambda: [self._task()]
        cancel_event = threading.Event()
        setter = threading.Timer(0.03, cancel_event.set)
        setter.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(Open115Error, "cancelled"):
                client.wait_for_download(
                    "magnet:?xt=urn:btih:" + "a" * 40,
                    timeout=120,
                    poll_initial_interval=30,
                    poll_max_interval=30,
                    poll_backoff_factor=1,
                    cancel_event=cancel_event,
                )
        finally:
            setter.cancel()

        self.assertLess(time.monotonic() - started, 0.5)


class FakeHost:
    def __init__(self):
        self.events = []
        self.notifications = []
        self.fail_publish = False
        self.reports = []
        self.milestones = []
        self.timeline = []

    async def publish_event(self, event_type, payload, **kwargs):
        if self.fail_publish:
            raise RuntimeError("host unavailable")
        self.timeline.append(("event", event_type))
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "event-1"}

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}

    async def report_operation(self, report, **kwargs):
        self.timeline.append(("report", report["state"], report["stage"]))
        self.reports.append(dict(report))
        return {
            "accepted": True,
            "operation_id": report["operation_id"],
            "state": report["state"],
            "revision": report["revision"],
        }

    async def seal_operation_stage(
        self,
        operation_id,
        milestone_id,
        text,
        *,
        deadline=10,
    ):
        self.timeline.append(("milestone", "stage", milestone_id))
        self.milestones.append({
            "operation_id": operation_id,
            "milestone_id": milestone_id,
            "text": text,
            "deadline": deadline,
        })
        return {"accepted": True, "duplicate": False}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable


class FakeClient:
    def __init__(self):
        self.renamed = []
        self.moved = []
        self.deleted_tasks = []
        self.deleted_files = []
        self.added = []
        self.tokens = ("", "")
        self.wait_kwargs = None

    def add_offline_task(self, link, selected_path):
        self.added.append((link, selected_path))
        return True

    def wait_for_download(self, link, **kwargs):
        self.wait_kwargs = dict(kwargs)
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback({
                "resource_name": "Show.S01E01.mkv",
                "info_hash": "hash-1",
                "progress": 50,
            })
        return {
            "resource_name": "Show.S01E01.mkv",
            "info_hash": "hash-1",
            "progress": 100,
        }

    def is_directory(self, path):
        return False

    def create_dir_recursive(self, path):
        return {"file_id": "dir-1"}

    def move_file(self, source, target):
        self.moved.append((source, target))
        return True

    def rename(self, source, leaf):
        self.renamed.append((source, leaf))
        return True

    def del_offline_task(self, info_hash, del_source_file=0):
        self.deleted_tasks.append((info_hash, del_source_file))
        return True

    def get_file_info(self, path):
        return {"path": path, "file_id": "1"}

    def get_file_tree(self, path):
        return [{
            "name": "Show.S01E01.mkv",
            "relative_path": "Show.S01E01.mkv",
            "path": path,
            "is_dir": False,
            "file_id": "1",
            "size": 1024,
        }]

    def set_tokens(self, access_token, refresh_token):
        self.tokens = (access_token, refresh_token)

    def create_device_authorization(self, app_id):
        return {
            "uid": "device-1",
            "qrcode": "https://115.com/scan/device-1",
            "code_verifier": "verifier",
            "time": 1,
            "sign": "sign",
        }

    def complete_device_authorization(self, authorization, **kwargs):
        cancel_event = kwargs.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("authorization cancelled")
        return {"access_token": "scan-access", "refresh_token": "scan-refresh"}


class FakeConfigStore:
    def __init__(self, config):
        self.config = dict(config)
        self.writes = []
        self.directory_writes = []
        self.fail_writes = False
        self.fail_directory_writes = False

    def read(self):
        return dict(self.config)

    def snapshot(self):
        return {"exists": True, "config": dict(self.config)}

    def restore(self, snapshot):
        self.config = dict(snapshot["config"])
        return dict(self.config)

    def write_tokens(self, access_token, refresh_token, *, auth_mode):
        if self.fail_writes:
            raise RuntimeError("token=secret-value")
        self.config.update({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "auth_mode": auth_mode,
        })
        self.writes.append((access_token, refresh_token, auth_mode))
        return dict(self.config)

    def write_save_directories(self, directories):
        from telepiplex_download.directories import normalize_save_directories

        if self.fail_directory_writes:
            raise RuntimeError("config=secret-value")
        normalized = normalize_save_directories(directories)
        self.config["save_directories"] = normalized
        self.directory_writes.append(normalized)
        return dict(self.config)


class DownloadFeatureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from telepiplex_download.service import DownloadFeature

        self.host = FakeHost()
        self.runtime = FakeRuntime()
        self.client = FakeClient()
        self.feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            config_store=FakeConfigStore({}),
        )
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for handle in getattr(self.feature, "session_expiry_handles", {}).values():
            handle.cancel()
        for task in self.runtime.tasks.values():
            if asyncio.iscoroutine(task):
                task.close()

    async def _open_directory_config(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        return await self.feature.callback({
            "payload": "config:directories", "user_id": 1, "chat_id": 10,
        })

    async def test_submit_returns_job_and_background_publishes_completion(self):
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "a" * 40,
                "selected_path": "/Downloads",
                "user_id": 123,
                "target_folder_name": "中文名 (English)",
                "media_metadata": {"schema_version": 1, "metadata_id": "m1"},
                "naming_metadata": {
                    "source": "search-live",
                    "chinese_title": "中文名",
                    "english_title": "English",
                },
                "release": {"title": "Show.S01E01.1080p", "indexer": "Prowlarr"},
            },
            "context": {"idempotency_key": "plan-1"},
        })

        self.assertTrue(result["accepted"])
        self.assertEqual(result["job_id"], "plan-1")
        await self.runtime.tasks.pop("plan-1")
        event_type, payload, kwargs = self.host.events[0]
        self.assertEqual(event_type, "download.completed")
        self.assertEqual(payload["job_id"], "plan-1")
        self.assertEqual(payload["download_root"], "/Downloads/Show.S01E01.mkv")
        self.assertEqual(payload["final_path"], payload["download_root"])
        self.assertEqual(payload["resource_name"], "Show.S01E01.mkv")
        self.assertEqual(payload["file_tree"][0]["path"], payload["download_root"])
        self.assertEqual(payload["release"]["title"], "Show.S01E01.1080p")
        self.assertEqual(payload["media_metadata"]["metadata_id"], "m1")
        self.assertEqual(payload["naming_metadata"]["source"], "search-live")
        self.assertEqual(payload["naming_metadata"]["chinese_title"], "中文名")
        self.assertEqual(kwargs["idempotency_key"], "plan-1:completed")
        self.assertEqual(self.client.renamed, [])
        self.assertEqual(self.client.moved, [])
        self.assertEqual(self.client.deleted_tasks, [("hash-1", 0)])

    async def test_service_wires_new_adaptive_polling_config(self):
        self.feature.config.update({
            "poll_initial_interval": 1.25,
            "poll_max_interval": 22,
            "poll_backoff_factor": 1.4,
            "poll_interval": 99,
        })

        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "d" * 40,
                "selected_path": "/Downloads",
                "user_id": 123,
            },
            "context": {"idempotency_key": "adaptive-config"},
        })
        await self.runtime.tasks.pop("adaptive-config")

        self.assertEqual(
            {
                key: self.client.wait_kwargs[key]
                for key in (
                    "poll_initial_interval",
                    "poll_max_interval",
                    "poll_backoff_factor",
                )
            },
            {
                "poll_initial_interval": 1.25,
                "poll_max_interval": 22,
                "poll_backoff_factor": 1.4,
            },
        )
        self.assertNotIn("poll_interval", self.client.wait_kwargs)

    async def test_service_uses_legacy_poll_interval_only_as_initial_fallback(self):
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "e" * 40,
                "selected_path": "/Downloads",
                "user_id": 123,
            },
            "context": {"idempotency_key": "legacy-poll-config"},
        })
        await self.runtime.tasks.pop("legacy-poll-config")

        self.assertEqual(self.client.wait_kwargs["poll_initial_interval"], 0.01)
        self.assertEqual(self.client.wait_kwargs["poll_max_interval"], 30)
        self.assertEqual(self.client.wait_kwargs["poll_backoff_factor"], 1.7)
        self.assertNotIn("poll_interval", self.client.wait_kwargs)

    async def test_completion_event_preserves_subtitles_in_full_file_tree(self):
        def file_tree(path):
            return [{
                "name": "Show.S01E01.mkv",
                "relative_path": "Show.S01E01.mkv",
                "path": path,
                "is_dir": False,
                "file_id": "video-1",
                "size": 1024,
            }, {
                "name": "subs/Show.S01E01.ass",
                "relative_path": "subs/Show.S01E01.ass",
                "path": f"{path}/subs/Show.S01E01.ass",
                "is_dir": False,
                "file_id": "subtitle-1",
                "size": 256,
            }, {
                "name": "subs/Show.S01E01.ENG.forced.srt",
                "relative_path": "subs/Show.S01E01.ENG.forced.srt",
                "path": f"{path}/subs/Show.S01E01.ENG.forced.srt",
                "is_dir": False,
                "file_id": "subtitle-2",
                "size": 256,
            }]

        self.client.get_file_tree = file_tree
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "b" * 40,
                "selected_path": "/Downloads",
                "user_id": 123,
            },
            "context": {"idempotency_key": "plan-with-subtitle"},
        })

        await self.runtime.tasks.pop("plan-with-subtitle")

        _event_type, payload, _kwargs = self.host.events[0]
        self.assertEqual(
            [item["name"] for item in payload["file_tree"]],
            [
                "Show.S01E01.mkv",
                "subs/Show.S01E01.ass",
                "subs/Show.S01E01.ENG.forced.srt",
            ],
        )
        self.assertEqual(
            payload["file_tree"][1]["file_id"],
            "subtitle-1",
        )

    def test_open115_error_preserves_provider_context(self):
        from telepiplex_download.client import Open115Error

        error = Open115Error(
            "登录状态已失效",
            code="40140125",
            operation="add_offline_task",
        )

        self.assertEqual(error.code, "40140125")
        self.assertEqual(error.operation, "add_offline_task")
        self.assertEqual(str(error), "登录状态已失效")

    def test_open115_failure_classifier_covers_actionable_categories(self):
        from telepiplex_download.client import Open115Error
        from telepiplex_download.failure import classify_download_failure

        cases = (
            (
                Open115Error(
                    "cannot create 115 directory: /Downloads",
                    operation="create_directory",
                ),
                "open115_directory_failed",
                "/config",
            ),
            (
                Open115Error(
                    "该任务已存在",
                    code="10008",
                    operation="add_offline_task",
                ),
                "open115_submit_rejected",
                "更换候选",
            ),
            (
                Open115Error(
                    "115 request failed: ReadTimeout",
                    operation="/open/offline/get_task_list",
                ),
                "open115_request_failed",
                "网络",
            ),
            (
                OSError("unexpected local failure"),
                "download_failed",
                "Download 日志",
            ),
        )

        for error, expected_code, remedy_fragment in cases:
            with self.subTest(expected_code=expected_code):
                failure = classify_download_failure(
                    error,
                    stage="preparing_submission",
                )
                self.assertEqual(failure.code, expected_code)
                self.assertIn(remedy_fragment, failure.remedy)
                self.assertIn(str(error), failure.detail)

    def test_offline_submit_rejection_preserves_provider_response(self):
        from telepiplex_download.client import Open115Client, Open115Error

        client = Open115Client({})
        client.create_dir_recursive = lambda _path: {"file_id": "folder-1"}
        client._request = lambda *_args, **_kwargs: {
            "state": False,
            "code": 10008,
            "message": "该任务已存在",
        }

        with self.assertRaises(Open115Error) as raised:
            client.add_offline_task(
                "magnet:?xt=urn:btih:" + "b" * 40,
                "/Downloads",
            )

        self.assertEqual(str(raised.exception), "该任务已存在")
        self.assertEqual(raised.exception.code, "10008")
        self.assertEqual(
            raised.exception.operation,
            "add_offline_task",
        )

    def test_existing_offline_task_matches_only_the_magnet_task_identity(self):
        from telepiplex_download.client import Open115Client

        requested_hash = "b" * 40
        client = Open115Client({})
        client.get_offline_tasks = lambda: [
            {
                "url": "magnet:?xt=urn:btih:" + "c" * 40,
                "info_hash": "c" * 40,
                "name": "renamed-to-look-like-requested-task",
                "file_id": "moved-file-1",
                "status": 1,
                "percentDone": 25,
            },
            {
                "url": (
                    "magnet:?xt=urn:btih:"
                    + requested_hash.upper()
                    + "&dn=Original.Name"
                ),
                "info_hash": requested_hash.upper(),
                "name": "Original.Name",
                "file_id": "task-file-1",
                "status": 1,
                "percentDone": 50,
            },
        ]

        matched = client.find_offline_task(
            "magnet:?xt=urn:btih:" + requested_hash + "&dn=New.Name"
        )

        self.assertEqual(matched["info_hash"], requested_hash.upper())
        self.assertEqual(matched["file_id"], "task-file-1")

    def test_existing_failed_task_is_polled_by_bound_identity_until_retry_succeeds(self):
        from telepiplex_download.client import Open115Client

        info_hash = "d" * 40
        link = "magnet:?xt=urn:btih:" + info_hash
        client = Open115Client({})
        snapshots = iter([
            [{
                "url": link,
                "info_hash": info_hash,
                "name": "Retry.Show",
                "status": -1,
                "percentDone": 12,
            }],
            [{
                "url": link,
                "info_hash": info_hash,
                "name": "Retry.Show",
                "status": 1,
                "percentDone": 40,
            }],
            [{
                "url": link,
                "info_hash": info_hash,
                "name": "Retry.Show",
                "status": 2,
                "percentDone": 100,
            }],
        ])
        client.get_offline_tasks = lambda: next(snapshots)
        progress = []

        completed = client.wait_for_download(
            link,
            existing_task={
                "url": link,
                "info_hash": info_hash,
                "status": -1,
                "percentDone": 12,
            },
            timeout=1,
            poll_interval=0.01,
            progress_callback=progress.append,
        )

        self.assertEqual(completed["info_hash"], info_hash)
        self.assertEqual(completed["resource_name"], "Retry.Show")
        self.assertEqual(
            [item["task_status"] for item in progress],
            [-1, 1, 2],
        )

    def test_completed_existing_task_is_not_reattached_after_output_was_moved(self):
        from telepiplex_download.client import Open115Client

        info_hash = "f" * 40
        link = "magnet:?xt=urn:btih:" + info_hash
        client = Open115Client({})
        client.get_offline_tasks = lambda: [{
            "url": link,
            "info_hash": info_hash,
            "name": "Already.Moved.Show",
            "status": 2,
            "percentDone": 100,
        }]
        client.get_file_info = lambda _path: None

        matched = client.find_offline_task(link, "/Downloads")

        self.assertIsNone(matched)

    async def test_10008_reattaches_running_failed_and_completed_tasks(self):
        from telepiplex_download.client import Open115Error
        from telepiplex_download.service import DownloadFeature

        cases = (
            (1, 45, "下载中"),
            (-1, 12, "失败，等待 115 重试"),
            (2, 100, "已完成"),
        )
        for status, percent, status_text in cases:
            with self.subTest(status=status):
                link = "magnet:?xt=urn:btih:" + str(abs(status) + 4) * 40
                existing_task = {
                    "url": link,
                    "info_hash": str(abs(status) + 4) * 40,
                    "name": "Existing.Show",
                    "status": status,
                    "percentDone": percent,
                    "wp_path_id": "folder-1",
                }

                class ExistingTaskClient(FakeClient):
                    def __init__(self):
                        super().__init__()
                        self.wait_existing_task = None

                    def add_offline_task(self, _link, _selected_path):
                        raise Open115Error(
                            "该任务已存在",
                            code="10008",
                            operation="add_offline_task",
                        )

                    def find_offline_task(self, _link, _selected_path=""):
                        return dict(existing_task)

                    def wait_for_download(self, _link, **kwargs):
                        self.wait_existing_task = kwargs.get("existing_task")
                        kwargs["progress_callback"]({
                            "resource_name": "Existing.Show",
                            "info_hash": existing_task["info_hash"],
                            "progress": 100,
                            "task_status": 2,
                        })
                        return {
                            "resource_name": "Existing.Show",
                            "info_hash": existing_task["info_hash"],
                            "progress": 100,
                        }

                host = FakeHost()
                runtime = FakeRuntime()
                client = ExistingTaskClient()
                feature = DownloadFeature(
                    config={"download_timeout": 30, "poll_interval": 0.01},
                    host=host,
                    client=client,
                )
                feature.bind_runtime(runtime)

                accepted = await feature.download_capability({
                    "method": "submit",
                    "payload": {
                        "link": link,
                        "selected_path": "/Downloads",
                        "operation_id": f"op-existing-{status}",
                        "chat_id": 10,
                        "user_id": 1,
                    },
                    "context": {"idempotency_key": f"existing-{status}"},
                })
                await runtime.tasks.pop(accepted["job_id"])

                reattached = [
                    report for report in host.reports
                    if "已接入现有下载" in report["status_text"]
                ]
                self.assertEqual(len(reattached), 1)
                self.assertIn(f"{percent:.1f}%", reattached[0]["status_text"])
                self.assertTrue(reattached[0]["details"]["reattached"])
                self.assertEqual(
                    reattached[0]["details"]["provider_status"],
                    status,
                )
                self.assertEqual(host.notifications, [])
                self.assertEqual(client.wait_existing_task, existing_task)
                self.assertEqual(host.events[0][0], "download.completed")
                self.assertEqual(
                    host.events[0][1]["download_root"],
                    "/Downloads/Existing.Show",
                )
                self.assertFalse(any(
                    event_type == "download.failed"
                    for event_type, _payload, _kwargs in host.events
                ))

    async def test_10008_without_matching_external_task_keeps_stable_failure(self):
        from telepiplex_download.client import Open115Error

        class MissingTaskClient(FakeClient):
            def add_offline_task(self, _link, _selected_path):
                raise Open115Error(
                    "该任务已存在",
                    code="10008",
                    operation="add_offline_task",
                )

            def find_offline_task(self, _link, _selected_path=""):
                return None

        self.feature.client = MissingTaskClient()
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "e" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-existing-missing",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "existing-missing"},
        })

        await self.runtime.tasks.pop(result["job_id"])

        event_type, payload, _kwargs = self.host.events[-1]
        self.assertEqual(event_type, "download.failed")
        self.assertEqual(payload["error_code"], "open115_submit_rejected")
        self.assertEqual(payload["provider_code"], "10008")
        self.assertEqual(
            self.host.reports[-1]["details"]["error_code"],
            "open115_submit_rejected",
        )

    async def test_different_download_is_rejected_while_one_task_is_active(self):
        class BlockingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.wait_started = threading.Event()

            def wait_for_download(self, _link, **kwargs):
                self.wait_started.set()
                kwargs["cancel_event"].wait(1)
                raise RuntimeError("cancelled")

        client = BlockingClient()
        self.feature.client = client
        first = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "1" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-active-one",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "active-one"},
        })
        running = asyncio.create_task(self.runtime.tasks.pop(first["job_id"]))
        self.assertTrue(await asyncio.to_thread(client.wait_started.wait, 1))

        with self.assertRaisesRegex(Exception, "active"):
            await self.feature.download_capability({
                "method": "submit",
                "payload": {
                    "link": "magnet:?xt=urn:btih:" + "2" * 40,
                    "selected_path": "/Downloads",
                    "operation_id": "op-active-two",
                    "chat_id": 10,
                    "user_id": 1,
                },
                "context": {"idempotency_key": "active-two"},
            })

        await self.feature.operation_control({
            "operation_id": "op-active-one",
            "action": "cancel",
            "revision": self.host.reports[-1]["revision"],
        })
        await running
        self.assertNotIn("active-two", self.runtime.tasks)

    async def test_open115_auth_failure_is_actionable_on_every_failure_surface(self):
        from telepiplex_download.client import Open115Error
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        class ExpiredAuthClient(FakeClient):
            def add_offline_task(self, link, selected_path):
                raise Open115Error(
                    "登录状态已失效 access_token=secret-value",
                    code="40140125",
                    operation="add_offline_task",
                )

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=ExpiredAuthClient(),
            jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        with self.assertLogs("telepiplex.download", level="ERROR") as captured:
            result = await feature.download_capability({
                "method": "submit",
                "payload": {
                    "link": "magnet:?xt=urn:btih:" + "a" * 40,
                    "selected_path": "/Downloads",
                    "operation_id": "op-auth-expired",
                    "chat_id": 10,
                    "user_id": 1,
                },
                "context": {"idempotency_key": "auth-expired"},
            })
            await runtime.tasks.pop(result["job_id"])

        job = jobs.get("auth-expired")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"], "open115_auth_failed")

        event_type, failure, kwargs = self.host.events[-1]
        self.assertEqual(event_type, "download.failed")
        self.assertEqual(failure["error"], "open115_auth_failed")
        self.assertEqual(failure["error_code"], "open115_auth_failed")
        self.assertEqual(failure["provider_code"], "40140125")
        self.assertEqual(failure["provider_operation"], "add_offline_task")
        self.assertEqual(failure["stage"], "preparing_submission")
        self.assertIn("登录状态已失效", failure["error_message"])
        self.assertIn("/auth", failure["remedy"])
        self.assertEqual(kwargs["idempotency_key"], "auth-expired:failed")

        self.assertEqual(self.host.notifications, [])

        report = self.host.reports[-1]
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["stage"], "preparing_submission")
        self.assertIn("115 授权已失效", report["status_text"])
        self.assertIn("/auth", report["status_text"])
        self.assertEqual(
            report["details"]["error_code"],
            "open115_auth_failed",
        )
        self.assertEqual(report["details"]["provider_code"], "40140125")
        self.assertEqual(
            report["details"]["provider_operation"],
            "add_offline_task",
        )

        output = "\n".join(captured.output)
        self.assertIn("error_code=open115_auth_failed", output)
        self.assertIn("detail=登录状态已失效", output)
        self.assertIn("provider_code=40140125", output)
        self.assertIn("operation=add_offline_task", output)
        for surface in (
            failure["error_message"],
            failure["remedy"],
            report["status_text"],
            str(report["details"]),
            output,
        ):
            self.assertNotIn("secret-value", surface)

    async def test_download_reports_stages_and_hands_same_operation_to_rename(self):
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "1" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-download-1",
                "operation_revision": 0,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "download-operation-1"},
        })

        self.assertEqual(result["operation_id"], "op-download-1")
        preparing = next(
            report for report in self.host.reports
            if report["stage"] == "preparing_submission"
        )
        self.assertEqual(preparing["details"], {
            "effect_receipt": {
                "effect_key": "download.submit:download-operation-1",
                "state": "completed",
                "receipt": {
                    "job_id": "download-operation-1",
                    "selected_path": "/Downloads",
                },
            },
            "telegram_visibility": "silent",
        })
        await self.runtime.tasks.pop("download-operation-1")

        stages = [report["stage"] for report in self.host.reports]
        for stage in (
            "preparing_submission",
            "submitted",
            "downloading",
            "reading_files",
            "handoff_rename",
        ):
            self.assertIn(stage, stages)
        self.assertEqual(self.host.reports[-1]["state"], "handed_off")
        self.assertEqual(self.host.reports[-1]["next_plugin_id"], "rename")
        self.assertEqual(
            self.host.reports[-1]["status_text"],
            "已下载，开始整理",
        )
        self.assertEqual(self.host.events[0][1]["operation_id"], "op-download-1")
        self.assertEqual(self.host.events[0][1]["chat_id"], 10)
        self.assertEqual(self.host.notifications, [])

    async def test_download_stage_seals_before_rename_event_is_published(self):
        original_seal = self.host.seal_operation_stage

        async def queue_before_later_delivery_failure(*args, **kwargs):
            response = await original_seal(*args, **kwargs)
            return {
                **response,
                "queued": True,
                "delivery_state": "failed",
            }

        self.host.seal_operation_stage = queue_before_later_delivery_failure
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "7" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-download-seal",
                "operation_revision": 0,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "download-stage-seal"},
        })

        await self.runtime.tasks.pop("download-stage-seal")

        handoff_index = self.host.timeline.index(
            ("report", "handed_off", "handoff_rename")
        )
        seal_index = next(
            index for index, item in enumerate(self.host.timeline)
            if item[:2] == ("milestone", "stage")
        )
        event_index = self.host.timeline.index(
            ("event", "download.completed")
        )
        self.assertLess(handoff_index, seal_index)
        self.assertLess(seal_index, event_index)
        self.assertEqual(self.host.milestones[0]["text"], "已下载，开始整理")
        self.assertEqual(
            [event[0] for event in self.host.events],
            ["download.completed"],
        )

    async def test_lost_download_stage_response_retries_same_milestone(self):
        from telepiplex_plugin_sdk import FeatureError

        original_seal = self.host.seal_operation_stage
        attempts = []

        async def accept_then_lose(
            operation_id,
            milestone_id,
            text,
            *,
            deadline=10,
        ):
            attempts.append(milestone_id)
            response = await original_seal(
                operation_id,
                milestone_id,
                text,
                deadline=deadline,
            )
            if len(attempts) == 1:
                raise FeatureError(
                    "internal_error",
                    "Host milestone bookkeeping was interrupted",
                )
            return {**response, "accepted": False, "duplicate": True}

        self.host.seal_operation_stage = accept_then_lose

        await self.feature._seal_download_stage(
            "op-download-lost-stage",
            "job-download-lost-stage",
            {"final_path": "/Downloads/Show.S01E01.mkv"},
        )

        self.assertEqual(attempts, [attempts[0], attempts[0]])

    async def test_rejected_download_stage_milestone_is_not_retried(self):
        from telepiplex_plugin_sdk import FeatureError

        attempts = []

        async def reject_owner(*_args, **_kwargs):
            attempts.append("owner_mismatch")
            raise FeatureError(
                "owner_mismatch",
                "operation belongs to another Feature",
            )

        self.host.seal_operation_stage = reject_owner

        with self.assertRaises(FeatureError) as raised:
            await self.feature._seal_download_stage(
                "op-download-rejected-stage",
                "job-download-rejected-stage",
                {"final_path": "/Downloads/Show.S01E01.mkv"},
            )

        self.assertEqual(raised.exception.code, "owner_mismatch")
        self.assertEqual(attempts, ["owner_mismatch"])

    async def test_download_completes_and_skips_organization_when_rename_is_inactive(self):
        async def reject_missing_target(report, **_kwargs):
            self.host.reports.append(dict(report))
            if (
                report.get("state") == "handed_off"
                and report.get("next_plugin_id") == "rename"
            ):
                return {
                    "accepted": False,
                    "operation_id": report["operation_id"],
                    "state": "running",
                    "revision": report["revision"] - 1,
                    "error_code": "handoff_target_unavailable",
                    "target_plugin_id": "rename",
                }
            return {
                "accepted": True,
                "operation_id": report["operation_id"],
                "state": report["state"],
                "revision": report["revision"],
            }

        self.host.report_operation = reject_missing_target
        result = await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "4" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-no-rename",
                "operation_revision": 0,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "download-no-rename"},
        })

        await self.runtime.tasks.pop("download-no-rename")

        self.assertTrue(result["accepted"])
        self.assertEqual(self.host.events, [])
        self.assertEqual(self.host.notifications, [])
        self.assertEqual(
            self.host.reports[-1]["status_text"],
            "已下载，未自动整理\n保存目录：/Downloads/Show.S01E01.mkv",
        )
        self.assertEqual(self.host.reports[-1]["state"], "completed")
        self.assertNotIn("next_plugin_id", self.host.reports[-1])

    async def test_cancelled_download_deletes_known_offline_record_once_not_media(self):
        class BlockingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.wait_started = threading.Event()

            def wait_for_download(self, link, **kwargs):
                kwargs["progress_callback"]({
                    "resource_name": "Show.partial",
                    "info_hash": "known-hash",
                    "progress": 5,
                })
                self.wait_started.set()
                cancel_event = kwargs["cancel_event"]
                while not cancel_event.wait(0.01):
                    pass
                raise RuntimeError("cancelled")

        client = BlockingClient()
        self.feature.client = client
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "2" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-cancel-1",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "cancel-job-1"},
        })
        task = asyncio.create_task(self.runtime.tasks.pop("cancel-job-1"))
        self.assertTrue(await asyncio.to_thread(client.wait_started.wait, 1))

        accepted = await self.feature.operation_control({
            "operation_id": "op-cancel-1",
            "action": "cancel",
            "revision": self.host.reports[-1]["revision"],
        })
        await task

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(client.deleted_tasks, [("known-hash", 0)])
        self.assertEqual(client.deleted_files, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.host.reports[-1]["details"]["offline_task_record"],
            "deleted",
        )

    async def test_lost_response_retry_preserves_live_cancel_owner(self):
        class BlockingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.wait_started = threading.Event()

            def wait_for_download(self, link, **kwargs):
                kwargs["progress_callback"]({
                    "resource_name": "Show.partial",
                    "info_hash": "retry-known-hash",
                    "progress": 5,
                })
                self.wait_started.set()
                kwargs["cancel_event"].wait(1)
                raise RuntimeError("cancelled")

        client = BlockingClient()
        self.feature.client = client
        request = {
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "3" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-retry-cancel",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "retry-cancel-job"},
        }

        first = await self.feature.download_capability(request)
        task = asyncio.create_task(self.runtime.tasks.pop("retry-cancel-job"))
        self.assertTrue(await asyncio.to_thread(client.wait_started.wait, 1))
        original_event = self.feature.operations["op-retry-cancel"][
            "cancel_event"
        ]

        retried = await self.feature.download_capability(request)
        self.assertTrue(retried["duplicate"])
        self.assertIs(
            self.feature.operations["op-retry-cancel"]["cancel_event"],
            original_event,
        )
        await self.feature.operation_control({
            "operation_id": "op-retry-cancel",
            "action": "cancel",
            "revision": retried["operation"]["revision"],
        })
        await task

        self.assertEqual(client.deleted_tasks, [("retry-known-hash", 0)])
        self.assertEqual(client.deleted_files, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")

    async def test_cancel_before_info_hash_keeps_offline_record_without_retry(self):
        class BlockingSubmitClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.add_started = threading.Event()
                self.release_add = threading.Event()

            def add_offline_task(self, link, selected_path):
                self.add_started.set()
                self.release_add.wait(1)
                return True

        client = BlockingSubmitClient()
        self.feature.client = client
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "4" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-cancel-unknown",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "cancel-job-unknown"},
        })
        task = asyncio.create_task(self.runtime.tasks.pop("cancel-job-unknown"))
        self.assertTrue(await asyncio.to_thread(client.add_started.wait, 1))
        report_count = len(self.host.reports)

        await self.feature.operation_control({
            "operation_id": "op-cancel-unknown",
            "action": "cancel",
            "revision": self.host.reports[-1]["revision"],
        })
        client.release_add.set()
        await task

        self.assertEqual(client.deleted_tasks, [])
        self.assertFalse(any(
            report["state"] == "running"
            for report in self.host.reports[report_count:]
        ))
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertEqual(
            self.host.reports[-1]["details"]["offline_task_record"],
            "retained",
        )
        self.assertEqual(
            self.host.reports[-1]["status_text"],
            "下载已停止；已下载内容保留。",
        )

    async def test_source_can_cancel_before_rename_accepts_handoff(self):
        await self.feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "5" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-provisional-handoff",
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "provisional-handoff"},
        })
        operation = self.feature.operations["op-provisional-handoff"]
        operation["info_hash"] = "known-handoff-hash"
        self.feature._advance_operation(
            "op-provisional-handoff",
            state="handed_off",
            stage="handoff_rename",
            status_text="正在交给 rename。",
            control="cancel",
            next_plugin_id="rename",
        )

        result = await self.feature.operation_control({
            "operation_id": "op-provisional-handoff",
            "action": "cancel",
            "revision": operation["revision"],
        })

        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertEqual(self.client.deleted_tasks, [("known-handoff-hash", 0)])
        self.assertEqual(self.client.deleted_files, [])

    async def test_download_flow_emits_sanitized_runtime_logs(self):
        magnet = "magnet:?xt=urn:btih:" + "f" * 40

        with self.assertLogs("telepiplex.download", level="INFO") as captured:
            result = await self.feature.download_capability({
                "method": "submit",
                "payload": {
                    "link": magnet,
                    "selected_path": "/Downloads",
                    "user_id": 123,
                },
                "context": {"idempotency_key": "log-job-1"},
            })
            self.assertEqual(result["job_id"], "log-job-1")
            await self.runtime.tasks.pop("log-job-1")

        output = "\n".join(captured.output)
        self.assertIn("download_download_started", output)
        self.assertIn("download_download_completed", output)
        self.assertIn("selected_path=/Downloads", output)
        self.assertNotIn(magnet, output)

    async def test_storage_capability_is_an_explicit_whitelist(self):
        result = await self.feature.storage_capability({
            "method": "get_file_info",
            "payload": {"args": ["/Downloads/Show"]},
        })
        self.assertEqual(result["value"]["file_id"], "1")

        with self.assertRaisesRegex(Exception, "not allowed"):
            await self.feature.storage_capability({
                "method": "__getattribute__",
                "payload": {"args": ["access_token"]},
            })

    async def test_storage_capability_batches_bounded_file_info_paths(self):
        calls = []

        def get_file_info_batch(paths):
            calls.append(list(paths))
            return {
                path: {"file_id": f"file-{index}", "file_category": "1"}
                for index, path in enumerate(paths)
            }

        self.client.get_file_info_batch = get_file_info_batch
        paths = [f"/Downloads/Show/episode-{index:03d}.mkv" for index in range(32)]

        result = await self.feature.storage_capability({
            "method": "get_file_info_batch",
            "payload": {"args": [paths]},
        })

        self.assertEqual(calls, [paths])
        self.assertEqual(len(result["value"]), 32)

        with self.assertRaisesRegex(Exception, "batch exceeds"):
            await self.feature.storage_capability({
                "method": "get_file_info_batch",
                "payload": {"args": [paths + ["/Downloads/overflow.mkv"]]},
            })

    async def test_storage_capability_exposes_bounded_native_move(self):
        calls = []

        def move_files_by_id(file_ids, target_dir_id):
            calls.append((list(file_ids), target_dir_id))
            return {"state": "submitted", "submitted": True}

        self.client.move_files_by_id = move_files_by_id
        ids = [f"episode-{index}" for index in range(100)]

        result = await self.feature.storage_capability({
            "method": "move_files_by_id",
            "payload": {"args": [ids, "season-1"]},
        })

        self.assertTrue(result["value"]["submitted"])
        self.assertEqual(calls, [(ids, "season-1")])

        with self.assertRaisesRegex(Exception, "batch exceeds"):
            await self.feature.storage_capability({
                "method": "move_files_by_id",
                "payload": {"args": [ids + ["overflow"], "season-1"]},
            })

    async def test_completed_job_is_persistently_idempotent(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        jobs = DownloadJobStore(Path(self._testMethodName + ".db"))
        self.addCleanup(Path(self._testMethodName + ".db").unlink, missing_ok=True)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "c" * 40,
            "selected_path": "/Downloads",
        }, "context": {"idempotency_key": "durable-1"}}

        await feature.download_capability(request)
        await runtime.tasks.pop("durable-1")
        report_count = len(self.host.reports)
        duplicate = await feature.download_capability(request)

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["state"], "completed")
        self.assertEqual(runtime.tasks, {})
        self.assertEqual(len(self.host.reports), report_count)

    async def test_concurrent_same_job_starts_once_with_one_operation_identity(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_report = feature._report_operation
        report_started = asyncio.Event()
        release_report = asyncio.Event()

        async def blocking_report(*args, **kwargs):
            report_started.set()
            await release_report.wait()
            return await original_report(*args, **kwargs)

        feature._report_operation = blocking_report
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "8" * 40,
            "selected_path": "/Downloads",
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "concurrent-one"}}
        first_task = asyncio.create_task(feature.download_capability(request))
        await report_started.wait()
        duplicate = await feature.download_capability(request)
        release_report.set()
        first = await first_task

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(list(runtime.tasks), ["concurrent-one"])
        await runtime.tasks.pop("concurrent-one")
        self.assertEqual(len(self.client.added), 1)

    async def test_lost_running_report_response_still_starts_executor_once(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_host_report = self.host.report_operation
        host_report_attempts = 0

        async def unavailable_once_then_accept(report, **kwargs):
            nonlocal host_report_attempts
            host_report_attempts += 1
            if host_report_attempts == 1:
                raise RuntimeError("Host still unavailable")
            return await original_host_report(report, **kwargs)

        self.host.report_operation = unavailable_once_then_accept
        original_report = feature._report_operation
        lost = False

        async def lose_first_response(*args, **kwargs):
            nonlocal lost
            if not lost:
                lost = True
                raise RuntimeError("Host response lost")
            return await original_report(*args, **kwargs)

        feature._report_operation = lose_first_response
        request = {"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "7" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-lost-running-response",
            "operation_revision": 5,
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "lost-running-response"}}

        accepted = await feature.download_capability(request)
        duplicate = await feature.download_capability(request)

        self.assertTrue(accepted["accepted"])
        self.assertTrue(accepted["report_pending"])
        self.assertTrue(duplicate["duplicate"])
        expected_effect = {
            "effect_receipt": {
                "effect_key": "download.submit:lost-running-response",
                "state": "completed",
                "receipt": {
                    "job_id": "lost-running-response",
                    "selected_path": "/Downloads",
                },
            },
            "telegram_visibility": "silent",
        }
        self.assertEqual(accepted["operation"]["details"], expected_effect)
        self.assertEqual(list(runtime.tasks), ["lost-running-response"])
        self.assertEqual(jobs.get("lost-running-response")["state"], "running")
        await runtime.tasks.pop("lost-running-response")
        self.assertEqual(self.host.reports[0]["details"], expected_effect)
        self.assertEqual(len(self.client.added), 1)
        self.assertEqual(jobs.get("lost-running-response")["state"], "completed")

    async def test_unconfirmed_pending_ownership_never_submits_offline_task(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        class RejectedHost(FakeHost):
            async def report_operation(self, report, **kwargs):
                self.reports.append(dict(report))
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": report["revision"],
                }

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        host = RejectedHost()
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        original_report = feature._report_operation
        first = True

        async def unavailable_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise RuntimeError("Host unavailable before ownership claim")
            return await original_report(*args, **kwargs)

        feature._report_operation = unavailable_once
        accepted = await feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "6" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-unconfirmed-owner",
                "operation_revision": 5,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "unconfirmed-owner"},
        })

        self.assertTrue(accepted["report_pending"])
        with self.assertRaises(Exception):
            await runtime.tasks.pop("unconfirmed-owner")
        self.assertEqual(self.client.added, [])
        self.assertEqual(jobs.get("unconfirmed-owner")["state"], "failed")

    async def test_cancelled_persisted_job_never_restarts_or_reports_running(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        payload = {
            "link": "magnet:?xt=urn:btih:" + "9" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-durable-cancelled",
            "chat_id": 10,
            "user_id": 1,
        }
        jobs.create_or_get("durable-cancelled", payload)
        jobs.update("durable-cancelled", "cancelled", error="cancelled")
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        duplicate = await feature.download_capability({
            "method": "submit",
            "payload": payload,
            "context": {"idempotency_key": "durable-cancelled"},
        })

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["state"], "cancelled")
        self.assertEqual(runtime.tasks, {})
        self.assertEqual(self.host.reports, [])

    async def test_completion_publish_failure_is_not_mislabeled_as_download_failure(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host, client=self.client, jobs=jobs,
        )
        runtime = FakeRuntime(); feature.bind_runtime(runtime)
        self.host.fail_publish = True
        await feature.download_capability({"method": "submit", "payload": {
            "link": "magnet:?xt=urn:btih:" + "d" * 40,
            "selected_path": "/Downloads",
            "operation_id": "op-outbox",
            "operation_revision": 4,
            "chat_id": 10,
            "user_id": 1,
        }, "context": {"idempotency_key": "outbox-1"}})

        await runtime.tasks.pop("outbox-1")

        downloaded = jobs.get("outbox-1")
        self.assertEqual(downloaded["state"], "downloaded")
        self.assertEqual(downloaded["result"]["operation_id"], "op-outbox")
        self.assertEqual(
            downloaded["result"]["completion_event_idempotency_key"],
            "outbox-1:completed",
        )
        self.assertNotIn("completion_event_id", downloaded["result"])
        self.assertEqual(
            downloaded["result"]["operation_revision"],
            feature.operations["op-outbox"]["revision"],
        )
        self.assertEqual(
            (await feature.operation_snapshot({"operation_id": "op-outbox"}))[
                "operations"
            ][0]["state"],
            "handed_off",
        )

        self.host.fail_publish = False
        restored = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            jobs=jobs,
        )
        restored_runtime = FakeRuntime()
        restored.bind_runtime(restored_runtime)
        self.assertEqual(
            restored.operations["op-outbox"]["state"], "handed_off"
        )
        await restored_runtime.tasks.pop("outbox-1")
        self.assertEqual(jobs.get("outbox-1")["state"], "completed")
        self.assertEqual(
            jobs.get("outbox-1")["result"]["completion_event_id"],
            "event-1",
        )
        self.assertEqual(self.host.events[-1][1]["operation_id"], "op-outbox")

    async def test_restore_downloaded_operation_uses_exact_persisted_handoff_report(self):
        from telepiplex_download.service import DownloadFeature

        report = {
            "operation_id": "op-exact-restored-handoff",
            "chat_id": 10,
            "user_id": 1,
            "state": "handed_off",
            "stage": "handoff_rename",
            "status_text": "exact persisted status",
            "control": "cancel",
            "revision": 9,
            "details": {"progress": 87.5, "proof": "persisted"},
            "next_plugin_id": "rename",
        }
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
        )

        feature._restore_downloaded_operation({
            "job_id": "exact-restored-handoff",
            "state": "downloaded",
            "result": {
                "operation_id": report["operation_id"],
                "operation_revision": report["revision"],
                "chat_id": report["chat_id"],
                "user_id": report["user_id"],
                "download_handoff_report": report,
                "download_handoff_accepted": False,
            },
        })

        self.assertEqual(
            feature._operation_view(feature.operations[report["operation_id"]]),
            report,
        )

    async def test_legacy_downloaded_positive_revision_migrates_as_accepted_handoff(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        jobs.create_or_get("legacy-accepted-handoff", {
            "operation_id": "op-legacy-accepted-handoff",
        })
        jobs.update("legacy-accepted-handoff", "downloaded", result={
            "job_id": "legacy-accepted-handoff",
            "operation_id": "op-legacy-accepted-handoff",
            "operation_revision": 7,
            "chat_id": 10,
            "user_id": 1,
            "final_path": "/Downloads/Legacy.Movie.mkv",
        })
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        migrated = jobs.get("legacy-accepted-handoff")["result"]
        self.assertTrue(migrated["download_handoff_accepted"])
        self.assertEqual(migrated["download_handoff_report"], {
            "operation_id": "op-legacy-accepted-handoff",
            "chat_id": 10,
            "user_id": 1,
            "state": "handed_off",
            "stage": "handoff_rename",
            "status_text": "已下载，开始整理",
            "control": "cancel",
            "revision": 7,
            "details": {"downloaded_content": "preserved"},
            "next_plugin_id": "rename",
        })

        await runtime.tasks.pop("legacy-accepted-handoff")

        self.assertEqual(self.host.reports, [])
        self.assertEqual(jobs.get("legacy-accepted-handoff")["state"], "completed")
        event_payload = self.host.events[-1][1]
        self.assertNotIn("download_handoff_report", event_payload)
        self.assertNotIn("download_handoff_accepted", event_payload)

    async def test_legacy_downloaded_without_revision_fails_closed_without_rev1(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature
        from telepiplex_plugin_sdk import FeatureError

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        jobs.create_or_get("legacy-unproven-handoff", {
            "operation_id": "op-legacy-unproven-handoff",
        })
        jobs.update("legacy-unproven-handoff", "downloaded", result={
            "job_id": "legacy-unproven-handoff",
            "operation_id": "op-legacy-unproven-handoff",
            "chat_id": 10,
            "user_id": 1,
            "final_path": "/Downloads/Unproven.Movie.mkv",
        })
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        self.assertNotIn("op-legacy-unproven-handoff", feature.operations)
        with self.assertRaises(FeatureError) as raised:
            await runtime.tasks.pop("legacy-unproven-handoff")

        self.assertEqual(raised.exception.code, "handoff_recovery_required")
        self.assertEqual(jobs.get("legacy-unproven-handoff")["state"], "downloaded")
        self.assertEqual(self.host.reports, [])
        self.assertEqual(self.host.events, [])

    async def test_committed_completion_response_loss_replays_stable_event_identity(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        class CommitThenLoseHost(FakeHost):
            def __init__(self):
                super().__init__()
                self.logical_events = {}
                self.publish_attempts = []

            async def publish_event(self, event_type, payload, **kwargs):
                key = kwargs["idempotency_key"]
                self.publish_attempts.append(key)
                event_id = self.logical_events.setdefault(key, "stable-event-1")
                if len(self.publish_attempts) == 1:
                    raise RuntimeError("Host committed event before response loss")
                return {"event_id": event_id}

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        host = CommitThenLoseHost()
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host,
            client=self.client,
            jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "e" * 40,
                "selected_path": "/Downloads",
                "operation_id": "op-committed-response-loss",
                "operation_revision": 4,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "committed-response-loss"},
        })
        await runtime.tasks.pop("committed-response-loss")

        durable = jobs.get("committed-response-loss")
        self.assertEqual(durable["state"], "downloaded")
        self.assertEqual(
            durable["result"]["completion_event_idempotency_key"],
            "committed-response-loss:completed",
        )

        restored = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host,
            client=self.client,
            jobs=jobs,
        )
        restored_runtime = FakeRuntime()
        restored.bind_runtime(restored_runtime)
        await restored_runtime.tasks.pop("committed-response-loss")

        completed = jobs.get("committed-response-loss")
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(
            completed["result"]["completion_event_id"],
            "stable-event-1",
        )
        self.assertEqual(host.publish_attempts, [
            "committed-response-loss:completed",
            "committed-response-loss:completed",
        ])
        self.assertEqual(host.logical_events, {
            "committed-response-loss:completed": "stable-event-1",
        })

    async def test_completion_event_identity_mismatch_fails_closed(self):
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        jobs.create_or_get("event-id-mismatch", {
            "operation_id": "op-event-id-mismatch",
        })
        jobs.update("event-id-mismatch", "downloaded", result={
            "job_id": "event-id-mismatch",
            "operation_id": "op-event-id-mismatch",
            "chat_id": 10,
            "user_id": 1,
            "operation_revision": 5,
            "completion_event_idempotency_key": "event-id-mismatch:completed",
            "completion_event_id": "original-event-id",
        })

        async def mismatched_publish(*_args, **_kwargs):
            return {"event_id": "different-event-id"}

        self.host.publish_event = mismatched_publish
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=self.host,
            client=self.client,
            jobs=jobs,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        with self.assertRaises(Exception):
            await runtime.tasks.pop("event-id-mismatch")
        self.assertEqual(jobs.get("event-id-mismatch")["state"], "downloaded")
        self.assertEqual(
            jobs.get("event-id-mismatch")["result"]["completion_event_id"],
            "original-event-id",
        )

    async def test_interrupted_external_transfer_requires_manual_retry(self):
        from telepiplex_download.jobs import DownloadJobStore

        path = Path(self._testMethodName + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        jobs = DownloadJobStore(path)
        jobs.create_or_get("crashed", {"link": "magnet:?x"})
        jobs.update("crashed", "running")

        restarted = DownloadJobStore(path)

        self.assertEqual(restarted.get("crashed")["state"], "interrupted")
        self.assertEqual(restarted.resumable(), [])

    async def test_magnet_command_uses_session_and_namespaced_callback(self):
        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "series/live action"},
        ]
        command = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "b" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(command["session"]["state"], "open")
        self.assertEqual(command["operation"]["state"], "awaiting_input")
        callback_data = command["actions"][0]["data"]["keyboard"][0][0]["callback_data"]
        self.assertEqual(callback_data, "download:path:0")

        callback = await self.feature.callback({
            "namespace": "download",
            "payload": "path:0",
            "user_id": 1,
            "chat_id": 10,
            "update_id": 22,
        })
        self.assertEqual(callback["session"]["state"], "close")
        self.assertEqual(callback["actions"][0]["text"], "已提交下载")
        self.assertEqual(callback["operation"]["state"], "running")
        self.assertEqual(len(self.runtime.tasks), 1)
        task_id = next(iter(self.runtime.tasks))
        await self.runtime.tasks.pop(task_id)
        self.assertEqual(self.client.added[0][1], "/series/live action")

    async def test_config_opens_home_while_auth_opens_authorization_directly(self):
        config = await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })
        config_buttons = [
            button["callback_data"]
            for row in config["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(config_buttons, [
            "download:config:auth",
            "download:config:directories",
            "download:exit",
        ])
        self.assertIn("保存目录：0 个", config["actions"][0]["text"])

        from_config = await self.feature.callback({
            "payload": "config:auth", "user_id": 1, "chat_id": 10,
        })
        self.assertIn(
            "download:auth:direct",
            str(from_config["actions"][0]["data"]["keyboard"]),
        )

        auth = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        auth_buttons = [
            button["callback_data"]
            for row in auth["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(auth_buttons, [
            "download:auth:direct", "download:auth:scan", "download:exit",
        ])

    async def test_directory_list_is_paginated_with_bounded_keyboard(self):
        directories = [
            {"name": f"目录{index}", "path": f"Path{index}"}
            for index in range(7)
        ]
        self.feature.config["save_directories"] = directories
        self.feature.config_store.config["save_directories"] = directories

        response = await self._open_directory_config()

        keyboard = response["actions"][0]["data"]["keyboard"]
        self.assertLessEqual(len(keyboard), 10)
        self.assertIn("download:config:page:1", str(keyboard))
        self.assertIn("目录4", str(keyboard))
        self.assertNotIn("目录5", str(keyboard))

        next_page = await self.feature.callback({
            "payload": "config:page:1", "user_id": 1, "chat_id": 10,
        })
        next_keyboard = next_page["actions"][0]["data"]["keyboard"]
        self.assertLessEqual(len(next_keyboard), 10)
        self.assertIn("download:config:page:0", str(next_keyboard))
        self.assertIn("目录5", str(next_keyboard))
        self.assertIn("目录6", str(next_keyboard))

    async def test_directory_editor_rejects_negative_item_index(self):
        directories = [
            {"name": "A", "path": "A"},
            {"name": "B", "path": "B"},
        ]
        self.feature.config["save_directories"] = directories
        self.feature.config_store.config["save_directories"] = directories
        await self._open_directory_config()

        response = await self.feature.callback({
            "payload": "config:item:-1", "user_id": 1, "chat_id": 10,
        })

        self.assertIn("不可用", response["actions"][0]["text"])
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "directory_list")
        self.assertNotIn("selected_index", self.feature.sessions[(10, 1)])

    async def test_config_out_of_order_callback_keeps_home_navigation(self):
        await self.feature.command({
            "command": "config", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })

        keyboard = response["actions"][0]["data"]["keyboard"]
        self.assertIn("download:config:auth", str(keyboard))
        self.assertIn("download:config:directories", str(keyboard))
        self.assertNotIn("download:config:back", str(keyboard))
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "config_home")

    async def test_magnet_rejects_negative_directory_index(self):
        self.feature.config["save_directories"] = [
            {"name": "A", "path": "A"},
            {"name": "B", "path": "B"},
        ]
        await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "5" * 40],
            "user_id": 1,
            "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "path:-1", "user_id": 1, "chat_id": 10,
        })

        self.assertIn("不可用", response["actions"][0]["text"])
        self.assertEqual(response["session"]["state"], "close")
        self.assertEqual(response["operation"]["state"], "cancelled")
        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.runtime.tasks, {})

    async def test_directory_working_copy_add_edit_delete_and_save(self):
        original = [
            {"name": "剧集", "path": "series"},
            {"name": "删除项", "path": "delete"},
        ]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        await self._open_directory_config()

        name_prompt = await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("第一步", name_prompt["actions"][0]["text"])
        self.assertIn("只用于按钮展示", name_prompt["actions"][0]["text"])
        path_prompt = await self.feature.message({
            "text": "真人电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("第二步", path_prompt["actions"][0]["text"])
        self.assertIn("单级目录", path_prompt["actions"][0]["text"])
        self.assertIn("真人电影", path_prompt["actions"][0]["text"])
        self.assertIn("series/live action", path_prompt["actions"][0]["text"])
        added = await self.feature.message({
            "text": "真人电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("真人电影", str(
            added["actions"][0]["data"]["keyboard"]
        ))

        await self.feature.callback({
            "payload": "config:item:0", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:edit:name", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "电视剧", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:item:0", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "config:edit:path", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "tv/live action/", "user_id": 1, "chat_id": 10,
        })

        await self.feature.callback({
            "payload": "config:item:1", "user_id": 1, "chat_id": 10,
        })
        confirm = await self.feature.callback({
            "payload": "config:delete", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("确认删除", confirm["actions"][0]["text"])
        self.assertEqual(len(
            self.feature.sessions[(10, 1)]["working_directories"]
        ), 3)
        await self.feature.callback({
            "payload": "config:delete:confirm", "user_id": 1, "chat_id": 10,
        })

        saved = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })
        expected = [
            {"name": "电视剧", "path": "tv/live action"},
            {"name": "真人电影", "path": "真人电影"},
        ]
        self.assertEqual(saved["session"]["state"], "close")
        self.assertEqual(self.feature.config_store.directory_writes, [expected])
        self.assertEqual(self.feature.config["save_directories"], expected)
        self.assertEqual(saved["operation"]["state"], "completed")

        magnet = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "4" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        magnet_keyboard = magnet["actions"][0]["data"]["keyboard"]
        self.assertIn("电视剧", str(magnet_keyboard))
        self.assertIn("真人电影", str(magnet_keyboard))

    async def test_directory_input_rejects_invalid_and_duplicate_values(self):
        original = [{"name": "剧集", "path": "series/live action"}]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        await self._open_directory_config()
        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })

        invalid_name = await self.feature.message({
            "text": "line-one\nline-two", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("名称", invalid_name["actions"][0]["text"])
        await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        leading_slash = await self.feature.message({
            "text": "/movies", "user_id": 1, "chat_id": 10,
        })
        duplicate_path = await self.feature.message({
            "text": "series/live action/", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("不要以 / 开头", leading_slash["actions"][0]["text"])
        self.assertIn("重复", duplicate_path["actions"][0]["text"])
        await self.feature.message({
            "text": "movies", "user_id": 1, "chat_id": 10,
        })

        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        duplicate_name = await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        self.assertIn("重复", duplicate_name["actions"][0]["text"])
        self.assertEqual(self.feature.config_store.directory_writes, [])

    async def test_directory_exit_and_q_discard_working_copy(self):
        for use_q in (False, True):
            with self.subTest(use_q=use_q):
                await self._open_directory_config()
                await self.feature.callback({
                    "payload": "config:add", "user_id": 1, "chat_id": 10,
                })
                await self.feature.message({
                    "text": "电影", "user_id": 1, "chat_id": 10,
                })
                await self.feature.message({
                    "text": "/Movies", "user_id": 1, "chat_id": 10,
                })
                if use_q:
                    response = await self.feature.command({
                        "command": "q", "user_id": 1, "chat_id": 10,
                    })
                else:
                    response = await self.feature.callback({
                        "payload": "exit", "user_id": 1, "chat_id": 10,
                    })
                self.assertEqual(response["session"]["state"], "close")
                self.assertEqual(self.feature.config_store.directory_writes, [])

    async def test_directory_session_timeout_discards_working_copy(self):
        from telepiplex_download import service

        with patch.object(service, "SESSION_TTL_SECONDS", 0):
            await self._open_directory_config()
            await asyncio.sleep(0.01)

        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.directory_writes, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertIn("目录配置", self.host.reports[-1]["status_text"])

    async def test_directory_save_failure_retains_old_config_and_working_copy(self):
        original = [{"name": "剧集", "path": "series"}]
        self.feature.config["save_directories"] = original
        self.feature.config_store.config["save_directories"] = original
        self.feature.config_store.fail_directory_writes = True
        await self._open_directory_config()
        await self.feature.callback({
            "payload": "config:add", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "电影", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "movies", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.callback({
            "payload": "config:save", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(response["operation"]["state"], "awaiting_input")
        self.assertEqual(self.feature.config["save_directories"], original)
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "directory_list")
        self.assertEqual(len(
            self.feature.sessions[(10, 1)]["working_directories"]
        ), 2)
        self.assertNotIn("secret-value", str(response))

    async def test_direct_token_wizard_writes_only_after_refresh_and_activates_client(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        direct = await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(direct["session"]["state"], "open")
        self.assertIn("Access token", direct["actions"][0]["text"])

        access = await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(access["session"]["state"], "open")
        self.assertIn("Refresh token", access["actions"][0]["text"])
        self.assertEqual(self.feature.config_store.writes, [])

        completed = await self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(completed["session"]["state"], "close")
        self.assertEqual(self.feature.config_store.writes, [
            ("access-new", "refresh-new", "direct"),
        ])
        self.assertEqual(self.client.tokens, ("access-new", "refresh-new"))
        self.assertNotIn("access-new", str(completed))
        self.assertNotIn("refresh-new", str(completed))

    async def test_direct_token_wizard_rejects_invalid_values_without_writing(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })

        for invalid in ("", "your_access_token", "line-one\nline-two"):
            response = await self.feature.message({
                "text": invalid, "user_id": 1, "chat_id": 10,
            })
            self.assertEqual(response["session"]["state"], "open")
            if invalid:
                self.assertNotIn(invalid, str(response))
        self.assertEqual(self.feature.config_store.writes, [])

        await self.feature.message({
            "text": "access-valid", "user_id": 1, "chat_id": 10,
        })
        response = await self.feature.message({
            "text": "refresh-one\nrefresh-two", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-valid", str(response))

    async def test_each_open_auth_and_path_step_has_one_explicit_exit(self):
        def exit_count(response):
            return sum(
                button.get("text") == "退出"
                for action in response.get("actions", [])
                for row in (action.get("data") or {}).get("keyboard", [])
                for button in row
            )

        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "series"},
        ]
        path = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "3" * 40],
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(exit_count(path), 1)

        choose = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(choose), 1)
        access = await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(access), 1)
        invalid_access = await self.feature.message({
            "text": "", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(invalid_access), 1)
        refresh = await self.feature.message({
            "text": "access", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(refresh), 1)
        invalid_refresh = await self.feature.message({
            "text": "your_refresh_token", "user_id": 1, "chat_id": 10,
        })
        self.assertEqual(exit_count(invalid_refresh), 1)

    async def test_explicit_exit_clears_session_and_terminalizes_operation(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })

        result = await self.feature.callback({
            "payload": "exit", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(result["session"]["state"], "close")
        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertNotIn((10, 1), self.feature.sessions)

    async def test_direct_token_wizard_q_exits_and_discards_pending_access(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-pending", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.command({
            "command": "q", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "close")
        self.assertEqual(
            response["actions"][0]["text"],
            "已退出。",
        )
        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-pending", str(response))

    async def test_direct_token_write_failure_preserves_client_and_secret(self):
        self.client.tokens = ("old-access", "old-refresh")
        self.feature.config_store.fail_writes = True
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertNotIn("access-new", str(response))
        self.assertNotIn("refresh-new", str(response))
        self.assertNotIn("secret-value", str(response))
        self.assertIn("使用 /q 退出", response["actions"][0]["text"])
        self.assertNotIn("使用 /q 取消", response["actions"][0]["text"])

    async def test_partial_token_write_failure_restores_exact_snapshot(self):
        class PartialWriteStore(FakeConfigStore):
            def write_tokens(self, *args, **kwargs):
                super().write_tokens(*args, **kwargs)
                raise OSError("chmod failed after replace")

        old = {
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "custom": "preserved",
        }
        self.feature.config_store = PartialWriteStore(old)
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })

        response = await self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.feature.config_store.config, old)
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(response["operation"]["state"], "awaiting_input")
        self.assertIn("原配置已恢复", response["operation"]["status_text"])

    async def test_rollback_after_token_write_before_terminal_commit_restores(self):
        old = {
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        }
        self.feature.config_store = FakeConfigStore(old)
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-new", "user_id": 1, "chat_id": 10,
        })
        original_persist = self.feature._persist_tokens
        persisted = asyncio.Event()
        release = asyncio.Event()

        async def pause_after_persist(*args, **kwargs):
            result = await original_persist(*args, **kwargs)
            persisted.set()
            await release.wait()
            return result

        self.feature._persist_tokens = pause_after_persist
        completing = asyncio.create_task(self.feature.message({
            "text": "refresh-new", "user_id": 1, "chat_id": 10,
        }))
        await persisted.wait()
        operation_id = next(iter(self.feature.operations))

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": self.feature.operations[operation_id]["revision"],
        })
        release.set()
        response = await completing

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertEqual(response["operation"]["state"], "rolled_back")
        self.assertEqual(self.feature.config_store.config, old)
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))

        retried = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": response["operation"]["revision"],
        })
        self.assertEqual(retried["operation"]["state"], "rolled_back")

    async def test_pending_access_token_expires_without_writing(self):
        from telepiplex_download import service

        with patch.object(service, "SESSION_TTL_SECONDS", 0):
            await self.feature.command({
                "command": "auth", "user_id": 1, "chat_id": 10,
            })
            await self.feature.callback({
                "payload": "auth:direct", "user_id": 1, "chat_id": 10,
            })
            await self.feature.message({
                "text": "access-pending", "user_id": 1, "chat_id": 10,
            })
            await asyncio.sleep(0.01)

        self.assertNotIn((10, 1), self.feature.sessions)
        self.assertEqual(self.feature.config_store.writes, [])

    async def test_magnet_session_replaces_and_clears_pending_access_token(self):
        await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })
        await self.feature.callback({
            "payload": "auth:direct", "user_id": 1, "chat_id": 10,
        })
        await self.feature.message({
            "text": "access-pending", "user_id": 1, "chat_id": 10,
        })
        self.feature.config["save_directories"] = [
            {"name": "剧集", "path": "series"},
        ]

        response = await self.feature.command({
            "command": "magnet",
            "args": ["magnet:?xt=urn:btih:" + "e" * 40],
            "user_id": 1,
            "chat_id": 10,
        })

        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(self.feature.sessions[(10, 1)]["stage"], "path")
        self.assertNotIn((10, 1), self.feature.session_expiry_handles)
        self.assertNotIn("access-pending", str(self.feature.sessions))

    async def test_scan_authorization_remains_independent_and_secret_safe(self):
        self.feature.config_store = FakeConfigStore({"app_id": "app-1"})
        await self.feature.command({
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        self.assertEqual(scan["actions"][0]["parse_mode"], "HTML")
        self.assertIn("<pre>", scan["actions"][0]["text"])
        auth_task_id = next(key for key in self.runtime.tasks if key.startswith("download-auth-"))
        await self.runtime.tasks.pop(auth_task_id)
        self.assertEqual(
            self.feature.config_store.writes[-1],
            ("scan-access", "scan-refresh", "scan"),
        )
        self.assertNotIn("scan-access", str(self.host.notifications))

    async def test_scan_authorization_can_be_cancelled_before_token_write(self):
        self.feature.config_store = FakeConfigStore({"app_id": "app-1"})
        await self.feature.command({
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        operation_id = scan["operation"]["operation_id"]

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "cancel",
            "revision": scan["operation"]["revision"],
        })
        auth_task_id = next(
            key for key in self.runtime.tasks if key.startswith("download-auth-")
        )
        await self.runtime.tasks.pop(auth_task_id)

        self.assertEqual(accepted["operation"]["state"], "cancelling")
        self.assertEqual(self.feature.config_store.writes, [])
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")

    async def test_scan_token_persistence_cancel_restores_exact_snapshot(self):
        class BlockingConfigStore(FakeConfigStore):
            def __init__(self, config):
                super().__init__(config)
                self.written = threading.Event()
                self.release = threading.Event()

            def write_tokens(self, *args, **kwargs):
                result = super().write_tokens(*args, **kwargs)
                self.written.set()
                self.release.wait(1)
                return result

        old = {
            "app_id": "app-1",
            "auth_mode": "direct",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "custom": "preserved",
        }
        store = BlockingConfigStore(old)
        self.feature.config_store = store
        self.feature.config.update(old)
        self.client.tokens = ("old-access", "old-refresh")
        await self.feature.command({
            "command": "auth", "user_id": 9, "chat_id": 10,
        })
        scan = await self.feature.callback({
            "payload": "auth:scan", "user_id": 9, "chat_id": 10,
        })
        operation_id = scan["operation"]["operation_id"]
        task_id = next(
            key for key in self.runtime.tasks if key.startswith("download-auth-")
        )
        task = asyncio.create_task(self.runtime.tasks.pop(task_id))
        self.assertTrue(await asyncio.to_thread(store.written.wait, 1))

        accepted = await self.feature.operation_control({
            "operation_id": operation_id,
            "action": "rollback",
            "revision": self.feature.operations[operation_id]["revision"],
        })
        store.release.set()
        await task

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertEqual(store.config, old)
        self.assertEqual(self.feature.config, {
            "download_timeout": 30,
            "poll_interval": 0.01,
            **old,
        })
        self.assertEqual(self.client.tokens, ("old-access", "old-refresh"))
        self.assertEqual(self.host.reports[-1]["state"], "rolled_back")

    async def test_operation_snapshot_returns_current_non_terminal_tasks(self):
        response = await self.feature.command({
            "command": "auth", "user_id": 1, "chat_id": 10,
        })

        snapshot = await self.feature.operation_snapshot({
            "operation_id": response["operation"]["operation_id"],
        })

        self.assertEqual(snapshot["operations"], [response["operation"]])


class FeatureConfigStoreTest(unittest.TestCase):
    def test_token_writeback_preserves_config_and_uses_private_permissions(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("app_id: app-1\nsave_directories: []\n", encoding="utf-8")
            store = FeatureConfigStore(path)
            updated = store.write_tokens("access", "refresh", auth_mode="scan")

            on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["app_id"], "app-1")
            self.assertEqual(on_disk["access_token"], "access")
            self.assertEqual(on_disk["refresh_token"], "refresh")
            self.assertEqual(updated["auth_mode"], "scan")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_directory_writeback_preserves_config_and_private_permissions(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "access_token: access\n"
                "refresh_token: refresh\n"
                "custom: keep\n"
                "save_directories: []\n",
                encoding="utf-8",
            )
            store = FeatureConfigStore(path)

            updated = store.write_save_directories([
                {"name": " 剧集 ", "path": " series/ "},
                {"name": "电影", "path": "movies"},
            ])

            self.assertEqual(updated["save_directories"], [
                {"name": "剧集", "path": "series"},
                {"name": "电影", "path": "movies"},
            ])
            self.assertEqual(updated["access_token"], "access")
            self.assertEqual(updated["refresh_token"], "refresh")
            self.assertEqual(updated["custom"], "keep")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_directory_writeback_rejects_invalid_and_duplicate_entries(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            invalid = (
                None,
                [{"name": "", "path": "series"}],
                [{"name": "剧集", "path": "/series"}],
                [{"name": "剧集", "path": "a", "extra": True}],
                [
                    {"name": "剧集", "path": "a"},
                    {"name": "剧集", "path": "b"},
                ],
                [
                    {"name": "A", "path": "series"},
                    {"name": "B", "path": "series/"},
                ],
            )
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.write_save_directories(value)

    def test_save_directory_writeback_normalizes_root_relative_paths(self):
        from telepiplex_download.config_store import FeatureConfigStore

        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            updated = store.write_save_directories([
                {"name": "剧集", "path": " series/live action/ "},
                {"name": "电影", "path": "movies"},
            ])

            self.assertEqual(updated["save_directories"], [
                {"name": "剧集", "path": "series/live action"},
                {"name": "电影", "path": "movies"},
            ])

    def test_save_directory_writeback_rejects_command_and_unsafe_paths(self):
        from telepiplex_download.config_store import FeatureConfigStore

        invalid_paths = (
            "/series",
            "/",
            "series//live action",
            "series//",
            "series///",
            ".",
            "..",
            "./series",
            "series/../live action",
            "series/./live action",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = FeatureConfigStore(Path(directory) / "config.yaml")
            for value in invalid_paths:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.write_save_directories([{
                        "name": "剧集",
                        "path": value,
                    }])


class RuntimeStartupTest(unittest.TestCase):
    @staticmethod
    def _context(root: Path):
        return SimpleNamespace(
            manifest={"plugin_id": "download", "version": "1.0.8"},
            token="runtime-token",
            socket_path=root / "runtime.sock",
            host_socket_path=root / "host.sock",
            config_path=root / "config.yaml",
            state_path=root / "state",
            host=FakeHost(),
        )

    def test_runtime_startup_persists_canonical_save_directories(self):
        from telepiplex_download.runtime import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            context.config_path.write_text(
                yaml.safe_dump({
                    "save_directories": [
                        {"name": "剧集", "path": "series/live action/"},
                    ],
                }, allow_unicode=True),
                encoding="utf-8",
            )

            runtime = main(context)
            feature = runtime.commands["config"].__self__
            expected = [
                {"name": "剧集", "path": "series/live action"},
            ]

            self.assertEqual(feature.config["save_directories"], expected)
            on_disk = yaml.safe_load(
                context.config_path.read_text(encoding="utf-8")
            )
            self.assertEqual(on_disk["save_directories"], expected)

    def test_runtime_startup_rejects_canonical_duplicate_directories(self):
        from telepiplex_download.runtime import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self._context(root)
            context.config_path.write_text(
                yaml.safe_dump({
                    "save_directories": [
                        {"name": "剧集", "path": "series/live action"},
                        {"name": "电影", "path": "series/live action/"},
                    ],
                }, allow_unicode=True),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unique"):
                main(context)


class FeatureSourceContractTest(unittest.TestCase):
    def test_adaptive_pacing_default_and_schema_contract(self):
        defaults = yaml.safe_load(
            (ROOT / "config.default.yaml").read_text(encoding="utf-8")
        )
        schema = yaml.safe_load(
            (ROOT / "config.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)

        validator.validate(defaults)
        self.assertEqual(defaults["poll_initial_interval"], 2)
        self.assertEqual(defaults["poll_max_interval"], 30)
        self.assertEqual(defaults["poll_backoff_factor"], 1.7)
        self.assertEqual(defaults["storage_read_workers"], 4)
        self.assertEqual(defaults["endpoint_intervals"], {
            "offline_poll": 1.0,
            "offline_mutation": 1.0,
            "storage_read": 0.25,
            "storage_mutation": 1.0,
            "token_refresh": 1.0,
        })
        self.assertNotIn("poll_interval", defaults)
        self.assertNotIn("request_interval", defaults)
        self.assertIn("poll_interval", schema["properties"])
        self.assertIn("request_interval", schema["properties"])
        self.assertNotIn("poll_interval", schema["required"])
        self.assertNotIn("request_interval", schema["required"])

        with_legacy = copy.deepcopy(defaults)
        with_legacy.update({"poll_interval": 10, "request_interval": 1})
        validator.validate(with_legacy)

        invalid_configs = []
        for workers in (0, 5, 1.5):
            invalid = copy.deepcopy(defaults)
            invalid["storage_read_workers"] = workers
            invalid_configs.append(invalid)
        missing_nested = copy.deepcopy(defaults)
        del missing_nested["endpoint_intervals"]["storage_read"]
        invalid_configs.append(missing_nested)
        extra_nested = copy.deepcopy(defaults)
        extra_nested["endpoint_intervals"]["future"] = 1
        invalid_configs.append(extra_nested)
        negative_interval = copy.deepcopy(defaults)
        negative_interval["endpoint_intervals"]["storage_read"] = -0.1
        invalid_configs.append(negative_interval)

        for invalid in invalid_configs:
            with self.subTest(invalid=invalid):
                self.assertTrue(list(validator.iter_errors(invalid)))

    def test_schema_declares_custom_config_command_registered_by_manifest(self):
        schema = yaml.safe_load((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text(encoding="utf-8"))
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertEqual(schema["x-telepiplex-config-command"], "config")
        path_pattern = schema["properties"]["save_directories"]["items"][
            "properties"
        ]["path"]["pattern"]
        for value in ("series/live action", "series/live action/"):
            self.assertIsNotNone(re.fullmatch(path_pattern, value))
        for value in ("/series", "/", "series//live", ".", "series/../live"):
            self.assertIsNone(re.fullmatch(path_pattern, value))
        commands = [item["name"] for item in manifest["commands"]]
        self.assertNotIn("config", commands)
        self.assertIn("auth", commands)
        self.assertEqual(manifest["version"], "1.0.20")
        self.assertEqual(manifest["host_api"], ">=1.6,<2.0")
        self.assertEqual(manifest["config_schema_version"], 1)
        self.assertEqual(manifest["state_schema_version"], 1)
        self.assertEqual(project["project"]["version"], "1.0.20")
        self.assertEqual(
            project["project"]["dependencies"][0],
            "telepiplex-plugin-sdk==1.3.2",
        )
        self.assertIn("/tmp/download-1.0.20.tpx", readme)
        self.assertNotIn("dist/download-1.0.20.tpx", readme)
        self.assertIn("逐条新增、编辑和删除", readme)
        self.assertIn("series/live action", readme)
        self.assertIn("单级目录", readme)
        self.assertIn("真人电影", readme)
        self.assertIn("不要以 / 开头", readme)

    def test_source_has_no_host_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    names = []
                forbidden.extend(
                    name for name in names
                    if name.split(".", 1)[0] in {"app", "init", "telegram"}
                )
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()

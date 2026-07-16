import ast
import asyncio
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from telepiplex_plex.feature import PlexFeature
from telepiplex_plex.jobs import PlexJobRepository
from telepiplex_plex.management import (
    PlexManagementService,
    PlexOperationCancelled,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeCore:
    def __init__(self):
        self.notifications = []
        self.reports = []

    async def notify_user(self, user_id, text, **kwargs):
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}

    async def report_operation(self, operation):
        self.reports.append(operation)
        return {"accepted": True, "revision": operation["revision"]}


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        self.tasks[task_id] = awaitable


class FakeService:
    def __init__(self, jobs):
        self.jobs = jobs
        self.runs = 0
        self.run_job_ids = []
        self.libraries = [
            {"id": "12", "title": "电影", "media_type": "movie"},
            {"id": "13", "title": "剧集", "media_type": "show"},
        ]
        self.list_library_calls = 0
        self.scan_requests = []
        self.scan_result = None
        self.selection_indexes = []
        self.confirmed_selections = []

    def enqueue_organized_event(self, payload):
        return self.jobs.create_or_get(
            str(payload.get("job_id") or "job"),
            {
                "user_id": payload.get("user_id"),
                "chat_id": payload.get("chat_id"),
                "resource_name": payload.get("resource_name") or "Movie",
                "final_path": payload.get("final_path"),
                "operation_id": payload.get("operation_id"),
                "operation_revision": payload.get("operation_revision"),
            },
        )

    def run_job(self, job_id, *, should_cancel=None, on_stage=None):
        for stage in ("scanning", "artwork", "audio", "subtitle"):
            if should_cancel and should_cancel():
                from telepiplex_plex.management import PlexOperationCancelled
                raise PlexOperationCancelled("cancelled")
            if on_stage:
                on_stage(stage, self.jobs.get(job_id))
        self.runs += 1
        self.run_job_ids.append(int(job_id))
        return self.jobs.update(job_id, state="completed")

    def list_jobs(self, limit=5):
        return self.jobs.list(limit)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def list_libraries(self):
        self.list_library_calls += 1
        return [dict(library) for library in self.libraries]

    def scan_libraries(self, library_ids=None, *, should_cancel=None):
        requested = (
            None
            if library_ids is None
            else [str(library_id) for library_id in library_ids]
        )
        self.scan_requests.append(requested)
        if self.scan_result is not None:
            return self.scan_result
        selected = self.libraries if requested is None else [
            library for library in self.libraries
            if str(library["id"]) in requested
        ]
        return {
            "succeeded": [dict(library) for library in selected],
            "failed": [],
        }

    def pending_selection(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        for name in ("scanning", "artwork", "audio", "subtitle"):
            waiting = (
                ((job or {}).get("step_results") or {}).get(name) or {}
            ).get("waiting")
            if isinstance(waiting, dict):
                return dict(waiting)
        return None

    def set_selection_index(self, job_id, index):
        job = self.jobs.get(job_id)
        waiting = self.pending_selection(job_id)
        candidates = list((waiting or {}).get("candidates") or [])
        index = int(index)
        if not waiting or index < 0 or index >= len(candidates):
            raise ValueError("selection index is out of range")
        steps = dict(job.get("step_results") or {})
        kind = str(waiting["kind"])
        step = dict(steps[kind])
        updated = dict(waiting)
        updated["candidate_index"] = index
        step["waiting"] = updated
        steps[kind] = step
        self.jobs.update(job_id, step_results=steps)
        self.selection_indexes.append((int(job_id), index))
        return updated

    def confirm_selection(
        self,
        job_id,
        index,
        *,
        should_cancel=None,
        on_stage=None,
    ):
        waiting = self.set_selection_index(job_id, index)
        self.confirmed_selections.append((int(job_id), int(index)))
        if on_stage:
            on_stage(str(waiting["kind"]), self.jobs.get(job_id))
        job = self.jobs.get(job_id)
        steps = dict(job.get("step_results") or {})
        step = dict(steps[str(waiting["kind"])])
        step["status"] = "success"
        step.pop("waiting", None)
        steps[str(waiting["kind"])] = step
        return self.jobs.update(
            job_id,
            state="completed",
            step_results=steps,
        )


class PlexFeatureRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = PlexJobRepository(self.root / "jobs.db")
        self.service = FakeService(self.jobs)
        self.runtime = FakeRuntime()
        self.feature = PlexFeature(
            config={},
            core=FakeCore(),
            state_path=self.root / "state",
            repository=self.jobs,
            service_factory=lambda: self.service,
        )
        self.feature.bind_runtime(self.runtime)

    async def asyncTearDown(self):
        for awaitable in self.runtime.tasks.values():
            if hasattr(awaitable, "close"):
                awaitable.close()
        self.temp.cleanup()

    def make_waiting_job(self, kind, candidates, *, candidate_index=0):
        job = self.jobs.create_or_get(
            f"waiting-{kind}-{len(self.jobs.list())}",
            {
                "user_id": 1,
                "chat_id": 10,
                "resource_name": f"{kind.title()} Choice",
            },
        )
        waiting = {
            "kind": kind,
            "target_id": "target-1",
            "rating_key": "42",
            "part_id": 11 if kind != "artwork" else 0,
            "candidates": candidates,
            "candidate_index": candidate_index,
        }
        return self.jobs.update(
            job["id"],
            state="awaiting_selection",
            step_results={
                kind: {
                    "status": "awaiting_selection",
                    "waiting": waiting,
                }
            },
        )

    async def test_duplicate_media_event_executes_job_once_and_completed_is_terminal(self):
        request = {"payload": {
            "job_id": "job-1",
            "user_id": 123,
            "resource_name": "Movie",
            "final_path": "/Movies/Movie",
        }}
        first = await self.feature.media_organized(request)
        second = await self.feature.media_organized(request)

        self.assertEqual(first["state"], "running")
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.runtime.tasks), 1)
        self.assertNotIn("job_ids", first)
        await self.runtime.tasks.pop("plex-job-job-1")
        third = await self.feature.media_organized(request)
        self.assertEqual(third["state"], "completed")
        self.assertEqual(self.service.runs, 1)
        self.assertEqual(self.service.run_job_ids, [first["job_id"]])

    async def test_media_event_accepts_chain_operation_and_reports_all_stages(self):
        request = {"payload": {
            "job_id": "job-chain",
            "user_id": 123,
            "chat_id": 10,
            "resource_name": "Movie",
            "final_path": "/Movies/Movie",
            "operation_id": "op-chain",
            "operation_revision": 20,
        }}

        accepted = await self.feature.media_organized(request)
        await self.runtime.tasks.pop("plex-job-job-chain")

        self.assertEqual(accepted["operation"]["operation_id"], "op-chain")
        self.assertEqual(accepted["operation"]["revision"], 21)
        stages = {report["stage"] for report in self.feature.core.reports}
        self.assertTrue({
            "scan_preparing", "scanning", "artwork", "audio",
            "subtitle", "completed",
        }.issubset(stages))
        self.assertEqual(self.feature.core.reports[-1]["state"], "completed")

    async def test_rejected_operation_claim_never_enqueues_or_scans(self):
        class RejectedCore(FakeCore):
            async def report_operation(self, operation):
                self.reports.append(operation)
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": operation["revision"],
                }

        feature = PlexFeature(
            config={}, core=RejectedCore(),
            state_path=self.root / "state-rejected",
            repository=self.jobs, service_factory=lambda: self.service,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        request = {"event_id": "rejected-event", "payload": {
            "job_id": "rejected-event",
            "final_path": "/Movies/Movie",
            "resource_name": "Movie",
            "user_id": 123,
            "chat_id": 10,
            "operation_id": "op-rejected",
            "operation_revision": 5,
        }}

        with self.assertRaisesRegex(Exception, "rejected"):
            await feature.media_organized(request)
        replay = await feature.media_organized(request)

        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["state"], "interrupted")
        self.assertEqual(self.jobs.list(), [])
        self.assertEqual(self.service.runs, 0)

    async def test_lost_response_retry_keeps_claimed_operation_running(self):
        request = {"event_id": "event-lost-response", "payload": {
            "job_id": "job-lost-response",
            "user_id": 123,
            "chat_id": 10,
            "resource_name": "Movie",
            "final_path": "/Movies/Movie",
            "operation_id": "op-lost-response",
            "operation_revision": 20,
        }}

        first = await self.feature.media_organized(request)
        retried = await self.feature.media_organized(request)

        self.assertEqual(first["state"], "running")
        self.assertTrue(retried["duplicate"])
        self.assertEqual(retried["state"], "running")
        self.assertEqual(retried["operation"]["state"], "running")
        self.assertEqual(retried["operation"]["revision"], 21)
        self.assertEqual(
            list(self.runtime.tasks), ["plex-job-event-lost-response"]
        )

        await self.runtime.tasks.pop("plex-job-event-lost-response")
        self.assertEqual(self.feature.core.reports[-1]["state"], "completed")

    async def test_media_event_service_start_failure_terminalizes_operation(self):
        core = FakeCore()
        feature = PlexFeature(
            config={},
            core=core,
            state_path=self.root / "failed-service",
            repository=self.jobs,
            service_factory=lambda: (_ for _ in ()).throw(
                RuntimeError("service unavailable")
            ),
        )
        feature.bind_runtime(FakeRuntime())

        with self.assertRaises(RuntimeError):
            await feature.media_organized({"payload": {
                "job_id": "job-service-failure",
                "user_id": 123,
                "chat_id": 10,
                "resource_name": "Movie",
                "final_path": "/Movies/Movie",
                "operation_id": "op-service-failure",
                "operation_revision": 4,
            }})

        self.assertEqual(core.reports[-1]["state"], "failed")
        self.assertIn("初始化失败", core.reports[-1]["status_text"])

    async def test_plex_non_numeric_argument_returns_usage_without_ai_task(self):
        result = await self.feature.command({
            "command": "plex",
            "args": ["刷新", "元数据"],
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(result["actions"][0]["text"], "用法：/plex [Job ID]")
        self.assertEqual(self.runtime.tasks, {})

    async def test_scan_menu_lists_live_libraries_and_scan_all(self):
        result = await self.feature.command({
            "command": "scan",
            "args": [],
            "chat_id": 10,
            "user_id": 1,
        })

        keyboard = result["actions"][0]["data"]["keyboard"]
        self.assertEqual(keyboard[0], [{
            "text": "扫描全部媒体库",
            "callback_data": "plex:scan:all",
        }])
        self.assertIn(
            {"text": "取消", "callback_data": "plex:scan:cancel"},
            keyboard[-1],
        )
        buttons = [button for row in keyboard for button in row]
        self.assertIn(
            {"text": "扫描全部媒体库", "callback_data": "plex:scan:all"},
            buttons,
        )
        self.assertIn(
            {"text": "电影", "callback_data": "plex:scan:12"},
            buttons,
        )
        self.assertIn(
            {"text": "剧集", "callback_data": "plex:scan:13"},
            buttons,
        )
        self.assertEqual(self.service.list_library_calls, 1)
        self.assertLessEqual(len(keyboard), 10)
        self.assertTrue(all(
            len(button["callback_data"].encode("utf-8")) <= 64
            for button in buttons
        ))

        cancelled = await self.feature.callback({
            "payload": "scan:cancel",
            "chat_id": 10,
            "user_id": 1,
        })
        self.assertEqual(
            cancelled["actions"][0]["text"],
            "已取消 Plex 扫描选择。",
        )
        self.assertEqual(self.jobs.list(), [])
        self.assertEqual(self.runtime.tasks, {})

    async def test_scan_menu_paginates_eight_libraries_per_page(self):
        self.service.libraries = [
            {"id": str(index), "title": f"媒体库 {index}"}
            for index in range(1, 10)
        ]

        first = await self.feature.command({
            "command": "scan",
            "chat_id": 10,
            "user_id": 1,
        })
        first_buttons = [
            button
            for row in first["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(
            len([
                button for button in first_buttons
                if button["callback_data"].startswith("plex:scan:")
                and button["callback_data"].count(":") == 2
                and button["callback_data"].rsplit(":", 1)[-1].isdigit()
            ]),
            8,
        )
        self.assertIn(
            {"text": "下一页", "callback_data": "plex:scan:page:1"},
            first_buttons,
        )
        self.assertIn(
            {"text": "取消", "callback_data": "plex:scan:cancel"},
            first["actions"][0]["data"]["keyboard"][-1],
        )
        self.assertLessEqual(
            len(first["actions"][0]["data"]["keyboard"]),
            10,
        )

        second = await self.feature.callback({
            "payload": "scan:page:1",
            "chat_id": 10,
            "user_id": 1,
        })
        second_buttons = [
            button
            for row in second["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertIn(
            {"text": "媒体库 9", "callback_data": "plex:scan:9"},
            second_buttons,
        )
        self.assertIn(
            {"text": "取消", "callback_data": "plex:scan:cancel"},
            second["actions"][0]["data"]["keyboard"][-1],
        )
        self.assertEqual(self.service.list_library_calls, 2)

    async def test_scan_selection_validates_fresh_list_and_scans_only_that_id(self):
        before_jobs = self.jobs.list()

        accepted = await self.feature.callback({
            "payload": "scan:12",
            "chat_id": 10,
            "user_id": 1,
        })
        task_id = f"plex-scan-{accepted['operation']['operation_id']}"
        await self.runtime.tasks.pop(task_id)

        self.assertEqual(self.service.scan_requests, [["12"]])
        self.assertEqual(self.jobs.list(), before_jobs)
        self.assertEqual(self.feature.core.reports[-1]["state"], "completed")
        self.assertIn("电影", self.feature.core.reports[-1]["status_text"])

    async def test_scan_rejects_library_removed_from_fresh_list(self):
        await self.feature.command({
            "command": "scan",
            "chat_id": 10,
            "user_id": 1,
        })
        self.service.libraries = [{"id": "13", "title": "剧集"}]

        result = await self.feature.callback({
            "payload": "scan:12",
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertIn("媒体库列表已变化", result["actions"][0]["text"])
        self.assertEqual(self.service.scan_requests, [])
        self.assertEqual(self.runtime.tasks, {})
        self.assertEqual(self.service.list_library_calls, 2)

    async def test_scan_all_reports_successes_and_failures(self):
        self.service.scan_result = {
            "succeeded": [{"id": "12", "title": "电影"}],
            "failed": [{
                "id": "13",
                "title": "剧集",
                "error": "scan unavailable",
            }],
        }

        accepted = await self.feature.callback({
            "payload": "scan:all",
            "chat_id": 10,
            "user_id": 1,
        })
        await self.runtime.tasks.pop(
            f"plex-scan-{accepted['operation']['operation_id']}"
        )

        report = self.feature.core.reports[-1]
        self.assertEqual(self.service.scan_requests, [None])
        self.assertEqual(report["state"], "completed")
        self.assertIn("电影", report["status_text"])
        self.assertIn("剧集", report["status_text"])
        self.assertIn("失败", report["status_text"])

    async def test_plex_numeric_reopens_artwork_as_photo_carousel(self):
        job = self.make_waiting_job("artwork", [
            {"url": "https://image.example/one.jpg", "source": "tmdb"},
            {"url": "https://image.example/two.jpg", "source": "fanart"},
        ])

        result = await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })

        action = result["actions"][0]
        self.assertEqual(action["kind"], "send_photo")
        self.assertEqual(
            action["data"]["photo_url"],
            "https://image.example/one.jpg",
        )
        self.assertEqual(
            [button["text"] for button in action["data"]["keyboard"][0]],
            ["上一张", "选择此海报", "下一张"],
        )
        self.assertEqual(result["operation"]["state"], "awaiting_input")

        next_result = await self.feature.callback({
            "payload": f"choice:{job['id']}:next",
            "chat_id": 10,
            "user_id": 1,
        })
        self.assertEqual(
            next_result["actions"][0]["data"]["photo_url"],
            "https://image.example/two.jpg",
        )
        self.assertEqual(self.service.selection_indexes[-1], (job["id"], 1))

    async def test_audio_and_subtitle_waiting_use_labeled_candidate_buttons(self):
        cases = (
            (
                "audio",
                [
                    {
                        "id": 21,
                        "language_code": "jpn",
                        "codec": "truehd",
                        "channels": 8,
                    },
                    {
                        "id": 22,
                        "language_code": "jpn",
                        "codec": "dts",
                        "channels": 6,
                    },
                ],
                ("#21", "jpn", "TRUEHD"),
            ),
            (
                "subtitle",
                [
                    {
                        "id": 31,
                        "language_code": "chi",
                        "title": "简体中文",
                        "external": True,
                    },
                    {
                        "id": 32,
                        "language_code": "chi",
                        "title": "繁体中文",
                        "external": False,
                    },
                ],
                ("#31", "简体中文", "外挂"),
            ),
        )
        for kind, candidates, expected_parts in cases:
            with self.subTest(kind=kind):
                job = self.make_waiting_job(kind, candidates)
                result = await self.feature.command({
                    "command": "plex",
                    "args": [str(job["id"])],
                    "chat_id": 10,
                    "user_id": 1,
                })

                action = result["actions"][0]
                self.assertEqual(action["kind"], "send_message")
                labels = [
                    button["text"]
                    for row in action["data"]["keyboard"]
                    for button in row
                    if ":pick:" in button["callback_data"]
                ]
                self.assertTrue(all(
                    part in labels[0] for part in expected_parts
                ))
                self.assertTrue(all(
                    button["callback_data"].startswith(
                        f"plex:choice:{job['id']}:pick:"
                    )
                    and len(button["callback_data"].encode("utf-8")) <= 64
                    for row in action["data"]["keyboard"]
                    for button in row
                    if ":pick:" in button["callback_data"]
                ))
                self.assertIn(
                    {"text": "取消", "callback_data": "plex:cancel"},
                    action["data"]["keyboard"][-1],
                )

    async def test_audio_and_subtitle_candidates_paginate_with_absolute_indexes(self):
        cases = (
            (
                "audio",
                [{
                    "id": 100 + index,
                    "display_title": f"Japanese Track {index + 1}",
                    "codec": "truehd",
                    "channels": 8,
                    "bitrate": 4000 + index,
                } for index in range(18)],
            ),
            (
                "subtitle",
                [{
                    "id": 200 + index,
                    "display_title": f"Chinese Subtitle {index + 1}",
                    "language_code": "chi",
                    "external": index % 2 == 0,
                } for index in range(18)],
            ),
        )
        for kind, candidates in cases:
            with self.subTest(kind=kind):
                job = self.make_waiting_job(kind, candidates)
                opened = await self.feature.command({
                    "command": "plex",
                    "args": [str(job["id"])],
                    "chat_id": 10,
                    "user_id": 1,
                })
                first_keyboard = opened["actions"][0]["data"]["keyboard"]
                first_picks = [
                    button
                    for row in first_keyboard
                    for button in row
                    if ":pick:" in button["callback_data"]
                ]
                self.assertEqual(
                    [button["callback_data"].rsplit(":", 1)[-1]
                     for button in first_picks],
                    [str(index) for index in range(8)],
                )
                self.assertIn(
                    {"text": "下一页", "callback_data": (
                        f"plex:choice:{job['id']}:next"
                    )},
                    first_keyboard[-1],
                )
                self.assertIn(
                    {"text": "取消", "callback_data": "plex:cancel"},
                    first_keyboard[-1],
                )
                if kind == "audio":
                    label = first_picks[0]["text"]
                    self.assertIn("Japanese Track 1", label)
                    self.assertIn("TRUEHD", label)
                    self.assertIn("8ch", label)
                    self.assertIn("4000kbps", label)

                second = await self.feature.callback({
                    "payload": f"choice:{job['id']}:next",
                    "chat_id": 10,
                    "user_id": 1,
                })
                second_keyboard = second["actions"][0]["data"]["keyboard"]
                second_picks = [
                    button
                    for row in second_keyboard
                    for button in row
                    if ":pick:" in button["callback_data"]
                ]
                self.assertEqual(
                    [button["callback_data"].rsplit(":", 1)[-1]
                     for button in second_picks],
                    [str(index) for index in range(8, 16)],
                )
                self.assertTrue(all(
                    len(button["callback_data"].encode("utf-8")) <= 64
                    for row in second_keyboard
                    for button in row
                ))

                accepted = await self.feature.callback({
                    "payload": f"choice:{job['id']}:pick:12",
                    "chat_id": 10,
                    "user_id": 1,
                })
                await self.runtime.tasks.pop(f"plex-choice-{job['id']}")
                self.assertEqual(
                    accepted["operation"]["operation_id"],
                    opened["operation"]["operation_id"],
                )
                self.assertEqual(
                    self.service.confirmed_selections[-1],
                    (job["id"], 12),
                )

    async def test_pending_selection_cancel_persists_job_and_discloses_no_rollback(self):
        job = self.make_waiting_job("audio", [{
            "id": 21,
            "display_title": "Japanese TrueHD",
            "codec": "truehd",
            "channels": 8,
            "bitrate": 4000,
        }])
        service = PlexManagementService(self.jobs, SimpleNamespace())
        self.feature.service = service
        opened = await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(opened["operation"]["control"], "cancel")
        self.assertIn(
            {"text": "取消", "callback_data": "plex:cancel"},
            opened["actions"][0]["data"]["keyboard"][-1],
        )

        cancelled = await self.feature.callback({
            "payload": "cancel",
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(self.jobs.get(job["id"])["state"], "cancelled")
        text = cancelled["operation"]["status_text"]
        self.assertIn("后续步骤不会继续", text)
        self.assertIn("扫描、海报和音轨/字幕写入", text)
        self.assertIn("不会自动回滚", text)
        self.assertEqual(service.resume_incomplete_jobs(), 0)
        self.assertEqual(self.jobs.get(job["id"])["state"], "cancelled")

    async def test_artwork_cancel_uses_photo_compatible_feedback(self):
        job = self.make_waiting_job("artwork", [{
            "url": "https://image.example/poster.jpg",
            "source": "tmdb",
        }])
        self.feature.service = PlexManagementService(
            self.jobs,
            SimpleNamespace(),
        )
        await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })

        cancelled = await self.feature.callback({
            "payload": "cancel",
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(cancelled["actions"][0]["kind"], "edit_photo")
        self.assertEqual(
            cancelled["actions"][0]["data"]["photo_url"],
            "https://image.example/poster.jpg",
        )
        self.assertEqual(self.jobs.get(job["id"])["state"], "cancelled")

    async def test_choice_cancel_before_worker_skips_write_and_cancels_job(self):
        class RecordingPlex:
            def __init__(nested_self):
                nested_self.poster_updates = []

            def set_poster_url(nested_self, rating_key, url):
                nested_self.poster_updates.append((rating_key, url))

        plex = RecordingPlex()
        job = self.make_waiting_job("artwork", [{
            "url": "https://image.example/poster.jpg",
            "source": "tmdb",
        }])
        self.feature.service = PlexManagementService(self.jobs, plex)
        opened = await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })

        await self.feature.callback({
            "payload": f"choice:{job['id']}:pick:0",
            "chat_id": 10,
            "user_id": 1,
        })
        cancelled = await self.feature.callback({
            "payload": "cancel",
            "chat_id": 10,
            "user_id": 1,
        })
        await self.runtime.tasks.pop(f"plex-choice-{job['id']}")

        self.assertEqual(plex.poster_updates, [])
        self.assertEqual(self.jobs.get(job["id"])["state"], "cancelled")
        self.assertEqual(
            cancelled["operation"]["operation_id"],
            opened["operation"]["operation_id"],
        )
        self.assertEqual(cancelled["operation"]["state"], "cancelled")
        self.assertIn(
            "不会自动回滚",
            cancelled["operation"]["status_text"],
        )

    async def test_confirm_selection_checks_cancel_before_any_plex_mutation(self):
        class RecordingPlex:
            def __init__(nested_self):
                nested_self.calls = []

            def set_poster_url(nested_self, rating_key, url):
                nested_self.calls.append(("artwork", rating_key, url))

            def select_audio(
                nested_self,
                rating_key,
                part_id,
                stream_id,
            ):
                nested_self.calls.append(
                    ("audio", rating_key, part_id, stream_id)
                )

            def select_subtitle(
                nested_self,
                rating_key,
                part_id,
                stream_id,
            ):
                nested_self.calls.append(
                    ("subtitle", rating_key, part_id, stream_id)
                )

        candidates = {
            "artwork": {"url": "https://image.example/poster.jpg"},
            "audio": {"id": 21},
            "subtitle": {"id": 31},
        }
        for kind, candidate in candidates.items():
            with self.subTest(kind=kind):
                plex = RecordingPlex()
                job = self.make_waiting_job(kind, [candidate])
                service = PlexManagementService(self.jobs, plex)

                with self.assertRaises(PlexOperationCancelled):
                    service.confirm_selection(
                        job["id"],
                        0,
                        should_cancel=lambda: True,
                    )

                self.assertEqual(plex.calls, [])

    async def test_choice_cancel_after_remote_write_cancels_job_without_rollback_claim(self):
        class CancellingPlex:
            def __init__(nested_self):
                nested_self.poster_updates = []
                nested_self.on_write = None

            def set_poster_url(nested_self, rating_key, url):
                nested_self.poster_updates.append((rating_key, url))
                nested_self.on_write()

        plex = CancellingPlex()
        job = self.make_waiting_job("artwork", [{
            "url": "https://image.example/poster.jpg",
            "source": "tmdb",
        }])
        self.feature.service = PlexManagementService(self.jobs, plex)
        opened = await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })
        operation_id = opened["operation"]["operation_id"]
        plex.on_write = (
            lambda: self.feature.operations[operation_id][
                "cancel_event"
            ].set()
        )

        await self.feature.callback({
            "payload": f"choice:{job['id']}:pick:0",
            "chat_id": 10,
            "user_id": 1,
        })
        await self.runtime.tasks.pop(f"plex-choice-{job['id']}")

        self.assertEqual(
            plex.poster_updates,
            [("42", "https://image.example/poster.jpg")],
        )
        self.assertEqual(self.jobs.get(job["id"])["state"], "cancelled")
        report = self.feature.core.reports[-1]
        self.assertEqual(report["operation_id"], operation_id)
        self.assertEqual(report["state"], "cancelled")
        self.assertIn("不会自动回滚", report["status_text"])

    async def test_choice_pick_confirms_and_continues_same_operation(self):
        job = self.make_waiting_job("artwork", [
            {"url": "https://image.example/one.jpg", "source": "tmdb"},
            {"url": "https://image.example/two.jpg", "source": "fanart"},
        ])
        opened = await self.feature.command({
            "command": "plex",
            "args": [str(job["id"])],
            "chat_id": 10,
            "user_id": 1,
        })
        operation_id = opened["operation"]["operation_id"]

        accepted = await self.feature.callback({
            "payload": f"choice:{job['id']}:pick:1",
            "chat_id": 10,
            "user_id": 1,
        })
        await self.runtime.tasks.pop(f"plex-choice-{job['id']}")

        self.assertEqual(accepted["operation"]["operation_id"], operation_id)
        self.assertEqual(
            self.service.confirmed_selections,
            [(job["id"], 1)],
        )
        self.assertEqual(
            self.feature.core.reports[-1]["operation_id"],
            operation_id,
        )
        self.assertEqual(self.feature.core.reports[-1]["state"], "completed")

    async def test_stale_choice_callback_returns_expired_feedback(self):
        result = await self.feature.callback({
            "payload": "choice:999:pick:0",
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(
            result["actions"][0]["text"],
            "⚠️ Plex 选择已失效。",
        )

    async def test_media_job_waiting_selection_reports_photo_choice(self):
        class WaitingService(FakeService):
            def run_job(
                nested_self,
                job_id,
                *,
                should_cancel=None,
                on_stage=None,
            ):
                waiting = {
                    "kind": "artwork",
                    "target_id": "target-1",
                    "rating_key": "42",
                    "part_id": 0,
                    "candidates": [
                        {"url": "https://image.example/one.jpg"},
                        {"url": "https://image.example/two.jpg"},
                    ],
                    "candidate_index": 0,
                }
                return nested_self.jobs.update(
                    job_id,
                    state="awaiting_selection",
                    step_results={
                        "artwork": {
                            "status": "awaiting_selection",
                            "waiting": waiting,
                        }
                    },
                )

        service = WaitingService(self.jobs)
        self.feature.service = service
        accepted = await self.feature.media_organized({"payload": {
            "job_id": "job-waiting",
            "user_id": 1,
            "chat_id": 10,
            "resource_name": "Movie",
            "final_path": "/Movies/Movie",
            "operation_id": "op-waiting",
            "operation_revision": 1,
        }})
        await self.runtime.tasks.pop("plex-job-job-waiting")

        report = self.feature.core.reports[-1]
        self.assertEqual(report["operation_id"], accepted["operation_id"])
        self.assertEqual(report["state"], "awaiting_input")
        self.assertEqual(
            report["details"]["photo_url"],
            "https://image.example/one.jpg",
        )
        self.assertEqual(
            [button["text"] for button in report["details"]["keyboard"][0]],
            ["上一张", "选择此海报", "下一张"],
        )

    async def test_obsolete_match_callback_is_rejected(self):
        self.feature.service = self.service

        result = await self.feature.callback({
            "payload": "match:1:0",
            "chat_id": 10,
            "user_id": 1,
        })

        self.assertEqual(
            result["actions"][0]["text"],
            "⚠️ Plex callback 无效。",
        )

    async def test_cancelled_job_stops_after_current_plex_step(self):
        from telepiplex_plex.management import PlexOperationCancelled

        started = threading.Event()

        class BlockingService(FakeService):
            def run_job(self, job_id, *, should_cancel=None, on_stage=None):
                on_stage("scanning", self.jobs.get(job_id))
                started.set()
                while not should_cancel():
                    threading.Event().wait(0.01)
                raise PlexOperationCancelled("cancelled")

        service = BlockingService(self.jobs)
        self.feature.service = service

        class TaskRuntime:
            def spawn(self, awaitable, *, task_id):
                return asyncio.create_task(awaitable, name=task_id)

        self.feature.runtime = TaskRuntime()
        accepted = await self.feature.media_organized({"payload": {
            "job_id": "job-cancel", "user_id": 123, "chat_id": 10,
            "resource_name": "Movie", "final_path": "/Movies/Movie",
            "operation_id": "op-cancel", "operation_revision": 2,
        }})
        task = self.feature.operations["op-cancel"]["task"]
        self.assertTrue(await asyncio.to_thread(started.wait, 1))

        cancelling = await self.feature.operation_control({
            "operation_id": "op-cancel", "action": "cancel",
            "revision": accepted["operation"]["revision"],
        })
        await task

        self.assertEqual(cancelling["operation"]["state"], "cancelling")
        self.assertEqual(self.feature.core.reports[-1]["state"], "cancelled")

    async def test_in_progress_job_is_marked_interrupted_then_resumed(self):
        job = self.jobs.create_or_get("old", {"final_path": "/Movies/Old"})
        self.jobs.update(job["id"], state="scanning")
        feature = PlexFeature(
            config={}, core=FakeCore(), state_path=self.root / "state2",
            repository=self.jobs, service_factory=lambda: self.service,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")

        await runtime.tasks.pop("plex-resume")
        await runtime.tasks.pop(f"plex-resume-{job['id']}")
        self.assertEqual(self.jobs.get(job["id"])["state"], "completed")

    async def test_restart_interrupts_new_and_legacy_active_states(self):
        states = (
            "running", "scanning", "artwork", "audio", "subtitle",
            "locating", "matching", "localizing", "streams",
        )
        jobs = []
        for index, state in enumerate(states):
            job = self.jobs.create_or_get(
                f"active-{index}",
                {"final_path": f"/Movies/{index}"},
            )
            self.jobs.update(job["id"], state=state)
            jobs.append(job)

        interrupted = self.jobs.mark_incomplete_interrupted()

        self.assertEqual(interrupted, [job["id"] for job in jobs])
        self.assertTrue(all(
            self.jobs.get(job["id"])["state"] == "interrupted"
            for job in jobs
        ))

    async def test_coordinated_interrupted_job_is_reported_not_auto_resumed(self):
        job = self.jobs.create_or_get("coordinated-old", {
            "final_path": "/Movies/Old",
            "user_id": 123,
            "chat_id": 10,
            "operation_id": "op-interrupted",
            "operation_revision": 8,
        })
        self.jobs.update(job["id"], state="scanning")
        core = FakeCore()
        feature = PlexFeature(
            config={}, core=core, state_path=self.root / "state-coordinated",
            repository=self.jobs, service_factory=lambda: self.service,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await runtime.tasks.pop("plex-resume")

        self.assertNotIn("plex-resume-batch", runtime.tasks)
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")
        self.assertEqual(core.reports[-1]["state"], "interrupted")
        self.assertIn("进程停止", core.reports[-1]["status_text"])

    async def test_coordinated_interruption_reports_before_service_initializes(self):
        job = self.jobs.create_or_get("coordinated-broken-service", {
            "final_path": "/Movies/Old",
            "user_id": 123,
            "chat_id": 10,
            "operation_id": "op-broken-service",
            "operation_revision": 4,
        })
        self.jobs.update(job["id"], state="scanning")
        core = FakeCore()
        feature = PlexFeature(
            config={},
            core=core,
            state_path=self.root / "state-broken-service",
            repository=self.jobs,
            service_factory=lambda: (_ for _ in ()).throw(
                RuntimeError("service unavailable")
            ),
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await runtime.tasks.pop("plex-resume")

        self.assertEqual(core.reports[-1]["state"], "interrupted")
        self.assertEqual(core.reports[-1]["operation_id"], "op-broken-service")
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")

    async def test_pending_event_replay_cannot_restart_interrupted_operation(self):
        payload = {
            "job_id": "same-event",
            "final_path": "/Movies/Old",
            "resource_name": "Old",
            "user_id": 123,
            "chat_id": 10,
            "operation_id": "op-replay-interrupted",
            "operation_revision": 8,
        }
        job = self.service.enqueue_organized_event(payload)
        self.jobs.update(job["id"], state="scanning")
        core = FakeCore()
        feature = PlexFeature(
            config={}, core=core, state_path=self.root / "state-replay",
            repository=self.jobs, service_factory=lambda: self.service,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        self.assertEqual(
            feature.operations["op-replay-interrupted"]["state"],
            "interrupted",
        )

        replay = await feature.media_organized({
            "event_id": "same-event",
            "payload": payload,
        })

        self.assertTrue(replay["accepted"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["state"], "interrupted")
        self.assertEqual(replay["operation"]["state"], "interrupted")
        self.assertNotIn("plex-job-same-event", runtime.tasks)
        self.assertEqual(self.jobs.get(job["id"])["state"], "interrupted")
        self.assertEqual(self.service.runs, 0)
        await runtime.tasks.pop("plex-resume")

    async def test_enabled_ai_with_missing_credentials_does_not_break_feature_startup(self):
        from telepiplex_plex.runtime import main

        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "plex": {"base_url": "", "token": ""},
            "ai": {"enabled": True, "api_url": "", "api_key": "", "model": ""},
        }), encoding="utf-8")
        context = SimpleNamespace(
            manifest={"plugin_id": "plex-management", "version": "1.0.0"},
            token="token",
            core=FakeCore(),
            config_path=config_path,
            state_path=self.root / "runtime-state",
        )
        runtime = main(context)
        self.assertEqual(runtime.state, "starting")
        self.assertIn("media.organized", runtime.events)
        self.assertIn("scan", runtime.commands)

    async def test_management_capability_is_read_only_and_whitelisted(self):
        job = self.jobs.create_or_get("visible", {"final_path": "/Movies/Visible"})

        status = await self.feature.management_capability({
            "method": "get_job",
            "payload": {"job_id": job["id"]},
        })
        listing = await self.feature.management_capability({
            "method": "list_jobs",
            "payload": {"limit": 1},
        })

        self.assertEqual(status["job"]["id"], job["id"])
        self.assertEqual(listing["jobs"][0]["id"], job["id"])
        with self.assertRaises(ValueError):
            await self.feature.management_capability({"method": "run_job", "payload": {}})


class FeatureSourceContractTest(unittest.TestCase):
    def test_manifest_registers_scan_command(self):
        manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
        commands = {
            command["name"]: command["description"]
            for command in manifest["commands"]
        }

        self.assertEqual(commands["scan"], "扫描 Plex 媒体库")
        self.assertEqual(manifest["core_api"], ">=1.2,<2.0")

    def test_readme_build_example_uses_current_version(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("dist/plex-management-1.1.0.tpx", source)
        self.assertNotIn("dist/plex-management-1.0.0.tpx", source)

    def test_mcp_uses_auth_token_config_key(self):
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text())

        self.assertIn("auth_token", config["mcp"])
        self.assertNotIn("api_key", config["mcp"])

    def test_source_has_no_core_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                forbidden.extend(name for name in names if name.split(".", 1)[0] in {"app", "init", "telegram"})
        self.assertEqual(forbidden, [])


class PlexManualScanServiceTest(unittest.TestCase):
    def test_scan_libraries_continues_after_failure(self):
        class ScanPlex:
            def __init__(self):
                self.calls = []

            def list_libraries(self):
                return [
                    {"id": "12", "title": "电影"},
                    {"id": "13", "title": "剧集"},
                ]

            def scan_library(self, library_id):
                self.calls.append(str(library_id))
                if str(library_id) == "12":
                    raise RuntimeError("movie scan failed")

        with tempfile.TemporaryDirectory() as temp:
            jobs = PlexJobRepository(Path(temp) / "jobs.db")
            plex = ScanPlex()
            service = PlexManagementService(jobs, plex)

            result = service.scan_libraries()

        self.assertEqual(plex.calls, ["12", "13"])
        self.assertEqual(
            [library["id"] for library in result["succeeded"]],
            ["13"],
        )
        self.assertEqual(
            [library["id"] for library in result["failed"]],
            ["12"],
        )

    def test_scan_libraries_observes_cancel_after_failed_call(self):
        cancelled = False

        class ScanPlex:
            def list_libraries(self):
                return [{"id": "12", "title": "电影"}]

            def scan_library(self, _library_id):
                nonlocal cancelled
                cancelled = True
                raise RuntimeError("scan failed while cancellation arrived")

        with tempfile.TemporaryDirectory() as temp:
            service = PlexManagementService(
                PlexJobRepository(Path(temp) / "jobs.db"),
                ScanPlex(),
            )

            with self.assertRaises(PlexOperationCancelled):
                service.scan_libraries(
                    ["12"],
                    should_cancel=lambda: cancelled,
                )


if __name__ == "__main__":
    unittest.main()

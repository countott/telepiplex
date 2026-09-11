import asyncio
from copy import deepcopy
import unittest
import threading
from unittest.mock import Mock, patch

import requests

from telepiplex_plugin_sdk import FeatureError
from telepiplex_search.adapters import prowlarr
from telepiplex_search.context import runtime_context
from telepiplex_search.service import SearchFeature

from tests.test_feature_service import FakeHost, FakeRuntime


def release(number, **overrides):
    return {
        "title": f"Release.{number:02d}.S00.CAM",
        "magnetUrl": f"magnet:?xt=urn:btih:{number:040x}",
        "protocol": "torrent",
        "indexer": "Example",
        "indexerId": 7,
        "guid": f"release-{number}",
        "size": number * 1024**3,
        "seeders": number,
        "leechers": 2,
        "publishDate": f"2026-09-{number:02d}T12:00:00Z",
        "ageMinutes": 1000 - number,
        "sortTitle": f"release {number:02d}",
        "grabs": 100 - number,
        "files": number * 2,
        "categories": [{"id": 5000, "name": "TV"}],
    } | overrides


class RawSearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {
            "category_folder": [{
                "kind": "live_action_series", "name": "剧集", "path": "/剧集",
            }],
            "search": {"prowlarr": {
                "base_url": "http://prowlarr:9696", "api_key": "secret",
                "indexer_ids": "7,9", "categories": {"movie": 2000, "tv": 5000},
                "result_limit": 2,
            }},
        }
        self.addCleanup(runtime_context.configure, runtime_context.config)
        runtime_context.configure(self.config)
        self.host = FakeHost()
        self.runtime = FakeRuntime()
        self.feature = SearchFeature(config=self.config, host=self.host)
        self.feature.bind_runtime(self.runtime)
        self.responses = [release(n) for n in range(1, 14)]
        self.requests = []

        def get(url, **kwargs):
            self.requests.append((url, deepcopy(kwargs)))
            return Mock(json=lambda: deepcopy(self.responses))

        self.http = patch.object(prowlarr.requests, "get", side_effect=get)
        self.http.start()
        self.addCleanup(self.http.stop)

    async def asyncTearDown(self):
        for worker in self.runtime.tasks.values():
            worker.close()

    async def start(self, query='"Some  Title" S00 -CAM'):
        response = await self.feature.command({
            "command": "pr", "args": query.split(),
            "text": f"/pr@my_bot {query}", "chat_id": 10, "user_id": 1,
        })
        self.operation_id = response["operation"]["operation_id"]
        self.assertEqual(response["operation"]["segment"]["role"], "search")
        await self.runtime.run("search-raw-")
        self.plan_id = self.feature.operations[self.operation_id]["plan_id"]
        return self.host.reports[-1]

    async def click(self, payload, user_id=1):
        return await self.feature.callback({
            "payload": payload.removeprefix("search:"),
            "chat_id": 10, "user_id": user_id,
        })

    def buttons(self, report):
        return [b for row in report["details"]["keyboard"] for b in row]

    async def test_raw_query_preserves_text_and_has_no_media_category_or_metadata_lookup(self):
        query = '"Some  Title"\nS00 -CAM & x=y'
        report = await self.start(query)
        self.assertEqual(len(self.requests), 1)
        url, kwargs = self.requests[0]
        self.assertEqual(url, "http://prowlarr:9696/api/v1/search")
        self.assertEqual(kwargs["params"]["query"], query)
        self.assertEqual(kwargs["params"]["type"], "search")
        self.assertEqual(kwargs["params"]["indexerIds"], "7,9")
        self.assertNotIn("categories", kwargs["params"])
        self.assertNotIn("sortKey", kwargs["params"])
        self.assertEqual(report["state"], "awaiting_input")
        self.assertIn("Release.13.S00.CAM", report["status_text"])
        self.assertEqual(len(self.feature.plans[self.plan_id]["results"]), 13)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.host.milestones, [])

    async def test_empty_command_opens_raw_input_and_preserves_followup_text(self):
        response = await self.feature.command({"command": "pr", "chat_id": 10, "user_id": 1})
        self.assertEqual(response["session"]["state"], "open")
        self.assertEqual(response["operation"]["segment"]["role"], "search")
        await self.feature.message({"text": "A  B: C", "chat_id": 10, "user_id": 1})
        await self.runtime.run("search-raw-")
        self.assertEqual(self.requests[0][1]["params"]["query"], "A  B: C")
        self.assertEqual(len(self.feature.operations), 1)

    async def test_sort_and_page_use_cached_results_and_stable_release_selection(self):
        report = await self.start()
        original = next(b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"])
        result = await self.click(f"search:raw_sort:{self.plan_id}:size")
        text = result["operation"]["status_text"]
        self.assertLess(text.index("Release.01"), text.index("Release.02"))
        result = await self.click(f"search:raw_sort:{self.plan_id}:size")
        self.assertIn("Release.13", result["operation"]["status_text"])
        result = await self.click(f"search:raw_page:{self.plan_id}:1")
        self.assertIn("Release.08", result["operation"]["status_text"])
        self.assertNotIn("Release.13", result["operation"]["status_text"])
        self.assertEqual(len(self.requests), 1)
        result = await self.click(original)
        self.assertEqual(result["operation"]["stage"], "raw_destination")
        self.assertIn("Release.13", result["operation"]["status_text"])
        self.assertEqual(result["operation"]["segment"]["role"], "search")
        self.assertEqual(self.host.calls, [])
        await self.click(f"search:raw_path:{self.plan_id}:0")
        await self.click(f"search:raw_path:{self.plan_id}:0")
        await self.runtime.run("search-submit-")
        self.assertEqual(len(self.host.calls), 1)
        capability, method, payload, options = self.host.calls[0]
        self.assertEqual((capability, method), ("download.provider", "submit"))
        self.assertEqual(payload["selected_path"], "/剧集")
        self.assertEqual(payload["release"]["title"], "Release.13.S00.CAM")
        self.assertNotIn("media_metadata", payload)
        self.assertNotIn("naming_metadata", payload)
        self.assertEqual(payload["operation_id"], self.operation_id)
        self.assertTrue(options["idempotency_key"].startswith(self.plan_id + ":release:"))
        self.assertEqual(self.feature.operations[self.operation_id]["state"], "handed_off")

    async def test_peers_sort_prioritizes_seeders_then_leechers(self):
        self.responses = [release(1, seeders=3, leechers=5), release(2, seeders=2, leechers=500), release(3, seeders=3, leechers=1)]
        await self.start()
        await self.click(f"search:raw_sort:{self.plan_id}:peers")
        result = await self.click(f"search:raw_sort:{self.plan_id}:peers")
        text = result["operation"]["status_text"]
        self.assertLess(text.index("Release.01"), text.index("Release.03"))
        self.assertLess(text.index("Release.03"), text.index("Release.02"))

    async def test_same_magnet_from_two_indexers_stays_separately_selectable(self):
        self.responses = [release(1), release(1, indexer="Second", indexerId=9)]
        report = await self.start()
        callbacks = [b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"]]
        self.assertEqual(len(set(callbacks)), 2)
        self.assertTrue(all(len(x.encode()) <= 64 for x in callbacks))

    async def test_foreign_owner_and_invalid_navigation_cannot_submit(self):
        report = await self.start()
        callback = next(b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"])
        result = await self.click(callback, user_id=2)
        self.assertEqual(result["session"]["state"], "close")
        for tail in ("raw_path:{id}:0", "raw_page:{id}:-1", "raw_page:{id}:500", "raw_sort:{id}:unknown", "confirm:{id}"):
            with self.assertRaises(FeatureError):
                await self.click("search:" + tail.format(id=self.plan_id))
        self.assertEqual(self.host.calls, [])

    async def test_unresolvable_release_returns_to_raw_results(self):
        report = await self.start()
        self.feature.release_resolver = lambda item: ""
        callback = next(b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"])
        await self.click(callback)
        await self.click(f"search:raw_path:{self.plan_id}:0")
        await self.runtime.run("search-submit-")
        report = self.host.reports[-1]
        self.assertEqual(report["state"], "awaiting_input")
        self.assertIn("Release.12", report["status_text"])
        self.assertNotIn("Release.13", report["status_text"])
        self.assertTrue(any(":raw_sort:" in b["callback_data"] for b in self.buttons(report)))
        self.assertEqual(self.host.calls, [])

    async def test_no_results_closes_operation(self):
        self.responses = []
        report = await self.start()
        self.assertEqual(report["state"], "completed")
        self.assertNotIn(self.plan_id, self.feature.plans)
        self.assertEqual(self.host.calls, [])

    async def test_unsupported_protocol_is_visible_but_cannot_be_submitted(self):
        self.responses = [release(1, protocol="usenet", magnetUrl="", downloadUrl="https://example.invalid/nzb")]
        report = await self.start()
        self.assertIn("Release.01", report["status_text"])
        self.assertFalse(any(":release:" in b["callback_data"] for b in self.buttons(report)))

    async def test_cancel_before_worker_starts_is_terminal(self):
        response = await self.feature.command({"command": "pr", "args": ["test"], "chat_id": 10, "user_id": 1})
        result = await self.feature.operation_control({"operation_id": response["operation"]["operation_id"], "action": "cancel"})
        self.assertEqual(result["operation"]["state"], "cancelled")
        await self.runtime.run("search-raw-")
        self.assertEqual(self.requests, [])

    async def test_request_timeout_releases_operation_and_does_not_expose_credentials(self):
        with patch.object(prowlarr.requests, "get", side_effect=requests.Timeout("secret")):
            report = await self.start()
        self.assertEqual(report["state"], "failed")
        self.assertEqual(report["segment"]["role"], "search")
        self.assertIn("超时", report["status_text"])
        self.assertNotIn("secret", report["status_text"])
        self.assertNotIn(self.plan_id, self.feature.plans)

    async def test_cancel_during_request_never_delivers_late_results(self):
        started, finish = threading.Event(), threading.Event()

        def get(*args, **kwargs):
            started.set()
            finish.wait(3)
            return Mock(json=lambda: self.responses)

        class TaskRuntime:
            def spawn(self, worker, *, task_id):
                return asyncio.create_task(worker, name=task_id)

        self.feature.bind_runtime(TaskRuntime())
        with patch.object(prowlarr.requests, "get", side_effect=get):
            response = await self.feature.command({"command": "pr", "args": ["test"], "chat_id": 10, "user_id": 1})
            operation_id = response["operation"]["operation_id"]
            task = self.feature.operations[operation_id]["task"]
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await self.feature.operation_control({"operation_id": operation_id, "action": "cancel"})
                await asyncio.wait_for(task, 2)
            finally:
                finish.set()
        self.assertEqual(self.host.reports[-1]["state"], "cancelled")
        self.assertFalse(self.feature.plans)
        self.assertFalse(any(report["state"] == "awaiting_input" for report in self.host.reports))

    async def test_missing_directory_keeps_result_selection_without_submitting(self):
        self.feature.config["category_folder"] = []
        report = await self.start()
        callback = next(b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"])
        result = await self.click(callback)
        self.assertEqual(result["operation"]["stage"], "release_selection")
        self.assertIn("保存目录", result["operation"]["status_text"])
        self.assertEqual(self.host.calls, [])

    async def test_raw_input_exit_clears_input_mode_and_config_guard(self):
        await self.feature.command({"command": "pr", "chat_id": 10, "user_id": 1})
        response = await self.feature.command({"command": "search_config", "chat_id": 10, "user_id": 1})
        self.assertIn("当前搜索", response["actions"][0]["text"])
        result = await self.click("search:exit")
        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertFalse(self.feature.raw_search.awaiting_queries)
        await self.feature.message({"text": "test", "chat_id": 10, "user_id": 1})
        self.assertFalse(self.runtime.tasks)

    async def test_plain_text_results_preserve_quotes_and_ampersands(self):
        self.responses = [release(1, title='A "title" & <test>')]
        report = await self.start('"Some Title" & x=y')
        self.assertIn('"Some Title" & x=y', report["status_text"])
        self.assertIn('A "title" & <test>', report["status_text"])

    async def test_result_report_response_delay_does_not_cancel_submission_takeover(self):
        visible = asyncio.Event()
        delayed_response = asyncio.Event()
        tasks = []

        class TaskRuntime:
            def spawn(self, worker, *, task_id):
                task = asyncio.create_task(worker, name=task_id)
                tasks.append(task)
                return task

        original_report = self.host.report_operation

        async def report(operation):
            response = await original_report(operation)
            if operation["stage"] == "release_selection":
                visible.set()
                await delayed_response.wait()
            return response

        self.host.report_operation = report
        self.feature.bind_runtime(TaskRuntime())
        try:
            response = await self.feature.command({"command": "pr", "args": ["test"], "chat_id": 10, "user_id": 1})
            operation_id = response["operation"]["operation_id"]
            await asyncio.wait_for(visible.wait(), 2)
            plan_id = self.feature.operations[operation_id]["plan_id"]
            callback = next(b["callback_data"] for b in self.buttons(self.host.reports[-1]) if ":release:" in b["callback_data"])
            await self.click(callback)
            await self.click(f"search:raw_path:{plan_id}:0")
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
            self.assertEqual(len(self.host.calls), 1)
            self.assertFalse(any(item["state"] == "cancelled" for item in self.host.reports))
            self.assertEqual(self.feature.operations[operation_id]["state"], "handed_off")
        finally:
            delayed_response.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_cancel_before_submission_worker_runs_is_terminal(self):
        report = await self.start()

        class TaskRuntime:
            def spawn(self, worker, *, task_id):
                return asyncio.create_task(worker, name=task_id)

        self.feature.bind_runtime(TaskRuntime())
        callback = next(b["callback_data"] for b in self.buttons(report) if ":release:" in b["callback_data"])
        await self.click(callback)
        await self.click(f"search:raw_path:{self.plan_id}:0")
        task = self.feature.operations[self.operation_id]["task"]
        result = await self.feature.operation_control({"operation_id": self.operation_id, "action": "cancel"})
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(result["operation"]["state"], "cancelled")
        self.assertEqual(self.feature.operations[self.operation_id]["state"], "cancelled")
        self.assertEqual(self.host.calls, [])


class RawAdapterTest(unittest.TestCase):
    def test_normalization_keeps_prowlarr_sort_fields(self):
        item = prowlarr._normalize_result(release(1))
        self.assertEqual(item.get("leechers"), 2)
        self.assertEqual(item.get("grabs"), 99)
        self.assertEqual(item.get("files"), 2)
        self.assertEqual(item.get("age_minutes"), 999)
        self.assertEqual(item.get("sort_title"), "release 01")
        self.assertEqual(item.get("categories"), [{"id": 5000, "name": "TV"}])

    def test_sort_fields_handle_numeric_order_and_missing_values(self):
        from telepiplex_search.raw_results import sorted_releases

        cases = [
            ("title", [{"sort_title": "beta"}, {"sort_title": "Alpha"}, {}]),
            ("size", [{"size": "10"}, {"size": 2}, {"size": None}]),
            ("files", [{"files": 10}, {"files": 2}, {}]),
            ("grabs", [{"grabs": 10}, {"grabs": 2}, {"grabs": "NaN"}]),
            ("indexer", [{"indexer": "z"}, {"indexer": "A"}, {}]),
            ("protocol", [{"protocol": "usenet"}, {"protocol": "torrent"}, {}]),
            ("category", [{"categories": [{"name": "TV"}]}, {"categories": [{"name": "Movies"}]}, {}]),
            ("age", [{"publish_date": "2025-01-01T00:00:00Z"}, {"publish_date": "2026-01-01T00:00:00Z"}, {}]),
        ]
        for key, rows in cases:
            with self.subTest(sort=key):
                rows = [row | {"number": index} for index, row in enumerate(rows)]
                self.assertEqual([item["number"] for item in sorted_releases(rows, key)], [1, 0, 2])
                self.assertEqual([item["number"] for item in sorted_releases(rows, key, True)], [0, 1, 2])
                self.assertEqual([item["number"] for item in rows], [0, 1, 2])

    def test_long_unicode_result_page_stays_within_telegram_text_limit(self):
        from telepiplex_search.raw_results import result_view

        stored = {
            "plan": {"raw_query": '"&🦀' * 1000},
            "raw_sort": "age", "raw_descending": False,
            "results": [prowlarr._normalize_result(release(n, title='"&🦀' * 1000, indexer="🦀" * 1000)) for n in range(1, 6)],
        }
        report = result_view("a" * 10, stored)
        self.assertLessEqual(len(report["text"].encode("utf-16-le")) // 2, 4096)
        self.assertNotIn("&quot;", report["text"])

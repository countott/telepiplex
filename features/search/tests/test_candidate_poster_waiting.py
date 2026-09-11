"""Poster priority, one waiting budget, and consumer ownership regressions."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from telepiplex_search.service import SearchFeature
from telepiplex_search.source_schedule import SourceRequestKey, SourceScheduler
from tests.test_feature_service import ranked_search_plan

PROVIDERS = ("tmdb", "douban", "tvdb")


def missing_candidates(count=1):
    candidates = []
    for index in range(count):
        candidate = deepcopy(ranked_search_plan()["candidates"][0])
        candidate["candidate_key"] = f"candidate-{index}"
        candidate["poster_url"] = ""
        candidate["media_metadata"]["identity"].pop("poster_url", None)
        candidate["media_metadata"]["identity"].pop("poster_source", None)
        candidates.append(candidate)
    return {"candidates": tuple(candidates), "confirmed_contract": {"sentinel": "unchanged"}}


async def drain():
    # Run ready callbacks without elapsed-time assertions or releasing providers.
    for _ in range(30):
        await asyncio.sleep(0)


class GatedPosters:
    def __init__(self, count):
        self.expected = count * 3
        self.started = asyncio.Event()
        self.replies = {}
        self.consumers = {}

    async def __call__(self, candidate, provider):
        key = (candidate["candidate_key"], provider)
        self.consumers[key] = asyncio.current_task()
        reply = asyncio.get_running_loop().create_future()
        self.replies[key] = reply
        if len(self.replies) == self.expected:
            self.started.set()
        return await reply

    def release(self, index, provider, value):
        reply = self.replies[(f"candidate-{index}", provider)]
        if isinstance(value, BaseException):
            reply.set_exception(value)
        else:
            reply.set_result(value)

    async def cleanup(self):
        for task in self.consumers.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.consumers.values(), return_exceptions=True)


class CandidatePosterWaitingTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, count=1):
        stored = missing_candidates(count)
        lookup = GatedPosters(min(count, 5))
        feature = SearchFeature(config={}, host=None, candidate_poster_lookup=lookup)
        feature.candidate_poster_timeout = 60
        task = asyncio.create_task(feature._supplement_candidate_posters(stored))
        self.addAsyncCleanup(lookup.cleanup)

        async def cleanup():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(lookup.started.wait(), 1)
        return stored, lookup, feature, task

    async def test_tmdb_winner_finishes_without_waiting_for_lower_sources(self):
        stored, lookup, _, task = await self.start()
        lookup.release(0, "tmdb", "https://image.example/tmdb.jpg")
        await drain()
        self.assertTrue(task.done(), "a known TMDB winner must not wait for lower providers")
        await task
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/tmdb.jpg")
        self.assertTrue(all(t.done() for t in lookup.consumers.values()))

    async def test_lower_source_cannot_win_until_higher_source_finishes(self):
        stored, lookup, _, task = await self.start()
        lookup.release(0, "douban", "https://image.example/douban.jpg")
        lookup.release(0, "tvdb", "https://image.example/tvdb.jpg")
        await drain()
        self.assertFalse(task.done())
        self.assertEqual(stored["candidates"][0]["poster_url"], "")
        lookup.release(0, "tmdb", "https://image.example/tmdb.jpg")
        await task
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/tmdb.jpg")

    async def test_exception_invalid_url_and_empty_allow_independent_winners(self):
        stored, lookup, _, task = await self.start(3)
        original = deepcopy(stored)
        lookup.release(0, "tmdb", "https://image.example/first.jpg")
        lookup.release(1, "douban", "https://image.example/second.jpg")
        lookup.release(1, "tmdb", RuntimeError("unavailable"))
        lookup.release(2, "tvdb", "https://image.example/third.jpg")
        lookup.release(2, "tmdb", "http://invalid.example/poster.jpg")
        await drain()
        self.assertFalse(task.done(), "third candidate still needs Douban's outcome")
        self.assertTrue(lookup.consumers[("candidate-0", "tvdb")].done())
        self.assertTrue(lookup.consumers[("candidate-1", "tvdb")].done())
        self.assertEqual(stored, original, "no partial candidate commit while waiting")
        lookup.release(2, "douban", "")
        await task
        expected = deepcopy(original)
        for candidate, provider, name in zip(expected["candidates"], PROVIDERS, ("first", "second", "third")):
            url = f"https://image.example/{name}.jpg"
            candidate["poster_url"] = url
            candidate["media_metadata"]["identity"].update(poster_url=url, poster_source=provider)
        self.assertEqual(stored, expected)

    async def test_individually_cancelled_provider_is_unavailable(self):
        stored, lookup, _, task = await self.start()
        lookup.release(0, "tmdb", asyncio.CancelledError())
        lookup.release(0, "douban", "https://image.example/douban.jpg")
        lookup.release(0, "tvdb", "")
        await task
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/douban.jpg")

    async def test_parent_cancel_reaps_consumers_and_never_commits(self):
        stored, lookup, _, task = await self.start(2)
        original = deepcopy(stored)
        lookup.release(0, "tmdb", "https://image.example/first.jpg")
        await drain()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(t.done() for t in lookup.consumers.values()), "parent cancellation must reap owned consumers")
        await drain()
        self.assertEqual(stored, original)

    async def test_completions_do_not_extend_total_deadline(self):
        loop = asyncio.get_running_loop()
        now = [loop.time()]
        with patch.object(loop, "time", lambda: now[0]):
            stored, lookup, _, task = await self.start(2)
            # Successes at 20/40 seconds must not renew the 60-second budget.
            now[0] += 20
            lookup.release(0, "douban", "https://image.example/fallback.jpg")
            await drain()
            now[0] += 20
            lookup.release(1, "tvdb", "")
            await drain()
            self.assertFalse(task.done())
            now[0] += 20.01
            await drain()
            self.assertTrue(task.done(), "completion events must not renew the overall budget")
            await task
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/fallback.jpg")
        self.assertEqual(stored["candidates"][1]["poster_url"], "")
        self.assertTrue(all(t.done() for t in lookup.consumers.values()))

    async def test_existing_https_and_sixth_candidate_start_no_requests(self):
        stored = missing_candidates(6)
        stored["candidates"][0]["media_metadata"]["identity"]["poster_url"] = "https://image.example/existing.jpg"
        calls = []

        async def lookup(candidate, provider):
            calls.append((candidate["candidate_key"], provider))
            return ""

        feature = SearchFeature(config={}, host=None, candidate_poster_lookup=lookup)
        await feature._supplement_candidate_posters(stored)
        self.assertEqual(set(calls), {(f"candidate-{i}", p) for i in range(1, 5) for p in PROVIDERS})
        self.assertEqual(len(calls), 12)
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/existing.jpg")
        self.assertEqual(stored["candidates"][5]["poster_url"], "")

    async def test_parent_cancel_preserves_shared_scheduler_flight(self):
        scheduler = SourceScheduler()
        release = asyncio.Event()
        started = asyncio.Event()
        consumers = []
        key = SourceRequestKey(provider="tmdb", purpose="poster", media_type="movie", identity="shared", scope="work")

        async def fetch():
            started.set()
            await release.wait()
            return "https://image.example/shared.jpg"

        async def lookup(_candidate, _provider):
            consumers.append(asyncio.current_task())
            return await scheduler.run(key, fetch)

        stored = missing_candidates()
        original = deepcopy(stored)
        feature = SearchFeature(config={}, host=None, candidate_poster_lookup=lookup)
        parent = asyncio.create_task(feature._supplement_candidate_posters(stored))
        other = asyncio.create_task(scheduler.run(key, fetch))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await drain()
            parent.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await parent
            self.assertTrue(all(t.done() for t in consumers))
            self.assertEqual(scheduler.in_flight_count, 1)
            self.assertFalse(other.done())
            release.set()
            self.assertEqual(await other, "https://image.example/shared.jpg")
            await drain()
            self.assertEqual(scheduler.in_flight_count, 0)
            self.assertEqual(stored, original)
        finally:
            release.set()
            for task in [parent, other, *consumers]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(parent, other, *consumers, return_exceptions=True)

    async def test_parent_cancel_during_consumer_cleanup_discards_winner(self):
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        consumers = []

        async def lookup(_candidate, provider):
            consumers.append(asyncio.current_task())
            if provider == "tmdb":
                return "https://image.example/winner.jpg"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await cleanup_release.wait()
                raise

        stored = missing_candidates()
        original = deepcopy(stored)
        feature = SearchFeature(config={}, host=None, candidate_poster_lookup=lookup)
        feature.candidate_poster_timeout = 0.01
        parent = asyncio.create_task(feature._supplement_candidate_posters(stored))
        try:
            await asyncio.wait_for(cleanup_started.wait(), 1)
            parent.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await parent
            self.assertTrue(all(t.done() for t in consumers))
            self.assertEqual(stored, original)
        finally:
            cleanup_release.set()
            for task in [parent, *consumers]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(parent, *consumers, return_exceptions=True)


class CandidatePosterCommandTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reuse the existing offline Host, runtime and release-provider fixture;
        # command, projection, callbacks and contract construction remain real.
        from tests.test_feature_service import SearchFeatureTest
        self.fixture = SearchFeatureTest()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.feature = self.fixture.feature
        self.host = self.fixture.host
        self.runtime = self.fixture.runtime
        self.feature.indexer_loader = lambda: []
        self.feature.indexer_summary = lambda _items: {}
        self.lookup = GatedPosters(1)
        self.addAsyncCleanup(self.lookup.cleanup)
        self.feature.candidate_poster_lookup = self.lookup
        self.feature.candidate_poster_timeout = 60

        async def planner(_raw_query, plan_id):
            result = ranked_search_plan()
            result["plan_id"] = plan_id
            result["candidates"] = list(missing_candidates()["candidates"])
            return result
        self.feature.plan_builder = planner

    async def start(self):
        result = await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        runner = asyncio.create_task(self.runtime.run("search-plan-"))

        async def cleanup():
            if not runner.done():
                runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(self.lookup.started.wait(), 1)
        return result, runner

    async def test_early_poster_projects_once_then_immediate_confirmation_keeps_v2(self):
        _, runner = await self.start()
        self.assertEqual(self.host.reports, [])
        self.lookup.release(0, "tmdb", "https://image.example/confirmed.jpg")
        await drain()
        self.assertTrue(runner.done(), "command must show its one candidate report after the winner is known")
        await runner
        self.assertEqual(len(self.host.reports), 1)
        report = self.host.reports[0]
        self.assertEqual(report["stage"], "plan_confirmation")
        self.assertEqual(report["details"]["photo_url"], "https://image.example/confirmed.jpg")
        buttons = [button for row in report["details"]["keyboard"] for button in row]
        confirm = next(button["callback_data"] for button in buttons if ":select:" in button["callback_data"])
        plan_id = next(iter(self.feature.plans))
        stored = self.feature.plans[plan_id]
        await self.feature.callback({"payload": confirm.removeprefix("search:"), "user_id": 1, "chat_id": 10})
        await self.runtime.run("search-releases-")
        contract = deepcopy(stored["confirmed_contract"])
        self.assertEqual(contract["schema_version"], 2)
        self.assertTrue(contract["confirmed"])
        self.assertEqual(stored["candidates"][0]["poster_url"], "https://image.example/confirmed.jpg")
        self.assertEqual(contract["identity"]["title_zh"], "中文标题1")
        self.assertEqual(contract["identity"]["primary_ref"], {"provider": "tvdb_movie", "id": "1"})
        self.assertEqual(contract["scope"], {"kind": "movie", "season_number": None, "episode_number": None})
        await drain()
        self.assertEqual(stored["confirmed_contract"], contract)
        self.assertEqual(sum(r["stage"] == "plan_confirmation" for r in self.host.reports), 1)
        self.assertTrue(all(t.done() for t in self.lookup.consumers.values()))

    async def test_command_cancel_during_posters_emits_no_late_candidate(self):
        class TaskRuntime:
            def spawn(self, awaitable, *, task_id):
                return asyncio.create_task(awaitable, name=task_id)
        self.feature.bind_runtime(TaskRuntime())
        result = await self.feature.command({
            "command": "s", "args": ["候选"], "user_id": 1, "chat_id": 10,
        })
        await asyncio.wait_for(self.lookup.started.wait(), 1)
        operation_id = result["operation"]["operation_id"]
        task = self.feature.operations[operation_id]["task"]
        await self.feature.operation_control({
            "operation_id": operation_id,
            "revision": result["operation"]["revision"], "action": "cancel",
        })
        await task
        self.assertTrue(all(t.done() for t in self.lookup.consumers.values()))
        await drain()
        self.assertEqual(self.feature.plans, {})
        self.assertEqual(self.feature.operations[operation_id]["state"], "cancelled")
        self.assertFalse(any(r["stage"] in ("candidate_selection", "plan_confirmation") for r in self.host.reports))
        self.assertEqual(self.host.calls, [])

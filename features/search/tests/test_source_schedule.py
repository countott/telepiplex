import asyncio
import unittest


from telepiplex_search.source_schedule import (
    SourceRequestKey,
    SourceScheduler,
)


def request_key(**overrides):
    values = {
        "provider": "TMDB",
        "purpose": "anchor",
        "media_type": "series",
        "identity": "tmdb:1396",
        "scope": "season",
        "season_number": 5,
        "episode_number": None,
    }
    values.update(overrides)
    return SourceRequestKey(**values)


class SourceSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_and_completion_are_observed_without_identity(self):
        observations = []
        scheduler = SourceScheduler(
            observer=lambda event, facts: observations.append((event, facts))
        )

        async def fetch():
            return {"ok": True}

        await scheduler.run(request_key(identity="private-identity"), fetch)
        await scheduler.run(request_key(identity="private-identity"), fetch)

        outcomes = [facts["outcome"] for _event, facts in observations]
        self.assertIn("completed", outcomes)
        self.assertIn("cache_hit", outcomes)
        self.assertTrue(all(
            event == "search.source.request"
            and "identity" not in facts
            and "private-identity" not in repr(facts)
            for event, facts in observations
        ))

    async def test_single_flight_join_and_queued_completion_are_observed(self):
        observations = []
        scheduler = SourceScheduler(
            max_concurrency=1,
            observer=lambda event, facts: observations.append((event, facts)),
        )
        first_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_fetch():
            first_started.set()
            await release.wait()
            return {"ok": True}

        first = asyncio.create_task(scheduler.run(request_key(), slow_fetch))
        await first_started.wait()
        joined = asyncio.create_task(scheduler.run(request_key(), slow_fetch))
        queued = asyncio.create_task(scheduler.run(
            request_key(identity="other-private-identity"),
            slow_fetch,
        ))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, joined, queued)

        outcomes = [facts["outcome"] for _event, facts in observations]
        self.assertIn("single_flight_join", outcomes)
        self.assertIn("queue_waited", outcomes)

    async def test_observer_errors_do_not_change_provider_result(self):
        def broken_observer(_event, _facts):
            raise RuntimeError("observer failure must be isolated")

        scheduler = SourceScheduler(observer=broken_observer)

        result = await scheduler.run(
            request_key(),
            lambda: {"ok": True},
        )

        self.assertEqual(result, {"ok": True})

    async def test_cache_hit_observer_cancellation_propagates_to_caller(self):
        observer_started = asyncio.Event()
        observer_release = asyncio.Event()

        async def blocking_observer(_event, facts):
            if facts["outcome"] == "cache_hit":
                observer_started.set()
                await observer_release.wait()

        scheduler = SourceScheduler(observer=blocking_observer)
        key = request_key()
        await scheduler.run(key, lambda: {"ok": True})

        waiting = asyncio.create_task(
            scheduler.run(key, lambda: {"ok": "should stay cached"})
        )
        await observer_started.wait()
        waiting.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await waiting

    async def test_identical_requests_share_one_flight_and_return_copies(self):
        scheduler = SourceScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"facts": [{"id": "1396"}]}

        first = asyncio.create_task(scheduler.run(request_key(), fetch))
        await started.wait()
        second = asyncio.create_task(scheduler.run(request_key(), fetch))
        await asyncio.sleep(0)
        release.set()
        first_value, second_value = await asyncio.gather(first, second)

        self.assertEqual(calls, 1)
        first_value["facts"][0]["id"] = "mutated"
        self.assertEqual(second_value["facts"][0]["id"], "1396")
        cached = await scheduler.run(request_key(), fetch)
        self.assertEqual(cached["facts"][0]["id"], "1396")
        self.assertEqual(calls, 1)

    async def test_every_request_dimension_is_part_of_the_key(self):
        scheduler = SourceScheduler()
        keys = [
            request_key(),
            request_key(provider="tvdb"),
            request_key(purpose="poster"),
            request_key(media_type="movie"),
            request_key(identity="tmdb:1397"),
            request_key(scope="episode"),
            request_key(season_number=6),
            request_key(episode_number=3),
        ]
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return {"call": calls}

        values = [await scheduler.run(key, fetch) for key in keys]

        self.assertEqual(calls, len(keys))
        self.assertEqual(
            [value["call"] for value in values],
            list(range(1, len(keys) + 1)),
        )

    def test_invalid_coordinates_are_treated_as_unspecified(self):
        invalid_values = (True, False, 0, -1, 1.5, "invalid", None)

        for value in invalid_values:
            with self.subTest(value=value):
                key = request_key(
                    season_number=value,
                    episode_number=value,
                )
                self.assertIsNone(key.season_number)
                self.assertIsNone(key.episode_number)

    async def test_cancelling_one_waiter_does_not_cancel_shared_fetch(self):
        scheduler = SourceScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"ok": True}

        cancelled = asyncio.create_task(
            scheduler.run(request_key(), fetch)
        )
        survivor = asyncio.create_task(
            scheduler.run(request_key(), fetch)
        )
        await started.wait()
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        release.set()

        self.assertEqual(await survivor, {"ok": True})
        self.assertEqual(calls, 1)

    async def test_failures_and_non_cacheable_results_are_not_cached(self):
        scheduler = SourceScheduler()
        calls = 0

        async def transient():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary")
            return {"status": "ok", "facts": [{"id": "1396"}]}

        with self.assertRaises(TimeoutError):
            await scheduler.run(request_key(), transient)
        self.assertEqual(await scheduler.run(request_key(), transient), {
            "status": "ok",
            "facts": [{"id": "1396"}],
        })
        self.assertEqual(calls, 2)

        unavailable_calls = 0

        async def unavailable():
            nonlocal unavailable_calls
            unavailable_calls += 1
            return {"status": "unavailable", "facts": []}

        for _ in range(2):
            await scheduler.run(
                request_key(identity="tmdb:unavailable"),
                unavailable,
                cacheable=lambda value: value.get("status") == "ok",
            )
        self.assertEqual(unavailable_calls, 2)

    async def test_ttl_and_lru_bound_are_deterministic(self):
        now = [100.0]
        scheduler = SourceScheduler(
            success_ttl=30.0,
            max_success_entries=256,
            clock=lambda: now[0],
        )
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return {"call": calls}

        key = request_key()
        self.assertEqual((await scheduler.run(key, fetch))["call"], 1)
        now[0] = 129.9
        self.assertEqual((await scheduler.run(key, fetch))["call"], 1)
        now[0] = 130.1
        self.assertEqual((await scheduler.run(key, fetch))["call"], 2)

        for index in range(257):
            await scheduler.run(
                request_key(identity=f"tmdb:lru:{index}"),
                fetch,
            )
        self.assertLessEqual(scheduler.success_entry_count, 256)
        before = calls
        await scheduler.run(request_key(identity="tmdb:lru:0"), fetch)
        self.assertEqual(calls, before + 1)

    async def test_actual_provider_fetches_obey_concurrency_bound(self):
        scheduler = SourceScheduler(max_concurrency=2)
        release = asyncio.Event()
        active = 0
        peak = 0

        async def fetch():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
                return {"ok": True}
            finally:
                active -= 1

        tasks = [
            asyncio.create_task(
                scheduler.run(request_key(identity=f"tmdb:{index}"), fetch)
            )
            for index in range(5)
        ]
        while peak < 2:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(peak, 2)
        release.set()
        await asyncio.gather(*tasks)

    async def test_cancelled_last_waiter_failure_is_consumed_and_cleaned(self):
        scheduler = SourceScheduler(success_ttl=0)
        started = asyncio.Event()
        release = asyncio.Event()
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        unhandled = []

        async def fetch():
            started.set()
            await release.wait()
            raise RuntimeError("late failure")

        loop.set_exception_handler(
            lambda _loop, context: unhandled.append(context)
        )
        try:
            waiter = asyncio.create_task(
                scheduler.run(request_key(), fetch)
            )
            await started.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            release.set()
            for _ in range(4):
                await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(original_handler)

        self.assertEqual(scheduler.in_flight_count, 0)
        self.assertEqual(unhandled, [])


if __name__ == "__main__":
    unittest.main()

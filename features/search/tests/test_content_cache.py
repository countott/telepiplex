import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

from telepiplex_search.content_cache import ContentCache
from telepiplex_search.service import SearchFeature
from telepiplex_search.series_scope import apply_series_scope
from tests.test_feature_service import FakeHost, FakeRuntime, series_ranked_search_plan, search_plan


def test_cache_survives_restart_expires_and_bounds_entries(tmp_path):
    now = [100.0]
    path = tmp_path / "cache.db"
    cache = ContentCache(path, now=lambda: now[0], max_entries=2)
    cache.put("discovery", "a", {"facts": [1]}, 60)
    cache = ContentCache(path, now=lambda: now[0], max_entries=2)
    hit = cache.get("discovery", "a")
    hit["facts"].append(2)
    assert cache.get("discovery", "a") == {"facts": [1]}
    cache.put("discovery", "b", {}, 60)
    now[0] += 1
    cache.put("discovery", "c", {}, 60)
    assert cache.get("discovery", "a") is None
    now[0] += 61
    assert cache.get("discovery", "c") is None


def test_discovery_cache_keeps_confirmation_rebinds_ids_and_never_caches_failures():
    async def run():
        feature = SearchFeature(config={}, host=FakeHost())
        plan = series_ranked_search_plan()
        plan["candidates"] = plan["candidates"][:1]
        feature._build_uncached_plan = AsyncMock(return_value=plan)
        first = await feature._build_plan("The Glory", "first")
        second = await feature._build_plan("  The Glory ", "second")
        assert feature._build_uncached_plan.await_count == 1
        assert second["plan_id"] == "second"
        assert second["candidates"][0]["media_metadata"]["metadata_id"] == "second"
        assert not second["candidates"][0]["media_metadata"]["confirmed"]
        feature.config["changed_source"] = True
        await feature._build_plan("The Glory", "third")
        assert feature._build_uncached_plan.await_count == 2
        feature._build_uncached_plan.side_effect = ValueError("offline")
        for _ in range(2):
            try:
                await feature._build_plan("Another title", "failed")
            except ValueError:
                pass
        assert feature._build_uncached_plan.await_count == 4
    asyncio.run(run())


def test_continuation_restores_full_inventory_new_task_owner_and_fixed_expiry(tmp_path):
    async def run():
        now = [100.0]
        cache = ContentCache(tmp_path / "cache.db", now=lambda: now[0])
        feature = SearchFeature(config={}, host=FakeHost(), content_cache=cache)
        candidate = series_ranked_search_plan()["candidates"][0]
        stored = {"owner": (10, 1), "operation_id": "original", "plan": {"raw_query": "黑暗荣耀"}}
        feature._remember_series(stored, candidate)
        original_expiry = cache.get("continuation", "original")["inventory_expires"]
        scoped = apply_series_scope(candidate["media_metadata"], "episode", season_number=1, episode_number=1)
        assert len(scoped["items"]) == 1
        feature._release_plan("old-plan")
        # Recreate the Feature: no old plan or operation is needed.
        feature = SearchFeature(config={}, host=FakeHost(), content_cache=ContentCache(cache.path, now=lambda: now[0]))
        feature.release_search = Mock(side_effect=AssertionError("must wait for range selection"))
        now[0] += 600
        request = {"command": "s", "chat_id": 10, "user_id": 1, "resume_operation_id": "original"}
        result = await feature.command(request)
        operation = result["operation"]
        assert operation["operation_id"] != "original"
        assert operation["stage"] == "series_scope"
        assert result["actions"][0]["kind"] == "send_message"
        new_plan = next(iter(feature.plans.values()))
        assert len(new_plan["plan"]["media_metadata"]["items"]) == 8
        assert not new_plan.get("release_by_id")
        assert cache.get("continuation", operation["operation_id"])["inventory_expires"] == original_expiry
        assert not feature._continuation_snapshot(dict(request, user_id=2))
        # Every following scope starts from the full copy, not the last selected episode.
        again = await feature.command(request)
        assert again["operation"]["operation_id"] != operation["operation_id"]
        assert len(next(reversed(feature.plans.values()))["plan"]["media_metadata"]["items"]) == 8
    asyncio.run(run())


def test_stale_inventory_refreshes_confirmed_anchors_not_title_discovery(tmp_path):
    async def run():
        now = [100.0]
        feature = SearchFeature(config={}, host=FakeHost(), content_cache=ContentCache(tmp_path / "cache.db", now=lambda: now[0]))
        candidate = series_ranked_search_plan()["candidates"][0]
        feature._remember_series({"owner": (10, 1), "operation_id": "original", "plan": {}}, candidate)
        now[0] += 86401
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        feature._hydrate_selected_candidate = AsyncMock(return_value=deepcopy(candidate))
        feature.plan_builder = AsyncMock(side_effect=AssertionError("no rediscovery"))
        result = await feature.command({"command": "s", "chat_id": 10, "user_id": 1, "resume_operation_id": "original"})
        assert result["operation"]["state"] == "running"
        await runtime.run("search-resume-")
        feature._hydrate_selected_candidate.assert_awaited_once()
        assert feature.host.reports[-1]["stage"] == "series_scope"
        feature.plan_builder.assert_not_awaited()
    asyncio.run(run())


def test_movie_cannot_create_or_restore_series_continuation():
    async def run():
        feature = SearchFeature(config={}, host=FakeHost())
        candidate = {"media_metadata": search_plan()["media_metadata"]}
        stored = {"owner": (10, 1), "operation_id": "movie", "plan": {}}
        feature._remember_series(stored, candidate)
        assert feature.content_cache.get("continuation", "movie") is None
        # Even an old or malformed cached record must not expose the entry.
        feature.content_cache.put("continuation", "movie", {
            "owner": [10, 1], "candidate": candidate,
        }, 86400)
        request = {"chat_id": 10, "user_id": 1, "resume_operation_id": "movie"}
        assert feature._continuation_snapshot(request) is None
        result = await feature.command(dict(request, command="s"))
        assert "operation" not in result
        assert not feature.plans
    asyncio.run(run())


def test_scope_specific_context_reads_root_inventory_before_offering_other_episodes():
    async def run():
        feature = SearchFeature(config={}, host=FakeHost())
        candidate = series_ranked_search_plan()["candidates"][0]
        candidate["intended_scope"] = "episode"
        candidate["requested_season_number"] = 1
        candidate["requested_episode_number"] = 1
        feature._remember_series({"owner": (10, 1), "operation_id": "original", "plan": {}}, candidate)
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        feature._hydrate_selected_candidate = AsyncMock(return_value=deepcopy(candidate))
        result = await feature.command({"command": "s", "chat_id": 10, "user_id": 1, "resume_operation_id": "original"})
        assert result["operation"]["state"] == "running"
        await runtime.run("search-resume-")
        requested = feature._hydrate_selected_candidate.await_args.args[0]
        assert requested["intended_scope"] == "work"
        assert requested["requested_episode_number"] is None
        assert requested["requested_season_number"] is None
    asyncio.run(run())


def test_same_work_release_search_always_calls_prowlarr_again():
    async def run():
        releases = Mock(return_value=[])
        feature = SearchFeature(config={}, host=FakeHost(), release_search=releases, indexer_loader=lambda: [])
        for plan_id in ("first", "second"):
            plan = search_plan()
            plan["plan_id"] = plan_id
            stored = {"owner": (10, 1), "operation_id": plan_id, "plan": plan,
                      "identity_segment_sealed": True, "results": [], "selected_path": "/Movies"}
            await feature._confirm_and_search(plan_id, stored)
        assert releases.call_count >= 2
        assert releases.call_args_list[:releases.call_count // 2] == releases.call_args_list[releases.call_count // 2:]
    asyncio.run(run())


def test_hydration_cache_isolates_scope_and_does_not_extend_inventory_age():
    async def run():
        now = [100.0]
        feature = SearchFeature(config={}, host=FakeHost(), content_cache=ContentCache(now=lambda: now[0]))
        candidate = series_ranked_search_plan()["candidates"][0]
        feature._hydrate_uncached_candidate = AsyncMock(return_value=deepcopy(candidate))
        first = await feature._hydrate_selected_candidate(candidate, metadata_id="one", raw_query="title", require_anchor=True)
        now[0] += 86000
        hit = await feature._hydrate_selected_candidate(candidate, metadata_id="two", raw_query="title", require_anchor=True)
        assert feature._hydrate_uncached_candidate.await_count == 1
        assert hit["media_metadata"]["metadata_id"] == "two"
        feature._remember_series({"owner": (10, 1), "operation_id": "two", "plan": {}}, hit)
        assert feature.content_cache.get("continuation", "two")["inventory_expires"] == first["_inventory_expires_at"]
        scoped = dict(candidate, intended_scope="episode", requested_season_number=1, requested_episode_number=1)
        await feature._hydrate_selected_candidate(scoped, metadata_id="three", raw_query="title", require_anchor=True)
        assert feature._hydrate_uncached_candidate.await_count == 2
    asyncio.run(run())

import asyncio
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from telepiplex_plugin_sdk import FeatureError
from telepiplex_search.service import SearchFeature
from tests.test_feature_service import FakeHost, FakeRuntime, frozen_douban_movie_candidate, series_ranked_search_plan


def setup_selection():
    feature = SearchFeature(config={}, host=FakeHost())
    runtime = FakeRuntime()
    feature.bind_runtime(runtime)
    operation = feature._new_operation({"chat_id": 10, "user_id": 1}, state="awaiting_input",
        stage="candidate_selection", status_text="候选", control="exit", kind="search")
    plan = series_ranked_search_plan()
    plan["plan_id"] = "selected"
    plan["candidates"][0]["links_frozen"] = True
    stored = {"owner": (10, 1), "operation_id": operation["operation_id"],
              "plan": plan, "candidates": plan["candidates"]}
    feature.plans["selected"] = stored
    feature.operations[operation["operation_id"]]["plan_id"] = "selected"
    request = {"payload": "select:selected:0", "chat_id": 10, "user_id": 1}
    return feature, runtime, stored, request


def test_slow_confirmation_returns_immediately_and_repeated_clicks_do_not_rehydrate():
    async def run():
        feature, runtime, stored, request = setup_selection()
        started, release = asyncio.Event(), asyncio.Event()
        async def hydrate(candidate, **kwargs):
            started.set()
            await release.wait()
            return deepcopy(candidate)
        feature._hydrate_selected_candidate = AsyncMock(side_effect=hydrate)
        result = await asyncio.wait_for(feature.callback(request), timeout=0.1)
        assert result["operation"]["state"] == "running"
        assert result["operation"]["details"]["keyboard"] == []
        worker = asyncio.create_task(runtime.run("search-select-"))
        await started.wait()
        for action in ("select:selected:0", "candidate_page:selected:0", "browse:selected:0"):
            with pytest.raises(FeatureError, match="no longer active"):
                await feature.callback(dict(request, payload=action))
        release.set()
        await worker
        feature._hydrate_selected_candidate.assert_awaited_once()
        assert feature.host.reports[-1]["stage"] == "series_scope"
        assert feature.host.reports[-1]["state"] == "awaiting_input"
        with pytest.raises(FeatureError):
            await feature.callback(request)
    asyncio.run(run())


def test_failed_confirmation_restores_retry_and_successful_retry_only_runs_once():
    async def run():
        feature, runtime, stored, request = setup_selection()
        request["update_id"] = 1
        feature._hydrate_selected_candidate = AsyncMock(side_effect=RuntimeError("offline"))
        await feature.callback(request)
        await runtime.run("search-select-")
        assert feature.host.reports[-1]["stage"] == "candidate_selection"
        assert feature.host.reports[-1]["details"]["keyboard"]
        with pytest.raises(FeatureError, match="already consumed"):
            await feature.callback(request)
        assert not runtime.tasks
        feature._hydrate_selected_candidate.assert_awaited_once()
        feature._hydrate_selected_candidate.side_effect = None
        feature._hydrate_selected_candidate.return_value = deepcopy(stored["candidates"][0])
        await feature.callback(dict(request, update_id=2))
        await runtime.run("search-select-")
        assert feature.host.reports[-1]["stage"] == "series_scope"
        assert feature._hydrate_selected_candidate.await_count == 2
    asyncio.run(run())


def test_cancel_slow_confirmation_stops_worker_without_opening_scope():
    async def run():
        feature, runtime, stored, request = setup_selection()
        runtime.spawn = lambda worker, **kwargs: asyncio.create_task(worker)
        started = asyncio.Event()
        async def hydrate(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        feature._hydrate_selected_candidate = AsyncMock(side_effect=hydrate)
        await feature.callback(request)
        worker = feature.operations[stored["operation_id"]]["task"]
        await started.wait()
        result = await feature.callback(dict(request, payload="cancel:selected"))
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert result["operation"]["state"] == "cancelled"
        assert "selected" not in feature.plans
        assert not feature.host.reports
    asyncio.run(run())


def test_movie_confirmation_starts_one_release_worker_and_reports_selected_identity():
    async def run():
        feature, runtime, stored, request = setup_selection()
        movie = frozen_douban_movie_candidate()
        stored["candidates"] = [movie]
        feature.config["category_folder"] = [{"kind": "live_action_movie", "name": "电影", "path": "/Movies"}]
        feature._hydrate_selected_candidate = AsyncMock(return_value=deepcopy(movie))
        feature.release_search = Mock(return_value=[])
        feature.indexer_loader = lambda: []
        await feature.callback(request)
        await runtime.run("search-select-")
        assert len(runtime.tasks) == 1
        assert next(iter(runtime.tasks)).startswith("search-releases-")
        with pytest.raises(FeatureError):
            await feature.callback(request)
        await runtime.run("search-releases-")
        identities = [r for r in feature.host.reports if r["stage"] == "identity_confirmation"]
        assert len(identities) == 1
        assert identities[0]["details"]["identity_confirmed"] is True
        assert "poster_items" not in identities[0]["details"]
        assert movie["media_metadata"]["identity"]["english_title"] in identities[0]["status_text"]
        assert not feature._continuation_snapshot(dict(request, resume_operation_id=stored["operation_id"]))
        assert not runtime.tasks
    asyncio.run(run())


def test_worker_spawn_failure_restores_candidate_choice():
    async def run():
        feature, runtime, stored, request = setup_selection()
        runtime.spawn = Mock(side_effect=RuntimeError("cannot spawn"))
        result = await feature.callback(request)
        assert result["operation"]["state"] == "awaiting_input"
        assert result["operation"]["stage"] == "candidate_selection"
        assert runtime.spawn.call_args.args[0].cr_frame is None
    asyncio.run(run())


def test_legacy_candidate_uses_the_same_background_confirmation_boundary():
    async def run():
        feature, runtime, stored, request = setup_selection()
        stored["candidates"][0]["links_frozen"] = False
        result = await feature.callback(request)
        try:
            assert result["operation"]["state"] == "running"
            assert len(runtime.tasks) == 1
            assert next(iter(runtime.tasks)).startswith("search-select-")
        finally:
            for worker in runtime.tasks.values():
                worker.close()
    asyncio.run(run())


@pytest.mark.parametrize("started", [False, True])
def test_host_cancel_finishes_confirmation_instead_of_sticking_in_cancelling(started):
    async def run():
        feature, runtime, stored, request = setup_selection()
        runtime.spawn = lambda worker, **kwargs: asyncio.create_task(worker)
        entered = asyncio.Event()
        async def hydrate(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        feature._hydrate_selected_candidate = AsyncMock(side_effect=hydrate)
        await feature.callback(request)
        worker = feature.operations[stored["operation_id"]]["task"]
        if started:
            await entered.wait()
        result = await feature.operation_control({"operation_id": stored["operation_id"], "action": "cancel"})
        await asyncio.gather(worker, return_exceptions=True)
        assert result["operation"]["state"] == "cancelled"
        assert feature.operations[stored["operation_id"]]["state"] == "cancelled"
        assert not feature.plans
        assert not feature.host.reports
    asyncio.run(run())


@pytest.mark.parametrize("exit_kind", ["callback", "host", "exit"])
def test_late_provider_result_cannot_revive_a_cancelled_confirmation(exit_kind):
    async def run():
        feature, runtime, stored, request = setup_selection()
        runtime.spawn = lambda worker, **kwargs: asyncio.create_task(worker)
        entered, finish = asyncio.Event(), asyncio.Event()
        async def stubborn_provider(candidate, **kwargs):
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                await finish.wait()
            return deepcopy(candidate)
        feature._hydrate_selected_candidate = AsyncMock(side_effect=stubborn_provider)
        await feature.callback(request)
        worker = feature.operations[stored["operation_id"]]["task"]
        await entered.wait()
        if exit_kind == "host":
            result = await feature.operation_control({"operation_id": stored["operation_id"], "action": "exit"})
        else:
            result = await feature.callback(dict(request, payload="exit" if exit_kind == "exit" else "cancel:selected"))
        finish.set()
        await asyncio.gather(worker, return_exceptions=True)
        assert result["operation"]["state"] == "cancelled"
        assert feature.operations[stored["operation_id"]]["state"] == "cancelled"
        assert not feature.plans
        assert not feature.host.reports
        assert not feature.content_cache.get("continuation", stored["operation_id"])
    asyncio.run(run())


def test_real_rpc_slow_confirmation_survives_deadline_and_one_hundred_duplicate_clicks():
    from telepiplex_plugin_sdk.runtime import FeatureRuntime

    async def run(socket_path):
        feature, _, stored, request = setup_selection()
        runtime = FeatureRuntime(manifest={"plugin_id": "search"}, token="test-token",
                                 callbacks={"search": feature.callback})
        feature.bind_runtime(runtime)
        entered, finish = asyncio.Event(), asyncio.Event()
        async def hydrate(candidate, **kwargs):
            entered.set()
            await finish.wait()
            return deepcopy(candidate)
        feature._hydrate_selected_candidate = AsyncMock(side_effect=hydrate)
        server = asyncio.create_task(runtime.serve(socket_path))
        try:
            async with asyncio.timeout(2):
                while not socket_path.exists():
                    await asyncio.sleep(0.001)
            async def rpc(number, payload=request["payload"], deadline=2):
                reader, writer = await asyncio.open_unix_connection(str(socket_path))
                try:
                    writer.write((json.dumps({"type": "request", "id": str(number), "token": "test-token",
                        "method": "callback.dispatch", "deadline_at": time.time() + deadline,
                        "idempotency_key": f"telegram:{number}", "params": {
                            **request, "namespace": "search", "payload": payload, "update_id": number,
                        }}) + "\n").encode())
                    await writer.drain()
                    async with asyncio.timeout(3):
                        return json.loads(await reader.readline())
                finally:
                    writer.close()
                    await writer.wait_closed()
            first = await rpc(1, deadline=0.25)
            assert first["ok"] is True
            assert first["result"]["operation"]["state"] == "running"
            await entered.wait()
            worker = feature.operations[stored["operation_id"]]["task"]
            # Exercise both delivery replay and fresh Telegram update IDs.
            repeats = await asyncio.gather(*(rpc(1 if i < 50 else i) for i in range(100)))
            assert all(not item["ok"] and item["error"]["code"] == "invalid_state" for item in repeats)
            for payload in ("confirm:selected", "scope:selected:whole_series", "retry:selected",
                            "clarify:selected:0", "placement:selected:standalone"):
                assert (await rpc(200, payload))["error"]["code"] == "invalid_state"
            # Cross the production Host's 30-second deadline, not just a mocked delay.
            await asyncio.sleep(30.1)
            assert not worker.done()
            assert len(runtime._background_tasks) == 1
            feature._hydrate_selected_candidate.assert_awaited_once()
            finish.set()
            await asyncio.wait_for(worker, timeout=2)
            assert feature.host.reports[-1]["stage"] == "series_scope"
            assert (await rpc(201))["error"]["code"] == "invalid_state"
            feature._hydrate_selected_candidate.assert_awaited_once()
        finally:
            finish.set()
            await asyncio.gather(*runtime._background_tasks.values(), return_exceptions=True)
            await runtime.close()
            await server
    with tempfile.TemporaryDirectory(prefix="tpc-rpc-", dir="/tmp") as directory:
        asyncio.run(run(Path(directory) / "search.sock"))


def test_old_confirmation_callback_after_feature_restart_does_not_restart_search():
    async def run():
        feature, _, _, request = setup_selection()
        restarted = SearchFeature(config={}, host=FakeHost(), content_cache=feature.content_cache)
        runtime = FakeRuntime()
        restarted.bind_runtime(runtime)
        result = await restarted.callback(request)
        assert "过期" in result["actions"][0]["text"]
        assert not runtime.tasks
        assert not restarted.operations
    asyncio.run(run())

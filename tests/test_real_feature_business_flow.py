"""Actual Search, download and rename services composed through local Host RPC."""
import asyncio
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tests import test_operation_pipeline_e2e as host_fixture
from tests.business_flow_storage import Memory115

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("lose_submission_response,large_tree", [(False, False), (True, False), (False, True)])
def test_real_features_confirm_v2_download_and_rename_once(tmp_path, monkeypatch, lose_submission_response, large_tree):
    for name in ("search", "download", "rename"):
        monkeypatch.syspath_prepend(str(ROOT / "features" / name / "src"))

    async def exercise():
        from app.handlers import interaction_handler
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_plugin_sdk.host_client import HostClient
        from telepiplex_search.audit_transport import FixtureProviders, audit_config
        from telepiplex_search.context import runtime_context as search_context
        from telepiplex_search.service import SearchFeature
        from telepiplex_download.service import DownloadFeature
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_rename.service import RenameFeature
        from telepiplex_rename.jobs import RenameJobStore
        from telepiplex_rename.context import runtime_context as rename_context

        harness = host_fixture.OperationPipelineEndToEndTest()
        await harness.asyncSetUp()
        case = {"case_id": "rpc-fargo-movie", "query": "Fargo 1996", "expected_titles": ["Fargo", "冰血暴"],
                "year": "1996", "media_type": "movie", "scope": "work"}
        providers = FixtureProviders(case)
        storage = Memory115(directories=1000 if large_tree else 0)
        features = {}
        manifests = {name: PluginManifest.from_mapping(yaml.safe_load(
            (ROOT / "features" / name / "manifest.yaml").read_text()
        )) for name in ("search", "download", "rename")}
        hosts = {name: HostClient(harness.broker.socket_path, name + "-token") for name in manifests}
        if lose_submission_response:
            from telepiplex_plugin_sdk import FeatureError
            original_call = hosts["search"].call_capability

            async def lost_response(capability, method, payload, **kwargs):
                result = await original_call(capability, method, payload, **kwargs)
                if (capability, method) == ("download.provider", "submit"):
                    raise FeatureError("deadline_exceeded", "simulated response lost after acceptance")
                return result

            hosts["search"].call_capability = lost_response
        next_message = iter(range(300, 1000))

        async def send(**kwargs):
            return SimpleNamespace(message_id=next(next_message))

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send), send_photo=AsyncMock(side_effect=send),
            edit_message_text=AsyncMock(), edit_message_caption=AsyncMock(), edit_message_media=AsyncMock(),
            edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock())
        app = SimpleNamespace(bot=bot, bot_data={interaction_handler.COORDINATOR_KEY: harness.coordinator,
                                               interaction_handler.ROUTER_KEY: harness.router})
        harness.operation_sink.attach(lambda record: interaction_handler.render_operation(app, harness.router, record))
        download_jobs = DownloadJobStore(tmp_path / "downloads.db")
        rename_jobs = RenameJobStore(tmp_path / "rename.db")
        rename_config = yaml.safe_load((ROOT / "features/rename/config.default.yaml").read_text())
        rename_config.update({"category_folder": audit_config()["category_folder"], "ai": {"enable": False}})
        rename_context.configure({**rename_config, "media": {"unorganized_path": "/unorganized"}})
        clients = {}

        async def drain(name):
            runtime = features[name].runtime
            async with asyncio.timeout(60):
                while runtime._background_tasks:
                    await asyncio.gather(*tuple(runtime._background_tasks.values()))
            await harness.operation_sink.drain()

        async def click(payload):
            result = await clients["search"].request("callback.dispatch", {
                "namespace": "search", "payload": payload, "chat_id": 10, "user_id": 1,
            }, deadline=20)
            await drain("search")
            return result

        frame_sizes = []
        original_write = asyncio.StreamWriter.write

        def measured_write(writer, data):
            frame_sizes.append(len(data))
            return original_write(writer, data)

        try:
            with providers.active(), patch.object(interaction_handler, "_segment_photo_media", AsyncMock(return_value=BytesIO(b"fixture"))), patch.object(asyncio.StreamWriter, "write", measured_write):
                search_context.configure(audit_config())
                features["search"] = SearchFeature(config=audit_config(), host=hosts["search"])
                features["download"] = DownloadFeature(config={"poll_interval": 0.01, "enable_tree_snapshot_references": large_tree}, host=hosts["download"],
                    client=storage, jobs=download_jobs)
                features["rename"] = RenameFeature(config=rename_config, host=hosts["rename"], jobs=rename_jobs)
                handlers = {
                    "search": {"commands": {"search": features["search"].command, "s": features["search"].command},
                               "callbacks": {"search": features["search"].callback},
                               "capabilities": {"media.search": features["search"].metadata_capability}},
                    "download": {"capabilities": {"download.provider": features["download"].download_capability,
                                                  "storage.provider": features["download"].storage_capability}},
                    "rename": {"events": {"download.completed": features["rename"].download_completed}},
                }
                for name in ("download", "search", "rename"):
                    clients[name] = await harness._start_runtime(manifests[name], name + "-token", **handlers[name])
                    features[name].bind_runtime(harness.runtimes[-1])
                result = await clients["search"].request("command.dispatch", {
                    "command": "s", "args": [case["query"]], "chat_id": 10, "user_id": 1,
                }, deadline=20)
                operation_id = result["operation"]["operation_id"]
                await harness.operation_sink("search", result["operation"])
                await drain("search")
                plan_id, stored = next(iter(features["search"].plans.items()))
                candidates = stored["candidates"]
                matching = [i for i, item in enumerate(candidates) if str((item.get("media_metadata") or {}).get("identity", {}).get("year") or item.get("year")) == "1996"]
                assert matching, candidates
                index = matching[0]
                await click(f"select:{plan_id}:{index}")
                contract = deepcopy(stored["confirmed_contract"])
                assert contract["schema_version"] == 2
                assert contract["identity"]["primary_ref"] == {"provider": "wikidata", "id": "Q91000001"}
                assert contract["scope"] == {"kind": "movie", "season_number": None, "episode_number": None}
                release_id = next(iter(stored["release_by_id"]))
                selected_release = deepcopy(stored["release_by_id"][release_id])
                selected_link = selected_release["magnet_url"]
                selected_path = stored["selected_path"]
                release_receipt = {key: selected_release[key] for key in ("title", "indexer", "size")}
                await click(f"release:{plan_id}:{release_id}")
                await drain("download")
                async with asyncio.timeout(60):
                    while not rename_jobs.get(f"{plan_id}:release:{release_id}") or harness.coordinator.get(operation_id).state not in {"completed", "failed", "cancelled"}:
                        await asyncio.sleep(0.01)
                await drain("rename")
                job_id = f"{plan_id}:release:{release_id}"
                downloaded = download_jobs.get(job_id)
                renamed = rename_jobs.get(job_id)
                assert downloaded["payload"]["media_metadata"] == contract
                assert downloaded["payload"]["release"] == release_receipt
                assert downloaded["payload"]["link"] == selected_link
                assert downloaded["payload"]["selected_path"] == selected_path
                assert downloaded["result"]["release"] == release_receipt
                assert downloaded["result"]["media_metadata"] == contract
                expected_state = "failed" if large_tree else "completed"
                assert renamed["state"] == expected_state, renamed["result"].get("message")
                assert harness.coordinator.get(operation_id).state == expected_state
                assert harness.coordinator.get(operation_id).plugin_id == "rename"
                assert storage.added == [(selected_link, selected_path)]
                assert len([write for write in storage.writes if write[0] == "move"]) == 1
                if large_tree:
                    assert downloaded["result"]["file_tree_transport"] == "snapshot_ref_v1"
                    assert downloaded["result"]["file_tree_snapshot"]["node_count"] == 1001
                    assert renamed["result"]["file_results"]["organized_files"] == 1
                    assert renamed["result"]["file_results"]["failed_files"] == 0
                    # Empty directories unrelated to a media source remain protected;
                    # failure to remove the source root must not claim full success.
                else:
                    assert storage.tree_reads == 2  # Retain independent post-cleanup verification.
                assert frame_sizes and max(frame_sizes) <= 1_048_576
                videos = [(path, node) for path, node in storage.nodes.items() if not node["is_dir"]]
                assert len(videos) == 1 and videos[0][1]["file_id"] == "downloaded-video"
                assert "Fargo" in videos[0][0] and "/Release/" not in videos[0][0]
                writes_before_duplicate = list(storage.writes)
                await click(f"release:{plan_id}:{release_id}")
                await drain("download")
                await drain("rename")
                assert storage.added == [(selected_link, selected_path)]
                assert storage.writes == writes_before_duplicate
                # Recreate the consumer from its durable store before replay.
                restarted = RenameFeature(config=rename_config, host=hosts["rename"],
                    jobs=RenameJobStore(tmp_path / "rename.db"))
                restarted.bind_runtime(features["rename"].runtime)
                features["rename"].runtime.events["download.completed"] = restarted.download_completed
                features["rename"] = restarted
                writes = list(storage.writes)
                replay = await clients["rename"].request("event.deliver", {
                    "event_type": "download.completed", "event_id": "repeat", "payload": downloaded["result"],
                }, deadline=20)
                assert replay["duplicate"] is True
                await drain("rename")
                await drain("download")
                assert storage.writes == writes
                assert harness.coordinator.get_active_segment(operation_id) is None
        finally:
            for feature in features.values():
                for handle in getattr(feature, "session_expiry_handles", {}).values():
                    handle.cancel()
            await harness.asyncTearDown()

    asyncio.run(exercise())

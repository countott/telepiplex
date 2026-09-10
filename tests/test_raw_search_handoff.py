"""Real Search and Download services coordinated by the Host, without I/O."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "features/search/src", ROOT / "features/download/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))


class RawSearchHandoffTest(unittest.IsolatedAsyncioTestCase):
    async def test_pr_uses_search_segment_and_hands_actual_download_one_untyped_release(self):
        from app.handlers.interaction_handler import OperationReportSink
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.command_catalog import build_bot_commands
        from app.runtime.interaction_coordinator import InteractionCoordinator
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_download.service import DownloadFeature
        from telepiplex_search.adapters import prowlarr
        from telepiplex_search.runtime import main

        class DeferredRuntime:
            def __init__(self):
                self.workers = []

            def spawn(self, worker, **kwargs):
                self.workers.append(worker)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordinator = InteractionCoordinator(root / "host.db")
            router = CapabilityRouter()
            sink = OperationReportSink(coordinator, router)
            for name in ("download", "search"):
                manifest = PluginManifest.from_mapping(yaml.safe_load(
                    (ROOT / f"features/{name}/manifest.yaml").read_text()
                ))
                router.activate(name, manifest, SimpleNamespace())
            self.assertIn("pr", [item.command for item in build_bot_commands(router)])

            class DownloadHost:
                async def report_operation(self, report):
                    return await sink("download", report)

            download = DownloadFeature(config={}, host=DownloadHost(), client=None)
            download_runtime = DeferredRuntime()
            download.bind_runtime(download_runtime)
            submitted = []

            class SearchHost:
                async def report_operation(self, report):
                    return await sink("search", report)

                async def seal_operation_segment(self, operation_id, role, **kwargs):
                    segment = coordinator.get_active_segment(operation_id)
                    coordinator.claim_segment_delivery(segment.segment_id, owner_plugin_id="search", generation=segment.generation)
                    coordinator.bind_segment_message(segment.segment_id, owner_plugin_id="search", generation=segment.generation, chat_id=10, message_id=501, message_kind="text")
                    coordinator.record_segment_rendered(segment.segment_id, owner_plugin_id="search", generation=segment.generation, business_revision=segment.business_revision, projection_hash=segment.projection_hash)
                    sealing = coordinator.seal_segment("search", operation_id, role)
                    sealed = coordinator.complete_segment_seal(sealing.segment_id, owner_plugin_id="search", generation=sealing.generation)
                    return {"accepted": True, "state": sealed.state}

                async def call_capability(self, capability, method, payload, **kwargs):
                    submitted.append(payload)
                    return await download.download_capability({
                        "method": method, "payload": payload,
                        "context": {"idempotency_key": kwargs["idempotency_key"]},
                    })

            config_path = root / "search.yaml"
            config_path.write_text(yaml.safe_dump({
                "category_folder": [{"name": "剧集", "path": "/Series"}],
                "search": {"prowlarr": {"base_url": "http://prowlarr:9696", "api_key": "test"}},
            }))
            runtime = main(SimpleNamespace(
                config_path=config_path, state_path=root,
                manifest=yaml.safe_load((ROOT / "features/search/manifest.yaml").read_text()),
                host=SearchHost(), token="test",
            ))
            feature = runtime.commands["pr"].__self__
            try:
                with patch.object(prowlarr.requests, "get", return_value=Mock(json=lambda: [{
                    "title": "Some.Show.S01E01.1080p",
                    "magnetUrl": "magnet:?xt=urn:btih:" + "a" * 40,
                    "protocol": "torrent", "indexer": "Fixture", "seeders": 10,
                }])):
                    response = await runtime.commands["pr"]({
                        "command": "pr", "text": "/pr Some.Show.S01E01",
                        "chat_id": 10, "user_id": 1,
                    })
                    operation_id = response["operation"]["operation_id"]
                    initial = await sink("search", response["operation"])
                    self.assertTrue(initial["accepted"])
                    await asyncio.wait_for(feature.operations[operation_id]["task"], 2)
                record = coordinator.get(operation_id)
                self.assertEqual(record.state, "awaiting_input")
                segment = coordinator.get_active_segment(operation_id)
                self.assertEqual((segment.role, segment.presentation_kind), ("search", "text"))
                release_button = next(
                    button["callback_data"]
                    for row in record.details["keyboard"] for button in row
                    if ":release:" in button["callback_data"]
                )
                response = await runtime.callbacks["search"]({"payload": release_button.removeprefix("search:"), "chat_id": 10, "user_id": 1})
                self.assertTrue((await sink("search", response["operation"]))["accepted"])
                plan_id = feature.operations[operation_id]["plan_id"]
                response = await runtime.callbacks["search"]({"payload": f"raw_path:{plan_id}:0", "chat_id": 10, "user_id": 1})
                self.assertTrue((await sink("search", response["operation"]))["accepted"])
                await asyncio.wait_for(feature.operations[operation_id]["task"], 2)
                record = coordinator.get(operation_id)
                self.assertEqual((record.plugin_id, record.state), ("download", "running"))
                self.assertEqual(len(download_runtime.workers), 1)
                self.assertEqual(len(submitted), 1)
                self.assertNotIn("media_metadata", submitted[0])
                self.assertEqual(submitted[0]["selected_path"], "/Series")
                self.assertEqual(coordinator.get_segment(segment.segment_id).state, "sealed")
            finally:
                await runtime.close()
                for worker in download_runtime.workers:
                    worker.close()
                await sink.drain()
                coordinator.close()

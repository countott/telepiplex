import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
if str(SDK_SOURCE) not in sys.path:
    sys.path.insert(0, str(SDK_SOURCE))


class OperationPipelineEndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.core_broker import CoreBroker
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.handlers.interaction_handler import OperationReportSink

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.router = CapabilityRouter()
        self.journal = EventJournal(self.root / "core.db")
        self.coordinator = InteractionCoordinator(self.root / "core.db")
        self.dispatcher = EventDispatcher(
            self.router,
            self.journal,
            retry_interval=0.01,
            operation_coordinator=self.coordinator,
        )
        self.broker = CoreBroker(
            self.router,
            self.journal,
            self.root / "runtime/core.sock",
            dispatcher=self.dispatcher,
            operation_sink=OperationReportSink(self.coordinator),
        )
        self.runtimes = []
        self.runtime_tasks = []
        await self.broker.start()

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            await runtime.close()
        await asyncio.gather(*self.runtime_tasks, return_exceptions=True)
        await self.broker.close()
        self.coordinator.close()
        self.journal.close()
        self.temp.cleanup()

    @staticmethod
    def _manifest(
        plugin_id,
        *,
        commands=(),
        callbacks=(),
        subscribes=(),
        publishes=(),
    ):
        from app.core.plugin_manifest import PluginManifest

        return PluginManifest.from_mapping({
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.1.0",
            "core_api": ">=1.1,<2.0",
            "entry_point": (
                f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main"
            ),
            "provides": [],
            "requires": [],
            "subscribes": list(subscribes),
            "publishes": list(publishes),
            "commands": [
                {"name": name, "description": name}
                for name in commands
            ],
            "callbacks": list(callbacks),
            "source": {
                "repository": "origin",
                "branch": f"feature/{plugin_id}",
                "commit": "a" * 40,
            },
        })

    async def _start_runtime(self, manifest, token, **handlers):
        from app.core.plugin_rpc import RpcClient
        from telepiplex_plugin_sdk.runtime import FeatureRuntime

        socket_path = self.root / "runtime" / f"{manifest.plugin_id}.sock"
        runtime = FeatureRuntime(
            manifest={
                "plugin_id": manifest.plugin_id,
                "version": manifest.version,
                "core_api": manifest.core_api,
            },
            token=token,
            **handlers,
        )
        task = asyncio.create_task(runtime.serve(socket_path))
        async with asyncio.timeout(2):
            while not socket_path.exists():
                await asyncio.sleep(0.01)
        self.runtimes.append(runtime)
        self.runtime_tasks.append(task)
        self.broker.register(manifest.plugin_id, token, manifest)
        client = RpcClient(socket_path, token)
        self.router.activate(manifest.plugin_id, manifest, client)
        self.journal.set_subscriptions(
            manifest.plugin_id, manifest.subscribes
        )
        return client

    async def test_search_confirmation_rename_and_plex_enqueue_use_real_rpc_events(self):
        from telepiplex_plugin_sdk.core_client import CoreClient

        operation_id = "op-real-pipeline"
        media_manifest = self._manifest(
            "media-search",
            commands=("search",),
            callbacks=("media-search",),
            publishes=("download.completed",),
        )
        renaming_manifest = self._manifest(
            "renaming",
            subscribes=("download.completed",),
            publishes=("media.organized",),
        )
        plex_manifest = self._manifest(
            "plex-management",
            subscribes=("media.organized",),
        )
        media_core = CoreClient(self.broker.socket_path, "media-token")
        renaming_core = CoreClient(self.broker.socket_path, "renaming-token")
        plex_core = CoreClient(self.broker.socket_path, "plex-token")

        async def search_command(_request):
            return {"actions": [], "operation": {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "awaiting_input",
                "stage": "confirmation",
                "status_text": "等待确认搜索结果。",
                "control": "exit",
                "revision": 1,
            }}

        async def confirm_callback(_request):
            handoff = {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "handed_off",
                "stage": "handoff_renaming",
                "status_text": "搜索已确认，交给 renaming。",
                "control": "cancel",
                "revision": 2,
                "next_plugin_id": "renaming",
            }
            await media_core.report_operation(handoff)
            await media_core.publish_event(
                "download.completed",
                {
                    "operation_id": operation_id,
                    "operation_revision": 2,
                    "chat_id": 10,
                    "user_id": 1,
                    "final_path": "/Downloads/Movie",
                },
                idempotency_key="real-download-completed",
            )
            return {"actions": [], "operation": handoff}

        async def rename_event(request):
            payload = request["payload"]
            await renaming_core.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "renaming",
                "status_text": "正在重命名。",
                "control": "rollback",
                "revision": payload["operation_revision"] + 1,
            })
            handoff_revision = payload["operation_revision"] + 2
            await renaming_core.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "handed_off",
                "stage": "handoff_plex",
                "status_text": "重命名完成，交给 Plex。",
                "control": "cancel",
                "revision": handoff_revision,
                "next_plugin_id": "plex-management",
            })
            await renaming_core.publish_event(
                "media.organized",
                {
                    **payload,
                    "operation_revision": handoff_revision,
                    "final_path": "/Movies/Movie",
                },
                idempotency_key="real-media-organized",
            )
            return {"accepted": True}

        async def plex_event(request):
            payload = request["payload"]
            await plex_core.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "scanning",
                "status_text": "Plex 已入队。",
                "control": "cancel",
                "revision": payload["operation_revision"] + 1,
            })
            await plex_core.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "completed",
                "stage": "completed",
                "status_text": "Plex 管理完成。",
                "control": "",
                "revision": payload["operation_revision"] + 2,
            })
            return {"accepted": True}

        media_client = await self._start_runtime(
            media_manifest,
            "media-token",
            commands={"search": search_command},
            callbacks={"media-search": confirm_callback},
        )
        await self._start_runtime(
            renaming_manifest,
            "renaming-token",
            events={"download.completed": rename_event},
        )
        await self._start_runtime(
            plex_manifest,
            "plex-token",
            events={"media.organized": plex_event},
        )

        opened = await media_client.request(
            "command.dispatch",
            {"command": "search", "args": ["Movie"]},
            deadline=2,
        )
        self.coordinator.report("media-search", opened["operation"])
        await media_client.request(
            "callback.dispatch",
            {"namespace": "media-search", "payload": "confirm"},
            deadline=2,
        )

        async with asyncio.timeout(3):
            while self.coordinator.get(operation_id).state != "completed":
                await asyncio.sleep(0.01)

        record = self.coordinator.get(operation_id)
        self.assertEqual(record.plugin_id, "plex-management")
        self.assertEqual(record.stage, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))
        self.assertEqual(self.journal.pending("renaming"), [])
        self.assertEqual(self.journal.pending("plex-management"), [])


if __name__ == "__main__":
    unittest.main()

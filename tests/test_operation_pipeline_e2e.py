import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
SDK_SOURCE = ROOT / "sdk/src"
if str(SDK_SOURCE) not in sys.path:
    sys.path.insert(0, str(SDK_SOURCE))


class OperationPipelineEndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.runtime_broker import RuntimeBroker
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator
        from app.handlers.interaction_handler import OperationReportSink

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.router = CapabilityRouter()
        self.journal = EventJournal(self.root / "host.db")
        self.coordinator = InteractionCoordinator(self.root / "host.db")
        self.dispatcher = EventDispatcher(
            self.router,
            self.journal,
            retry_interval=0.01,
            operation_coordinator=self.coordinator,
        )
        self.operation_sink = OperationReportSink(self.coordinator)
        self.ownership = []
        self.operation_sink.attach(
            lambda record: self.ownership.append(record.plugin_id)
        )
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            self.root / "runtime/host.sock",
            dispatcher=self.dispatcher,
            operation_sink=self.operation_sink,
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
        provides=(),
        requires=(),
    ):
        from app.runtime.plugin_manifest import PluginManifest

        return PluginManifest.from_mapping({
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": "1.1.0",
            "host_api": ">=1.1,<2.0",
            "entry_point": (
                f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main"
            ),
            "provides": [
                {"name": name, "exclusive": True}
                for name in provides
            ],
            "requires": list(requires),
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
        from app.runtime.plugin_rpc import RpcClient
        from telepiplex_plugin_sdk.runtime import FeatureRuntime

        socket_path = self.root / "runtime" / f"{manifest.plugin_id}.sock"
        runtime = FeatureRuntime(
            manifest={
                "plugin_id": manifest.plugin_id,
                "version": manifest.version,
                "host_api": manifest.host_api,
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

    async def test_full_pipeline_handoff_control_and_menu_use_real_rpc_events(self):
        from app.runtime.command_catalog import build_bot_commands, sync_bot_commands
        from app.handlers.interaction_handler import operation_control_callback
        from telepiplex_plugin_sdk.host_client import HostClient

        operation_id = "op-real-pipeline"
        media_manifest = self._manifest(
            "search",
            commands=("search",),
            callbacks=("search",),
            requires=("download.provider",),
        )
        open_manifest = self._manifest(
            "download",
            commands=("magnet",),
            provides=("download.provider",),
            publishes=("download.completed",),
        )
        rename_manifest = self._manifest(
            "rename",
            commands=("rename_config",),
            subscribes=("download.completed",),
            publishes=("media.organized",),
        )
        plex_manifest = self._manifest(
            "sync",
            commands=("plex",),
            subscribes=("media.organized",),
        )
        media_host = HostClient(self.broker.socket_path, "media-token")
        open_host = HostClient(self.broker.socket_path, "open-token")
        rename_host = HostClient(self.broker.socket_path, "rename-token")
        plex_host = HostClient(self.broker.socket_path, "plex-token")
        controls = {
            "search": [],
            "download": [],
            "rename": [],
            "sync": [],
        }

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
                "stage": "handoff_download",
                "status_text": "搜索已确认，交给 download。",
                "control": "cancel",
                "revision": 2,
                "next_plugin_id": "download",
            }
            await media_host.report_operation(handoff)
            await media_host.call_capability(
                "download.provider",
                "submit",
                {
                    "operation_id": operation_id,
                    "operation_revision": 2,
                    "chat_id": 10,
                    "user_id": 1,
                    "final_path": "/Downloads/Movie",
                },
                idempotency_key="real-download-submit",
            )
            return {"actions": [], "operation": handoff}

        async def open_download(request):
            payload = request["payload"]
            await open_host.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "downloading",
                "status_text": "115 正在下载。",
                "control": "cancel",
                "revision": payload["operation_revision"] + 1,
            })
            handoff_revision = payload["operation_revision"] + 2
            handoff = {
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "handed_off",
                "stage": "handoff_rename",
                "status_text": "115 下载完成，交给 rename。",
                "control": "cancel",
                "revision": handoff_revision,
                "next_plugin_id": "rename",
            }
            await open_host.report_operation(handoff)
            await open_host.publish_event(
                "download.completed",
                {
                    **payload,
                    "operation_revision": handoff_revision,
                    "final_path": "/Downloads/Movie",
                },
                idempotency_key="real-download-completed",
            )
            return {"accepted": True, "operation": handoff}

        async def rename_event(request):
            payload = request["payload"]
            await rename_host.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "rename",
                "status_text": "正在重命名。",
                "control": "rollback",
                "revision": payload["operation_revision"] + 1,
            })
            handoff_revision = payload["operation_revision"] + 2
            await rename_host.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "handed_off",
                "stage": "handoff_plex",
                "status_text": "重命名完成，交给 Plex。",
                "control": "cancel",
                "revision": handoff_revision,
                "next_plugin_id": "sync",
            })
            await rename_host.publish_event(
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
            running = {
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "scanning",
                "status_text": "Plex 已入队。",
                "control": "cancel",
                "revision": payload["operation_revision"] + 1,
            }
            await plex_host.report_operation(running)
            return {"accepted": True}

        async def passive_control(plugin_id, request):
            controls[plugin_id].append(dict(request))
            raise AssertionError(f"control reached stale owner {plugin_id}")

        async def plex_control(request):
            controls["sync"].append(dict(request))
            record = self.coordinator.get(operation_id)
            terminal = {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "cancelled",
                "stage": record.stage,
                "status_text": "Plex 任务已取消。",
                "control": "",
                "revision": record.revision + 1,
            }
            await plex_host.report_operation(terminal)
            return {"actions": [], "operation": terminal}

        await self._start_runtime(
            open_manifest,
            "open-token",
            capabilities={"download.provider": open_download},
            operation_control=lambda request: passive_control("download", request),
        )
        await self._start_runtime(
            rename_manifest,
            "rename-token",
            events={"download.completed": rename_event},
            operation_control=lambda request: passive_control("rename", request),
        )
        await self._start_runtime(
            plex_manifest,
            "plex-token",
            events={"media.organized": plex_event},
            operation_control=plex_control,
        )
        media_client = await self._start_runtime(
            media_manifest,
            "media-token",
            commands={"search": search_command},
            callbacks={"search": confirm_callback},
            operation_control=lambda request: passive_control("search", request),
        )

        command_names = [item.command for item in build_bot_commands(self.router)]
        for command in ("search", "magnet", "plex"):
            self.assertIn(command, command_names)
        self.assertNotIn("rename_config", command_names)
        menu_bot = SimpleNamespace(set_my_commands=AsyncMock())
        self.assertTrue(await sync_bot_commands(
            SimpleNamespace(bot=menu_bot), self.router
        ))
        synced = [
            item.command
            for item in menu_bot.set_my_commands.await_args.args[0]
        ]
        self.assertEqual(synced, command_names)

        opened = await media_client.request(
            "command.dispatch",
            {"command": "search", "args": ["Movie"]},
            deadline=2,
        )
        self.coordinator.report("search", opened["operation"])
        await media_client.request(
            "callback.dispatch",
            {"namespace": "search", "payload": "confirm"},
            deadline=2,
        )

        async with asyncio.timeout(3):
            while self.coordinator.get(operation_id).plugin_id != "sync":
                await asyncio.sleep(0.01)

        record = self.coordinator.get(operation_id)
        self.assertEqual(record.plugin_id, "sync")
        self.assertEqual(record.stage, "scanning")
        self.assertIsNotNone(self.coordinator.active(10, 1))
        async with asyncio.timeout(1):
            while not self.ownership or self.ownership[-1] != "sync":
                await asyncio.sleep(0.01)
        owners = []
        for plugin_id in self.ownership:
            if not owners or owners[-1] != plugin_id:
                owners.append(plugin_id)
        self.assertEqual(
            owners,
            ["search", "download", "rename", "sync"],
        )

        bot = SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=90)
            ),
            edit_message_text=AsyncMock(),
        )
        application = SimpleNamespace(bot=bot, bot_data={
            "telepiplex_interaction_coordinator": self.coordinator,
            "telepiplex_plugin_router": self.router,
        })
        query = SimpleNamespace(
            data=f"host-operation:cancel:{operation_id}",
            answer=AsyncMock(),
            message=SimpleNamespace(message_id=90),
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=query,
        )
        await operation_control_callback(
            update, SimpleNamespace(application=application, bot=bot)
        )

        self.assertEqual(len(controls["sync"]), 1)
        self.assertEqual(controls["search"], [])
        self.assertEqual(controls["download"], [])
        self.assertEqual(controls["rename"], [])
        self.assertEqual(self.coordinator.get(operation_id).state, "cancelled")
        self.assertIsNone(self.coordinator.active(10, 1))
        self.assertEqual(self.journal.pending("rename"), [])
        self.assertEqual(self.journal.pending("sync"), [])


if __name__ == "__main__":
    unittest.main()

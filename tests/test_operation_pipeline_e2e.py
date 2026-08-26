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
        from app.handlers.interaction_handler import (
            OperationMilestoneSink,
            OperationReportSink,
        )

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
        self.operation_sink = OperationReportSink(
            self.coordinator, self.router
        )
        self.ownership = []
        self.operation_sink.attach(
            lambda record: self.ownership.append(record.plugin_id)
        )
        self.milestone_deliveries = []
        self.block_milestone_delivery = False
        self.milestone_delivery_started = asyncio.Event()
        self.release_milestone_delivery = asyncio.Event()

        async def deliver_milestone(record, mode, photo_url, text):
            self.milestone_deliveries.append({
                "plugin_id": record.plugin_id,
                "operation_id": record.operation_id,
                "mode": mode,
                "photo_url": photo_url,
                "text": text,
            })
            if self.block_milestone_delivery:
                self.milestone_delivery_started.set()
                await self.release_milestone_delivery.wait()
            return {
                "accepted": True,
                "message_id": 100 + len(self.milestone_deliveries),
                "message_kind": "photo" if mode == "identity" else "text",
            }

        self.milestone_sink = OperationMilestoneSink(
            self.coordinator,
            deliver_milestone,
        )
        await self.milestone_sink.start()
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            self.root / "runtime/host.sock",
            dispatcher=self.dispatcher,
            milestone_sink=self.milestone_sink,
            operation_sink=self.operation_sink,
        )
        self.runtimes = []
        self.runtime_tasks = []
        await self.broker.start()

    async def asyncTearDown(self):
        self.release_milestone_delivery.set()
        for runtime in self.runtimes:
            await runtime.close()
        await asyncio.gather(*self.runtime_tasks, return_exceptions=True)
        await self.broker.close()
        await self.operation_sink.drain()
        await self.milestone_sink.drain()
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

    async def test_full_pipeline_ends_at_rename_without_sync_or_plex(self):
        from app.runtime.command_catalog import build_bot_commands, sync_bot_commands
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
        )
        media_host = HostClient(self.broker.socket_path, "media-token")
        open_host = HostClient(self.broker.socket_path, "open-token")
        rename_host = HostClient(self.broker.socket_path, "rename-token")
        controls = {
            "search": [],
            "download": [],
            "rename": [],
        }
        download_call_count = 0
        rename_event_count = 0

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
            await media_host.report_operation({
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "identity_confirmation",
                "status_text": "正在确认媒体身份。",
                "control": "cancel",
                "revision": 2,
            })
            await media_host.publish_operation_milestone(
                operation_id,
                "media-real-pipeline",
                "🎬 Movie (Movie)",
                photo_url="https://img.example/movie.jpg",
            )
            await media_host.report_operation({
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "prowlarr_search",
                "status_text": "正在搜索资源。",
                "control": "cancel",
                "revision": 3,
            })
            handoff = {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "handed_off",
                "stage": "handoff_download",
                "status_text": "搜索已确认，交给 download。",
                "control": "cancel",
                "revision": 4,
                "next_plugin_id": "download",
            }
            await media_host.report_operation(handoff)
            await media_host.seal_operation_stage(
                operation_id,
                "search-stage-complete:real-pipeline",
                "✅ 资源搜索已完成。",
            )
            await media_host.call_capability(
                "download.provider",
                "submit",
                {
                    "operation_id": operation_id,
                    "operation_revision": 4,
                    "chat_id": 10,
                    "user_id": 1,
                    "selected_path": "/Downloads",
                    "final_path": "/Downloads/Movie",
                },
                idempotency_key="real-download-submit",
            )
            return {"actions": [], "operation": handoff}

        async def open_download(request):
            nonlocal download_call_count
            download_call_count += 1
            payload = request["payload"]
            await open_host.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "running",
                "stage": "preparing_submission",
                "status_text": "正在准备提交 115 离线下载任务。",
                "control": "cancel",
                "revision": payload["operation_revision"] + 1,
                "details": {
                    "effect_receipt": {
                        "effect_key": "download.submit:real-download-submit",
                        "state": "completed",
                        "receipt": {
                            "job_id": "real-download-submit",
                            "selected_path": payload["selected_path"],
                        },
                    },
                },
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
            await open_host.seal_operation_stage(
                payload["operation_id"],
                "download-stage-complete:real-pipeline",
                "✅ 115 下载已完成。",
            )
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
            nonlocal rename_event_count
            rename_event_count += 1
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
            await rename_host.seal_operation_stage(
                payload["operation_id"],
                "rename-stage-complete:real-pipeline",
                "✅ 媒体整理已完成。",
            )
            await rename_host.report_operation({
                "operation_id": payload["operation_id"],
                "chat_id": payload["chat_id"],
                "user_id": payload["user_id"],
                "state": "completed",
                "stage": "completed",
                "status_text": "重命名完成。",
                "control": "",
                "revision": payload["operation_revision"] + 2,
                "details": {
                    "organized": True,
                    "cleanup_complete": True,
                    "partial_completed": False,
                    "final_path": "/Movies/Movie",
                    "effect_receipt": {
                        "effect_key": "rename.organize:real-download-submit",
                        "state": "completed",
                        "receipt": {
                            "job_id": "real-download-submit",
                            "organized": True,
                            "cleanup_complete": True,
                            "partial_completed": False,
                            "final_path": "/Movies/Movie",
                        },
                    },
                },
            })
            return {"accepted": True}

        async def passive_control(plugin_id, request):
            controls[plugin_id].append(dict(request))
            raise AssertionError(f"control reached stale owner {plugin_id}")

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
        media_client = await self._start_runtime(
            media_manifest,
            "media-token",
            commands={"search": search_command},
            callbacks={"search": confirm_callback},
            operation_control=lambda request: passive_control("search", request),
        )

        command_names = [item.command for item in build_bot_commands(self.router)]
        for command in ("search", "magnet"):
            self.assertIn(command, command_names)
        self.assertNotIn("plex", command_names)
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
        self.block_milestone_delivery = True
        callback_result = await media_client.request(
            "callback.dispatch",
            {"namespace": "search", "payload": "confirm"},
            deadline=2,
        )
        self.assertEqual(callback_result["actions"], [])
        async with asyncio.timeout(1):
            await self.milestone_delivery_started.wait()
        self.assertFalse(self.release_milestone_delivery.is_set())

        async with asyncio.timeout(3):
            while self.coordinator.get(operation_id).state != "completed":
                await asyncio.sleep(0.01)
        await self.operation_sink.drain()

        record = self.coordinator.get(operation_id)
        self.assertEqual(record.plugin_id, "rename")
        self.assertEqual(record.state, "completed")
        self.assertEqual(record.stage, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))
        async with asyncio.timeout(1):
            while not self.ownership or self.ownership[-1] != "rename":
                await asyncio.sleep(0.01)
        owners = []
        for plugin_id in self.ownership:
            if not owners or owners[-1] != plugin_id:
                owners.append(plugin_id)
        self.assertEqual(
            owners,
            ["search", "download", "rename"],
        )
        self.assertEqual(download_call_count, 1)
        self.assertEqual(rename_event_count, 1)
        self.assertEqual(
            {
                (item["plugin_id"], item["mode"])
                for item in self.milestone_deliveries
            },
            {
                ("search", "identity"),
                ("search", "stage"),
                ("download", "stage"),
                ("rename", "stage"),
            },
        )

        self.assertEqual(controls["search"], [])
        self.assertEqual(controls["download"], [])
        self.assertEqual(controls["rename"], [])
        self.assertIsNone(self.router.plugin_route("sync"))
        event_types = [
            row["event_type"]
            for row in self.journal._connection.execute(
                "SELECT event_type FROM events ORDER BY created_at, id"
            )
        ]
        self.assertEqual(event_types, ["download.completed"])
        self.assertEqual(self.journal.pending("rename"), [])
        handoffs = self.coordinator.get_handoffs(operation_id)
        self.assertEqual(
            [
                (item.source_plugin_id, item.target_plugin_id, item.state)
                for item in handoffs
            ],
            [
                ("search", "download", "accepted"),
                ("download", "rename", "accepted"),
            ],
        )
        self.assertEqual(handoffs[0].event_id, "")
        self.assertTrue(handoffs[1].event_id)
        effects = self.coordinator.get_effect_receipts(operation_id)
        self.assertEqual(
            [
                (item.effect_key, item.plugin_id, item.state, dict(item.receipt))
                for item in effects
            ],
            [
                (
                    "download.submit:real-download-submit",
                    "download",
                    "completed",
                    {
                        "job_id": "real-download-submit",
                        "selected_path": "/Downloads",
                    },
                ),
                (
                    "rename.organize:real-download-submit",
                    "rename",
                    "completed",
                    {
                        "job_id": "real-download-submit",
                        "organized": True,
                        "cleanup_complete": True,
                        "partial_completed": False,
                        "final_path": "/Movies/Movie",
                    },
                ),
            ],
        )
        milestone_rows = self.coordinator._connection.execute(
            "SELECT delivery_state FROM operation_milestones "
            "WHERE operation_id = ? ORDER BY created_at, milestone_id",
            (operation_id,),
        ).fetchall()
        self.assertEqual(len(milestone_rows), 4)
        self.assertTrue(all(
            row["delivery_state"] in {"pending", "delivering"}
            for row in milestone_rows
        ))
        self.release_milestone_delivery.set()
        self.assertTrue(await self.milestone_sink.drain(timeout=2))

    async def test_real_rpc_rejects_handoff_to_uninstalled_feature_without_mutation(self):
        from telepiplex_plugin_sdk.host_client import HostClient

        manifest = self._manifest(
            "search",
            commands=("search",),
            callbacks=("search",),
        )
        client = HostClient(self.broker.socket_path, "search-token")
        await self._start_runtime(
            manifest,
            "search-token",
            commands={"search": lambda _request: {"actions": []}},
        )
        initial = {
            "operation_id": "op-missing-download",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "release_selection",
            "status_text": "等待选择片源。",
            "control": "cancel",
            "revision": 1,
        }
        accepted = await client.report_operation(initial)
        rejected = await client.report_operation({
            **initial,
            "state": "handed_off",
            "stage": "submitting_download",
            "status_text": "正在交给下载模块。",
            "revision": 2,
            "next_plugin_id": "download",
        })

        self.assertTrue(accepted["accepted"])
        self.assertFalse(rejected["accepted"])
        self.assertEqual(
            rejected["error_code"], "handoff_target_unavailable"
        )
        self.assertEqual(rejected["target_plugin_id"], "download")
        current = self.coordinator.get("op-missing-download")
        self.assertEqual(
            (current.plugin_id, current.state, current.revision),
            ("search", "running", 1),
        )

    async def test_download_handoff_commit_response_loss_restarts_with_exact_revision(self):
        from app.handlers.interaction_handler import OperationReportSink

        for source in (
            ROOT / "features/download/src",
            ROOT / "features/rename/src",
        ):
            if str(source) not in sys.path:
                sys.path.insert(0, str(source))
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature
        from telepiplex_rename.jobs import RenameJobStore
        from telepiplex_rename.service import RenameFeature

        operation_id = "op-download-handoff-response-loss"
        sink = OperationReportSink(self.coordinator)
        for plugin_id, report in (
            ("search", {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "search",
                "status_text": "searching",
                "control": "cancel",
                "revision": 1,
            }),
            ("search", {
                "operation_id": operation_id,
                "chat_id": 10,
                "user_id": 1,
                "state": "handed_off",
                "stage": "handoff_download",
                "status_text": "to download",
                "control": "cancel",
                "revision": 2,
                "next_plugin_id": "download",
            }),
        ):
            accepted = await sink(plugin_id, report)
            self.assertTrue(accepted["accepted"])

        class DeferredRuntime:
            def __init__(self):
                self.tasks = {}

            def spawn(self, awaitable, *, task_id):
                self.tasks[task_id] = awaitable
                return awaitable

        class CompletedClient:
            def __init__(self):
                self.add_calls = 0

            def add_offline_task(self, _link, _selected_path):
                self.add_calls += 1
                return True

            def wait_for_download(self, _link, **_kwargs):
                return {
                    "resource_name": "Movie.2024.mkv",
                    "info_hash": "hash-response-loss",
                    "progress": 100,
                }

            def get_file_tree(self, path):
                return [{
                    "name": "Movie.2024.mkv",
                    "relative_path": "Movie.2024.mkv",
                    "path": path,
                    "is_dir": False,
                    "file_id": "movie-1",
                    "size": 200 * 1024 * 1024,
                }]

            def del_offline_task(self, _info_hash, _del_source_file=0):
                return True

        organization_calls = []

        class RenameCoreHost:
            async def report_operation(self, report):
                return await sink("rename", report)

        rename_jobs = RenameJobStore(self.root / "rename-response-loss.db")
        rename_feature = RenameFeature(
            config={"unorganized_path": "/Unorganized"},
            host=RenameCoreHost(),
            jobs=rename_jobs,
        )
        rename_feature.bind_runtime(DeferredRuntime())

        def capture_organization(job_id, payload, accepted_operation_id):
            organization_calls.append((
                job_id,
                payload["operation_revision"],
                accepted_operation_id,
            ))
            return None

        rename_feature._spawn_organization = capture_organization

        class DownloadCoreHost:
            def __init__(self):
                self.lost_handoff_response = False
                self.handoff_revisions = []
                self.publish_count = 0

            async def report_operation(self, report):
                response = await sink("download", report)
                if report.get("state") == "handed_off":
                    self.handoff_revisions.append(report["revision"])
                    if not self.lost_handoff_response:
                        self.lost_handoff_response = True
                        raise RuntimeError(
                            "Core committed Download handoff before response loss"
                        )
                return response

            async def seal_operation_segment(
                self,
                operation_id,
                role,
                **_kwargs,
            ):
                segment = self_coordinator.get_active_segment(operation_id)
                if segment.state == "creating":
                    self_coordinator.claim_segment_delivery(
                        segment.segment_id,
                        owner_plugin_id="download",
                        generation=segment.generation,
                    )
                    self_coordinator.bind_segment_message(
                        segment.segment_id,
                        owner_plugin_id="download",
                        generation=segment.generation,
                        chat_id=10,
                        message_id=7001,
                    )
                    segment = self_coordinator.get_active_segment(
                        operation_id
                    )
                self_coordinator.record_segment_rendered(
                    segment.segment_id,
                    owner_plugin_id="download",
                    generation=segment.generation,
                    business_revision=segment.business_revision,
                    projection_hash=segment.projection_hash,
                )
                sealing = self_coordinator.seal_segment(
                    "download", operation_id, role
                )
                sealed = self_coordinator.complete_segment_seal(
                    sealing.segment_id,
                    owner_plugin_id="download",
                    generation=sealing.generation,
                )
                return {"accepted": True, "state": sealed.state}

            async def publish_event(self, event_type, payload, **_kwargs):
                self.publish_count += 1
                self.assert_event_type = event_type
                self.published_payload = dict(payload)
                event_id = "event-download-handoff-response-loss"
                handoff = self_coordinator.capture_handoff(
                    payload["operation_id"], "download"
                )
                self_coordinator.record_handoff_event(
                    payload["operation_id"],
                    event_id,
                    "rename",
                    handoff_key=handoff.handoff_key,
                )
                self.rename_result = await rename_feature.download_completed({
                    "event_id": event_id,
                    "payload": payload,
                })
                return {"event_id": event_id}

            async def notify_user(self, *_args, **_kwargs):
                return {"accepted": True}

        self_coordinator = self.coordinator
        host = DownloadCoreHost()
        client = CompletedClient()
        download_jobs = DownloadJobStore(self.root / "download-response-loss.db")
        feature = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host,
            client=client,
            jobs=download_jobs,
        )
        runtime = DeferredRuntime()
        feature.bind_runtime(runtime)
        await feature.download_capability({
            "method": "submit",
            "payload": {
                "link": "magnet:?xt=urn:btih:" + "a" * 40,
                "selected_path": "/Downloads",
                "operation_id": operation_id,
                "operation_revision": 2,
                "chat_id": 10,
                "user_id": 1,
            },
            "context": {"idempotency_key": "download-handoff-response-loss"},
        })
        await runtime.tasks.pop("download-handoff-response-loss")

        durable = download_jobs.get("download-handoff-response-loss")
        self.assertEqual(durable["state"], "downloaded")
        self.assertIn("download_handoff_report", durable["result"])
        committed_revision = self.coordinator.get(operation_id).revision
        self.assertEqual(
            durable["result"]["download_handoff_report"]["revision"],
            committed_revision,
        )

        restored = DownloadFeature(
            config={"download_timeout": 30, "poll_interval": 0.01},
            host=host,
            client=client,
            jobs=download_jobs,
        )
        restored_runtime = DeferredRuntime()
        restored.bind_runtime(restored_runtime)
        await restored_runtime.tasks.pop("download-handoff-response-loss")

        core = self.coordinator.get(operation_id)
        self.assertEqual((core.plugin_id, core.state), ("rename", "running"))
        self.assertEqual(host.handoff_revisions, [
            committed_revision,
            committed_revision,
        ])
        self.assertEqual(host.publish_count, 1)
        self.assertEqual(host.assert_event_type, "download.completed")
        self.assertNotIn("download_handoff_report", host.published_payload)
        self.assertNotIn("download_handoff_accepted", host.published_payload)
        self.assertTrue(host.rename_result["accepted"])
        self.assertEqual(host.rename_result["state"], "running")
        self.assertEqual(organization_calls, [
            (
                "download-handoff-response-loss",
                committed_revision,
                operation_id,
            ),
        ])
        self.assertEqual(client.add_calls, 1)
        self.assertEqual(
            download_jobs.get("download-handoff-response-loss")["state"],
            "completed",
        )
        handoffs = self.coordinator.get_handoffs(operation_id)
        download_to_rename = next(
            item for item in handoffs
            if item.source_plugin_id == "download"
            and item.target_plugin_id == "rename"
        )
        self.assertEqual(download_to_rename.state, "accepted")
        self.assertEqual(
            download_to_rename.event_id,
            "event-download-handoff-response-loss",
        )


if __name__ == "__main__":
    unittest.main()

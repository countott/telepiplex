import tempfile
import logging
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


class ProviderClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        return {"provider": "download", "payload": params["payload"]}


class FailingProviderClient:
    async def request(self, method, params, *, deadline, idempotency_key=""):
        from app.runtime.plugin_contract import ContractError

        raise ContractError(
            "metadata_source_unavailable",
            "metadata providers are temporarily unavailable",
        )


def manifest(plugin_id, *, provides=(), requires=(), publishes=(), subscribes=()):
    from app.runtime.plugin_manifest import PluginManifest

    return PluginManifest.from_mapping({
        "plugin_id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "host_api": ">=1.0,<2.0",
        "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
        "provides": [{"name": name, "exclusive": True} for name in provides],
        "requires": list(requires),
        "subscribes": list(subscribes),
        "publishes": list(publishes),
        "commands": [],
        "callbacks": [],
        "source": {
            "repository": "origin",
            "branch": f"feature/{plugin_id}",
            "commit": "a" * 40,
        },
    })


class RuntimeBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.interaction_coordinator import InteractionCoordinator
        from app.runtime.runtime_broker import RuntimeBroker
        from app.runtime.event_journal import EventJournal

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.router = CapabilityRouter()
        self.journal = EventJournal(root / "host.db")
        self.coordinator = InteractionCoordinator(root / "host.db")
        self.notifications = []
        self.milestones = []
        self.operation_sink = AsyncMock(return_value={"accepted": True, "revision": 1})
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            root / "host.sock",
            notification_sink=lambda user_id, text: self.notifications.append((user_id, text)),
            milestone_sink=lambda plugin_id, payload: self.milestones.append(
                (plugin_id, payload)
            ),
            operation_sink=self.operation_sink,
            operation_coordinator=self.coordinator,
        )
        await self.broker.start()

    async def asyncTearDown(self):
        await self.broker.close()
        self.coordinator.close()
        self.journal.close()
        self.temp.cleanup()

    async def test_feature_calls_only_declared_required_capability(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        provider = ProviderClient()
        self.router.activate(
            "download",
            manifest("download", provides=("download.provider",)),
            provider,
        )
        caller = manifest("search", requires=("download.provider",))
        self.broker.register("search", "caller-token", caller)
        client = HostClient(self.broker.socket_path, "caller-token")

        result = await client.call_capability(
            "download.provider",
            "submit",
            {"url": "magnet:?xt=test"},
            deadline=2,
            idempotency_key="plan-1",
        )
        self.assertEqual(result["provider"], "download")
        self.assertEqual(provider.calls[0][3], "plan-1")
        self.assertLessEqual(provider.calls[0][2], 2)

        undeclared = manifest("echo")
        self.broker.register("echo", "echo-token", undeclared)
        with self.assertRaises(FeatureError) as raised:
            await HostClient(self.broker.socket_path, "echo-token").call_capability(
                "download.provider", "submit", {}, deadline=1
            )
        self.assertEqual(raised.exception.code, "capability_not_declared")

    async def test_feature_to_host_rpc_binds_diagnostics_while_routing_provider(self):
        from telepiplex_plugin_sdk import HostClient
        from telepiplex_plugin_sdk.diagnostics import (
            bind_diagnostic_context,
            current_diagnostic_context,
        )

        class ContextProvider:
            async def request(self, method, params, *, deadline, idempotency_key=""):
                return {"context": current_diagnostic_context()}

        self.router.activate(
            "download",
            manifest("download", provides=("download.provider",)),
            ContextProvider(),
        )
        self.broker.register(
            "search",
            "search-token",
            manifest("search", requires=("download.provider",)),
        )
        with bind_diagnostic_context(
            trace_id="TRC-HOST-1",
            span_id="SPN-FEATURE-PARENT",
            operation_id="operation-host-1",
        ):
            result = await HostClient(
                self.broker.socket_path,
                "search-token",
            ).call_capability(
                "download.provider",
                "submit",
                {"value": 1},
                deadline=1,
            )

        observed = result["context"]
        self.assertEqual(observed["trace_id"], "TRC-HOST-1")
        self.assertEqual(observed["parent_span_id"], "SPN-FEATURE-PARENT")
        self.assertEqual(observed["operation_id"], "operation-host-1")
        self.assertTrue(observed["span_id"].startswith("SPN-"))
        self.assertTrue(observed["request_id"])

    async def test_host_client_logs_typed_start_and_failure_with_stable_error_code(self):
        from telepiplex_plugin_sdk import FeatureError, HostClient

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        self.broker.register("echo", "echo-token", manifest("echo"))
        logger = logging.getLogger("telepiplex.rpc.host")
        original_level = logger.level
        logger.setLevel(logging.INFO)
        capture = Capture()
        logger.addHandler(capture)
        self.addCleanup(logger.removeHandler, capture)
        self.addCleanup(logger.setLevel, original_level)

        with self.assertRaises(FeatureError) as raised:
            await HostClient(self.broker.socket_path, "echo-token").call_capability(
                "download.provider",
                "submit",
                {"value": "x" * 100_000},
                deadline=1,
            )

        started = next(
            record for record in records
            if getattr(record, "event_name", "") == "rpc.host.started"
        )
        failed = next(
            record for record in records
            if getattr(record, "event_name", "") == "rpc.host.failed"
        )
        self.assertEqual(raised.exception.code, "capability_not_declared")
        self.assertEqual(started.diagnostic_fields["transport"]["method"], "capability.call")
        self.assertGreater(
            started.diagnostic_fields["input"]["params"][
                "_diagnostic_summary"
            ]["bytes"],
            100_000,
        )
        self.assertEqual(
            failed.diagnostic_fields["output"]["error_code"],
            "capability_not_declared",
        )
        self.assertGreaterEqual(failed.diagnostic_fields["duration_ms"], 0)

    async def test_broker_logs_typed_receive_and_completion_with_plugin_method_and_duration(self):
        from telepiplex_plugin_sdk import HostClient

        logger = Mock()
        self.broker.logger = logger
        self.broker.register("notify", "notify-token", manifest("notify"))

        result = await HostClient(
            self.broker.socket_path,
            "notify-token",
        ).notify_user(123, "处理完成", deadline=1)

        events = [
            call.kwargs for call in logger.info.call_args_list
            if call.kwargs.get("event_name")
        ]
        received = next(
            item for item in events
            if item["event_name"] == "rpc.host.received"
        )
        completed = next(
            item for item in events
            if item["event_name"] == "rpc.host.completed"
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(received["diagnostic_fields"]["transport"]["plugin_id"], "notify")
        self.assertEqual(received["diagnostic_fields"]["transport"]["method"], "notification.send")
        self.assertEqual(
            received["diagnostic_fields"]["transport"]["request_id"],
            completed["diagnostic_fields"]["transport"]["request_id"],
        )
        self.assertGreaterEqual(completed["diagnostic_fields"]["duration_ms"], 0)

    async def test_provider_error_code_survives_the_full_feature_to_feature_route(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.router.activate(
            "search",
            manifest("search", provides=("media.search",)),
            FailingProviderClient(),
        )
        self.broker.register(
            "rename",
            "rename-token",
            manifest("rename", requires=("media.search",)),
        )

        with self.assertRaises(FeatureError) as raised:
            await HostClient(
                self.broker.socket_path,
                "rename-token",
            ).call_capability(
                "media.search",
                "resolve_metadata",
                {"query": "Honey and Clover"},
                deadline=1,
            )

        self.assertEqual(
            raised.exception.code,
            "metadata_source_unavailable",
        )
        self.assertEqual(
            raised.exception.message,
            "metadata providers are temporarily unavailable",
        )

    async def test_feature_publishes_only_declared_event_and_token_is_revocable(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.journal.set_subscriptions("rename", ["download.completed"])
        publisher = manifest("download", publishes=("download.completed",))
        self.broker.register("download", "publisher-token", publisher)
        client = HostClient(self.broker.socket_path, "publisher-token")

        event = await client.publish_event(
            "download.completed",
            {"path": "/downloads/show"},
            idempotency_key="download-1",
            deadline=1,
        )
        self.assertTrue(event["event_id"])
        self.assertEqual(len(self.journal.pending("rename")), 1)

        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("media.organized", {}, deadline=1)
        self.assertEqual(raised.exception.code, "event_not_declared")

        self.broker.unregister("publisher-token")
        with self.assertRaises(FeatureError) as raised:
            await client.publish_event("download.completed", {}, deadline=1)
        self.assertEqual(raised.exception.code, "unauthorized")

    async def test_event_publish_records_the_matching_handoff_submission(self):
        from telepiplex_plugin_sdk import HostClient

        report = {
            "operation_id": "op-event-receipt",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "downloading",
            "status_text": "下载中",
            "control": "cancel",
            "revision": 1,
        }
        self.coordinator.report("download", report)
        self.coordinator.report(
            "download",
            {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            },
        )
        self.broker.register(
            "download",
            "receipt-token",
            manifest("download", publishes=("download.completed",)),
        )
        client = HostClient(self.broker.socket_path, "receipt-token")

        first = await client.publish_event(
            "download.completed",
            {"operation_id": "op-event-receipt", "job_id": "job-1"},
            idempotency_key="rename.enqueue:job-1",
            deadline=1,
        )
        duplicate = await client.publish_event(
            "download.completed",
            {"operation_id": "op-event-receipt", "job_id": "job-1"},
            idempotency_key="rename.enqueue:job-1",
            deadline=1,
        )

        self.assertEqual(first, duplicate)
        handoffs = self.coordinator.get_handoffs("op-event-receipt")
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0].state, "submitted")
        self.assertEqual(handoffs[0].event_id, first["event_id"])

    async def test_event_publish_binds_the_captured_handoff_after_target_accepts(self):
        from telepiplex_plugin_sdk import HostClient

        report = {
            "operation_id": "op-event-race",
            "chat_id": 20,
            "user_id": 2,
            "state": "running",
            "stage": "downloading",
            "status_text": "下载中",
            "control": "cancel",
            "revision": 1,
        }
        self.coordinator.report("download", report)
        self.coordinator.report("download", {
            **report,
            "state": "handed_off",
            "stage": "handoff_rename",
            "next_plugin_id": "rename",
            "revision": 2,
        })
        self.broker.register(
            "download",
            "race-token",
            manifest("download", publishes=("download.completed",)),
        )
        publish = self.journal.publish

        def publish_then_accept(
            event_type,
            payload,
            idempotency_key,
            *,
            handoff_binding=None,
        ):
            event_id = publish(
                event_type,
                payload,
                idempotency_key,
                handoff_binding=handoff_binding,
            )
            self.coordinator.report(
                "rename",
                {
                    **report,
                    "state": "running",
                    "stage": "rename",
                    "revision": 3,
                },
            )
            return event_id

        self.journal.publish = publish_then_accept

        result = await HostClient(
            self.broker.socket_path,
            "race-token",
        ).publish_event(
            "download.completed",
            {"operation_id": "op-event-race", "job_id": "job-race"},
            idempotency_key="rename.enqueue:job-race",
            deadline=1,
        )

        receipt = self.coordinator.get_handoffs("op-event-race")[0]
        self.assertEqual(receipt.state, "accepted")
        self.assertEqual(receipt.event_id, result["event_id"])

    async def test_event_publish_retry_after_restart_recovers_durable_handoff_binding(self):
        from app.runtime.event_journal import EventJournal
        from app.runtime.runtime_broker import RuntimeBroker
        from telepiplex_plugin_sdk import FeatureError, HostClient

        report = {
            "operation_id": "op-event-restart",
            "chat_id": 25,
            "user_id": 5,
            "state": "running",
            "stage": "downloading",
            "status_text": "下载中",
            "control": "cancel",
            "revision": 1,
        }
        self.coordinator.report("download", report)
        self.coordinator.report("download", {
            **report,
            "state": "handed_off",
            "stage": "handoff_rename",
            "next_plugin_id": "rename",
            "revision": 2,
        })
        self.journal.set_subscriptions("rename", ["download.completed"])
        publisher = manifest("download", publishes=("download.completed",))
        self.broker.register("download", "restart-token", publisher)
        client = HostClient(self.broker.socket_path, "restart-token")
        bind = self.coordinator.record_handoff_event

        def stop_after_journal_commit(*args, **kwargs):
            raise RuntimeError("injected stop after journal commit")

        self.coordinator.record_handoff_event = stop_after_journal_commit
        with self.assertRaises(FeatureError) as raised:
            await client.publish_event(
                "download.completed",
                {"operation_id": "op-event-restart", "job_id": "job-restart"},
                idempotency_key="rename.enqueue:job-restart",
                deadline=1,
            )
        self.assertEqual(raised.exception.code, "internal_error")
        event_id = self.journal.pending("rename")[0].event_id
        database = self.journal.database_path
        socket_path = self.broker.socket_path

        await self.broker.close()
        self.journal.close()
        self.coordinator.record_handoff_event = bind
        self.coordinator.report("rename", {
            **report,
            "state": "running",
            "stage": "rename",
            "revision": 3,
        })
        self.assertIsNone(
            self.coordinator.capture_handoff("op-event-restart", "download")
        )

        self.journal = EventJournal(database)
        durable = self.journal.handoff_binding(event_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.handoff_key, "op-event-restart:2:rename")
        self.broker = RuntimeBroker(
            self.router,
            self.journal,
            socket_path,
            operation_coordinator=self.coordinator,
        )
        self.broker.register("download", "restart-token", publisher)
        await self.broker.start()

        retried = await HostClient(
            socket_path,
            "restart-token",
        ).publish_event(
            "download.completed",
            {"operation_id": "op-event-restart", "job_id": "job-restart"},
            idempotency_key="rename.enqueue:job-restart",
            deadline=1,
        )

        self.assertEqual(retried["event_id"], event_id)
        receipt = self.coordinator.get_handoffs("op-event-restart")[0]
        self.assertEqual(receipt.state, "accepted")
        self.assertEqual(receipt.event_id, event_id)

    async def test_event_idempotency_collision_cannot_rebind_another_operation(self):
        from telepiplex_plugin_sdk import FeatureError, HostClient

        self.journal.set_subscriptions("rename", ["download.completed"])
        self.broker.register(
            "download",
            "collision-token",
            manifest("download", publishes=("download.completed",)),
        )
        client = HostClient(self.broker.socket_path, "collision-token")

        def handoff(operation_id, chat_id, user_id):
            report = {
                "operation_id": operation_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            self.coordinator.report("download", report)
            self.coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })

        handoff("op-collision-1", 31, 3)
        first = await client.publish_event(
            "download.completed",
            {"operation_id": "op-collision-1"},
            idempotency_key="shared-rename-enqueue",
            deadline=1,
        )
        handoff("op-collision-2", 41, 4)

        with self.assertRaises(FeatureError) as raised:
            await client.publish_event(
                "download.completed",
                {"operation_id": "op-collision-2"},
                idempotency_key="shared-rename-enqueue",
                deadline=1,
            )

        self.assertEqual(raised.exception.code, "handoff_event_conflict")
        first_receipt = self.coordinator.get_handoffs("op-collision-1")[0]
        second_receipt = self.coordinator.get_handoffs("op-collision-2")[0]
        self.assertEqual(first_receipt.event_id, first["event_id"])
        self.assertEqual(first_receipt.state, "submitted")
        self.assertEqual(second_receipt.event_id, "")
        self.assertEqual(second_receipt.state, "prepared")
        self.assertEqual(len(self.journal.pending("rename")), 1)

    async def test_runtime_broker_keeps_the_legacy_constructor_compatible(self):
        from app.runtime.runtime_broker import RuntimeBroker

        broker = RuntimeBroker(
            self.router,
            self.journal,
            Path(self.temp.name) / "legacy-host.sock",
        )

        self.assertIsNone(broker.operation_coordinator)

    async def test_authenticated_feature_can_send_bounded_user_notification(self):
        from telepiplex_plugin_sdk import HostClient, FeatureError

        self.broker.register("download", "notify-token", manifest("download"))
        client = HostClient(self.broker.socket_path, "notify-token")
        result = await client.notify_user(123, "下载完成", deadline=1)
        self.assertTrue(result["accepted"])
        self.assertEqual(self.notifications, [(123, "下载完成")])

        with self.assertRaises(FeatureError) as raised:
            await client.notify_user(123, "x" * 5000, deadline=1)
        self.assertEqual(raised.exception.code, "invalid_notification")

    async def test_feature_publishes_operation_owned_photo_milestone(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("search", "milestone-token", manifest("search"))
        client = HostClient(self.broker.socket_path, "milestone-token")

        result = await client.publish_operation_milestone(
            "op-1",
            "media-douban-35981510",
            "繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
            photo_url="https://img.example/blossoms.jpg",
            deadline=1,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(
            self.milestones,
            [(
                "search",
                {
                    "operation_id": "op-1",
                    "milestone_id": "media-douban-35981510",
                    "mode": "identity",
                    "text": "繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
                    "photo_url": "https://img.example/blossoms.jpg",
                },
            )],
        )

    async def test_feature_can_seal_operation_stage_with_semantic_mode(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("download", "seal-token", manifest("download"))
        client = HostClient(self.broker.socket_path, "seal-token")

        result = await client.seal_operation_stage(
            "op-1",
            "download-completed",
            "✅ 115 下载完成",
            deadline=1,
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(self.milestones, [(
            "download",
            {
                "operation_id": "op-1",
                "milestone_id": "download-completed",
                "mode": "stage",
                "text": "✅ 115 下载完成",
                "photo_url": "",
            },
        )])

    async def test_operation_milestone_preserves_coordinator_error_code(self):
        from app.runtime.interaction_coordinator import InteractionError
        from telepiplex_plugin_sdk import FeatureError, HostClient

        def reject_milestone(_plugin_id, _payload):
            raise InteractionError(
                "owner_mismatch",
                "operation belongs to another Feature",
            )

        self.broker.milestone_sink = reject_milestone
        self.broker.register("search", "milestone-error-token", manifest("search"))

        with self.assertRaises(FeatureError) as raised:
            await HostClient(
                self.broker.socket_path,
                "milestone-error-token",
            ).publish_operation_milestone(
                "op-1",
                "media-owner-check",
                "媒体身份",
                deadline=1,
            )

        self.assertEqual(raised.exception.code, "owner_mismatch")
        self.assertEqual(
            raised.exception.message,
            "operation belongs to another Feature",
        )

    async def test_internal_rpc_error_logs_safe_correlation_without_payload(
        self,
    ):
        from telepiplex_plugin_sdk import FeatureError, HostClient

        logger = Mock()

        async def fail_milestone(_plugin_id, _payload):
            raise RuntimeError(
                "access_token=must-not-leak https://secret.example/path"
            )

        self.broker.logger = logger
        self.broker.milestone_sink = fail_milestone
        self.broker.register(
            "search",
            "internal-error-token",
            manifest("search"),
        )

        with self.assertRaises(FeatureError) as raised:
            await HostClient(
                self.broker.socket_path,
                "internal-error-token",
            ).publish_operation_milestone(
                "op-internal-log",
                "media-internal-log",
                "sensitive title must-not-leak",
                deadline=1,
            )

        self.assertEqual(raised.exception.code, "internal_error")
        message = logger.error.call_args.args[0]
        self.assertIn("event=runtime_broker.internal_error", message)
        self.assertIn("plugin_id=search", message)
        self.assertIn("method=operation.milestone", message)
        self.assertIn("error_type=RuntimeError", message)
        self.assertIn("request_id=", message)
        self.assertNotIn("must-not-leak", message)
        self.assertNotIn("secret.example", message)
        self.assertEqual(
            logger.error.call_args.kwargs["event_name"],
            "runtime_broker.internal_error",
        )
        fields = logger.error.call_args.kwargs["diagnostic_fields"]
        self.assertEqual(fields["status"], "failed")
        self.assertEqual(fields["transport"]["plugin_id"], "search")
        self.assertEqual(fields["transport"]["method"], "operation.milestone")
        self.assertGreaterEqual(fields["duration_ms"], 0)
        self.assertIsInstance(logger.error.call_args.kwargs["exc_info"][1], RuntimeError)

    async def test_operation_report_uses_authenticated_feature_identity(self):
        from telepiplex_plugin_sdk import HostClient

        self.broker.register("echo", "echo-token", manifest("echo"))
        result = await HostClient(
            self.broker.socket_path, "echo-token"
        ).report_operation({
            "operation_id": "op-1",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "planning",
            "status_text": "规划中",
            "control": "cancel",
            "revision": 1,
        })

        self.assertTrue(result["accepted"])
        self.assertEqual(self.operation_sink.await_args.args[0], "echo")
        self.assertEqual(self.operation_sink.await_args.args[1]["operation_id"], "op-1")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
import sqlite3
from pathlib import Path

from tests.test_runtime_broker import manifest


class SubscriberClient:
    def __init__(self):
        self.calls = []
        self.fail = True

    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        if self.fail:
            raise RuntimeError("temporary failure")
        return {"accepted": True}


class PoisonAwareClient(SubscriberClient):
    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        if params.get("payload", {}).get("poison"):
            from app.runtime.plugin_contract import ContractError
            raise ContractError("invalid_request", "permanent failure")
        return {"accepted": True}


class InternalErrorThenSuccessClient(SubscriberClient):
    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        if len(self.calls) <= 2:
            from app.runtime.plugin_contract import ContractError
            raise ContractError("internal_error", "temporary failure")
        return {"accepted": True}


class EventDispatcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_only_successful_delivery_and_retries_pending(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "host.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter()
            client = SubscriberClient()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            event_id = journal.publish(
                "download.completed",
                {"path": "/downloads/show"},
                "download-1",
            )
            dispatcher = EventDispatcher(router, journal, retry_interval=0.01)

            delivered = await dispatcher.deliver_once()
            self.assertEqual(delivered, 0)
            self.assertEqual(len(journal.pending("rename")), 1)

            client.fail = False
            delivered = await dispatcher.deliver_once()
            self.assertEqual(delivered, 1)
            self.assertEqual(journal.pending("rename"), [])
            method, params, _deadline, key = client.calls[-1]
            self.assertEqual(method, "event.deliver")
            self.assertEqual(params["event_id"], event_id)
            self.assertEqual(key, event_id)

    async def test_poison_event_is_dead_lettered_without_blocking_later_events(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "host.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter()
            client = PoisonAwareClient()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            journal.publish("download.completed", {"poison": True}, "bad")
            journal.publish("download.completed", {"poison": False}, "good")
            dispatcher = EventDispatcher(router, journal, max_attempts=1)

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("rename"), [])
            self.assertEqual(len(journal.dead_letters("rename")), 1)

    async def test_poison_handoff_event_marks_the_operation_for_manual_check(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, PoisonAwareClient())
            journal.set_subscriptions("rename", subscriber.subscribes)
            report = {
                "operation_id": "op-poison-handoff",
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", {
                **report,
                "details": {
                    "effect_receipt": {
                        "effect_key": "download.submit:job-poison",
                        "state": "completed",
                        "receipt": {
                            "job_id": "job-poison",
                            "provider_id": "provider-poison",
                        },
                    }
                },
            })
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            event_id = journal.publish(
                "download.completed",
                {"operation_id": "op-poison-handoff", "poison": True},
                "poison-handoff",
            )
            coordinator.record_handoff_event(
                "op-poison-handoff", event_id, "rename"
            )
            dispatcher = EventDispatcher(
                router,
                journal,
                max_attempts=1,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 0)

            self.assertEqual(len(journal.dead_letters("rename")), 1)
            record = coordinator.get("op-poison-handoff")
            self.assertEqual(record.state, "failed")
            self.assertTrue(record.details["manual_check_required"])
            self.assertEqual(record.details["handoff_event_id"], event_id)
            self.assertEqual(record.details["handoff_target_plugin_id"], "rename")
            self.assertEqual(record.details["handoff_error_code"], "invalid_request")
            handoff = coordinator.get_handoffs("op-poison-handoff")[0]
            self.assertEqual(handoff.state, "failed")
            self.assertEqual(handoff.target_plugin_id, "rename")
            self.assertEqual(handoff.error_code, "invalid_request")
            effects = coordinator.get_effect_receipts("op-poison-handoff")
            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0].effect_key, "download.submit:job-poison")
            self.assertEqual(effects[0].state, "completed")
            self.assertEqual(
                dict(effects[0].receipt),
                {"job_id": "job-poison", "provider_id": "provider-poison"},
            )
            self.assertEqual(journal.unprojected_dead_letters(), [])

    async def test_durable_handoff_binding_must_apply_before_feature_delivery(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            client = SubscriberClient()
            client.fail = False
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            report = {
                "operation_id": "op-bind-before-delivery",
                "chat_id": 11,
                "user_id": 11,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            receipt = coordinator.capture_handoff(
                "op-bind-before-delivery", "download"
            )
            event_id = journal.publish(
                "download.completed",
                {"operation_id": "op-bind-before-delivery"},
                "bind-before-delivery",
                handoff_binding={
                    "operation_id": receipt.operation_id,
                    "handoff_key": receipt.handoff_key,
                    "source_plugin_id": receipt.source_plugin_id,
                    "source_revision": receipt.source_revision,
                    "target_plugin_id": receipt.target_plugin_id,
                },
            )
            bind = coordinator.record_handoff_event

            def unavailable_binding_store(*args, **kwargs):
                raise RuntimeError("injected binding failure")

            coordinator.record_handoff_event = unavailable_binding_store
            dispatcher = EventDispatcher(
                router,
                journal,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 0)
            self.assertEqual(client.calls, [])
            self.assertEqual(len(journal.pending("rename")), 1)

            coordinator.record_handoff_event = bind
            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(journal.pending("rename"), [])
            stored = coordinator.get_handoffs("op-bind-before-delivery")[0]
            self.assertEqual(stored.state, "submitted")
            self.assertEqual(stored.event_id, event_id)

    async def test_dead_letter_before_binding_stays_pending_until_binding_reconciles(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            report = {
                "operation_id": "op-dead-before-bind",
                "chat_id": 12,
                "user_id": 12,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            receipt = coordinator.capture_handoff("op-dead-before-bind", "download")
            event_id = journal.publish(
                "download.completed",
                {"operation_id": "op-dead-before-bind", "poison": True},
                "dead-before-bind",
                handoff_binding={
                    "operation_id": receipt.operation_id,
                    "handoff_key": receipt.handoff_key,
                    "source_plugin_id": receipt.source_plugin_id,
                    "source_revision": receipt.source_revision,
                    "target_plugin_id": receipt.target_plugin_id,
                },
            )
            self.assertTrue(
                journal.record_failure(
                    event_id,
                    "rename",
                    "invalid_request",
                    max_attempts=1,
                )
            )
            bind = coordinator.record_handoff_event

            def binding_not_ready(*args, **kwargs):
                raise RuntimeError("injected binding failure")

            coordinator.record_handoff_event = binding_not_ready
            dispatcher = EventDispatcher(
                router,
                journal,
                operation_coordinator=coordinator,
            )
            project = dispatcher._project_dead_letter
            projection_results = []

            def capture_projection_result(*args, **kwargs):
                result = project(*args, **kwargs)
                projection_results.append(result)
                return result

            dispatcher._project_dead_letter = capture_projection_result

            self.assertEqual(await dispatcher.deliver_once(), 0)
            self.assertEqual(projection_results, ["binding_pending"])
            self.assertEqual(coordinator.get("op-dead-before-bind").state, "handed_off")
            self.assertEqual(len(journal.unprojected_dead_letters()), 1)
            self.assertEqual(
                coordinator.get_handoffs("op-dead-before-bind")[0].event_id,
                "",
            )

            coordinator.record_handoff_event = bind
            self.assertEqual(await dispatcher.deliver_once(), 0)

            self.assertEqual(projection_results[-1], "applied")
            self.assertEqual(journal.unprojected_dead_letters(), [])
            operation = coordinator.get("op-dead-before-bind")
            self.assertEqual(operation.state, "failed")
            self.assertTrue(operation.details["manual_check_required"])
            handoff = coordinator.get_handoffs("op-dead-before-bind")[0]
            self.assertEqual(handoff.event_id, event_id)
            self.assertEqual(handoff.state, "failed")
            self.assertEqual(handoff.error_code, "invalid_request")

    async def test_legacy_unbound_event_binds_before_delivery_and_poison_fails_operation(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator
        from app.runtime.plugin_contract import ContractError

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    plugin_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status_text TEXT NOT NULL,
                    control TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    message_id INTEGER,
                    message_kind TEXT NOT NULL DEFAULT 'text',
                    next_plugin_id TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE operation_milestones (
                    operation_id TEXT NOT NULL,
                    milestone_id TEXT NOT NULL,
                    plugin_id TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    delivery_started INTEGER NOT NULL DEFAULT 0,
                    delivered_message_id INTEGER,
                    delivered_message_kind TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(operation_id, milestone_id)
                );
                CREATE TABLE event_subscriptions (
                    plugin_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    PRIMARY KEY(plugin_id, event_type)
                );
                CREATE TABLE events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(event_type, idempotency_key)
                );
                CREATE TABLE event_deliveries (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    plugin_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(event_id, plugin_id)
                );
                CREATE TABLE event_delivery_failures (
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    plugin_id TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    dead_lettered_at REAL,
                    PRIMARY KEY(event_id, plugin_id)
                );
            """)
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "op-legacy-business",
                    21,
                    21,
                    "download",
                    "handed_off",
                    "handoff_rename",
                    "等待整理",
                    "cancel",
                    2,
                    None,
                    "text",
                    "rename",
                    '{}',
                    10.0,
                    11.0,
                ),
            )
            connection.execute(
                "INSERT INTO event_subscriptions VALUES (?, ?)",
                ("rename", "download.completed"),
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy-business-event",
                    "download.completed",
                    '{"operation_id":"op-legacy-business","poison":true}',
                    "legacy-business-key",
                    12.0,
                ),
            )
            connection.execute(
                "INSERT INTO event_deliveries VALUES (?, ?, ?, ?)",
                ("legacy-business-event", "rename", "pending", 12.0),
            )
            connection.commit()
            connection.close()

            migrated_journal = EventJournal(database)
            migrated_coordinator = InteractionCoordinator(database)
            self.assertIsNone(
                migrated_journal.handoff_binding("legacy-business-event")
            )
            self.assertEqual(
                migrated_coordinator.get_handoffs("op-legacy-business"),
                [],
            )
            migrated_coordinator.close()
            migrated_journal.close()

            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)

            class ObserveBindingThenPoison:
                def __init__(self):
                    self.calls = 0
                    self.binding_at_delivery = None
                    self.receipt_at_delivery = None

                async def request(
                    self,
                    method,
                    params,
                    *,
                    deadline,
                    idempotency_key="",
                ):
                    self.calls += 1
                    self.binding_at_delivery = journal.handoff_binding(
                        params["event_id"]
                    )
                    receipts = coordinator.get_handoffs(
                        params["payload"]["operation_id"]
                    )
                    self.receipt_at_delivery = receipts[0] if receipts else None
                    raise ContractError("invalid_request", "permanent failure")

            router = CapabilityRouter()
            client = ObserveBindingThenPoison()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            dispatcher = EventDispatcher(
                router,
                journal,
                max_attempts=1,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 0)

            self.assertEqual(client.calls, 1)
            self.assertIsNotNone(client.binding_at_delivery)
            self.assertEqual(
                client.binding_at_delivery.handoff_key,
                "op-legacy-business:2:rename",
            )
            self.assertIsNotNone(client.receipt_at_delivery)
            self.assertEqual(client.receipt_at_delivery.state, "submitted")
            self.assertEqual(
                client.receipt_at_delivery.event_id,
                "legacy-business-event",
            )
            record = coordinator.get("op-legacy-business")
            self.assertEqual(record.state, "failed")
            self.assertTrue(record.details["manual_check_required"])
            self.assertEqual(
                record.details["handoff_event_id"],
                "legacy-business-event",
            )
            receipt = coordinator.get_handoffs("op-legacy-business")[0]
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(receipt.error_code, "invalid_request")
            self.assertEqual(journal.unprojected_dead_letters(), [])
            self.assertIsNotNone(
                journal.dead_letters("rename")[0]["projected_at"]
            )

    async def test_non_target_fanout_dead_letter_is_not_applicable_and_target_still_fails(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            report = {
                "operation_id": "op-fanout-dead-letter",
                "chat_id": 22,
                "user_id": 22,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            receipt = coordinator.capture_handoff(
                "op-fanout-dead-letter", "download"
            )
            journal.set_subscriptions(
                "rename", ["download.completed"]
            )
            journal.set_subscriptions(
                "audit", ["download.completed"]
            )
            event_id = journal.publish(
                "download.completed",
                {"operation_id": "op-fanout-dead-letter"},
                "fanout-dead-letter",
                handoff_binding={
                    "operation_id": receipt.operation_id,
                    "handoff_key": receipt.handoff_key,
                    "source_plugin_id": receipt.source_plugin_id,
                    "source_revision": receipt.source_revision,
                    "target_plugin_id": receipt.target_plugin_id,
                },
            )
            dispatcher = EventDispatcher(
                CapabilityRouter(),
                journal,
                max_attempts=1,
                operation_coordinator=coordinator,
            )
            self.assertTrue(
                journal.record_failure(
                    event_id,
                    "audit",
                    "invalid_request",
                    max_attempts=1,
                )
            )

            self.assertEqual(
                dispatcher._project_dead_letter(
                    event_id,
                    "audit",
                    "invalid_request",
                ),
                "not_applicable",
            )
            self.assertEqual(journal.unprojected_dead_letters(), [])
            self.assertEqual(
                coordinator.get("op-fanout-dead-letter").state,
                "handed_off",
            )
            submitted = coordinator.get_handoffs("op-fanout-dead-letter")[0]
            self.assertEqual(submitted.state, "submitted")
            self.assertEqual(submitted.event_id, event_id)

            self.assertTrue(
                journal.record_failure(
                    event_id,
                    "rename",
                    "invalid_request",
                    max_attempts=1,
                )
            )
            self.assertEqual(
                dispatcher._project_dead_letter(
                    event_id,
                    "rename",
                    "invalid_request",
                ),
                "applied",
            )
            self.assertEqual(
                coordinator.get("op-fanout-dead-letter").state,
                "failed",
            )

    async def test_dead_letter_projection_retries_without_blocking_later_events(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            client = PoisonAwareClient()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            report = {
                "operation_id": "op-projection-retry",
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            poison_id = journal.publish(
                "download.completed",
                {"operation_id": "op-projection-retry", "poison": True},
                "projection-retry-poison",
            )
            receipt = coordinator.capture_handoff(
                "op-projection-retry", "download"
            )
            coordinator.record_handoff_event(
                "op-projection-retry",
                poison_id,
                "rename",
                handoff_key=receipt.handoff_key,
            )
            journal.publish(
                "download.completed",
                {"poison": False, "sequence": "later"},
                "projection-retry-later",
            )
            project = coordinator.fail_handoff_delivery
            attempts = 0

            def fail_once(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("injected projection failure")
                return project(*args, **kwargs)

            coordinator.fail_handoff_delivery = fail_once
            dispatcher = EventDispatcher(
                router,
                journal,
                max_attempts=1,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(journal.pending("rename"), [])
            self.assertEqual(
                coordinator.get("op-projection-retry").state,
                "handed_off",
            )
            self.assertEqual(len(journal.unprojected_dead_letters()), 1)

            self.assertEqual(await dispatcher.deliver_once(), 0)

            self.assertEqual(attempts, 2)
            self.assertEqual(journal.unprojected_dead_letters(), [])
            self.assertEqual(
                coordinator.get("op-projection-retry").state,
                "failed",
            )

    async def test_transport_failure_never_consumes_poison_attempt_budget(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "host.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter(); client = SubscriberClient()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            journal.publish("download.completed", {"path": "/download"}, "transient")
            dispatcher = EventDispatcher(router, journal, max_attempts=1)

            await dispatcher.deliver_once()

            self.assertEqual(len(journal.pending("rename")), 1)
            self.assertEqual(journal.dead_letters("rename"), [])

    async def test_transport_failure_keeps_the_handoff_submitted_and_pending(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, SubscriberClient())
            journal.set_subscriptions("rename", subscriber.subscribes)
            report = {
                "operation_id": "op-transient-handoff",
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            event_id = journal.publish(
                "download.completed",
                {"operation_id": "op-transient-handoff"},
                "transient-handoff",
            )
            coordinator.record_handoff_event(
                "op-transient-handoff", event_id, "rename"
            )
            dispatcher = EventDispatcher(
                router,
                journal,
                max_attempts=1,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 0)

            self.assertEqual(len(journal.pending("rename")), 1)
            self.assertEqual(coordinator.get("op-transient-handoff").state, "handed_off")
            self.assertEqual(
                coordinator.get_handoffs("op-transient-handoff")[0].state,
                "submitted",
            )

    async def test_internal_error_does_not_consume_poison_attempt_budget(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "host.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter(); client = InternalErrorThenSuccessClient()
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            journal.publish("download.completed", {"path": "/download"}, "transient")
            dispatcher = EventDispatcher(router, journal, max_attempts=2)

            await dispatcher.deliver_once()
            await dispatcher.deliver_once()

            self.assertEqual(len(journal.pending("rename")), 1)
            self.assertEqual(journal.dead_letters("rename"), [])

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("rename"), [])

    async def test_terminal_operation_acks_pending_handoff_without_delivery(self):
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_dispatcher import EventDispatcher
        from app.runtime.event_journal import EventJournal
        from app.runtime.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "host.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            client = SubscriberClient()
            client.fail = False
            subscriber = manifest("rename", subscribes=("download.completed",))
            router.activate("rename", subscriber, client)
            journal.set_subscriptions("rename", subscriber.subscribes)
            report = {
                "operation_id": "op-cancelled-handoff",
                "chat_id": 10,
                "user_id": 1,
                "state": "running",
                "stage": "downloading",
                "status_text": "下载中",
                "control": "cancel",
                "revision": 1,
            }
            coordinator.report("download", report)
            coordinator.report("download", {
                **report,
                "state": "handed_off",
                "stage": "handoff_rename",
                "next_plugin_id": "rename",
                "revision": 2,
            })
            journal.publish(
                "download.completed",
                {"operation_id": "op-cancelled-handoff"},
                "cancelled-handoff",
            )
            coordinator.report("download", {
                **report,
                "state": "cancelled",
                "stage": "handoff_rename",
                "control": "",
                "revision": 3,
            })
            dispatcher = EventDispatcher(
                router,
                journal,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("rename"), [])
            self.assertEqual(client.calls, [])
            self.assertEqual(journal.dead_letters("rename"), [])


if __name__ == "__main__":
    unittest.main()

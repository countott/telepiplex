import tempfile
import unittest
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

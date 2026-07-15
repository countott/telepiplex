import tempfile
import unittest
from pathlib import Path

from tests.test_core_broker import manifest


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
            from app.core.plugin_contract import ContractError
            raise ContractError("invalid_request", "permanent failure")
        return {"accepted": True}


class InternalErrorThenSuccessClient(SubscriberClient):
    async def request(self, method, params, *, deadline, idempotency_key=""):
        self.calls.append((method, params, deadline, idempotency_key))
        if len(self.calls) <= 2:
            from app.core.plugin_contract import ContractError
            raise ContractError("internal_error", "temporary failure")
        return {"accepted": True}


class EventDispatcherTest(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledges_only_successful_delivery_and_retries_pending(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "core.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter()
            client = SubscriberClient()
            subscriber = manifest("renaming", subscribes=("download.completed",))
            router.activate("renaming", subscriber, client)
            journal.set_subscriptions("renaming", subscriber.subscribes)
            event_id = journal.publish(
                "download.completed",
                {"path": "/downloads/show"},
                "download-1",
            )
            dispatcher = EventDispatcher(router, journal, retry_interval=0.01)

            delivered = await dispatcher.deliver_once()
            self.assertEqual(delivered, 0)
            self.assertEqual(len(journal.pending("renaming")), 1)

            client.fail = False
            delivered = await dispatcher.deliver_once()
            self.assertEqual(delivered, 1)
            self.assertEqual(journal.pending("renaming"), [])
            method, params, _deadline, key = client.calls[-1]
            self.assertEqual(method, "event.deliver")
            self.assertEqual(params["event_id"], event_id)
            self.assertEqual(key, event_id)

    async def test_poison_event_is_dead_lettered_without_blocking_later_events(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "core.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter()
            client = PoisonAwareClient()
            subscriber = manifest("renaming", subscribes=("download.completed",))
            router.activate("renaming", subscriber, client)
            journal.set_subscriptions("renaming", subscriber.subscribes)
            journal.publish("download.completed", {"poison": True}, "bad")
            journal.publish("download.completed", {"poison": False}, "good")
            dispatcher = EventDispatcher(router, journal, max_attempts=1)

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("renaming"), [])
            self.assertEqual(len(journal.dead_letters("renaming")), 1)

    async def test_transport_failure_never_consumes_poison_attempt_budget(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "core.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter(); client = SubscriberClient()
            subscriber = manifest("renaming", subscribes=("download.completed",))
            router.activate("renaming", subscriber, client)
            journal.set_subscriptions("renaming", subscriber.subscribes)
            journal.publish("download.completed", {"path": "/download"}, "transient")
            dispatcher = EventDispatcher(router, journal, max_attempts=1)

            await dispatcher.deliver_once()

            self.assertEqual(len(journal.pending("renaming")), 1)
            self.assertEqual(journal.dead_letters("renaming"), [])

    async def test_internal_error_does_not_consume_poison_attempt_budget(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal

        with tempfile.TemporaryDirectory() as tmpdir:
            journal = EventJournal(Path(tmpdir) / "core.db")
            self.addCleanup(journal.close)
            router = CapabilityRouter(); client = InternalErrorThenSuccessClient()
            subscriber = manifest("renaming", subscribes=("download.completed",))
            router.activate("renaming", subscriber, client)
            journal.set_subscriptions("renaming", subscriber.subscribes)
            journal.publish("download.completed", {"path": "/download"}, "transient")
            dispatcher = EventDispatcher(router, journal, max_attempts=2)

            await dispatcher.deliver_once()
            await dispatcher.deliver_once()

            self.assertEqual(len(journal.pending("renaming")), 1)
            self.assertEqual(journal.dead_letters("renaming"), [])

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("renaming"), [])

    async def test_terminal_operation_acks_pending_handoff_without_delivery(self):
        from app.core.capability_router import CapabilityRouter
        from app.core.event_dispatcher import EventDispatcher
        from app.core.event_journal import EventJournal
        from app.core.interaction_coordinator import InteractionCoordinator

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "core.db"
            journal = EventJournal(database)
            coordinator = InteractionCoordinator(database)
            self.addCleanup(journal.close)
            self.addCleanup(coordinator.close)
            router = CapabilityRouter()
            client = SubscriberClient()
            client.fail = False
            subscriber = manifest("renaming", subscribes=("download.completed",))
            router.activate("renaming", subscriber, client)
            journal.set_subscriptions("renaming", subscriber.subscribes)
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
            coordinator.report("open115", report)
            coordinator.report("open115", {
                **report,
                "state": "handed_off",
                "stage": "handoff_renaming",
                "next_plugin_id": "renaming",
                "revision": 2,
            })
            journal.publish(
                "download.completed",
                {"operation_id": "op-cancelled-handoff"},
                "cancelled-handoff",
            )
            coordinator.report("open115", {
                **report,
                "state": "cancelled",
                "stage": "handoff_renaming",
                "control": "",
                "revision": 3,
            })
            dispatcher = EventDispatcher(
                router,
                journal,
                operation_coordinator=coordinator,
            )

            self.assertEqual(await dispatcher.deliver_once(), 1)
            self.assertEqual(journal.pending("renaming"), [])
            self.assertEqual(client.calls, [])
            self.assertEqual(journal.dead_letters("renaming"), [])


if __name__ == "__main__":
    unittest.main()

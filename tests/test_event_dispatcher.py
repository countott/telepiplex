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
            raise RuntimeError("permanent failure")
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


if __name__ == "__main__":
    unittest.main()

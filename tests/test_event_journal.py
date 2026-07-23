import tempfile
import unittest
from pathlib import Path


class EventJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "host.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_fans_out_and_ack_is_per_subscriber(self):
        from app.runtime.event_journal import EventJournal

        journal = EventJournal(self.database)
        self.addCleanup(journal.close)
        journal.set_subscriptions("rename", ["download.completed"])
        journal.set_subscriptions("audit", ["download.completed"])

        event_id = journal.publish(
            "download.completed",
            {"path": "/downloads/show"},
            "download-1",
        )

        rename = journal.pending("rename")
        audit = journal.pending("audit")
        self.assertEqual(rename[0].event_id, event_id)
        self.assertEqual(rename[0].payload["path"], "/downloads/show")
        self.assertEqual(audit[0].event_id, event_id)
        self.assertTrue(journal.ack(event_id, "rename"))
        self.assertFalse(journal.ack(event_id, "rename"))
        self.assertEqual(journal.pending("rename"), [])
        self.assertEqual(len(journal.pending("audit")), 1)

    def test_duplicate_idempotency_key_returns_existing_event_without_redelivery(self):
        from app.runtime.event_journal import EventJournal

        journal = EventJournal(self.database)
        self.addCleanup(journal.close)
        journal.set_subscriptions("rename", ["download.completed"])

        first = journal.publish("download.completed", {"n": 1}, "same-key")
        second = journal.publish("download.completed", {"n": 2}, "same-key")

        self.assertEqual(first, second)
        pending = journal.pending("rename")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].payload, {"n": 1})

    def test_pending_deliveries_survive_reopen(self):
        from app.runtime.event_journal import EventJournal

        first = EventJournal(self.database)
        first.set_subscriptions("plex", ["media.organized"])
        event_id = first.publish("media.organized", {"path": "/library/show"}, "media-1")
        first.close()

        reopened = EventJournal(self.database)
        self.addCleanup(reopened.close)
        pending = reopened.pending("plex")

        self.assertEqual(pending[0].event_id, event_id)
        self.assertEqual(pending[0].event_type, "media.organized")
        self.assertFalse(reopened.ack("missing", "plex"))


if __name__ == "__main__":
    unittest.main()

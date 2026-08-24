import tempfile
import unittest
import sqlite3
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

    def test_handoff_binding_is_atomic_with_event_and_survives_idempotent_reopen(self):
        from app.runtime.event_journal import EventJournal

        binding = {
            "operation_id": "op-journal-binding",
            "handoff_key": "op-journal-binding:2:rename",
            "source_plugin_id": "download",
            "source_revision": 2,
            "target_plugin_id": "rename",
        }
        first = EventJournal(self.database)
        event_id = first.publish(
            "download.completed",
            {"operation_id": "op-journal-binding"},
            "journal-binding",
            handoff_binding=binding,
        )
        first.close()

        reopened = EventJournal(self.database)
        self.addCleanup(reopened.close)
        duplicate = reopened.publish(
            "download.completed",
            {"operation_id": "op-journal-binding"},
            "journal-binding",
        )
        durable = reopened.handoff_binding(duplicate)

        self.assertEqual(duplicate, event_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.event_id, event_id)
        self.assertEqual(durable.operation_id, binding["operation_id"])
        self.assertEqual(durable.handoff_key, binding["handoff_key"])
        self.assertEqual(durable.source_plugin_id, binding["source_plugin_id"])
        self.assertEqual(durable.source_revision, binding["source_revision"])
        self.assertEqual(durable.target_plugin_id, binding["target_plugin_id"])

    def test_existing_event_handoff_binding_attach_is_idempotent_and_conflict_checked(self):
        from app.runtime.event_journal import EventJournal, EventJournalError

        journal = EventJournal(self.database)
        self.addCleanup(journal.close)
        binding = {
            "operation_id": "op-legacy-attach",
            "handoff_key": "op-legacy-attach:2:rename",
            "source_plugin_id": "download",
            "source_revision": 2,
            "target_plugin_id": "rename",
        }
        event_id = journal.publish(
            "download.completed",
            {"operation_id": "op-legacy-attach"},
            "legacy-attach",
        )

        first = journal.attach_handoff_binding(event_id, binding)
        replay = journal.attach_handoff_binding(event_id, binding)

        self.assertEqual(first, replay)
        self.assertEqual(journal.event_payload(event_id), {
            "operation_id": "op-legacy-attach",
        })
        competing_event = journal.publish(
            "download.completed",
            {"operation_id": "op-legacy-attach"},
            "legacy-attach-competing",
        )
        with self.assertRaises(EventJournalError) as raised:
            journal.attach_handoff_binding(competing_event, binding)
        self.assertEqual(raised.exception.code, "handoff_event_conflict")
        self.assertIsNone(journal.handoff_binding(competing_event))

    def test_unprojected_dead_letter_survives_reopen_until_marked(self):
        from app.runtime.event_journal import EventJournal

        journal = EventJournal(self.database)
        journal.set_subscriptions("rename", ["download.completed"])
        event_id = journal.publish(
            "download.completed",
            {"operation_id": "op-dead-letter"},
            "dead-letter-1",
        )
        self.assertTrue(
            journal.record_failure(
                event_id,
                "rename",
                "invalid_request",
                max_attempts=1,
            )
        )
        journal.close()

        reopened = EventJournal(self.database)
        self.addCleanup(reopened.close)
        pending_projection = reopened.unprojected_dead_letters()

        self.assertEqual(len(pending_projection), 1)
        self.assertEqual(pending_projection[0]["event_id"], event_id)
        self.assertEqual(pending_projection[0]["plugin_id"], "rename")
        self.assertEqual(pending_projection[0]["last_error"], "invalid_request")
        self.assertTrue(reopened.mark_dead_letter_projected(event_id, "rename"))
        self.assertFalse(reopened.mark_dead_letter_projected(event_id, "rename"))
        self.assertEqual(reopened.unprojected_dead_letters(), [])
        self.assertIsNotNone(reopened.dead_letters("rename")[0]["projected_at"])

    def test_legacy_dead_letter_table_gains_a_pending_projection_marker(self):
        from app.runtime.event_journal import EventJournal

        connection = sqlite3.connect(self.database)
        connection.executescript("""
            CREATE TABLE events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(event_type, idempotency_key)
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
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-event",
                "download.completed",
                '{"operation_id":"op-legacy"}',
                "legacy-key",
                10.0,
            ),
        )
        connection.execute(
            "INSERT INTO event_delivery_failures VALUES (?, ?, ?, ?, ?)",
            ("legacy-event", "rename", 5, "invalid_request", 11.0),
        )
        connection.commit()
        connection.close()

        migrated = EventJournal(self.database)
        self.addCleanup(migrated.close)

        pending = migrated.unprojected_dead_letters()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["event_id"], "legacy-event")
        self.assertEqual(pending[0]["plugin_id"], "rename")
        self.assertEqual(pending[0]["last_error"], "invalid_request")
        self.assertIsNone(migrated.handoff_binding("legacy-event"))


if __name__ == "__main__":
    unittest.main()

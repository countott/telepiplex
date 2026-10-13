"""Dead-letter polling must stay bounded as already handled history grows."""
import sqlite3

import pytest

from app.runtime.event_journal import EventJournal


def insert_failures(journal, rows):
    connection = journal._connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executemany(
            "INSERT INTO events VALUES (?, 'download.completed', '{}', ?, ?)",
            ((event_id, event_id, 1) for event_id, _, _ in rows),
        )
        connection.executemany(
            "INSERT INTO event_delivery_failures VALUES (?, 'rename', 5, 'invalid_request', ?, ?)",
            ((event_id, dead_at, projected_at) for event_id, dead_at, projected_at in rows),
        )
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def counted_poll(journal):
    steps = 0
    statements = []

    def step():
        nonlocal steps
        steps += 1
        return 0

    connection = journal._connection
    connection.set_progress_handler(step, 1)
    connection.set_trace_callback(statements.append)
    try:
        result = journal.unprojected_dead_letters()
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_trace_callback(None)
    return steps, result, statements[-1]


@pytest.mark.parametrize("history", [1_000, 10_000, 100_000])
def test_empty_dead_letter_poll_does_not_scan_handled_history(tmp_path, history):
    journal = EventJournal(tmp_path / "journal.db")
    try:
        before, expected, _ = counted_poll(journal)
        assert expected == []
        insert_failures(journal, [(f"history-{i}", i, i) for i in range(history)])
        after, result, query = counted_poll(journal)
        assert result == expected
        assert after <= before + 128, (history, before, after)
        plan = " ".join(str(row[3]) for row in journal._connection.execute(
            "EXPLAIN QUERY PLAN " + query
        ))
        assert "SCAN event_delivery_failures" not in plan
        assert "USE TEMP B-TREE" not in plan
    finally:
        journal.close()


def test_pending_order_filter_and_idempotent_reopen_with_historical_rows(tmp_path):
    path = tmp_path / "journal.db"
    journal = EventJournal(path)
    try:
        insert_failures(journal, [(f"history-{i}", i, i) for i in range(10_000)])
        insert_failures(journal, [
            ("later", 20, None),
            ("tie-b", 10, None),
            ("tie-a", 10, None),
            ("not-dead", None, None),
            ("projected-zero", 1, 0),
        ])
        expected = ["tie-a", "tie-b", "later"]
        assert [row["event_id"] for row in journal.unprojected_dead_letters()] == expected
        assert [row["event_id"] for row in journal.unprojected_dead_letters(2)] == expected[:2]
        journal._create_schema()
        assert [row["event_id"] for row in journal.unprojected_dead_letters()] == expected
    finally:
        journal.close()
    reopened = EventJournal(path)
    try:
        assert [row["event_id"] for row in reopened.unprojected_dead_letters()] == expected
        assert reopened.mark_dead_letter_projected("tie-a", "rename") is True
        assert [row["event_id"] for row in reopened.unprojected_dead_letters()] == expected[1:]
    finally:
        reopened.close()


def test_index_can_be_added_after_legacy_projected_at_migration(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE events (
            id TEXT PRIMARY KEY, event_type TEXT, payload_json TEXT,
            idempotency_key TEXT, created_at REAL,
            UNIQUE(event_type, idempotency_key)
        );
        CREATE TABLE event_delivery_failures (
            event_id TEXT, plugin_id TEXT, attempt_count INTEGER,
            last_error TEXT, dead_lettered_at REAL,
            PRIMARY KEY(event_id, plugin_id)
        );
        INSERT INTO events VALUES ('legacy', 'download.completed', '{}', 'legacy', 1);
        INSERT INTO event_delivery_failures VALUES ('legacy', 'rename', 5, 'invalid_request', 1);
    """)
    connection.close()
    journal = EventJournal(path)
    try:
        assert journal.unprojected_dead_letters()[0]["event_id"] == "legacy"
        _, _, query = counted_poll(journal)
        plan = " ".join(str(row[3]) for row in journal._connection.execute(
            "EXPLAIN QUERY PLAN " + query
        ))
        assert "event_delivery_failures_unprojected" in plan
    finally:
        journal.close()

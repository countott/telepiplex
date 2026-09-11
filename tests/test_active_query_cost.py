import tempfile
import unittest
from pathlib import Path


class ActiveQueryCostTest(unittest.TestCase):
    INSERT_OPERATION = (
        "INSERT INTO operations ("
        "operation_id, chat_id, user_id, plugin_id, state, stage, "
        "status_text, control, revision, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def setUp(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = InteractionCoordinator(
            Path(self.temp.name) / "host.db"
        )

    def tearDown(self):
        self.coordinator.close()
        self.temp.cleanup()

    def insert_operations(self, rows):
        connection = self.coordinator._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executemany(self.INSERT_OPERATION, rows)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def vm_steps(self, call):
        steps = 0

        def count_step():
            nonlocal steps
            steps += 1
            return 0

        connection = self.coordinator._connection
        connection.set_progress_handler(count_step, 1)
        try:
            result = call()
        finally:
            connection.set_progress_handler(None, 0)
        return steps, result

    @staticmethod
    def operation(
        operation_id,
        chat_id,
        user_id,
        state,
        created_at,
        updated_at,
    ):
        return (
            operation_id,
            chat_id,
            user_id,
            "search",
            state,
            "search",
            state,
            "cancel" if state not in {
                "completed",
                "cancelled",
                "rolled_back",
                "partially_rolled_back",
                "failed",
                "interrupted",
            } else "",
            1,
            created_at,
            updated_at,
        )

    def terminal_history(self, count):
        from app.runtime.interaction_coordinator import TERMINAL_STATES

        states = tuple(sorted(TERMINAL_STATES))
        return [
            self.operation(
                f"history-{index:05d}",
                10 + index % 7,
                1 + index % 5,
                states[index % len(states)],
                100 + index,
                100 + index,
            )
            for index in range(count)
        ]

    def test_cleanup_protection_lookup_cost_is_independent_of_terminal_history(self):
        self.insert_operations([
            self.operation("current", 10, 1, "running", 1, 1),
        ])
        before_steps, before = self.vm_steps(
            lambda: self.coordinator.active(10, 1)
        )

        self.insert_operations(self.terminal_history(10_000))
        after_steps, after = self.vm_steps(
            lambda: self.coordinator.active(10, 1)
        )

        self.assertEqual(before.operation_id, "current")
        self.assertEqual(after.operation_id, "current")
        self.assertIsNone(self.coordinator.active(999, 999))
        self.assertLessEqual(after_steps, before_steps + 256)

    def test_active_records_cost_and_order_are_independent_of_terminal_history(self):
        from app.runtime.interaction_coordinator import ACTIVE_STATES

        active_rows = [
            self.operation("active-running", 30, 1, "running", 3, 20),
            self.operation("active-awaiting", 30, 2, "awaiting_input", 1, 50),
            self.operation("active-handoff", 30, 3, "handed_off", 2, 40),
            self.operation("active-cancelling", 30, 4, "cancelling", 3, 30),
            self.operation("active-rollback", 30, 5, "rolling_back", 2, 10),
        ]
        self.assertEqual(
            {row[4] for row in active_rows},
            set(ACTIVE_STATES),
        )
        self.insert_operations(active_rows)
        before_steps, before = self.vm_steps(self.coordinator.active_records)

        self.insert_operations(self.terminal_history(10_000))
        after_steps, after = self.vm_steps(self.coordinator.active_records)

        expected_ids = [
            "active-awaiting",
            "active-handoff",
            "active-rollback",
            "active-cancelling",
            "active-running",
        ]
        self.assertEqual([record.operation_id for record in before], expected_ids)
        self.assertEqual([record.operation_id for record in after], expected_ids)
        for index, row in enumerate(active_rows, start=1):
            self.assertEqual(
                self.coordinator.active(30, index).operation_id,
                row[0],
            )
        self.assertLessEqual(after_steps, before_steps + 256)


if __name__ == "__main__":
    unittest.main()

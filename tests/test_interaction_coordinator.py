import tempfile
import unittest
from pathlib import Path


class InteractionCoordinatorTest(unittest.TestCase):
    def setUp(self):
        from app.core.interaction_coordinator import InteractionCoordinator

        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "core.db"
        self.coordinator = InteractionCoordinator(self.database_path)

    def tearDown(self):
        self.coordinator.close()
        self.temp.cleanup()

    @staticmethod
    def report(**overrides):
        report = {
            "operation_id": "op-1",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "planning",
            "status_text": "规划中",
            "control": "cancel",
            "revision": 1,
            "details": {"provider": "demo"},
        }
        report.update(overrides)
        return report

    def test_report_creates_active_record_and_terminal_state_releases_gate(self):
        record = self.coordinator.report("media-search", self.report())

        self.assertEqual(record.plugin_id, "media-search")
        self.assertEqual(record.details, {"provider": "demo"})
        self.assertEqual(self.coordinator.active(10, 1), record)

        terminal = self.coordinator.report(
            "media-search",
            self.report(state="completed", control="", revision=2),
        )
        self.assertEqual(terminal.state, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_only_one_non_terminal_operation_may_own_a_user(self):
        from app.core.interaction_coordinator import InteractionError

        self.coordinator.report("media-search", self.report())

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "open115",
                self.report(operation_id="op-2", revision=1),
            )
        self.assertEqual(raised.exception.code, "operation_conflict")

    def test_owner_change_requires_matching_handoff(self):
        from app.core.interaction_coordinator import InteractionError

        self.coordinator.report("media-search", self.report())
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report("open115", self.report(revision=2))
        self.assertEqual(raised.exception.code, "owner_mismatch")

        self.coordinator.report(
            "media-search",
            self.report(
                state="handed_off",
                next_plugin_id="renaming",
                revision=2,
            ),
        )
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report("open115", self.report(revision=3))
        self.assertEqual(raised.exception.code, "owner_mismatch")

    def test_handoff_changes_owner_without_releasing_gate(self):
        self.coordinator.report("media-search", self.report())
        handed_off = self.coordinator.report(
            "media-search",
            self.report(
                state="handed_off",
                next_plugin_id="open115",
                revision=2,
            ),
        )
        self.assertEqual(self.coordinator.active(10, 1), handed_off)

        record = self.coordinator.report(
            "open115",
            self.report(state="running", stage="download", revision=3),
        )
        self.assertEqual(record.plugin_id, "open115")
        self.assertEqual(record.next_plugin_id, "")
        self.assertEqual(self.coordinator.active(10, 1).operation_id, "op-1")

    def test_full_feature_handoff_chain_keeps_one_gate_until_plex_completes(self):
        chain = (
            ("media-search", "planning", "open115"),
            ("open115", "downloading", "renaming"),
            ("renaming", "organizing", "plex-management"),
        )
        revision = 1
        self.coordinator.report(
            "media-search",
            self.report(stage="planning", revision=revision),
        )

        for plugin_id, stage, next_plugin_id in chain:
            revision += 1
            handed_off = self.coordinator.report(
                plugin_id,
                self.report(
                    state="handed_off",
                    stage=stage,
                    next_plugin_id=next_plugin_id,
                    revision=revision,
                ),
            )
            self.assertEqual(self.coordinator.active(10, 1), handed_off)
            revision += 1
            accepted = self.coordinator.report(
                next_plugin_id,
                self.report(
                    state="running",
                    stage=f"{next_plugin_id}-accepted",
                    revision=revision,
                ),
            )
            self.assertEqual(accepted.plugin_id, next_plugin_id)
            self.assertEqual(self.coordinator.active(10, 1).operation_id, "op-1")

        completed = self.coordinator.report(
            "plex-management",
            self.report(
                state="completed",
                stage="completed",
                control="",
                revision=revision + 1,
            ),
        )
        self.assertEqual(completed.state, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_source_may_finish_failed_provisional_handoff_before_target_accepts(self):
        self.coordinator.report("media-search", self.report())
        self.coordinator.report(
            "media-search",
            self.report(
                state="handed_off",
                next_plugin_id="open115",
                revision=2,
            ),
        )

        failed = self.coordinator.report(
            "media-search",
            self.report(
                state="failed",
                stage="submitting_download",
                status_text="提交失败",
                control="",
                revision=3,
            ),
        )

        self.assertEqual(failed.state, "failed")
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_source_may_enter_cancelling_during_provisional_handoff(self):
        self.coordinator.report("media-search", self.report())
        self.coordinator.report(
            "media-search",
            self.report(
                state="handed_off",
                next_plugin_id="open115",
                revision=2,
            ),
        )

        cancelling = self.coordinator.report(
            "media-search",
            self.report(
                state="cancelling",
                stage="submitting_download",
                revision=3,
            ),
        )

        self.assertEqual(cancelling.state, "cancelling")
        self.assertEqual(cancelling.next_plugin_id, "")
        self.assertEqual(self.coordinator.active(10, 1), cancelling)

    def test_terminal_state_ignores_late_higher_revision_from_same_feature(self):
        self.coordinator.report("media-search", self.report())
        terminal = self.coordinator.report(
            "media-search",
            self.report(state="cancelled", control="", revision=2),
        )

        late = self.coordinator.report(
            "media-search",
            self.report(
                state="running",
                stage="late-result",
                revision=99,
            ),
        )

        self.assertEqual(late, terminal)
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_late_revision_cannot_overwrite_cancelled_state(self):
        self.coordinator.report("media-search", self.report())
        current = self.coordinator.report(
            "media-search",
            self.report(revision=3, state="cancelled", control=""),
        )
        stale = self.coordinator.report(
            "media-search",
            self.report(revision=2, state="running"),
        )
        self.assertEqual(stale, current)
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_message_id_and_record_survive_reload(self):
        from app.core.interaction_coordinator import InteractionCoordinator

        created = self.coordinator.report("media-search", self.report())
        updated = self.coordinator.set_message_id(
            created.operation_id, 77, "photo"
        )
        self.assertEqual(updated.message_id, 77)
        self.assertEqual(updated.message_kind, "photo")
        self.coordinator.close()

        self.coordinator = InteractionCoordinator(self.database_path)
        reloaded = self.coordinator.active(10, 1)
        self.assertEqual(reloaded.operation_id, "op-1")
        self.assertEqual(reloaded.message_id, 77)
        self.assertEqual(reloaded.message_kind, "photo")

    def test_interrupt_unowned_releases_only_missing_feature_operations(self):
        self.coordinator.report("media-search", self.report())
        self.coordinator.report(
            "media-search",
            self.report(state="completed", control="", revision=2),
        )
        self.coordinator.report(
            "open115",
            self.report(
                operation_id="op-2",
                chat_id=20,
                user_id=2,
                revision=1,
            ),
        )

        interrupted = self.coordinator.interrupt_unowned({"media-search"})

        self.assertEqual([record.operation_id for record in interrupted], ["op-2"])
        self.assertEqual(interrupted[0].state, "interrupted")
        self.assertEqual(interrupted[0].revision, 2)
        self.assertIsNone(self.coordinator.active(20, 2))

    def test_interrupt_unconfirmed_uses_operation_identity_not_only_plugin(self):
        self.coordinator.report("media-search", self.report())
        self.coordinator.report(
            "media-search",
            self.report(
                operation_id="op-2",
                chat_id=20,
                user_id=2,
                revision=1,
            ),
        )

        interrupted = self.coordinator.interrupt_unconfirmed({"op-1"})

        self.assertEqual([record.operation_id for record in interrupted], ["op-2"])
        self.assertEqual(
            [record.operation_id for record in self.coordinator.active_records()],
            ["op-1"],
        )

    def test_report_validation_rejects_unsafe_or_invalid_values(self):
        from app.core.interaction_coordinator import InteractionError

        cases = [
            ({"state": "pending"}, "invalid_state"),
            ({"control": "stop"}, "invalid_control"),
            ({"details": {"bad": object()}}, "invalid_details"),
            ({"revision": 0}, "invalid_revision"),
            ({"chat_id": 0}, "invalid_owner"),
        ]
        for overrides, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(InteractionError) as raised:
                    self.coordinator.report("media-search", self.report(**overrides))
                self.assertEqual(raised.exception.code, code)

    def test_status_text_is_bounded(self):
        record = self.coordinator.report(
            "media-search",
            self.report(status_text="状" * 5000),
        )
        self.assertEqual(len(record.status_text), 4096)

    def test_sensitive_details_and_raw_magnets_are_redacted_before_storage(self):
        record = self.coordinator.report(
            "open115",
            self.report(details={
                "access_token": "secret-value",
                "nested": {"source": "magnet:?xt=urn:btih:raw-secret"},
            }),
        )

        self.assertEqual(record.details["access_token"], "***redacted***")
        self.assertEqual(record.details["nested"]["source"], "magnet:?***redacted***")


if __name__ == "__main__":
    unittest.main()

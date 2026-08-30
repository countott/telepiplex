import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class InteractionCoordinatorTest(unittest.TestCase):
    def setUp(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "host.db"
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
        record = self.coordinator.report("search", self.report())

        self.assertEqual(record.plugin_id, "search")
        self.assertEqual(record.details, {"provider": "demo"})
        self.assertEqual(self.coordinator.active(10, 1), record)

        terminal = self.coordinator.report(
            "search",
            self.report(state="completed", control="", revision=2),
        )
        self.assertEqual(terminal.state, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_segment_report_opens_durable_owner_scoped_photo_segment(self):
        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={
                    "text": "正在识别媒体…",
                    "buttons": [],
                },
            ),
        )

        self.assertEqual(operation.operation_id, "op-1")
        self.assertEqual(segment.operation_id, "op-1")
        self.assertEqual(segment.sequence, 1)
        self.assertEqual(segment.owner_plugin_id, "search")
        self.assertEqual(segment.role, "identity")
        self.assertEqual(segment.generation, 1)
        self.assertEqual(segment.presentation_kind, "photo")
        self.assertEqual(segment.state, "creating")
        self.assertEqual(segment.business_revision, 1)
        self.assertEqual(segment.rendered_revision, 0)
        self.assertEqual(segment.callback_generation, 1)
        self.assertEqual(segment.delivery_state, "reserved")
        self.assertIsNone(segment.message_id)
        self.assertEqual(self.coordinator.get_active_segment("op-1"), segment)

    def test_bound_segment_message_survives_coordinator_restart(self):
        _operation, created = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                }
            ),
        )

        bound = self.coordinator.bind_segment_message(
            created.segment_id,
            owner_plugin_id="search",
            generation=1,
            chat_id=10,
            message_id=77,
        )
        self.coordinator.close()

        from app.runtime.interaction_coordinator import InteractionCoordinator

        self.coordinator = InteractionCoordinator(self.database_path)
        reloaded = self.coordinator.get_active_segment("op-1")

        self.assertEqual(reloaded, bound)
        self.assertEqual(reloaded.state, "open")
        self.assertEqual(reloaded.delivery_state, "delivered")
        self.assertEqual(reloaded.message_id, 77)
        self.assertEqual(reloaded.message_kind, "photo")
        self.assertIsNone(self.coordinator.get("op-1").message_id)

    def test_only_one_creator_can_claim_a_reserved_segment_delivery(self):
        _operation, created = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                }
            ),
        )

        claimed = self.coordinator.claim_segment_delivery(
            created.segment_id,
            owner_plugin_id="search",
            generation=created.generation,
        )
        competing = self.coordinator.claim_segment_delivery(
            created.segment_id,
            owner_plugin_id="search",
            generation=created.generation,
        )

        self.assertEqual(claimed.delivery_state, "delivering")
        self.assertIsNone(competing)

    def test_only_one_replacement_can_claim_a_known_text_cursor(self):
        _operation, created = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                }
            ),
        )
        bound = self.coordinator.bind_segment_message(
            created.segment_id,
            owner_plugin_id="search",
            generation=created.generation,
            chat_id=10,
            message_id=77,
            message_kind="text",
        )

        claimed = self.coordinator.claim_segment_replacement_delivery(
            bound.segment_id,
            owner_plugin_id="search",
            generation=bound.generation,
            chat_id=10,
            expected_message_id=77,
            expected_message_kind="text",
        )
        competing = self.coordinator.claim_segment_replacement_delivery(
            bound.segment_id,
            owner_plugin_id="search",
            generation=bound.generation,
            chat_id=10,
            expected_message_id=77,
            expected_message_kind="text",
        )
        replaced = self.coordinator.replace_segment_message(
            bound.segment_id,
            owner_plugin_id="search",
            generation=bound.generation,
            chat_id=10,
            expected_message_id=77,
            expected_message_kind="text",
            message_id=78,
            message_kind="photo",
        )

        self.assertEqual(claimed.delivery_state, "delivering")
        self.assertIsNone(competing)
        self.assertEqual(replaced.state, "open")
        self.assertEqual(replaced.delivery_state, "delivered")
        self.assertEqual(replaced.message_id, 78)
        self.assertEqual(replaced.message_kind, "photo")

    def test_same_segment_accepts_a_newer_business_revision_in_place(self):
        _operation, created = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "正在识别媒体…", "buttons": []},
            ),
        )

        operation, updated = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                stage="candidates",
                status_text="请选择作品",
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={
                    "text": "请选择作品",
                    "buttons": [["死神：千年血战篇"]],
                },
            ),
        )

        self.assertEqual(updated.segment_id, created.segment_id)
        self.assertEqual(updated.sequence, 1)
        self.assertEqual(updated.business_revision, 2)
        self.assertEqual(updated.rendered_revision, 0)
        self.assertEqual(updated.projection, {
            "text": "请选择作品",
            "buttons": [["死神：千年血战篇"]],
        })
        self.assertEqual(operation.revision, 2)
        self.assertEqual(operation.active_segment_id, created.segment_id)

    def test_active_segment_rejects_role_or_presentation_kind_conflict(self):
        from app.runtime.interaction_coordinator import InteractionError

        _operation, created = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                }
            ),
        )

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.accept_segment_report(
                "search",
                self.report(
                    revision=2,
                    segment={
                        "role": "search",
                        "presentation_kind": "text",
                    },
                ),
            )

        self.assertEqual(raised.exception.code, "segment_role_conflict")
        self.assertEqual(self.coordinator.get("op-1").revision, 1)
        self.assertEqual(self.coordinator.get_active_segment("op-1"), created)

    def test_same_segment_revision_and_projection_is_idempotent(self):
        report = self.report(
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
            projection={"text": "正在识别媒体…", "buttons": []},
        )
        first_operation, first_segment = self.coordinator.accept_segment_report(
            "search", report
        )

        replay_operation, replay_segment = self.coordinator.accept_segment_report(
            "search", report
        )

        self.assertEqual(replay_operation, first_operation)
        self.assertEqual(replay_segment, first_segment)

    def test_equal_revision_segment_report_cannot_replace_current_projection(self):
        first_operation, first_segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                stage="candidates",
                status_text="请选择作品",
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "请选择作品", "buttons": [["死神"]]},
            ),
        )

        replay_operation, replay_segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                stage="prowlarr",
                status_text="正在搜索片源",
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                },
                projection={"text": "过时的同序号投影"},
            ),
        )

        self.assertEqual(replay_operation, first_operation)
        self.assertEqual(replay_segment, first_segment)
        self.assertEqual(
            self.coordinator.get_active_segment("op-1"),
            first_segment,
        )

    def test_stale_segment_report_cannot_recreate_a_sealed_segment(self):
        _operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                },
                projection={"text": "正在搜索片源"},
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=94,
        )
        segment = self.coordinator.record_segment_rendered(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            business_revision=1,
            projection_hash=segment.projection_hash,
        )
        self.coordinator.seal_segment("search", "op-1", "search")
        self.coordinator.complete_segment_seal(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
        )
        current = self.coordinator.report(
            "search",
            self.report(
                revision=2,
                stage="handoff",
                status_text="准备推送下载",
            ),
        )

        stale_operation, stale_segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=1,
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                },
                projection={"text": "晚到的旧搜索结果"},
            ),
        )

        self.assertEqual(stale_operation, current)
        self.assertIsNone(stale_segment)
        self.assertIsNone(self.coordinator.get_active_segment("op-1"))

        handed_off = self.coordinator.report(
            "search",
            self.report(
                revision=3,
                state="handed_off",
                next_plugin_id="download",
            ),
        )
        self.assertEqual(handed_off.state, "handed_off")
        self.assertEqual(handed_off.revision, 3)

    def test_legacy_message_cursor_migrates_once_to_read_only_open_segment(self):
        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 88, "photo")
        self.coordinator.close()

        from app.runtime.interaction_coordinator import (
            InteractionCoordinator,
            InteractionError,
        )

        self.coordinator = InteractionCoordinator(self.database_path)
        migrated = self.coordinator.get_active_segment("op-1")

        self.assertEqual(migrated.role, "legacy")
        self.assertEqual(migrated.owner_plugin_id, "search")
        self.assertEqual(migrated.state, "open")
        self.assertEqual(migrated.delivery_state, "delivered")
        self.assertEqual(migrated.message_id, 88)
        self.assertEqual(migrated.message_kind, "photo")

        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.database_path)
        replay = self.coordinator.get_active_segment("op-1")
        self.assertEqual(replay.segment_id, migrated.segment_id)

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.accept_segment_report(
                "search",
                self.report(
                    revision=2,
                    segment={
                        "role": "identity",
                        "presentation_kind": "photo",
                    },
                ),
            )
        self.assertEqual(raised.exception.code, "segment_role_conflict")

    def test_rendered_segment_seals_before_the_next_role_opens(self):
        _operation, identity = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "已确认：死神：千年血战篇"},
            ),
        )
        identity = self.coordinator.bind_segment_message(
            identity.segment_id,
            owner_plugin_id="search",
            generation=identity.generation,
            chat_id=10,
            message_id=91,
        )
        identity = self.coordinator.record_segment_rendered(
            identity.segment_id,
            owner_plugin_id="search",
            generation=identity.generation,
            business_revision=1,
            projection_hash=identity.projection_hash,
        )

        sealing = self.coordinator.seal_segment(
            "search", "op-1", "identity"
        )
        sealed = self.coordinator.complete_segment_seal(
            sealing.segment_id,
            owner_plugin_id="search",
            generation=sealing.generation,
        )

        self.assertEqual(identity.rendered_revision, 1)
        self.assertEqual(sealing.state, "sealing")
        self.assertEqual(sealed.state, "sealed")
        self.assertIsNotNone(sealed.sealed_at)
        self.assertIsNone(self.coordinator.get_active_segment("op-1"))

        operation, search_segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                stage="prowlarr",
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                },
                projection={"text": "正在搜索片源…"},
            ),
        )
        self.assertEqual(operation.revision, 2)
        self.assertEqual(search_segment.sequence, 2)
        self.assertEqual(search_segment.generation, 2)
        self.assertEqual(search_segment.role, "search")
        self.assertNotEqual(search_segment.segment_id, sealed.segment_id)

    def test_owner_handoff_is_rejected_until_the_source_segment_is_sealed(self):
        from app.runtime.interaction_coordinator import InteractionError

        _operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                }
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=93,
        )
        segment = self.coordinator.record_segment_rendered(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            business_revision=1,
            projection_hash=segment.projection_hash,
        )

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "search",
                self.report(
                    revision=2,
                    state="handed_off",
                    next_plugin_id="download",
                ),
            )
        self.assertEqual(raised.exception.code, "segment_not_sealed")
        self.assertEqual(self.coordinator.get("op-1").revision, 1)

        self.coordinator.seal_segment("search", "op-1", "search")
        self.coordinator.complete_segment_seal(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
        )
        handed_off = self.coordinator.report(
            "search",
            self.report(
                revision=2,
                state="handed_off",
                next_plugin_id="download",
            ),
        )
        operation, download = self.coordinator.accept_segment_report(
            "download",
            self.report(
                revision=3,
                stage="download",
                segment={
                    "role": "download",
                    "presentation_kind": "text",
                },
            ),
        )

        self.assertEqual(handed_off.state, "handed_off")
        self.assertEqual(operation.plugin_id, "download")
        self.assertEqual(download.sequence, 2)
        self.assertNotEqual(download.segment_id, segment.segment_id)

    def test_nonnewer_handoff_report_does_not_hit_the_segment_seal_gate(self):
        current, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                segment={
                    "role": "search",
                    "presentation_kind": "text",
                },
            ),
        )

        for revision in (1, 2):
            with self.subTest(revision=revision):
                replay = self.coordinator.report(
                    "search",
                    self.report(
                        revision=revision,
                        state="handed_off",
                        next_plugin_id="download",
                    ),
                )
                self.assertEqual(replay, current)
                self.assertEqual(
                    self.coordinator.get_active_segment("op-1"),
                    segment,
                )

    def test_segment_callback_claim_accepts_only_the_first_keyboard_generation(self):
        _operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "请选择作品", "buttons": [["死神"]]},
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
        )

        accepted = self.coordinator.claim_segment_callback(
            "search",
            "op-1",
            message_id=92,
            segment_generation=segment.generation,
            callback_generation=1,
            callback_token="search:select:p1:0",
            busy_text="正在确认媒体身份…",
        )
        replay = self.coordinator.claim_segment_callback(
            "search",
            "op-1",
            message_id=92,
            segment_generation=segment.generation,
            callback_generation=1,
            callback_token="search:select:p1:0",
            busy_text="正在确认媒体身份…",
        )

        self.assertEqual(accepted.callback_generation, 2)
        self.assertEqual(accepted.callback_state, "busy")
        self.assertEqual(accepted.callback_token, "search:select:p1:0")
        self.assertEqual(accepted.callback_busy_text, "正在确认媒体身份…")
        self.assertIsNone(replay)
        self.assertEqual(
            self.coordinator.get_active_segment("op-1").callback_generation,
            2,
        )

        _operation, refreshed = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                state="awaiting_input",
                stage="candidate_selection",
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "请选择作品（第 2 页）"},
            ),
        )
        self.assertEqual(refreshed.callback_generation, 2)
        self.assertEqual(refreshed.callback_state, "busy")
        self.assertEqual(refreshed.callback_token, "search:select:p1:0")
        self.assertEqual(refreshed.callback_busy_text, "正在确认媒体身份…")

        released = self.coordinator.release_segment_callback(
            "search",
            "op-1",
            message_id=92,
            segment_generation=segment.generation,
            callback_generation=2,
            callback_token="search:select:p1:0",
        )
        self.assertEqual(released.callback_state, "idle")
        self.assertEqual(released.callback_token, "")
        self.assertEqual(released.callback_busy_text, "")
        self.assertEqual(released.rendered_projection_hash, "")

    def test_operation_milestone_is_duplicate_only_after_atomic_completion(self):
        from app.runtime.interaction_coordinator import InteractionError

        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 77, "photo")

        first = self.coordinator.claim_milestone(
            "search",
            "op-1",
            "media-douban-35981510",
        )
        retry_before_completion = self.coordinator.claim_milestone(
            "search",
            "op-1",
            "media-douban-35981510",
        )
        completed = self.coordinator.complete_milestone(
            "search",
            "op-1",
            "media-douban-35981510",
        )
        duplicate = self.coordinator.claim_milestone(
            "search",
            "op-1",
            "media-douban-35981510",
        )

        self.assertEqual(first.operation_id, "op-1")
        self.assertEqual(retry_before_completion.operation_id, "op-1")
        self.assertIsNone(completed.message_id)
        self.assertIsNone(duplicate)
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.claim_milestone(
                "rename",
                "op-1",
                "media-douban-35981510-other",
            )
        self.assertEqual(raised.exception.code, "owner_mismatch")

    def test_failed_milestone_delivery_can_be_retried(self):
        self.coordinator.report("search", self.report())
        self.assertIsNotNone(
            self.coordinator.claim_milestone("search", "op-1", "media-1")
        )

        self.coordinator.release_milestone("search", "op-1", "media-1")

        self.assertIsNotNone(
            self.coordinator.claim_milestone("search", "op-1", "media-1")
        )

    def test_milestone_intent_persists_payload_and_exact_enqueue_cursor(self):
        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 41, "photo")

        intent, duplicate = self.coordinator.enqueue_milestone(
            "search",
            {
                "operation_id": "op-1",
                "milestone_id": "media-frozen",
                "mode": "identity",
                "text": "🎬 繁花",
                "photo_url": "https://img.example/frozen.jpg",
            },
        )

        self.assertFalse(duplicate)
        self.assertEqual(intent.delivery_state, "pending")
        self.assertEqual(intent.attempt_count, 0)
        self.assertEqual(intent.expected_message_id, 41)
        self.assertEqual(intent.expected_message_kind, "photo")
        self.assertEqual(intent.text, "🎬 繁花")
        self.assertEqual(intent.photo_url, "https://img.example/frozen.jpg")

        reloaded = self.coordinator.get_milestone("op-1", "media-frozen")
        self.assertEqual(reloaded, intent)

    def test_milestone_null_cursor_is_exact_and_duplicate_cannot_mutate_payload(self):
        payload = {
            "operation_id": "op-1",
            "milestone_id": "stage-frozen",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        }
        self.coordinator.report("search", self.report())

        first, duplicate = self.coordinator.enqueue_milestone("search", payload)
        replay, replay_duplicate = self.coordinator.enqueue_milestone(
            "search", payload
        )

        self.assertFalse(duplicate)
        self.assertTrue(replay_duplicate)
        self.assertEqual(replay, first)
        self.assertIsNone(first.expected_message_id)
        self.assertEqual(first.expected_message_kind, "")

        from app.runtime.interaction_coordinator import InteractionError

        conflicting = dict(payload, text="被篡改")
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.enqueue_milestone("search", conflicting)
        self.assertEqual(raised.exception.code, "milestone_conflict")
        self.assertEqual(
            self.coordinator.get_milestone("op-1", "stage-frozen").text,
            "搜索完成",
        )

    def test_milestone_claim_rejection_bound_and_uncertain_quarantine(self):
        self.coordinator.report("search", self.report())
        self.coordinator.enqueue_milestone("search", {
            "operation_id": "op-1",
            "milestone_id": "stage-retry",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })

        for attempt in range(1, 4):
            claimed = self.coordinator.claim_milestone_delivery(
                "op-1", "stage-retry"
            )
            self.assertEqual(claimed.attempt_count, attempt)
            self.coordinator.reject_milestone_delivery(
                "search", "op-1", "stage-retry", "telegram_rejected"
            )
        self.assertIsNone(
            self.coordinator.claim_milestone_delivery("op-1", "stage-retry")
        )
        exhausted = self.coordinator.get_milestone("op-1", "stage-retry")
        self.assertEqual(exhausted.delivery_state, "failed")
        self.assertEqual(exhausted.attempt_count, 3)

        with self.coordinator._connection:
            self.coordinator._connection.execute(
                "UPDATE operation_milestones SET delivery_state = 'delivering' "
                "WHERE operation_id = ? AND milestone_id = ?",
                ("op-1", "stage-retry"),
            )
        self.coordinator.mark_milestone_delivery_unknown(
            "search", "op-1", "stage-retry", "TimedOut"
        )
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "stage-retry"
            ).delivery_state,
            "unknown",
        )
        self.assertNotIn(
            ("op-1", "stage-retry"),
            {
                (item.operation_id, item.milestone_id)
                for item in self.coordinator.recover_milestones()
            },
        )

    def test_claim_materialization_failure_rolls_back_pending_state(self):
        self.coordinator.report("search", self.report())
        self.coordinator.enqueue_milestone("search", {
            "operation_id": "op-1",
            "milestone_id": "claim-decode-failure",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })

        with patch.object(
            self.coordinator,
            "_milestone_from_row",
            side_effect=ValueError("durable milestone decode failed"),
        ):
            with self.assertRaisesRegex(
                ValueError, "durable milestone decode failed"
            ):
                self.coordinator.claim_milestone_delivery(
                    "op-1", "claim-decode-failure"
                )

        row = self.coordinator._connection.execute(
            "SELECT delivery_state, delivery_started, attempt_count "
            "FROM operation_milestones WHERE operation_id = ? "
            "AND milestone_id = ?",
            ("op-1", "claim-decode-failure"),
        ).fetchone()
        self.assertEqual(
            (
                str(row["delivery_state"]),
                int(row["delivery_started"]),
                int(row["attempt_count"]),
            ),
            ("pending", 0, 0),
        )

    def test_milestone_mutations_roll_back_when_return_materialization_fails(self):
        def create_operation(index: int):
            operation_id = f"op-decode-{index}"
            self.coordinator.report("search", self.report(
                operation_id=operation_id,
                chat_id=200 + index,
                user_id=200 + index,
            ))
            return operation_id

        def payload(operation_id: str, milestone_id: str):
            return {
                "operation_id": operation_id,
                "milestone_id": milestone_id,
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            }

        decode_error = ValueError("durable milestone decode failed")

        operation_id = create_operation(1)
        with self.subTest(method="enqueue-new"):
            with patch.object(
                self.coordinator,
                "_milestone_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.enqueue_milestone(
                        "search", payload(operation_id, "enqueue-new")
                    )
            count = self.coordinator._connection.execute(
                "SELECT COUNT(*) FROM operation_milestones "
                "WHERE operation_id = ? AND milestone_id = ?",
                (operation_id, "enqueue-new"),
            ).fetchone()[0]
            self.assertEqual(count, 0)

        operation_id = create_operation(2)
        duplicate_payload = payload(operation_id, "enqueue-duplicate")
        self.coordinator.enqueue_milestone("search", duplicate_payload)
        with self.subTest(method="enqueue-duplicate"):
            with patch.object(
                self.coordinator,
                "_milestone_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.enqueue_milestone(
                        "search", duplicate_payload
                    )
            count = self.coordinator._connection.execute(
                "SELECT COUNT(*) FROM operation_milestones "
                "WHERE operation_id = ? AND milestone_id = ?",
                (operation_id, "enqueue-duplicate"),
            ).fetchone()[0]
            self.assertEqual(count, 1)

        operation_id = create_operation(3)
        self.coordinator.enqueue_milestone(
            "search", payload(operation_id, "transition")
        )
        self.coordinator.claim_milestone_delivery(
            operation_id, "transition"
        )
        with self.subTest(method="transition"):
            with patch.object(
                self.coordinator,
                "_milestone_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.reject_milestone_delivery(
                        "search",
                        operation_id,
                        "transition",
                        "telegram_rejected",
                    )
            row = self.coordinator._connection.execute(
                "SELECT delivery_state, last_error "
                "FROM operation_milestones WHERE operation_id = ? "
                "AND milestone_id = ?",
                (operation_id, "transition"),
            ).fetchone()
            self.assertEqual(
                (str(row["delivery_state"]), str(row["last_error"])),
                ("delivering", ""),
            )

        operation_id = create_operation(4)
        self.coordinator.enqueue_milestone(
            "search", payload(operation_id, "target-new")
        )
        self.coordinator.claim_milestone_delivery(
            operation_id, "target-new"
        )
        with self.subTest(method="target-new"):
            with patch.object(
                self.coordinator,
                "_milestone_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.record_milestone_delivery_target(
                        "search", operation_id, "target-new", 90, "text"
                    )
            row = self.coordinator._connection.execute(
                "SELECT delivered_message_id, delivered_message_kind "
                "FROM operation_milestones WHERE operation_id = ? "
                "AND milestone_id = ?",
                (operation_id, "target-new"),
            ).fetchone()
            self.assertIsNone(row["delivered_message_id"])
            self.assertEqual(str(row["delivered_message_kind"]), "")

        operation_id = create_operation(5)
        self.coordinator.enqueue_milestone(
            "search", payload(operation_id, "target-existing")
        )
        self.coordinator.claim_milestone_delivery(
            operation_id, "target-existing"
        )
        self.coordinator.record_milestone_delivery_target(
            "search", operation_id, "target-existing", 91, "photo"
        )
        with self.subTest(method="target-existing"):
            with patch.object(
                self.coordinator,
                "_milestone_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.record_milestone_delivery_target(
                        "search",
                        operation_id,
                        "target-existing",
                        91,
                        "photo",
                    )
            target = self.coordinator.milestone_delivery_target(
                "search", operation_id, "target-existing"
            )
            self.assertEqual(target, (91, "photo"))

        operation_id = create_operation(6)
        self.coordinator.set_message_id(operation_id, 41, "text")
        self.coordinator.enqueue_milestone(
            "search", payload(operation_id, "complete")
        )
        self.coordinator.claim_milestone_delivery(operation_id, "complete")
        with self.subTest(method="complete"):
            with patch.object(
                self.coordinator,
                "_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.complete_milestone_delivery(
                        "search", operation_id, "complete"
                    )
            milestone = self.coordinator._connection.execute(
                "SELECT delivered, delivery_state FROM operation_milestones "
                "WHERE operation_id = ? AND milestone_id = ?",
                (operation_id, "complete"),
            ).fetchone()
            operation = self.coordinator._connection.execute(
                "SELECT message_id, message_kind FROM operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            self.assertEqual(
                (int(milestone["delivered"]), str(milestone["delivery_state"])),
                (0, "delivering"),
            )
            self.assertEqual(
                (int(operation["message_id"]), str(operation["message_kind"])),
                (41, "text"),
            )

        operation_id = create_operation(7)
        self.coordinator.set_message_id(operation_id, 42, "photo")
        self.coordinator.claim_milestone(
            "search", operation_id, "compat-complete"
        )
        with self.subTest(method="compat-complete"):
            with patch.object(
                self.coordinator,
                "_from_row",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(
                    ValueError, "durable milestone decode failed"
                ):
                    self.coordinator.complete_milestone(
                        "search", operation_id, "compat-complete"
                    )
            milestone = self.coordinator._connection.execute(
                "SELECT delivered, delivery_started, delivery_state "
                "FROM operation_milestones WHERE operation_id = ? "
                "AND milestone_id = ?",
                (operation_id, "compat-complete"),
            ).fetchone()
            operation = self.coordinator._connection.execute(
                "SELECT message_id, message_kind FROM operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            self.assertEqual(
                (
                    int(milestone["delivered"]),
                    int(milestone["delivery_started"]),
                    str(milestone["delivery_state"]),
                ),
                (0, 0, "pending"),
            )
            self.assertEqual(
                (int(operation["message_id"]), str(operation["message_kind"])),
                (42, "photo"),
            )

    def test_milestone_completion_uses_enqueue_cursor_and_owner_cas(self):
        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 41, "text")
        self.coordinator.enqueue_milestone("search", {
            "operation_id": "op-1",
            "milestone_id": "late-search",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })
        self.coordinator.claim_milestone_delivery("op-1", "late-search")

        self.coordinator.report("search", self.report(
            state="handed_off",
            stage="handoff_download",
            revision=2,
            next_plugin_id="download",
        ))
        accepted = self.coordinator.report("download", self.report(
            stage="downloading",
            revision=3,
        ))
        self.coordinator.set_message_id(accepted.operation_id, 42, "photo")

        self.coordinator.record_milestone_delivery_target(
            "search", "op-1", "late-search", 90, "text"
        )
        self.coordinator.complete_milestone_delivery(
            "search", "op-1", "late-search"
        )

        current = self.coordinator.get("op-1")
        self.assertEqual(current.plugin_id, "download")
        self.assertEqual(current.message_id, 42)
        self.assertEqual(current.message_kind, "photo")
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "late-search"
            ).delivery_state,
            "delivered",
        )

    def test_milestone_cursor_cas_preserves_newer_same_owner_cursors(self):
        scenarios = (
            ("expected-41-same", 41, None, None),
            ("expected-41-newer", 41, 42, 42),
            ("expected-null-same", None, None, None),
            ("expected-null-newer", None, 42, 42),
        )
        for index, (
            operation_id,
            expected_cursor,
            current_cursor,
            final_cursor,
        ) in enumerate(scenarios, 1):
            with self.subTest(operation_id=operation_id):
                report = self.report(
                    operation_id=operation_id,
                    chat_id=100 + index,
                    user_id=100 + index,
                )
                created = self.coordinator.report("search", report)
                if expected_cursor is not None:
                    self.coordinator.set_message_id(
                        created.operation_id, expected_cursor, "text"
                    )
                self.coordinator.enqueue_milestone("search", {
                    "operation_id": operation_id,
                    "milestone_id": "stage-cas",
                    "mode": "stage",
                    "text": "搜索完成",
                    "photo_url": "",
                })
                self.coordinator.claim_milestone_delivery(
                    operation_id, "stage-cas"
                )
                if current_cursor is not None and current_cursor != expected_cursor:
                    self.coordinator.set_message_id(
                        operation_id, current_cursor, "photo"
                    )

                self.coordinator.complete_milestone_delivery(
                    "search", operation_id, "stage-cas"
                )

                current = self.coordinator.get(operation_id)
                self.assertEqual(current.message_id, final_cursor)
                self.assertEqual(
                    current.message_kind,
                    "photo" if final_cursor is not None else "text",
                )

    def test_milestone_recovery_finalizes_target_and_quarantines_targetless(self):
        self.coordinator.report("search", self.report())
        for milestone_id in ("with-target", "without-target"):
            self.coordinator.enqueue_milestone("search", {
                "operation_id": "op-1",
                "milestone_id": milestone_id,
                "mode": "stage",
                "text": milestone_id,
                "photo_url": "",
            })
            self.coordinator.claim_milestone_delivery("op-1", milestone_id)
        self.coordinator.record_milestone_delivery_target(
            "search", "op-1", "with-target", 91, "text"
        )

        recoverable = self.coordinator.recover_milestones()

        self.assertEqual(recoverable, [])
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "with-target"
            ).delivery_state,
            "delivered",
        )
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "without-target"
            ).delivery_state,
            "unknown",
        )

    def test_milestone_recovery_preserves_materialization_error_and_transaction(self):
        self.coordinator.report("search", self.report())
        self.coordinator.enqueue_milestone("search", {
            "operation_id": "op-1",
            "milestone_id": "decode-failure",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })

        with patch.object(
            self.coordinator,
            "_milestone_from_row",
            side_effect=ValueError("durable milestone decode failed"),
        ):
            with self.assertRaisesRegex(
                ValueError, "durable milestone decode failed"
            ):
                self.coordinator.recover_milestones()

        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "decode-failure"
            ).delivery_state,
            "pending",
        )

    def test_existing_milestone_table_gains_delivery_state(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        legacy_path = Path(self.temp.name) / "legacy-host.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "CREATE TABLE operation_milestones ("
            "operation_id TEXT NOT NULL, milestone_id TEXT NOT NULL, "
            "plugin_id TEXT NOT NULL, created_at REAL NOT NULL, "
            "PRIMARY KEY(operation_id, milestone_id))"
        )
        connection.execute(
            "INSERT INTO operation_milestones VALUES (?, ?, ?, ?)",
            ("op-legacy", "media-complete", "search", 1.0),
        )
        connection.commit()
        connection.close()

        migrated = InteractionCoordinator(legacy_path)
        try:
            columns = {
                row["name"]
                for row in migrated._connection.execute(
                    "PRAGMA table_info(operation_milestones)"
                ).fetchall()
            }
            migrated.report(
                "search",
                self.report(operation_id="op-legacy"),
            )
            duplicate = migrated.claim_milestone(
                "search",
                "op-legacy",
                "media-complete",
            )
        finally:
            migrated.close()

        self.assertIn("delivered", columns)
        self.assertIn("delivery_started", columns)
        self.assertIn("delivered_message_id", columns)
        self.assertIsNone(duplicate)

    def test_legacy_milestone_migration_quarantines_payloadless_rows(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        legacy_path = Path(self.temp.name) / "legacy-task4-host.db"
        connection = sqlite3.connect(legacy_path)
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
        """)
        connection.execute(
            "INSERT INTO operations VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op-legacy-task4", 10, 1, "search", "running",
                "planning", "规划中", "cancel", 1, None, "", "", "{}",
                1.0, 1.0,
            ),
        )
        connection.executemany(
            "INSERT INTO operation_milestones VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("op-legacy-task4", "already-delivered", "search", 1, 1, 80, "text", 2.0),
                ("op-legacy-task4", "target-recorded", "search", 0, 1, 81, "photo", 3.0),
                ("op-legacy-task4", "started-targetless", "search", 0, 1, None, "", 4.0),
                ("op-legacy-task4", "unstarted-targetless", "search", 0, 0, None, "", 5.0),
            ],
        )
        connection.commit()
        connection.close()

        migrated = InteractionCoordinator(legacy_path)
        try:
            states = {
                milestone_id: migrated.get_milestone(
                    "op-legacy-task4", milestone_id
                ).delivery_state
                for milestone_id in (
                    "already-delivered",
                    "target-recorded",
                    "started-targetless",
                    "unstarted-targetless",
                )
            }
            self.assertEqual(states, {
                "already-delivered": "delivered",
                "target-recorded": "delivering",
                "started-targetless": "unknown",
                "unstarted-targetless": "unknown",
            })

            self.assertEqual(migrated.recover_milestones(), [])
            self.assertEqual(
                migrated.get_milestone(
                    "op-legacy-task4", "target-recorded"
                ).delivery_state,
                "delivered",
            )
        finally:
            migrated.close()

    def test_partial_milestone_schema_never_recovers_missing_text_payload(self):
        from app.handlers.interaction_handler import OperationMilestoneSink
        from app.runtime.interaction_coordinator import InteractionCoordinator

        partial_path = Path(self.temp.name) / "partial-task4-host.db"
        connection = sqlite3.connect(partial_path)
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
                mode TEXT NOT NULL DEFAULT '',
                photo_url TEXT NOT NULL DEFAULT '',
                delivery_state TEXT NOT NULL DEFAULT 'unknown',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                expected_message_id INTEGER,
                expected_message_kind TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(operation_id, milestone_id)
            );
        """)
        connection.execute(
            "INSERT INTO operations VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op-partial", 20, 2, "search", "running", "planning",
                "规划中", "cancel", 1, None, "", "", "{}", 1.0, 1.0,
            ),
        )
        connection.execute(
            "INSERT INTO operation_milestones VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op-partial", "partial-stage", "search", 0, 0, None, "",
                "stage", "", "pending", 0, "", None, "", 2.0, 2.0,
            ),
        )
        connection.commit()
        connection.close()

        first = InteractionCoordinator(partial_path)
        try:
            self.assertEqual(
                first.get_milestone(
                    "op-partial", "partial-stage"
                ).delivery_state,
                "unknown",
            )
        finally:
            first.close()

        second = InteractionCoordinator(partial_path)
        try:
            self.assertEqual(
                second.get_milestone(
                    "op-partial", "partial-stage"
                ).delivery_state,
                "unknown",
            )

            async def recover_without_delivery():
                delivery = AsyncMock(return_value=True)
                sink = OperationMilestoneSink(second, delivery)
                await sink.start()
                await sink.drain()
                return delivery.await_count

            self.assertEqual(asyncio.run(recover_without_delivery()), 0)
            self.assertEqual(
                second.get_milestone(
                    "op-partial", "partial-stage"
                ).delivery_state,
                "unknown",
            )
        finally:
            second.close()

    def test_malformed_recorded_targets_stay_unknown_across_reopen_and_recovery(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        corrupt_path = Path(self.temp.name) / "corrupt-target-task4-host.db"
        seeded = InteractionCoordinator(corrupt_path)
        try:
            created = seeded.report("search", self.report(
                operation_id="op-corrupt-target",
                chat_id=30,
                user_id=3,
            ))
            seeded.set_message_id(created.operation_id, 41, "text")
            malformed_targets = (
                ("zero-id", 0, "text"),
                ("negative-id", -1, "photo"),
                ("empty-kind", 90, ""),
                ("invalid-kind", 91, "bogus"),
            )
            for milestone_id, message_id, message_kind in malformed_targets:
                seeded.enqueue_milestone("search", {
                    "operation_id": "op-corrupt-target",
                    "milestone_id": milestone_id,
                    "mode": "stage",
                    "text": "搜索完成",
                    "photo_url": "",
                })
                seeded.claim_milestone_delivery(
                    "op-corrupt-target", milestone_id
                )
                with seeded._connection:
                    seeded._connection.execute(
                        "UPDATE operation_milestones SET "
                        "delivered_message_id = ?, delivered_message_kind = ? "
                        "WHERE operation_id = ? AND milestone_id = ?",
                        (
                            message_id,
                            message_kind,
                            "op-corrupt-target",
                            milestone_id,
                        ),
                    )
        finally:
            seeded.close()

        first = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(
                {
                    milestone_id: first.get_milestone(
                        "op-corrupt-target", milestone_id
                    ).delivery_state
                    for milestone_id, _message_id, _message_kind
                    in malformed_targets
                },
                {
                    milestone_id: "unknown"
                    for milestone_id, _message_id, _message_kind
                    in malformed_targets
                },
            )
        finally:
            first.close()

        second = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(second.recover_milestones(), [])
            self.assertEqual(
                {
                    milestone_id: second.get_milestone(
                        "op-corrupt-target", milestone_id
                    ).delivery_state
                    for milestone_id, _message_id, _message_kind
                    in malformed_targets
                },
                {
                    milestone_id: "unknown"
                    for milestone_id, _message_id, _message_kind
                    in malformed_targets
                },
            )
            current = second.get("op-corrupt-target")
            self.assertEqual(current.message_id, 41)
            self.assertEqual(current.message_kind, "text")
        finally:
            second.close()

    def test_malformed_expected_cursors_quarantine_pending_and_targeted_rows(self):
        from app.handlers.interaction_handler import OperationMilestoneSink
        from app.runtime.interaction_coordinator import InteractionCoordinator

        corrupt_path = Path(self.temp.name) / "corrupt-cursor-task4-host.db"
        malformed_cursors = (
            ("real-id", 41.5, "text"),
            ("text-id", "corrupt", "text"),
            ("negative-id", -1, "text"),
            ("zero-id", 0, "text"),
            ("id-without-kind", 41, ""),
            ("kind-without-id", None, "text"),
            ("invalid-kind", 41, "bogus"),
        )
        seeded = InteractionCoordinator(corrupt_path)
        try:
            created = seeded.report("search", self.report(
                operation_id="op-corrupt-cursor",
                chat_id=40,
                user_id=4,
            ))
            seeded.set_message_id(created.operation_id, 41, "text")
            for index, (case, expected_id, expected_kind) in enumerate(
                malformed_cursors, 1
            ):
                for target_state in ("pending", "targeted"):
                    milestone_id = f"{target_state}-{case}"
                    seeded.enqueue_milestone("search", {
                        "operation_id": "op-corrupt-cursor",
                        "milestone_id": milestone_id,
                        "mode": "stage",
                        "text": "搜索完成",
                        "photo_url": "",
                    })
                    update = (
                        "UPDATE operation_milestones SET "
                        "expected_message_id = ?, expected_message_kind = ?"
                    )
                    values = [expected_id, expected_kind]
                    if target_state == "targeted":
                        update += (
                            ", delivered_message_id = ?, "
                            "delivered_message_kind = 'text'"
                        )
                        values.append(100 + index)
                    update += " WHERE operation_id = ? AND milestone_id = ?"
                    values.extend(("op-corrupt-cursor", milestone_id))
                    with seeded._connection:
                        seeded._connection.execute(update, tuple(values))
        finally:
            seeded.close()

        expected_states = {
            f"{target_state}-{case}": "unknown"
            for case, _expected_id, _expected_kind in malformed_cursors
            for target_state in ("pending", "targeted")
        }

        def raw_states(coordinator):
            return {
                str(row["milestone_id"]): str(row["delivery_state"])
                for row in coordinator._connection.execute(
                    "SELECT milestone_id, delivery_state "
                    "FROM operation_milestones WHERE operation_id = ?",
                    ("op-corrupt-cursor",),
                ).fetchall()
            }

        first = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(first), expected_states)
        finally:
            first.close()

        second = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(second), expected_states)
            self.assertEqual(second.recover_milestones(), [])

            async def start_without_delivery():
                delivery = AsyncMock(return_value=True)
                sink = OperationMilestoneSink(second, delivery)
                await sink.start()
                await sink.drain()
                return delivery.await_count

            self.assertEqual(asyncio.run(start_without_delivery()), 0)
            self.assertEqual(raw_states(second), expected_states)
            current = second.get("op-corrupt-cursor")
            self.assertEqual(current.message_id, 41)
            self.assertEqual(current.message_kind, "text")
        finally:
            second.close()

    def test_malformed_attempt_counts_quarantine_pending_and_targeted_rows(self):
        from app.handlers.interaction_handler import OperationMilestoneSink
        from app.runtime.interaction_coordinator import InteractionCoordinator

        corrupt_path = Path(self.temp.name) / "corrupt-attempt-task4-host.db"
        malformed_attempts = (
            ("real", 1.5),
            ("text", "corrupt"),
            ("negative", -1),
            ("above-max", 4),
        )
        seeded = InteractionCoordinator(corrupt_path)
        try:
            created = seeded.report("search", self.report(
                operation_id="op-corrupt-attempt",
                chat_id=41,
                user_id=7,
            ))
            seeded.set_message_id(created.operation_id, 41, "text")
            for index, (case, attempt_count) in enumerate(
                malformed_attempts, 1
            ):
                for target_state in ("pending", "targeted"):
                    milestone_id = f"{target_state}-{case}"
                    seeded.enqueue_milestone("search", {
                        "operation_id": "op-corrupt-attempt",
                        "milestone_id": milestone_id,
                        "mode": "stage",
                        "text": "搜索完成",
                        "photo_url": "",
                    })
                    update = (
                        "UPDATE operation_milestones SET attempt_count = ?"
                    )
                    values = [attempt_count]
                    if target_state == "targeted":
                        update += (
                            ", delivered_message_id = ?, "
                            "delivered_message_kind = 'text'"
                        )
                        values.append(120 + index)
                    update += " WHERE operation_id = ? AND milestone_id = ?"
                    values.extend(("op-corrupt-attempt", milestone_id))
                    with seeded._connection:
                        seeded._connection.execute(update, tuple(values))
        finally:
            seeded.close()

        expected_states = {
            f"{target_state}-{case}": "unknown"
            for case, _attempt_count in malformed_attempts
            for target_state in ("pending", "targeted")
        }

        def raw_states(coordinator):
            return {
                str(row["milestone_id"]): str(row["delivery_state"])
                for row in coordinator._connection.execute(
                    "SELECT milestone_id, delivery_state "
                    "FROM operation_milestones WHERE operation_id = ?",
                    ("op-corrupt-attempt",),
                ).fetchall()
            }

        first = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(first), expected_states)
        finally:
            first.close()

        second = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(second), expected_states)
            self.assertEqual(second.recover_milestones(), [])

            async def start_without_delivery():
                delivery = AsyncMock(return_value=True)
                sink = OperationMilestoneSink(second, delivery)
                await sink.start()
                await sink.drain()
                return delivery.await_count

            self.assertEqual(asyncio.run(start_without_delivery()), 0)
            self.assertEqual(raw_states(second), expected_states)
            current = second.get("op-corrupt-attempt")
            self.assertEqual(current.message_id, 41)
            self.assertEqual(current.message_kind, "text")
        finally:
            second.close()

    def test_unproven_pending_and_failed_rows_never_restart_delivery(self):
        from app.handlers.interaction_handler import OperationMilestoneSink
        from app.runtime.interaction_coordinator import InteractionCoordinator

        corrupt_path = Path(self.temp.name) / "corrupt-provenance-task4-host.db"
        corruptions = (
            ("pending-started", "pending", 1, 1, ""),
            ("pending-error", "pending", 0, 0, "telegram_rejected"),
            ("pending-real-started", "pending", 0.5, 0, ""),
            ("pending-text-started", "pending", "corrupt", 0, ""),
            ("failed-timeout", "failed", 1, 1, "TimedOut"),
            ("failed-empty", "failed", 1, 1, ""),
            (
                "failed-real-started",
                "failed",
                1.5,
                1,
                "telegram_rejected",
            ),
            (
                "failed-invalid-explicit-shape",
                "failed",
                0,
                1,
                "explicit_rejection",
            ),
        )
        seeded = InteractionCoordinator(corrupt_path)
        try:
            created = seeded.report("search", self.report(
                operation_id="op-corrupt-provenance",
                chat_id=42,
                user_id=8,
            ))
            seeded.set_message_id(created.operation_id, 41, "text")
            for (
                milestone_id,
                state,
                delivery_started,
                attempt_count,
                last_error,
            ) in corruptions:
                seeded.enqueue_milestone("search", {
                    "operation_id": "op-corrupt-provenance",
                    "milestone_id": milestone_id,
                    "mode": "stage",
                    "text": "搜索完成",
                    "photo_url": "",
                })
                with seeded._connection:
                    seeded._connection.execute(
                        "UPDATE operation_milestones SET delivery_state = ?, "
                        "delivery_started = ?, attempt_count = ?, last_error = ? "
                        "WHERE operation_id = ? AND milestone_id = ?",
                        (
                            state,
                            delivery_started,
                            attempt_count,
                            last_error,
                            "op-corrupt-provenance",
                            milestone_id,
                        ),
                    )
        finally:
            seeded.close()

        expected_states = {
            milestone_id: "unknown"
            for milestone_id, _state, _started, _attempt, _error in corruptions
        }

        def raw_states(coordinator):
            return {
                str(row["milestone_id"]): str(row["delivery_state"])
                for row in coordinator._connection.execute(
                    "SELECT milestone_id, delivery_state "
                    "FROM operation_milestones WHERE operation_id = ?",
                    ("op-corrupt-provenance",),
                ).fetchall()
            }

        first = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(first), expected_states)
        finally:
            first.close()

        second = InteractionCoordinator(corrupt_path)
        try:
            self.assertEqual(raw_states(second), expected_states)
            self.assertEqual(second.recover_milestones(), [])

            async def start_without_delivery():
                delivery = AsyncMock(return_value=True)
                sink = OperationMilestoneSink(second, delivery)
                await sink.start()
                await sink.drain()
                return delivery.await_count

            self.assertEqual(asyncio.run(start_without_delivery()), 0)
            self.assertEqual(raw_states(second), expected_states)
            current = second.get("op-corrupt-provenance")
            self.assertEqual(current.message_id, 41)
            self.assertEqual(current.message_kind, "text")
        finally:
            second.close()

    def test_pristine_pending_and_explicit_rejections_remain_recoverable(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        valid_path = Path(self.temp.name) / "valid-provenance-task4-host.db"
        seeded = InteractionCoordinator(valid_path)
        try:
            seeded.report("search", self.report(
                operation_id="op-valid-provenance",
                chat_id=43,
                user_id=9,
            ))
            seeded.enqueue_milestone("search", {
                "operation_id": "op-valid-provenance",
                "milestone_id": "pristine-pending",
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            })
            seeded.enqueue_milestone("search", {
                "operation_id": "op-valid-provenance",
                "milestone_id": "telegram-rejected",
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            })
            seeded.claim_milestone_delivery(
                "op-valid-provenance", "telegram-rejected"
            )
            seeded.reject_milestone_delivery(
                "search",
                "op-valid-provenance",
                "telegram-rejected",
                "telegram_rejected",
            )
            seeded.claim_milestone(
                "search", "op-valid-provenance", "legacy-unstarted"
            )
            seeded.release_milestone(
                "search", "op-valid-provenance", "legacy-unstarted"
            )
            seeded.enqueue_milestone("search", {
                "operation_id": "op-valid-provenance",
                "milestone_id": "legacy-started",
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            })
            seeded.claim_milestone_delivery(
                "op-valid-provenance", "legacy-started"
            )
            seeded.release_milestone(
                "search", "op-valid-provenance", "legacy-started"
            )
        finally:
            seeded.close()

        reopened = InteractionCoordinator(valid_path)
        try:
            recoverable = reopened.recover_milestones()
            self.assertEqual(
                {
                    item.milestone_id: (
                        item.delivery_state,
                        item.attempt_count,
                        item.last_error,
                    )
                    for item in recoverable
                },
                {
                    "pristine-pending": ("pending", 0, ""),
                    "telegram-rejected": (
                        "failed", 1, "telegram_rejected"
                    ),
                    "legacy-unstarted": (
                        "failed", 0, "explicit_rejection"
                    ),
                    "legacy-started": (
                        "failed", 1, "explicit_rejection"
                    ),
                },
            )
        finally:
            reopened.close()

    def test_positive_integer_expected_cursors_remain_recoverable(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        valid_path = Path(self.temp.name) / "valid-cursor-task4-host.db"
        seeded = InteractionCoordinator(valid_path)
        try:
            pending = seeded.report("search", self.report(
                operation_id="op-valid-pending",
                chat_id=50,
                user_id=5,
            ))
            seeded.set_message_id(pending.operation_id, 41, "text")
            seeded.enqueue_milestone("search", {
                "operation_id": pending.operation_id,
                "milestone_id": "valid-pending",
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            })

            targeted = seeded.report("search", self.report(
                operation_id="op-valid-targeted",
                chat_id=51,
                user_id=6,
            ))
            seeded.set_message_id(targeted.operation_id, 42, "photo")
            seeded.enqueue_milestone("search", {
                "operation_id": targeted.operation_id,
                "milestone_id": "valid-targeted",
                "mode": "stage",
                "text": "搜索完成",
                "photo_url": "",
            })
            seeded.claim_milestone_delivery(
                targeted.operation_id, "valid-targeted"
            )
            seeded.record_milestone_delivery_target(
                "search", targeted.operation_id, "valid-targeted", 90, "text"
            )
            with seeded._connection:
                seeded._connection.execute(
                    "UPDATE operation_milestones SET attempt_count = 2 "
                    "WHERE operation_id = ? AND milestone_id = ?",
                    (targeted.operation_id, "valid-targeted"),
                )
        finally:
            seeded.close()

        reopened = InteractionCoordinator(valid_path)
        try:
            self.assertEqual(
                reopened.get_milestone(
                    "op-valid-pending", "valid-pending"
                ).delivery_state,
                "pending",
            )
            self.assertEqual(
                reopened.get_milestone(
                    "op-valid-targeted", "valid-targeted"
                ).delivery_state,
                "delivering",
            )

            recoverable = reopened.recover_milestones()

            self.assertEqual(
                [(item.operation_id, item.milestone_id) for item in recoverable],
                [("op-valid-pending", "valid-pending")],
            )
            self.assertEqual(recoverable[0].attempt_count, 0)
            self.assertEqual(
                reopened.get_milestone(
                    "op-valid-targeted", "valid-targeted"
                ).delivery_state,
                "delivered",
            )
            self.assertIsNone(reopened.get("op-valid-targeted").message_id)
        finally:
            reopened.close()

    def test_only_one_non_terminal_operation_may_own_a_user(self):
        from app.runtime.interaction_coordinator import InteractionError

        self.coordinator.report("search", self.report())

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "download",
                self.report(operation_id="op-2", revision=1),
            )
        self.assertEqual(raised.exception.code, "operation_conflict")

    def test_owner_change_requires_matching_handoff(self):
        from app.runtime.interaction_coordinator import InteractionError

        self.coordinator.report("search", self.report())
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report("download", self.report(revision=2))
        self.assertEqual(raised.exception.code, "owner_mismatch")

        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="rename",
                revision=2,
            ),
        )
        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report("download", self.report(revision=3))
        self.assertEqual(raised.exception.code, "owner_mismatch")

    def test_handoff_changes_owner_without_releasing_gate(self):
        initial = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(initial.operation_id, 55, "text")
        handed_off = self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )
        self.assertEqual(self.coordinator.active(10, 1), handed_off)

        record = self.coordinator.report(
            "download",
            self.report(state="running", stage="download", revision=3),
        )
        self.assertEqual(record.plugin_id, "download")
        self.assertEqual(record.next_plugin_id, "")
        self.assertIsNone(record.message_id)
        self.assertEqual(self.coordinator.active(10, 1).operation_id, "op-1")

    def test_handoff_and_effect_receipts_follow_the_durable_feature_chain(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                stage="handoff_download",
                next_plugin_id="download",
                revision=2,
            ),
        )
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                stage="handoff_download",
                next_plugin_id="download",
                revision=2,
            ),
        )

        handoffs = self.coordinator.get_handoffs("op-1")
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0].handoff_key, "op-1:2:download")
        self.assertEqual(handoffs[0].state, "prepared")

        self.coordinator.report(
            "download",
            self.report(
                state="running",
                stage="download",
                revision=3,
                details={
                    "effect_receipt": {
                        "effect_key": "download.submit:job-1",
                        "state": "completed",
                        "receipt": {
                            "job_id": "job-1",
                            "access_token": "must-not-persist",
                        },
                    }
                },
            ),
        )
        self.assertEqual(
            self.coordinator.get_handoffs("op-1")[0].state,
            "accepted",
        )

        self.coordinator.report(
            "download",
            self.report(
                state="handed_off",
                stage="handoff_rename",
                next_plugin_id="rename",
                revision=4,
            ),
        )
        submitted = self.coordinator.record_handoff_event(
            "op-1", "event-1", "rename"
        )
        duplicate = self.coordinator.record_handoff_event(
            "op-1", "event-1", "rename"
        )
        self.assertEqual(submitted, duplicate)
        self.assertEqual(submitted.state, "submitted")
        self.assertEqual(submitted.event_id, "event-1")

        self.coordinator.report(
            "rename",
            self.report(state="running", stage="rename", revision=5),
        )
        self.coordinator.report(
            "rename",
            self.report(
                state="completed",
                stage="completed",
                status_text="整理完成",
                control="",
                revision=6,
                details={
                    "effect_receipt": {
                        "effect_key": "rename.organize:job-1",
                        "state": "completed",
                        "receipt": {
                            "final_path": "/TV/Show",
                            "source": "magnet:?xt=urn:btih:must-not-persist",
                        },
                    }
                },
            ),
        )

        handoffs = self.coordinator.get_handoffs("op-1")
        self.assertEqual([item.state for item in handoffs], ["accepted", "accepted"])
        effects = self.coordinator.get_effect_receipts("op-1")
        self.assertEqual(
            [item.effect_key for item in effects],
            ["download.submit:job-1", "rename.organize:job-1"],
        )
        self.assertEqual(effects[0].receipt["access_token"], "***redacted***")
        self.assertEqual(effects[1].receipt["source"], "magnet:?***redacted***")

        from app.runtime.interaction_coordinator import InteractionCoordinator

        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.database_path)
        self.assertEqual(
            [item.state for item in self.coordinator.get_handoffs("op-1")],
            ["accepted", "accepted"],
        )
        self.assertEqual(
            len(self.coordinator.get_effect_receipts("op-1")),
            2,
        )

    def test_effect_key_cannot_be_reused_by_another_operation(self):
        from app.runtime.interaction_coordinator import InteractionError

        effect = {
            "effect_receipt": {
                "effect_key": "download.submit:job-shared",
                "state": "completed",
                "receipt": {"job_id": "job-shared"},
            }
        }
        self.coordinator.report("download", self.report(details=effect))

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "download",
                self.report(
                    operation_id="op-2",
                    chat_id=20,
                    user_id=2,
                    details=effect,
                ),
            )

        self.assertEqual(raised.exception.code, "effect_conflict")
        self.assertIsNone(self.coordinator.get("op-2"))

    def test_effect_receipt_progresses_monotonically_with_an_identical_payload(self):
        receipt = {
            "effect_key": "download.submit:job-progress",
            "state": "prepared",
            "receipt": {"job_id": "job-progress", "provider_id": "provider-1"},
        }
        self.coordinator.report(
            "download",
            self.report(details={"effect_receipt": receipt}),
        )

        self.coordinator.report(
            "download",
            self.report(
                revision=2,
                details={
                    "effect_receipt": {
                        **receipt,
                        "state": "completed",
                    }
                },
            ),
        )
        completed = self.coordinator.get_effect_receipts("op-1")[0]
        self.assertEqual(completed.state, "completed")
        self.assertEqual(
            dict(completed.receipt),
            {"job_id": "job-progress", "provider_id": "provider-1"},
        )

        replay = self.coordinator.report(
            "download",
            self.report(
                revision=3,
                details={
                    "effect_receipt": {
                        **receipt,
                        "state": "completed",
                    }
                },
            ),
        )

        self.assertEqual(replay.revision, 3)
        self.assertEqual(
            self.coordinator.get_effect_receipts("op-1")[0],
            completed,
        )

    def test_effect_receipt_payload_conflict_rolls_back_the_report(self):
        from app.runtime.interaction_coordinator import InteractionError

        effect = {
            "effect_key": "download.submit:job-payload",
            "state": "prepared",
            "receipt": {"job_id": "job-payload", "provider_id": "provider-1"},
        }
        original = self.coordinator.report(
            "download",
            self.report(details={"effect_receipt": effect}),
        )

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "download",
                self.report(
                    revision=2,
                    details={
                        "effect_receipt": {
                            **effect,
                            "receipt": {
                                "job_id": "job-payload",
                                "provider_id": "provider-2",
                            },
                        }
                    },
                ),
            )

        self.assertEqual(raised.exception.code, "effect_conflict")
        self.assertEqual(self.coordinator.get("op-1"), original)
        self.assertEqual(
            self.coordinator.get_effect_receipts("op-1")[0].receipt["provider_id"],
            "provider-1",
        )

    def test_effect_receipt_cannot_change_feature_owner(self):
        from app.runtime.interaction_coordinator import InteractionError

        effect = {
            "effect_key": "download.submit:job-owner",
            "state": "prepared",
            "receipt": {"job_id": "job-owner"},
        }
        self.coordinator.report(
            "download",
            self.report(details={"effect_receipt": effect}),
        )
        handed_off = self.coordinator.report(
            "download",
            self.report(
                state="handed_off",
                next_plugin_id="rename",
                revision=2,
            ),
        )

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "rename",
                self.report(
                    revision=3,
                    details={
                        "effect_receipt": {
                            **effect,
                            "state": "completed",
                        }
                    },
                ),
            )

        self.assertEqual(raised.exception.code, "effect_conflict")
        self.assertEqual(self.coordinator.get("op-1"), handed_off)
        self.assertEqual(
            self.coordinator.get_handoffs("op-1")[0].state,
            "prepared",
        )

    def test_terminal_effect_receipt_rejects_a_later_state(self):
        from app.runtime.interaction_coordinator import InteractionError

        effect = {
            "effect_key": "download.submit:job-terminal",
            "state": "completed",
            "receipt": {"job_id": "job-terminal"},
        }
        original = self.coordinator.report(
            "download",
            self.report(details={"effect_receipt": effect}),
        )

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.report(
                "download",
                self.report(
                    revision=2,
                    details={
                        "effect_receipt": {
                            **effect,
                            "state": "failed",
                        }
                    },
                ),
            )

        self.assertEqual(raised.exception.code, "effect_conflict")
        self.assertEqual(self.coordinator.get("op-1"), original)
        self.assertEqual(
            self.coordinator.get_effect_receipts("op-1")[0].state,
            "completed",
        )

    def test_terminal_source_cancels_an_unaccepted_handoff_receipt(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )

        self.coordinator.report(
            "search",
            self.report(state="failed", control="", revision=3),
        )

        self.assertEqual(
            self.coordinator.get_handoffs("op-1")[0].state,
            "cancelled",
        )

    def test_event_target_pair_cannot_be_bound_to_another_handoff(self):
        from app.runtime.interaction_coordinator import InteractionError

        self.coordinator.report("download", self.report())
        self.coordinator.report(
            "download",
            self.report(
                state="handed_off",
                next_plugin_id="rename",
                revision=2,
            ),
        )
        first = self.coordinator.capture_handoff("op-1", "download")
        self.coordinator.record_handoff_event(
            "op-1",
            "shared-event",
            "rename",
            handoff_key=first.handoff_key,
        )

        self.coordinator.report(
            "download",
            self.report(
                operation_id="op-2",
                chat_id=20,
                user_id=2,
            ),
        )
        self.coordinator.report(
            "download",
            self.report(
                operation_id="op-2",
                chat_id=20,
                user_id=2,
                state="handed_off",
                next_plugin_id="rename",
                revision=2,
            ),
        )
        second = self.coordinator.capture_handoff("op-2", "download")

        with self.assertRaises(InteractionError) as raised:
            self.coordinator.record_handoff_event(
                "op-2",
                "shared-event",
                "rename",
                handoff_key=second.handoff_key,
            )

        self.assertEqual(raised.exception.code, "handoff_event_conflict")
        self.assertEqual(
            self.coordinator.get_handoffs("op-1")[0].state,
            "submitted",
        )
        unbound = self.coordinator.get_handoffs("op-2")[0]
        self.assertEqual(unbound.state, "prepared")
        self.assertEqual(unbound.event_id, "")

    def test_pre_task3_database_adds_empty_ledgers_without_rewriting_rows(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        legacy_path = Path(self.temp.name) / "pre-task3-host.db"
        connection = sqlite3.connect(legacy_path)
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
        """)
        connection.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op-legacy-task3",
                10,
                1,
                "search",
                "running",
                "planning",
                "规划中",
                "cancel",
                7,
                41,
                "photo",
                "",
                '{"provider":"legacy"}',
                11.0,
                12.0,
            ),
        )
        connection.execute(
            "INSERT INTO operation_milestones VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op-legacy-task3",
                "identity-legacy",
                "search",
                1,
                1,
                41,
                "photo",
                13.0,
            ),
        )
        connection.commit()
        connection.close()

        migrated = InteractionCoordinator(legacy_path)
        try:
            record = migrated.get("op-legacy-task3")
            milestone = migrated._connection.execute(
                "SELECT * FROM operation_milestones WHERE operation_id = ?",
                ("op-legacy-task3",),
            ).fetchone()

            self.assertEqual(record.plugin_id, "search")
            self.assertEqual(record.revision, 7)
            self.assertEqual(record.message_id, 41)
            self.assertEqual(record.message_kind, "photo")
            self.assertEqual(dict(record.details), {"provider": "legacy"})
            self.assertEqual(record.created_at, 11.0)
            self.assertEqual(record.updated_at, 12.0)
            self.assertEqual(milestone["milestone_id"], "identity-legacy")
            self.assertEqual(milestone["delivered_message_id"], 41)
            self.assertEqual(milestone["created_at"], 13.0)
            self.assertEqual(migrated.get_handoffs("op-legacy-task3"), [])
            self.assertEqual(migrated.get_effect_receipts("op-legacy-task3"), [])
        finally:
            migrated.close()

    def test_legacy_handed_off_operation_is_lazily_receipted_on_acceptance(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )
        with self.coordinator._connection:
            self.coordinator._connection.execute(
                "DELETE FROM operation_handoffs WHERE operation_id = ?",
                ("op-1",),
            )

        accepted = self.coordinator.report(
            "download",
            self.report(state="running", stage="download", revision=3),
        )

        self.assertEqual(accepted.plugin_id, "download")
        handoffs = self.coordinator.get_handoffs("op-1")
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0].handoff_key, "op-1:2:download")
        self.assertEqual(handoffs[0].state, "accepted")

    def test_legacy_handed_off_capture_lazily_materializes_prepared_receipt(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )
        with self.coordinator._connection:
            self.coordinator._connection.execute(
                "DELETE FROM operation_handoffs WHERE operation_id = ?",
                ("op-1",),
            )

        first = self.coordinator.capture_handoff("op-1", "search")
        second = self.coordinator.capture_handoff("op-1", "search")

        self.assertIsNotNone(first)
        self.assertEqual(first.handoff_key, "op-1:2:download")
        self.assertEqual(first.source_plugin_id, "search")
        self.assertEqual(first.source_revision, 2)
        self.assertEqual(first.target_plugin_id, "download")
        self.assertEqual(first.state, "prepared")
        self.assertEqual(first, second)
        self.assertEqual(len(self.coordinator.get_handoffs("op-1")), 1)

    def test_full_feature_handoff_chain_keeps_one_gate_until_rename_completes(self):
        chain = (
            ("search", "planning", "download"),
            ("download", "downloading", "rename"),
        )
        revision = 1
        self.coordinator.report(
            "search",
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
            "rename",
            self.report(
                state="completed",
                stage="completed",
                control="",
                revision=revision + 1,
            ),
        )
        self.assertEqual(completed.plugin_id, "rename")
        self.assertEqual(completed.state, "completed")
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_source_may_finish_failed_provisional_handoff_before_target_accepts(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )

        failed = self.coordinator.report(
            "search",
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
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(
                state="handed_off",
                next_plugin_id="download",
                revision=2,
            ),
        )

        cancelling = self.coordinator.report(
            "search",
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
        self.coordinator.report("search", self.report())
        terminal = self.coordinator.report(
            "search",
            self.report(state="cancelled", control="", revision=2),
        )

        late = self.coordinator.report(
            "search",
            self.report(
                state="running",
                stage="late-result",
                revision=99,
            ),
        )

        self.assertEqual(late, terminal)
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_late_revision_cannot_overwrite_cancelled_state(self):
        self.coordinator.report("search", self.report())
        current = self.coordinator.report(
            "search",
            self.report(revision=3, state="cancelled", control=""),
        )
        stale = self.coordinator.report(
            "search",
            self.report(revision=2, state="running"),
        )
        self.assertEqual(stale, current)
        self.assertIsNone(self.coordinator.active(10, 1))

    def test_message_id_and_record_survive_reload(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        created = self.coordinator.report("search", self.report())
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

    def test_clear_message_id_seals_current_message_segment(self):
        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 77, "photo")

        sealed = self.coordinator.clear_message_id(created.operation_id)

        self.assertIsNone(sealed.message_id)
        self.assertIsNone(self.coordinator.get("op-1").message_id)

    def test_interrupt_unowned_releases_only_missing_feature_operations(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(state="completed", control="", revision=2),
        )
        self.coordinator.report(
            "download",
            self.report(
                operation_id="op-2",
                chat_id=20,
                user_id=2,
                revision=1,
            ),
        )

        interrupted = self.coordinator.interrupt_unowned({"search"})

        self.assertEqual([record.operation_id for record in interrupted], ["op-2"])
        self.assertEqual(interrupted[0].state, "interrupted")
        self.assertEqual(interrupted[0].revision, 2)
        self.assertIsNone(self.coordinator.active(20, 2))

    def test_interrupt_unconfirmed_uses_operation_identity_not_only_plugin(self):
        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
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
        from app.runtime.interaction_coordinator import InteractionError

        cases = [
            ({"state": "pending"}, "invalid_state"),
            ({"control": "stop"}, "invalid_control"),
            ({"details": {"bad": object()}}, "invalid_details"),
            ({"details": {"effect_receipt": "bad"}}, "invalid_effect_receipt"),
            ({
                "details": {
                    "effect_receipt": {
                        "effect_key": "unsafe key",
                        "state": "unknown",
                        "receipt": [],
                    }
                }
            }, "invalid_effect_receipt"),
            ({"revision": 0}, "invalid_revision"),
            ({"chat_id": 0}, "invalid_owner"),
        ]
        for overrides, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(InteractionError) as raised:
                    self.coordinator.report("search", self.report(**overrides))
                self.assertEqual(raised.exception.code, code)

    def test_status_text_is_bounded(self):
        record = self.coordinator.report(
            "search",
            self.report(status_text="状" * 5000),
        )
        self.assertEqual(len(record.status_text), 4096)

    def test_sensitive_details_and_raw_magnets_are_redacted_before_storage(self):
        record = self.coordinator.report(
            "download",
            self.report(details={
                "access_token": "secret-value",
                "nested": {"source": "magnet:?xt=urn:btih:raw-secret"},
            }),
        )

        self.assertEqual(record.details["access_token"], "***redacted***")
        self.assertEqual(record.details["nested"]["source"], "magnet:?***redacted***")


if __name__ == "__main__":
    unittest.main()

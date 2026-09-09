import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.runtime.interaction_coordinator import InteractionCoordinator


class MessageCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "host.db"
        self.coordinator = InteractionCoordinator(self.path)
        self.application = SimpleNamespace(bot_data={
            "telepiplex_interaction_coordinator": self.coordinator,
        }, bot=SimpleNamespace(
            delete_message=AsyncMock(return_value=True),
            edit_message_reply_markup=AsyncMock(return_value=True),
        ))
        self.coordinator.report("search", self.report())

    async def asyncTearDown(self):
        self.coordinator.close()
        self.temp.cleanup()

    @staticmethod
    def report(**changes):
        return dict({"operation_id": "op", "chat_id": 10, "user_id": 1,
                     "state": "running", "stage": "search", "control": "cancel",
                     "revision": 1, "status_text": "Searching", "details": {}}, **changes)

    async def test_double_failure_survives_restart_and_only_acknowledged_removal_completes(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        queued = self.coordinator.queue_message_cleanup("op", 71, delete=True)
        self.application.bot.delete_message.side_effect = RuntimeError("cannot delete")
        self.application.bot.edit_message_reply_markup.side_effect = RuntimeError("cannot edit")
        self.assertFalse(await deliver_message_cleanup(self.application, queued))
        failed = self.coordinator.get_message_cleanup("op", 71)
        self.assertEqual(failed.state, "pending")
        self.assertEqual(failed.attempt_count, 1)
        self.assertGreater(failed.next_retry_at, queued.next_retry_at)
        self.assertIn("RuntimeError", failed.last_error)
        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.path)
        self.application.bot_data["telepiplex_interaction_coordinator"] = self.coordinator
        self.assertEqual(self.coordinator.get_message_cleanup("op", 71), failed)
        self.application.bot.edit_message_reply_markup.side_effect = None
        self.assertTrue(await deliver_message_cleanup(self.application, failed))
        self.assertEqual(self.coordinator.get_message_cleanup("op", 71).state, "completed")
        self.assertEqual(self.coordinator.pending_message_cleanups(now=failed.next_retry_at + 100), [])

    async def test_current_interactive_message_is_never_erased_by_old_cleanup(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        self.coordinator.set_message_id("op", 72, "text")
        queued = self.coordinator.queue_message_cleanup("op", 72, delete=True)
        self.assertFalse(await deliver_message_cleanup(self.application, queued))
        self.application.bot.delete_message.assert_not_awaited()
        self.application.bot.edit_message_reply_markup.assert_not_awaited()
        self.assertEqual(self.coordinator.get_message_cleanup("op", 72).state, "pending")

    async def test_old_operation_cleanup_cannot_erase_a_new_operation_cursor(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        from app.runtime.interaction_coordinator import InteractionError
        self.coordinator.set_message_id("op", 79, "text")
        self.coordinator.report("search", self.report(state="completed", control="", revision=2))
        queued = self.coordinator.get_message_cleanup("op", 79)
        self.coordinator.report("search", self.report(operation_id="new-op"))
        with self.assertRaises(InteractionError):
            self.coordinator.set_message_id("new-op", 79, "text")
        self.assertTrue(await deliver_message_cleanup(self.application, queued))

    async def test_cleanup_inflight_prevents_rebinding_history_to_another_operation(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        from app.runtime.interaction_coordinator import InteractionError
        queued = self.coordinator.queue_message_cleanup("op", 84, delete=True)
        entered, released = asyncio.Event(), asyncio.Event()
        async def blocked(**_kwargs):
            entered.set()
            await released.wait()
            return True
        self.application.bot.delete_message.side_effect = blocked
        pending = asyncio.create_task(deliver_message_cleanup(self.application, queued))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            self.coordinator.report("search", self.report(operation_id="other", user_id=2))
            with self.assertRaises(InteractionError):
                self.coordinator.set_message_id("other", 84, "text")
        finally:
            released.set()
            await pending

    async def test_direct_cleanup_of_migrated_foreign_history_is_quarantined(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        self.coordinator.set_message_id("op", 85, "text")
        self.coordinator.report("search", self.report(state="completed", control="", revision=2))
        queued = self.coordinator.get_message_cleanup("op", 85)
        self.coordinator.report("search", self.report(operation_id="new", user_id=2))
        self.coordinator._connection.execute(
            "UPDATE operations SET message_id=85, message_kind='text' WHERE operation_id='new'")
        self.coordinator._connection.execute("DROP TABLE operation_message_ownership")
        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.path)
        self.application.bot_data["telepiplex_interaction_coordinator"] = self.coordinator
        self.assertFalse(await deliver_message_cleanup(self.application, queued))
        self.application.bot.delete_message.assert_not_awaited()
        self.application.bot.edit_message_reply_markup.assert_not_awaited()
        self.assertEqual(self.coordinator.get_message_cleanup("op", 85).state, "pending")

    async def test_not_editable_is_retained_but_missing_and_unchanged_are_acknowledged(self):
        from telegram.error import BadRequest
        from app.runtime.message_cleanup import deliver_message_cleanup
        for message_id, error, success in (
            (73, "Message can't be edited", False),
            (74, "Message to edit not found", True),
            (75, "Message is not modified", True),
        ):
            queued = self.coordinator.queue_message_cleanup("op", message_id)
            self.application.bot.edit_message_reply_markup.side_effect = BadRequest(error)
            self.assertEqual(await deliver_message_cleanup(self.application, queued), success)
            self.assertEqual(self.coordinator.get_message_cleanup("op", message_id).state,
                             "completed" if success else "pending")

    async def test_worker_honors_durable_backoff_and_cancellation_keeps_intent(self):
        from app.runtime.message_cleanup import MessageCleanupWorker
        queued = self.coordinator.queue_message_cleanup("op", 76)
        self.coordinator.fail_message_cleanup("op", 76, error="network", now=100)
        worker = MessageCleanupWorker(self.application, self.coordinator)
        self.assertEqual(await worker.run_once(now=100), 0)
        self.assertEqual(await worker.run_once(now=1000), 1)
        queued = self.coordinator.queue_message_cleanup("op", 77)
        entered = asyncio.Event()
        async def blocked(**_kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.application.bot.edit_message_reply_markup.side_effect = blocked
        worker.start()
        await asyncio.wait_for(entered.wait(), 1)
        await worker.close(timeout=0.1)
        self.assertEqual(self.coordinator.get_message_cleanup("op", 77).state, "pending")

    async def test_cleanup_waits_for_operation_writer_and_rechecks_new_cursor(self):
        from app.handlers.interaction_handler import operation_render_lock
        from app.runtime.message_cleanup import deliver_message_cleanup
        queued = self.coordinator.queue_message_cleanup("op", 81)
        lock = operation_render_lock(self.application, "op")
        async with lock:
            pending = asyncio.create_task(deliver_message_cleanup(self.application, queued))
            await asyncio.sleep(0)
            self.application.bot.edit_message_reply_markup.assert_not_awaited()
            self.coordinator.set_message_id("op", 81, "text")
        self.assertFalse(await pending)
        self.application.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_unacknowledged_api_return_does_not_complete_cleanup(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        queued = self.coordinator.queue_message_cleanup("op", 82)
        self.application.bot.edit_message_reply_markup.return_value = False
        self.assertFalse(await deliver_message_cleanup(self.application, queued))
        self.assertEqual(self.coordinator.get_message_cleanup("op", 82).state, "pending")

    async def test_native_active_cursor_survives_but_sealing_cursor_can_be_cleaned(self):
        from app.runtime.message_cleanup import deliver_message_cleanup
        _, segment = self.coordinator.accept_segment_report("search", self.report(
            revision=2, segment={"role": "search", "presentation_kind": "text"}))
        self.coordinator.bind_segment_message(segment.segment_id, owner_plugin_id="search",
            generation=segment.generation, chat_id=10, message_id=83)
        queued = self.coordinator.queue_message_cleanup("op", 83)
        self.assertFalse(await deliver_message_cleanup(self.application, queued))
        self.coordinator.seal_segment("search", "op", "search")
        self.assertTrue(await deliver_message_cleanup(self.application, queued))

    async def test_requeued_intent_cannot_be_acknowledged_by_previous_delivery(self):
        first = self.coordinator.queue_message_cleanup("op", 78)
        self.coordinator.ack_message_cleanup("op", 78, version=first.version)
        second = self.coordinator.queue_message_cleanup("op", 78)
        self.assertGreater(second.version, first.version)
        self.assertFalse(self.coordinator.ack_message_cleanup("op", 78, version=first.version))

    async def test_restart_discovers_known_terminal_message_without_reopening_completed_cleanup(self):
        self.coordinator.set_message_id("op", 80, "text")
        self.coordinator.report("search", self.report(state="completed", control="", revision=2))
        self.coordinator._connection.execute("DELETE FROM operation_message_cleanups")
        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.path)
        queued = self.coordinator.get_message_cleanup("op", 80)
        self.assertIsNotNone(queued)
        self.coordinator.ack_message_cleanup("op", 80)
        self.coordinator.close()
        self.coordinator = InteractionCoordinator(self.path)
        self.assertEqual(self.coordinator.get_message_cleanup("op", 80).state, "completed")

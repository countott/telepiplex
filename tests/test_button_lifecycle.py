import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import test_interaction_handler as fixture_module


class ButtonLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.InteractionHandlerTest()
        await self.fixture.asyncSetUp()
        self.coordinator = self.fixture.coordinator
        self.route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
            client=SimpleNamespace(request=AsyncMock(return_value={})),
        )
        self.router = SimpleNamespace(
            plugin_route=lambda _plugin: self.route,
            callback_route=lambda _namespace: self.route,
        )
        self.context = self.fixture.context(router=self.router)

    async def asyncTearDown(self):
        from app.handlers.interaction_handler import drain_callback_feedback

        await drain_callback_feedback(self.context.application, timeout=0.1)
        await self.fixture.asyncTearDown()

    def native(self, *, kind="text", revision=1, **overrides):
        report = self.fixture.report(
            state="awaiting_input", stage="candidate_selection",
            status_text="请选择作品", control="cancel", revision=revision,
            segment={"role": "identity", "presentation_kind": kind},
            details={"keyboard": [[{
                "text": "作品", "callback_data": "search:select:p1:0",
            }]]},
        )
        report.update(overrides)
        record, segment = self.coordinator.accept_segment_report("search", report)
        if segment.message_id is None:
            segment = self.coordinator.bind_segment_message(
                segment.segment_id, owner_plugin_id="search",
                generation=segment.generation, chat_id=10,
                message_id=92, message_kind=kind,
            )
        return record, segment

    async def test_historical_message_host_control_cannot_dispatch(self):
        from app.handlers.interaction_handler import operation_control_callback, operation_markup

        record, segment = self.native()
        markup = operation_markup(record, self.router, segment=segment)
        callback = markup.inline_keyboard[-1][0].callback_data
        update = self.fixture.callback_update(callback, message_id=11)
        with patch("app.handlers.plugin_handler.handle_feature_result", new=AsyncMock()):
            await operation_control_callback(update, self.context)
        self.route.client.request.assert_not_awaited()

    async def test_current_host_control_consumes_exact_keyboard_generation(self):
        from app.handlers.interaction_handler import operation_control_callback, operation_markup

        record, segment = self.native()
        callback = operation_markup(record, self.router, segment=segment).inline_keyboard[-1][0].callback_data
        self.assertNotEqual(callback, "host-operation:cancel:op-1")
        update = self.fixture.callback_update(callback, message_id=92)
        with patch("app.handlers.plugin_handler.handle_feature_result", new=AsyncMock()):
            await operation_control_callback(update, self.context)
            await operation_control_callback(update, self.context)
        self.route.client.request.assert_awaited_once()

    async def test_busy_writer_serializes_with_latest_projection(self):
        from app.handlers.interaction_handler import render_operation, schedule_callback_feedback

        for kind in ("text", "photo"):
            with self.subTest(kind=kind):
                # Each subcase uses a separate coordinator fixture.
                if kind == "photo":
                    await self.fixture.asyncTearDown()
                    await self.fixture.asyncSetUp()
                    self.coordinator = self.fixture.coordinator
                    self.context = self.fixture.context(router=self.router)
                record, segment = self.native(kind=kind)
                self.coordinator.record_segment_rendered(
                    segment.segment_id, owner_plugin_id="search",
                    generation=segment.generation, business_revision=1,
                    projection_hash=segment.projection_hash,
                )
                claimed = self.coordinator.claim_segment_callback(
                    "search", "op-1", message_id=92,
                    segment_generation=segment.generation, callback_generation=1,
                    callback_token="search:select:p1:0",
                    busy_text="正在确认媒体身份…",
                )
                started, release = asyncio.Event(), asyncio.Event()
                visible = []

                async def edit(**kwargs):
                    text = kwargs.get("text", kwargs.get("caption"))
                    if text == "正在确认媒体身份…":
                        started.set()
                        await release.wait()
                    visible.append(text)

                self.context.bot.edit_message_text.side_effect = edit
                self.context.bot.edit_message_caption.side_effect = edit
                task = schedule_callback_feedback(
                    self.fixture.callback_update("unused", message_id=92),
                    self.context.application, record, claimed,
                )
                await asyncio.wait_for(started.wait(), timeout=1)
                self.coordinator.release_segment_callback(
                    "search", "op-1", message_id=92,
                    segment_generation=claimed.generation,
                    callback_generation=claimed.callback_generation,
                    callback_token="search:select:p1:0",
                )
                pending = asyncio.create_task(render_operation(
                    self.context.application, self.router,
                    self.coordinator.get("op-1"),
                ))
                await asyncio.sleep(0)
                serialized = not pending.done()
                release.set()
                await asyncio.wait_for(asyncio.gather(task, pending), timeout=1)
                self.assertTrue(serialized, "busy and business projections used concurrent writers")
                self.assertEqual(visible[-1], "请选择作品")

    async def test_obsolete_busy_feedback_does_not_edit_released_keyboard(self):
        from app.handlers.interaction_handler import schedule_callback_feedback

        record, segment = self.native()
        claimed = self.coordinator.claim_segment_callback(
            "search", "op-1", message_id=92, segment_generation=segment.generation,
            callback_generation=1, callback_token="search:select:p1:0",
        )
        self.coordinator.release_segment_callback(
            "search", "op-1", message_id=92, segment_generation=segment.generation,
            callback_generation=claimed.callback_generation, callback_token="search:select:p1:0",
        )
        task = schedule_callback_feedback(
            self.fixture.callback_update("unused", message_id=92),
            self.context.application, record, claimed,
        )
        await task
        texts = [call.kwargs["text"] for call in self.context.bot.edit_message_text.await_args_list]
        self.assertNotIn("正在确认媒体身份…", texts)

    def test_processing_projection_never_reuses_awaiting_choice_buttons(self):
        from app.handlers.interaction_handler import operation_markup

        record, segment = self.native(state="running", stage="identity_confirmation")
        markup = operation_markup(record, self.router, segment=segment)
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertFalse(any("search:select:" in callback for callback in callbacks))

    async def test_callback_result_is_accepted_before_claim_release(self):
        from app.handlers.interaction_handler import operation_gate, operation_markup
        from app.handlers.plugin_handler import dynamic_callback_gateway

        record, segment = self.native()
        data = operation_markup(record, self.router, segment=segment).inline_keyboard[0][0].callback_data
        update = self.fixture.callback_update(data, message_id=92)
        accepted_while_busy = []

        async def accept(*_args):
            accepted_while_busy.append(self.coordinator.get_active_segment("op-1").callback_state)

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True), patch(
            "app.handlers.plugin_handler.handle_feature_result", side_effect=accept,
        ):
            await operation_gate(update, self.context)
            await dynamic_callback_gateway(update, self.context)
        self.assertEqual(accepted_while_busy, ["busy"])

    async def test_old_generation_repairs_current_card_without_removing_new_choices(self):
        from app.handlers.interaction_handler import operation_gate, operation_markup
        from telegram.ext import ApplicationHandlerStop

        record, segment = self.native()
        self.coordinator.record_segment_rendered(
            segment.segment_id, owner_plugin_id="search", generation=segment.generation,
            business_revision=1, projection_hash=segment.projection_hash,
        )
        update = self.fixture.callback_update("~1.ff~search:select:p1:0", message_id=92)
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context)
        self.context.bot.edit_message_text.assert_awaited_once()
        markup = self.context.bot.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            markup.to_dict(), operation_markup(record, self.router, segment=segment).to_dict(),
        )
        self.context.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_terminal_old_feature_callback_is_answered_and_cleans_its_own_message(self):
        from app.handlers.interaction_handler import operation_gate
        from telegram.ext import ApplicationHandlerStop

        record, segment = self.native()
        self.native(revision=2, state="completed", stage="completed", control="", details={})
        update = self.fixture.callback_update("~1.1~search:select:p1:0", message_id=92)
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context)
        update.callback_query.answer.assert_awaited_once()
        self.context.bot.edit_message_reply_markup.assert_awaited_once()
        self.assertEqual(self.context.bot.edit_message_reply_markup.await_args.kwargs["message_id"], 92)
        self.route.client.request.assert_not_awaited()

    async def test_replaced_message_double_failure_stays_pending_until_retry(self):
        from app.handlers.interaction_handler import _discard_replaced_segment_message
        from app.runtime.message_cleanup import deliver_message_cleanup

        record, segment = self.native()
        claimed = self.coordinator.claim_segment_replacement_delivery(
            segment.segment_id, owner_plugin_id="search", generation=segment.generation,
            chat_id=10, expected_message_id=92, expected_message_kind="text",
        )
        self.assertIsNotNone(claimed)
        self.coordinator.replace_segment_message(
            segment.segment_id, owner_plugin_id="search", generation=segment.generation,
            chat_id=10, expected_message_id=92, expected_message_kind="text",
            message_id=93, message_kind="photo",
        )
        self.context.bot.delete_message.side_effect = RuntimeError("delete failed")
        self.context.bot.edit_message_reply_markup.side_effect = RuntimeError("clear failed")
        with patch("app.handlers.interaction_handler._log") as log:
            await _discard_replaced_segment_message(
                self.context.application, 10, 92, operation_id="op-1",
            )
        pending = self.coordinator.get_message_cleanup("op-1", 92)
        self.assertEqual(pending.state, "pending")
        self.assertGreater(pending.attempt_count, 0)
        self.assertFalse(any("已清理其按钮" in str(call) for call in log.call_args_list))
        self.context.bot.delete_message.side_effect = None
        self.context.bot.delete_message.return_value = True
        self.assertTrue(await deliver_message_cleanup(self.context.application, pending))
        self.assertEqual(self.coordinator.get_message_cleanup("op-1", 92).state, "completed")

    async def test_cancelled_host_control_releases_claim_after_render_lock(self):
        from app.handlers.interaction_handler import operation_control_callback, operation_markup, operation_render_lock

        record, segment = self.native()
        data = operation_markup(record, self.router, segment=segment).inline_keyboard[-1][0].callback_data
        dispatched = asyncio.Event()

        async def dispatch(*_args, **_kwargs):
            dispatched.set()
            return {}

        self.route.client.request.side_effect = dispatch
        lock = operation_render_lock(self.context.application, "op-1")
        await lock.acquire()
        with patch("app.handlers.plugin_handler.handle_feature_result", new=AsyncMock()):
            task = asyncio.create_task(operation_control_callback(
                self.fixture.callback_update(data, message_id=92), self.context,
            ))
            await asyncio.wait_for(dispatched.wait(), timeout=1)
            task.cancel()
            await asyncio.sleep(0)
            lock.release()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        self.assertEqual(self.coordinator.get_active_segment("op-1").callback_state, "idle")

    def test_long_operation_id_retains_versioned_host_control(self):
        from app.handlers.interaction_handler import operation_markup, _CONTROL_RE

        record, segment = self.native(operation_id="a" * 40, control="rollback", details={})
        markup = operation_markup(record, self.router, segment=segment)
        self.assertIsNotNone(markup)
        data = markup.inline_keyboard[-1][0].callback_data
        self.assertLessEqual(len(data.encode()), 64)
        self.assertIsNotNone(_CONTROL_RE.fullmatch(data))

    async def test_initial_send_bind_failure_cleans_known_orphan(self):
        from app.handlers.interaction_handler import render_operation

        record, segment = self.coordinator.accept_segment_report("search", self.fixture.report(
            state="awaiting_input", stage="candidate_selection",
            segment={"role": "identity", "presentation_kind": "text"},
            details={"keyboard": [[{"text": "作品", "callback_data": "search:select:p1:0"}]]},
        ))
        self.context.bot.send_message.return_value = SimpleNamespace(message_id=92)
        self.context.bot.delete_message.return_value = True
        with patch.object(self.coordinator, "bind_segment_message", return_value=None):
            await render_operation(self.context.application, self.router, record)
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=92)
        self.assertEqual(self.coordinator.get_message_cleanup("op-1", 92).state, "completed")

    async def test_terminal_projection_acknowledges_its_cleanup_without_second_api_call(self):
        from app.handlers.interaction_handler import render_operation
        from app.runtime.message_cleanup import MessageCleanupWorker

        self.native()
        record, _segment = self.native(revision=2, state="completed", stage="completed", control="", details={})
        await render_operation(self.context.application, self.router, record)
        pending = self.coordinator.get_message_cleanup("op-1", 92)
        self.assertEqual(pending.state, "completed")
        worker = MessageCleanupWorker(self.context.application, self.coordinator)
        self.assertEqual(await worker.run_once(now=pending.next_retry_at + 100), 0)
        self.context.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_restart_snapshot_releases_only_orphaned_claim_and_renders_current_choices(self):
        from pathlib import Path
        from app.runtime.interaction_coordinator import InteractionCoordinator
        from app.handlers.interaction_handler import recover_active_operations

        record, segment = self.native()
        self.coordinator.claim_segment_callback(
            "search", "op-1", message_id=92, segment_generation=segment.generation,
            callback_generation=segment.callback_generation, callback_token="search:select:p1:0",
        )
        self.coordinator.close()
        self.coordinator = self.fixture.coordinator = InteractionCoordinator(Path(self.fixture.temp.name) / "host.db")
        self.context = self.fixture.context(router=self.router)
        self.route.client.request.return_value = {"operations": [self.fixture.report(
            state="awaiting_input", stage="candidate_selection", status_text="请选择恢复后的作品",
            revision=2, segment={"role": "identity", "presentation_kind": "text"},
            details=dict(record.details),
        )]}
        await recover_active_operations(self.context.application, self.router, self.coordinator)
        self.assertEqual(self.coordinator.get_active_segment("op-1").callback_state, "idle")
        edit = self.context.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edit["text"], "请选择恢复后的作品")
        self.assertTrue(edit["reply_markup"].inline_keyboard)

    async def test_migrated_nonowner_cursor_cannot_overwrite_current_message(self):
        from app.handlers.interaction_handler import render_operation

        record, _segment = self.native()
        with patch.object(self.coordinator, "owns_message", return_value=False):
            await render_operation(self.context.application, self.router, record)
        self.context.bot.edit_message_text.assert_not_awaited()
        self.context.bot.edit_message_caption.assert_not_awaited()
        self.context.bot.edit_message_reply_markup.assert_not_awaited()

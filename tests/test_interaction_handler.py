import asyncio
import json
import logging
import tempfile
import time
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest, TimedOut
from telegram.ext import ApplicationHandlerStop


class InteractionHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.runtime.interaction_coordinator import InteractionCoordinator

        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = InteractionCoordinator(Path(self.temp.name) / "host.db")

    async def asyncTearDown(self):
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
        }
        report.update(overrides)
        return report

    def context(self, *, router=None):
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=90)),
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=91)),
            answer_callback_query=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        application = SimpleNamespace(
            bot=bot,
            bot_data={
                "telepiplex_interaction_coordinator": self.coordinator,
                "telepiplex_plugin_router": router or Mock(),
            },
        )
        return SimpleNamespace(application=application, bot=bot)

    def test_operation_render_lock_registry_drops_unused_operations(self):
        from app.handlers.interaction_handler import (
            OPERATION_RENDER_LOCKS_KEY,
            operation_render_lock,
        )

        application = SimpleNamespace(bot_data={})
        first = operation_render_lock(application, "op-finished")
        second = operation_render_lock(application, "op-finished")
        self.assertIs(first, second)
        self.assertIn(
            "op-finished",
            application.bot_data[OPERATION_RENDER_LOCKS_KEY],
        )

        del first
        del second

        self.assertNotIn(
            "op-finished",
            application.bot_data[OPERATION_RENDER_LOCKS_KEY],
        )

    async def test_callback_feedback_drain_waits_for_inflight_delivery(self):
        from app.handlers.interaction_handler import (
            drain_callback_feedback,
            schedule_callback_feedback,
        )

        record, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                segment={
                    "role": "identity",
                    "presentation_kind": "text",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=55,
            message_kind="text",
        )
        context = self.context()
        delivery_started = asyncio.Event()
        release_delivery = asyncio.Event()

        async def blocked_edit(**_kwargs):
            delivery_started.set()
            await release_delivery.wait()

        context.application.bot.edit_message_text.side_effect = blocked_edit
        update = SimpleNamespace(
            callback_query=SimpleNamespace(answer=AsyncMock()),
        )
        schedule_callback_feedback(
            update,
            context.application,
            record,
            segment,
        )
        await asyncio.wait_for(delivery_started.wait(), timeout=0.1)

        draining = asyncio.create_task(
            drain_callback_feedback(context.application, timeout=0.2)
        )
        await asyncio.sleep(0)
        self.assertFalse(draining.done())
        release_delivery.set()

        self.assertTrue(await draining)

    @staticmethod
    def message_update(text: str):
        return SimpleNamespace(
            update_id=10,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(
                text=text,
                reply_text=AsyncMock(),
            ),
            callback_query=None,
        )

    @staticmethod
    def callback_update(data: str, *, message_id: int = 55):
        query = SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(message_id=message_id),
        )
        return SimpleNamespace(
            update_id=11,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=query,
        )

    async def test_running_operation_drops_unrelated_command(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report("search", self.report())
        router = Mock()
        context = self.context(router=router)

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(self.message_update("/search test"), context)

        router.command_route.assert_not_called()

    async def test_operation_gate_records_the_actual_incoming_command_before_routing(self):
        from app.handlers import interaction_handler
        from app.handlers.interaction_handler import operation_gate
        from app.utils.logger import Logger
        from telepiplex_plugin_sdk.diagnostics import set_diagnostic_context

        logger = Logger(
            config_root=Path(self.temp.name) / "diagnostics",
            session_id="INCOMING-COMMAND",
        )
        update = self.message_update(
            "/search 蜂蜜与四叶草 access_token=command-secret"
        )
        set_diagnostic_context(operation_id="stale-operation")
        try:
            with patch.object(interaction_handler.init, "logger", logger):
                await operation_gate(update, self.context())
        finally:
            set_diagnostic_context(operation_id=None)
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, "_telepiplex_handler_kind", ""):
                    logging.getLogger().removeHandler(handler)
                    handler.close()

        event = json.loads(logger.session.machine_path.read_text(encoding="utf-8"))
        assert event["event"]["name"] == "telegram.interaction.received"
        assert event["identity"]["operation_id"] is None
        assert event["facts"]["user_surface"] == {
            "direction": "incoming",
            "kind": "command",
            "text": "/search 蜂蜜与四叶草 access_token=***redacted***",
            "callback_data": None,
        }
        human = logger.session.human_path.read_text(encoding="utf-8")
        assert "收到指令：/search 蜂蜜与四叶草 access_token=***redacted***" in human
        assert "command-secret" not in human

    async def test_operation_gate_records_the_actual_incoming_callback(self):
        from app.handlers import interaction_handler
        from app.handlers.interaction_handler import operation_gate
        from app.utils.logger import Logger

        logger = Logger(
            config_root=Path(self.temp.name) / "diagnostics-callback",
            session_id="INCOMING-CALLBACK",
        )
        try:
            with patch.object(interaction_handler.init, "logger", logger):
                await operation_gate(
                    self.callback_update("search:select:p1:0"),
                    self.context(),
                )
        finally:
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, "_telepiplex_handler_kind", ""):
                    logging.getLogger().removeHandler(handler)
                    handler.close()

        event = json.loads(logger.session.machine_path.read_text(encoding="utf-8"))
        assert event["facts"]["user_surface"]["kind"] == "callback"
        assert event["facts"]["user_surface"]["callback_data"] == "search:select:p1:0"
        assert "收到回调：search:select:p1:0" in logger.session.human_path.read_text(
            encoding="utf-8"
        )

    def test_terminal_control_dedup_keeps_navigation_duplicates(self):
        from app.handlers.interaction_handler import operation_markup

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            control="",
            details={"keyboard": [[
                {
                    "text": "上一项",
                    "callback_data": "search:browse:p1:1",
                },
                {
                    "text": "下一项",
                    "callback_data": "search:browse:p1:1",
                },
            ], [
                {
                    "text": "取消",
                    "callback_data": "search:cancel:p1",
                },
                {
                    "text": "退出",
                    "callback_data": "search:cancel:p1",
                },
            ]]},
        ))
        router = Mock()
        router.plugin_route.return_value = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )

        result = operation_markup(record, router).inline_keyboard

        self.assertEqual(
            [button.text for button in result[0]],
            ["上一项", "下一项"],
        )
        self.assertEqual(
            [button.text for button in result[1]],
            ["取消"],
        )

    async def test_operation_sink_rejects_same_revision_terminal_mismatch(self):
        from app.handlers.interaction_handler import OperationReportSink

        sink = OperationReportSink(self.coordinator)
        self.assertTrue((await sink("search", self.report()))["accepted"])
        terminal = self.report(
            state="cancelled",
            stage="cancelled",
            status_text="已取消",
            control="",
            revision=2,
        )
        self.assertTrue((await sink("search", terminal))["accepted"])

        stale = self.report(
            state="running",
            stage="downloading",
            status_text="仍在下载",
            control="cancel",
            revision=2,
        )
        response = await sink("search", stale)

        self.assertFalse(response["accepted"])
        self.assertEqual(response["state"], "cancelled")

    async def test_operation_sink_rejects_handoff_to_inactive_feature_without_mutation(self):
        from app.handlers.interaction_handler import OperationReportSink

        router = Mock()
        router.plugin_route.return_value = None
        sink = OperationReportSink(self.coordinator, router=router)
        self.assertTrue((await sink("rename", self.report()))["accepted"])

        response = await sink("rename", self.report(
            state="handed_off",
            stage="handoff_plex",
            status_text="已交给 Plex",
            control="cancel",
            revision=2,
            next_plugin_id="sync",
        ))

        self.assertFalse(response["accepted"])
        self.assertEqual(
            response["error_code"], "handoff_target_unavailable"
        )
        self.assertEqual(response["target_plugin_id"], "sync")
        current = self.coordinator.get("op-1")
        self.assertEqual((current.plugin_id, current.state, current.revision), (
            "rename", "running", 1,
        ))

    async def test_operation_sink_ignores_nonnewer_handoff_before_target_check(self):
        from app.handlers.interaction_handler import OperationReportSink

        router = Mock()
        router.plugin_route.return_value = None
        sink = OperationReportSink(self.coordinator, router=router)
        current = self.coordinator.report(
            "search",
            self.report(
                revision=2,
                stage="prowlarr",
                status_text="正在搜索片源",
            ),
        )

        for revision in (1, 2):
            with self.subTest(revision=revision):
                response = await sink(
                    "search",
                    self.report(
                        revision=revision,
                        state="handed_off",
                        stage="handoff_download",
                        next_plugin_id="download",
                    ),
                )
                self.assertFalse(response["accepted"])
                self.assertEqual(response["state"], current.state)
                self.assertEqual(response["revision"], current.revision)
                self.assertNotIn("error_code", response)

        router.plugin_route.assert_not_called()

    async def test_operation_sink_handoff_returns_before_attached_renderer(self):
        from app.handlers.interaction_handler import OperationReportSink

        release = asyncio.Event()
        started = asyncio.Event()

        async def render(_record):
            started.set()
            await release.wait()

        sink = OperationReportSink(self.coordinator)
        sink.attach(render)
        response = await asyncio.wait_for(sink("download", self.report(
            state="handed_off",
            stage="handoff_rename",
            status_text="✅ 115 下载完成\n保存目录：/真人剧集/Veep.S01",
            next_plugin_id="rename",
        )), timeout=0.1)

        await started.wait()
        self.assertTrue(response["accepted"])
        release.set()
        await sink.drain()

    async def test_operation_sink_coalesces_first_inflight_and_latest_pending(self):
        from app.handlers.interaction_handler import OperationReportSink

        release = asyncio.Event()
        started = asyncio.Event()
        revisions = []

        async def render(record):
            revisions.append(record.revision)
            if record.revision == 1:
                started.set()
                await release.wait()

        sink = OperationReportSink(self.coordinator)
        sink.attach(render)
        self.assertTrue((await sink("search", self.report()))["accepted"])
        await started.wait()

        for revision in range(2, 52):
            response = await sink("search", self.report(
                revision=revision,
                status_text=f"进度 {revision}",
            ))
            self.assertTrue(response["accepted"])

        self.assertEqual(self.coordinator.get("op-1").revision, 51)
        release.set()
        await sink.drain()
        self.assertEqual(revisions, [1, 51])
        self.assertEqual(sink._pending, {})
        self.assertEqual(sink._workers, {})

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_segment_defers_photo_until_candidate_media(
        self,
        build_poster_grid,
    ):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        candidate_grid = BytesIO(b"candidate-grid")
        candidate_grid.name = "telepiplex-candidates.jpg"
        build_poster_grid.return_value = candidate_grid
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)
        events = []

        async def send_photo(**kwargs):
            events.append(("send_photo", kwargs.get("reply_markup")))
            return SimpleNamespace(message_id=91)

        async def delete_message(**_kwargs):
            events.append(("delete_old", None))

        async def attach_keyboard(**kwargs):
            events.append(("attach_keyboard", kwargs.get("reply_markup")))
            raise RuntimeError("telegram keyboard edit timed out")

        async def retry_photo(**kwargs):
            events.append(("retry_photo", kwargs.get("reply_markup")))

        context.application.bot.send_photo.side_effect = send_photo
        context.application.bot.delete_message.side_effect = delete_message
        context.application.bot.edit_message_reply_markup.side_effect = (
            attach_keyboard
        )
        context.application.bot.edit_message_media.side_effect = retry_photo
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            router,
            record,
        ))

        response = await sink(
            "search",
            self.report(
                status_text="正在识别媒体…",
                details={"defer_photo_until_media": True},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        await sink.drain()

        self.assertTrue(response["accepted"])
        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.send_photo.assert_not_awaited()
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.message_id, 90)
        self.assertEqual(segment.message_kind, "text")
        self.assertEqual(segment.rendered_revision, 1)

        original_replace = self.coordinator.replace_segment_message

        def replace_message(*args, **kwargs):
            events.append(("replace_cursor", None))
            return original_replace(*args, **kwargs)

        with patch.object(
            self.coordinator,
            "replace_segment_message",
            side_effect=replace_message,
        ):
            await sink(
                "search",
                self.report(
                    revision=2,
                    state="awaiting_input",
                    stage="candidate_selection",
                    status_text="请选择作品",
                    control="exit",
                    details={
                        "defer_photo_until_media": True,
                        "poster_items": [{
                            "number": 1,
                            "title": "蜂蜜与四叶草",
                            "poster_url": "https://image.example/poster.jpg",
                        }],
                        "keyboard": [[{
                            "text": "① 蜂蜜与四叶草",
                            "callback_data": "search:select:plan:0",
                        }]],
                    },
                    segment={
                        "role": "identity",
                        "presentation_kind": "photo",
                    },
                ),
            )
            await sink.drain()

        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.send_photo.assert_awaited_once()
        context.application.bot.delete_message.assert_awaited_once_with(
            chat_id=10,
            message_id=90,
        )
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.message_id, 91)
        self.assertEqual(segment.message_kind, "photo")
        self.assertEqual(segment.rendered_revision, 2)
        self.assertEqual(
            [name for name, _value in events],
            [
                "send_photo",
                "replace_cursor",
                "delete_old",
                "attach_keyboard",
                "retry_photo",
            ],
        )
        self.assertIsNone(events[0][1])
        self.assertIsNotNone(events[-1][1])
        self.assertTrue(
            events[-1][1].inline_keyboard[0][0].callback_data.startswith("~1.1~")
        )

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_photo_promotion_discards_new_message_when_cursor_cas_raises(
        self,
        build_poster_grid,
    ):
        from app.handlers.interaction_handler import (
            _promote_segment_text_to_photo,
        )

        candidate_grid = BytesIO(b"candidate-grid")
        candidate_grid.name = "telepiplex-candidates.jpg"
        build_poster_grid.return_value = candidate_grid
        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                details={"defer_photo_until_media": True},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=90,
            message_kind="text",
        )
        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                details={
                    "poster_items": [{
                        "number": 1,
                        "title": "蜂蜜与四叶草",
                        "poster_url": "https://image.example/poster.jpg",
                    }],
                },
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        context = self.context()

        with patch.object(
            self.coordinator,
            "replace_segment_message",
            side_effect=RuntimeError("cursor database unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await _promote_segment_text_to_photo(
                    context.application,
                    operation,
                    segment,
                    text="请选择作品",
                    markup=Mock(),
                )

        context.application.bot.delete_message.assert_awaited_once_with(
            chat_id=10,
            message_id=91,
        )

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_photo_promotion_timeout_is_uncertain_and_never_resends(
        self,
        build_poster_grid,
    ):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        candidate_grid = BytesIO(b"candidate-grid")
        candidate_grid.name = "telepiplex-candidates.jpg"
        build_poster_grid.return_value = candidate_grid
        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            status_text="正在识别媒体…",
            details={"defer_photo_until_media": True},
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await sink.drain()

        async def timeout_after_send(**_kwargs):
            current = self.coordinator.get_active_segment("op-1")
            self.assertEqual(current.state, "open")
            self.assertEqual(current.delivery_state, "delivering")
            raise TimedOut()

        context.application.bot.send_photo.side_effect = timeout_after_send
        await sink("search", self.report(
            revision=2,
            state="awaiting_input",
            stage="candidate_selection",
            status_text="请选择作品",
            control="exit",
            details={
                "defer_photo_until_media": True,
                "poster_items": [{
                    "number": 1,
                    "title": "蜂蜜与四叶草",
                    "poster_url": "https://image.example/poster.jpg",
                }],
            },
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await sink.drain()

        uncertain = self.coordinator.get_active_segment("op-1")
        context.application.bot.send_photo.assert_awaited_once()
        self.assertEqual(uncertain.state, "delivery_uncertain")
        self.assertEqual(uncertain.delivery_state, "uncertain")
        self.assertEqual(uncertain.message_id, 90)
        self.assertEqual(uncertain.message_kind, "text")
        self.assertEqual(uncertain.business_revision, 2)
        self.assertEqual(uncertain.rendered_revision, 1)

        await render_operation(
            context.application,
            Mock(),
            self.coordinator.get("op-1"),
        )
        context.application.bot.send_photo.assert_awaited_once()

    async def test_segment_update_arriving_during_first_send_edits_the_same_message(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def delayed_photo(**_kwargs):
            send_started.set()
            await release_send.wait()
            return SimpleNamespace(message_id=91)

        context = self.context()
        context.application.bot.send_photo.side_effect = delayed_photo
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))

        await sink("search", self.report(
            status_text="正在识别媒体…",
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await asyncio.wait_for(send_started.wait(), timeout=1)
        await sink("search", self.report(
            revision=2,
            state="awaiting_input",
            stage="candidate_selection",
            status_text="请选择作品",
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))

        release_send.set()
        await sink.drain()

        context.application.bot.send_photo.assert_awaited_once()
        context.application.bot.send_message.assert_not_awaited()
        context.application.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(
            context.application.bot.edit_message_caption.await_args.kwargs[
                "message_id"
            ],
            91,
        )
        self.assertEqual(
            context.application.bot.edit_message_caption.await_args.kwargs[
                "caption"
            ],
            "请选择作品",
        )
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.message_id, 91)
        self.assertEqual(segment.business_revision, 2)
        self.assertEqual(segment.rendered_revision, 2)

    async def test_segment_edit_failure_retries_same_known_message_without_replacement(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="正在搜索片源…",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()
        context.application.bot.edit_message_text.side_effect = [
            RuntimeError("telegram edit outcome unknown"),
            None,
        ]

        await sink("search", self.report(
            revision=2,
            stage="release_selection",
            state="awaiting_input",
            status_text="请选择片源",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.send_photo.assert_not_awaited()
        self.assertEqual(
            context.application.bot.edit_message_text.await_count,
            2,
        )
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.state, "open")
        self.assertEqual(segment.delivery_state, "delivered")
        self.assertEqual(segment.message_id, 90)
        self.assertEqual(segment.rendered_revision, 2)

    async def test_segment_first_send_failure_is_uncertain_and_never_falls_back(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        context.application.bot.send_photo.side_effect = RuntimeError(
            "telegram send outcome unknown"
        )
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))

        await sink("search", self.report(
            status_text="正在识别媒体…",
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await sink.drain()

        context.application.bot.send_photo.assert_awaited_once()
        context.application.bot.send_message.assert_not_awaited()
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.state, "delivery_uncertain")
        self.assertEqual(segment.delivery_state, "uncertain")
        self.assertIsNone(segment.message_id)

    async def test_same_projection_hash_advances_revision_without_telegram_edit(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        projection = {"text": "正在搜索片源…", "buttons": []}
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="正在搜索片源…",
            projection=projection,
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        await sink("search", self.report(
            revision=2,
            stage="prowlarr_search",
            status_text="正在搜索片源…",
            projection=projection,
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.edit_message_text.assert_not_awaited()
        segment = self.coordinator.get_active_segment("op-1")
        self.assertEqual(segment.business_revision, 2)
        self.assertEqual(segment.rendered_revision, 2)

    async def test_operation_sink_seals_only_after_the_latest_segment_render(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="搜索完成",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        response = await sink.seal("search", "op-1", "search")

        self.assertTrue(response["accepted"])
        self.assertEqual(response["segment"]["state"], "sealed")
        self.assertIsNone(self.coordinator.get_active_segment("op-1"))
        sealed = self.coordinator.get_segment(response["segment"]["segment_id"])
        self.assertEqual(sealed.state, "sealed")
        self.assertIsNotNone(sealed.sealed_at)
        sealed_edit = (
            context.application.bot.edit_message_reply_markup.await_args.kwargs
        )
        self.assertIsNone(sealed_edit["reply_markup"])

    async def test_latest_segment_report_and_seal_share_one_telegram_edit(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="正在搜索片源",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()
        context.application.bot.edit_message_text.reset_mock()
        context.application.bot.edit_message_reply_markup.reset_mock()

        await sink("search", self.report(
            revision=2,
            stage="release_selection",
            status_text="片源搜索完成",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        # Simulate a loaded Host where the Feature's seal RPC reaches the
        # event loop after the old 10 ms fixed coalescing delay.
        await asyncio.sleep(0.025)
        seal_started = asyncio.get_running_loop().time()
        response = await sink.seal("search", "op-1", "search")
        seal_elapsed = asyncio.get_running_loop().time() - seal_started
        await sink.drain()

        self.assertTrue(response["accepted"])
        self.assertLess(seal_elapsed, 0.1)
        context.application.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(
            context.application.bot.edit_message_text.await_args.kwargs[
                "reply_markup"
            ],
            {"inline_keyboard": []},
        )
        context.application.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_segment_seal_waits_for_initial_send_to_open_segment(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        render_started = asyncio.Event()
        release_render = asyncio.Event()

        async def blocked_initial_render(record):
            render_started.set()
            await release_render.wait()
            return await render_operation(
                context.application,
                Mock(),
                record,
            )

        sink = OperationReportSink(self.coordinator)
        sink.attach(blocked_initial_render)
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="搜索完成",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await render_started.wait()

        seal_task = asyncio.create_task(
            sink.seal("search", "op-1", "search")
        )
        await asyncio.sleep(0)
        returned_before_initial_render = seal_task.done()
        release_render.set()
        result = (await asyncio.gather(
            seal_task,
            return_exceptions=True,
        ))[0]
        await sink.drain()

        self.assertFalse(returned_before_initial_render)
        if isinstance(result, BaseException):
            raise result
        self.assertTrue(result["accepted"])
        self.assertEqual(result["segment"]["state"], "sealed")
        context.application.bot.send_message.assert_awaited_once()

    async def test_segment_seal_during_initial_send_renders_latest_revision_once(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def blocked_send(**_kwargs):
            send_started.set()
            await release_send.wait()
            return SimpleNamespace(message_id=90)

        context.application.bot.send_message.side_effect = blocked_send
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            stage="prowlarr_search",
            status_text="正在搜索片源…",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await send_started.wait()
        await sink("search", self.report(
            revision=2,
            stage="release_selection",
            state="awaiting_input",
            status_text="请选择片源",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))

        seal_task = asyncio.create_task(
            sink.seal("search", "op-1", "search")
        )
        await asyncio.sleep(0)
        self.assertFalse(seal_task.done())
        release_send.set()
        response = await seal_task
        await sink.drain()

        self.assertTrue(response["accepted"])
        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.edit_message_text.assert_awaited_once()
        sealed = self.coordinator.get_segment(
            response["segment"]["segment_id"]
        )
        self.assertEqual(sealed.state, "sealed")
        self.assertEqual(sealed.business_revision, 2)
        self.assertEqual(sealed.rendered_revision, 2)
        self.assertIsNone(self.coordinator.get_active_segment("op-1"))

    async def test_segment_seal_waits_for_inflight_render_without_duplicate_photo(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        sink.attach(lambda record: render_operation(
            context.application,
            Mock(),
            record,
        ))
        await sink("search", self.report(
            stage="candidate_selection",
            status_text="请选择作品候选",
            details={"photo_url": "https://image.example/candidate.jpg"},
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await sink.drain()

        edit_started = asyncio.Event()
        release_edit = asyncio.Event()

        async def blocked_edit(**_kwargs):
            edit_started.set()
            await release_edit.wait()

        context.application.bot.edit_message_media.side_effect = blocked_edit
        await sink("search", self.report(
            revision=2,
            stage="identity_confirmation",
            status_text="已确认身份，开始搜索",
            details={"photo_url": "https://image.example/confirmed.jpg"},
            segment={
                "role": "identity",
                "presentation_kind": "photo",
            },
        ))
        await edit_started.wait()

        seal_task = asyncio.create_task(
            sink.seal("search", "op-1", "identity")
        )
        await asyncio.sleep(0)
        returned_before_render = seal_task.done()
        release_edit.set()
        response = await seal_task
        await sink.drain()

        self.assertFalse(returned_before_render)
        self.assertTrue(response["accepted"])
        self.assertEqual(response["segment"]["state"], "sealed")
        self.assertEqual(context.application.bot.send_photo.await_count, 1)
        self.assertEqual(
            context.application.bot.edit_message_media.await_count,
            1,
        )

        search_response = await sink("search", self.report(
            revision=3,
            stage="prowlarr_search",
            status_text="正在搜索片源",
            segment={
                "role": "search",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        self.assertTrue(search_response["accepted"])
        context.application.bot.send_message.assert_awaited_once()

    async def test_terminal_segment_queued_behind_render_does_not_send_duplicate_text(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        sink = OperationReportSink(self.coordinator)
        render_started = asyncio.Event()
        release_render = asyncio.Event()
        gated = False

        async def listener(record):
            nonlocal gated
            if record.revision >= 2 and not gated:
                gated = True
                render_started.set()
                await release_render.wait()
            return await render_operation(
                context.application,
                Mock(),
                record,
            )

        sink.attach(listener)
        await sink("rename", self.report(
            stage="organizing",
            status_text="正在整理",
            segment={
                "role": "rename",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        await sink("rename", self.report(
            revision=2,
            stage="organizing",
            status_text="正在整理文件",
            segment={
                "role": "rename",
                "presentation_kind": "text",
            },
        ))
        await render_started.wait()
        await sink("rename", self.report(
            revision=3,
            state="failed",
            stage="organizing",
            status_text="无法确定整理规则，文件保留在原目录。",
            control="",
            segment={
                "role": "rename",
                "presentation_kind": "text",
            },
        ))
        seal_task = asyncio.create_task(
            sink.seal("rename", "op-1", "rename")
        )
        await asyncio.sleep(0)
        release_render.set()

        response = await seal_task
        await sink.drain()

        self.assertTrue(response["accepted"])
        context.application.bot.send_message.assert_awaited_once()
        context.application.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(
            context.application.bot.edit_message_text.await_args.kwargs["text"],
            "无法确定整理规则，文件保留在原目录。",
        )

    async def test_operation_sink_persists_silent_transition_without_rendering(self):
        from app.handlers.interaction_handler import OperationReportSink

        rendered = AsyncMock()
        sink = OperationReportSink(self.coordinator)
        sink.attach(rendered)

        response = await sink("download", self.report(
            details={"telegram_visibility": "silent"},
        ))
        await sink.drain()

        self.assertTrue(response["accepted"])
        self.assertEqual(
            self.coordinator.get("op-1").details["telegram_visibility"],
            "silent",
        )
        rendered.assert_not_awaited()

    async def test_operation_sink_does_not_block_running_report_on_renderer(self):
        from app.handlers.interaction_handler import OperationReportSink

        release = asyncio.Event()
        started = asyncio.Event()

        async def render(_record):
            started.set()
            await release.wait()

        sink = OperationReportSink(self.coordinator)
        sink.attach(render)
        task = asyncio.create_task(
            sink("search", self.report())
        )

        await started.wait()
        await asyncio.sleep(0)
        self.assertTrue(task.done())
        self.assertTrue((await task)["accepted"])
        release.set()
        await asyncio.gather(*sink._tasks)

    async def test_download_handoff_freezes_before_rename_uses_new_message(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        context.application.bot.send_message.side_effect = [
            SimpleNamespace(message_id=41),
            SimpleNamespace(message_id=42),
        ]
        sink = OperationReportSink(self.coordinator)
        sink.attach(
            lambda record: render_operation(
                context.application,
                Mock(),
                record,
            )
        )

        await sink("download", self.report(
            stage="downloading",
            status_text="正在下载。",
        ))
        await asyncio.gather(*sink._tasks)
        self.assertEqual(self.coordinator.get("op-1").message_id, 41)

        await sink("download", self.report(
            state="handed_off",
            stage="handoff_rename",
            status_text=(
                "✅ 115 下载完成\n"
                "保存目录：/真人剧集/Veep.S01"
            ),
            revision=2,
            next_plugin_id="rename",
        ))
        await sink.drain()
        self.assertEqual(self.coordinator.get("op-1").message_id, 41)
        self.assertEqual(
            context.application.bot.edit_message_text.await_args.kwargs["text"],
            "✅ 115 下载完成\n保存目录：/真人剧集/Veep.S01",
        )

        await sink("rename", self.report(
            stage="organizing",
            status_text="正在移动媒体文件。",
            revision=3,
        ))
        await asyncio.gather(*sink._tasks)
        self.assertEqual(self.coordinator.get("op-1").message_id, 42)
        self.assertEqual(
            context.application.bot.send_message.await_args.kwargs["text"],
            "正在移动媒体文件。",
        )

        await sink("rename", self.report(
            stage="organizing",
            status_text="正在重命名媒体文件。",
            revision=4,
        ))
        await asyncio.gather(*sink._tasks)
        edited = context.application.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edited["chat_id"], 10)
        self.assertEqual(edited["message_id"], 42)
        self.assertEqual(edited["text"], "正在重命名媒体文件。")

    async def test_segmented_handoff_is_control_plane_only_and_next_owner_gets_one_message(self):
        from app.handlers.interaction_handler import (
            OperationReportSink,
            render_operation,
        )

        context = self.context()
        context.application.bot.send_message.side_effect = [
            SimpleNamespace(message_id=41),
            SimpleNamespace(message_id=42),
        ]
        sink = OperationReportSink(self.coordinator)
        sink.attach(
            lambda record: render_operation(
                context.application,
                Mock(),
                record,
            )
        )

        await sink("download", self.report(
            stage="downloading",
            status_text="115：下载中",
            segment={
                "role": "download",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()
        self.assertEqual(context.application.bot.send_message.await_count, 1)

        sealed = await sink.seal("download", "op-1", "download")
        self.assertTrue(sealed["accepted"])
        handoff = await sink("download", self.report(
            state="handed_off",
            stage="handoff_rename",
            status_text="已下载，开始整理",
            revision=2,
            next_plugin_id="rename",
        ))
        await sink.drain()

        self.assertTrue(handoff["accepted"])
        self.assertEqual(context.application.bot.send_message.await_count, 1)

        rename = await sink("rename", self.report(
            stage="organizing",
            status_text="正在整理媒体文件",
            revision=3,
            segment={
                "role": "rename",
                "presentation_kind": "text",
            },
        ))
        await sink.drain()

        self.assertTrue(rename["accepted"])
        self.assertEqual(context.application.bot.send_message.await_count, 2)
        self.assertEqual(
            context.application.bot.send_message.await_args.kwargs["text"],
            "正在整理媒体文件",
        )

    async def test_operation_milestone_is_idempotent_after_durable_enqueue(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        record = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(record.operation_id, 42, "photo")
        deliveries = []

        def deliver(delivery_record, mode, photo_url, text):
            deliveries.append((delivery_record.chat_id, mode, photo_url, text))
            return True

        sink = OperationMilestoneSink(self.coordinator, deliver)
        await sink.start()
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-douban-35981510",
            "text": "繁花 (Blossoms Shanghai)",
            "photo_url": "https://img.example/blossoms.jpg",
        }

        first = await sink("search", payload)
        duplicate = await sink("search", payload)
        await sink.drain()

        self.assertEqual(first, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        self.assertEqual(duplicate, {
            "accepted": True, "queued": True, "duplicate": True,
        })
        self.assertEqual(deliveries, [
            (
                10,
                "identity",
                "https://img.example/blossoms.jpg",
                "繁花 (Blossoms Shanghai)",
            ),
        ])
        self.assertIsNone(self.coordinator.get("op-1").message_id)

    async def test_operation_milestone_returns_after_enqueue_before_async_delivery(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        release = asyncio.Event()
        started = asyncio.Event()

        async def deliver(_record, _mode, _photo_url, _text):
            started.set()
            await release.wait()
            return True

        sink = OperationMilestoneSink(self.coordinator, deliver)
        await sink.start()
        response = await asyncio.wait_for(sink("search", {
            "operation_id": "op-1",
            "milestone_id": "media-wait",
            "text": "繁花 (Blossoms Shanghai)",
            "photo_url": "https://img.example/blossoms.jpg",
        }), timeout=0.1)

        await started.wait()
        self.assertEqual(response, {
            "accepted": True,
            "queued": True,
            "duplicate": False,
        })
        self.assertIn(
            self.coordinator.get_milestone(
                "op-1", "media-wait"
            ).delivery_state,
            {"pending", "delivering"},
        )
        release.set()
        await sink.drain()
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-wait"
            ).delivery_state,
            "delivered",
        )

    async def test_operation_milestone_retries_only_explicit_rejection_three_times(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        delivery = AsyncMock(return_value={"accepted": False})
        sink = OperationMilestoneSink(self.coordinator, delivery)
        await sink.start()

        response = await sink("search", {
            "operation_id": "op-1",
            "milestone_id": "media-rejected",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })
        await sink.drain()

        self.assertTrue(response["accepted"])
        self.assertEqual(delivery.await_count, 3)
        intent = self.coordinator.get_milestone("op-1", "media-rejected")
        self.assertEqual(intent.delivery_state, "failed")
        self.assertEqual(intent.attempt_count, 3)

    async def test_operation_milestone_uncertain_exception_is_unknown_without_retry(self):
        from telegram.error import TimedOut
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        delivery = AsyncMock(side_effect=TimedOut("uncertain"))
        sink = OperationMilestoneSink(self.coordinator, delivery)
        await sink.start()

        response = await sink("search", {
            "operation_id": "op-1",
            "milestone_id": "media-uncertain",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })
        await sink.drain()

        self.assertTrue(response["accepted"])
        delivery.assert_awaited_once()
        intent = self.coordinator.get_milestone("op-1", "media-uncertain")
        self.assertEqual(intent.delivery_state, "unknown")
        self.assertEqual(intent.attempt_count, 1)

    async def test_malformed_milestone_targets_are_unknown_without_retry_or_cursor_cas(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        malformed_results = (
            ("id-without-kind", {"accepted": True, "message_id": 99}),
            ("kind-without-id", {"accepted": True, "message_kind": "text"}),
            (
                "invalid-kind",
                {
                    "accepted": True,
                    "message_id": 99,
                    "message_kind": "bogus",
                },
            ),
            (
                "zero-id",
                {
                    "accepted": True,
                    "message_id": 0,
                    "message_kind": "text",
                },
            ),
            (
                "non-numeric-id",
                {
                    "accepted": True,
                    "message_id": "not-a-message-id",
                    "message_kind": "text",
                },
            ),
        )
        for index, (case, result) in enumerate(malformed_results, 1):
            with self.subTest(case=case):
                operation_id = f"op-invalid-target-{index}"
                self.coordinator.report("search", self.report(
                    operation_id=operation_id,
                    chat_id=100 + index,
                    user_id=100 + index,
                ))
                self.coordinator.set_message_id(operation_id, 41, "text")
                delivery = AsyncMock(return_value=result)
                sink = OperationMilestoneSink(self.coordinator, delivery)
                await sink.start()

                response = await sink("search", {
                    "operation_id": operation_id,
                    "milestone_id": f"malformed-{case}",
                    "mode": "stage",
                    "text": "搜索完成",
                    "photo_url": "",
                })
                await sink.drain()

                self.assertTrue(response["accepted"])
                delivery.assert_awaited_once()
                intent = self.coordinator.get_milestone(
                    operation_id, f"malformed-{case}"
                )
                self.assertEqual(intent.delivery_state, "unknown")
                self.assertEqual(intent.attempt_count, 1)
                self.assertIsNone(intent.delivered_message_id)
                self.assertEqual(self.coordinator.get(operation_id).message_id, 41)

    async def test_targetless_mapping_success_remains_delivered(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        delivery = AsyncMock(return_value={"accepted": True})
        sink = OperationMilestoneSink(self.coordinator, delivery)
        await sink.start()

        await sink("search", {
            "operation_id": "op-1",
            "milestone_id": "targetless-success",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        })
        await sink.drain()

        delivery.assert_awaited_once()
        intent = self.coordinator.get_milestone(
            "op-1", "targetless-success"
        )
        self.assertEqual(intent.delivery_state, "delivered")
        self.assertIsNone(intent.delivered_message_id)

    async def test_telegram_success_without_message_id_omits_target_pair(self):
        from app.handlers.interaction_handler import deliver_operation_milestone

        context = self.context()
        context.application.bot.send_message.return_value = SimpleNamespace()

        result = await deliver_operation_milestone(
            context.application,
            self.coordinator.report("search", self.report()),
            "stage",
            "",
            "搜索完成",
        )

        self.assertEqual(result, {"accepted": True})

    async def test_fifty_duplicate_milestones_share_one_delivery_worker(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        release = asyncio.Event()
        started = asyncio.Event()
        delivery = Mock()

        async def blocked_delivery(*_args):
            delivery(*_args)
            started.set()
            await release.wait()
            return True

        sink = OperationMilestoneSink(self.coordinator, blocked_delivery)
        await sink.start()
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-duplicate-wave",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        }

        responses = await asyncio.gather(*(
            sink("search", payload) for _ in range(50)
        ))
        await started.wait()

        self.assertEqual(sum(not item["duplicate"] for item in responses), 1)
        self.assertEqual(len(sink._workers), 1)
        self.assertEqual(delivery.call_count, 1)
        release.set()
        await sink.drain()
        self.assertEqual(delivery.call_count, 1)

    async def test_milestone_start_recovers_pending_once_idempotently(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-restart",
            "mode": "stage",
            "text": "搜索完成",
            "photo_url": "",
        }
        dormant = OperationMilestoneSink(self.coordinator, AsyncMock())
        queued = await dormant("search", payload)
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-restart"
            ).delivery_state,
            "pending",
        )

        delivery = AsyncMock(return_value=True)
        recovered = OperationMilestoneSink(self.coordinator, delivery)
        await asyncio.gather(recovered.start(), recovered.start())
        await recovered.drain()

        self.assertTrue(queued["accepted"])
        delivery.assert_awaited_once()
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-restart"
            ).delivery_state,
            "delivered",
        )

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_milestone_retry_reuses_new_message_after_completion_failure(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import (
            OperationMilestoneSink,
            deliver_operation_milestone,
        )

        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 42, "text")
        photo = BytesIO(b"identity")
        photo.name = "telepiplex-identity.jpg"
        build_grid.return_value = photo
        context = self.context()
        original_complete = self.coordinator.complete_milestone_delivery
        completion_lost = True

        def complete_then_lose(*args):
            nonlocal completion_lost
            if completion_lost:
                completion_lost = False
                raise RuntimeError("completion interrupted")
            return original_complete(*args)

        self.coordinator.complete_milestone_delivery = complete_then_lose
        sink = OperationMilestoneSink(
            self.coordinator,
            lambda current, mode, photo_url, text: deliver_operation_milestone(
                context.application,
                current,
                mode,
                photo_url,
                text,
            ),
        )
        await sink.start()
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-recover",
            "mode": "identity",
            "text": "🎬 繁花 (Blossoms Shanghai)",
            "photo_url": "https://img.example/poster.jpg",
        }

        response = await sink("search", payload)
        await sink.drain()
        self.assertIsNone(self.coordinator.get("op-1").message_id)
        context.application.bot.send_photo.assert_awaited_once()

        duplicate = await sink("search", payload)
        await sink.drain()

        self.assertEqual(response, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        self.assertEqual(duplicate, {
            "accepted": True, "queued": True, "duplicate": True,
        })
        context.application.bot.send_photo.assert_awaited_once()
        self.assertIsNone(self.coordinator.get("op-1").message_id)
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-recover"
            ).delivery_state,
            "delivered",
        )

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_milestone_retry_does_not_resend_when_target_record_fails(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import (
            OperationMilestoneSink,
            deliver_operation_milestone,
        )

        created = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(created.operation_id, 42, "text")
        photo = BytesIO(b"identity")
        photo.name = "telepiplex-identity.jpg"
        build_grid.return_value = photo
        context = self.context()
        record_lost = True

        original_record = self.coordinator.record_milestone_delivery_target

        def record_then_lose(*args):
            nonlocal record_lost
            if record_lost:
                record_lost = False
                raise RuntimeError("delivery target record interrupted")
            return original_record(*args)

        self.coordinator.record_milestone_delivery_target = record_then_lose
        sink = OperationMilestoneSink(
            self.coordinator,
            lambda current, mode, photo_url, text: deliver_operation_milestone(
                context.application,
                current,
                mode,
                photo_url,
                text,
            ),
        )
        await sink.start()
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-record-recover",
            "mode": "identity",
            "text": "🎬 繁花 (Blossoms Shanghai)",
            "photo_url": "https://img.example/poster.jpg",
        }

        response = await sink("search", payload)
        await sink.drain()
        context.application.bot.send_photo.assert_awaited_once()

        duplicate = await sink("search", payload)
        await sink.drain()

        self.assertEqual(response, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        self.assertEqual(duplicate, {
            "accepted": True, "queued": True, "duplicate": True,
        })
        context.application.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.coordinator.get("op-1").message_id, 42)
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-record-recover"
            ).delivery_state,
            "unknown",
        )

    async def test_operation_milestone_shares_operation_render_lock(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        render_lock = asyncio.Lock()
        delivery = AsyncMock(return_value=True)
        sink = OperationMilestoneSink(
            self.coordinator,
            delivery,
            lambda _operation_id: render_lock,
        )
        await sink.start()
        await render_lock.acquire()
        response = await sink("search", {
            "operation_id": "op-1",
            "milestone_id": "media-locked",
            "mode": "stage",
            "text": "资源搜索已完成。",
        })

        await asyncio.sleep(0)
        delivery.assert_not_awaited()
        render_lock.release()

        self.assertEqual(response, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        await sink.drain()
        delivery.assert_awaited_once()

    async def test_cancelled_operation_milestone_becomes_unknown_without_retry(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        self.coordinator.report("search", self.report())
        started = asyncio.Event()

        async def deliver(_record, _mode, _photo_url, _text):
            started.set()
            await asyncio.Event().wait()

        sink = OperationMilestoneSink(self.coordinator, deliver)
        await sink.start()
        payload = {
            "operation_id": "op-1",
            "milestone_id": "media-cancelled",
            "text": "繁花 (Blossoms Shanghai)",
            "photo_url": "",
        }
        response = await sink("search", payload)
        await started.wait()
        self.assertFalse(await sink.drain(timeout=0.01))

        self.assertTrue(response["accepted"])
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "media-cancelled"
            ).delivery_state,
            "unknown",
        )
        self.assertEqual(sink._workers, {})
        self.assertEqual(sink._tasks, set())

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_milestone_replaces_current_photo_then_rotates_cursor(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import (
            OperationMilestoneSink,
            deliver_operation_milestone,
        )

        photo = BytesIO(b"identity")
        photo.name = "telepiplex-identity.jpg"
        build_grid.return_value = photo
        record = self.coordinator.report("search", self.report())
        self.coordinator.set_message_id(record.operation_id, 42, "photo")
        context = self.context()
        sink = OperationMilestoneSink(
            self.coordinator,
            lambda current, mode, photo_url, text: deliver_operation_milestone(
                context.application,
                current,
                mode,
                photo_url,
                text,
            ),
        )
        await sink.start()

        result = await sink("search", {
            "operation_id": "op-1",
            "milestone_id": "media-identity",
            "mode": "identity",
            "text": "🎬 繁花 (Blossoms Shanghai)",
            "photo_url": "https://img.example/poster.jpg",
        })
        await sink.drain()

        self.assertEqual(result, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        context.application.bot.edit_message_media.assert_awaited_once()
        edited = context.application.bot.edit_message_media.await_args.kwargs
        self.assertEqual(edited["chat_id"], 10)
        self.assertEqual(edited["message_id"], 42)
        self.assertIsNone(edited["reply_markup"])
        context.application.bot.send_photo.assert_not_awaited()
        self.assertIsNone(self.coordinator.get("op-1").message_id)

    async def test_stage_milestone_edits_current_text_then_rotates_cursor(self):
        from app.handlers.interaction_handler import (
            OperationMilestoneSink,
            deliver_operation_milestone,
        )

        record = self.coordinator.report("download", self.report())
        self.coordinator.set_message_id(record.operation_id, 43, "text")
        context = self.context()
        sink = OperationMilestoneSink(
            self.coordinator,
            lambda current, mode, photo_url, text: deliver_operation_milestone(
                context.application,
                current,
                mode,
                photo_url,
                text,
            ),
        )
        await sink.start()

        result = await sink("download", {
            "operation_id": "op-1",
            "milestone_id": "download-completed",
            "mode": "stage",
            "text": "✅ 115 下载完成",
            "photo_url": "",
        })
        await sink.drain()

        self.assertEqual(result, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        context.application.bot.edit_message_text.assert_awaited_once_with(
            chat_id=10,
            message_id=43,
            text="✅ 115 下载完成",
            reply_markup=None,
        )
        context.application.bot.send_message.assert_not_awaited()
        self.assertIsNone(self.coordinator.get("op-1").message_id)

    async def test_failed_milestone_delivery_preserves_message_cursor(self):
        from app.handlers.interaction_handler import OperationMilestoneSink

        record = self.coordinator.report("download", self.report())
        self.coordinator.set_message_id(record.operation_id, 43, "text")
        sink = OperationMilestoneSink(
            self.coordinator,
            lambda _record, _mode, _photo_url, _text: False,
        )
        await sink.start()

        result = await sink("download", {
            "operation_id": "op-1",
            "milestone_id": "download-completed",
            "mode": "stage",
            "text": "✅ 115 下载完成",
            "photo_url": "",
        })
        await sink.drain()

        self.assertEqual(result, {
            "accepted": True, "queued": True, "duplicate": False,
        })
        self.assertEqual(self.coordinator.get("op-1").message_id, 43)
        self.assertEqual(
            self.coordinator.get_milestone(
                "op-1", "download-completed"
            ).delivery_state,
            "failed",
        )

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_delivery_without_remote_url_sends_title_placeholder(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import deliver_operation_milestone

        placeholder = BytesIO(b"placeholder")
        placeholder.name = "telepiplex-candidates.jpg"
        build_grid.return_value = placeholder
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=92)
        )

        accepted = await deliver_operation_milestone(
            context.application,
            10,
            None,
            "🎬 繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
        )

        self.assertTrue(accepted)
        self.assertEqual(
            build_grid.call_args.args[0],
            [{
                "number": 1,
                "title": "繁花 (Blossoms Shanghai)",
                "poster_url": "",
            }],
        )
        self.assertIs(
            context.application.bot.send_photo.await_args.kwargs["photo"],
            placeholder,
        )
        context.application.bot.send_message.assert_not_awaited()

    async def test_stage_delivery_logs_the_exact_frontend_text_and_operation_in_typed_events(self):
        from app.handlers import interaction_handler
        from app.handlers.interaction_handler import deliver_operation_milestone

        record = self.coordinator.report("search", self.report())
        context = self.context()
        logger = Mock()

        with patch.object(interaction_handler.init, "logger", logger):
            result = await deliver_operation_milestone(
                context.application,
                record,
                "stage",
                "",
                "✅ 搜索完成，共 38 条候选",
            )

        events = [
            call.kwargs for call in logger.info.call_args_list
            if call.kwargs.get("event_name")
        ]
        started = next(
            item for item in events
            if item["event_name"] == "telegram.milestone.delivery.started"
        )
        completed = next(
            item for item in events
            if item["event_name"] == "telegram.milestone.delivery.completed"
        )
        self.assertEqual(result["message_id"], 90)
        self.assertEqual(
            started["diagnostic_fields"]["user_surface"]["text"],
            "✅ 搜索完成，共 38 条候选",
        )
        self.assertEqual(
            started["diagnostic_fields"]["input"]["operation_id"],
            record.operation_id,
        )
        self.assertEqual(completed["diagnostic_fields"]["output"]["message_id"], 90)

    async def test_stage_transport_timeout_does_not_fallback_or_resend(self):
        from telegram.error import TimedOut
        from app.handlers.interaction_handler import deliver_operation_milestone

        created = self.coordinator.report("search", self.report())
        record = self.coordinator.set_message_id(
            created.operation_id, 43, "text"
        )
        context = self.context()
        context.application.bot.edit_message_text.side_effect = TimedOut(
            "uncertain"
        )

        with self.assertRaises(TimedOut):
            await deliver_operation_milestone(
                context.application,
                record,
                "stage",
                "",
                "搜索完成",
            )

        context.application.bot.edit_message_text.assert_awaited_once()
        context.application.bot.send_message.assert_not_awaited()

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_transport_timeout_does_not_fallback_to_text(
        self, build_grid
    ):
        from telegram.error import TimedOut
        from app.handlers.interaction_handler import deliver_operation_milestone

        photo = BytesIO(b"identity")
        photo.name = "telepiplex-identity.jpg"
        build_grid.return_value = photo
        context = self.context()
        context.application.bot.send_photo.side_effect = TimedOut("uncertain")

        with self.assertRaises(TimedOut):
            await deliver_operation_milestone(
                context.application,
                10,
                "https://img.example/poster.jpg",
                "🎬 繁花",
            )

        context.application.bot.send_photo.assert_awaited_once()
        context.application.bot.send_message.assert_not_awaited()

    async def test_final_bad_request_is_an_explicit_milestone_rejection(self):
        from app.handlers.interaction_handler import deliver_operation_milestone

        context = self.context()
        context.application.bot.send_message.side_effect = BadRequest(
            "chat rejected"
        )

        result = await deliver_operation_milestone(
            context.application,
            self.coordinator.report("search", self.report()),
            "stage",
            "",
            "搜索完成",
        )

        self.assertEqual(result["accepted"], False)
        context.application.bot.send_message.assert_awaited_once()

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_delivery_reuses_title_placeholder_when_remote_fails(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import deliver_operation_milestone
        from app.runtime.poster_grid import PosterGridUnavailable

        placeholder = BytesIO(b"placeholder")
        placeholder.name = "telepiplex-candidates.jpg"
        build_grid.side_effect = [
            PosterGridUnavailable("poster_grid_no_images"),
            placeholder,
        ]
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=91)
        )

        accepted = await deliver_operation_milestone(
            context.application,
            10,
            "https://img9.doubanio.com/poster.jpg",
            "🎬 繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
        )

        self.assertTrue(accepted)
        self.assertEqual(build_grid.call_count, 2)
        self.assertEqual(
            build_grid.call_args_list[1].args[0],
            [{
                "number": 1,
                "title": "繁花 (Blossoms Shanghai)",
                "poster_url": "",
            }],
        )
        self.assertIs(
            context.application.bot.send_photo.await_args.kwargs["photo"],
            placeholder,
        )
        context.application.bot.send_message.assert_not_awaited()

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_identity_delivery_falls_back_to_text_when_local_photo_fails(
        self,
        build_grid,
    ):
        from app.handlers.interaction_handler import deliver_operation_milestone

        local_photo = BytesIO(b"photo")
        local_photo.name = "telepiplex-candidates.jpg"
        build_grid.return_value = local_photo
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            side_effect=BadRequest("photo failed")
        )

        accepted = await deliver_operation_milestone(
            context.application,
            10,
            "https://img.example/poster.jpg",
            "🎬 繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
        )

        self.assertTrue(accepted)
        context.application.bot.send_message.assert_awaited_once_with(
            chat_id=10,
            text="🎬 繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
        )

    async def test_running_operation_rejects_unrelated_callback_with_toast(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report("search", self.report())
        update = self.callback_update("plex:scan")

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context())

        update.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_running_prowlarr_allows_only_current_opted_in_release_button(self):
        from app.handlers.interaction_handler import operation_gate

        record = self.coordinator.report(
            "search",
            self.report(
                state="running",
                stage="prowlarr_search",
                details={
                    "allow_running_callbacks": True,
                    "keyboard": [[{
                        "text": "①",
                        "callback_data": "search:release:current",
                    }]],
                },
            ),
        )
        self.coordinator.set_message_id(record.operation_id, 55)
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        router.plugin_route.return_value = route

        current = self.callback_update("search:release:current")
        await operation_gate(current, self.context(router=router))
        current.callback_query.answer.assert_not_awaited()

        stale = self.callback_update("search:release:stale")
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(stale, self.context(router=router))
        stale.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_segment_callback_ack_and_dispatch_do_not_wait_for_busy_render(self):
        from telegram import CallbackQuery, Chat, Message, User
        from app.handlers import interaction_handler
        from app.handlers.interaction_handler import operation_gate, operation_markup
        from app.handlers.plugin_handler import dynamic_callback_gateway

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
        )
        rpc_started = asyncio.Event()

        async def dispatch_callback(*_args, **_kwargs):
            rpc_started.set()
            return {"actions": []}

        client = SimpleNamespace(request=AsyncMock(side_effect=dispatch_callback))
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
            client=client,
        )
        router = Mock()
        router.plugin_route.return_value = route
        router.callback_route.return_value = route
        encoded = operation_markup(
            operation,
            router,
            segment=segment,
        ).inline_keyboard[0][0].callback_data

        context = self.context(router=router)
        callback_query = CallbackQuery(
            id="candidate-click-fast-ack",
            from_user=User(id=1, first_name="Tester", is_bot=False),
            chat_instance="candidate-chat",
            message=Message(
                message_id=92,
                date=datetime.now(timezone.utc),
                chat=Chat(id=10, type="private"),
            ),
            data=encoded,
        )
        callback_query.set_bot(context.application.bot)
        update = SimpleNamespace(
            update_id=12,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=callback_query,
        )
        busy_started = asyncio.Event()
        release_busy = asyncio.Event()
        busy_calls = 0

        async def blocked_busy_caption(**_kwargs):
            nonlocal busy_calls
            busy_calls += 1
            busy_started.set()
            if busy_calls == 1:
                await release_busy.wait()

        context.application.bot.edit_message_caption.side_effect = (
            blocked_busy_caption
        )
        async def process_update():
            await operation_gate(update, context)
            with patch(
                "app.handlers.plugin_handler.init.check_user",
                return_value=True,
            ):
                await dynamic_callback_gateway(update, context)

        processing = asyncio.create_task(process_update())
        try:
            await asyncio.wait_for(busy_started.wait(), timeout=0.1)
            await asyncio.wait_for(rpc_started.wait(), timeout=0.1)
        finally:
            release_busy.set()
            await asyncio.wait_for(processing, timeout=0.3)

        self.assertEqual(
            context.application.bot.answer_callback_query.await_count,
            1,
        )
        callback_ack = (
            context.application.bot.answer_callback_query.await_args.kwargs
        )
        self.assertEqual(
            callback_ack["callback_query_id"],
            "candidate-click-fast-ack",
        )
        self.assertEqual(callback_ack["text"], "处理中...")
        client.request.assert_awaited_once()
        self.assertEqual(
            self.coordinator.get_active_segment("op-1").callback_state,
            "idle",
        )

    async def test_segment_busy_render_does_not_block_latest_projection(self):
        from telegram import CallbackQuery, Chat, Message, User
        from app.handlers.interaction_handler import (
            CALLBACK_FEEDBACK_TASKS_KEY,
            operation_gate,
            operation_markup,
            render_operation,
        )

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
        )
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        router.callback_route.return_value = route
        encoded = operation_markup(
            operation,
            router,
            segment=segment,
        ).inline_keyboard[0][0].callback_data
        context = self.context(router=router)
        callback_query = CallbackQuery(
            id="candidate-click-render-lock",
            from_user=User(id=1, first_name="Tester", is_bot=False),
            chat_instance="candidate-chat",
            message=Message(
                message_id=92,
                date=datetime.now(timezone.utc),
                chat=Chat(id=10, type="private"),
            ),
            data=encoded,
        )
        callback_query.set_bot(context.application.bot)
        update = SimpleNamespace(
            update_id=13,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=callback_query,
        )
        busy_started = asyncio.Event()
        release_busy = asyncio.Event()
        projection_started = asyncio.Event()
        visible_captions = []
        edit_calls = 0

        async def record_caption_delivery(**kwargs):
            nonlocal edit_calls
            edit_calls += 1
            if edit_calls == 1:
                busy_started.set()
                await release_busy.wait()
            else:
                projection_started.set()
            visible_captions.append(kwargs["caption"])

        context.application.bot.edit_message_caption.side_effect = (
            record_caption_delivery
        )
        await operation_gate(update, context)
        await asyncio.wait_for(busy_started.wait(), timeout=0.2)
        refreshed, _segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                state="awaiting_input",
                stage="candidate_selection",
                status_text="候选证据已刷新",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        await asyncio.wait_for(
            render_operation(context.application, router, refreshed),
            timeout=0.2,
        )
        self.assertTrue(projection_started.is_set())
        claimed = self.coordinator.get_active_segment("op-1")
        self.coordinator.release_segment_callback(
            "search",
            "op-1",
            message_id=92,
            segment_generation=claimed.generation,
            callback_generation=claimed.callback_generation,
            callback_token="search:select:p1:0",
        )

        release_busy.set()
        for _attempt in range(100):
            if not context.application.bot_data.get(
                CALLBACK_FEEDBACK_TASKS_KEY
            ):
                break
            await asyncio.sleep(0)
        else:
            self.fail("callback feedback task did not finish")

        self.assertEqual(visible_captions[-1], "候选证据已刷新")

    async def test_late_busy_delivery_restores_sealed_segment_projection(self):
        from telegram import CallbackQuery, Chat, Message, User
        from app.handlers.interaction_handler import (
            CALLBACK_FEEDBACK_TASKS_KEY,
            operation_gate,
            operation_markup,
            render_operation,
        )

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
            message_kind="photo",
        )
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        encoded = operation_markup(
            operation,
            router,
            segment=segment,
        ).inline_keyboard[0][0].callback_data
        context = self.context(router=router)
        callback_query = CallbackQuery(
            id="candidate-click-late-busy",
            from_user=User(id=1, first_name="Tester", is_bot=False),
            chat_instance="candidate-chat",
            message=Message(
                message_id=92,
                date=datetime.now(timezone.utc),
                chat=Chat(id=10, type="private"),
            ),
            data=encoded,
        )
        callback_query.set_bot(context.application.bot)
        update = SimpleNamespace(
            update_id=14,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=callback_query,
        )
        busy_started = asyncio.Event()
        release_busy = asyncio.Event()
        visible_captions = []

        async def record_caption_delivery(**kwargs):
            caption = kwargs["caption"]
            if caption == "正在确认媒体身份…":
                busy_started.set()
                await release_busy.wait()
            visible_captions.append(caption)

        context.application.bot.edit_message_caption.side_effect = (
            record_caption_delivery
        )

        await operation_gate(update, context)
        await asyncio.wait_for(busy_started.wait(), timeout=0.1)
        refreshed, refreshed_segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                state="running",
                stage="identity_confirmed",
                status_text="已确认作品",
                details={},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        await render_operation(context.application, router, refreshed)
        self.coordinator.seal_segment("search", "op-1", "identity")
        await render_operation(context.application, router, refreshed)
        self.assertEqual(
            self.coordinator.get_segment(refreshed_segment.segment_id).state,
            "sealed",
        )

        release_busy.set()
        for _attempt in range(100):
            if not context.application.bot_data.get(
                CALLBACK_FEEDBACK_TASKS_KEY
            ):
                break
            await asyncio.sleep(0)
        else:
            self.fail("callback feedback task did not finish")

        self.assertEqual(visible_captions[-1], "已确认作品")

    async def test_segment_keyboard_claim_disables_double_click_before_feature_dispatch(self):
        from telegram import CallbackQuery, Chat, Message, User
        from app.handlers.interaction_handler import operation_gate, operation_markup
        from app.handlers.plugin_handler import dynamic_callback_gateway

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                status_text="请选择作品",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
        )
        client = SimpleNamespace(request=AsyncMock(return_value={"actions": []}))
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
            client=client,
        )
        router = Mock()
        router.plugin_route.return_value = route
        router.callback_route.return_value = route
        markup = operation_markup(operation, router, segment=segment)
        encoded = markup.inline_keyboard[0][0].callback_data
        self.assertNotEqual(encoded, "search:select:p1:0")

        context = self.context(router=router)
        callback_query = CallbackQuery(
            id="candidate-click",
            from_user=User(id=1, first_name="Tester", is_bot=False),
            chat_instance="candidate-chat",
            message=Message(
                message_id=92,
                date=datetime.now(timezone.utc),
                chat=Chat(id=10, type="private"),
            ),
            data=encoded,
        )
        callback_query.set_bot(context.application.bot)
        accepted = SimpleNamespace(
            update_id=11,
            effective_chat=SimpleNamespace(id=10),
            effective_user=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(text=None),
            callback_query=callback_query,
        )
        busy_started = asyncio.Event()
        release_busy = asyncio.Event()

        async def delayed_busy_caption(**_kwargs):
            busy_started.set()
            await release_busy.wait()

        context.application.bot.edit_message_caption.side_effect = (
            delayed_busy_caption
        )
        gate_task = asyncio.create_task(operation_gate(accepted, context))
        await asyncio.wait_for(busy_started.wait(), timeout=1)
        self.coordinator.accept_segment_report(
            "search",
            self.report(
                revision=2,
                state="awaiting_input",
                stage="candidate_selection",
                status_text="候选证据已刷新",
                details={"keyboard": [[{
                    "text": "死神：千年血战篇",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
                projection={"text": "候选证据已刷新"},
            ),
        )
        self.assertEqual(
            self.coordinator.get_active_segment("op-1").callback_state,
            "busy",
        )
        release_busy.set()
        await gate_task

        self.assertEqual(accepted.callback_query.data, encoded)
        self.assertEqual(
            self.coordinator.get_active_segment("op-1").callback_generation,
            2,
        )
        context.application.bot.edit_message_caption.assert_awaited_once_with(
            chat_id=10,
            message_id=92,
            caption="正在确认媒体身份…",
            reply_markup=None,
        )
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_callback_gateway(accepted, context)
        request = client.request.await_args
        self.assertEqual(request.args[0], "callback.dispatch")
        self.assertEqual(request.args[1]["namespace"], "search")
        self.assertEqual(request.args[1]["payload"], "select:p1:0")
        released = self.coordinator.get_active_segment("op-1")
        self.assertEqual(released.callback_state, "idle")
        self.assertEqual(released.callback_token, "")
        self.assertGreaterEqual(
            context.application.bot.edit_message_caption.await_count,
            2,
        )

        replay = self.callback_update(encoded, message_id=92)
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(replay, context)
        replay.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_segment_callback_busy_render_failure_does_not_drop_dispatch(self):
        from app.handlers.interaction_handler import (
            callback_dispatch_data,
            operation_gate,
            operation_markup,
        )
        from app.handlers.plugin_handler import dynamic_callback_gateway

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                details={"keyboard": [[{
                    "text": "蜂蜜与四叶草",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "photo",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=92,
        )
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
            client=SimpleNamespace(
                request=AsyncMock(return_value={"actions": []}),
            ),
        )
        router = Mock()
        router.plugin_route.return_value = route
        router.callback_route.return_value = route
        markup = operation_markup(operation, router, segment=segment)
        encoded = markup.inline_keyboard[0][0].callback_data
        update = self.callback_update(encoded, message_id=92)
        context = self.context(router=router)
        context.application.bot.edit_message_caption.side_effect = [
            RuntimeError("telegram timeout"),
            None,
        ]

        await operation_gate(update, context)

        self.assertEqual(
            callback_dispatch_data(update, self.coordinator),
            "search:select:p1:0",
        )
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_callback_gateway(update, context)
        route.client.request.assert_awaited_once()
        released = self.coordinator.get_active_segment("op-1")
        self.assertEqual(released.callback_state, "idle")
        self.assertEqual(released.state, "open")

    async def test_cancelled_gateway_finishes_durable_callback_release(self):
        from app.handlers.interaction_handler import (
            operation_gate,
            operation_markup,
            operation_render_lock,
        )
        from app.handlers.plugin_handler import dynamic_callback_gateway

        operation, segment = self.coordinator.accept_segment_report(
            "search",
            self.report(
                state="awaiting_input",
                stage="candidate_selection",
                details={"keyboard": [[{
                    "text": "蜂蜜与四叶草",
                    "callback_data": "search:select:p1:0",
                }]]},
                segment={
                    "role": "identity",
                    "presentation_kind": "text",
                },
            ),
        )
        segment = self.coordinator.bind_segment_message(
            segment.segment_id,
            owner_plugin_id="search",
            generation=segment.generation,
            chat_id=10,
            message_id=55,
        )
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
            client=SimpleNamespace(
                request=AsyncMock(return_value={"actions": []}),
            ),
        )
        router = Mock()
        router.plugin_route.return_value = route
        router.callback_route.return_value = route
        encoded = operation_markup(
            operation,
            router,
            segment=segment,
        ).inline_keyboard[0][0].callback_data
        update = self.callback_update(encoded, message_id=55)
        context = self.context(router=router)

        await operation_gate(update, context)
        claimed = self.coordinator.get_active_segment("op-1")
        self.assertEqual(claimed.callback_state, "busy")
        self.assertEqual(claimed.callback_token, "search:select:p1:0")

        lock = operation_render_lock(context.application, "op-1")
        await lock.acquire()
        gateway = None
        try:
            with patch(
                "app.handlers.plugin_handler.init.check_user",
                return_value=True,
            ):
                gateway = asyncio.create_task(
                    dynamic_callback_gateway(update, context)
                )
                for _attempt in range(100):
                    release_tasks = [
                        task
                        for task in asyncio.all_tasks()
                        if task.get_name()
                        == "telepiplex-callback-release-11"
                    ]
                    if release_tasks:
                        break
                    await asyncio.sleep(0)
                else:
                    self.fail("callback release task did not start")

                gateway.cancel()
                await asyncio.sleep(0)
                self.assertFalse(gateway.done())
        finally:
            lock.release()

        assert gateway is not None
        with self.assertRaises(asyncio.CancelledError):
            await gateway

        released = self.coordinator.get_active_segment("op-1")
        self.assertEqual(released.callback_state, "idle")
        self.assertEqual(released.callback_token, "")
        route.client.request.assert_awaited_once()
        await asyncio.sleep(0)
        self.assertFalse(any(
            task.get_name() == "telepiplex-callback-release-11"
            for task in asyncio.all_tasks()
        ))

    async def test_button_only_operation_rejects_plain_text_and_allows_owned_callback(self):
        from app.handlers.interaction_handler import operation_gate

        record = self.coordinator.report(
            "search",
            self.report(
                state="awaiting_input",
                stage="release_selection",
                status_text="请选择资源",
                control="exit",
                details={"keyboard": [[{
                    "text": "资源 1",
                    "callback_data": "search:release:1",
                }]]},
            ),
        )
        self.coordinator.set_message_id(record.operation_id, 55)
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        router.plugin_route.return_value = route
        context = self.context(router=router)

        blocked = self.message_update("第二季")
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(blocked, context)
        self.assertIn(
            "当前任务未结束",
            blocked.effective_message.reply_text.await_args.args[0],
        )
        owned = self.callback_update("search:release:1")
        await operation_gate(owned, context)
        owned.callback_query.answer.assert_not_awaited()

        router.callback_route.return_value = SimpleNamespace(plugin_id="download")
        unrelated = self.callback_update("download:path:1")
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(unrelated, context)
        unrelated.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_awaiting_input_allows_text_only_for_matching_open_session(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report(
            "search",
            self.report(
                state="awaiting_input",
                stage="query_input",
                status_text="等待输入片名",
                control="exit",
            ),
        )
        context = self.context()
        context.application.bot_data["telepiplex_plugin_sessions"] = {
            (10, 1): {
                "plugin_id": "search",
                "expires_at": time.time() + 60,
            },
        }

        await operation_gate(self.message_update("后室"), context)

        context.application.bot_data["telepiplex_plugin_sessions"][
            (10, 1)
        ]["plugin_id"] = "download"
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(self.message_update("后室"), context)

    async def test_awaiting_input_rejects_stale_callback_from_same_feature(self):
        from app.handlers.interaction_handler import operation_gate

        record = self.coordinator.report(
            "search",
            self.report(
                state="awaiting_input",
                stage="release_selection",
                status_text="请选择资源",
                control="exit",
                details={"keyboard": [[{
                    "text": "当前资源",
                    "callback_data": "search:release:current",
                }]]},
            ),
        )
        self.coordinator.set_message_id(record.operation_id, 55)
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        router.plugin_route.return_value = route
        update = self.callback_update("search:release:stale")

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context(router=router))

        update.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_awaiting_input_rejects_current_callback_from_old_message(self):
        from app.handlers.interaction_handler import operation_gate

        record = self.coordinator.report(
            "search",
            self.report(
                state="awaiting_input",
                stage="release_selection",
                status_text="请选择资源",
                control="exit",
                details={"keyboard": [[{
                    "text": "资源 1",
                    "callback_data": "search:release:1",
                }]]},
            ),
        )
        self.coordinator.set_message_id(record.operation_id, 55)
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        update = self.callback_update(
            "search:release:1", message_id=40
        )

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context(router=router))

        update.callback_query.answer.assert_awaited_once_with("当前任务进行中")

    async def test_terminal_control_press_is_idempotent_without_feature_dispatch(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("search", self.report())
        self.coordinator.report(
            "search",
            self.report(state="cancelled", status_text="已取消", control="", revision=2),
        )
        update = self.callback_update("host-operation:cancel:op-1")
        router = Mock()

        await operation_control_callback(update, self.context(router=router))

        update.callback_query.answer.assert_awaited_once_with("任务已结束")
        router.plugin_route.assert_not_called()

    async def test_control_dispatches_once_and_persists_returned_revision(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("search", self.report())
        client = SimpleNamespace(request=AsyncMock(return_value=self.report(
            state="cancelling",
            stage="cancelling",
            status_text="正在取消",
            revision=2,
        )))
        route = SimpleNamespace(
            plugin_id="search",
            client=client,
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        update = self.callback_update("host-operation:cancel:op-1")

        await operation_control_callback(update, self.context(router=router))

        client.request.assert_awaited_once()
        self.assertEqual(client.request.await_args.args[0], "operation.control")
        self.assertEqual(self.coordinator.get("op-1").state, "cancelling")

        repeated = self.callback_update("host-operation:cancel:op-1")
        await operation_control_callback(repeated, self.context(router=router))
        client.request.assert_awaited_once()
        repeated.callback_query.answer.assert_awaited_once_with("任务正在取消")

    async def test_control_reloads_owner_after_callback_answer_yields(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("search", self.report())
        source_client = SimpleNamespace(request=AsyncMock())
        target_client = SimpleNamespace(request=AsyncMock(return_value=self.report(
            state="cancelling",
            stage="cancelling",
            status_text="正在取消",
            revision=4,
        )))
        routes = {
            "search": SimpleNamespace(
                plugin_id="search", client=source_client,
                manifest=SimpleNamespace(callbacks=("search",)),
            ),
            "rename": SimpleNamespace(
                plugin_id="rename", client=target_client,
                manifest=SimpleNamespace(callbacks=("rename",)),
            ),
        }
        router = Mock()
        router.plugin_route.side_effect = routes.get
        update = self.callback_update("host-operation:cancel:op-1")

        async def accept_handoff(_text):
            self.coordinator.report("search", self.report(
                state="handed_off",
                stage="handoff_rename",
                status_text="已交给 rename",
                revision=2,
                next_plugin_id="rename",
            ))
            self.coordinator.report("rename", self.report(
                state="running",
                stage="organizing",
                status_text="正在整理",
                revision=3,
            ))

        update.callback_query.answer.side_effect = accept_handoff
        await operation_control_callback(update, self.context(router=router))

        source_client.request.assert_not_awaited()
        target_client.request.assert_awaited_once()
        self.assertEqual(
            target_client.request.await_args.args[1]["revision"], 3
        )

    async def test_control_retries_new_owner_when_old_owner_rpc_rejects_handoff(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("search", self.report())

        async def old_owner_rejects(*_args, **_kwargs):
            self.coordinator.report("search", self.report(
                state="handed_off",
                stage="handoff_rename",
                status_text="已交给 rename",
                revision=2,
                next_plugin_id="rename",
            ))
            self.coordinator.report("rename", self.report(
                state="running",
                stage="organizing",
                status_text="正在整理",
                revision=3,
            ))
            raise RuntimeError("owner_mismatch")

        source_client = SimpleNamespace(request=AsyncMock(
            side_effect=old_owner_rejects
        ))
        target_client = SimpleNamespace(request=AsyncMock(return_value=self.report(
            state="cancelling",
            stage="cancelling",
            status_text="正在取消",
            revision=4,
        )))
        routes = {
            "search": SimpleNamespace(
                plugin_id="search", client=source_client,
                manifest=SimpleNamespace(callbacks=("search",)),
            ),
            "rename": SimpleNamespace(
                plugin_id="rename", client=target_client,
                manifest=SimpleNamespace(callbacks=("rename",)),
            ),
        }
        router = Mock()
        router.plugin_route.side_effect = routes.get

        await operation_control_callback(
            self.callback_update("host-operation:cancel:op-1"),
            self.context(router=router),
        )

        source_client.request.assert_awaited_once()
        target_client.request.assert_awaited_once()
        self.assertEqual(
            target_client.request.await_args.args[1]["revision"], 3
        )

    async def test_control_follows_two_consecutive_handoffs(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("search", self.report())

        async def handoff_to_download(*_args, **_kwargs):
            self.coordinator.report("search", self.report(
                state="handed_off", stage="handoff_download",
                revision=2, next_plugin_id="download",
            ))
            self.coordinator.report("download", self.report(
                state="running", stage="downloading", revision=3,
            ))
            raise RuntimeError("owner_mismatch")

        async def handoff_to_rename(*_args, **_kwargs):
            self.coordinator.report("download", self.report(
                state="handed_off", stage="handoff_rename",
                revision=4, next_plugin_id="rename",
            ))
            self.coordinator.report("rename", self.report(
                state="running", stage="organizing", revision=5,
            ))
            raise RuntimeError("owner_mismatch")

        clients = {
            "search": SimpleNamespace(request=AsyncMock(
                side_effect=handoff_to_download
            )),
            "download": SimpleNamespace(request=AsyncMock(
                side_effect=handoff_to_rename
            )),
            "rename": SimpleNamespace(request=AsyncMock(
                return_value=self.report(
                    state="cancelling", stage="cancelling",
                    status_text="正在取消", revision=6,
                )
            )),
        }
        routes = {
            plugin_id: SimpleNamespace(
                plugin_id=plugin_id,
                client=client,
                manifest=SimpleNamespace(callbacks=(plugin_id,)),
            )
            for plugin_id, client in clients.items()
        }
        router = Mock()
        router.plugin_route.side_effect = routes.get

        await operation_control_callback(
            self.callback_update("host-operation:cancel:op-1"),
            self.context(router=router),
        )

        for client in clients.values():
            client.request.assert_awaited_once()
        self.assertEqual(
            clients["rename"].request.await_args.args[1]["revision"], 5
        )

    async def test_failed_status_edit_sends_replacement_and_persists_message_id(self):
        from app.handlers import interaction_handler
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report())
        record = self.coordinator.set_message_id(record.operation_id, 12)
        context = self.context()
        context.application.bot.edit_message_text.side_effect = BadRequest(
            "message missing access_token=should-not-leak"
        )
        context.application.bot.send_message.return_value = SimpleNamespace(message_id=34)
        logger = Mock()

        with patch.object(interaction_handler.init, "logger", logger):
            await render_operation(context.application, Mock(), record)

        context.application.bot.edit_message_text.assert_awaited_once()
        context.application.bot.send_message.assert_awaited_once()
        self.assertEqual(self.coordinator.get("op-1").message_id, 34)
        warning = logger.warn.call_args.args[0]
        self.assertIn("message missing", warning)
        self.assertIn("message_id=12", warning)
        self.assertIn("message_kind=text", warning)
        self.assertIn("access_token=***redacted***", warning)
        self.assertNotIn("should-not-leak", warning)

    async def test_stale_supplied_snapshot_cannot_overwrite_newer_cursor_owner(self):
        from app.handlers.interaction_handler import render_operation

        stale = self.coordinator.report("search", self.report())
        self.coordinator.report("search", self.report(
            state="handed_off",
            stage="handoff_download",
            revision=2,
            next_plugin_id="download",
        ))
        self.coordinator.report("download", self.report(
            stage="downloading",
            revision=3,
        ))
        context = self.context()
        context.application.bot.send_message.return_value = SimpleNamespace(
            message_id=88
        )

        rendered = await render_operation(
            context.application, Mock(), stale
        )

        self.assertEqual(rendered, 88)
        current = self.coordinator.get("op-1")
        self.assertEqual(current.plugin_id, "download")
        self.assertEqual(current.revision, 3)
        self.assertIsNone(current.message_id)

    async def test_unchanged_status_edit_does_not_send_duplicate_message(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report())
        record = self.coordinator.set_message_id(record.operation_id, 12)
        context = self.context()
        context.application.bot.edit_message_text.side_effect = BadRequest(
            "Message is not modified"
        )

        message_id = await render_operation(
            context.application, Mock(), record
        )

        self.assertEqual(message_id, 12)
        context.application.bot.send_message.assert_not_awaited()

    async def test_status_renderer_sends_candidate_photo(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选 1",
            control="exit",
            details={"photo_url": "https://image.example/poster.jpg"},
        ))
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=55)
        )

        message_id = await render_operation(
            context.application, Mock(), record
        )

        self.assertEqual(message_id, 55)
        context.application.bot.send_photo.assert_awaited_once()
        self.assertEqual(
            context.application.bot.send_photo.await_args.kwargs["photo"],
            "https://image.example/poster.jpg",
        )
        context.application.bot.send_message.assert_not_awaited()

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_status_renderer_sends_candidate_poster_grid(
        self,
        build_grid,
    ):
        from io import BytesIO
        from app.handlers.interaction_handler import render_operation

        grid = BytesIO(b"grid")
        grid.name = "grid.jpg"
        build_grid.return_value = grid
        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选",
            control="exit",
            details={
                "poster_items": [{
                    "number": 1,
                    "title": "候选一",
                    "poster_url": "https://image.example/one.jpg",
                }],
                "parse_mode": "HTML",
            },
        ))
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=56)
        )

        message_id = await render_operation(
            context.application,
            Mock(),
            record,
        )

        self.assertEqual(message_id, 56)
        self.assertIs(
            context.application.bot.send_photo.await_args.kwargs["photo"],
            grid,
        )
        self.assertEqual(
            context.application.bot.send_photo.await_args.kwargs["parse_mode"],
            "HTML",
        )

    @patch(
        "app.handlers.interaction_handler.build_poster_grid",
        side_effect=RuntimeError(
            "poster_grid_no_images "
            "https://img9.doubanio.com/private/poster.jpg "
            "http_status:403"
        ),
    )
    async def test_status_renderer_grid_failure_falls_back_to_text(
        self,
        _build_grid,
    ):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选文本",
            control="exit",
            details={
                "poster_items": [{
                    "number": 1,
                    "title": "想见你",
                    "poster_url": "https://image.example/one.jpg",
                }],
            },
        ))
        context = self.context()
        context.application.bot.send_photo = AsyncMock()
        context.application.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=58)
        )
        logger = Mock()

        with patch("app.handlers.interaction_handler.init.logger", logger):
            message_id = await render_operation(
                context.application,
                Mock(),
                record,
            )

        self.assertEqual(message_id, 58)
        context.application.bot.send_photo.assert_not_awaited()
        context.application.bot.send_message.assert_awaited_once()
        self.assertEqual(
            context.application.bot.send_message.await_args.kwargs["text"],
            "候选文本",
        )
        logged = logger.warn.call_args.args[0]
        self.assertIn("http_status:403", logged)
        self.assertNotIn("img9.doubanio.com/private", logged)

    @patch("app.handlers.interaction_handler.build_poster_grid")
    async def test_status_renderer_bounds_long_html_photo_caption(
        self, build_grid
    ):
        from io import BytesIO
        from app.handlers.interaction_handler import render_operation

        grid = BytesIO(b"grid")
        grid.name = "grid.jpg"
        build_grid.return_value = grid
        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="<b>" + ("候选内容" * 400) + "</b>",
            control="exit",
            details={
                "poster_items": [{
                    "number": 1,
                    "title": "候选一",
                    "poster_url": "https://image.example/one.jpg",
                }],
                "parse_mode": "HTML",
            },
        ))
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=57)
        )

        await render_operation(context.application, Mock(), record)

        kwargs = context.application.bot.send_photo.await_args.kwargs
        self.assertLessEqual(len(kwargs["caption"]), 1024)
        self.assertNotIn("<b>", kwargs["caption"])
        self.assertNotIn("parse_mode", kwargs)

    async def test_status_renderer_edits_existing_candidate_photo(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选 2",
            control="exit",
            details={"photo_url": "https://image.example/poster-2.jpg"},
        ))
        record = self.coordinator.set_message_id(
            record.operation_id, 44, "photo"
        )
        context = self.context()
        context.application.bot.edit_message_media = AsyncMock()

        message_id = await render_operation(
            context.application, Mock(), record
        )

        self.assertEqual(message_id, 44)
        context.application.bot.edit_message_media.assert_awaited_once()
        context.application.bot.edit_message_text.assert_not_awaited()

    async def test_status_renderer_replaces_photo_before_text_progress(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选 1",
            control="exit",
            details={"photo_url": "https://image.example/poster.jpg"},
        ))
        record = self.coordinator.set_message_id(
            record.operation_id, 55, "photo"
        )
        record = self.coordinator.report("search", self.report(
            revision=2,
            state="running",
            stage="prowlarr_search",
            status_text="正在搜索片源",
            control="cancel",
            details={},
        ))
        context = self.context()
        context.application.bot.send_message.return_value = SimpleNamespace(
            message_id=56
        )

        message_id = await render_operation(
            context.application, Mock(), record
        )

        self.assertEqual(message_id, 56)
        context.application.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=10,
            message_id=55,
            reply_markup=None,
        )
        context.application.bot.edit_message_text.assert_not_awaited()
        context.application.bot.send_message.assert_awaited_once()
        current = self.coordinator.get(record.operation_id)
        self.assertEqual((current.message_id, current.message_kind), (56, "text"))

    async def test_terminal_operation_never_renders_stale_feature_keyboard(self):
        from app.handlers.interaction_handler import operation_markup

        record = self.coordinator.report("search", self.report(
            state="completed",
            stage="completed",
            status_text="已完成",
            control="",
            details={"keyboard": [[{
                "text": "旧选项",
                "callback_data": "search:release:stale",
            }]]},
        ))
        route = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route

        self.assertIsNone(operation_markup(record, router))

    async def test_status_photo_failure_falls_back_to_text(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="candidate_selection",
            status_text="候选 1",
            control="exit",
            details={"photo_url": "https://image.example/poster.jpg"},
        ))
        context = self.context()
        context.application.bot.send_photo = AsyncMock(
            side_effect=RuntimeError("image unavailable")
        )
        context.application.bot.send_message.return_value = SimpleNamespace(
            message_id=56
        )

        message_id = await render_operation(
            context.application, Mock(), record
        )

        self.assertEqual(message_id, 56)
        context.application.bot.send_photo.assert_awaited_once()
        context.application.bot.send_message.assert_awaited_once()

    async def test_status_renderer_accepts_only_current_feature_keyboard(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="release_selection",
            status_text="请选择资源",
            control="exit",
            details={"keyboard": [[
                {"text": "资源 1", "callback_data": "search:release:1"},
                {"text": "越权", "callback_data": "download:path:1"},
            ]]},
        ))
        router = Mock()
        router.plugin_route.return_value = SimpleNamespace(
            plugin_id="search",
            manifest=SimpleNamespace(callbacks=("search",)),
        )
        context = self.context(router=router)

        await render_operation(context.application, router, record)

        markup = context.application.bot.send_message.await_args.kwargs["reply_markup"]
        buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            [(button.text, button.callback_data) for button in buttons],
            [
                ("资源 1", "search:release:1"),
                ("退出", "host-operation:exit:op-1"),
            ],
        )

    async def test_startup_recovery_confirms_each_operation_and_interrupts_missing_one(self):
        from app.handlers.interaction_handler import recover_active_operations

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
        snapshot = self.report(
            state="running",
            stage="provider_lookup",
            status_text="已恢复查询",
            revision=2,
        )
        client = SimpleNamespace(request=AsyncMock(side_effect=[
            {"operations": [snapshot]},
            {"operations": []},
        ]))
        route = SimpleNamespace(plugin_id="search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        result = await recover_active_operations(
            context.application, router, self.coordinator
        )

        self.assertEqual(result["confirmed"], ["op-1"])
        self.assertEqual(self.coordinator.get("op-1").status_text, "已恢复查询")
        self.assertEqual(self.coordinator.get("op-2").state, "interrupted")
        self.assertIsNone(self.coordinator.active(20, 2))

    async def test_startup_recovery_restores_awaiting_input_session_route(self):
        from app.handlers.interaction_handler import recover_active_operations

        self.coordinator.report("search", self.report(
            state="awaiting_input",
            stage="release_selection",
            control="exit",
        ))
        snapshot = self.report(
            state="awaiting_input",
            stage="release_selection",
            control="exit",
            revision=2,
        )
        client = SimpleNamespace(
            request=AsyncMock(return_value={"operations": [snapshot]})
        )
        route = SimpleNamespace(plugin_id="search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        await recover_active_operations(
            context.application, router, self.coordinator
        )

        session = context.application.bot_data[
            "telepiplex_plugin_sessions"
        ][(10, 1)]
        self.assertEqual(session["plugin_id"], "search")
        self.assertGreater(session["expires_at"], time.time())

    async def test_startup_recovery_keeps_gate_when_snapshot_temporarily_fails(self):
        from app.handlers.interaction_handler import recover_active_operations

        original = self.coordinator.report("search", self.report())
        client = SimpleNamespace(
            request=AsyncMock(side_effect=TimeoutError("snapshot timeout"))
        )
        route = SimpleNamespace(plugin_id="search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        result = await recover_active_operations(
            context.application,
            router,
            self.coordinator,
        )

        self.assertEqual(result["deferred"], ["op-1"])
        active = self.coordinator.active(10, 1)
        self.assertEqual(active.operation_id, original.operation_id)
        self.assertEqual(active.state, "running")
        self.assertIsNotNone(active.message_id)

    async def test_recovery_does_not_interrupt_operation_created_mid_pass(self):
        from app.handlers.interaction_handler import recover_active_operations

        self.coordinator.report("search", self.report())

        async def snapshot(*_args, **_kwargs):
            self.coordinator.report("download", self.report(
                operation_id="op-new",
                chat_id=20,
                user_id=2,
            ))
            return {"operations": []}

        route = SimpleNamespace(
            plugin_id="search",
            client=SimpleNamespace(request=AsyncMock(side_effect=snapshot)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        await recover_active_operations(
            context.application, router, self.coordinator
        )

        self.assertEqual(self.coordinator.get("op-1").state, "interrupted")
        self.assertEqual(self.coordinator.get("op-new").state, "running")
        self.assertEqual(self.coordinator.active(20, 2).operation_id, "op-new")

    async def test_recovery_defers_operation_that_hands_off_mid_snapshot(self):
        from app.handlers.interaction_handler import recover_active_operations

        self.coordinator.report("search", self.report())

        async def snapshot(*_args, **_kwargs):
            self.coordinator.report("search", self.report(
                state="handed_off",
                stage="handoff_download",
                control="cancel",
                revision=2,
                next_plugin_id="download",
            ))
            self.coordinator.report("download", self.report(
                state="running",
                stage="submission",
                control="cancel",
                revision=3,
            ))
            return {"operations": []}

        media_route = SimpleNamespace(
            plugin_id="search",
            client=SimpleNamespace(request=AsyncMock(side_effect=snapshot)),
        )
        router = Mock()
        router.plugin_route.return_value = media_route
        context = self.context(router=router)

        result = await recover_active_operations(
            context.application, router, self.coordinator
        )

        current = self.coordinator.get("op-1")
        self.assertEqual(current.plugin_id, "download")
        self.assertEqual(current.state, "running")
        self.assertEqual(result["deferred"], ["op-1"])
        self.assertEqual(result["interrupted"], [])

    async def test_deferred_recovery_retries_until_snapshot_is_authoritative(self):
        from app.handlers.interaction_handler import reconcile_deferred_operations

        self.coordinator.report("search", self.report())
        client = SimpleNamespace(request=AsyncMock(side_effect=[
            TimeoutError("snapshot timeout"),
            {"operations": []},
        ]))
        route = SimpleNamespace(plugin_id="search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        result = await reconcile_deferred_operations(
            context.application,
            router,
            self.coordinator,
            retry_interval=0,
        )

        self.assertEqual(client.request.await_count, 2)
        self.assertEqual(result["deferred"], [])
        self.assertEqual(self.coordinator.get("op-1").state, "interrupted")
        self.assertIsNone(self.coordinator.active(10, 1))

    async def test_permanent_snapshot_failure_eventually_releases_gate(self):
        from app.handlers.interaction_handler import reconcile_deferred_operations

        self.coordinator.report("search", self.report())
        client = SimpleNamespace(request=AsyncMock(
            side_effect=RuntimeError("snapshot protocol unavailable")
        ))
        route = SimpleNamespace(plugin_id="search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        result = await reconcile_deferred_operations(
            context.application,
            router,
            self.coordinator,
            retry_interval=0,
            max_attempts=2,
        )

        self.assertEqual(client.request.await_count, 2)
        self.assertEqual(result["deferred"], [])
        self.assertEqual(self.coordinator.get("op-1").state, "interrupted")
        self.assertIsNone(self.coordinator.active(10, 1))


if __name__ == "__main__":
    unittest.main()

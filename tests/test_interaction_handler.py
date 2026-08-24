import asyncio
import json
import logging
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest
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

        logger = Logger(
            config_root=Path(self.temp.name) / "diagnostics",
            session_id="INCOMING-COMMAND",
        )
        update = self.message_update(
            "/search 蜂蜜与四叶草 access_token=command-secret"
        )
        try:
            with patch.object(interaction_handler.init, "logger", logger):
                await operation_gate(update, self.context())
        finally:
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, "_telepiplex_handler_kind", ""):
                    logging.getLogger().removeHandler(handler)
                    handler.close()

        event = json.loads(logger.session.machine_path.read_text(encoding="utf-8"))
        assert event["event"]["name"] == "telegram.interaction.received"
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

        update.callback_query.answer.assert_awaited_once_with("当前任务执行中")

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
        stale.callback_query.answer.assert_awaited_once_with("当前任务执行中")

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
            "等待按钮",
            blocked.effective_message.reply_text.await_args.args[0],
        )
        owned = self.callback_update("search:release:1")
        await operation_gate(owned, context)
        owned.callback_query.answer.assert_not_awaited()

        router.callback_route.return_value = SimpleNamespace(plugin_id="download")
        unrelated = self.callback_update("download:path:1")
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(unrelated, context)
        unrelated.callback_query.answer.assert_awaited_once_with("当前任务执行中")

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

        update.callback_query.answer.assert_awaited_once_with("当前任务执行中")

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

        update.callback_query.answer.assert_awaited_once_with("当前任务执行中")

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

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop


class InteractionHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.core.interaction_coordinator import InteractionCoordinator

        self.temp = tempfile.TemporaryDirectory()
        self.coordinator = InteractionCoordinator(Path(self.temp.name) / "core.db")

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
            edit_message_text=AsyncMock(),
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
            effective_message=SimpleNamespace(text=text),
            callback_query=None,
        )

    @staticmethod
    def callback_update(data: str):
        query = SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
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

        self.coordinator.report("media-search", self.report())
        router = Mock()
        context = self.context(router=router)

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(self.message_update("/search test"), context)

        router.command_route.assert_not_called()

    async def test_running_operation_rejects_unrelated_callback_with_toast(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report("media-search", self.report())
        update = self.callback_update("plex:scan")

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context())

        update.callback_query.answer.assert_awaited_once_with("当前任务执行中")

    async def test_awaiting_input_allows_plain_text_and_owned_callback_only(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report(
            "media-search",
            self.report(
                state="awaiting_input",
                stage="release_selection",
                status_text="请选择资源",
                control="exit",
                details={"keyboard": [[{
                    "text": "资源 1",
                    "callback_data": "media-search:release:1",
                }]]},
            ),
        )
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        router.plugin_route.return_value = route
        context = self.context(router=router)

        await operation_gate(self.message_update("第二季"), context)
        owned = self.callback_update("media-search:release:1")
        await operation_gate(owned, context)
        owned.callback_query.answer.assert_not_awaited()

        router.callback_route.return_value = SimpleNamespace(plugin_id="open115")
        unrelated = self.callback_update("open115:path:1")
        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(unrelated, context)
        unrelated.callback_query.answer.assert_awaited_once_with("当前任务执行中")

    async def test_awaiting_input_rejects_stale_callback_from_same_feature(self):
        from app.handlers.interaction_handler import operation_gate

        self.coordinator.report(
            "media-search",
            self.report(
                state="awaiting_input",
                stage="release_selection",
                status_text="请选择资源",
                control="exit",
                details={"keyboard": [[{
                    "text": "当前资源",
                    "callback_data": "media-search:release:current",
                }]]},
            ),
        )
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        router.plugin_route.return_value = route
        update = self.callback_update("media-search:release:stale")

        with self.assertRaises(ApplicationHandlerStop):
            await operation_gate(update, self.context(router=router))

        update.callback_query.answer.assert_awaited_once_with("当前任务执行中")

    async def test_terminal_control_press_is_idempotent_without_feature_dispatch(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("media-search", self.report())
        self.coordinator.report(
            "media-search",
            self.report(state="cancelled", status_text="已取消", control="", revision=2),
        )
        update = self.callback_update("core-operation:cancel:op-1")
        router = Mock()

        await operation_control_callback(update, self.context(router=router))

        update.callback_query.answer.assert_awaited_once_with("任务已结束")
        router.plugin_route.assert_not_called()

    async def test_control_dispatches_once_and_persists_returned_revision(self):
        from app.handlers.interaction_handler import operation_control_callback

        self.coordinator.report("media-search", self.report())
        client = SimpleNamespace(request=AsyncMock(return_value=self.report(
            state="cancelling",
            stage="cancelling",
            status_text="正在取消",
            revision=2,
        )))
        route = SimpleNamespace(
            plugin_id="media-search",
            client=client,
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        router = Mock()
        router.plugin_route.return_value = route
        update = self.callback_update("core-operation:cancel:op-1")

        await operation_control_callback(update, self.context(router=router))

        client.request.assert_awaited_once()
        self.assertEqual(client.request.await_args.args[0], "operation.control")
        self.assertEqual(self.coordinator.get("op-1").state, "cancelling")

        repeated = self.callback_update("core-operation:cancel:op-1")
        await operation_control_callback(repeated, self.context(router=router))
        client.request.assert_awaited_once()
        repeated.callback_query.answer.assert_awaited_once_with("任务正在取消")

    async def test_failed_status_edit_sends_replacement_and_persists_message_id(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("media-search", self.report())
        record = self.coordinator.set_message_id(record.operation_id, 12)
        context = self.context()
        context.application.bot.edit_message_text.side_effect = BadRequest("message missing")
        context.application.bot.send_message.return_value = SimpleNamespace(message_id=34)

        await render_operation(context.application, Mock(), record)

        context.application.bot.edit_message_text.assert_awaited_once()
        context.application.bot.send_message.assert_awaited_once()
        self.assertEqual(self.coordinator.get("op-1").message_id, 34)

    async def test_status_renderer_accepts_only_current_feature_keyboard(self):
        from app.handlers.interaction_handler import render_operation

        record = self.coordinator.report("media-search", self.report(
            state="awaiting_input",
            stage="release_selection",
            status_text="请选择资源",
            control="exit",
            details={"keyboard": [[
                {"text": "资源 1", "callback_data": "media-search:release:1"},
                {"text": "越权", "callback_data": "open115:path:1"},
            ]]},
        ))
        router = Mock()
        router.plugin_route.return_value = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
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
                ("资源 1", "media-search:release:1"),
                ("退出", "core-operation:exit:op-1"),
            ],
        )

    async def test_startup_recovery_confirms_each_operation_and_interrupts_missing_one(self):
        from app.handlers.interaction_handler import recover_active_operations

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
        route = SimpleNamespace(plugin_id="media-search", client=client)
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

        self.coordinator.report("media-search", self.report(
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
        route = SimpleNamespace(plugin_id="media-search", client=client)
        router = Mock()
        router.plugin_route.return_value = route
        context = self.context(router=router)

        await recover_active_operations(
            context.application, router, self.coordinator
        )

        session = context.application.bot_data[
            "telepiplex_plugin_sessions"
        ][(10, 1)]
        self.assertEqual(session["plugin_id"], "media-search")
        self.assertGreater(session["expires_at"], time.time())

    async def test_startup_recovery_keeps_gate_when_snapshot_temporarily_fails(self):
        from app.handlers.interaction_handler import recover_active_operations

        original = self.coordinator.report("media-search", self.report())
        client = SimpleNamespace(
            request=AsyncMock(side_effect=TimeoutError("snapshot timeout"))
        )
        route = SimpleNamespace(plugin_id="media-search", client=client)
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

    async def test_deferred_recovery_retries_until_snapshot_is_authoritative(self):
        from app.handlers.interaction_handler import reconcile_deferred_operations

        self.coordinator.report("media-search", self.report())
        client = SimpleNamespace(request=AsyncMock(side_effect=[
            TimeoutError("snapshot timeout"),
            {"operations": []},
        ]))
        route = SimpleNamespace(plugin_id="media-search", client=client)
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


if __name__ == "__main__":
    unittest.main()

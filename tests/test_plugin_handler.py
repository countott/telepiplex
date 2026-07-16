import asyncio
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class FakeManager:
    def __init__(self):
        self.calls = []
        self.router = Mock()
        self.candidates = []
        self.updates = []
        self.configurations = []
        self.configs = {
            "media-search": {
                "ai": {"api_key": "kept-secret", "model": "old-model"},
                "search": {"prowlarr": {"base_url": "http://old"}},
            }
        }

    async def _operation(self, name, value):
        self.calls.append((name, value))
        return SimpleNamespace(
            state="active",
            plugin_id=str(value).split("@", 1)[0],
            version="1.0.0",
            message=f"{name} complete",
            details={},
        )

    async def install(self, value):
        return await self._operation("install", value)

    async def update(self, value):
        return await self._operation("update", value)

    async def enable(self, value):
        return await self._operation("enable", value)

    async def disable(self, value):
        return await self._operation("disable", value)

    async def rollback(self, value):
        return await self._operation("rollback", value)

    async def remove(self, value):
        return await self._operation("remove", value)

    def status(self, value):
        self.calls.append(("status", value))
        return {"plugin_id": value, "state": "active", "version": "1.0.0"}

    def doctor(self):
        self.calls.append(("doctor", None))
        return [{"plugin_id": "echo", "state": "active", "version": "1.0.0"}]

    async def available_plugins(self):
        return list(self.candidates)

    async def available_updates(self):
        return list(self.updates)

    def config(self, plugin_id):
        return {"plugin_id": plugin_id, "config": deepcopy(self.configs[plugin_id])}

    async def configure(self, plugin_id, value, should_cancel=None):
        self.configurations.append((plugin_id, deepcopy(value)))
        self.configs[plugin_id] = deepcopy(value)
        return SimpleNamespace(
            state="active", plugin_id=plugin_id, version="1.0.1",
            message="Feature configuration saved and reloaded",
            details={"restarted": True},
        )

    def config_state(self, plugin_id):
        return {
            "plugin_id": plugin_id,
            "state": "configurable",
            "configurable": True,
            "command": "configure_echo",
        }


class PluginHandlerTest(unittest.IsolatedAsyncioTestCase):
    def _request(self, args, user_id=1):
        update = Mock()
        update.update_id = 99
        update.effective_user.id = user_id
        update.effective_chat.id = 10
        update.effective_message.text = "/plugin " + " ".join(args)
        update.effective_message.reply_text = AsyncMock()
        update.callback_query = None
        manager = FakeManager()
        context = Mock()
        context.args = args
        context.user_data = {}
        context.application.bot_data = {"telepiplex_plugin_manager": manager}
        return update, context, manager

    async def test_feature_config_patch_is_merged_and_reloaded_by_core(self):
        from app.handlers.plugin_handler import handle_feature_result

        update, context, manager = self._request([], user_id=1)
        context.application.bot_data["telepiplex_plugin_sessions"] = {
            (10, 1): {"plugin_id": "media-search", "expires_at": 9999999999},
        }
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        result = {
            "actions": [],
            "session": {"state": "close"},
            "config_patch": {"ai": {"model": "new-model"}},
        }

        await handle_feature_result(update, context, route, result)

        self.assertEqual(manager.configurations, [(
            "media-search",
            {
                "ai": {"api_key": "kept-secret", "model": "new-model"},
                "search": {"prowlarr": {"base_url": "http://old"}},
            },
        )])
        self.assertNotIn("telepiplex_plugin_sessions", context.application.bot_data)
        message = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("已写入并重新加载", message)
        self.assertNotIn("kept-secret", message)

    async def test_successful_feature_config_patch_completes_operation(self):
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.handlers.plugin_handler import handle_feature_result

        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = InteractionCoordinator(Path(tmpdir) / "core.db")
            self.addCleanup(coordinator.close)
            update, context, _manager = self._request([], user_id=1)
            context.application.bot_data["telepiplex_interaction_coordinator"] = coordinator
            route = SimpleNamespace(
                plugin_id="media-search",
                manifest=SimpleNamespace(callbacks=("media-search",)),
            )

            await handle_feature_result(update, context, route, {
                "actions": [],
                "session": {"state": "close"},
                "config_patch": {"ai": {"model": "new-model"}},
                "operation": {
                    "operation_id": "op-config",
                    "chat_id": 10,
                    "user_id": 1,
                    "state": "running",
                    "stage": "config_apply",
                    "status_text": "正在保存配置",
                    "control": "cancel",
                    "revision": 4,
                },
            })

            record = coordinator.get("op-config")
            self.assertEqual(record.state, "completed")
            self.assertEqual(record.stage, "config_apply")
            self.assertIsNone(coordinator.active(10, 1))

    async def test_config_apply_rollback_is_owned_by_core_task(self):
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.interaction_handler import operation_control_callback
        from app.handlers.plugin_handler import handle_feature_result

        class BlockingManager(FakeManager):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()

            async def configure(self, plugin_id, value, should_cancel=None):
                self.configurations.append((plugin_id, deepcopy(value)))
                self.started.set()
                while should_cancel is not None and not should_cancel():
                    await asyncio.sleep(0)
                raise PluginOperationError(
                    "config_cancelled", "configuration cancelled"
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = InteractionCoordinator(Path(tmpdir) / "core.db")
            self.addCleanup(coordinator.close)
            update, context, _unused = self._request([], user_id=1)
            manager = BlockingManager()
            route = SimpleNamespace(
                plugin_id="media-search",
                manifest=SimpleNamespace(callbacks=("media-search",)),
                client=SimpleNamespace(request=AsyncMock()),
            )
            router = Mock()
            router.plugin_route.return_value = route
            context.application.bot = SimpleNamespace(
                send_message=AsyncMock(
                    return_value=SimpleNamespace(message_id=80)
                ),
                edit_message_text=AsyncMock(),
            )
            context.application.bot_data.update({
                "telepiplex_plugin_manager": manager,
                "telepiplex_plugin_router": router,
                "telepiplex_interaction_coordinator": coordinator,
            })
            applying = asyncio.create_task(handle_feature_result(
                update,
                context,
                route,
                {
                    "actions": [],
                    "session": {"state": "close"},
                    "config_patch": {"ai": {"model": "new-model"}},
                    "operation": {
                        "operation_id": "op-config-rollback",
                        "chat_id": 10,
                        "user_id": 1,
                        "state": "running",
                        "stage": "config_apply",
                        "status_text": "正在保存配置",
                        "control": "cancel",
                        "revision": 4,
                    },
                },
            ))
            await manager.started.wait()
            record = coordinator.get("op-config-rollback")
            self.assertEqual(record.control, "rollback")

            query = SimpleNamespace(
                data="core-operation:rollback:op-config-rollback",
                answer=AsyncMock(),
            )
            control_update = SimpleNamespace(
                effective_chat=SimpleNamespace(id=10),
                effective_user=SimpleNamespace(id=1),
                callback_query=query,
            )
            await operation_control_callback(control_update, context)
            await applying

            self.assertEqual(
                coordinator.get("op-config-rollback").state,
                "rolled_back",
            )
            self.assertIsNone(coordinator.active(10, 1))
            route.client.request.assert_not_awaited()
            query.answer.assert_awaited_once_with("正在回滚配置...")

    async def test_feature_result_persists_operation_and_injects_missing_exit(self):
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.handlers.plugin_handler import handle_feature_result

        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = InteractionCoordinator(Path(tmpdir) / "core.db")
            self.addCleanup(coordinator.close)
            update, context, _manager = self._request([], user_id=1)
            update.effective_message.reply_text.return_value = SimpleNamespace(message_id=55)
            context.application.bot_data["telepiplex_interaction_coordinator"] = coordinator
            context.application.bot = SimpleNamespace(
                send_message=AsyncMock(), edit_message_text=AsyncMock()
            )
            route = SimpleNamespace(
                plugin_id="media-search",
                manifest=SimpleNamespace(callbacks=("media-search",)),
            )

            await handle_feature_result(update, context, route, {
                "actions": [{"kind": "send_message", "text": "请输入片名"}],
                "session": {"state": "open"},
                "operation": {
                    "operation_id": "op-1",
                    "chat_id": 10,
                    "user_id": 1,
                    "state": "awaiting_input",
                    "stage": "query",
                    "status_text": "等待片名",
                    "control": "exit",
                    "revision": 1,
                },
            })

            record = coordinator.active(10, 1)
            self.assertEqual(record.message_id, 55)
            markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
            button = markup.inline_keyboard[-1][0]
            self.assertEqual(button.text, "退出")
            self.assertEqual(button.callback_data, "core-operation:exit:op-1")

    async def test_closing_session_releases_awaiting_operation(self):
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.handlers.plugin_handler import handle_feature_result

        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = InteractionCoordinator(Path(tmpdir) / "core.db")
            self.addCleanup(coordinator.close)
            coordinator.report("media-search", {
                "operation_id": "op-1",
                "chat_id": 10,
                "user_id": 1,
                "state": "awaiting_input",
                "stage": "query",
                "status_text": "等待片名",
                "control": "exit",
                "revision": 1,
            })
            update, context, _manager = self._request([], user_id=1)
            context.application.bot_data.update({
                "telepiplex_interaction_coordinator": coordinator,
                "telepiplex_plugin_sessions": {
                    (10, 1): {
                        "plugin_id": "media-search",
                        "expires_at": 9999999999,
                    },
                },
            })
            context.application.bot = SimpleNamespace(
                send_message=AsyncMock(), edit_message_text=AsyncMock()
            )
            route = SimpleNamespace(
                plugin_id="media-search",
                manifest=SimpleNamespace(callbacks=("media-search",)),
            )

            await handle_feature_result(update, context, route, {
                "actions": [{"kind": "send_message", "text": "已退出"}],
                "session": {"state": "close"},
            })

            self.assertIsNone(coordinator.active(10, 1))
            self.assertEqual(coordinator.get("op-1").state, "cancelled")

    async def test_feature_result_persists_only_current_prompt_callbacks(self):
        from app.core.interaction_coordinator import InteractionCoordinator
        from app.handlers.interaction_handler import operation_gate
        from app.handlers.plugin_handler import handle_feature_result
        from telegram.ext import ApplicationHandlerStop

        with tempfile.TemporaryDirectory() as tmpdir:
            coordinator = InteractionCoordinator(Path(tmpdir) / "core.db")
            self.addCleanup(coordinator.close)
            update, context, _manager = self._request([], user_id=1)
            update.effective_message.reply_text.return_value = SimpleNamespace(
                message_id=55
            )
            context.application.bot = SimpleNamespace(
                send_message=AsyncMock(), edit_message_text=AsyncMock()
            )
            route = SimpleNamespace(
                plugin_id="media-search",
                manifest=SimpleNamespace(callbacks=("media-search",)),
            )
            router = Mock()
            router.plugin_route.return_value = route
            context.application.bot_data.update({
                "telepiplex_interaction_coordinator": coordinator,
                "telepiplex_plugin_router": router,
            })

            await handle_feature_result(update, context, route, {
                "actions": [{
                    "kind": "send_message",
                    "text": "请选择",
                    "data": {"keyboard": [[{
                        "text": "当前选项",
                        "callback_data": "media-search:release:current",
                    }]]},
                }],
                "session": {"state": "open"},
                "operation": {
                    "operation_id": "op-current-prompt",
                    "chat_id": 10,
                    "user_id": 1,
                    "state": "awaiting_input",
                    "stage": "release_selection",
                    "status_text": "请选择",
                    "control": "exit",
                    "revision": 1,
                    "details": {},
                },
            })

            def callback_request(data):
                return SimpleNamespace(
                    effective_chat=SimpleNamespace(id=10),
                    effective_user=SimpleNamespace(id=1),
                    effective_message=SimpleNamespace(text=None),
                    callback_query=SimpleNamespace(
                        data=data,
                        answer=AsyncMock(),
                        message=SimpleNamespace(message_id=55),
                    ),
                )

            current = callback_request("media-search:release:current")
            await operation_gate(current, context)
            stale = callback_request("media-search:release:stale")
            with self.assertRaises(ApplicationHandlerStop):
                await operation_gate(stale, context)

            self.assertEqual(
                coordinator.get("op-current-prompt").details["keyboard"][0][0][
                    "callback_data"
                ],
                "media-search:release:current",
            )
            stale.callback_query.answer.assert_awaited_once_with("当前任务执行中")

    async def test_feature_result_persists_candidate_photo_for_operation_rendering(self):
        from app.handlers.plugin_handler import _with_rendered_keyboard

        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        operation = {"details": {}}
        result = {"actions": [{
            "kind": "send_photo",
            "text": "候选 1",
            "data": {
                "photo_url": "https://image.example/poster.jpg",
                "keyboard": [[{
                    "text": "选择此项",
                    "callback_data": "media-search:select:p1:0",
                }]],
            },
        }]}

        normalized = _with_rendered_keyboard(route, result, operation)

        self.assertEqual(
            normalized["details"]["photo_url"],
            "https://image.example/poster.jpg",
        )

    async def test_feature_config_patch_from_callback_updates_original_message(self):
        from app.handlers.plugin_handler import handle_feature_result

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.edit_message_text = AsyncMock()
        context.application.bot_data["telepiplex_plugin_sessions"] = {
            (10, 1): {"plugin_id": "media-search", "expires_at": 9999999999},
        }
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        result = {
            "actions": [],
            "session": {"state": "close"},
            "config_patch": {"ai": {"model": "new-model"}},
        }

        await handle_feature_result(update, context, route, result)

        self.assertEqual(manager.configurations, [(
            "media-search",
            {
                "ai": {"api_key": "kept-secret", "model": "new-model"},
                "search": {"prowlarr": {"base_url": "http://old"}},
            },
        )])
        messages = [
            call.args[0]
            for call in update.callback_query.edit_message_text.await_args_list
        ]
        self.assertEqual(
            messages,
            [
                "⏳ 正在保存并重新加载 media-search 配置...",
                "✅ media-search 配置已写入并重新加载。",
            ],
        )
        update.effective_message.reply_text.assert_not_awaited()
        self.assertNotIn("telepiplex_plugin_sessions", context.application.bot_data)

    async def test_invalid_feature_config_patch_is_rejected_without_write(self):
        from app.handlers.plugin_handler import handle_feature_result

        update, context, manager = self._request([], user_id=1)
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )

        await handle_feature_result(
            update, context, route,
            {"actions": [], "config_patch": ["not-a-mapping"]},
        )

        self.assertEqual(manager.configurations, [])
        self.assertIn(
            "invalid_config_patch",
            update.effective_message.reply_text.await_args.args[0],
        )

    async def test_invalid_feature_config_patch_from_callback_updates_original_message(self):
        from app.handlers.plugin_handler import handle_feature_result

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.edit_message_text = AsyncMock()
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )

        await handle_feature_result(
            update, context, route,
            {"actions": [], "config_patch": ["not-a-mapping"]},
        )

        self.assertEqual(manager.configurations, [])
        update.callback_query.edit_message_text.assert_awaited_once()
        self.assertIn(
            "invalid_config_patch",
            update.callback_query.edit_message_text.await_args.args[0],
        )
        update.effective_message.reply_text.assert_not_awaited()

    async def test_lifecycle_subcommands_dispatch_exact_arguments(self):
        from app.handlers.plugin_handler import plugin_command

        cases = (
            ("install", "echo@1.0.0", "echo@1.0.0"),
            ("update", "echo@2.0.0", "echo@2.0.0"),
            ("enable", "echo", "echo"),
            ("disable", "echo", "echo"),
            ("rollback", "echo", "echo"),
            ("remove", "echo", "echo"),
        )
        for command, raw, expected in cases:
            with self.subTest(command=command):
                update, context, manager = self._request([command, raw])
                with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
                    await plugin_command(update, context)
                self.assertEqual(manager.calls, [(command, expected)])
                messages = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
                self.assertIn("处理中", messages[0])
                self.assertIn("complete", messages[-1])

    async def test_status_doctor_usage_and_authorization(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request(["status", "echo"])
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)
        self.assertEqual(manager.calls, [("status", "echo")])
        self.assertIn("echo", update.effective_message.reply_text.await_args.args[0])

        update, context, manager = self._request(["doctor"])
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)
        self.assertEqual(manager.calls, [("doctor", None)])

        update, context, manager = self._request(["remove", "echo"], user_id=2)
        with patch("app.handlers.plugin_handler.init.check_user", return_value=False):
            await plugin_command(update, context)
        self.assertEqual(manager.calls, [])
        self.assertIn("无权", update.effective_message.reply_text.await_args.args[0])

        update, context, manager = self._request(["unknown"])
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)
        self.assertIn("用法", update.effective_message.reply_text.await_args.args[0])

    async def test_dynamic_command_uses_current_route_without_handler_reload(self):
        from app.handlers.plugin_handler import dynamic_command_gateway

        first_client = AsyncMock()
        first_client.request.return_value = {
            "actions": [{"kind": "send_message", "text": "v1"}]
        }
        second_client = AsyncMock()
        second_client.request.return_value = {
            "actions": [{"kind": "send_message", "text": "v2"}]
        }
        routes = [
            SimpleNamespace(plugin_id="echo", client=first_client),
            SimpleNamespace(plugin_id="echo", client=second_client),
        ]
        router = Mock()
        router.command_route.side_effect = routes
        update, context, manager = self._request([], user_id=1)
        update.effective_message.text = "/echo hello world"
        context.application.bot_data.update({
            "telepiplex_plugin_router": router,
        })

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_command_gateway(update, context)
            await dynamic_command_gateway(update, context)

        replies = [call.args[0] for call in update.effective_message.reply_text.await_args_list]
        self.assertEqual(replies, ["v1", "v2"])
        first_request = first_client.request.await_args.args
        self.assertEqual(first_request[0], "command.dispatch")
        self.assertEqual(first_request[1]["command"], "echo")
        self.assertEqual(first_request[1]["args"], ["hello", "world"])

    async def test_feature_session_routes_followup_text_and_closes_explicitly(self):
        from app.handlers.plugin_handler import (
            dynamic_command_gateway,
            dynamic_message_gateway,
        )

        client = AsyncMock()
        client.request.side_effect = [
            {"actions": [{"kind": "send_message", "text": "请输入名称"}],
             "session": {"state": "open"}},
            {"actions": [{"kind": "send_message", "text": "已完成"}],
             "session": {"state": "close"}},
        ]
        route = SimpleNamespace(plugin_id="open115", client=client)
        router = Mock()
        router.command_route.return_value = route
        router.plugin_route.return_value = route
        update, context, _manager = self._request([], user_id=1)
        context.application.bot_data["telepiplex_plugin_router"] = router
        update.effective_message.text = "/auth"

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_command_gateway(update, context)
            update.effective_message.text = "授权码"
            await dynamic_message_gateway(update, context)

        methods = [call.args[0] for call in client.request.await_args_list]
        self.assertEqual(methods, ["command.dispatch", "message.dispatch"])
        self.assertNotIn("telepiplex_plugin_sessions", context.application.bot_data)

    async def test_route_loss_closes_feature_session_without_dispatch(self):
        from app.handlers.plugin_handler import dynamic_message_gateway

        update, context, _manager = self._request([], user_id=1)
        update.effective_message.text = "follow up"
        context.application.bot_data.update({
            "telepiplex_plugin_router": Mock(),
            "telepiplex_plugin_sessions": {
                (10, 1): {"plugin_id": "open115", "expires_at": 9999999999},
            },
        })
        context.application.bot_data["telepiplex_plugin_router"].plugin_route.return_value = None

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_message_gateway(update, context)

        self.assertNotIn("telepiplex_plugin_sessions", context.application.bot_data)
        self.assertIn("已结束", update.effective_message.reply_text.await_args.args[0])

    async def test_inline_keyboard_callback_must_belong_to_feature_namespace(self):
        from app.handlers.plugin_handler import dynamic_command_gateway

        client = AsyncMock()
        client.request.return_value = {
            "actions": [{
                "kind": "send_message",
                "text": "选择",
                "data": {"keyboard": [[
                    {"text": "安全", "callback_data": "echo:next"},
                    {"text": "越权", "callback_data": "other:next"},
                ]]},
            }]
        }
        manifest = SimpleNamespace(callbacks=("echo",))
        route = SimpleNamespace(plugin_id="echo", client=client, manifest=manifest)
        router = Mock()
        router.command_route.return_value = route
        update, context, _manager = self._request([], user_id=1)
        update.effective_message.text = "/echo"
        context.application.bot_data["telepiplex_plugin_router"] = router

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_command_gateway(update, context)

        self.assertIn("无效响应", update.effective_message.reply_text.await_args.args[0])

    async def test_feature_send_photo_action_preserves_namespaced_keyboard(self):
        from app.handlers.plugin_handler import _render_actions

        update, context, _manager = self._request([], user_id=1)
        update.effective_message.reply_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=81)
        )
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        result = {
            "actions": [{
                "kind": "send_photo",
                "text": "候选 1",
                "data": {
                    "photo_url": "https://image.example/poster.jpg",
                    "keyboard": [[{
                        "text": "选择此项",
                        "callback_data": "media-search:select:p1:0",
                    }]],
                },
            }]
        }

        rendered, message_id, message_kind = await _render_actions(
            update, context, route, result
        )

        self.assertTrue(rendered)
        self.assertEqual(message_id, 81)
        self.assertEqual(message_kind, "photo")
        update.effective_message.reply_photo.assert_awaited_once()
        kwargs = update.effective_message.reply_photo.await_args.kwargs
        self.assertEqual(kwargs["photo"], "https://image.example/poster.jpg")
        self.assertEqual(
            kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "media-search:select:p1:0",
        )

    async def test_feature_photo_failure_falls_back_to_same_text_card(self):
        from app.handlers.plugin_handler import _render_actions

        update, context, _manager = self._request([], user_id=1)
        update.effective_message.reply_photo = AsyncMock(
            side_effect=RuntimeError("image unavailable")
        )
        update.effective_message.reply_text = AsyncMock(
            return_value=SimpleNamespace(message_id=82)
        )
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        result = {"actions": [{
            "kind": "send_photo",
            "text": "候选 1",
            "data": {"photo_url": "https://image.example/poster.jpg"},
        }]}

        rendered, _message_id, message_kind = await _render_actions(
            update, context, route, result
        )

        self.assertTrue(rendered)
        self.assertEqual(message_kind, "text")
        update.effective_message.reply_text.assert_awaited_once_with("候选 1")

    async def test_feature_rejects_non_https_photo_url(self):
        from app.handlers.plugin_handler import _render_actions

        update, context, _manager = self._request([], user_id=1)
        update.effective_message.reply_photo = AsyncMock()
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        result = {"actions": [{
            "kind": "send_photo",
            "text": "候选 1",
            "data": {"photo_url": "http://image.example/poster.jpg"},
        }]}

        rendered, message_id, message_kind = await _render_actions(
            update, context, route, result
        )

        self.assertFalse(rendered)
        self.assertIsNone(message_id)
        self.assertIsNone(message_kind)
        update.effective_message.reply_photo.assert_not_awaited()
        self.assertIn(
            "无效响应",
            update.effective_message.reply_text.await_args.args[0],
        )

    async def test_feature_text_update_on_photo_sends_replacement_message(self):
        from app.handlers.plugin_handler import _render_actions

        update, context, _manager = self._request([], user_id=1)
        update.effective_message.photo = [SimpleNamespace(file_id="poster")]
        update.effective_message.edit_text = AsyncMock()
        update.effective_message.reply_text = AsyncMock(
            return_value=SimpleNamespace(message_id=82)
        )
        route = SimpleNamespace(
            plugin_id="media-search",
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )

        rendered, message_id, message_kind = await _render_actions(
            update,
            context,
            route,
            {"actions": [{
                "kind": "edit_message",
                "text": "正在搜索片源",
            }]},
        )

        self.assertTrue(rendered)
        self.assertEqual((message_id, message_kind), (82, "text"))
        update.effective_message.edit_text.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()

    async def test_callback_routes_namespace_and_rejects_unknown_response_action(self):
        from app.handlers.plugin_handler import dynamic_callback_gateway

        client = AsyncMock()
        client.request.return_value = {
            "actions": [{"kind": "run_shell", "text": "forbidden"}]
        }
        router = Mock()
        router.callback_route.return_value = SimpleNamespace(plugin_id="echo", client=client)
        update = Mock()
        update.update_id = 100
        update.effective_user.id = 1
        update.effective_chat.id = 10
        update.effective_message.reply_text = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.callback_query.data = "echo:next"
        update.callback_query.answer = AsyncMock()
        context = Mock()
        context.application.bot_data = {"telepiplex_plugin_router": router}

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_callback_gateway(update, context)

        update.callback_query.answer.assert_awaited_once()
        client.request.assert_awaited_once()
        self.assertIn(
            "无效响应",
            update.callback_query.edit_message_text.await_args.args[0],
        )
        update.effective_message.reply_text.assert_not_awaited()

    async def test_feature_callback_acknowledges_with_progress_feedback(self):
        from app.handlers.plugin_handler import dynamic_callback_gateway

        client = AsyncMock()
        client.request.return_value = {
            "actions": [{"kind": "edit_message", "text": "已完成"}]
        }
        route = SimpleNamespace(
            plugin_id="media-search",
            client=client,
            manifest=SimpleNamespace(callbacks=("media-search",)),
        )
        router = Mock()
        router.callback_route.return_value = route
        update = Mock()
        update.update_id = 100
        update.effective_user.id = 1
        update.effective_chat.id = 10
        update.effective_message.reply_text = AsyncMock()
        update.effective_message.edit_text = AsyncMock()
        update.callback_query.data = "media-search:confirm:plan-a"
        update.callback_query.answer = AsyncMock()
        context = Mock()
        context.application.bot_data = {"telepiplex_plugin_router": router}

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_callback_gateway(update, context)

        update.callback_query.answer.assert_awaited_once_with(
            text="处理中...",
        )
        update.effective_message.edit_text.assert_awaited_once()

    async def test_manager_error_is_sanitized_and_does_not_escape_handler(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request(["enable", "echo"])
        manager.enable = AsyncMock(
            side_effect=PluginOperationError("enable_failed", "token=secret-value")
        )

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        message = update.effective_message.reply_text.await_args_list[-1].args[0]
        self.assertIn("enable_failed", message)
        self.assertNotIn("secret-value", message)

    async def test_successful_lifecycle_reports_command_menu_sync_failure(self):
        from app.core.capability_router import CapabilityRouter
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request(["enable", "echo"])
        context.application.bot_data["telepiplex_plugin_router"] = CapabilityRouter()
        context.application.bot.set_my_commands = AsyncMock(
            side_effect=RuntimeError("telegram unavailable")
        )

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        self.assertEqual(manager.calls, [("enable", "echo")])
        context.application.bot.set_my_commands.assert_awaited_once()
        message = update.effective_message.reply_text.await_args_list[-1].args[0]
        self.assertIn("命令列表同步失败", message)
        self.assertIn("不会回滚", message)

    async def test_disabling_feature_clears_its_sessions(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request(["disable", "echo"])
        context.application.bot_data["telepiplex_plugin_sessions"] = {
            (10, 1): {"plugin_id": "echo", "expires_at": 9999999999},
            (20, 2): {"plugin_id": "other", "expires_at": 9999999999},
        }
        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        sessions = context.application.bot_data["telepiplex_plugin_sessions"]
        self.assertEqual(list(sessions.values())[0]["plugin_id"], "other")

    async def test_manual_update_clears_stale_core_config_state(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request(["update", "echo@2.0.0"])
        context.user_data.update({
            "core_config_plugins": ["echo"],
            "core_config_plugin": "echo",
        })

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        self.assertEqual(context.user_data, {})
        markup = update.effective_message.reply_text.await_args_list[-1].kwargs[
            "reply_markup"
        ]
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data,
            "core-config-direct:echo",
        )

    async def test_core_update_callback_requires_authorization_and_confirmation(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_update_callback(update, context)

        self.assertEqual(manager.calls, [("update", "echo@1.1.0")])
        update.callback_query.answer.assert_awaited_once()
        self.assertIn(
            "1.0.0",
            update.callback_query.edit_message_text.await_args_list[-1].args[0],
        )

        update, context, manager = self._request([], user_id=2)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with patch("app.handlers.plugin_handler.init.check_user", return_value=False):
            await plugin_update_callback(update, context)
        self.assertEqual(manager.calls, [])

    async def test_core_update_callback_can_decline_without_update(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:decline:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_update_callback(update, context)

        self.assertEqual(manager.calls, [])
        self.assertIn(
            "暂不更新",
            update.callback_query.edit_message_text.await_args.args[0],
        )

    async def test_update_success_clears_stale_config_state_and_links_current_wizard(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:confirm:open115@1.0.1"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context.user_data.update({
            "core_config_plugins": ["open115"],
            "core_config_plugin": "open115",
        })
        manager.update = AsyncMock(return_value=SimpleNamespace(
            state="active", plugin_id="open115", version="1.0.1",
            message="Feature updated", details={},
        ))

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_update_callback(update, context)

        self.assertEqual(context.user_data, {})
        markup = update.callback_query.edit_message_text.await_args_list[-1].kwargs[
            "reply_markup"
        ]
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data,
            "core-config-direct:open115",
        )

    async def test_update_success_reports_added_config_keys_without_values(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:confirm:echo@2.0.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        manager.update = AsyncMock(return_value=SimpleNamespace(
            state="active", plugin_id="echo", version="2.0.0",
            message="Feature updated",
            details={
                "config_added_keys": ["mcp.path", "service.timeout"],
                "config_values": {"api_key": "operator-secret"},
            },
        ))

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_update_callback(update, context)

        message = update.callback_query.edit_message_text.await_args_list[-1].args[0]
        self.assertIn("新增配置项：mcp.path、service.timeout", message)
        self.assertNotIn("operator-secret", message)

    async def test_core_update_callback_sanitizes_manager_errors(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-update:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        manager.update = AsyncMock(side_effect=PluginOperationError(
            "update_failed",
            "token=secret-value",
        ))

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_update_callback(update, context)

        message = update.callback_query.edit_message_text.await_args_list[-1].args[0]
        self.assertIn("update_failed", message)
        self.assertNotIn("secret-value", message)

    async def test_plugin_overview_lists_ready_and_blocked_candidates(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.doctor = Mock(return_value=[])
        manager.candidates = [
            SimpleNamespace(
                plugin_id="open115",
                target_version="1.0.0",
                reference="open115@1.0.0",
                ready=True,
                missing_capabilities=(),
                dependency_plugins=(),
            ),
            SimpleNamespace(
                plugin_id="media-search",
                target_version="1.0.0",
                reference="media-search@1.0.0",
                ready=False,
                missing_capabilities=("download.provider",),
                dependency_plugins=("open115",),
            ),
            SimpleNamespace(
                plugin_id="orphan",
                target_version="1.0.0",
                reference="orphan@1.0.0",
                ready=False,
                missing_capabilities=("missing.provider",),
                dependency_plugins=(),
            ),
        ]

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        self.assertEqual(manager.calls, [])
        call = update.effective_message.reply_text.await_args
        self.assertIn("open115", call.args[0])
        self.assertIn("先安装：open115", call.args[0])
        self.assertIn("缺少能力：missing.provider", call.args[0])
        buttons = call.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(buttons), 1)
        self.assertEqual(
            buttons[0][0].callback_data,
            "core-plugin-install:confirm:open115@1.0.0",
        )

    async def test_plugin_overview_lists_update_action_for_installed_feature(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.doctor = Mock(return_value=[{
            "plugin_id": "open115",
            "state": "active",
            "version": "1.0.0",
        }])
        manager.updates = [SimpleNamespace(
            plugin_id="open115",
            current_version="1.0.0",
            target_version="1.1.0",
            reference="open115@1.1.0",
        )]

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        call = update.effective_message.reply_text.await_args
        message = call.args[0]
        self.assertIn("可更新", message)
        self.assertIn("open115 1.0.0 → 1.1.0", message)
        callbacks = [
            row[0].callback_data
            for row in call.kwargs["reply_markup"].inline_keyboard
        ]
        self.assertIn(
            "core-plugin-update:confirm:open115@1.1.0",
            callbacks,
        )

    async def test_plugin_overview_keeps_install_buttons_when_update_discovery_fails(self):
        from app.core.plugin_catalog import CatalogError
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.available_updates = AsyncMock(side_effect=CatalogError(
            "updates_unavailable",
            "network token=secret-value",
        ))
        manager.candidates = [SimpleNamespace(
            plugin_id="open115",
            target_version="1.0.0",
            reference="open115@1.0.0",
            ready=True,
            missing_capabilities=(),
            dependency_plugins=(),
        )]

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        call = update.effective_message.reply_text.await_args
        message = call.args[0]
        callbacks = [
            row[0].callback_data
            for row in call.kwargs["reply_markup"].inline_keyboard
        ]
        self.assertIn("updates_unavailable", message)
        self.assertNotIn("secret-value", message)
        self.assertIn(
            "core-plugin-install:confirm:open115@1.0.0",
            callbacks,
        )

    async def test_plugin_overview_keeps_update_buttons_when_install_discovery_fails(self):
        from app.core.plugin_catalog import CatalogError
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.available_plugins = AsyncMock(side_effect=CatalogError(
            "catalog_unavailable",
            "network api_key=secret-value",
        ))
        manager.updates = [SimpleNamespace(
            plugin_id="open115",
            current_version="1.0.0",
            target_version="1.1.0",
            reference="open115@1.1.0",
        )]

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        call = update.effective_message.reply_text.await_args
        message = call.args[0]
        callbacks = [
            row[0].callback_data
            for row in call.kwargs["reply_markup"].inline_keyboard
        ]
        self.assertIn("catalog_unavailable", message)
        self.assertNotIn("secret-value", message)
        self.assertIn(
            "core-plugin-update:confirm:open115@1.1.0",
            callbacks,
        )

    async def test_plugin_overview_links_installed_features_to_config_ui(self):
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.available_plugins = AsyncMock(return_value=[])

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        buttons = update.effective_message.reply_text.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "core-config-open")
        self.assertIn("配置", buttons[0][0].text)

    async def test_plugin_overview_keeps_manual_entry_when_catalog_is_unavailable(self):
        from app.core.plugin_catalog import CatalogError
        from app.handlers.plugin_handler import plugin_command

        update, context, manager = self._request([], user_id=1)
        manager.available_plugins = AsyncMock(side_effect=CatalogError(
            "catalog_unavailable",
            "network token=secret-value",
        ))

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_command(update, context)

        message = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("catalog_unavailable", message)
        self.assertIn("/plugin install", message)
        self.assertNotIn("secret-value", message)

    async def test_core_install_callback_requires_authorization_and_click(self):
        from app.handlers.plugin_handler import plugin_install_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-install:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_install_callback(update, context)

        self.assertEqual(manager.calls, [("install", "echo@1.1.0")])
        self.assertIn(
            "1.0.0",
            update.callback_query.edit_message_text.await_args_list[-1].args[0],
        )
        markup = update.callback_query.edit_message_text.await_args_list[-1].kwargs[
            "reply_markup"
        ]
        self.assertEqual(
            markup.inline_keyboard[0][0].callback_data,
            "core-config-direct:echo",
        )

        update, context, manager = self._request([], user_id=2)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-install:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with patch("app.handlers.plugin_handler.init.check_user", return_value=False):
            await plugin_install_callback(update, context)
        self.assertEqual(manager.calls, [])

    async def test_core_install_callback_sanitizes_manager_errors(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.plugin_handler import plugin_install_callback

        update, context, manager = self._request([], user_id=1)
        update.callback_query = Mock()
        update.callback_query.data = "core-plugin-install:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        manager.install = AsyncMock(side_effect=PluginOperationError(
            "install_failed",
            "api_key=secret-value",
        ))

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await plugin_install_callback(update, context)

        message = update.callback_query.edit_message_text.await_args_list[-1].args[0]
        self.assertIn("install_failed", message)
        self.assertNotIn("secret-value", message)


if __name__ == "__main__":
    unittest.main()

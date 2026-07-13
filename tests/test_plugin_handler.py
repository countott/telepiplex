import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class FakeManager:
    def __init__(self):
        self.calls = []
        self.router = Mock()
        self.candidates = []

    async def _operation(self, name, value):
        self.calls.append((name, value))
        return SimpleNamespace(
            state="active",
            plugin_id=str(value),
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


class PluginHandlerTest(unittest.IsolatedAsyncioTestCase):
    def _request(self, args, user_id=1):
        update = Mock()
        update.update_id = 99
        update.effective_user.id = user_id
        update.effective_chat.id = 10
        update.effective_message.text = "/plugin " + " ".join(args)
        update.effective_message.reply_text = AsyncMock()
        manager = FakeManager()
        context = Mock()
        context.args = args
        context.application.bot_data = {"telepiplex_plugin_manager": manager}
        return update, context, manager

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
        update.callback_query.data = "echo:next"
        update.callback_query.answer = AsyncMock()
        context = Mock()
        context.application.bot_data = {"telepiplex_plugin_router": router}

        with patch("app.handlers.plugin_handler.init.check_user", return_value=True):
            await dynamic_callback_gateway(update, context)

        update.callback_query.answer.assert_awaited_once()
        client.request.assert_awaited_once()
        self.assertIn("无效响应", update.effective_message.reply_text.await_args.args[0])

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

    async def test_core_update_callback_requires_authorization_and_confirmation(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
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
        update.callback_query.data = "core-plugin-update:confirm:echo@1.1.0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        with patch("app.handlers.plugin_handler.init.check_user", return_value=False):
            await plugin_update_callback(update, context)
        self.assertEqual(manager.calls, [])

    async def test_core_update_callback_can_decline_without_update(self):
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
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

    async def test_core_update_callback_sanitizes_manager_errors(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.plugin_handler import plugin_update_callback

        update, context, manager = self._request([], user_id=1)
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

        update, context, manager = self._request([], user_id=2)
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

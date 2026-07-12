import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class FakeManager:
    def __init__(self):
        self.calls = []
        self.router = Mock()

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


if __name__ == "__main__":
    unittest.main()

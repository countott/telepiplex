import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def custom_schema(command="config"):
    return {
        "type": "object",
        "x-telepiplex-config-command": command,
        "properties": {"access_token": {"type": "string", "writeOnly": True}},
    }


class FakeManager:
    def __init__(self):
        self.views = {
            "open115": {
                "plugin_id": "open115",
                "version": "1.0.1",
                "schema": custom_schema(),
                "config": {"access_token": "secret-value"},
            }
        }
        self.statuses = [
            {"plugin_id": "open115", "version": "1.0.1", "state": "active"},
        ]

    def doctor(self):
        return list(self.statuses)

    def config(self, plugin_id):
        return self.views[plugin_id]

    def config_state(self, plugin_id):
        view = self.config(plugin_id)
        command = str(
            (view.get("schema") or {}).get("x-telepiplex-config-command") or ""
        )
        return {
            "plugin_id": plugin_id,
            "version": view.get("version") or "",
            "state": "configurable" if command else "not_configurable",
            "configurable": bool(command),
            "command": command,
            "error_code": "" if command else "not_configurable",
        }


class ConfigHandlerTest(unittest.IsolatedAsyncioTestCase):
    def request(self, *, callback_data="", text=""):
        manager = FakeManager()
        update = Mock()
        update.update_id = 99
        update.effective_user.id = 1
        update.effective_chat.id = 10
        update.effective_message = Mock()
        update.effective_message.text = text
        update.effective_message.reply_text = AsyncMock()
        update.callback_query = Mock()
        update.callback_query.data = callback_data
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}
        context.application.bot_data = {
            "telepiplex_plugin_manager": manager,
            "telepiplex_plugin_router": Mock(),
        }
        return update, context, manager

    @staticmethod
    def custom_route(command="config"):
        client = AsyncMock()
        client.request.return_value = {
            "actions": [{
                "kind": "send_message",
                "text": "请选择授权方式",
                "data": {"keyboard": [[{
                    "text": "Access / Refresh Token",
                    "callback_data": "open115:auth:direct",
                }]]},
            }],
            "session": {"state": "open"},
        }
        return SimpleNamespace(
            plugin_id="open115",
            client=client,
            manifest=SimpleNamespace(
                commands=(SimpleNamespace(name=command),),
                callbacks=("open115",),
            ),
        )

    async def test_custom_config_feature_is_listed_and_handed_to_current_route(self):
        from app.handlers.config_handler import config_command, select_config_plugin

        update, context, _manager = self.request(text="/config")
        route = self.custom_route()
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.return_value = route

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            await config_command(update, context)

        menu = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("open115", menu)
        self.assertNotIn("secret-value", menu)

        update.callback_query.data = "core-config-plugin:0"
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await select_config_plugin(update, context)

        self.assertEqual(state, -1)
        request = route.client.request.await_args.args
        self.assertEqual(request[0], "command.dispatch")
        self.assertEqual(request[1]["command"], "config")
        self.assertEqual(
            context.application.bot_data[
                "telepiplex_plugin_sessions"
            ][(10, 1)]["plugin_id"],
            "open115",
        )

    async def test_invalid_config_feature_remains_visible_with_stable_error_code(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.config_handler import config_command

        update, context, manager = self.request(text="/config")
        manager.statuses.insert(0, {
            "plugin_id": "media-search",
            "version": "1.0.1",
            "state": "active",
        })
        original_config_state = manager.config_state

        def config_state(plugin_id):
            if plugin_id == "media-search":
                return {
                    "plugin_id": plugin_id,
                    "version": "1.0.1",
                    "state": "invalid_config",
                    "configurable": False,
                    "command": "media_search_config",
                    "error_code": "invalid_config",
                }
            return original_config_state(plugin_id)

        manager.config_state = config_state
        route = self.custom_route()
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.side_effect = (
            lambda plugin_id: route if plugin_id == "open115" else None
        )

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            await config_command(update, context)

        menu = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("media-search", menu)
        self.assertIn("invalid_config", menu)
        self.assertNotIn("secret-value", menu)
        self.assertEqual(context.user_data["core_config_plugins"], ["open115"])

    async def test_nested_scalars_without_custom_command_are_not_given_a_button(self):
        from app.handlers.config_handler import config_command

        update, context, manager = self.request(text="/config")
        manager.views["media-search"] = {
            "plugin_id": "media-search",
            "version": "1.0.1",
            "schema": {
                "type": "object",
                "properties": {
                    "ai": {
                        "type": "object",
                        "properties": {"timeout": {"type": "number"}},
                    }
                },
            },
            "config": {"ai": {"timeout": 60}},
        }
        manager.statuses.append({
            "plugin_id": "media-search", "version": "1.0.1", "state": "active",
        })
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.return_value = self.custom_route()

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            await config_command(update, context)

        menu = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("media-search", menu)
        self.assertIn("未提供独立配置向导", menu)
        self.assertEqual(context.user_data["core_config_plugins"], ["open115"])

    async def test_invalid_custom_declaration_is_reported_not_dispatched(self):
        from app.handlers.config_handler import config_command

        update, context, _manager = self.request(text="/config")
        route = self.custom_route(command="auth")
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.return_value = route
        original_config_state = _manager.config_state

        def invalid_state(plugin_id):
            state = original_config_state(plugin_id)
            state.update({
                "state": "invalid_declaration",
                "configurable": False,
                "error_code": "invalid_config_command",
            })
            return state

        _manager.config_state = invalid_state

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await config_command(update, context)

        self.assertEqual(state, -1)
        message = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("open115", message)
        self.assertIn("配置入口声明无效", message)
        route.client.request.assert_not_awaited()

    async def test_direct_config_callback_reloads_current_schema_and_route(self):
        from app.handlers.config_handler import direct_config_callback

        update, context, _manager = self.request(
            callback_data="core-config-direct:open115"
        )
        route = self.custom_route()
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.return_value = route

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await direct_config_callback(update, context)

        self.assertEqual(state, -1)
        self.assertEqual(route.client.request.await_args.args[1]["command"], "config")

    async def test_custom_config_dispatch_error_is_sanitized(self):
        from app.handlers.config_handler import config_command, select_config_plugin

        update, context, _manager = self.request(text="/config")
        route = self.custom_route()
        route.client.request.side_effect = RuntimeError("token=secret-value")
        context.application.bot_data[
            "telepiplex_plugin_router"
        ].plugin_route.return_value = route

        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            await config_command(update, context)
        update.callback_query.data = "core-config-plugin:0"
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await select_config_plugin(update, context)

        self.assertEqual(state, -1)
        message = update.callback_query.edit_message_text.await_args.args[0]
        self.assertIn("custom_config_failed", message)
        self.assertNotIn("secret-value", message)

    async def test_unauthorized_command_does_not_read_manager(self):
        from app.handlers.config_handler import config_command

        update, context, manager = self.request(text="/config")
        manager.doctor = Mock(side_effect=AssertionError("must not be called"))
        with patch("app.handlers.config_handler.init.check_user", return_value=False):
            state = await config_command(update, context)

        self.assertEqual(state, -1)
        self.assertIn("无权", update.effective_message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()

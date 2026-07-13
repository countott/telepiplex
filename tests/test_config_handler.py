import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def sample_schema():
    return {
        "type": "object",
        "properties": {
            "metadata": {
                "type": "object",
                "properties": {
                    "tvdb": {
                        "type": "object",
                        "title": "TVDB",
                        "properties": {
                            "enable": {"type": "boolean", "title": "启用"},
                            "api_key": {
                                "type": "string",
                                "title": "API Key",
                                "writeOnly": True,
                            },
                            "timeout": {"type": "number", "minimum": 1},
                        },
                    }
                },
            },
            "ai": {"$ref": "#/$defs/ai"},
        },
        "$defs": {
            "ai": {
                "type": "object",
                "title": "AI",
                "properties": {
                    "api_url": {"type": "string"},
                    "api_key": {"type": "string", "writeOnly": True},
                    "model": {"type": "string"},
                },
            }
        },
    }


def sample_config():
    return {
        "metadata": {
            "tvdb": {"enable": True, "api_key": "tvdb-secret", "timeout": 15},
        },
        "ai": {
            "api_url": "https://api.example.com",
            "api_key": "ai-secret",
            "model": "example-model",
        },
    }


class FakeManager:
    def __init__(self):
        self.views = {
            "media-search": {
                "plugin_id": "media-search",
                "version": "1.0.0",
                "schema": sample_schema(),
                "config": sample_config(),
            }
        }
        self.configure = AsyncMock(return_value=SimpleNamespace(
            state="active",
            plugin_id="media-search",
            version="1.0.0",
            message="Feature configuration saved and reloaded",
            details={"restarted": True},
        ))

    def doctor(self):
        return [{"plugin_id": "media-search", "version": "1.0.0", "state": "healthy"}]

    def config(self, plugin_id):
        return self.views[plugin_id]


class ConfigPureFunctionTest(unittest.TestCase):
    def test_discovers_nested_and_local_ref_sections(self):
        from app.handlers.config_handler import discover_config_sections

        sections = discover_config_sections(sample_schema(), sample_config())

        self.assertEqual([section.path for section in sections], [
            ("metadata", "tvdb"),
            ("ai",),
        ])
        self.assertEqual([section.title for section in sections], ["TVDB", "AI"])
        tvdb = sections[0]
        self.assertTrue(next(field for field in tvdb.fields if field.name == "api_key").secret)

    def test_prompt_masks_secret_but_shows_non_secret_current_values(self):
        from app.handlers.config_handler import (
            discover_config_sections,
            format_section_prompt,
        )

        tvdb = discover_config_sections(sample_schema(), sample_config())[0]
        prompt = format_section_prompt("media-search", tvdb)

        self.assertIn("api_key=<已配置>", prompt)
        self.assertNotIn("tvdb-secret", prompt)
        self.assertIn("timeout=15", prompt)

    def test_parse_patch_coerces_types_and_rejects_unknown_fields(self):
        from app.handlers.config_handler import (
            ConfigInputError,
            discover_config_sections,
            parse_config_patch,
        )

        tvdb = discover_config_sections(sample_schema(), sample_config())[0]
        self.assertEqual(
            parse_config_patch("enable=false\napi_key=new-key\ntimeout=12.5", tvdb),
            {"enable": False, "api_key": "new-key", "timeout": 12.5},
        )
        with self.assertRaises(ConfigInputError):
            parse_config_patch("unknown=value", tvdb)
        with self.assertRaises(ConfigInputError):
            parse_config_patch("enable=maybe", tvdb)

    def test_merge_patch_preserves_unselected_config(self):
        from app.handlers.config_handler import merge_config_patch

        merged = merge_config_patch(
            sample_config(),
            ("metadata", "tvdb"),
            {"api_key": "new-key"},
        )

        self.assertEqual(merged["metadata"]["tvdb"]["api_key"], "new-key")
        self.assertEqual(merged["metadata"]["tvdb"]["timeout"], 15)
        self.assertEqual(merged["ai"]["api_key"], "ai-secret")


class ConfigHandlerTest(unittest.IsolatedAsyncioTestCase):
    def request(self, *, callback_data="", text=""):
        manager = FakeManager()
        update = Mock()
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
        context.application.bot_data = {"telepiplex_plugin_manager": manager}
        return update, context, manager

    async def test_command_lists_configurable_features_without_secret_values(self):
        from app.handlers.config_handler import CONFIG_SELECT_PLUGIN, config_command

        update, context, _manager = self.request(text="/config")
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await config_command(update, context)

        self.assertEqual(state, CONFIG_SELECT_PLUGIN)
        sent = update.effective_message.reply_text.await_args
        self.assertIn("media-search", sent.args[0])
        self.assertNotIn("secret", sent.args[0])
        self.assertEqual(
            sent.kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "core-config-plugin:0",
        )

    async def test_select_section_masks_secret_and_save_reloads_feature(self):
        from app.handlers.config_handler import (
            CONFIG_INPUT,
            CONFIG_SELECT_SECTION,
            receive_config_input,
            select_config_plugin,
            select_config_section,
        )

        update, context, manager = self.request(callback_data="core-config-plugin:0")
        context.user_data["core_config_plugins"] = ["media-search"]
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await select_config_plugin(update, context)
        self.assertEqual(state, CONFIG_SELECT_SECTION)

        update.callback_query.data = "core-config-section:0"
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await select_config_section(update, context)
        self.assertEqual(state, CONFIG_INPUT)
        prompt = update.callback_query.edit_message_text.await_args.args[0]
        self.assertIn("api_key=<已配置>", prompt)
        self.assertNotIn("tvdb-secret", prompt)

        update.effective_message.text = "api_key=new-key"
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await receive_config_input(update, context)
        self.assertEqual(state, -1)
        configured = manager.configure.await_args.args
        self.assertEqual(configured[0], "media-search")
        self.assertEqual(configured[1]["metadata"]["tvdb"]["api_key"], "new-key")
        self.assertEqual(configured[1]["ai"]["api_key"], "ai-secret")
        message = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("已写入并重新加载", message)
        self.assertNotIn("new-key", message)

    async def test_save_error_is_sanitized(self):
        from app.core.plugin_manager import PluginOperationError
        from app.handlers.config_handler import receive_config_input

        update, context, manager = self.request(text="api_key=new-key")
        context.user_data.update({
            "core_config_plugin": "media-search",
            "core_config_path": ("metadata", "tvdb"),
        })
        manager.configure.side_effect = PluginOperationError(
            "config_reload_failed",
            "value 'secret-value' is too short",
        )
        with patch("app.handlers.config_handler.init.check_user", return_value=True):
            state = await receive_config_input(update, context)

        self.assertNotEqual(state, -1)
        message = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("config_reload_failed", message)
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

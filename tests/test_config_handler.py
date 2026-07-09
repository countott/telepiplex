import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.handlers.config_handler import (
    CONFIG_INPUT_115_REFRESH,
    CONFIG_SELECT_115_MODE,
    CONFIG_SELECT_OPTIONAL_ITEM,
    apply_optional_config_payload,
    apply_115_openapi_payload,
    apply_115_token_payload,
    build_config_keyboard,
    parse_key_value_lines,
    receive_115_access_token,
    receive_115_refresh_token,
    select_config_item,
)


class ConfigHandlerTest(unittest.TestCase):
    def test_parse_key_value_lines_accepts_colon_and_equals(self):
        self.assertEqual(
            parse_key_value_lines("Access Token=aaa\nrefresh-token: bbb"),
            {"access_token": "aaa", "refresh_token": "bbb"},
        )

    @patch("app.handlers.config_handler.init.initialize_115open", return_value=True)
    @patch("app.handlers.config_handler.init.load_yaml_config")
    def test_apply_115_token_payload_writes_yaml_and_token_cache(self, load_mock, init_115_mock):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            token_path = Path(tmpdir) / "115_tokens.json"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "115_app_id": "real-app-id",
                        "access_token": "",
                        "refresh_token": "",
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            old_config_file = init.CONFIG_FILE
            old_token_file = init.TOKEN_FILE
            init.CONFIG_FILE = str(config_path)
            init.TOKEN_FILE = str(token_path)
            self.addCleanup(setattr, init, "CONFIG_FILE", old_config_file)
            self.addCleanup(setattr, init, "TOKEN_FILE", old_token_file)

            result = apply_115_token_payload("access_token=access-new\nrefresh_token=refresh-new")

            self.assertTrue(result["ready"])
            written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertIsNone(written["115_app_id"])
            self.assertEqual(written["access_token"], "access-new")
            self.assertEqual(written["refresh_token"], "refresh-new")
            self.assertEqual(
                json.loads(token_path.read_text(encoding="utf-8")),
                {"access_token": "access-new", "refresh_token": "refresh-new"},
            )
            load_mock.assert_called_once()
            init_115_mock.assert_called_once()

    @patch("app.handlers.config_handler.init.load_yaml_config")
    def test_apply_optional_config_payload_writes_nested_service_tokens(self, load_mock):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "search": {"enable": False, "prowlarr": {"base_url": "", "api_key": ""}},
                        "media": {"plex": {"base_url": "", "token": ""}},
                        "metadata": {"tvdb": {"enable": False, "api_key": "", "subscriber_pin": ""}},
                        "ai": {"enable": False, "api_url": "", "api_key": "", "model": ""},
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            old_config_file = init.CONFIG_FILE
            init.CONFIG_FILE = str(config_path)
            self.addCleanup(setattr, init, "CONFIG_FILE", old_config_file)

            apply_optional_config_payload("prowlarr", "base_url=http://prowlarr:9696\napi_key=prowlarr-key")
            apply_optional_config_payload("plex", "base_url=http://plex:32400\ntoken=plex-token")
            apply_optional_config_payload("tvdb", "api_key=tvdb-key\nsubscriber_pin=tvdb-pin")
            result = apply_optional_config_payload(
                "ai",
                "api_url=https://api.deepseek.com\napi_key=ai-key\nmodel=deepseek-chat",
            )

            self.assertEqual(result["config_file"], str(config_path))
            written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertTrue(written["search"]["enable"])
            self.assertEqual(written["search"]["prowlarr"]["api_key"], "prowlarr-key")
            self.assertEqual(written["media"]["plex"]["token"], "plex-token")
            self.assertTrue(written["metadata"]["tvdb"]["enable"])
            self.assertEqual(written["metadata"]["tvdb"]["api_key"], "tvdb-key")
            self.assertTrue(written["ai"]["enable"])
            self.assertEqual(written["ai"]["api_key"], "ai-key")
            self.assertEqual(load_mock.call_count, 4)

    @patch("app.handlers.config_handler.init.load_yaml_config")
    def test_apply_115_openapi_payload_writes_app_id_and_clears_direct_token_cache(self, load_mock):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            token_path = Path(tmpdir) / "115_tokens.json"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "115_app_id": None,
                        "access_token": "old-access",
                        "refresh_token": "old-refresh",
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            token_path.write_text(json.dumps({"access_token": "old", "refresh_token": "old"}), encoding="utf-8")
            old_config_file = init.CONFIG_FILE
            old_token_file = init.TOKEN_FILE
            old_openapi = init.openapi_115
            init.CONFIG_FILE = str(config_path)
            init.TOKEN_FILE = str(token_path)
            init.openapi_115 = Mock()
            self.addCleanup(setattr, init, "CONFIG_FILE", old_config_file)
            self.addCleanup(setattr, init, "TOKEN_FILE", old_token_file)
            self.addCleanup(setattr, init, "openapi_115", old_openapi)

            result = apply_115_openapi_payload("openapi-app-id")

            self.assertTrue(result["ready"])
            written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(written["115_app_id"], "openapi-app-id")
            self.assertEqual(written["access_token"], "")
            self.assertEqual(written["refresh_token"], "")
            self.assertFalse(token_path.exists())
            self.assertIsNone(init.openapi_115)
            load_mock.assert_called_once()

    def test_config_menu_exposes_115_and_optional_configuration(self):
        keyboard = build_config_keyboard()
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertEqual(callback_data, ["config_select:115", "config_select:optional", "config_cancel"])
        self.assertIn("可选服务配置", labels)

    def test_115_select_shows_openapi_or_direct_token_routes(self):
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "config_select:115"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}
        init.check_user = Mock(return_value=True)

        state = asyncio.run(select_config_item(update, context))

        self.assertEqual(state, CONFIG_SELECT_115_MODE)
        keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("config_115_mode:openapi", callback_data)
        self.assertIn("config_115_mode:tokens", callback_data)

    def test_optional_select_shows_service_routes(self):
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "config_select:optional"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}
        init.check_user = Mock(return_value=True)

        state = asyncio.run(select_config_item(update, context))

        self.assertEqual(state, CONFIG_SELECT_OPTIONAL_ITEM)
        keyboard = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(
            callback_data,
            [
                "config_optional:prowlarr",
                "config_optional:plex",
                "config_optional:tvdb",
                "config_optional:ai",
                "config_back",
                "config_cancel",
            ],
        )

    @patch("app.handlers.config_handler.apply_115_token_values", return_value={"ready": True})
    def test_115_token_conversation_collects_access_then_refresh(self, apply_mock):
        update = Mock()
        update.message.text = "access-from-chat"
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(receive_115_access_token(update, context))

        self.assertEqual(state, CONFIG_INPUT_115_REFRESH)
        self.assertEqual(context.user_data["config_115_access_token"], "access-from-chat")

        update.message.text = "refresh-from-chat"
        state = asyncio.run(receive_115_refresh_token(update, context))

        self.assertEqual(state, -1)
        apply_mock.assert_called_once_with("access-from-chat", "refresh-from-chat")
        update.message.reply_text.assert_awaited()


if __name__ == "__main__":
    unittest.main()

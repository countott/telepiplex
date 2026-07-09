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
    CONFIG_INPUT_PLEX_TOKEN,
    CONFIG_INPUT_TVDB_PIN,
    CONFIG_SELECT_115_MODE,
    CONFIG_SELECT_TVDB_PIN,
    apply_115_token_payload,
    apply_115_openapi_payload,
    apply_optional_token_payload,
    receive_115_access_token,
    receive_115_refresh_token,
    receive_plex_base_url,
    receive_plex_token,
    receive_tvdb_api_key,
    select_115_mode,
    select_tvdb_pin_option,
    missing_optional_config_labels,
    parse_key_value_lines,
    register_config_handlers,
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
                        "metadata": {"tvdb": {"enable": False}},
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

    def test_apply_optional_token_payload_writes_tvdb_and_plex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("metadata: {}\nmedia: {}\n", encoding="utf-8")
            old_config_file = init.CONFIG_FILE
            init.CONFIG_FILE = str(config_path)
            self.addCleanup(setattr, init, "CONFIG_FILE", old_config_file)
            init.load_yaml_config = Mock()

            apply_optional_token_payload("tvdb", "api_key=tvdb-key\nsubscriber_pin=pin-1")
            apply_optional_token_payload("plex", "base_url=http://plex:32400\ntoken=plex-token\nlibrary_id=2")

            written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                written["metadata"]["tvdb"],
                {
                    "enable": True,
                    "base_url": "https://api4.thetvdb.com/v4",
                    "api_key": "tvdb-key",
                    "subscriber_pin": "pin-1",
                    "timeout": 15,
                },
            )
            self.assertEqual(
                written["media"]["plex"],
                {"base_url": "http://plex:32400", "token": "plex-token"},
            )

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

    def test_115_openapi_route_prompts_for_app_id(self):
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "config_115_mode:openapi"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}
        init.check_user = Mock(return_value=True)

        state = asyncio.run(select_115_mode(update, context))

        self.assertNotEqual(state, CONFIG_INPUT_115_REFRESH)
        self.assertIn("115_app_id", update.callback_query.edit_message_text.await_args.args[0])

    @patch("app.handlers.config_handler.apply_tvdb_values", return_value={"ready": True})
    def test_tvdb_conversation_collects_api_then_optional_pin_choice(self, apply_mock):
        update = Mock()
        update.message.text = "tvdb-api-key"
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(receive_tvdb_api_key(update, context))

        self.assertEqual(state, CONFIG_SELECT_TVDB_PIN)
        self.assertEqual(context.user_data["config_tvdb_api_key"], "tvdb-api-key")

        callback_update = Mock()
        callback_update.effective_user.id = 472943219
        callback_update.callback_query.data = "config_tvdb_pin:none"
        callback_update.callback_query.answer = AsyncMock()
        callback_update.callback_query.edit_message_text = AsyncMock()
        init.check_user = Mock(return_value=True)

        state = asyncio.run(select_tvdb_pin_option(callback_update, context))

        self.assertEqual(state, -1)
        apply_mock.assert_called_once_with("tvdb-api-key", subscriber_pin="")

    @patch("app.handlers.config_handler.apply_plex_values", return_value={"ready": True})
    def test_plex_conversation_collects_base_url_then_token_without_library_id(self, apply_mock):
        update = Mock()
        update.message.text = "http://plex:32400"
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(receive_plex_base_url(update, context))

        self.assertEqual(state, CONFIG_INPUT_PLEX_TOKEN)
        self.assertEqual(context.user_data["config_plex_base_url"], "http://plex:32400")

        update.message.text = "plex-token"
        state = asyncio.run(receive_plex_token(update, context))

        self.assertEqual(state, -1)
        apply_mock.assert_called_once_with("http://plex:32400", "plex-token")

    def test_apply_optional_token_payload_reports_invalid_tvdb_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("metadata: {}\n", encoding="utf-8")
            old_config_file = init.CONFIG_FILE
            init.CONFIG_FILE = str(config_path)
            self.addCleanup(setattr, init, "CONFIG_FILE", old_config_file)

            with self.assertRaisesRegex(ValueError, "TVDB timeout 必须是整数秒"):
                apply_optional_token_payload("tvdb", "api_key=tvdb-key\ntimeout=slow")

    def test_missing_optional_config_labels_detects_tvdb_and_plex(self):
        labels = missing_optional_config_labels(
            {
                "metadata": {"tvdb": {"enable": False, "api_key": ""}},
                "media": {"plex": {"base_url": "", "token": ""}},
            }
        )

        self.assertEqual(labels, ["TVDB", "Plex"])

    def test_config_inline_select_buttons_are_conversation_entry_points(self):
        from telegram.ext import CallbackQueryHandler

        app = Mock()
        old_logger = init.logger
        init.logger = Mock()
        self.addCleanup(setattr, init, "logger", old_logger)

        register_config_handlers(app)

        handler = app.add_handler.call_args.args[0]
        entry_callbacks = [
            entry.callback
            for entry in handler.entry_points
            if isinstance(entry, CallbackQueryHandler)
        ]
        self.assertIn(select_config_item, entry_callbacks)


if __name__ == "__main__":
    unittest.main()

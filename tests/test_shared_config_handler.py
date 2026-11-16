import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


class SharedConfigHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_reserved_commands_keep_navigation_and_q_fallback(self):
        from telegram import Chat, Message, MessageEntity, Update, User
        from app.handlers.config_handler import register_feature_config_handlers
        from app.handlers.shared_config_handler import CONFIG_SHARED

        application = Mock()
        register_feature_config_handlers(application)
        conversation = application.add_handler.call_args.args[0]
        path_handler = conversation.states[CONFIG_SHARED][1]
        for command in ("config", "q", "start", "reload", "START", "ReLoAd", "Q", "CoNfIg"):
            text = "/" + command
            message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(10, "private"), from_user=User(1, "Owner", False), text=text, entities=[MessageEntity("bot_command", 0, len(text))])
            message.set_bot(SimpleNamespace(username="telepiplex_test_bot"))
            update = Update(1, message)
            self.assertFalse(path_handler.check_update(update), command)
            if command.lower() == "q":
                self.assertTrue(conversation.fallbacks[0].check_update(update))
            if command.lower() == "config":
                self.assertTrue(conversation.allow_reentry)
                self.assertTrue(conversation.entry_points[0].check_update(update))

    async def test_path_shorthand_and_absolute_input_create_equivalent_drafts(self):
        from app.handlers.shared_config_handler import SESSION_KEY, shared_config_callback, shared_config_message

        for value in ("电影/动作", "/电影/动作"):
            update, context, manager = self.request()
            with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
                await shared_config_callback(update, context)
                update.callback_query.data = "host-config-shared:route:live_action_movie"
                await shared_config_callback(update, context)
                update.effective_message.text = value
                await shared_config_message(update, context)
                self.assertEqual(context.user_data[SESSION_KEY][(10, 1)]["stage"], "route_library")
                self.assertEqual(context.user_data[SESSION_KEY][(10, 1)]["routes"][0]["path"], "/电影/动作")
                update.effective_message.text = "-"
                await shared_config_message(update, context)
            self.assertEqual(context.user_data[SESSION_KEY][(10, 1)]["patch"]["category_folder"][0]["path"], "/电影/动作")
            manager.configure_shared.assert_not_awaited()

    async def test_unsafe_paths_do_not_advance_or_change_shared_draft(self):
        from app.handlers.shared_config_handler import SESSION_KEY, shared_config_callback, shared_config_message

        for value in ("/", "/a//b", "/a/./b", "/a/../b", "/a\\b", "/a\n", "/a\x7f"):
            update, context, manager = self.request()
            with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
                await shared_config_callback(update, context)
                update.callback_query.data = "host-config-shared:route:live_action_movie"
                await shared_config_callback(update, context)
                update.effective_message.text = value
                await shared_config_message(update, context)
            self.assertEqual(context.user_data[SESSION_KEY][(10, 1)]["stage"], "route_path", value)
            self.assertEqual(context.user_data[SESSION_KEY][(10, 1)]["routes"][0]["path"], "/电影")

    async def test_absolute_path_starting_with_slash_reaches_shared_editor(self):
        from telegram import Chat, Message, MessageEntity, Update, User
        from app.handlers.config_handler import register_feature_config_handlers
        from app.handlers.shared_config_handler import CONFIG_SHARED

        application = Mock()
        register_feature_config_handlers(application)
        conversation = application.add_handler.call_args.args[0]
        message_handler = conversation.states[CONFIG_SHARED][1]
        message = Message(
            message_id=1, date=datetime.now(timezone.utc), chat=Chat(10, "private"),
            from_user=User(1, "Owner", False), text="/Movies",
            entities=[MessageEntity("bot_command", 0, 7)],
        )
        self.assertTrue(message_handler.check_update(Update(1, message)))

    def request(self):
        manager = SimpleNamespace(
            central_config_enabled=True,
            store=SimpleNamespace(central_config=SimpleNamespace(read=lambda: {
                "shared": {
                    "tmdb": {"api_key": "private-token"},
                    "category_folder": [
                        {"kind": "live_action_movie", "name": "真人电影", "path": "/电影", "plex_library_id": "1"},
                        {"kind": "animated_movie", "name": "动画电影", "path": "/动画电影", "plex_library_id": "2"},
                        {"kind": "live_action_series", "name": "真人剧集", "path": "/剧集", "plex_library_id": "3"},
                        {"kind": "animated_series", "name": "动画剧集", "path": "/动画剧集", "plex_library_id": "4"},
                    ],
                },
            })),
            configure_shared=AsyncMock(return_value=SimpleNamespace(details={})),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=10),
            callback_query=SimpleNamespace(
                data="host-config-shared:open", answer=AsyncMock(), edit_message_text=AsyncMock(),
            ),
            effective_message=SimpleNamespace(text="", reply_text=AsyncMock()),
        )
        context = SimpleNamespace(user_data={}, application=SimpleNamespace(bot_data={"telepiplex_plugin_manager": manager}))
        return update, context, manager

    async def test_secret_is_drafted_without_echo_or_write_until_confirmation(self):
        from app.handlers.shared_config_handler import shared_config_callback, shared_config_message

        update, context, manager = self.request()
        with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
            await shared_config_callback(update, context)
            update.callback_query.data = "host-config-shared:tmdb"
            await shared_config_callback(update, context)
            update.effective_message.text = "replacement-secret"
            await shared_config_message(update, context)
            manager.configure_shared.assert_not_awaited()
            rendered = str(update.callback_query.edit_message_text.await_args_list) + str(update.effective_message.reply_text.await_args_list)
            self.assertNotIn("private-token", rendered)
            self.assertNotIn("replacement-secret", rendered)
            update.callback_query.data = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            await shared_config_callback(update, context)
        manager.configure_shared.assert_awaited_once_with(
            {"tmdb": {"api_key": "replacement-secret"}},
            expected={"tmdb": {"api_key": "private-token"}},
        )

    async def test_keep_token_does_not_rewrite_a_draft_snapshot(self):
        from app.handlers.shared_config_handler import shared_config_callback, shared_config_message

        update, context, manager = self.request()
        with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
            await shared_config_callback(update, context)
            update.callback_query.data = "host-config-shared:tmdb"
            await shared_config_callback(update, context)
            update.effective_message.text = "-"
            await shared_config_message(update, context)
        manager.configure_shared.assert_not_awaited()

    async def test_save_feedback_reports_only_consumers_actually_switched(self):
        from app.handlers.shared_config_handler import shared_config_callback, shared_config_message

        for affected, expected in ((["sync"], "已重新加载：sync。"), ([], "没有运行中的插件切换配置。")):
            with self.subTest(affected=affected):
                update, context, manager = self.request()
                manager.configure_shared.return_value = SimpleNamespace(details={
                    "affected_plugins": affected, "api_key": "result-secret",
                })
                with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
                    await shared_config_callback(update, context)
                    update.callback_query.data = "host-config-shared:tmdb"
                    await shared_config_callback(update, context)
                    update.effective_message.text = "replacement-secret"
                    await shared_config_message(update, context)
                    update.callback_query.data = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
                    await shared_config_callback(update, context)
                message = update.callback_query.edit_message_text.await_args.args[0]
                self.assertIn("已保存到顶层 config.yaml", message)
                self.assertIn(expected, message)
                self.assertNotIn("search", message)
                for secret in ("private-token", "replacement-secret", "result-secret"):
                    self.assertNotIn(secret, message)

    async def test_route_edit_preserves_other_kinds_and_cancel_never_writes(self):
        from app.handlers.shared_config_handler import shared_config_callback, shared_config_message

        update, context, manager = self.request()
        with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
            await shared_config_callback(update, context)
            update.callback_query.data = "host-config-shared:route:live_action_movie"
            await shared_config_callback(update, context)
            update.effective_message.text = "/新电影"
            await shared_config_message(update, context)
            update.effective_message.text = "-"
            await shared_config_message(update, context)
            update.callback_query.data = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            await shared_config_callback(update, context)
        saved = manager.configure_shared.await_args.args[0]["category_folder"]
        self.assertEqual(saved[0], {"kind": "live_action_movie", "name": "真人电影", "path": "/新电影", "plex_library_id": "1"})
        self.assertEqual(saved[1]["path"], "/动画电影")

        update, context, manager = self.request()
        with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
            await shared_config_callback(update, context)
            update.callback_query.data = "host-config-shared:cancel"
            await shared_config_callback(update, context)
        manager.configure_shared.assert_not_awaited()

    async def test_cross_chat_or_unauthorized_confirmation_cannot_save(self):
        from app.handlers.shared_config_handler import shared_config_callback, shared_config_message

        update, context, manager = self.request()
        with patch("app.handlers.shared_config_handler.init.check_user", return_value=True):
            await shared_config_callback(update, context)
            update.callback_query.data = "host-config-shared:tmdb"
            await shared_config_callback(update, context)
            update.effective_message.text = "replacement-secret"
            await shared_config_message(update, context)
            update.effective_chat.id = 11
            update.callback_query.data = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            await shared_config_callback(update, context)
        manager.configure_shared.assert_not_awaited()

import sys
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def tool_response(name="plex_server_status", arguments="{}", call_id="call-1"):
    return {
        "choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }],
        }}]
    }


def text_response(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class FakeDispatcher:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"online": True}

    def tool_schemas(self):
        return [{
            "type": "function",
            "function": {
                "name": "plex_server_status",
                "description": "status",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def dispatch(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


class PlexAIOrchestratorTest(unittest.TestCase):
    def config(self):
        return {
            "api_url": "https://ai.example/v1",
            "api_key": "secret",
            "model": "model",
            "timeout": 12,
        }

    @patch("app.services.plex_ai.requests.post")
    def test_read_tool_call_executes_and_returns_final_message(self, post):
        from app.services.plex_ai import PlexAIOrchestrator

        first = Mock()
        first.json.return_value = tool_response()
        second = Mock()
        second.json.return_value = text_response("Plex 正常运行")
        post.side_effect = [first, second]
        dispatcher = FakeDispatcher()

        result = PlexAIOrchestrator(self.config(), dispatcher).run("Plex 正常吗")

        self.assertEqual(result["message"], "Plex 正常运行")
        self.assertEqual(dispatcher.calls, [("plex_server_status", {})])
        self.assertEqual(post.call_args_list[0].args[0], "https://ai.example/v1/chat/completions")
        self.assertEqual(post.call_args_list[0].kwargs["timeout"], 12)
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer secret")
        first.raise_for_status.assert_called_once_with()
        second.raise_for_status.assert_called_once_with()

    @patch("app.services.plex_ai.requests.post")
    def test_tool_round_limit_stops_loop_at_three(self, post):
        from app.services.plex_ai import PlexAIOrchestrator

        responses = []
        for index in range(4):
            response = Mock()
            response.json.return_value = tool_response(call_id=f"call-{index}")
            responses.append(response)
        post.side_effect = responses
        dispatcher = FakeDispatcher()

        result = PlexAIOrchestrator(
            self.config(), dispatcher, max_tool_rounds=3
        ).run("循环")

        self.assertEqual(result["error"], "tool_round_limit")
        self.assertEqual(len(dispatcher.calls), 3)
        self.assertEqual(post.call_count, 3)

    @patch("app.services.plex_ai.requests.post")
    def test_write_preview_is_returned_as_confirmation_not_auto_applied(self, post):
        from app.services.plex_ai import PlexAIOrchestrator

        preview = {
            "status": "confirmation_required",
            "action": "fix_match",
            "payload": {"job_id": 1, "rating_key": "42", "candidate_guid": "tmdb://20"},
            "confirmation_token": "once",
        }
        first = Mock()
        first.json.return_value = tool_response(
            "plex_fix_match",
            '{"job_id":1,"rating_key":"42","candidate_guid":"tmdb://20"}',
        )
        second = Mock()
        second.json.return_value = text_response("需要确认匹配")
        post.side_effect = [first, second]

        result = PlexAIOrchestrator(self.config(), FakeDispatcher(preview)).run("修复匹配")

        self.assertEqual(result["confirmation"]["confirmation_token"], "once")
        self.assertEqual(result["message"], "需要确认匹配")

    @patch("app.services.plex_ai.requests.post")
    def test_ai_cannot_forward_confirmation_token_to_apply_a_write(self, post):
        from app.services.plex_ai import PlexAIOrchestrator

        first = Mock()
        first.json.return_value = tool_response(
            "plex_fix_match",
            '{"job_id":1,"rating_key":"42","candidate_guid":"tmdb://20","confirmation_token":"stolen"}',
        )
        second = Mock()
        second.json.return_value = text_response("等待用户确认")
        post.side_effect = [first, second]
        dispatcher = FakeDispatcher({"status": "confirmation_required"})

        PlexAIOrchestrator(self.config(), dispatcher).run("执行写操作")

        self.assertNotIn("confirmation_token", dispatcher.calls[0][1])


class PlexAIHandlerTest(unittest.TestCase):
    @patch("app.modules.plex_management.get_plex_ai_orchestrator")
    @patch("app.modules.plex_management.get_plex_management_service")
    def test_enabled_ai_is_initialized_in_worker_outside_telegram_event_loop(
        self,
        get_service,
        get_ai,
    ):
        from app.handlers.plex_handler import plex_command

        service = Mock(ai_enabled=True, ai=None)
        ai = Mock()
        ai.run.return_value = {"message": "Plex 正常运行"}

        def build_ai():
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return ai

        get_service.return_value = service
        get_ai.side_effect = build_ai
        update = Mock()
        update.effective_user.id = 1
        update.effective_message.reply_text = AsyncMock()
        context = Mock(args=["状态"], user_data={})

        with patch("app.handlers.plex_handler.init.check_user", return_value=True):
            asyncio.run(plex_command(update, context))

        get_ai.assert_called_once_with()
        ai.run.assert_called_once_with("状态")
        self.assertEqual(
            update.effective_message.reply_text.await_args.args[0],
            "Plex 正常运行",
        )

    @patch("app.modules.plex_management.get_plex_management_service")
    def test_disabled_ai_returns_concise_message(self, get_service):
        from app.handlers.plex_handler import plex_command

        service = Mock(ai_enabled=False, ai=None)
        get_service.return_value = service
        update = Mock()
        update.effective_user.id = 1
        update.effective_message.reply_text = AsyncMock()
        context = Mock(args=["状态"], user_data={})
        with patch("app.handlers.plex_handler.init.check_user", return_value=True):
            asyncio.run(plex_command(update, context))

        self.assertEqual(
            update.effective_message.reply_text.await_args.args[0],
            "Plex AI 管理未启用。",
        )

    @patch("app.modules.plex_management.get_plex_management_service")
    def test_write_preview_creates_telegram_confirmation_button(self, get_service):
        from app.handlers.plex_handler import plex_command

        service = Mock(ai_enabled=True)
        service.ai.run.return_value = {
            "message": "需要确认匹配",
            "confirmation": {
                "action": "fix_match",
                "payload": {"job_id": 1},
                "confirmation_token": "once",
            },
        }
        get_service.return_value = service
        update = Mock()
        update.effective_user.id = 1
        update.effective_message.reply_text = AsyncMock()
        context = Mock(args=["修复", "匹配"], user_data={})
        with patch("app.handlers.plex_handler.init.check_user", return_value=True):
            asyncio.run(plex_command(update, context))

        kwargs = update.effective_message.reply_text.await_args.kwargs
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "plex_write_confirm:once")
        self.assertEqual(context.user_data["plex_write:once"], "fix_match")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexModuleTest(unittest.TestCase):
    def test_match_notification_uses_compact_telegram_callbacks(self):
        import init
        import app.utils.message_queue
        from app.modules.plex_management import _queue_notifier

        with patch("app.utils.message_queue.add_task_to_queue") as add_task:
            _queue_notifier(1, "候选 (测试)", {
                "job_id": 9,
                "kind": "match",
                "candidates": [{"guid": "plex://movie/" + "x" * 100, "title": "电影"}],
            })

            keyboard = add_task.call_args.kwargs["keyboard"]
            message = add_task.call_args.args[2]
        callback = keyboard.inline_keyboard[0][0].callback_data
        self.assertEqual(callback, "plex_match_confirm:9:0")
        self.assertLessEqual(len(callback.encode("utf-8")), 64)
        self.assertIn(r"\(测试\)", message)

    def test_plex_module_registers_completion_hook_command_and_config(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.plex_management import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(registry.download_completion_hooks[0][0], "plex.management")
        self.assertIn("plex", [command.command for command in registry.bot_commands()])
        self.assertIn("media.plex.mcp", registry.config_sections)
        self.assertEqual(len(registry.startup_hooks), 1)

    def test_mcp_start_failure_is_isolated_from_bot_startup(self):
        import init
        import app.plex_mcp.server
        from app.modules import plex_management as module

        service = Mock(mcp_enabled=True, mcp_config={"host": "0.0.0.0"})
        original_logger = init.logger
        init.logger = Mock()
        try:
            with patch.object(module, "get_plex_management_service", return_value=service), patch(
                "app.plex_mcp.server.start_plex_mcp_server",
                side_effect=ValueError("unsafe config token=secret"),
            ):
                result = module.start_plex_module_services()
        finally:
            logger = init.logger
            init.logger = original_logger

        self.assertIsNone(result)
        service.resume_incomplete_jobs.assert_called_once_with(module.plex_executor)
        self.assertNotIn("secret", logger.error.call_args.args[0])

    @patch("app.modules.plex_management.get_plex_management_service")
    def test_unorganized_completion_is_ignored(self, get_service):
        from app.core.module_registry import DownloadCompletedEvent, DownloadPipelineCompletion, PostDownloadResult
        from app.modules.plex_management import on_download_completed

        event = DownloadCompletedEvent("link", "/电影", 1, "/未整理/a", "a")
        completion = DownloadPipelineCompletion(
            event,
            PostDownloadResult(True, final_path=event.final_path),
            "open115.unorganized_fallback",
        )

        self.assertIsNone(on_download_completed(completion))
        get_service.assert_not_called()

    @patch("app.modules.plex_management.plex_executor")
    @patch("app.modules.plex_management.get_plex_management_service")
    def test_renaming_completion_enqueues_background_job(self, get_service, executor):
        from app.core.module_registry import DownloadCompletedEvent, DownloadPipelineCompletion, PostDownloadResult
        from app.modules.plex_management import on_download_completed

        service = Mock(enabled=True)
        service.enqueue_completion.return_value = {"id": 9}
        get_service.return_value = service
        event = DownloadCompletedEvent("link", "/电影", 1, "/电影/a", "a")
        completion = DownloadPipelineCompletion(
            event,
            PostDownloadResult(True, final_path=event.final_path),
            "renaming.generic_media",
        )

        self.assertEqual(on_download_completed(completion)["id"], 9)
        executor.submit.assert_called_once_with(service.run_job, 9)

    @patch("app.modules.plex_management.plex_executor")
    @patch("app.modules.plex_management.get_plex_management_service")
    def test_rejected_contract_completion_never_submits_background_job(
        self, get_service, executor
    ):
        from app.core.module_registry import (
            DownloadCompletedEvent,
            DownloadPipelineCompletion,
            PostDownloadResult,
        )
        from app.modules.plex_management import on_download_completed

        service = Mock(enabled=True)
        service.enqueue_completion.return_value = None
        get_service.return_value = service
        event = DownloadCompletedEvent("link", "/电影", 1, "/电影/a", "a")
        completion = DownloadPipelineCompletion(
            event,
            PostDownloadResult(True, final_path=event.final_path),
            "renaming.media_metadata",
        )

        self.assertIsNone(on_download_completed(completion))
        executor.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

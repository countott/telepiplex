import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexModuleTest(unittest.TestCase):
    def test_plex_module_registers_completion_hook_command_and_config(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.plex_management import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(registry.download_completion_hooks[0][0], "plex.management")
        self.assertIn("plex", [command.command for command in registry.bot_commands()])
        self.assertIn("media.plex.mcp", registry.config_sections)
        self.assertEqual(len(registry.startup_hooks), 1)

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


if __name__ == "__main__":
    unittest.main()

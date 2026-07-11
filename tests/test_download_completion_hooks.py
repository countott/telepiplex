import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


def make_event(final_path="/下载"):
    from app.core.module_registry import DownloadCompletedEvent

    return DownloadCompletedEvent(
        link="magnet:?xt=urn:btih:" + "a" * 40,
        selected_path="/电影",
        user_id=1,
        final_path=final_path,
        resource_name="Release",
    )


class DownloadCompletionHookTest(unittest.TestCase):
    def test_hook_runs_after_terminal_processor_with_final_path(self):
        from app.core.module_registry import ModuleRegistry, PostDownloadResult

        registry = ModuleRegistry()
        seen = []
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/整理后", should_stop=True),
            priority=100,
            name="renaming.generic_media",
        )
        registry.add_download_completion_hook(seen.append, "plex.management")

        result = registry.run_post_download_pipeline(make_event())

        self.assertEqual(result.final_path, "/整理后")
        self.assertEqual(seen[0].event.final_path, "/整理后")
        self.assertEqual(seen[0].terminal_processor, "renaming.generic_media")

    def test_hook_failure_does_not_change_primary_result(self):
        from app.core.module_registry import ModuleRegistry, PostDownloadResult

        registry = ModuleRegistry()
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/整理后", should_stop=True),
            priority=100,
            name="renaming.generic_media",
        )

        def broken_hook(completion):
            raise RuntimeError("plex down")

        registry.add_download_completion_hook(broken_hook, "plex.management")

        result = registry.run_post_download_pipeline(make_event())

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/整理后")


if __name__ == "__main__":
    unittest.main()

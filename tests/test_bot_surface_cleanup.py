import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BotSurfaceCleanupTest(unittest.TestCase):
    def test_removed_commands_are_not_advertised_or_registered(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")

        for command in ("av", "csh", "cjav"):
            self.assertNotIn(f"<code>/{command}</code>", bot_source)
            self.assertNotIn(f'BotCommand("{command}"', bot_source)
            self.assertNotIn(f"CommandHandler('{command}'", bot_source)
            self.assertNotIn(f"`/{command}`", readme_source)
            self.assertNotIn(f"`/{command}`", readme_en_source)

        self.assertNotIn("register_av_download_handlers", bot_source)
        self.assertNotIn("register_crawl_handlers", bot_source)

    def test_system_notifications_do_not_attach_decorative_images(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        download_source = (ROOT / "app" / "handlers" / "download_handler.py").read_text(encoding="utf-8")
        open115_source = (ROOT / "app" / "core" / "open_115.py").read_text(encoding="utf-8")
        app_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))

        self.assertNotIn('f"{init.IMAGE_PATH}/neuter010.png"', bot_source)
        self.assertNotIn('f"{init.IMAGE_PATH}/male023.png"', bot_source)
        self.assertNotIn('f"{init.IMAGE_PATH}/male023.png"', download_source)
        self.assertNotIn('"/app/images/male023.png"', open115_source)
        self.assertNotRegex(app_source, r"add_task_to_queue\([^)]*init\.IMAGE_PATH")


if __name__ == "__main__":
    unittest.main()

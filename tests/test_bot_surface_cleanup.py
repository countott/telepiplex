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

    def test_screenshot_ocr_surface_is_removed(self):
        search_source = (ROOT / "app" / "handlers" / "search_handler.py").read_text(encoding="utf-8")
        requirements_source = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        dockerfile_source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerfile_local_source = (ROOT / "Dockerfile.local").read_text(encoding="utf-8")
        config_source = (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        app_config_source = (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")

        self.assertNotIn("filters.PHOTO", search_source)
        self.assertNotIn("search_screenshot_command", search_source)
        self.assertNotIn("search_ocr", search_source)
        self.assertFalse((ROOT / "app" / "utils" / "local_ocr.py").exists())
        self.assertFalse((ROOT / "app" / "utils" / "telegram_safe.py").exists())

        for source in (requirements_source, dockerfile_source, dockerfile_local_source, config_source, app_config_source):
            self.assertNotIn("paddleocr", source.lower())
            self.assertNotIn("paddlepaddle", source.lower())
            self.assertNotIn("tesseract", source.lower())
            self.assertNotIn("ocr", source.lower())

    def test_search_handler_is_registered_before_generic_download_links(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        self.assertLess(bot_source.index("register_search_handlers(application)"), bot_source.index("register_download_handlers(application)"))

    def test_external_douban_api_deployment_is_removed(self):
        self.assertFalse((ROOT / "deploy" / "douban-api").exists())

        config_source = (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        app_config_source = (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("douban_api:", config_source)
        self.assertNotIn("douban_api:", app_config_source)
        self.assertNotIn("deploy/douban-api", readme_source)


if __name__ == "__main__":
    unittest.main()

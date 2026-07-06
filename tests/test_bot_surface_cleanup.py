import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BotSurfaceCleanupTest(unittest.TestCase):
    def test_removed_commands_are_not_advertised_or_registered(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")

        for command in ("av", "csh", "cjav", "rss"):
            self.assertNotIn(f"<code>/{command}</code>", bot_source)
            self.assertNotIn(f'BotCommand("{command}"', bot_source)
            self.assertNotIn(f"CommandHandler('{command}'", bot_source)
            self.assertNotIn(f"`/{command}`", readme_source)
            self.assertNotIn(f"`/{command}`", readme_en_source)

        self.assertNotIn("register_av_download_handlers", bot_source)
        self.assertNotIn("register_crawl_handlers", bot_source)
        self.assertNotIn("register_rss_handlers", bot_source)

    def test_adult_rss_and_tmdb_subscription_code_is_removed_but_aria_helpers_remain(self):
        removed_paths = [
            ROOT / "app" / "handlers" / "av_download_handler.py",
            ROOT / "app" / "handlers" / "crawl_handler.py",
            ROOT / "app" / "handlers" / "rss_handler.py",
            ROOT / "app" / "handlers" / "subscribe_movie_handler.py",
            ROOT / "app" / "core" / "av_daily_update.py",
            ROOT / "app" / "core" / "sehua_spider.py",
            ROOT / "app" / "core" / "javbus.py",
            ROOT / "app" / "core" / "t66y.py",
            ROOT / "app" / "core" / "subscribe_movie.py",
        ]
        for path in removed_paths:
            self.assertFalse(path.exists(), str(path))

        self.assertTrue((ROOT / "app" / "handlers" / "aria2_handler.py").exists())
        self.assertTrue((ROOT / "app" / "utils" / "aria2.py").exists())
        self.assertTrue((ROOT / "app" / "utils" / "ai.py").exists())
        self.assertTrue((ROOT / "app" / "utils" / "cover_capture.py").exists())

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

    def test_startup_logs_current_search_runtime_features(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        self.assertIn("direct_metadata_link_search=enabled", bot_source)
        self.assertIn("builtin_douban_title_priority=latin_or_original_first", bot_source)
        self.assertIn("external_metadata_douban_reverse_lookup=enabled", bot_source)
        self.assertIn("prowlarr_indexer_summary=enabled", bot_source)

    def test_scheduler_and_config_do_not_expose_removed_adult_or_tmdb_pipelines(self):
        scheduler_source = (ROOT / "app" / "core" / "scheduler.py").read_text(encoding="utf-8")
        init_source = (ROOT / "app" / "init.py").read_text(encoding="utf-8")
        app_config_source = (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        app_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))

        removed_terms = [
            "av_daily_update",
            "sehua_spider",
            "rsshub",
            "register_rss_handlers",
            "register_subscribe_movie_handlers",
            "指定标准的TMDB名称",
            "指定TMDB名称并添加到重试列表",
        ]
        for source in (scheduler_source, init_source, app_config_source, readme_source, readme_en_source, app_source):
            for term in removed_terms:
                self.assertNotIn(term, source)

        self.assertIn("aria2:", app_config_source)
        self.assertIn("register_aria2_handlers", (ROOT / "app" / "115bot.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

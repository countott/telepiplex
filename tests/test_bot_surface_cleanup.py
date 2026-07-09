import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BotSurfaceCleanupTest(unittest.TestCase):
    def test_removed_commands_are_not_advertised_or_registered(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")

        for command in ("av", "csh", "cjav", "rss", "find"):
            self.assertNotIn(f"<code>/{command}</code>", bot_source)
            self.assertNotIn(f'BotCommand("{command}"', bot_source)
            self.assertNotIn(f"CommandHandler('{command}'", bot_source)
            self.assertNotIn(f'CommandHandler("{command}"', bot_source)
            self.assertNotIn(f"`/{command}`", readme_source)
            self.assertNotIn(f"`/{command}`", readme_en_source)

        self.assertNotIn("register_av_download_handlers", bot_source)
        self.assertNotIn("register_crawl_handlers", bot_source)
        self.assertNotIn("register_rss_handlers", bot_source)

    def test_search_magnet_and_retry_are_the_public_commands(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        search_source = (ROOT / "app" / "handlers" / "search_handler.py").read_text(encoding="utf-8")
        download_source = (ROOT / "app" / "handlers" / "download_handler.py").read_text(encoding="utf-8")
        offline_source = (ROOT / "app" / "handlers" / "offline_task_handler.py").read_text(encoding="utf-8")

        self.assertIn('BotCommand("search"', bot_source)
        self.assertIn('BotCommand("s"', bot_source)
        self.assertIn('BotCommand("magnet"', bot_source)
        self.assertIn('BotCommand("m"', bot_source)
        self.assertIn('BotCommand("retry"', bot_source)
        self.assertIn('BotCommand("r"', bot_source)
        self.assertIn('CommandHandler("search"', search_source)
        self.assertIn('CommandHandler("s"', search_source)
        self.assertIn('CommandHandler("magnet"', download_source)
        self.assertIn('CommandHandler("m"', download_source)
        self.assertIn('CommandHandler("retry"', offline_source)
        self.assertIn('CommandHandler("r"', offline_source)
        self.assertNotRegex(bot_source, r'BotCommand\("mag"\s*,')
        self.assertNotRegex(bot_source, r'BotCommand\("rl"\s*,')
        self.assertNotRegex(bot_source, r'BotCommand\("sync"\s*,')
        self.assertNotRegex(download_source, r'CommandHandler\("mag"\s*,')
        self.assertNotRegex(offline_source, r'CommandHandler\("rl"\s*,')
        self.assertNotIn('BotCommand("strm"', bot_source)
        self.assertNotIn('CommandHandler("strm"', bot_source)
        self.assertNotIn("`/sync`", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertNotIn("`/sync`", (ROOT / "README_EN.md").read_text(encoding="utf-8"))
        self.assertNotIn('CommandHandler("find"', search_source)
        self.assertNotIn("find_command", search_source)
        self.assertFalse((ROOT / "app" / "handlers" / "sync_handler.py").exists())

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
        self.assertIn("metadata_object=enabled", bot_source)
        self.assertIn("search_command=enabled", bot_source)
        self.assertIn("search_short_command=enabled", bot_source)
        self.assertIn("magnet_command=enabled", bot_source)
        self.assertIn("find_command_removed=enabled", bot_source)
        self.assertIn("retry_command=enabled", bot_source)
        self.assertNotIn("strm_command=enabled", bot_source)
        self.assertIn("tvdb_adapter=enabled", bot_source)
        self.assertIn("ai_tvdb_inference=enabled", bot_source)
        self.assertIn("tvdb_ai_115_tree_rename=enabled", bot_source)

    def test_legacy_search_resolve_metadata_state_is_removed(self):
        search_source = (ROOT / "app" / "handlers" / "search_handler.py").read_text(encoding="utf-8")

        self.assertNotIn("SEARCH_RESOLVE_METADATA", search_source)
        self.assertNotIn("resolve_plain_search_metadata", search_source)
        self.assertNotIn("pending_plain_search_query", search_source)

    def test_legacy_bulk_retry_callbacks_are_removed(self):
        download_source = (ROOT / "app" / "handlers" / "download_handler.py").read_text(encoding="utf-8")
        scheduler_source = (ROOT / "app" / "core" / "scheduler.py").read_text(encoding="utf-8")

        self.assertNotIn("handle_retry_callback", download_source)
        self.assertNotIn("callback_data=f\"retry_", download_source)
        self.assertNotIn("cancel_download", download_source)
        self.assertNotIn("try_to_offline2115_again", scheduler_source)
        self.assertNotIn("retry_failed_downloads", scheduler_source)

    def test_runtime_config_logging_uses_redacted_snapshot(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        self.assertIn("sanitize_config_for_log", bot_source)
        self.assertNotIn("json.dumps(init.bot_config)", bot_source)

    def test_english_readme_uses_media_naming_not_plex_naming(self):
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")

        self.assertNotIn("automatic Plex naming", readme_en_source)
        self.assertIn("media-library naming", readme_en_source)

    def test_strm_and_emby_surfaces_are_removed(self):
        bot_source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")
        download_source = (ROOT / "app" / "handlers" / "download_handler.py").read_text(encoding="utf-8")
        open115_source = (ROOT / "app" / "core" / "open_115.py").read_text(encoding="utf-8")
        readme_source = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en_source = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        config_source = (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        app_config_source = (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")

        for source in (bot_source, readme_source, readme_en_source):
            self.assertNotIn("`/strm`", source)
            self.assertNotIn("<code>/strm</code>", source)

        for source in (download_source, open115_source, config_source, app_config_source, readme_source, readme_en_source):
            self.assertNotIn("emby", source.lower())
            self.assertNotIn("strm", source.lower())
            self.assertNotIn("mount_root", source)
            self.assertNotIn("openlist_root", source)

        self.assertNotIn("register_sync_handlers", bot_source)
        self.assertNotIn("get_sync_dir", open115_source)
        self.assertFalse((ROOT / "app" / "core" / "offline_task_retry.py").exists())
        self.assertNotIn("offline_task_retry", open115_source)

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

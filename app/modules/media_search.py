# -*- coding: utf-8 -*-


def _register_handlers(application):
    from app.handlers.search_handler import register_search_handlers

    register_search_handlers(application)


def register_module(registry):
    registry.add_commands(
        [
            ("search", "搜索片源"),
            ("s", "搜索片源"),
        ]
    )
    registry.add_config_sections(
        [
            "search.prowlarr",
            "metadata.wikipedia",
            "metadata.douban",
            "metadata.tvdb",
            "ai",
        ]
    )
    registry.add_handlers(_register_handlers)

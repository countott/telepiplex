# -*- coding: utf-8 -*-

from app.core.module_registry import PostDownloadResult


class Open115DownloadProvider:
    def submit(self, request):
        from app.handlers.download_handler import download_executor, download_task

        return download_executor.submit(download_task, request)


class Open115StorageProvider:
    def __getattr__(self, name):
        import init

        if init.openapi_115 is None:
            raise RuntimeError("115 OpenAPI 尚未初始化")
        return getattr(init.openapi_115, name)


def _register_handlers(application):
    from app.handlers.auth_handler import register_auth_handlers
    from app.handlers.config_handler import register_config_handlers
    from app.handlers.download_handler import register_download_handlers

    register_config_handlers(application)
    register_auth_handlers(application)
    register_download_handlers(application)


def _startup(_application=None):
    import init

    if init.openapi_115 is None:
        init.initialize_115open()


def process_unorganized_fallback(event):
    import init

    unorganized_path = str(((init.bot_config or {}).get("media") or {}).get("unorganized_path") or "").rstrip("/")
    if not unorganized_path:
        return PostDownloadResult(False, final_path=event.final_path)

    source_path = str(event.final_path or "").rstrip("/")
    source_leaf = source_path.rsplit("/", 1)[-1] or str(event.resource_name or "").strip("/")
    target_path = f"{unorganized_path}/{source_leaf}"
    if source_path == target_path:
        return PostDownloadResult(
            True,
            final_path=target_path,
            message=f"✅ 离线下载完成，已保存在未整理目录。\n\n保存目录：`{target_path}`",
            should_stop=True,
        )

    storage = event.storage
    if storage is None:
        raise RuntimeError("未整理兜底失败：下载事件缺少 storage provider")
    if not storage.create_dir_recursive(unorganized_path):
        raise RuntimeError(f"未整理兜底失败：无法创建目录 {unorganized_path}")
    if storage.move_file(source_path, unorganized_path) is not True:
        raise RuntimeError(f"未整理兜底失败：无法移动 {source_path}")

    return PostDownloadResult(
        True,
        final_path=target_path,
        message=f"✅ 离线下载完成，已转入未整理目录。\n\n保存目录：`{target_path}`",
        should_stop=True,
    )


def register_module(registry):
    registry.add_commands(
        [
            ("auth", "115 扫码授权"),
            ("config", "配置 115 Token"),
            ("magnet", "投递磁力链接"),
            ("m", "投递磁力链接"),
            ("q", "退出当前会话"),
        ]
    )
    registry.add_config_sections(["115", "open115"])
    registry.set_download_provider(Open115DownloadProvider())
    registry.set_storage_provider(Open115StorageProvider())
    registry.add_handlers(_register_handlers)
    registry.add_startup_hook(_startup)
    registry.add_post_download_processor(
        process_unorganized_fallback,
        priority=900,
        name="open115.unorganized_fallback",
    )

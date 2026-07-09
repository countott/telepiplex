# -*- coding: utf-8 -*-


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


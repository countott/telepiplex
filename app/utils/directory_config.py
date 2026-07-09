# -*- coding: utf-8 -*-


def get_save_directories(config=None):
    if config is None:
        import init

        config = init.bot_config

    directories = []
    for item in (config or {}).get("category_folder") or []:
        if not isinstance(item, dict):
            continue
        if item.get("path"):
            name = item.get("name") or item.get("display_name") or item.get("path")
            directories.append({"name": str(name), "path": _normalize_path(item["path"])})
    return directories


def _normalize_path(path: str) -> str:
    normalized = "/" + str(path or "").strip("/")
    return normalized.rstrip("/") or "/"

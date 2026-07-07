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
            directory = {"name": str(name), "path": str(item["path"])}
            if item.get("plex_library_id") is not None:
                directory["plex_library_id"] = str(item["plex_library_id"])
            directories.append(directory)
            continue

        for nested in item.get("path_map") or []:
            if not isinstance(nested, dict) or not nested.get("path"):
                continue
            name = nested.get("name") or nested.get("display_name") or nested.get("path")
            directory = {"name": str(name), "path": str(nested["path"])}
            if nested.get("plex_library_id") is not None:
                directory["plex_library_id"] = str(nested["plex_library_id"])
            directories.append(directory)

    return directories


def _normalize_path(path: str) -> str:
    normalized = "/" + str(path or "").strip("/")
    return normalized.rstrip("/") or "/"


def find_save_directory_for_path(path: str, config=None):
    target_path = _normalize_path(path)
    matches = []
    for directory in get_save_directories(config):
        directory_path = _normalize_path(directory.get("path"))
        if target_path == directory_path or target_path.startswith(f"{directory_path}/"):
            matches.append((len(directory_path), directory))
    if not matches:
        return None
    return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]


def get_plex_library_id_for_path(path: str, config=None):
    if config is None:
        import init

        config = init.bot_config

    directory = find_save_directory_for_path(path, config)
    if directory and directory.get("plex_library_id"):
        return str(directory["plex_library_id"]).strip()

    plex_config = ((config or {}).get("media") or {}).get("plex") or {}
    for item in plex_config.get("libraries") or []:
        if not isinstance(item, dict) or not item.get("path") or item.get("library_id") is None:
            continue
        directory_path = _normalize_path(item["path"])
        target_path = _normalize_path(path)
        if target_path == directory_path or target_path.startswith(f"{directory_path}/"):
            return str(item["library_id"]).strip()

    return str(plex_config.get("library_id") or "").strip()

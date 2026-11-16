"""Current Host configuration facts used by the runtime and factory template."""
from copy import deepcopy


LOG_LEVEL = "info"
PLUGIN_DEFAULTS = {
    "root": "/config/plugins",
    "runtime_root": "/tmp/telepiplex",
    "catalog": "https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml",
    "catalog_refresh_interval": 21600,
    "install_timeout": 300,
    "startup_timeout": 30,
    "drain_timeout": 120,
    "stabilize_seconds": 10,
    "restart_limit": 3,
    "event_retry_interval": 1,
    "event_delivery_timeout": 1800,
    "event_max_attempts": 5,
}
EXAMPLE_CREDENTIALS = {"bot_token": "your_bot_token", "allowed_user": 123456789}
TMDB_TOKEN = ""
MEDIA_CATEGORIES = (
    ("live_action_movie", "真人电影"), ("animated_movie", "动画电影"),
    ("live_action_series", "真人剧集"), ("animated_series", "动画剧集"),
)
GITHUB_PROVIDERS = ("nekomoe", "mingy", "kitauji", "haruhana")


def host_defaults():
    return {"log_level": LOG_LEVEL, "plugins": deepcopy(PLUGIN_DEFAULTS)}


def host_schema():
    types = {
        "catalog_refresh_interval": "number", "install_timeout": "number",
        "startup_timeout": "number", "drain_timeout": "number", "stabilize_seconds": "number",
        "event_retry_interval": "number", "event_delivery_timeout": "number",
        "restart_limit": "integer", "event_max_attempts": "integer",
    }
    plugins = {key: {"type": types.get(key, "string")} for key in PLUGIN_DEFAULTS}
    return {"type": "object", "properties": {
        "log_level": {"type": "string"}, "bot_token": {"type": "string", "writeOnly": True},
        "allowed_user": {"type": ["integer", "string"]}, "plugins": {"type": "object", "properties": plugins}}}


def category_defaults():
    return [{"kind": kind, "name": name, "path": "/" + name, "plex_library_id": ""}
            for kind, name in MEDIA_CATEGORIES]


def shared_defaults():
    directories = {}
    categories = {}
    for row in category_defaults():
        kind = row["kind"]
        directories[kind] = {"name": row["name"], "path": row["path"]}
        categories[kind] = {"directory_id": kind, "label": row["name"], "plex_library_id": row["plex_library_id"]}
    roles = {"media_categories": categories, "manual_downloads": []}
    for role, name, path in (("unorganized_inventory", "额外盘点", "/未整理"),
                             ("standalone_subtitles", "独立字幕导出", "/字幕")):
        directories[role] = {"name": name, "path": path}
        roles[role] = {"directory_id": role}
    return {"directories": directories, "directory_roles": roles, "tmdb": {"api_key": TMDB_TOKEN}}

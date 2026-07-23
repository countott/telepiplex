from __future__ import annotations

from html import escape

from telegram import BotCommand


HOST_COMMANDS = (
    ("start", "获取核心状态"),
    ("reload", "重载配置"),
    ("plugin", "安装和管理 Feature"),
    ("config", "配置 Feature"),
)
HOST_RESERVED_COMMANDS = frozenset(command for command, _ in HOST_COMMANDS)
LEGACY_HIDDEN_COMMANDS = frozenset({"auth", "q"})


def active_feature_routes(router):
    if router is None:
        return []
    snapshot = getattr(router, "snapshot", None)
    plugin_ids = tuple(getattr(snapshot, "plugin_ids", ()) or ())
    routes = []
    for plugin_id in sorted(plugin_ids):
        route = router.plugin_route(plugin_id)
        if route is not None:
            routes.append(route)
    return routes


def command_is_advertised(declaration) -> bool:
    if declaration.name in HOST_RESERVED_COMMANDS:
        return False
    if declaration.menu_visible is not None:
        return declaration.menu_visible
    return (
        declaration.name not in LEGACY_HIDDEN_COMMANDS
        and not declaration.name.endswith("_config")
    )


def advertised_feature_commands(route):
    return tuple(
        declaration
        for declaration in route.manifest.commands
        if command_is_advertised(declaration)
    )


def build_bot_commands(router) -> list[BotCommand]:
    commands = [BotCommand(command, description) for command, description in HOST_COMMANDS]
    seen = set(HOST_RESERVED_COMMANDS)
    for route in active_feature_routes(router):
        for declaration in advertised_feature_commands(route):
            if declaration.name in seen:
                continue
            commands.append(BotCommand(
                declaration.name,
                _telegram_description(declaration.description),
            ))
            seen.add(declaration.name)
    return commands


def build_start_help(router, host_version: str) -> str:
    lines = [
        f"<b>Telepiplex {escape(str(host_version))}</b>",
        "",
        "<b>Telepiplex</b>",
    ]
    lines.extend(
        f"<code>/{escape(command)}</code> - {escape(description)}"
        for command, description in HOST_COMMANDS
    )
    feature_count = 0
    for route in active_feature_routes(router):
        declarations = advertised_feature_commands(route)
        if not declarations:
            continue
        feature_count += 1
        lines.extend([
            "",
            f"<b>{escape(route.manifest.name)}</b> "
            f"<code>{escape(route.plugin_id)}</code>",
        ])
        lines.extend(
            f"<code>/{escape(declaration.name)}</code> - "
            f"{escape(declaration.description)}"
            for declaration in declarations
        )
    if not feature_count:
        lines.extend(["", "当前没有已启用且可路由的 Feature 命令。"])
    return "\n".join(lines)


async def sync_bot_commands(application, router) -> bool:
    try:
        await application.bot.set_my_commands(build_bot_commands(router))
        return True
    except Exception:
        return False


def _telegram_description(value: str) -> str:
    text = str(value).strip()
    if len(text) <= 256:
        return text
    return text[:255].rstrip() + "…"

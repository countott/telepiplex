from __future__ import annotations

from html import escape

from telegram import BotCommand


CORE_COMMANDS = (
    ("start", "获取核心状态"),
    ("reload", "重载配置"),
    ("plugin", "安装和管理 Feature"),
    ("config", "配置 Feature"),
)
CORE_RESERVED_COMMANDS = frozenset(command for command, _ in CORE_COMMANDS)


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


def build_bot_commands(router) -> list[BotCommand]:
    commands = [BotCommand(command, description) for command, description in CORE_COMMANDS]
    seen = set(CORE_RESERVED_COMMANDS)
    for route in active_feature_routes(router):
        for declaration in route.manifest.commands:
            if declaration.name in seen:
                continue
            commands.append(BotCommand(
                declaration.name,
                _telegram_description(declaration.description),
            ))
            seen.add(declaration.name)
    return commands


def build_start_help(router, core_version: str) -> str:
    lines = [
        f"<b>Telepiplex Core {escape(str(core_version))}</b>",
        "",
        "<b>Core</b>",
    ]
    lines.extend(
        f"<code>/{escape(command)}</code> - {escape(description)}"
        for command, description in CORE_COMMANDS
    )
    feature_count = 0
    for route in active_feature_routes(router):
        declarations = [
            declaration
            for declaration in route.manifest.commands
            if declaration.name not in CORE_RESERVED_COMMANDS
        ]
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

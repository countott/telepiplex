from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class PluginUpdateMonitor:
    """Periodically discover Feature updates and notify once per transition."""

    def __init__(
        self,
        manager,
        notify: Callable[[object], Awaitable[bool]],
        *,
        interval: float = 21600,
        logger=None,
    ):
        self.manager = manager
        self.notify = notify
        self.interval = max(300.0, float(interval))
        self.logger = logger
        self._notified: set[tuple[str, str, str]] = set()

    async def run_once(self) -> list[object]:
        try:
            updates = await self.manager.available_updates()
        except Exception as exc:
            error_code = str(
                getattr(exc, "code", type(exc).__name__)
            )[:100]
            self._warn(
                "Feature 更新目录检查失败；本轮已跳过，Core 将继续运行："
                f"{error_code}"
            )
            return []

        notified = []
        for update in updates:
            transition = (
                str(update.plugin_id),
                str(update.current_version),
                str(update.target_version),
            )
            if transition in self._notified:
                continue
            try:
                sent = await self.notify(update)
            except Exception as exc:
                self._warn(
                    "Feature 更新通知发送失败；稍后将重试："
                    f"{type(exc).__name__}"
                )
                continue
            if sent:
                self._notified.add(transition)
                notified.append(update)
        return notified

    async def run(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.interval)

    def _warn(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warn(message)

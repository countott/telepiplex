from __future__ import annotations

import asyncio

from app.core.capability_router import CapabilityRouter
from app.core.event_journal import EventJournal
from app.core.plugin_contract import ContractError


_POISON_CODES = {
    "internal_error", "invalid_request", "not_found", "method_not_allowed",
    "invalid_callback", "english_title_missing",
}


class EventDispatcher:
    def __init__(
        self,
        router: CapabilityRouter,
        journal: EventJournal,
        *,
        retry_interval: float = 1,
        delivery_deadline: float = 30,
        batch_size: int = 100,
        max_attempts: int = 5,
    ):
        self.router = router
        self.journal = journal
        self.retry_interval = max(0.01, float(retry_interval))
        self.delivery_deadline = max(0.1, float(delivery_deadline))
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self._wake = asyncio.Event()
        self._closed = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._task is None or self._task.done():
            self._closed.clear()
            self._task = asyncio.create_task(self._run())

    async def close(self):
        self._closed.set()
        self._wake.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def wake(self):
        self._wake.set()

    async def _run(self):
        while not self._closed.is_set():
            await self.deliver_once()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.retry_interval)
            except TimeoutError:
                pass

    async def deliver_once(self) -> int:
        delivered = 0
        for plugin_id in self.router.snapshot.plugin_ids:
            route = self.router.plugin_route(plugin_id)
            if route is None:
                continue
            for event in self.journal.pending(plugin_id, self.batch_size):
                try:
                    await route.client.request(
                        "event.deliver",
                        {
                            "event_id": event.event_id,
                            "event_type": event.event_type,
                            "payload": event.payload,
                        },
                        deadline=self.delivery_deadline,
                        idempotency_key=event.event_id,
                    )
                except Exception as exc:
                    if isinstance(exc, ContractError) and exc.code in _POISON_CODES:
                        self.journal.record_failure(
                            event.event_id, plugin_id, exc.code, self.max_attempts,
                        )
                    continue
                if self.journal.ack(event.event_id, plugin_id):
                    delivered += 1
        return delivered

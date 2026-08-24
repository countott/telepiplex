from __future__ import annotations

import asyncio

from app.runtime.capability_router import CapabilityRouter
from app.runtime.event_journal import EventJournal
from app.runtime.interaction_coordinator import TERMINAL_STATES
from app.runtime.plugin_contract import ContractError


_POISON_CODES = {
    "invalid_request", "not_found", "method_not_allowed",
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
        operation_coordinator=None,
    ):
        self.router = router
        self.journal = journal
        self.retry_interval = max(0.01, float(retry_interval))
        self.delivery_deadline = max(0.1, float(delivery_deadline))
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self.operation_coordinator = operation_coordinator
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
        self._reconcile_dead_letters()
        for plugin_id in self.router.snapshot.plugin_ids:
            route = self.router.plugin_route(plugin_id)
            if route is None:
                continue
            for event in self.journal.pending(plugin_id, self.batch_size):
                if not self._ensure_handoff_binding(
                    event.event_id,
                    plugin_id,
                    event.payload,
                ):
                    continue
                if self._operation_is_terminal(event.payload):
                    if self.journal.ack(event.event_id, plugin_id):
                        delivered += 1
                    continue
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
                        exhausted = self.journal.record_failure(
                            event.event_id, plugin_id, exc.code, self.max_attempts,
                        )
                        if exhausted:
                            self._project_dead_letter(
                                event.event_id,
                                plugin_id,
                                exc.code,
                            )
                    continue
                if self.journal.ack(event.event_id, plugin_id):
                    delivered += 1
        return delivered

    def _reconcile_dead_letters(self) -> None:
        try:
            pending = self.journal.unprojected_dead_letters(self.batch_size)
        except Exception:
            return
        for failure in pending:
            self._project_dead_letter(
                str(failure.get("event_id") or ""),
                str(failure.get("plugin_id") or ""),
                str(failure.get("last_error") or "delivery_failed"),
            )

    def _project_dead_letter(
        self,
        event_id: str,
        plugin_id: str,
        error_code: str,
    ) -> str:
        coordinator = self.operation_coordinator
        try:
            binding = self.journal.handoff_binding(event_id)
        except Exception:
            return "retry"
        if binding is None:
            try:
                payload = self.journal.event_payload(event_id)
            except Exception:
                return "retry"
            if payload is None:
                return "retry"
            if not self._ensure_handoff_binding(event_id, plugin_id, payload):
                return "binding_pending"
            try:
                binding = self.journal.handoff_binding(event_id)
            except Exception:
                return "retry"
        elif binding.target_plugin_id != plugin_id:
            self._ensure_handoff_binding(event_id, plugin_id)
            try:
                marked = self.journal.mark_dead_letter_projected(
                    event_id,
                    plugin_id,
                )
            except Exception:
                return "retry"
            return "not_applicable" if marked else "already_applied"
        elif not self._ensure_handoff_binding(event_id, plugin_id):
            return "binding_pending"
        if binding is not None and binding.target_plugin_id != plugin_id:
            try:
                marked = self.journal.mark_dead_letter_projected(
                    event_id,
                    plugin_id,
                )
            except Exception:
                return "retry"
            return "not_applicable" if marked else "already_applied"
        if coordinator is None:
            if binding is not None:
                return "binding_pending"
            try:
                marked = self.journal.mark_dead_letter_projected(
                    event_id,
                    plugin_id,
                )
            except Exception:
                return "retry"
            return "not_applicable" if marked else "already_applied"
        try:
            receipt = coordinator.fail_handoff_delivery(
                event_id,
                plugin_id,
                error_code,
            )
            if binding is not None and receipt is None:
                return "binding_pending"
            marked = self.journal.mark_dead_letter_projected(event_id, plugin_id)
        except Exception:
            return "retry"
        if not marked:
            return "already_applied"
        return "applied" if receipt is not None else "not_applicable"

    def _ensure_handoff_binding(
        self,
        event_id: str,
        plugin_id: str,
        payload: dict | None = None,
    ) -> bool:
        try:
            binding = self.journal.handoff_binding(event_id)
        except Exception:
            return False
        if binding is None:
            if payload is None:
                try:
                    payload = self.journal.event_payload(event_id)
                except Exception:
                    return False
                if payload is None:
                    return False
            operation_id = str((payload or {}).get("operation_id") or "").strip()
            if not operation_id:
                return True
            coordinator = self.operation_coordinator
            if coordinator is None:
                return False
            try:
                operation = coordinator.get(operation_id)
            except Exception:
                return False
            if operation is None or operation.state != "handed_off":
                return True
            if (
                not plugin_id
                or operation.next_plugin_id != plugin_id
                or not operation.plugin_id
            ):
                return False
            try:
                receipt = coordinator.capture_handoff(
                    operation_id,
                    operation.plugin_id,
                )
                if (
                    receipt is None
                    or receipt.operation_id != operation_id
                    or receipt.source_plugin_id != operation.plugin_id
                    or receipt.target_plugin_id != plugin_id
                ):
                    return False
                binding = self.journal.attach_handoff_binding(
                    event_id,
                    receipt,
                )
            except Exception:
                return False
        coordinator = self.operation_coordinator
        if coordinator is None:
            return False
        try:
            receipt = coordinator.record_handoff_event(
                binding.operation_id,
                binding.event_id,
                binding.target_plugin_id,
                handoff_key=binding.handoff_key,
            )
        except Exception:
            return False
        return bool(
            receipt is not None
            and receipt.handoff_key == binding.handoff_key
            and receipt.event_id == binding.event_id
        )

    def _operation_is_terminal(self, payload: dict) -> bool:
        coordinator = self.operation_coordinator
        operation_id = str((payload or {}).get("operation_id") or "")
        if coordinator is None or not operation_id:
            return False
        record = coordinator.get(operation_id)
        return bool(record is not None and record.state in TERMINAL_STATES)

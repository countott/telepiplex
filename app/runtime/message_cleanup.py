"""Durable removal of controls from retired telepiplex Telegram messages."""
from __future__ import annotations

import asyncio
import logging

from telegram.error import BadRequest

from .interaction_coordinator import TERMINAL_STATES

COORDINATOR_KEY = "telepiplex_interaction_coordinator"
MESSAGE_CLEANUP_WORKER_KEY = "telepiplex_message_cleanup_worker"
_LOGGER = logging.getLogger(__name__)


def _already_removed(exc: Exception, *, delete: bool) -> bool:
    if not isinstance(exc, BadRequest):
        return False
    text = str(exc).casefold()
    return ("message to delete not found" in text if delete else
            "message to edit not found" in text or "message is not modified" in text)


def _protected(coordinator, cleanup) -> bool:
    operation = coordinator.active(cleanup.chat_id, cleanup.user_id)
    if operation is None or operation.state in TERMINAL_STATES:
        return False
    segment = coordinator.get_active_segment(operation.operation_id)
    if segment is not None:
        return segment.message_id == cleanup.message_id and segment.state in {"open", "creating"}
    return operation.message_id == cleanup.message_id


async def deliver_message_cleanup(application, cleanup, *, lock_held=False) -> bool:
    """One bounded attempt. Failure or a still-active cursor remains pending."""
    from app.handlers.interaction_handler import operation_render_lock

    coordinator = application.bot_data.get(COORDINATOR_KEY)
    if coordinator is None:
        return False
    if not lock_held:
        async with operation_render_lock(application, cleanup.operation_id):
            return await deliver_message_cleanup(application, cleanup, lock_held=True)
    current = coordinator.get_message_cleanup(cleanup.operation_id, cleanup.message_id)
    if current is None or current.version != cleanup.version or current.state != "pending":
        return bool(current and current.state == "completed" and current.version == cleanup.version)
    if not coordinator.owns_message(current.operation_id, current.message_id):
        coordinator.fail_message_cleanup(current.operation_id, current.message_id,
            error="message_owner_conflict", version=current.version)
        return False
    if _protected(coordinator, current):
        coordinator.fail_message_cleanup(current.operation_id, current.message_id,
            error="active_message_protected", version=current.version)
        return False
    worker = application.bot_data.get(MESSAGE_CLEANUP_WORKER_KEY)
    timeout = float(getattr(worker, "io_timeout", 10.0))
    failures = []
    for delete in ([True, False] if current.delete else [False]):
        try:
            if delete:
                result = await asyncio.wait_for(application.bot.delete_message(
                    chat_id=current.chat_id, message_id=current.message_id), timeout)
            else:
                result = await asyncio.wait_for(application.bot.edit_message_reply_markup(
                    chat_id=current.chat_id, message_id=current.message_id, reply_markup=None), timeout)
            if result is None or result is False:
                raise RuntimeError("cleanup_not_acknowledged")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not _already_removed(exc, delete=delete):
                # Persist only bounded classifications; API error strings can contain secrets.
                failures.append(f"{'delete' if delete else 'markup'}:{type(exc).__name__}")
                continue
        return coordinator.ack_message_cleanup(current.operation_id, current.message_id,
                                               version=current.version)
    coordinator.fail_message_cleanup(current.operation_id, current.message_id,
        error=";".join(failures), version=current.version)
    return False


class MessageCleanupWorker:
    def __init__(self, application, coordinator, *, interval=1.0, io_timeout=10.0):
        self.application = application
        self.coordinator = coordinator
        self.interval = float(interval)
        self.io_timeout = float(io_timeout)
        self._wake = asyncio.Event()
        self._task = None

    def start(self):
        self.application.bot_data[MESSAGE_CLEANUP_WORKER_KEY] = self
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="telepiplex-message-cleanup")
        return self._task

    def wake(self):
        self._wake.set()

    async def run_once(self, *, now=None):
        completed = 0
        for cleanup in self.coordinator.pending_message_cleanups(now=now):
            completed += bool(await deliver_message_cleanup(self.application, cleanup))
        return completed

    async def run(self):
        while True:
            self._wake.clear()
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("telepiplex message cleanup worker failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), self.interval)
            except asyncio.TimeoutError:
                pass

    async def close(self, *, timeout=2.0):
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=max(0, float(timeout)))
        if task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass


def wake_message_cleanup(application):
    worker = application.bot_data.get(MESSAGE_CLEANUP_WORKER_KEY)
    if worker is not None:
        worker.wake()

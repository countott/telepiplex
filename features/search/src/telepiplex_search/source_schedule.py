"""Process-local scheduling for duplicate metadata provider requests."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import inspect
import time
import unicodedata
from typing import Any, Callable


def _normalized_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Cf"
    )
    return " ".join(text.split()).casefold()


def _positive_coordinate(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        coordinate = int(value)
    except (TypeError, ValueError):
        return None
    return coordinate if coordinate > 0 else None


@dataclass(frozen=True, slots=True)
class SourceRequestKey:
    provider: str
    purpose: str
    media_type: str
    identity: str
    scope: str
    season_number: int | None = None
    episode_number: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider",
            "purpose",
            "media_type",
            "identity",
            "scope",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalized_text(getattr(self, field_name)),
            )
        object.__setattr__(
            self,
            "season_number",
            _positive_coordinate(self.season_number),
        )
        object.__setattr__(
            self,
            "episode_number",
            _positive_coordinate(self.episode_number),
        )


class SourceScheduler:
    """Share safe in-flight provider reads and short-lived successes."""

    def __init__(
        self,
        *,
        success_ttl: float = 30.0,
        max_success_entries: int = 256,
        max_concurrency: int = 16,
        clock: Callable[[], float] = time.monotonic,
        observer=None,
    ) -> None:
        self._success_ttl = max(0.0, float(success_ttl))
        self._max_success_entries = max(0, int(max_success_entries))
        self._clock = clock
        self._observer = observer
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._flights: dict[SourceRequestKey, asyncio.Task] = {}
        self._successes: OrderedDict[
            SourceRequestKey,
            tuple[float, Any],
        ] = OrderedDict()

    async def _observe(self, outcome: str, key: SourceRequestKey, **facts):
        if not callable(self._observer):
            return
        payload = {
            "provider": key.provider,
            "purpose": key.purpose,
            "media_type": key.media_type,
            "scope": key.scope,
            "outcome": outcome,
            **facts,
        }
        try:
            observed = self._observer("search.source.request", payload)
            if inspect.isawaitable(observed):
                await observed
        except Exception:
            pass

    @property
    def success_entry_count(self) -> int:
        return len(self._successes)

    @property
    def in_flight_count(self) -> int:
        return len(self._flights)

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            key
            for key, (expires_at, _value) in self._successes.items()
            if expires_at <= now
        ]
        for key in expired:
            self._successes.pop(key, None)

    @staticmethod
    def _consume_completion(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            return

    async def _execute(
        self,
        key: SourceRequestKey,
        fetch,
        cacheable,
    ):
        current = asyncio.current_task()
        started_at = self._clock()
        try:
            was_queued = self._semaphore.locked()
            async with self._semaphore:
                wait_ms = max(
                    0,
                    round((self._clock() - started_at) * 1000),
                )
                if was_queued:
                    await self._observe(
                        "queue_waited",
                        key,
                        wait_ms=wait_ms,
                    )
                value = fetch()
                if inspect.isawaitable(value):
                    value = await value
            should_cache = (
                True if cacheable is None else bool(cacheable(value))
            )
            if (
                should_cache
                and self._success_ttl > 0
                and self._max_success_entries > 0
            ):
                cached = deepcopy(value)
                completed_at = self._clock()
                async with self._lock:
                    self._purge_expired_locked(completed_at)
                    self._successes.pop(key, None)
                    self._successes[key] = (
                        completed_at + self._success_ttl,
                        cached,
                    )
                    while (
                        len(self._successes)
                        > self._max_success_entries
                    ):
                        self._successes.popitem(last=False)
            await self._observe(
                "completed",
                key,
                duration_ms=max(
                    0,
                    round((self._clock() - started_at) * 1000),
                ),
                cacheable=should_cache,
            )
            return value
        except Exception as exc:
            await self._observe(
                "failed",
                key,
                duration_ms=max(
                    0,
                    round((self._clock() - started_at) * 1000),
                ),
                error_code=type(exc).__name__,
            )
            raise
        finally:
            async with self._lock:
                if self._flights.get(key) is current:
                    self._flights.pop(key, None)

    async def run(
        self,
        key: SourceRequestKey,
        fetch,
        *,
        cacheable=None,
    ):
        if not isinstance(key, SourceRequestKey):
            raise TypeError("key must be a SourceRequestKey")
        now = self._clock()
        cache_hit = False
        joined_flight = False
        async with self._lock:
            self._purge_expired_locked(now)
            cached = self._successes.get(key)
            if cached is not None:
                self._successes.move_to_end(key)
                cached_value = deepcopy(cached[1])
                cache_hit = True
            else:
                cached_value = None
            if cache_hit:
                flight = None
            else:
                flight = self._flights.get(key)
                joined_flight = flight is not None
                if flight is None:
                    flight = asyncio.create_task(
                        self._execute(key, fetch, cacheable)
                    )
                    flight.add_done_callback(self._consume_completion)
                    self._flights[key] = flight
        if cache_hit:
            await self._observe("cache_hit", key, cacheable=True)
            return cached_value
        if joined_flight:
            await self._observe("single_flight_join", key)
        value = await asyncio.shield(flight)
        return deepcopy(value)


__all__ = ["SourceRequestKey", "SourceScheduler"]

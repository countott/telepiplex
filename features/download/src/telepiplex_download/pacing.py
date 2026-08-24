from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime


_CLASS_TO_CONFIG = {
    "offline.poll": "offline_poll",
    "offline.mutation": "offline_mutation",
    "storage.read": "storage_read",
    "storage.mutation": "storage_mutation",
    "token.refresh": "token_refresh",
}
_DEFAULT_INTERVALS = {
    "offline.poll": 1.0,
    "offline.mutation": 1.0,
    "storage.read": 0.25,
    "storage.mutation": 1.0,
    "token.refresh": 1.0,
}
_SAFETY_FLOOR_CLASSES = {
    "offline.mutation",
    "storage.mutation",
    "token.refresh",
}


@dataclass(slots=True)
class _EndpointState:
    interval: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_start: float = 0.0
    cooldown_until: float = 0.0


class EndpointPacer:
    """Reserve provider request starts independently for each endpoint class."""

    def __init__(
        self,
        intervals: dict | None = None,
        *,
        clock=None,
        sleeper=None,
        wall_clock=None,
    ):
        configured = intervals if isinstance(intervals, dict) else {}
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._wall_clock = wall_clock or time.time
        self._states = {}
        for endpoint_class, config_key in _CLASS_TO_CONFIG.items():
            value = configured.get(config_key, _DEFAULT_INTERVALS[endpoint_class])
            try:
                interval = float(value)
            except (TypeError, ValueError):
                interval = _DEFAULT_INTERVALS[endpoint_class]
            if not math.isfinite(interval) or interval < 0:
                interval = _DEFAULT_INTERVALS[endpoint_class]
            if endpoint_class in _SAFETY_FLOOR_CLASSES:
                interval = max(interval, 1.0)
            self._states[endpoint_class] = _EndpointState(interval=interval)

    def _state(self, endpoint_class: str) -> _EndpointState:
        try:
            return self._states[str(endpoint_class)]
        except KeyError as exc:
            raise ValueError(f"unknown endpoint class: {endpoint_class}") from exc

    def acquire(self, endpoint_class: str) -> float:
        state = self._state(endpoint_class)
        started = self._clock()
        while True:
            with state.lock:
                now = self._clock()
                target = max(state.next_start, state.cooldown_until)
                delay = max(0.0, target - now)
                if delay <= 0:
                    state.next_start = now + state.interval
                    return max(0.0, self._clock() - started)
            self._sleep(delay)

    def observe_throttle(self, endpoint_class: str, retry_after) -> float:
        state = self._state(endpoint_class)
        delay = self._retry_after_seconds(retry_after)
        if delay <= 0:
            return 0.0
        with state.lock:
            state.cooldown_until = max(
                state.cooldown_until,
                self._clock() + delay,
            )
        return delay

    def _retry_after_seconds(self, value) -> float:
        if value is None or isinstance(value, bool):
            return 0.0
        text = str(value).strip()
        if not text:
            return 0.0
        try:
            delay = float(text)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
                if parsed is None:
                    return 0.0
                delay = parsed.timestamp() - float(self._wall_clock())
            except (TypeError, ValueError, OverflowError):
                return 0.0
        if not math.isfinite(delay) or delay <= 0:
            return 0.0
        return min(delay, 300.0)

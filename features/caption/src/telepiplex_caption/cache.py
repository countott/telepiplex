"""One small in-memory LRU budget for immutable decoder and quality results."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import is_dataclass
import sys
import time


def _retained_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if is_dataclass(value):
        size += _retained_size(vars(value), seen)
    elif isinstance(value, dict):
        size += sum(_retained_size(key, seen) + _retained_size(item, seen) for key, item in value.items())
    elif isinstance(value, (tuple, list)):
        size += sum(_retained_size(item, seen) for item in value)
    return size


class BoundedCPUCache:
    def __init__(self, *, max_bytes=32 * 1024 * 1024, max_entries=128, ttl_seconds=600, clock=time.monotonic):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.entries = OrderedDict()
        self.total_bytes = 0

    def __len__(self):
        return len(self.entries)

    def _expire(self):
        now = self.clock()
        for key in [key for key, (expires, _, _) in self.entries.items() if expires <= now]:
            self.total_bytes -= self.entries.pop(key)[2]

    def get(self, key):
        self._expire()
        entry = self.entries.get(key)
        if entry is None:
            return None
        self.entries.move_to_end(key)
        return entry[1]

    def put(self, key, value):
        self._expire()
        # Charge the key, bytes, Unicode strings and object dictionaries too.
        # Per-entry overhead conservatively covers the LRU node/value tuple.
        size = _retained_size((key, value)) + 256
        if size > self.max_bytes or self.max_entries < 1:
            return False
        previous = self.entries.pop(key, None)
        if previous is not None:
            self.total_bytes -= previous[2]
        while self.entries and (len(self.entries) >= self.max_entries or self.total_bytes + size > self.max_bytes):
            self.total_bytes -= self.entries.popitem(last=False)[1][2]
        self.entries[key] = (self.clock() + self.ttl_seconds, value, size)
        self.total_bytes += size
        return True

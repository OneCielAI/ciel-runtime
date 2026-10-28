"""Snapshot-isolated TTL cache with FIFO capacity and optimistic transactions."""

from dataclasses import dataclass
from threading import RLock


class ConflictError(RuntimeError):
    """A transaction touched a key modified since its creation."""


@dataclass(frozen=True)
class _Entry:
    value: object
    expires_at: object = None

    def visible(self, now):
        return self.expires_at is None or self.expires_at > now


class Snapshot:
    """A frozen view of bindings and TTL visibility at creation time."""

    def __init__(self, entries, versions, now):
        self._values = {k: e.value for k, e in entries.items() if e.visible(now)}
        self._versions = versions.copy()

    def get(self, key, default=None):
        return self._values.get(key, default)


class Cache:
    def __init__(self, clock, capacity=None):
        if capacity is not None and (not isinstance(capacity, int) or capacity <= 0):
            raise ValueError("capacity must be a positive integer or None")
        self._clock = clock
        self._capacity = capacity
        self._entries = {}
        self._versions = {}
        self._lock = RLock()

    def get(self, key, default=None):
        with self._lock:
            entry = self._entries.get(key)
            return entry.value if entry is not None and entry.visible(self._clock()) else default

    def set(self, key, value, ttl=None):
        with self._lock:
            self._apply([(key, False, value, ttl)], self._clock())

    def delete(self, key):
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            existed = entry is not None and entry.visible(now)
            self._apply([(key, True, None, None)], now)
            return existed

    def snapshot(self):
        with self._lock:
            return Snapshot(self._entries, self._versions, self._clock())

    def transaction(self):
        with self._lock:
            return Transaction(self, self.snapshot())

    def _apply(self, operations, now):
        # Stage everything, including TTL arithmetic, before publishing any change.
        entries = {k: e for k, e in self._entries.items() if e.visible(now)}
        versions = self._versions.copy()
        for key, deleted, value, ttl in operations:
            versions[key] = versions.get(key, 0) + 1
            if deleted:
                entries.pop(key, None)
            else:
                entry = _Entry(value, None if ttl is None else now + ttl)
                if entry.visible(now):
                    # Assignment to an existing dict key preserves insertion order.
                    entries[key] = entry
                else:
                    entries.pop(key, None)
        if self._capacity is not None:
            while len(entries) > self._capacity:
                key = next(iter(entries))
                del entries[key]
                versions[key] = versions.get(key, 0) + 1
        self._entries, self._versions = entries, versions


class Transaction:
    def __init__(self, cache, snapshot):
        self._cache = cache
        self._snapshot = snapshot
        self._touched = set()
        self._operations = []
        self._writes = {}
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise RuntimeError("transaction is closed")

    def get(self, key, default=None):
        self._check_open()
        self._touched.add(key)
        if key in self._writes:
            deleted, value, ttl = self._writes[key]
            return default if deleted or (ttl is not None and ttl <= 0) else value
        return self._snapshot.get(key, default)

    def set(self, key, value, ttl=None):
        self._check_open()
        self._touched.add(key)
        self._writes[key] = (False, value, ttl)
        self._operations.append((key, False, value, ttl))

    def delete(self, key):
        self._check_open()
        marker = object()
        existed = self.get(key, marker) is not marker
        self._writes[key] = (True, None, None)
        self._operations.append((key, True, None, None))
        return existed

    def commit(self):
        self._check_open()
        self._closed = True
        cache = self._cache
        with cache._lock:
            for key in self._touched:
                if cache._versions.get(key, 0) != self._snapshot._versions.get(key, 0):
                    raise ConflictError(f"key changed: {key!r}")
            cache._apply(self._operations, cache._clock())

    def rollback(self):
        self._check_open()
        self._closed = True
        self._operations.clear()
        self._writes.clear()

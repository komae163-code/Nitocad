"""
A small in-process cache for built vehicle packages, so a repeat download does not rebuild the whole thing.

The key is a hash of everything that changes the output (the vehicle spec, any overrides, whether outside parts are
fetched), never of who asked. Payment is checked by the caller before the cache is consulted, so a cached zip is
only ever handed to someone who paid for that version.

  * Entries expire after `ttl_s` and the least recently used is dropped past `max_items`.
  * A package bigger than `max_bytes` is built and served but not kept, so a few huge STEP exports cannot fill memory.
  * Two requests for the same key at once build it once: the second waits for the first (single flight).
  * Per process only. With several workers each keeps its own copy; that is correct, just less effective.
  * ttl_s <= 0 turns the cache off (every call builds).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

Built = tuple[bytes, dict[str, Any]]
DEFAULT_MAX_BYTES = 40_000_000


def make_key(vspec: dict[str, Any], fetch_external: bool, overrides: dict[str, Any] | None = None) -> str:
    """Hash of the vehicle spec (the plain spec stored in the part), the overrides and the fetch flag."""
    body = json.dumps({"spec": vspec, "overrides": overrides or {}, "fetch": bool(fetch_external)},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PackageCache:
    def __init__(self, ttl_s: float = 900, max_items: int = 8, clock: Callable[[], float] = time.monotonic,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.ttl_s = float(ttl_s)
        self.max_items = max(1, int(max_items))
        self.max_bytes = int(max_bytes)
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, Built]] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.hits = 0
        self.builds = 0

    def _fresh(self, key: str) -> Built | None:
        item = self._items.get(key)
        if item is None:
            return None
        if self._clock() - item[0] > self.ttl_s:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return item[1]

    def get_or_build(self, key: str, build: Callable[[], Built]) -> tuple[Built, bool]:
        """(built, from_cache). Only a non-empty zip is cached: a failed build is retried next time, and an exception
        from `build` passes through to the caller with nothing stored."""
        if self.ttl_s <= 0:
            self.builds += 1
            return build(), False
        with self._guard:
            hit = self._fresh(key)
            if hit is not None:
                self.hits += 1
                return hit, True
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            try:
                with self._guard:
                    hit = self._fresh(key)
                    if hit is not None:
                        self.hits += 1
                        return hit, True
                built = build()
                self.builds += 1
                data = built[0]
                with self._guard:
                    if data and len(data) <= self.max_bytes:
                        self._items[key] = (self._clock(), built)
                        self._items.move_to_end(key)
                        while len(self._items) > self.max_items:
                            self._items.popitem(last=False)
                return built, False
            finally:
                with self._guard:
                    if self._locks.get(key) is lock:
                        self._locks.pop(key, None)

    def clear(self) -> None:
        with self._guard:
            self._items.clear()


def get_package(cache: PackageCache, vspec: dict[str, Any], fetch_external: bool,
                build: Callable[[], tuple[bytes, str]]) -> tuple[bytes, str, bool]:
    """(zip bytes, file name, from_cache) for a vehicle. `build` returns (zip bytes, file name) and may raise; the
    caller has already checked that this version was paid for."""
    def run() -> Built:
        data, name = build()
        return data, {"name": name}
    (data, meta), hit = cache.get_or_build(make_key(vspec, fetch_external), run)
    return data, meta["name"], hit


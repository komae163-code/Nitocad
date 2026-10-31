"""package_cache: built vehicle packages are reused briefly, built once under concurrency, never cached on failure."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from package_cache import PackageCache, make_key


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_key_depends_on_spec_overrides_and_fetch_flag_only():
    spec = {"name": "x", "components": [1]}
    assert make_key(spec, False) == make_key(dict(spec), False)
    assert make_key(spec, False) != make_key(spec, True)
    assert make_key(spec, False) != make_key(spec, False, {"w": 3})
    assert make_key(spec, False) != make_key({"name": "x", "components": [2]}, False)
    assert make_key({"b": 1, "a": 2}, False) == make_key({"a": 2, "b": 1}, False)      # key order never matters


def test_second_request_is_served_from_the_cache_until_it_expires():
    clock, calls = Clock(), []
    c = PackageCache(ttl_s=100, clock=clock)

    def build():
        calls.append(1)
        return b"zip", {"ok": True}
    assert c.get_or_build("k", build) == ((b"zip", {"ok": True}), False)
    assert c.get_or_build("k", build)[1] is True and len(calls) == 1
    clock.t = 101
    assert c.get_or_build("k", build)[1] is False and len(calls) == 2


def test_failed_or_empty_builds_are_not_cached():
    c, calls = PackageCache(ttl_s=100), []

    def build():
        calls.append(1)
        return b"", {"ok": False, "errors": ["x"]}
    c.get_or_build("k", build)
    c.get_or_build("k", build)
    assert len(calls) == 2


def test_least_recently_used_entry_is_dropped():
    c = PackageCache(ttl_s=100, max_items=2)
    for k in ("a", "b"):
        c.get_or_build(k, lambda k=k: (k.encode(), {}))
    c.get_or_build("a", lambda: (b"new", {}))            # touch a
    c.get_or_build("c", lambda: (b"c", {}))              # evicts b
    assert c.get_or_build("a", lambda: (b"rebuilt", {}))[1] is True
    assert c.get_or_build("b", lambda: (b"rebuilt", {}))[1] is False


def test_zero_ttl_turns_the_cache_off():
    c, calls = PackageCache(ttl_s=0), []
    for _ in range(2):
        c.get_or_build("k", lambda: (calls.append(1) or b"z", {}))
    assert len(calls) == 2


def test_simultaneous_requests_build_once():
    c, calls, results = PackageCache(ttl_s=100), [], []

    def build():
        calls.append(1)
        time.sleep(0.15)
        return b"zip", {}
    ts = [threading.Thread(target=lambda: results.append(c.get_or_build("k", build))) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and len(results) == 4 and all(r[0][0] == b"zip" for r in results)


def test_a_package_over_the_size_cap_is_served_but_not_kept():
    c, calls = PackageCache(ttl_s=100, max_bytes=10), []

    def build():
        calls.append(1)
        return b"x" * 11, {}
    assert c.get_or_build("k", build)[1] is False
    assert c.get_or_build("k", build)[1] is False and len(calls) == 2


def test_an_exception_from_the_build_reaches_the_caller_and_nothing_is_cached():
    c = PackageCache(ttl_s=100)

    def boom():
        raise RuntimeError("kernel died")
    try:
        c.get_or_build("k", boom)
        assert False, "should have raised"
    except RuntimeError:
        pass
    assert c.get_or_build("k", lambda: (b"ok", {}))[1] is False      # the next request builds normally
    assert c.get_or_build("k", lambda: (b"again", {}))[1] is True
    assert not c._locks



def test_get_package_builds_once_per_vehicle_and_keeps_the_file_name():
    from package_cache import get_package
    c, calls = PackageCache(ttl_s=100), []

    def build():
        calls.append(1)
        return b"zipdata", "bike_package.zip"
    spec = {"name": "bike", "components": [1]}
    assert get_package(c, spec, True, build) == (b"zipdata", "bike_package.zip", False)
    assert get_package(c, spec, True, build) == (b"zipdata", "bike_package.zip", True) and len(calls) == 1
    assert get_package(c, {**spec, "components": [2]}, True, build)[2] is False        # another design builds again
    assert len(calls) == 2

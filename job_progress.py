"""
What a background build reports while it runs, and how it is asked to stop.

Code anywhere in a turn calls `step("Planning the parts")`. Inside a background job that appends a line to the job's
progress list (and raises JobCancelled / JobTimeout when the person pressed Cancel or the job ran too long, so a stuck
job ends at its next step instead of running forever). Outside a job it does nothing, so the normal one-request path is
unchanged and nothing needs to know which mode it is in.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable


class JobCancelled(Exception):
    """The person pressed Cancel."""


class JobTimeout(Exception):
    """The job ran past its maximum run time."""


@dataclass
class Tracker:
    add_step: Callable[[str, str | None], None]
    is_cancelled: Callable[[], bool]
    max_runtime_s: float
    plan_budget_s: float | None = None          # the planning time limit for this job, replacing the one-request limit
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        if self.is_cancelled():
            raise JobCancelled()
        if time.monotonic() - self.started > self.max_runtime_s:
            raise JobTimeout()


_ctx: ContextVar[Tracker | None] = ContextVar("job_tracker", default=None)


@contextmanager
def bind(tracker: Tracker):
    token = _ctx.set(tracker)
    try:
        yield tracker
    finally:
        _ctx.reset(token)


def active() -> bool:
    return _ctx.get() is not None


def check() -> None:
    t = _ctx.get()
    if t is not None:
        t.check()


def step(label: str, detail: str | None = None) -> None:
    """Record progress. Never raises for a storage problem: a progress line is not worth failing a build."""
    t = _ctx.get()
    if t is None:
        return
    t.check()
    try:
        t.add_step(str(label)[:120], (str(detail)[:200] if detail else None))
    except Exception:  # noqa: BLE001
        pass


def budget_override() -> float | None:
    """The planning time limit of the running background job, or None outside one."""
    t = _ctx.get()
    return t.plan_budget_s if t is not None else None

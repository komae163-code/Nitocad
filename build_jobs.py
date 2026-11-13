"""
Background builds: press Build, leave, come back and it is done.

A turn that would outlast one web request (a big vehicle plan, retries, the finished-model inspection) runs as a job:
the server stores it, runs it on a worker thread, records every step it reaches, and keeps the result until the person
is back. The browser only asks "how is it going?". Nothing here lives in the browser.

  create(...)            one job per chat at a time, a few per person
  Runner                 worker threads (a few at once; the rest wait their turn)
  execute(...)           runs the work with progress and cancel hooked in, then stores the result or the error
  requeue_stale()        after a restart: jobs that were running are run again (the user message is not saved twice),
                         at most MAX_ATTEMPTS times, then marked failed so nobody waits for ever

Stdlib only, like chat_store: web_app.py hands it db.get_conn through configure().
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import job_progress as JP

MAX_STEPS = 80                 # kept per job; the oldest are dropped
MAX_ATTEMPTS = 2               # runs of one job, counting a re-run after a restart
HEARTBEAT_S = 15               # a running job touches its row this often
STALE_S = 75                   # a running job silent for this long belongs to a process that died
KEEP_DAYS = 7                  # finished jobs are deleted after this
ACTIVE = ("queued", "running")
DONE = ("done", "failed", "cancelled")

_get_conn: Callable[[], Any] | None = None


class JobActive(Exception):
    """This chat already has a job queued or running."""
    def __init__(self, job: dict):
        super().__init__("This chat is still working on your last message.")
        self.job = job


class JobLimit(Exception):
    """The person has too many jobs queued or running."""


def configure(get_conn: Callable[[], Any]) -> None:
    global _get_conn
    _get_conn = get_conn


def _conn():
    if _get_conn is None:
        raise RuntimeError("build_jobs.configure(get_conn) was never called")
    return _get_conn()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_tables() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS build_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL, text TEXT NOT NULL, model_id TEXT,
                status TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]', step_count INTEGER NOT NULL DEFAULT 0,
                result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_build_jobs_chat ON build_jobs (chat_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_build_jobs_user ON build_jobs (user_id, status)")


def _row(r) -> dict:
    d = dict(r)
    d["steps"] = json.loads(d.get("steps") or "[]")
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    d["cancel"] = bool(d.get("cancel"))
    return d


def create(user_id: str, chat_id: str, text: str, model_id: str | None, *, max_active_per_user: int = 3) -> dict:
    with _conn() as conn:
        busy = conn.execute("SELECT * FROM build_jobs WHERE chat_id = ? AND user_id = ? AND status IN ('queued','running')",
                            (chat_id, user_id)).fetchone()
        if busy is not None:
            raise JobActive(public(_row(busy)))
        n = conn.execute("SELECT COUNT(*) FROM build_jobs WHERE user_id = ? AND status IN ('queued','running')", (user_id,)).fetchone()[0]
        if n >= max_active_per_user:
            raise JobLimit(f"You already have {n} builds running. Wait for one to finish, or cancel one.")
        jid, now = secrets.token_urlsafe(9), _now()
        conn.execute("INSERT INTO build_jobs (id, user_id, chat_id, text, model_id, status, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,'queued',?,?)", (jid, user_id, chat_id, text, model_id, now, now))
    return get(user_id, jid)


def get(user_id: str, job_id: str) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM build_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
    return _row(r) if r else None


def _get_any(job_id: str) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM build_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row(r) if r else None


def active_for_chat(user_id: str, chat_id: str) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM build_jobs WHERE chat_id = ? AND user_id = ? AND status IN ('queued','running') "
                         "ORDER BY created_at DESC LIMIT 1", (chat_id, user_id)).fetchone()
    return _row(r) if r else None


def public(job: dict, *, with_result: bool = False) -> dict:
    """What the browser sees. The text and the owner stay on the server."""
    steps = job.get("steps") or []
    try:
        started = datetime.fromisoformat(job["created_at"])
        end = datetime.fromisoformat(job["finished_at"]) if job.get("finished_at") else datetime.now(timezone.utc)
        elapsed = max(0, int((end - started).total_seconds()))
    except Exception:  # noqa: BLE001
        elapsed = 0
    out = {"id": job["id"], "chat_id": job["chat_id"], "status": job["status"], "steps": steps[-40:],
           "step_count": job.get("step_count", len(steps)), "error": job.get("error"), "elapsed_s": elapsed,
           "cancel_requested": bool(job.get("cancel")), "created_at": job["created_at"], "updated_at": job["updated_at"]}
    if with_result:
        out["result"] = job.get("result")
    return out


def add_step(job_id: str, label: str, detail: str | None = None) -> None:
    with _conn() as conn:
        r = conn.execute("SELECT steps, step_count FROM build_jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None:
            return
        steps = json.loads(r["steps"] or "[]")
        if steps and steps[-1].get("label") == label and steps[-1].get("detail") == detail:
            return                                                    # the same line twice is noise
        n = r["step_count"] + 1
        steps.append({"n": n, "label": label, "detail": detail, "t": _now()})
        conn.execute("UPDATE build_jobs SET steps = ?, step_count = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(steps[-MAX_STEPS:], separators=(",", ":")), n, _now(), job_id))


def _set(job_id: str, **cols: Any) -> None:
    cols["updated_at"] = _now()
    keys = ", ".join(f"{k} = ?" for k in cols)
    with _conn() as conn:
        conn.execute(f"UPDATE build_jobs SET {keys} WHERE id = ?", (*cols.values(), job_id))


def request_cancel(user_id: str, job_id: str) -> dict | None:
    job = get(user_id, job_id)
    if job is None:
        return None
    if job["status"] == "queued":
        _set(job_id, cancel=1, status="cancelled", finished_at=_now(), error="Cancelled before it started.")
    elif job["status"] == "running":
        _set(job_id, cancel=1)                                         # the job stops at its next step
    return get(user_id, job_id)


def _is_cancelled(job_id: str) -> bool:
    with _conn() as conn:
        r = conn.execute("SELECT cancel FROM build_jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(r and r["cancel"])


def touch(job_id: str) -> None:
    _set(job_id)


def requeue_stale(*, older_than_s: float = STALE_S, skip_ids: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """Jobs a dead process left behind, to be run again here. A running job is stale when its heartbeat stopped
    `older_than_s` ago; a queued job that long unclaimed (and not waiting in this process, `skip_ids`) is picked up too.
    A job that has already had MAX_ATTEMPTS runs is marked failed instead. Safe with several processes: whoever runs
    it first claims it (see execute), so a job never runs twice at once. Returns the jobs to resubmit."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_s)).isoformat()
    again: list[dict] = []
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM build_jobs WHERE status IN ('queued','running') AND updated_at < ?", (cutoff,)).fetchall()
    for r in rows:
        job = _row(r)
        if job["id"] in skip_ids:
            continue
        if job["cancel"]:
            _set(job["id"], status="cancelled", finished_at=_now(), error="Cancelled.")
        elif job["attempts"] >= MAX_ATTEMPTS:
            _set(job["id"], status="failed", finished_at=_now(),
                 error="The server restarted while this was building, twice. Nothing was lost, send the message again.")
        else:
            if job["status"] == "running":
                _set(job["id"], status="queued")
            again.append(job)
    return again


def purge_old(days: int = KEEP_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as conn:
        return conn.execute("DELETE FROM build_jobs WHERE status IN ('done','failed','cancelled') AND finished_at < ?", (cutoff,)).rowcount


def resume_state(tail: list[dict], text: str) -> tuple[str, dict | None, dict | None]:
    """What a job run again after a restart should do, from the last (up to two) saved messages of its chat:
      ("done",  user_msg, assistant_msg)  the turn had already saved its reply: return it, run nothing again
      ("reuse", user_msg, None)           the message is saved but there is no reply: keep it, do not add it twice
      ("new",   None, None)               nothing saved yet: add the message as a normal turn does"""
    if len(tail) == 2 and tail[0]["role"] == "user" and tail[0]["content"] == text and tail[1]["role"] == "assistant":
        return "done", tail[0], tail[1]
    if tail and tail[-1]["role"] == "user" and tail[-1]["content"] == text:
        return "reuse", tail[-1], None
    return "new", None, None


# ------------------------------------------------------------------------------ running
def execute(job_id: str, work: Callable[[int], dict], *, plan_budget_s: float | None, max_runtime_s: float) -> None:
    """Run `work(attempt)` as this job: progress on, cancel on, the result or the error stored. Never raises."""
    with _conn() as conn:                                    # claim it: only one process runs a job
        claimed = conn.execute("UPDATE build_jobs SET status = 'running', attempts = attempts + 1, updated_at = ? "
                               "WHERE id = ? AND status = 'queued'", (_now(), job_id)).rowcount
    if not claimed:
        return
    job = _get_any(job_id)
    attempt = job["attempts"] if job else 1
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_S):
            try:
                touch(job_id)
            except Exception:  # noqa: BLE001 - a missed heartbeat is not worth failing a build
                pass
    threading.Thread(target=beat, name=f"build-beat-{job_id}", daemon=True).start()
    tracker = JP.Tracker(add_step=lambda label, detail: add_step(job_id, label, detail), is_cancelled=lambda: _is_cancelled(job_id),
                         max_runtime_s=max_runtime_s, plan_budget_s=plan_budget_s)
    try:
        with JP.bind(tracker):
            tracker.check()
            result = work(attempt)
        _set(job_id, status="done", result=json.dumps(result, separators=(",", ":"), default=str), finished_at=_now())
    except JP.JobCancelled:
        _set(job_id, status="cancelled", finished_at=_now(), error="Cancelled. Nothing more was built.")
    except JP.JobTimeout:
        _set(job_id, status="failed", finished_at=_now(),
             error=f"This took longer than {int(max_runtime_s // 60)} minutes, so it was stopped. Try a simpler version.")
    except Exception as exc:  # noqa: BLE001 - a crashed worker must end as a clear failure, not a job that never finishes
        import logging
        logging.getLogger(__name__).exception("background build failed")
        _set(job_id, status="failed", finished_at=_now(), error=(str(exc)[:240] or "The build failed. Nothing was charged for it."))
    finally:
        stop.set()


class Runner:
    """Worker threads, `max_workers` running at once. A job waiting for a free worker stays "queued"."""

    def __init__(self, max_workers: int = 2):
        self._slots = threading.BoundedSemaphore(max(1, max_workers))
        self._done: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, job_id: str, run: Callable[[], None]) -> None:
        ev = threading.Event()
        with self._lock:
            self._done[job_id] = ev

        def target():
            try:
                with self._slots:
                    run()
            finally:
                ev.set()
                with self._lock:
                    self._done.pop(job_id, None)
        threading.Thread(target=target, name=f"build-job-{job_id}", daemon=True).start()

    def live_ids(self) -> set[str]:
        with self._lock:
            return set(self._done)

    def wait(self, job_id: str, seconds: float) -> bool:
        """True if the job finished within `seconds`."""
        with self._lock:
            ev = self._done.get(job_id)
        return True if ev is None else ev.wait(seconds)

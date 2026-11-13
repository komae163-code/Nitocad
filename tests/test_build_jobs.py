"""Background builds: saved jobs, progress, cancel, restart, and the longer planning budget."""
import contextlib
import sqlite3
import threading
import time

import pytest

import build_jobs as BJ
import job_progress as JP


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "jobs.db")

    @contextlib.contextmanager
    def get_conn():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    BJ.configure(get_conn)
    BJ.init_tables()
    return get_conn


def run(job_id, work, **kw):
    BJ.execute(job_id, work, plan_budget_s=kw.get("plan_budget_s"), max_runtime_s=kw.get("max_runtime_s", 60))


def test_a_job_runs_records_its_steps_and_keeps_the_result(db):
    job = BJ.create("u1", "c1", "a car", "plus:gpt")
    assert job["status"] == "queued"

    def work(attempt):
        JP.step("Planning the design")
        JP.step("Building the model", "23 parts")
        return {"assistant_message": {"content": "built"}, "balance": 7}
    run(job["id"], work)
    done = BJ.get("u1", job["id"])
    assert done["status"] == "done" and done["attempts"] == 1
    assert [s["label"] for s in done["steps"]] == ["Planning the design", "Building the model"]
    assert done["steps"][1]["detail"] == "23 parts" and done["result"]["assistant_message"]["content"] == "built"
    pub = BJ.public(done, with_result=True)
    assert "text" not in pub and "user_id" not in pub and pub["result"]["balance"] == 7 and pub["step_count"] == 2


def test_progress_is_a_no_op_outside_a_job():
    JP.step("anything")                                    # nothing bound: no error, nothing recorded
    assert not JP.active() and JP.budget_override() is None


def test_a_job_has_its_own_planning_budget_and_only_while_it_runs(db):
    job = BJ.create("u1", "c1", "x", None)
    seen = {}
    run(job["id"], lambda a: seen.update(budget=JP.budget_override(), active=JP.active()) or {}, plan_budget_s=900)
    assert seen == {"budget": 900, "active": True} and JP.budget_override() is None


def test_one_job_at_a_time_per_chat_and_a_cap_per_person(db):
    a = BJ.create("u1", "c1", "x", None)
    with pytest.raises(BJ.JobActive) as e:
        BJ.create("u1", "c1", "y", None)
    assert e.value.job["id"] == a["id"]
    BJ.create("u1", "c2", "x", None); BJ.create("u1", "c3", "x", None)
    with pytest.raises(BJ.JobLimit):
        BJ.create("u1", "c4", "x", None)
    BJ.create("u2", "c1", "x", None)                        # another person, same chat id: separate
    run(a["id"], lambda attempt: {})
    BJ.create("u1", "c1", "again", None)                    # a finished job no longer blocks the chat


def test_jobs_belong_to_their_owner(db):
    job = BJ.create("u1", "c1", "x", None)
    assert BJ.get("u2", job["id"]) is None and BJ.request_cancel("u2", job["id"]) is None
    assert BJ.active_for_chat("u2", "c1") is None and BJ.active_for_chat("u1", "c1")["id"] == job["id"]


def test_cancel_stops_a_running_job_at_its_next_step(db):
    job = BJ.create("u1", "c1", "x", None)
    reached = []

    def work(attempt):
        JP.step("one"); reached.append(1)
        BJ.request_cancel("u1", job["id"])                  # the person presses Cancel mid-build
        JP.step("two"); reached.append(2)
        return {}
    run(job["id"], work)
    done = BJ.get("u1", job["id"])
    assert done["status"] == "cancelled" and reached == [1] and "Cancelled" in done["error"]


def test_a_queued_job_cancels_at_once(db):
    job = BJ.create("u1", "c1", "x", None)
    assert BJ.request_cancel("u1", job["id"])["status"] == "cancelled"
    ran = []
    run(job["id"], lambda attempt: ran.append(1) or {})     # a worker that picks it up later skips it
    assert ran == []


def test_a_job_that_runs_too_long_is_stopped_with_a_clear_message(db):
    job = BJ.create("u1", "c1", "x", None)

    def work(attempt):
        time.sleep(0.05)
        JP.step("late")
        return {}
    run(job["id"], work, max_runtime_s=0.01)
    done = BJ.get("u1", job["id"])
    assert done["status"] == "failed" and "longer than" in done["error"]


def test_a_crashing_job_fails_cleanly_and_a_progress_problem_never_fails_a_build(db):
    job = BJ.create("u1", "c1", "x", None)
    run(job["id"], lambda attempt: (_ for _ in ()).throw(RuntimeError("the kernel fell over")))
    done = BJ.get("u1", job["id"])
    assert done["status"] == "failed" and "kernel fell over" in done["error"]
    ok = BJ.create("u1", "c2", "x", None)
    t = JP.Tracker(add_step=lambda *a: (_ for _ in ()).throw(OSError("disk")), is_cancelled=lambda: False, max_runtime_s=60)
    with JP.bind(t):
        JP.step("this must not raise")
    run(ok["id"], lambda attempt: {"fine": True})
    assert BJ.get("u1", ok["id"])["status"] == "done"


def _age(job_id, seconds):
    from datetime import datetime, timedelta, timezone
    BJ._set(job_id, status="running")
    with BJ._conn() as conn:
        conn.execute("UPDATE build_jobs SET updated_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(), job_id))


def test_a_job_left_by_a_dead_process_is_run_again_but_one_still_beating_is_left_alone(db):
    job = BJ.create("u1", "c1", "x", None)
    BJ._set(job["id"], status="running", attempts=1)                  # another process is running it: fresh heartbeat
    assert BJ.requeue_stale() == []
    _age(job["id"], BJ.STALE_S + 30)                                  # its heartbeat stopped: that process died
    again = BJ.requeue_stale()
    assert [j["id"] for j in again] == [job["id"]] and BJ.get("u1", job["id"])["status"] == "queued"
    attempts = []
    run(job["id"], lambda attempt: attempts.append(attempt) or {})
    assert attempts == [2] and BJ.get("u1", job["id"])["status"] == "done"      # attempt 2 tells run_turn to resume


def test_a_job_that_keeps_dying_fails_after_two_runs(db):
    dead = BJ.create("u1", "c2", "x", None)
    BJ._set(dead["id"], attempts=BJ.MAX_ATTEMPTS)
    _age(dead["id"], BJ.STALE_S + 30)
    assert BJ.requeue_stale() == [] and BJ.get("u1", dead["id"])["status"] == "failed"


def test_a_job_waiting_in_this_process_is_not_taken_over_and_a_job_never_runs_twice(db):
    waiting = BJ.create("u1", "c3", "x", None)
    BJ._set(waiting["id"], status="queued")
    with BJ._conn() as conn:
        conn.execute("UPDATE build_jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (waiting["id"],))
    assert BJ.requeue_stale(skip_ids={waiting["id"]}) == []           # it is queued behind a busy worker here
    ran = []
    run(waiting["id"], lambda attempt: ran.append(attempt) or {})
    run(waiting["id"], lambda attempt: ran.append("second") or {})    # a second process picking it up does nothing
    assert ran == [1]


def test_a_running_job_keeps_its_heartbeat(db, monkeypatch):
    monkeypatch.setattr(BJ, "HEARTBEAT_S", 0.02)
    job = BJ.create("u1", "c4", "x", None)
    before = job["updated_at"]
    run(job["id"], lambda attempt: time.sleep(0.15) or {})
    assert BJ.get("u1", job["id"])["updated_at"] > before


def test_progress_lines_are_deduplicated_and_capped(db):
    job = BJ.create("u1", "c1", "x", None)
    for _ in range(3):
        BJ.add_step(job["id"], "same line")
    assert BJ.get("u1", job["id"])["step_count"] == 1
    for i in range(BJ.MAX_STEPS + 20):
        BJ.add_step(job["id"], f"step {i}")
    got = BJ.get("u1", job["id"])
    assert len(got["steps"]) == BJ.MAX_STEPS and got["step_count"] == BJ.MAX_STEPS + 21 and got["steps"][-1]["label"] == f"step {BJ.MAX_STEPS + 19}"
    assert len(BJ.public(got)["steps"]) == 40


def test_old_finished_jobs_are_purged_but_running_ones_never(db):
    old, live = BJ.create("u1", "c1", "x", None), BJ.create("u1", "c2", "x", None)
    run(old["id"], lambda attempt: {})
    BJ._set(old["id"], finished_at="2000-01-01T00:00:00+00:00")
    BJ._set(live["id"], status="running")
    assert BJ.purge_old() == 1 and BJ.get("u1", old["id"]) is None and BJ.get("u1", live["id"]) is not None


def test_the_runner_runs_a_job_off_the_calling_thread_and_wait_reports_whether_it_finished(db):
    r = BJ.Runner(max_workers=1)
    gate = threading.Event()
    r.submit("slow", lambda: gate.wait(2))
    assert r.wait("slow", 0.05) is False                    # still running: the caller gets a 202 and the job id
    gate.set()
    assert r.wait("slow", 2) is True
    assert r.wait("never-started", 0.01) is True            # nothing to wait for


def test_one_worker_runs_one_job_at_a_time(db):
    r = BJ.Runner(max_workers=1)
    order, gate = [], threading.Event()
    r.submit("a", lambda: (order.append("a-start"), gate.wait(2), order.append("a-end")))
    time.sleep(0.05)
    r.submit("b", lambda: order.append("b"))
    time.sleep(0.05)
    assert order == ["a-start"]                              # b waits for a worker
    gate.set()
    assert r.wait("b", 2) and order == ["a-start", "a-end", "b"]


def test_the_planner_reports_each_attempt_and_the_vehicle_budget_follows_the_job(db):
    import vehicle_chat as VC
    import vehicle_planner as VP
    assert VC._plan_budget() == float(VP.DEFAULT_TIME_BUDGET_S) or VC._plan_budget() > 0
    job = BJ.create("u1", "c1", "x", None)
    seen = {}
    run(job["id"], lambda attempt: seen.update(b=VC._plan_budget()) or {}, plan_budget_s=777)
    assert seen["b"] == 777.0 and VC._plan_budget() != 777.0


def test_the_inspection_reports_its_steps_to_the_job(db):
    import vehicle_inspect as VI
    lines = []
    box = lambda n, lo, hi: {"name": n, "part_type": "box", "parameters": {"_b": (*lo, *hi)}, "position": [0, 0, 0], "rotation": [0, 0, 0]}
    parts = [box("body", (0, -900, 300), (4000, 900, 1100)), box("wing", (3900, -800, 1500), (4300, 800, 1540))]
    VI.inspect_vehicle({"name": "car"}, expand=lambda v: (parts, []), bbox_fn=lambda p: p["parameters"]["_b"], kind="car",
                       on_step=lambda label, detail=None: lines.append(label))
    assert lines[0] == "Inspecting the finished model"


def test_resume_never_saves_the_message_twice_or_runs_a_finished_turn_again():
    u = {"role": "user", "content": "a car", "id": "m1"}
    a = {"role": "assistant", "content": "done", "id": "m2"}
    assert BJ.resume_state([u, a], "a car") == ("done", u, a)          # the reply was saved before the crash
    assert BJ.resume_state([a, u], "a car") == ("reuse", u, None)      # the message was saved, the reply was not
    assert BJ.resume_state([u], "a car") == ("reuse", u, None)
    assert BJ.resume_state([a], "a car") == ("new", None, None)        # nothing saved yet
    assert BJ.resume_state([u, a], "a different message") == ("new", None, None)
    assert BJ.resume_state([], "a car") == ("new", None, None)

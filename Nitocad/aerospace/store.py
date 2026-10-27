"""Append-only persistence for aerospace designs and evidence (sqlite via db.get_conn).

Evidence rows are never updated or deleted: a new assessment adds new rows
bound to its revision, and older rows simply become stale in reads.
"""
import json
from datetime import datetime, timezone

import db

_DDL = (
    """CREATE TABLE IF NOT EXISTS aero_designs (
         design_id TEXT NOT NULL, owner TEXT NOT NULL, part_type TEXT NOT NULL,
         spec_json TEXT NOT NULL, revision_id TEXT NOT NULL, status TEXT NOT NULL,
         updated_at TEXT NOT NULL, PRIMARY KEY (design_id, owner))""",
    """CREATE TABLE IF NOT EXISTS aero_evidence (
         id TEXT PRIMARY KEY, design_id TEXT NOT NULL, owner TEXT NOT NULL,
         revision_id TEXT NOT NULL, record_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_aero_evidence_design ON aero_evidence(owner, design_id)",
    """CREATE TABLE IF NOT EXISTS aero_approvals (
         id INTEGER PRIMARY KEY AUTOINCREMENT, design_id TEXT NOT NULL, owner TEXT NOT NULL,
         prev_hash TEXT, record_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    # one successor per record: a forked chain is rejected by the database itself
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_aero_approvals_chain ON aero_approvals(owner, design_id, COALESCE(prev_hash, 'genesis'))",
)


def init() -> None:
    with db.get_conn() as c:
        for stmt in _DDL:
            c.execute(stmt)


def save_assessment(owner: str, spec: dict, assessment: dict) -> None:
    init()
    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as c:
        c.execute(
            """INSERT INTO aero_designs (design_id, owner, part_type, spec_json, revision_id, status, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(design_id, owner) DO UPDATE SET part_type=excluded.part_type,
                 spec_json=excluded.spec_json, revision_id=excluded.revision_id,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (assessment["design_id"], owner, assessment["part_type"], json.dumps(spec),
             assessment["revision_id"], assessment["status"], now))
        for rec in assessment["evidence_records"]:
            c.execute("INSERT OR IGNORE INTO aero_evidence (id, design_id, owner, revision_id, record_json, created_at) VALUES (?,?,?,?,?,?)",
                      (rec["id"], rec["design_id"], owner, rec["revision_id"], json.dumps(rec), rec["created_at"]))


def load_design(owner: str, design_id: str):
    init()
    with db.get_conn() as c:
        row = c.execute("SELECT * FROM aero_designs WHERE owner=? AND design_id=?", (owner, design_id)).fetchone()
        if not row:
            return None
        evid = [json.loads(r["record_json"]) for r in c.execute(
            "SELECT record_json FROM aero_evidence WHERE owner=? AND design_id=? ORDER BY created_at DESC LIMIT 200",
            (owner, design_id))]
    return {"design_id": design_id, "part_type": row["part_type"], "revision_id": row["revision_id"],
            "status": row["status"], "updated_at": row["updated_at"],
            "spec": json.loads(row["spec_json"]), "evidence": evid}


def list_approvals(owner: str, design_id: str) -> list:
    init()
    with db.get_conn() as c:
        return [json.loads(r["record_json"]) for r in c.execute(
            "SELECT record_json FROM aero_approvals WHERE owner=? AND design_id=? ORDER BY id", (owner, design_id))]


def append_approval(owner: str, design_id: str, build) -> dict:
    """build(records) -> new record. Reads the chain and inserts in one transaction;
    the unique index turns a concurrent fork into ChainConflict instead of corrupting history."""
    init()
    try:
        with db.get_conn() as c:
            records = [json.loads(r["record_json"]) for r in c.execute(
                "SELECT record_json FROM aero_approvals WHERE owner=? AND design_id=? ORDER BY id", (owner, design_id))]
            rec = build(records)
            c.execute("INSERT INTO aero_approvals (design_id, owner, prev_hash, record_json, created_at) VALUES (?,?,?,?,?)",
                      (design_id, owner, rec.get("prev_hash"), json.dumps(rec), rec["created_at"]))
        return rec
    except Exception as e:        # sqlite3.IntegrityError / psycopg unique violation
        if "unique" in str(e).lower() or "integrity" in type(e).__name__.lower():
            raise ChainConflict("another approval was recorded at the same moment; reload and try again")
        raise


class ChainConflict(Exception):
    pass

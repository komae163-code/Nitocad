"""
A library of vehicle specs that gets better with use, without training a model.

How it "learns" (and what it does not do)
  * Proven specs are stored and retrieved as worked examples for similar requests (few-shot retrieval).
  * A spec starts as a CANDIDATE. Only user feedback promotes it to ACCEPTED (used as an example) or demotes it to
    REJECTED (never used). Shipped examples are SEED. Nothing is promoted automatically.
  * Every failed planning attempt is counted by error pattern, and the most frequent patterns become warnings in
    the next prompt ("lessons").
  * The model itself is not trained or changed. Quality improves only through better examples and warnings.

Storage is pluggable (Store). FileStore writes JSON files: fine on a server with a disk. On Vercel or any
serverless host the disk is wiped between runs, so use a real database behind the Store interface there.
Every spec is re-validated with vehicle_spec.expand_vehicle before it is stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

import vehicle_spec as VS

MAX_VALUE_BYTES = 1_000_000
MAX_CANDIDATES = 300            # unreviewed AI-planned specs kept; the oldest are dropped first
STATUSES = ("seed", "candidate", "accepted", "rejected")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,100}(/[a-z0-9][a-z0-9_.-]{0,100})*$")
_SYNONYMS = {"bike": "bicycle", "cycle": "bicycle", "barrow": "wheelbarrow", "heli": "helicopter", "chopper": "helicopter",
             "copter": "helicopter", "rc": "radio", "trike": "tricycle", "loco": "locomotive", "train": "locomotive",
             "wheelchair": "wheelchair", "chair": "wheelchair", "drone": "multirotor", "quadcopter": "multirotor"}


class Store(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, text: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str = "") -> list[str]: ...


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.match(key) or ".." in key:
        raise ValueError(f"bad storage key {key!r}")
    return key


class MemoryStore:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._d.get(_check_key(key))

    def put(self, key: str, text: str) -> None:
        if len(text.encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("value too large")
        self._d[_check_key(key)] = text

    def delete(self, key: str) -> None:
        self._d.pop(_check_key(key), None)

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._d if k.startswith(prefix))


class FileStore:
    """One JSON file per key under `root`. Writes are atomic (temp file + replace)."""
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / (_check_key(key) + ".json")).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("key escapes the store")
        return p

    def get(self, key: str) -> str | None:
        p = self._path(key)
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def put(self, key: str, text: str) -> None:
        if len(text.encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("value too large")
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()

    def keys(self, prefix: str = "") -> list[str]:
        out = []
        for f in self.root.rglob("*.json"):
            k = str(f.relative_to(self.root).with_suffix("")).replace(os.sep, "/")
            if k.startswith(prefix):
                out.append(k)
        return sorted(out)


def tokens(text: str) -> list[str]:
    raw = re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text)).lower())
    return [_SYNONYMS.get(t, t) for t in raw if len(t) > 1]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:48]
    return s or "vehicle"


def error_pattern(msg: str) -> str:
    """Collapse an error message so the same mistake with different names or numbers counts as one."""
    m = re.sub(r"'[^']*'", "'X'", msg)
    m = re.sub(r"\"[^\"]*\"", "'X'", m)
    m = re.sub(r"-?\d+(\.\d+)?", "N", m)
    return m[:200]


class SpecLibrary:
    def __init__(self, store: Store, module_dir: Path | None = None, *, review_required: bool = False) -> None:
        self.store = store
        self.module_dir = module_dir
        # When True, a thumbs-up (or pressing Generate) does not promote a candidate by itself. It only records the vote
        # and puts the spec in the review queue; approve() (an operator decision) is what makes it an example that
        # other people's prompts can see.
        self.review_required = review_required

    # ------------------------------------------------------------------ writing
    def add(self, spec: Any, description: str = "", *, status: str = "candidate", source: str = "planner",
            tags: list[str] | None = None) -> dict[str, Any]:
        if status not in STATUSES:
            return {"ok": False, "errors": [f"status must be one of {STATUSES}"]}
        ex = VS.expand_vehicle(spec, None, None, self.module_dir)
        if not ex["ok"]:
            return {"ok": False, "errors": ex["errors"][:20]}
        body = json.dumps(spec, sort_keys=True)
        rid = f"{slug(spec['name'])}_{hashlib.sha256(body.encode()).hexdigest()[:8]}"
        key = f"specs/{rid}"
        existing = self.store.get(key)
        rec = json.loads(existing) if existing else {"accepted": 0, "rejected": 0, "added": time.time()}
        rec.update({"id": rid, "name": spec["name"], "description": str(description)[:2000], "status": rec.get("status", status)
                    if existing else status, "source": source, "tags": list(tags or rec.get("tags", []))[:20], "spec": spec,
                    "parts": len(ex["parts"]) + len(ex["external"])})
        self.store.put(key, json.dumps(rec))
        if rec["status"] == "candidate":
            self._prune_candidates()
        return {"ok": True, "id": rid, "status": rec["status"]}

    def _prune_candidates(self, keep: int | None = None) -> int:
        keep = MAX_CANDIDATES if keep is None else keep
        cands = sorted(self.all(("candidate",)), key=lambda r: r.get("added", 0))
        dropped = 0
        # A design waiting in the review queue is never dropped to make room: someone liked it and an operator has not
        # looked at it yet. Only unvoted candidates are pruned, oldest first.
        droppable = [r for r in cands if not r.get("pending_review")]
        for r in droppable[:max(0, len(cands) - keep)]:
            self.store.delete(f"specs/{r['id']}")
            dropped += 1
        return dropped

    def seed_from_dir(self, folder: str | Path) -> dict[str, Any]:
        added, failed = [], {}
        for f in sorted(Path(folder).glob("*.json")):
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                failed[f.name] = [str(e)]
                continue
            r = self.add(spec, spec.get("notes", "") if isinstance(spec, dict) else "", status="seed", source=f"seed:{f.name}")
            if r["ok"]:
                added.append(r["id"])
            else:
                failed[f.name] = r["errors"]
        return {"added": added, "failed": failed}

    def feedback(self, rid: str, accepted: bool, note: str = "") -> dict[str, Any]:
        raw = self.store.get(f"specs/{rid}") if isinstance(rid, str) and re.match(r"^[a-z0-9_]+$", rid) else None
        if raw is None:
            return {"ok": False, "errors": ["unknown spec id"]}
        rec = json.loads(raw)
        if rec["status"] == "seed":
            rec["accepted" if accepted else "rejected"] += 1            # seeds keep their status; the count still records opinion
        elif accepted and self.review_required:
            rec["accepted"] += 1                                         # a vote, not a promotion: wait for approve()
            if rec["status"] == "candidate":
                rec["pending_review"] = True
        else:
            rec["status"] = "accepted" if accepted else "rejected"
            rec["accepted" if accepted else "rejected"] += 1
            rec.pop("pending_review", None)
        if note:
            rec["last_note"] = str(note)[:300]
        self.store.put(f"specs/{rid}", json.dumps(rec))
        return {"ok": True, "status": rec["status"], "pending_review": bool(rec.get("pending_review"))}

    # ------------------------------------------------------------------ review queue
    def pending_review(self) -> list[dict[str, Any]]:
        """Candidates somebody gave a thumbs-up (or generated), waiting for an operator. Oldest first."""
        recs = [r for r in self.all(("candidate",)) if r.get("pending_review")]
        return sorted(recs, key=lambda r: (r.get("added", 0), r["id"]))

    def approve(self, rid: str, *, allow_failing_checks: bool = False) -> dict[str, Any]:
        """Promote a reviewed candidate to an example. Refuses a spec that no longer validates or whose own design
        checks fail, unless the operator says otherwise."""
        rec = self.get(rid)
        if rec is None:
            return {"ok": False, "errors": ["unknown spec id"]}
        ex = VS.expand_vehicle(rec["spec"], None, None, self.module_dir)
        if not ex["ok"]:
            return {"ok": False, "errors": ex["errors"][:5]}
        bad = [c["name"] for c in ex["checks"] if c["status"] != "pass"]
        if bad and not allow_failing_checks:
            return {"ok": False, "errors": ["design checks failing: " + ", ".join(bad[:5])]}
        if rec["status"] != "seed":
            rec["status"] = "accepted"
        rec.pop("pending_review", None)
        rec["reviewed"] = time.time()
        self.store.put(f"specs/{rid}", json.dumps(rec))
        return {"ok": True, "status": rec["status"]}

    def decline(self, rid: str, note: str = "") -> dict[str, Any]:
        rec = self.get(rid)
        if rec is None:
            return {"ok": False, "errors": ["unknown spec id"]}
        if rec["status"] != "seed":
            rec["status"] = "rejected"
        rec.pop("pending_review", None)
        rec["rejected"] = rec.get("rejected", 0) + 1
        if note:
            rec["last_note"] = str(note)[:300]
        self.store.put(f"specs/{rid}", json.dumps(rec))
        return {"ok": True, "status": rec["status"]}

    def record_failure(self, description: str, errors: list[str]) -> None:
        raw = self.store.get("lessons")
        data = json.loads(raw) if raw else {}
        for e in errors[:20]:
            p = error_pattern(e)
            cur = data.get(p, {"count": 0, "example": str(e)[:300]})
            cur["count"] += 1
            data[p] = cur
        if len(data) > 500:                                              # keep the table bounded
            data = dict(sorted(data.items(), key=lambda kv: -kv[1]["count"])[:300])
        self.store.put("lessons", json.dumps(data))

    # ------------------------------------------------------------------ reading
    def get(self, rid: str) -> dict[str, Any] | None:
        raw = self.store.get(f"specs/{rid}") if isinstance(rid, str) and re.match(r"^[a-z0-9_]+$", rid) else None
        return json.loads(raw) if raw else None

    def all(self, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        recs = [json.loads(self.store.get(k) or "{}") for k in self.store.keys("specs/")]
        return [r for r in recs if r and (statuses is None or r.get("status") in statuses)]

    def search(self, text: str, k: int = 2, statuses: tuple[str, ...] = ("seed", "accepted")) -> list[dict[str, Any]]:
        recs = self.all(statuses)
        if not recs:
            return []

        def doc(r: dict[str, Any]) -> set[str]:
            comp = " ".join(f"{c.get('id', '')} {c.get('type', '')} {c.get('module', '')}"
                            for c in r["spec"].get("components", []) if isinstance(c, dict))
            return set(tokens(f"{r['name']} {r['description']} {' '.join(r.get('tags', []))} {comp}"))
        docs = [doc(r) for r in recs]
        n = len(recs)
        q = set(tokens(text))
        scored = []
        for r, d in zip(recs, docs):
            s = sum(math.log(1 + n / (1 + sum(t in dd for dd in docs))) for t in q if t in d)
            name_hit = any(t in set(tokens(r["name"])) for t in q)
            s += 2.0 if name_hit else 0.0
            s += 0.3 * min(r.get("accepted", 0), 5) - 0.3 * min(r.get("rejected", 0), 5)
            if s > 0:
                scored.append((s, r["id"], r))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [r for _, _, r in scored[:k]]

    def lessons(self, limit: int = 6, min_count: int = 2) -> list[str]:
        raw = self.store.get("lessons")
        data = json.loads(raw) if raw else {}
        top = sorted(data.items(), key=lambda kv: -kv[1]["count"])
        return [f"{v['example']}  (seen {v['count']}x)" for _, v in top if v["count"] >= min_count][:limit]


def _main(argv: list[str] | None = None) -> int:
    """Operator tool for the shared vehicle library (the same store the app uses, see VEHICLE_LIBRARY_DIR):
        python spec_library.py pending            list designs waiting for review
        python spec_library.py show <id>          print one spec
        python spec_library.py approve <id>       make it an example for other users
        python spec_library.py decline <id>       never use it
    Run it on the server that holds the library folder. With no writable folder the app keeps the library in memory,
    and there is nothing for this tool to see."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("pending", "show", "approve", "decline"):
        print(_main.__doc__)
        return 2
    import spec_library as _app          # run as a script this file is __main__; the app's library uses this imported copy
    import vehicle_chat as VC
    lib = VC.get_library()
    if isinstance(lib.store, _app.MemoryStore):
        print("The library is in memory only (no writable VEHICLE_LIBRARY_DIR), so there is nothing to review here.")
        return 1
    cmd = args[0]
    if cmd == "pending":
        for r in lib.pending_review():
            print(f"{r['id']}\t{r['parts']} parts\t+{r.get('accepted', 0)}/-{r.get('rejected', 0)}\t{r.get('name', '')}")
        return 0
    if len(args) < 2:
        print(_main.__doc__)
        return 2
    if cmd == "show":
        rec = lib.get(args[1])
        print(json.dumps(rec, indent=2) if rec else "unknown spec id")
        return 0 if rec else 1
    res = lib.approve(args[1]) if cmd == "approve" else lib.decline(args[1])
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())

"""Revision identity, evidence records, staleness and change impact."""
import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from aerospace.analysis import CONSUMES, METHOD_VERSIONS

PACK_VERSION = "0.2.0"
TEMPLATE_VERSION = "1"          # bump when a CAD template's geometry logic changes


def _canon(o):
    if isinstance(o, float):
        return format(o, ".12g")
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in sorted(o.items())}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    return o


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(_canon(obj), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def revision_id(part_type: str, params: dict, material: dict) -> str:
    """Identity of a design revision: any change to geometry inputs or material
    yields a new id, so evidence bound to the old id is visibly stale."""
    return digest({"part_type": part_type, "params": params, "material": material,
                   "template_version": TEMPLATE_VERSION})


def _lookup(spec: dict, path: str):
    cur = spec
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def consumed_snapshot(spec: dict, method: str) -> dict:
    return {p: _lookup(spec, p) for p in CONSUMES[method]}


@dataclass
class EvidenceRecord:
    design_id: str
    revision_id: str
    kind: str                    # 'analysis' | 'fea' | 'test' | 'inspection'
    method: str
    method_version: str
    status: str                  # 'pass' | 'fail' | 'not_evaluable'
    inputs_hash: str
    outputs: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)
    tool: str = "nitocad.aerospace"
    tool_version: str = PACK_VERSION
    created_by: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self):
        return asdict(self)


def record_from_analysis(design_id, rev, spec, res, created_by="") -> EvidenceRecord:
    return EvidenceRecord(design_id, rev, "analysis", res.method, res.version, res.status,
                          digest(consumed_snapshot(spec, res.method)),
                          dict(res.outputs), list(res.assumptions), created_by=created_by)


def is_current(rec: dict, current_rev: str) -> bool:
    ok_method = rec.get("method_version") == METHOD_VERSIONS.get(rec.get("method"), rec.get("method_version"))
    return rec.get("revision_id") == current_rev and ok_method


def external_evidence_status(ev: dict, current_rev: str, spec: dict) -> dict:
    """External evidence (FEA/test/inspection) cannot be recomputed. It is current
    only if bound to this revision, or if every input it declared as consumed is
    unchanged (then it is carried forward automatically and says so)."""
    if ev.get("revision_id") == current_rev:
        return {"id": ev.get("id"), "current": True, "carried_forward": False}
    snap = ev.get("consumed_inputs")
    if isinstance(snap, dict) and snap and all(_lookup(spec, k) == v for k, v in snap.items()):
        return {"id": ev.get("id"), "current": True, "carried_forward": True,
                "note": "declared inputs unchanged since the bound revision"}
    return {"id": ev.get("id"), "current": False, "carried_forward": False,
            "note": "bound to an older revision and its declared inputs changed or were not declared"}


def _flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def change_impact(old_spec: dict, new_spec: dict) -> dict:
    """What changed, which analyses consume it, which external evidence goes stale."""
    a, b = _flatten(old_spec), _flatten(new_spec)
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)
                     and not k.startswith(("evidence", "requirements", "design_id")))
    affected = {}
    for method, paths in CONSUMES.items():
        hit = [c for c in changed for p in paths if c == p or c.startswith(p + ".") or p.startswith(c + ".")]
        if hit:
            affected[method] = sorted(set(hit))
    old_rev = revision_id(old_spec.get("part_type", ""), old_spec.get("params", {}), old_spec.get("material", {}))
    new_rev = revision_id(new_spec.get("part_type", ""), new_spec.get("params", {}), new_spec.get("material", {}))
    ext = [external_evidence_status(e, new_rev, new_spec) for e in new_spec.get("evidence", [])]
    return {
        "revision_changed": old_rev != new_rev, "old_revision": old_rev, "new_revision": new_rev,
        "changed_fields": [{"field": k, "old": a.get(k), "new": b.get(k)} for k in changed],
        "analyses_to_rerun": affected,
        "external_evidence": ext,
        "stale_external_evidence": [e["id"] for e in ext if not e["current"]],
    }

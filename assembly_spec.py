"""
Assembly specification: parts + mates -> validated, placed, checked assembly.

Pure Python (no CadQuery). Given a spec dict

    {"name": "front_left_corner", "root": "arm",
     "parts": [{"id": "arm", "part_type": "control_arm", "parameters": {...}},
               {"id": "hub", "part_type": "wheel_hub", "placement": {"position": [x,y,z], "rotation": [rx,ry,rz]}}, ...],
     "mates": [{"a": "arm.ball_joint_socket", "b": "knuckle.ball_joint_pin", "gap_mm": 0}, ...]}

resolve_assembly():
  1. validates every part's parameters (auto-corrections are recorded, errors block)
  2. collects each part's mating interfaces
  3. places parts by walking the mates from the root: a mate whose one side is
     already placed positions the other side. Mates between two parts that are
     both already placed are VERIFIED instead (a loop of mates must agree)
  4. runs the compatibility rule for every mate (bolt patterns, bores vs
     pilots, faces, journals, pins)
  5. reports interfaces nothing is connected to ("open interfaces") honestly
  6. builds the dependency graph and supports what-if impact analysis

It never raises for a design problem - problems are Findings. Structurally
unusable input raises AssemblySpecError.

The solver and rules are dimensional. Actual overlap of the real solids is
checked separately on the built geometry (assembly.check_interference).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import mating as M

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# No part or mate cap. Set either to an int to bring a limit back; None means unlimited.
MAX_PARTS: int | None = None
MAX_MATES: int | None = None
MAX_GAP_MM = 200.0


class AssemblySpecError(ValueError):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Dependency:
    src: str            # "part.param" or "part.@placement"
    dst: str
    relation: str
    mate: str
    directed: bool


class DependencyGraph:
    """Which parameters constrain which. Coupling edges (undirected) come from
    mate rules, e.g. rotor.bolt_pcd_mm == hub.bolt_pcd_mm. Placement edges
    (directed) say what moves a part: the parameters that locate the
    interfaces it is mated through, and its parent's placement."""

    def __init__(self) -> None:
        self.edges: list[Dependency] = []

    def add(self, dep: Dependency) -> None:
        if dep not in self.edges:
            self.edges.append(dep)

    def affected_by(self, start: Iterable[str]) -> list[str]:
        seen = set(start)
        frontier = list(seen)
        while frontier:
            n = frontier.pop()
            for e in self.edges:
                nxt = None
                if e.src == n:
                    nxt = e.dst
                elif e.dst == n and not e.directed:
                    nxt = e.src
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return sorted(seen - set(start))

    def to_dict(self) -> dict:
        nodes = sorted({e.src for e in self.edges} | {e.dst for e in self.edges})
        return {"nodes": nodes, "edges": [
            {"from": e.src, "to": e.dst, "relation": e.relation, "mate": e.mate, "directed": e.directed}
            for e in self.edges]}


@dataclass
class ResolvedPart:
    id: str
    part_type: str
    parameters: dict
    corrections: dict = field(default_factory=dict)
    interfaces: dict[str, M.Interface] = field(default_factory=dict)
    placement: M.Transform | None = None
    placed_by: str | None = None          # "root" | "explicit" | "mate:<index>"
    explicit: dict | None = None          # the original explicit placement, if any
    valid: bool = True                    # False: its own parameters failed validation
    values: dict = field(default_factory=dict)   # every parameter, defaults + derived included


@dataclass
class ResolvedAssembly:
    name: str
    root: str | None
    parts: dict[str, ResolvedPart]
    mates: list[dict]
    findings: list[M.Finding]
    graph: DependencyGraph
    order: list[str]

    @property
    def errors(self) -> list[M.Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[M.Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def resolved_spec(self) -> dict:
        """The spec with corrected parameters: rebuilding from this is
        deterministic (this is what gets stored with a job)."""
        parts = []
        for p in self.parts.values():
            d: dict[str, Any] = {"id": p.id, "part_type": p.part_type, "parameters": dict(p.parameters)}
            if p.explicit:
                d["placement"] = p.explicit
            parts.append(d)
        out: dict[str, Any] = {"name": self.name, "parts": parts, "mates": [dict(m) for m in self.mates]}
        if self.root:
            out["root"] = self.root
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name, "ok": self.ok, "root": self.root, "build_order": self.order,
            "parts": [{
                "id": p.id, "part_type": p.part_type, "parameters": p.parameters,
                "resolved_values": p.values, "corrections": p.corrections, "placed_by": p.placed_by,
                "placement": p.placement.to_dict() if p.placement else None,
                "interfaces": [{"name": i.name, "kind": i.kind, "description": i.description,
                                "data": dict(i.data)} for i in p.interfaces.values()],
            } for p in self.parts.values()],
            "mates": self.mates,
            "findings": [f.to_dict() for f in self.findings],
            "dependency_graph": self.graph.to_dict(),
        }


# --- structural validation ----------------------------------------------------------

def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def _vec3(x: Any) -> bool:
    return isinstance(x, (list, tuple)) and len(x) == 3 and all(_num(c) for c in x)


def _validate_structure(spec: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["assembly spec must be an object"]
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        return ["assembly needs a non-empty 'parts' list"]
    if MAX_PARTS is not None and len(parts) > MAX_PARTS:
        errs.append(f"too many parts ({len(parts)}); max is {MAX_PARTS}")
    seen: set[str] = set()
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            errs.append(f"parts[{i}] must be an object")
            continue
        pid, ptype = p.get("id"), p.get("part_type")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            errs.append(f"parts[{i}].id must be lowercase letters/digits/underscore starting with a letter (got {pid!r})")
        elif pid in seen:
            errs.append(f"duplicate part id {pid!r}")
        else:
            seen.add(pid)
        if not isinstance(ptype, str) or not ptype:
            errs.append(f"parts[{i}].part_type is required")
        params = p.get("parameters", {})
        if params is not None and not isinstance(params, dict):
            errs.append(f"parts[{i}].parameters must be an object")
        pl = p.get("placement")
        if pl is not None:
            if not isinstance(pl, dict) or not _vec3(pl.get("position", [0, 0, 0])) or not _vec3(pl.get("rotation", [0, 0, 0])):
                errs.append(f"parts[{i}].placement needs numeric position [x,y,z] and rotation [rx,ry,rz] degrees")
    mates = spec.get("mates", [])
    if not isinstance(mates, list):
        errs.append("'mates' must be a list")
    else:
        if MAX_MATES is not None and len(mates) > MAX_MATES:
            errs.append(f"too many mates ({len(mates)}); max is {MAX_MATES}")
        for i, m in enumerate(mates):
            if not isinstance(m, dict) or not isinstance(m.get("a"), str) or not isinstance(m.get("b"), str):
                errs.append(f"mates[{i}] needs string 'a' and 'b' (\"part.interface\")")
                continue
            g = m.get("gap_mm")
            if g is not None and (not _num(g) or g < 0 or g > MAX_GAP_MM):
                errs.append(f"mates[{i}].gap_mm must be a number between 0 and {MAX_GAP_MM:g}")
    root = spec.get("root")
    if root is not None and (not isinstance(root, str) or root not in seen):
        errs.append(f"'root' must be the id of one of the parts (got {root!r})")
    return errs


def _default_known_types() -> set[str]:
    import domain_packs
    types = set(domain_packs.part_type_names())
    try:
        from cad_templates import TEMPLATES
        types |= set(TEMPLATES)
    except Exception:  # noqa: BLE001 - no CadQuery here (pure-Python use)
        pass
    return types


def _split(ref: str) -> tuple[str, str] | None:
    if ref.count(".") != 1:
        return None
    a, b = ref.split(".")
    return (a, b) if a and b else None


# --- resolve ------------------------------------------------------------------------

def resolve_assembly(spec: dict, known_types: set[str] | None = None) -> ResolvedAssembly:
    errs = _validate_structure(spec)
    if errs:
        raise AssemblySpecError(errs)

    import domain_packs
    from validator import validate_parameters

    known = known_types if known_types is not None else _default_known_types()
    findings: list[M.Finding] = []
    parts: dict[str, ResolvedPart] = {}

    # 1) parts: validate parameters, collect interfaces
    for p in spec["parts"]:
        pid, ptype = p["id"], p["part_type"]
        if ptype not in known:
            findings.append(M.Finding("error", "unknown_part_type", f"unknown part type {ptype!r}", pid))
            parts[pid] = ResolvedPart(pid, ptype, dict(p.get("parameters") or {}), valid=False)
            continue
        corrected, vres = validate_parameters(ptype, dict(p.get("parameters") or {}))
        rp = ResolvedPart(pid, ptype, corrected, dict(vres.corrections), explicit=p.get("placement"),
                          valid=vres.is_valid)
        parts[pid] = rp
        for e in vres.errors:
            findings.append(M.Finding("error", "part_invalid", e, pid))
        for param, c in vres.corrections.items():
            findings.append(M.Finding("info", "parameter_corrected",
                                      f"{param} auto-corrected from {c.get('old')} to {c.get('new')}", pid))
        if vres.is_valid:
            try:
                rv = domain_packs.resolved_values(ptype, corrected)
                rp.values = rv if rv is not None else dict(corrected)
                for itf in domain_packs.part_interfaces(ptype, corrected):
                    rp.interfaces[itf.name] = itf
            except ValueError as exc:
                findings.append(M.Finding("error", "interfaces_failed", f"could not derive interfaces: {exc}", pid))

    # 2) mates: resolve references
    mates: list[dict] = []
    refs: list[tuple[str, str, str, str] | None] = []
    for i, m in enumerate(spec.get("mates", [])):
        a, b = _split(m["a"]), _split(m["b"])
        ok = True
        for ref, sp in ((m["a"], a), (m["b"], b)):
            if sp is None:
                findings.append(M.Finding("error", "bad_mate_reference", f"mate reference {ref!r} must look like 'part.interface'"))
                ok = False
            elif sp[0] not in parts:
                findings.append(M.Finding("error", "unknown_part", f"mate refers to unknown part {sp[0]!r}", ref))
                ok = False
            elif not parts[sp[0]].valid:
                ok = False      # root cause already reported as part_invalid; don't add knock-on noise
            elif parts[sp[0]].interfaces and sp[1] not in parts[sp[0]].interfaces:
                findings.append(M.Finding("error", "unknown_interface",
                                          f"{sp[0]} has no interface {sp[1]!r} (it has: {', '.join(parts[sp[0]].interfaces)})", ref))
                ok = False
            elif not parts[sp[0]].interfaces:
                findings.append(M.Finding("error", "no_interfaces",
                                          f"{sp[0]} ({parts[sp[0]].part_type}) declares no mating interfaces; give it an explicit placement instead", ref))
                ok = False
        mates.append({k: v for k, v in m.items() if k in ("a", "b", "gap_mm")})
        refs.append((a[0], a[1], b[0], b[1]) if ok and a and b else None)
        if ok and a and b and a[0] == b[0]:
            findings.append(M.Finding("error", "self_mate", "a part cannot be mated to itself", m["a"], m["b"]))
            refs[-1] = None

    # 3) placement walk
    placed: list[str] = []
    root = spec.get("root") or spec["parts"][0]["id"]
    for pid, rp in parts.items():
        if rp.explicit is not None:
            pl = rp.explicit
            rp.placement = M.transform_from_pose(tuple(pl.get("position", (0, 0, 0))), tuple(pl.get("rotation", (0, 0, 0))))
            rp.placed_by = "explicit"
            placed.append(pid)
    if root not in placed:
        parts[root].placement = M.Transform()
        parts[root].placed_by = "root"
        placed.insert(0, root)

    used_to_position: set[int] = set()
    progress = True
    while progress:
        progress = False
        for i, r in enumerate(refs):
            if r is None or i in used_to_position:
                continue
            pa, ia, pb, ib = r
            a_placed, b_placed = parts[pa].placement is not None, parts[pb].placement is not None
            if a_placed == b_placed:
                continue
            parent, pi, child, ci = (pa, ia, pb, ib) if a_placed else (pb, ib, pa, ia)
            if parts[child].explicit is not None:
                continue
            A = parts[parent].interfaces[pi]
            B = parts[child].interfaces[ci]
            gap = mates[i].get("gap_mm")
            if gap is None:
                gap = max(float(A.data.get("standoff_mm", 0.0)), float(B.data.get("standoff_mm", 0.0)))
            a_world = A.frame.transformed(parts[parent].placement)
            parts[child].placement = M.solve_mate(a_world, B.frame, gap)
            parts[child].placed_by = f"mate:{i}"
            used_to_position.add(i)
            placed.append(child)
            progress = True

    for pid, rp in parts.items():
        if rp.placement is None and rp.valid:
            findings.append(M.Finding("error", "unplaced_part",
                                      f"{pid} is not connected to the root ({root}) by any mate and has no explicit placement", pid))

    # 4) verify closing mates + run compatibility rules + collect couplings
    graph = DependencyGraph()
    for i, r in enumerate(refs):
        if r is None:
            continue
        pa, ia, pb, ib = r
        A, B = parts[pa].interfaces[ia], parts[pb].interfaces[ib]
        an, bn = f"{pa}.{ia}", f"{pb}.{ib}"
        rule_findings, couplings = M.check_pair(A, B, an, bn)
        findings.extend(rule_findings)
        label = f"{an} <-> {bn}"
        gap = mates[i].get("gap_mm")
        if gap is None:
            gap = max(float(A.data.get("standoff_mm", 0.0)), float(B.data.get("standoff_mm", 0.0)))
        if i not in used_to_position and parts[pa].placement and parts[pb].placement:
            findings.extend(M.verify_mate_world(
                A.frame.transformed(parts[pa].placement), B.frame.transformed(parts[pb].placement), gap, True, an, bn))
        for c in couplings:
            for sa in _srcs(A, c.a_key):
                for sb in _srcs(B, c.b_key):
                    graph.add(Dependency(f"{pa}.{sa}", f"{pb}.{sb}", c.relation, label, False))
        if i in used_to_position:
            child_is_b = parts[pb].placed_by == f"mate:{i}"
            par, parent_if, ch, child_if = (pa, A, pb, B) if child_is_b else (pb, B, pa, A)
            dst = f"{ch}.@placement"
            for p_ in parent_if.frame_sources:
                graph.add(Dependency(f"{par}.{p_}", dst, "locates", label, True))
            for p_ in child_if.frame_sources:
                graph.add(Dependency(f"{ch}.{p_}", dst, "locates", label, True))
            for itf, owner in ((parent_if, par), (child_if, ch)):
                for p_ in _srcs(itf, "standoff_mm"):
                    graph.add(Dependency(f"{owner}.{p_}", dst, "gap", label, True))
            graph.add(Dependency(f"{par}.@placement", dst, "carries", label, True))

    # 5) open interfaces
    used = {(r[0], r[1]) for r in refs if r} | {(r[2], r[3]) for r in refs if r}
    for pid, rp in parts.items():
        for iname, itf in rp.interfaces.items():
            if (pid, iname) not in used:
                extra = " (no mating part exists in the catalog yet)" if itf.kind in M.OPEN_ONLY_KINDS else ""
                findings.append(M.Finding("info", "open_interface",
                                          f"{pid}.{iname} ({itf.kind}) is not connected to anything{extra}", f"{pid}.{iname}"))

    return ResolvedAssembly(
        name=str(spec.get("name") or "assembly"), root=root, parts=parts, mates=mates,
        findings=findings, graph=graph, order=placed)


def _srcs(itf: M.Interface, key: str) -> list[str]:
    s = itf.sources.get(key)
    return [x.strip() for x in s.split(",")] if s else []


# --- what-if impact -------------------------------------------------------------------

def analyze_change(spec: dict, part_id: str, changes: dict, known_types: set[str] | None = None) -> dict:
    """What happens if `part_id`'s parameters are changed? Re-resolves the
    assembly with the change applied and reports, against the baseline:
      * which parameters/placements the dependency graph says are affected
      * which parts actually moved
      * which findings are new (the change broke something) or resolved
    Pure Python - no geometry is built, no credit is spent."""
    base = resolve_assembly(spec, known_types)
    if part_id not in base.parts:
        raise AssemblySpecError([f"unknown part {part_id!r}"])
    if not isinstance(changes, dict) or not changes:
        raise AssemblySpecError(["'changes' must be a non-empty object of parameter: value"])
    mod_spec = copy.deepcopy(spec)
    for p in mod_spec["parts"]:
        if p["id"] == part_id:
            p["parameters"] = {**(p.get("parameters") or {}), **changes}
    mod = resolve_assembly(mod_spec, known_types)

    start = [f"{part_id}.{k}" for k in changes]
    affected = sorted(set(base.graph.affected_by(start)) | set(mod.graph.affected_by(start)))
    affected_parts = sorted({n.split(".")[0] for n in affected} - {part_id})

    moved = []
    for pid, bp in base.parts.items():
        mp = mod.parts.get(pid)
        if bp.placement and mp and mp.placement and not bp.placement.close_to(mp.placement, 1e-6):
            d = M.sub(mp.placement.t, bp.placement.t)
            moved.append({"part": pid, "translation_mm": [round(x, 4) for x in d],
                          "rotated": not all(abs(bp.placement.R[i][j] - mp.placement.R[i][j]) < 1e-6
                                             for i in range(3) for j in range(3))})

    def keyed(res: ResolvedAssembly) -> dict:
        return {f.key(): f for f in res.findings if f.severity in ("error", "warning")}

    kb, km = keyed(base), keyed(mod)
    return {
        "changed": {"part": part_id, "parameters": changes},
        "ok_before": base.ok, "ok_after": mod.ok,
        "affected_nodes": affected,
        "affected_parts": affected_parts,
        "moved_parts": moved,
        "new_findings": [f.to_dict() for k, f in km.items() if k not in kb],
        "resolved_findings": [f.to_dict() for k, f in kb.items() if k not in km],
        "blocking": [f.to_dict() for f in mod.errors],
        "corrections_after": {pid: p.corrections for pid, p in mod.parts.items() if p.corrections},
    }

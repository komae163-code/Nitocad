"""
Look at what was just built, fix what is wrong, look again. Every kind of design, not only vehicles.

vehicle_inspect.py does this for vehicles from the parts' boxes. This module does it for everything else the chat
builds, from the checks each domain already runs:

  assembly   mechanical assemblies, machines, aerospace and building assemblies: the real-solid overlap test and the
             "separate groups" test the build already ran (assembly_checks), read from the build result. A serious
             overlap or a part that touches nothing is a fault; a small overlap is a fit and only noted.
  pcb_board  circuit boards: NitoCAD's own routing and design-rule checks (pcb.pipeline.build_board, pure Python, about
             7 s a run, no KiCad needed). Unrouted nets and clearance, courtyard, edge or connectivity violations are
             faults.

A pass proposes one fix and keeps it only if the checks come back better; otherwise the design that went in is the one
that comes out. At most MAX_PASSES fixes. Never raises (a check that cannot run says so and changes nothing).

  assembly fix  the planner is asked to fix exactly the listed faults (needs an AI plan; the Engine plan has none)
  pcb fix       the board is made wider (board.max_width_mm, 20 to 200 mm), which gives the placer and router room.
                Not proven here: no failing board could be produced offline, so a wider board is only kept when the
                rebuilt checks are strictly better.

Single parts (a bracket, a wall, a wing) are not inspected here: their checks (validation, geometry verification) block a
bad build before this point and their notes are already shown.
"""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any, Callable

MAX_PASSES = 2
MAX_FINDINGS = 6
PCB_WIDTH_STEP = 1.3
PCB_WIDTH_RANGE = (20.0, 200.0)                    # board.max_width_mm must be inside this (pcb.compose)
_PCB_FIXABLE = {"clearance", "courtyard", "edge", "connectivity"}


def _finding(code: str, severity: str, parts: list[str], detail: str, instruction: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "parts": parts[:6], "detail": detail, "instruction": instruction}


def score(findings: list[dict[str, Any]]) -> int:
    return sum(3 if f["severity"] == "fix" else 1 for f in findings)


# ------------------------------------------------------------------------------ assemblies
def assess_assembly(result: dict[str, Any]) -> list[dict[str, Any]]:
    chk = result.get("assembly_checks")
    if not isinstance(chk, dict):
        return []
    out: list[dict[str, Any]] = []
    inter = [i for i in (chk.get("interferences") or []) if isinstance(i, dict)]
    major = [i for i in inter if i.get("severity") == "major"]
    if major:
        pairs = "; ".join(f"{i['a']} and {i['b']}" for i in major[:3])
        out.append(_finding("overlap", "fix", sorted({n for i in major[:3] for n in (i["a"], i["b"])}),
                            f"{len(major)} serious overlap{'s' if len(major) != 1 else ''}: {pairs}",
                            f"These parts overlap in space: {pairs}. Move or resize them so they only touch, "
                            "keeping every other part and size as it is."))
    if len(inter) > len(major):
        n = len(inter) - len(major)
        out.append(_finding("small_overlap", "note", [], f"{n} small overlap{'s' if n != 1 else ''} (fits or tolerances)", ""))
    groups = [g for g in (chk.get("disconnected_groups") or []) if isinstance(g, list)]
    if len(groups) > 1:
        loose = [n for g in groups[1:] for n in g]
        out.append(_finding("floating", "fix", loose,
                            f"{len(loose)} part{'s' if len(loose) != 1 else ''} not touching the rest: " + ", ".join(loose[:4]),
                            "Attach " + ", ".join(loose[:6]) + " to the rest of the assembly: mate them or move them so they touch "
                            "the nearest part. Keep everything else as it is."))
    if chk.get("unchecked_pairs"):
        out.append(_finding("unchecked", "note", [], f"{len(chk['unchecked_pairs'])} part pairs could not be tested", ""))
    return out[:MAX_FINDINGS]


# ------------------------------------------------------------------------------ circuit boards
def assess_pcb(board_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Route and check the board with NitoCAD's own pure-Python pipeline. A board that cannot be checked here (a missing
    footprint library, a spec error) gives one note and nothing to fix."""
    try:
        from pcb.pipeline import build_board
        with tempfile.TemporaryDirectory() as d:
            rep = build_board(copy.deepcopy(board_spec), Path(d), run_kicad=False)
    except Exception as exc:  # noqa: BLE001
        return [_finding("not_checked", "note", [], "the board could not be checked here: " + str(exc)[:110], "")]
    stages = rep.get("stages") or {}
    out: list[dict[str, Any]] = []
    unrouted = list((stages.get("route") or {}).get("unrouted_nets") or [])
    if unrouted:
        out.append(_finding("unrouted", "fix", [], f"{len(unrouted)} net{'s' if len(unrouted) != 1 else ''} could not be routed: "
                            + ", ".join(map(str, unrouted[:4])), "Give the router more room."))
    for kind, items in ((stages.get("drc_lite") or {}).get("problems") or {}).items():
        if items:
            out.append(_finding(kind, "fix" if kind in _PCB_FIXABLE else "note", [],
                                f"{len(items)} {kind} problem{'s' if len(items) != 1 else ''}: {str(items[0])[:90]}", "Give the layout more room."))
    return out[:MAX_FINDINGS]


def widen_board(plan_spec: dict[str, Any]) -> dict[str, Any] | None:
    """The plan with a board 30 % wider, or None at the limit or when this is not a board plan."""
    try:
        board_spec = plan_spec["parameters"]["spec"]
        cur = float((board_spec.get("board") or {}).get("max_width_mm", 80.0))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    new = min(PCB_WIDTH_RANGE[1], max(PCB_WIDTH_RANGE[0], round(cur * PCB_WIDTH_STEP, 1)))
    if new <= cur:
        return None
    out = copy.deepcopy(plan_spec)
    out["parameters"]["spec"].setdefault("board", {})["max_width_mm"] = new
    return out


# ------------------------------------------------------------------------------ the loop
def part_names(spec: dict[str, Any] | None) -> set[str]:
    try:
        return {str(p.get("name")) for p in spec["parameters"]["parts"] if isinstance(p, dict)}
    except (KeyError, TypeError):
        return set()


def kind_of(plan_spec: dict[str, Any] | None, result: dict[str, Any] | None) -> str | None:
    if not isinstance(plan_spec, dict):
        return None
    if plan_spec.get("part_type") == "pcb_board":
        return "pcb"
    if isinstance((result or {}).get("assembly_checks"), dict):
        return "assembly"
    return None


def refine(kind: str, spec: dict[str, Any], result: dict[str, Any], *, rebuild: Callable[[dict[str, Any]], "dict[str, Any] | None"],
           replan: Callable[[str, dict[str, Any]], "dict[str, Any] | None"] | None = None,
           say: Callable[..., None] | None = None, max_passes: int = MAX_PASSES) -> dict[str, Any]:
    """Check, fix, check again. `rebuild(spec)` builds a spec and returns its result (None on failure); `replan(text, spec)`
    asks the planner for a fixed spec (assemblies). Returns {"spec", "result", "changed", "findings", "summary"}."""
    say = say or (lambda *a, **k: None)
    max_passes = max(0, min(int(max_passes), MAX_PASSES))
    best_spec, best_result = spec, result
    changed, checks = False, 0
    findings: list[dict[str, Any]] = []
    try:
        say("Inspecting the finished design", "checking the built model" if kind == "assembly" else "routing and rule checks, about 10 seconds")
        findings = assess_assembly(result) if kind == "assembly" else assess_pcb(spec["parameters"]["spec"])
        checks = 1
        for n in range(max_passes):
            fixable = [f for f in findings if f["severity"] == "fix"]
            if not fixable:
                break
            say("Fixing what the inspection found", "; ".join(f["detail"] for f in fixable[:2]))
            if kind == "assembly":
                if replan is None:
                    break
                new_spec = replan(" ".join(f["instruction"] for f in fixable[:MAX_FINDINGS]), best_spec)
                if not isinstance(new_spec, dict) or new_spec == best_spec:
                    break
                if not part_names(best_spec) <= part_names(new_spec):
                    break                                       # a fix may move, resize or add parts, never delete one
                new_result = rebuild(new_spec)
                if new_result is None:
                    break
                new_findings = assess_assembly(new_result)
            else:                                                   # board: check the wider spec first, build only the winner
                new_spec = widen_board(best_spec)
                if new_spec is None:
                    break
                new_result = None
                new_findings = assess_pcb(new_spec["parameters"]["spec"])
            checks += 1
            if score(new_findings) >= score(findings):
                break                                               # not better: keep what we had
            best_spec, findings, changed = new_spec, new_findings, True
            if new_result is not None:
                best_result = new_result
        if changed and kind == "pcb":
            rebuilt = rebuild(best_spec)                            # the 3D board for the wider spec
            if rebuilt is None:
                best_spec, best_result, changed = spec, result, False
            else:
                best_result = rebuilt
    except Exception:  # noqa: BLE001 - an inspection problem must never lose the finished design
        import logging
        logging.getLogger(__name__).exception("design inspection failed")
        best_spec, best_result, changed = spec, result, False
    return {"spec": best_spec, "result": best_result, "changed": changed, "findings": findings, "checks": checks,
            "summary": summary(kind, changed, findings, checks)}


def summary(kind: str, changed: bool, findings: list[dict[str, Any]], checks: int) -> str:
    if not checks:
        return ""
    what = "overlaps, parts that touch nothing" if kind == "assembly" else "routing and design rules"
    lead = f"Checked the finished {'board' if kind == 'pcb' else 'assembly'} for {what}."
    if changed:
        lead += " Fixed what it found and checked again."
    if any(f["code"] == "not_checked" for f in findings):
        return lead + " " + next(f["detail"] for f in findings if f["code"] == "not_checked").capitalize() + "."
    if not findings:
        return lead + " Nothing wrong found."
    items = "; ".join(f["detail"] for f in findings[:3])
    if any(f["severity"] == "fix" for f in findings):
        return lead + " Still open: " + items + ". Tell me which to fix, or generate as it is."
    return lead + " Worth a look: " + items + "."

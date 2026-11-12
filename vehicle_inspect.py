"""
Look at the finished vehicle before handing it over, fix what is wrong, look again. At most two passes.

    report = inspect_vehicle(vspec, bbox_fn=..., revise_fn=..., vision_fn=..., meshes_fn=...)

Why: a model can pass every number check and still be wrong on screen (a wing floating over the tail, wheels poking
out of the body, glass that is a box on the roof). Nothing here trusts "does it look good?". Every pass runs a fixed
checklist on the placed parts, and, when a vision model is wired in, shows it renders next to the checklist's findings
and asks only about the same list of faults.

The checklist (all free, numpy only, no CAD kernel needed; boxes come from `bbox_fn`)
  floating     a part that touches nothing connected to the body
  overlap      two parts sharing a large share of their volume (small overlaps are fits, not faults)
  wheels       wheels not level, body hanging below the wheels
  wheel_poke   wheels sticking far out of the body sideways
  symmetry     left/right pairs that differ in size or are not mirrored about the centre line
  proportions  overall length/width/height outside a sane range for a car or truck

Fixing
  `revise_fn(vspec, instruction) -> new vspec or None` is vehicle_chat's existing revise path (one sentence in, a
  validated spec out). A pass that changes nothing, or makes the checklist worse, is dropped and the loop stops, so
  the design that comes back is never worse than the one that went in.

Vision (optional)
  `vision_fn(prompt, [png bytes]) -> str` is deployment wiring. When it is None the report says the visual pass was
  skipped; it never pretends to have looked.

Not verified: AABB tests are approximate on curved parts, and a clean checklist is not a safe or load-rated design.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import mates as M

BBoxFn = Callable[[dict[str, Any]], tuple[float, float, float, float, float, float]]
ReviseFn = Callable[[dict[str, Any], str], "dict[str, Any] | None"]
VisionFn = Callable[[str, "list[bytes]"], str]

MAX_PASSES = 2
MAX_FINDINGS = 6                                   # per pass: enough to act on, few enough for one instruction
_TOKENS = re.compile(r"[^a-z0-9]+")
# "wheel" in these names is not a road wheel: steering_wheel, crown_wheel, engine_flywheel, gear/pulley wheels.
_NOT_ROAD = {"steering", "crown", "fly", "flywheel", "gear", "cog", "pulley", "timing", "water", "hand", "impeller", "idler"}


def _tokens(name: str) -> set[str]:
    return {t for t in _TOKENS.split(name.lower()) if t}


def _is_wheel_part(name: str) -> bool:
    """Part of a road-wheel assembly (tyre, rim, axle stub, bearing ...). Used to let wheels overlap each other."""
    t = _tokens(name)
    return bool(t & {"tyre", "tire", "tyres", "tires", "rim"}) or ("wheel" in t and not t & _NOT_ROAD)


def _ground_wheels(names: list[str]) -> list[str]:
    """The parts that touch the ground. Tyres when the car has them, because rims and axles sit above the tread;
    otherwise whole wheels, never steering/crown/fly wheels."""
    tyres = [n for n in names if _tokens(n) & {"tyre", "tire", "tyres", "tires"}]
    return tyres or [n for n in names if _is_wheel_part(n) and not _tokens(n) & {"axle", "hub", "bearing", "bolt", "nut", "stub", "disc", "rotor", "brake"}]
# (min, max) of height/length and width/length. Only kinds where a wrong ratio is plainly a fault.
RATIOS = {"car": {"h/l": (0.20, 0.45), "w/l": (0.30, 0.55)}, "truck": {"h/l": (0.25, 0.70), "w/l": (0.25, 0.50)}}


_CORNER = {"fl": ("f*", "L"), "fr": ("f*", "R"), "rl": ("r*", "L"), "rr": ("r*", "R")}
_SIDE = {"l": "L", "left": "L", "r": "R", "right": "R"}


def _side_key(name: str) -> tuple[tuple[str, ...], str] | None:
    """(pairing key, 'L' or 'R') for a name that says which side of the car it is on, else None.
    A corner token (fl, fr, rl, rr) decides the side. A trailing _l/_r on such a name belongs to a pair inside
    that corner (wheel_fl_bearing_l and wheel_fl_bearing_r), so such a name is left out of the symmetry check."""
    toks = [t for t in name.lower().split("_") if t]
    for i, t in enumerate(toks):
        if t in _CORNER:
            if any(u in _SIDE for u in toks):      # a second side marker means a local pair whose handing depends on
                return None                        # how the wheel was mounted, so it is not checked here
            mark, side = _CORNER[t]
            return tuple(toks[:i] + [mark] + toks[i + 1:]), side
    for i, t in enumerate(toks):
        if t in _SIDE:
            return tuple(toks[:i] + ["*"] + toks[i + 1:]), _SIDE[t]
    return None


def _finding(code: str, severity: str, parts: list[str], detail: str, instruction: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "parts": parts[:6], "detail": detail, "instruction": instruction}


def world_boxes(parts: list[dict[str, Any]], bbox_fn: BBoxFn) -> dict[str, tuple[float, ...]]:
    out: dict[str, tuple[float, ...]] = {}
    for p in parts:
        if p.get("position") is None:
            continue
        loc = bbox_fn(p)
        out[p["name"]] = M.world_aabb(loc, tuple(p.get("rotation") or (0, 0, 0)), tuple(p["position"]))
    return out


def _vol(b: tuple[float, ...]) -> float:
    return max(0.0, b[3] - b[0]) * max(0.0, b[4] - b[1]) * max(0.0, b[5] - b[2])


def _gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Distance between two boxes along their worst-separated axis; 0 or less when they touch or overlap."""
    return max(max(a[i] - b[i + 3], b[i] - a[i + 3]) for i in range(3))


def _overlap_vol(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    e = [min(a[i + 3], b[i + 3]) - max(a[i], b[i]) for i in range(3)]
    return e[0] * e[1] * e[2] if min(e) > 0 else 0.0


# ------------------------------------------------------------------------------ the checklist
def check_geometry(parts: list[dict[str, Any]], bbox_fn: BBoxFn, *, kind: str | None = None,
                   intended_contact: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """Findings for the placed parts, worst first. [] means the checklist is clean."""
    boxes = world_boxes(parts, bbox_fn)
    if len(boxes) < 2:
        return []
    lo = [min(b[i] for b in boxes.values()) for i in range(3)]
    hi = [max(b[i + 3] for b in boxes.values()) for i in range(3)]
    dims = [hi[i] - lo[i] for i in range(3)]
    tol = max(2.0, 0.002 * max(dims))
    skip = {frozenset(p) for p in (intended_contact or [])}
    body = max(boxes, key=lambda n: _vol(boxes[n]))
    out: list[dict[str, Any]] = []

    # floating: parts not connected to the body through touching boxes
    names = list(boxes)
    seen, stack = {body}, [body]
    while stack:
        cur = stack.pop()
        for n in names:
            if n not in seen and _gap(boxes[cur], boxes[n]) <= tol:
                seen.add(n)
                stack.append(n)
    loose = [n for n in names if n not in seen]
    if loose:
        out.append(_finding("floating", "fix", loose, "not touching the body or anything attached to it: " + ", ".join(loose[:6]),
                            "Attach " + ", ".join(loose[:6]) + " to the body: move each so it touches the nearest body surface or its support."))

    # overlap: a large share of the smaller part's volume inside the other (small overlaps are fits)
    deep = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if frozenset((a, b)) in skip or (_is_wheel_part(a) and _is_wheel_part(b)):
                continue
            ov = _overlap_vol(boxes[a], boxes[b])
            small = min(_vol(boxes[a]), _vol(boxes[b]))
            if small > 0 and ov / small > 0.5 and body not in (a, b):
                deep.append((ov / small, a, b))
    if deep:
        deep.sort(reverse=True)
        out.append(_finding("overlap", "fix", sorted({n for _, a, b in deep[:3] for n in (a, b)}),
                            "mostly inside each other: " + "; ".join(f"{a} / {b}" for _, a, b in deep[:3]),
                            "Separate " + "; ".join(f"{a} and {b}" for _, a, b in deep[:3]) + " so they no longer share most of their volume."))

    # wheels: level, and nothing hanging below them
    wheels = _ground_wheels(names)
    if wheels:
        bottoms = {n: boxes[n][2] for n in wheels}
        ground = min(bottoms.values())
        high = [n for n, z in bottoms.items() if z - ground > tol]
        if high:
            out.append(_finding("wheels", "fix", high, "wheels are not level: " + ", ".join(high[:4]) + " sit higher than the rest",
                                "Put every wheel on the same ground level: " + ", ".join(high[:4]) + " are too high."))
        below = [n for n in names if n not in wheels and boxes[n][2] < ground - tol]
        if below:
            out.append(_finding("wheels", "fix", below, "hangs below the wheels: " + ", ".join(below[:4]),
                                "Raise " + ", ".join(below[:4]) + " so nothing hangs below the wheels, or lower the wheels."))
        bw = boxes[body]
        poke = [n for n in wheels if (bw[1] - boxes[n][1] > 0.35 * (boxes[n][4] - boxes[n][1]))
                or (boxes[n][4] - bw[4] > 0.35 * (boxes[n][4] - boxes[n][1]))]
        if poke:
            out.append(_finding("wheel_poke", "note", poke, "wheels stick far out of the body: " + ", ".join(poke[:4]),
                                "Tuck " + ", ".join(poke[:4]) + " in so no more than a third of each wheel's width is outside the body, "
                                "or add wheel arches."))

    # symmetry: left/right pairs
    cy = (lo[1] + hi[1]) / 2
    pairs: dict[tuple[str, ...], dict[str, str]] = {}
    for n in names:
        sk = _side_key(n)
        if sk:
            pairs.setdefault(sk[0], {})[sk[1]] = n
    for _key, pr in pairs.items():
        if "L" in pr and "R" in pr:
            l, r = boxes[pr["L"]], boxes[pr["R"]]
            size_off = max(abs((l[i + 3] - l[i]) - (r[i + 3] - r[i])) for i in range(3))
            mirror_off = abs(((l[1] + l[4]) / 2 - cy) + ((r[1] + r[4]) / 2 - cy)) + abs(l[0] - r[0]) + abs(l[2] - r[2])
            if size_off > 3 * tol or mirror_off > 3 * tol:
                out.append(_finding("symmetry", "fix", [pr["L"], pr["R"]], f"{pr['L']} and {pr['R']} are not mirror images",
                                    f"Make {pr['L']} and {pr['R']} mirror images about the centre line (same size, same length and height position)."))

    # proportions
    lim = RATIOS.get(kind or "")
    if lim and dims[0] > 0:
        h_l, w_l = dims[2] / dims[0], dims[1] / dims[0]
        bad = []
        if not lim["h/l"][0] <= h_l <= lim["h/l"][1]:
            bad.append(f"height is {h_l:.2f} of length (expected {lim['h/l'][0]} to {lim['h/l'][1]})")
        if not lim["w/l"][0] <= w_l <= lim["w/l"][1]:
            bad.append(f"width is {w_l:.2f} of length (expected {lim['w/l'][0]} to {lim['w/l'][1]})")
        if bad:
            out.append(_finding("proportions", "note", [], "; ".join(bad),
                                "Correct the overall proportions: " + "; ".join(bad) + "."))
    order = {"fix": 0, "note": 1}
    out.sort(key=lambda f: order[f["severity"]])
    return out


# ------------------------------------------------------------------------------ renders
def _env_int(name: str, default: int) -> int:
    import os
    try:
        return max(1000, int(os.environ.get(name, default)))
    except ValueError:
        return default


# Drawing cost grows with triangles: about 5 s per 200k on one core, and the check can render up to 3 times. Over the
# budget, heavy parts are simplified (never swapped for boxes), so every part keeps its real outline.
MAX_RENDER_TRIS = _env_int("NITOCAD_RENDER_TRIS", 150000)


def decimate_mesh(v: Any, t: Any, target: int) -> tuple[Any, Any]:
    """Fewer triangles with the same overall shape: vertices are merged on a grid, the grid is coarsened until the
    triangle count is at most `target`, and triangles that collapse are dropped. No holes appear, fine detail goes."""
    import numpy as np
    v, t = np.asarray(v, float), np.asarray(t, int)
    if len(t) <= target or len(t) == 0:
        return v, t
    lo, hi = v.min(0), v.max(0)
    span = float((hi - lo).max()) or 1.0

    def cluster(cell: float):
        q = np.floor((v - lo) / cell).astype(np.int64)
        k = int(q.max()) + 1
        key = (q[:, 0] * k + q[:, 1]) * k + q[:, 2]
        uniq, inv = np.unique(key, return_inverse=True)
        sums = np.zeros((len(uniq), 3)); np.add.at(sums, inv, v)
        nv = sums / np.bincount(inv, minlength=len(uniq))[:, None]
        nt = inv[t]
        ok = (nt[:, 0] != nt[:, 1]) & (nt[:, 1] != nt[:, 2]) & (nt[:, 0] != nt[:, 2])
        return nv, nt[ok]
    low, high = span / 4096, span                         # fine grid keeps everything, coarse grid keeps nothing
    best = cluster(high)
    for _ in range(14):
        mid = (low + high) / 2
        nv, nt = cluster(mid)
        if len(nt) <= target:
            best, high = (nv, nt), mid
        else:
            low = mid
    return best


def within_triangle_budget(meshes: dict[str, tuple[Any, Any]] | None, budget: int | None = None) -> dict[str, tuple[Any, Any]]:
    """The meshes to draw. Over `budget` in total, every part is simplified in proportion to its size (at least 300
    triangles each), so a gear with 100k triangles does not slow the whole check and no part turns into a box."""
    budget = budget or MAX_RENDER_TRIS
    if not meshes:
        return {}
    counts = {n: len(m[1]) for n, m in meshes.items()}
    total = sum(counts.values())
    if total <= budget:
        return dict(meshes)
    share = budget / total
    out = {}
    for n, (v, t) in meshes.items():
        target = max(300, int(counts[n] * share))
        out[n] = decimate_mesh(v, t, target) if counts[n] > target else (v, t)
    return out


def render_views(boxes: dict[str, tuple[float, ...]], out_dir: str | Path, *, meshes: dict[str, tuple[Any, Any]] | None = None,
                 size: int = 640) -> list[Path]:
    """isometric, side, front and top PNGs. Real triangles when `meshes` is given, otherwise each part's box.
    matplotlib only; returns [] if it is not installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    polys: list[tuple[Any, tuple[float, float, float]]] = []
    meshes = within_triangle_budget(meshes)
    for i, (name, b) in enumerate(sorted(boxes.items())):
        col = plt.cm.tab20(i % 20)[:3]
        if meshes and name in meshes:
            v, t = np.asarray(meshes[name][0], float), np.asarray(meshes[name][1], int)
            polys.append((v[t], col))
        else:
            x0, y0, z0, x1, y1, z1 = b
            c = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float).reshape(2, 2, 2, 3)
            faces = [c[0], c[1], c[:, 0], c[:, 1], c[:, :, 0], c[:, :, 1]]
            polys.append((np.array([f.reshape(4, 3)[[0, 1, 3, 2]] for f in faces]), col))
    allp = np.concatenate([p.reshape(-1, 3) for p, _ in polys])
    mid, half = (allp.min(0) + allp.max(0)) / 2, float((allp.max(0) - allp.min(0)).max()) / 2 or 1.0
    paths = []
    for label, (elev, azim) in {"iso": (22, -55), "side": (0, -90), "front": (0, 0), "top": (90, -90)}.items():
        fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
        ax = fig.add_subplot(111, projection="3d")
        for p, col in polys:
            ax.add_collection3d(Poly3DCollection(p, facecolor=col, edgecolor=(0, 0, 0, 0.25), linewidths=0.3))
        ax.set_xlim(mid[0] - half, mid[0] + half); ax.set_ylim(mid[1] - half, mid[1] + half); ax.set_zlim(mid[2] - half, mid[2] + half)
        ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
        path = out_dir / f"{label}.png"
        fig.savefig(path, facecolor="white")
        plt.close(fig)
        paths.append(path)
    return paths


# ------------------------------------------------------------------------------ the vision pass
_VISION_PROMPT = ("You are checking a CAD model of a {kind}. Four renders are attached: isometric, side, front, top. "
                  "An automatic checklist already found: {known}. Report ONLY visible faults from this list: a part floating "
                  "or detached, a part sunk into another, wheels not on the ground or poking out of the body, left/right "
                  "parts that differ, obviously wrong proportions, a flat box where a shaped part is expected. "
                  "Do not comment on style. Reply with JSON only: "
                  '{{"issues":[{{"parts":["part_name"],"problem":"short","fix":"one sentence instruction"}}]}} '
                  "Use only these part names: {names}. Return at most 4 issues, or an empty list if you see none.")


def parse_vision(text: str, known_names: set[str]) -> list[dict[str, Any]]:
    """Findings from a vision reply. Anything unparseable, or naming parts that do not exist, is dropped."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in (data.get("issues") if isinstance(data, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        parts = [p for p in (it.get("parts") or []) if isinstance(p, str) and p in known_names]
        problem, fix = str(it.get("problem") or "")[:140], str(it.get("fix") or "")[:240]
        if parts and problem and fix:
            out.append(_finding("visual", "fix", parts, problem, fix))
    return out[:4]


# ------------------------------------------------------------------------------ the loop
def _score(findings: list[dict[str, Any]]) -> int:
    return sum(3 if f["severity"] == "fix" else 1 for f in findings)


def inspect_vehicle(vspec: dict[str, Any], *, expand: Callable[[dict[str, Any]], "tuple[list[dict[str, Any]], list[tuple[str, str]]] | None"],
                    bbox_fn: BBoxFn, revise_fn: ReviseFn | None = None, vision_fn: VisionFn | None = None,
                    meshes_fn: Callable[[list[dict[str, Any]]], dict[str, tuple[Any, Any]]] | None = None,
                    work_dir: str | Path | None = None, kind: str | None = None, max_passes: int = MAX_PASSES) -> dict[str, Any]:
    """Check, fix, check again, at most `max_passes` fixes. `expand(vspec)` returns (placed parts, intended contact
    pairs) or None when the spec cannot be placed. Returns {"vspec", "changed", "passes", "findings", "visual",
    "summary"}. Never raises: any failure ends the loop with the best design so far."""
    max_passes = max(0, min(int(max_passes), MAX_PASSES))
    best, best_found = vspec, None
    passes: list[dict[str, Any]] = []
    visual = "skipped: no vision model is connected" if vision_fn is None else "not run"
    changed = False
    try:
        for n in range(max_passes + 1):
            placed = expand(best)
            if placed is None:
                break
            parts, contact = placed
            found = check_geometry(parts, bbox_fn, kind=kind, intended_contact=contact)
            if vision_fn is not None:
                try:
                    boxes = world_boxes(parts, bbox_fn)
                    try:
                        meshes = meshes_fn(parts) if meshes_fn else None
                    except Exception:  # noqa: BLE001 - no real triangles: show each part's box instead
                        meshes = None
                    imgs = render_views(boxes, work_dir or Path("/tmp") / "nitocad_inspect", meshes=meshes)
                    if imgs:
                        known = "; ".join(f["detail"] for f in found) or "nothing"
                        reply = vision_fn(_VISION_PROMPT.format(kind=kind or "vehicle", known=known, names=", ".join(sorted(boxes))),
                                          [p.read_bytes() for p in imgs])
                        found += parse_vision(reply, set(boxes))
                        visual = "done"
                    else:
                        visual = "skipped: renders could not be made"
                except Exception:  # noqa: BLE001 - a vision problem never blocks the checklist
                    visual = "skipped: the vision model failed"
            found = found[:MAX_FINDINGS * 2]
            if best_found is None:
                best_found = found
            passes.append({"pass": n, "findings": [f["detail"] for f in found]})
            fixable = [f for f in found if f["severity"] == "fix"][:MAX_FINDINGS]
            if not fixable or n == max_passes or revise_fn is None:
                best_found = found
                break
            new = revise_fn(best, " ".join(f["instruction"] for f in fixable))
            if not isinstance(new, dict) or new == best:
                best_found = found
                break
            placed2 = expand(new)
            if placed2 is None or _score(check_geometry(placed2[0], bbox_fn, kind=kind, intended_contact=placed2[1])) >= _score(found):
                best_found = found                       # not better: keep what we had
                break
            best, changed = new, True
            best_found = None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("vehicle inspection failed")
    findings = best_found or []
    left = [f for f in findings if f["severity"] == "fix"]
    summary = _summary(len(passes), changed, findings, left, visual)
    return {"vspec": best, "changed": changed, "passes": passes, "findings": findings, "visual": visual, "summary": summary}


def _summary(n_checks: int, changed: bool, findings: list[dict[str, Any]], left: list[dict[str, Any]], visual: str) -> str:
    lead = "Checked the finished model for floating parts, overlaps, level wheels, symmetry and proportions"
    if visual == "done":
        lead += " and looked at four renders"
    lead += "."
    if changed:
        lead += " Fixed what it found and checked again."
    if not findings:
        return lead + " Nothing wrong found."
    items = "; ".join(f["detail"] for f in findings[:3])
    if left:
        return lead + " Still open: " + items + ". Tell me which to fix, or press Generate as it is."
    return lead + " Worth a look: " + items + "."

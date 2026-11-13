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
MAX_OVERLAP_PARTS = 150                            # the exact overlap test samples space; skip it on very large assemblies
OVERLAP_SHARE = 0.15                               # share of the smaller part's bounding volume that counts as a fault
MAX_FINDINGS = 6                                   # per pass: enough to act on, few enough for one instruction
# What touches the ground: the tyre. Axles, bearings, hubs and brake discs named wheel_* are not wheels for the ground
# test. A vehicle with no tyre in any name (a locomotive) uses its parts named wheel, wheel_l_1, wheel_right...
_TYRE = re.compile(r"tyre|tire", re.I)
_BARE_WHEEL = re.compile(r"(?:^|_)wheel(?:_(?:l|r|left|right|f[lr]|r[lr]|front|rear))*(?:_\d+)?$", re.I)
_PAIR = re.compile(r"^(?P<a>.*?)(?P<s>_left|_right|_l|_r)(?P<b>(?:_.*)?)$", re.I)
_CORNER = re.compile(r"_(?P<c>f[lr]|r[lr])(?=_|$)", re.I)             # wheel_fl_tyre, panels_door_rr
_LEFT = {"_left", "_l"}
# (min, max) of height/length and width/length. Only kinds where a wrong ratio is plainly a fault.
RATIOS = {"car": {"h/l": (0.20, 0.45), "w/l": (0.30, 0.55)}, "truck": {"h/l": (0.25, 0.70), "w/l": (0.25, 0.50)}}


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

    # overlap: parts that really share volume. This uses the exact shape models of vehicle_overlap (a shaft through a
    # bearing or a tyre ring is not an overlap; a bounding-box test would call it one). Parts it has no model for are
    # skipped rather than guessed at. A fit is a small share of the smaller part; a fault is a large one.
    deep: list[tuple[float, str, str]] = []
    if len(parts) <= MAX_OVERLAP_PARTS:
        try:
            import vehicle_overlap as VO
            res = VO.find_overlaps(parts, skip_pairs=lambda a, b: frozenset((a, b)) in skip or bool(_TYRE.search(a) and _TYRE.search(b)))
            for r in res["overlaps"]:
                small = min(_vol(boxes[r["a"]]), _vol(boxes[r["b"]]))
                if small > 0 and r["volume_mm3"] / small > OVERLAP_SHARE and body not in (r["a"], r["b"]):
                    deep.append((r["volume_mm3"] / small, r["a"], r["b"]))
        except Exception:  # noqa: BLE001 - no overlap model available: the other checks still run
            deep = []
    if deep:
        deep.sort(reverse=True)
        out.append(_finding("overlap", "fix", sorted({n for _, a, b in deep[:3] for n in (a, b)}),
                            "share a lot of volume: " + "; ".join(f"{a} / {b}" for _, a, b in deep[:3]),
                            "Separate " + "; ".join(f"{a} and {b}" for _, a, b in deep[:3]) + " so they no longer share much volume."))

    # wheels: level, and nothing hanging below them
    wheels = [n for n in names if _TYRE.search(n)] or [n for n in names if _BARE_WHEEL.search(n)]
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
    pairs: dict[tuple[str, str], dict[str, str]] = {}
    for n in names:
        c = _CORNER.search(n)
        if c:                                                  # front-left / front-right, rear-left / rear-right
            if re.search(r"(?:^|_)(?:l|r|left|right)(?:_|$)", n[c.end():], re.I):
                continue                                       # wheel_fl_bearing_l: the _l is the wheel's own side, not the car's
            key = (n[:c.start()].lower() + "|" + c.group("c")[0].lower(), n[c.end():].lower())
            pairs.setdefault(key, {})["L" if c.group("c")[1].lower() == "l" else "R"] = n
            continue
        m = _PAIR.match(n)
        if m:
            pairs.setdefault((m.group("a").lower(), m.group("b").lower()), {})["L" if m.group("s").lower() in _LEFT else "R"] = n
    for (_a, _b), pr in pairs.items():
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
                    work_dir: str | Path | None = None, kind: str | None = None, max_passes: int = MAX_PASSES,
                    on_step: Callable[..., None] | None = None) -> dict[str, Any]:
    """Check, fix, check again, at most `max_passes` fixes. `expand(vspec)` returns (placed parts, intended contact
    pairs) or None when the spec cannot be placed. Returns {"vspec", "changed", "passes", "findings", "visual",
    "summary"}. Never raises: any failure ends the loop with the best design so far."""
    max_passes = max(0, min(int(max_passes), MAX_PASSES))
    say = on_step or (lambda *a, **k: None)
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
            say("Inspecting the finished model", f"check {n + 1}: floating parts, overlaps, wheels, symmetry")
            found = check_geometry(parts, bbox_fn, kind=kind, intended_contact=contact)
            if vision_fn is not None:
                try:
                    boxes = world_boxes(parts, bbox_fn)
                    try:
                        meshes = meshes_fn(parts) if meshes_fn else None
                    except Exception:  # noqa: BLE001 - no real triangles: show each part's box instead
                        meshes = None
                    say("Looking at renders of the model")
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
            say("Fixing what the inspection found", "; ".join(f["detail"] for f in fixable[:2]))
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

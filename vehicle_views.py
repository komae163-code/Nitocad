"""
What the studio shows about a vehicle part, beyond the 3D preview. Pure functions, so they are testable without a
web server or a CAD kernel (the kernel's box function is passed in).

  summary(vspec)                        sizes, groups, design checks, limits (free, no geometry)
  exploded(vspec, parts, bbox_fn, ...)  viewer JSON for frontend/exploded.html (boxes only, schematic)
  build_package(vspec, out_dir, ...)    the whole package via vehicle_build.build_vehicle, then a zip

The exploded view is free. The package is a Generate-level deliverable: vehicle_api only serves it for a
part whose files are unlocked.
"""
from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from typing import Any, Callable

import assembly_exploded as AE
import mates as M
import vehicle_spec as VS

BBoxFn = Callable[[dict[str, Any]], tuple[float, float, float, float, float, float]]


def _label(name: str) -> str:
    return " ".join(w for w in name.replace("_mm", "").split("_") if w)


def summary(vspec: dict[str, Any], module_dir: Path | None = None) -> dict[str, Any]:
    ex = VS.expand_vehicle(vspec, None, None, module_dir)
    if not ex["ok"]:
        return {"ok": False, "errors": ex["errors"][:10]}
    sizes = []
    for k, v in (vspec.get("params") or {}).items():
        d = v.get("default") if isinstance(v, dict) else v
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            sizes.append({"name": k, "label": _label(k), "value": d,
                          "min": v.get("min") if isinstance(v, dict) else None,
                          "max": v.get("max") if isinstance(v, dict) else None})
    return {"ok": True, "name": vspec.get("name"), "notes": vspec.get("notes") or "", "sizes": sizes,
            "parts": len(ex["parts"]), "groups": {g: len(m) for g, m in ex["assemblies"].items()},
            "checks": [{"name": c["name"], "status": c["status"], "detail": c.get("detail") or ""} for c in ex["checks"]],
            "external": [e["name"] for e in ex["external"]], "limits": ex["limits"]}


_UP = re.compile(r"roof|hood|bonnet|canopy|lid|cover|windscreen|windshield|head|spinner|rotor|prop", re.I)
_DOWN = re.compile(r"floor|tank|battery|exhaust|muffler|sump|skid|undertray|pan$|_pan", re.I)
_FRONT = re.compile(r"bumper_f|front_bumper|radiator|grille|nose|headlight|engine", re.I)
_REAR = re.compile(r"bumper_r|rear_bumper|tailgate|trunk|boot|tail_light|exhaust|muffler", re.I)
_SIDE = re.compile(r"door|wheel|tyre|tire|hub|axle|bearing|mirror|panel_[lr]|side|brake|disc|caliper|_[lr]$|_[lr]_", re.I)


def default_direction(name: str, centre: tuple[float, float, float], ref: tuple[float, float, float]) -> tuple[float, float, float] | None:
    """A sensible pull-apart direction for a part that has none: wheels, doors and mirrors go out sideways, the roof
    and hood go up, the floor, tank and exhaust go down, bumpers and the tail go along the length. None when the name
    says nothing and the part sits on the centre (the caller then falls back to radial)."""
    y = centre[1] - ref[1]
    side = (0.0, 1.0 if y >= 0 else -1.0, 0.0)
    if _SIDE.search(name) and abs(y) > 1.0:
        return side
    if _REAR.search(name) and not _DOWN.search(name):
        return (-1.0, 0.0, 0.0)
    if _FRONT.search(name):
        return (1.0, 0.0, 0.0)
    if _UP.search(name):
        return (0.0, 0.0, 1.0)
    if _DOWN.search(name):
        return (0.0, 0.0, -1.0)
    return None


NEST_SCALE_CAP = 3.0


def nest_scales(boxes: dict[str, AE.BBox], dirs: dict[str, tuple[float, float, float]]) -> dict[str, float]:
    """Distance multipliers for parts that overlap another part going the same way. The smaller part of such a pair is
    pulled further than the larger one (a door further than its side panel, an axle further than its tyre), so
    they come apart instead of travelling together. Nests of any depth are handled, largest first."""
    vol = {n: math.prod(b["max"][i] - b["min"][i] for i in range(3)) for n, b in boxes.items()}
    scale = {n: 1.0 for n in dirs}
    order = sorted(dirs, key=lambda n: -vol[n])
    for i, big in enumerate(order):
        for small in order[i + 1:]:
            same = sum(a * b for a, b in zip(AE._unit(dirs[big]) or (0, 0, 0), AE._unit(dirs[small]) or (0, 0, 0))) > 0.99
            a, b = boxes[big], boxes[small]
            touching = all(a["min"][k] < b["max"][k] + 1.0 and b["min"][k] < a["max"][k] + 1.0 for k in range(3))
            if same and touching:                       # overlapping, nested or just touching: all travel together
                scale[small] = min(NEST_SCALE_CAP, max(scale[small], scale[big] + 0.5))
    return scale


def exploded(vspec: dict[str, Any], parts: list[dict[str, Any]], bbox_fn: BBoxFn, *, group: str | None = None,
             module_dir: Path | None = None) -> dict[str, Any]:
    """Viewer JSON for the placed `parts` (the chat part's parts list: name, part_type, parameters, position,
    rotation). `group` limits it to one of the vehicle's assemblies. Returns {"ok", "viewer", "warnings", "groups"}."""
    ex = VS.expand_vehicle(vspec, None, None, module_dir)
    if not ex["ok"]:
        return {"ok": False, "errors": ex["errors"][:10]}
    names_in_group = ex["assemblies"].get(group) if group else None
    if group and names_in_group is None:
        return {"ok": False, "errors": [f"no group called {group!r}"], "groups": list(ex["assemblies"])}
    boxes: dict[str, AE.BBox] = {}
    for p in parts:
        if names_in_group is not None and p["name"] not in names_in_group:
            continue
        loc = bbox_fn(p)
        w = M.world_aabb(loc, tuple(p.get("rotation") or (0, 0, 0)), tuple(p["position"]))
        boxes[p["name"]] = {"min": (w[0], w[1], w[2]), "max": (w[3], w[4], w[5])}
    if len(boxes) < 2:
        return {"ok": False, "errors": ["an exploded view needs at least two parts"], "groups": list(ex["assemblies"])}
    settings = ex.get("explode") or {}
    dirs = {k: v for k, v in (settings.get("directions") or {}).items() if k in boxes}
    lo = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    hi = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    ref = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    for n, b in boxes.items():                       # parts without a set direction get one from their name and side
        if n not in dirs:
            d = default_direction(n, tuple((b["min"][i] + b["max"][i]) / 2 for i in range(3)), ref)
            if d is not None:
                dirs[n] = d
    fixed = ex["fixed"].get(group) if group else None
    res = AE.explode(boxes, fixed=fixed if fixed in boxes else None, directions=dirs, scales=nest_scales(boxes, dirs),
                     intended_contact=[tuple(x) for x in settings.get("intended_contact", []) if len(x) == 2])
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"], "groups": list(ex["assemblies"])}
    warnings = []
    if res["unseparated"]:
        warnings.append("still overlapping: " + ", ".join("/".join(p) for p in res["unseparated"][:6]))
    if res["no_direction"]:
        warnings.append("no direction set for: " + ", ".join(res["no_direction"][:8]))
    title = f"{vspec.get('name', 'vehicle')}" + (f": {group}" if group else "")
    return {"ok": True, "viewer": AE.to_viewer_json(boxes, res, title), "warnings": warnings,
            "groups": list(ex["assemblies"]), "sequence": res.get("sequence", [])}


def zip_dir(src: str | Path, dest: str | Path) -> Path:
    src, dest = Path(src), Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())
    return dest


def build_package(vspec: dict[str, Any], work_dir: str | Path, *, bbox_fn: BBoxFn | None = None,
                  export_fn: Any = None, fetch: bool = True, registry: Any = None) -> dict[str, Any]:
    """Run vehicle_build.build_vehicle into work_dir/package and zip it to work_dir/<name>.zip.
    Returns {"ok", "zip", "validation", "stages", "errors"}. A package built without a CAD kernel says so in its
    own validation report and is never marked PASS."""
    import vehicle_build as VB
    work = Path(work_dir)
    pkg = work / "package"
    res = VB.build_vehicle(vspec, pkg, bbox_fn=bbox_fn, export_fn=export_fn, fetch=fetch, registry=registry,
                           work_dir=work / "scratch")
    if res.get("manifest") is None:
        return {"ok": False, "errors": res.get("errors") or ["the package could not be built"], "stages": res.get("stages", [])}
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(vspec.get("name") or "vehicle"))[:40] or "vehicle"
    z = zip_dir(pkg, work / f"{safe}_package.zip")
    return {"ok": True, "zip": z, "validation": res["validation"], "stages": res["stages"], "errors": []}

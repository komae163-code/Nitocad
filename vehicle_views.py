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
    fixed = ex["fixed"].get(group) if group else None
    res = AE.explode(boxes, fixed=fixed if fixed in boxes else None, directions=dirs,
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

"""
One call from a vehicle spec to a delivered package.

    build_vehicle(spec, out_dir, overrides=..., registry=..., bbox_fn=..., export_fn=...)

Stages, in order. Each reports ok / skipped / failed in the result, and a stage that cannot run says why:

  1 expand     vehicle_spec.expand_vehicle: parameters, repeats, mirrors, mates, design checks, planner validation
  2 externals  fetch every `source` component through asset_providers (licence-gated); failures are reported
  3 mates      solve mates against real part boxes (needs the CAD kernel: pass bbox_fn or have CadQuery installed)
  4 exploded   per assembly: offsets, a schematic SVG and a viewer JSON (boxes only)
  5 export     STEP/STL through `export_fn` (default: CadQuery via assembly.generate_assembly)
  6 package    assembly_package.package_assembly: folders, manifest with hashes, params, BOM, validation report

The network and the CAD kernel are injected, so every stage is testable offline. Without a kernel, stages 3 to 5
are skipped and the package says so; it is then never reported as PASS.

Not verified on real CadQuery or on the live download services. The default kernel adapters below are the least
tested part of this file.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

import assembly_exploded as AE
import assembly_package as PK
import asset_providers as AP
import mates as M
import vehicle_spec as VS

AABB = tuple[float, float, float, float, float, float]       # mates order: xmin, ymin, zmin, xmax, ymax, zmax
BBoxFn = Callable[[dict[str, Any]], AABB]
ExportFn = Callable[[list[dict[str, Any]], Path], list[tuple[str, Path]]]


def _stage(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"stage": name, "status": status, "detail": detail}


def kernel_bbox_fn() -> BBoxFn | None:
    """Local bounding box of a part from the real templates, or None when CadQuery is not installed.
    assembly._template_bbox returns (xmin, xmax, ymin, ymax, zmin, zmax); mates want (xmin, ymin, zmin, ...)."""
    try:
        import assembly                                        # imports cadquery
    except Exception:                                          # noqa: BLE001
        return None

    def fn(part: dict[str, Any]) -> AABB:
        x0, x1, y0, y1, z0, z1 = assembly._template_bbox(part["part_type"], part.get("parameters", {}))
        return (x0, y0, z0, x1, y1, z1)
    return fn


def kernel_export_fn(formats: tuple[str, ...] = ("step", "stl")) -> ExportFn | None:
    """STEP and STL of the whole assembly via CadQuery, or None when it is not installed."""
    try:
        import assembly
        import exporters
    except Exception:                                          # noqa: BLE001
        return None

    def fn(parts: list[dict[str, Any]], out: Path) -> list[tuple[str, Path]]:
        assy = assembly.generate_assembly(parts)
        made: list[tuple[str, Path]] = []
        if "step" in formats:
            p = out / "assembly.step"
            assy.save(str(p), "STEP")                          # keeps named sub-parts (see exporters._as_workplane)
            made.append(("CAD", p))
        if "stl" in formats:
            made.append(("Meshes", exporters.export_stl(assy, out / "assembly.stl")))
        return made
    return fn


# ------------------------------------------------------------------------------------------- externals
def fetch_externals(externals: list[dict[str, Any]], registry: AP.Registry, cache_dir: Path
                    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fetch each external component. Returns (fetched results with a 'part_name', failure checks)."""
    fetched: list[dict[str, Any]] = []
    checks: list[dict[str, str]] = []
    for ext in externals:
        src = ext.get("source") or {}
        nm = ext["name"]
        query = src.get("query") or src.get("id")
        if not isinstance(query, str) or not query.strip():
            checks.append({"name": f"external:{nm}", "status": "fail", "detail": "source needs a 'query' or an 'id'"})
            continue
        kinds = tuple(src["kinds"]) if isinstance(src.get("kinds"), list) else None
        res = registry.search(query, kinds=kinds, limit=100)
        cands = [r for r in res["results"] if not src.get("provider") or r.provider == src["provider"]]
        if src.get("id"):
            cands = [r for r in cands if r.id == src["id"]]
        if not cands:
            why = "; ".join(res["notes"]) or "no match"
            checks.append({"name": f"external:{nm}", "status": "fail",
                           "detail": f"no asset found for {query!r} ({why[:200]})"})
            continue
        rec = cands[0]
        try:
            got = registry.fetch(rec, cache_dir)
        except (AP.LicenceBlocked, AP.FetchError, OSError, ValueError) as e:
            checks.append({"name": f"external:{nm}", "status": "fail", "detail": f"{type(e).__name__}: {str(e)[:200]}"})
            continue
        got["part_name"] = nm
        got["position"] = ext.get("position")
        got["ambiguous"] = len(cands) > 1
        fetched.append(got)
        detail = f"{rec.provider}:{rec.id}"
        if len(cands) > 1:
            detail += f" (first of {len(cands)} matches; set source.id to pin it)"
        checks.append({"name": f"external:{nm}", "status": "pass", "detail": detail})
    return fetched, checks


# ------------------------------------------------------------------------------------------- main
def build_vehicle(spec: Any, out_dir: str | Path, *, overrides: dict[str, Any] | None = None,
                  registry: AP.Registry | None = None, fetch: bool = True,
                  bbox_fn: BBoxFn | None = None, export_fn: ExportFn | None = None,
                  export: bool = True, work_dir: str | Path | None = None) -> dict[str, Any]:
    out = Path(out_dir)
    vname = spec.get("name", "vehicle") if isinstance(spec, dict) else "vehicle"
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="nito_vehicle_"))
    work.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, str]] = []
    checks: list[dict[str, str]] = []
    limits: list[str] = []

    # 1 ---- expand
    ex = VS.expand_vehicle(spec, overrides)
    if not ex["ok"]:
        return {"ok": False, "stages": [_stage("expand", "failed", f"{len(ex['errors'])} error(s)")],
                "errors": ex["errors"], "manifest": None}
    stages.append(_stage("expand", "ok", f"{len(ex['parts'])} parts, {len(ex['external'])} external"))
    parts = [dict(p) for p in ex["parts"]]
    for c in ex["checks"]:
        checks.append({"name": f"design:{c['name']}", "status": {"pass": "pass", "fail": "fail"}.get(c["status"], "fail"),
                       "detail": c["detail"]})

    # 2 ---- externals
    fetched: list[dict[str, Any]] = []
    if ex["external"]:
        if not fetch:
            stages.append(_stage("externals", "skipped", "fetch=False"))
            checks += [{"name": f"external:{e['name']}", "status": "not_run", "detail": "not fetched"} for e in ex["external"]]
        else:
            fetched, ec = fetch_externals(ex["external"], registry or AP.default_registry(), work / "cache")
            checks += ec
            bad = sum(c["status"] == "fail" for c in ec)
            stages.append(_stage("externals", "failed" if bad else "ok", f"{len(fetched)} fetched, {bad} failed"))
    else:
        stages.append(_stage("externals", "skipped", "none in the spec"))

    # 3 ---- mates / world boxes
    bbox = bbox_fn if bbox_fn is not None else kernel_bbox_fn()
    boxes: dict[str, AE.BBox] = {}
    placed_ok = False
    if bbox is None:
        has_mates = any(p.get("mate") for p in parts)
        stages.append(_stage("mates", "skipped", "no CAD kernel (CadQuery) and no bbox_fn given"))
        checks.append({"name": "geometry", "status": "not_run",
                       "detail": "no CAD kernel: " + ("mated parts have no position yet; " if has_mates else "")
                                 + "boxes, exploded view and STEP/STL were not produced"})
        limits.append("No geometry was built in this run.")
    else:
        try:
            parts = M.resolve_mates(parts, lambda p: bbox(p))
            n_m = sum(1 for p in parts if p.get("mate"))
            for p in parts:
                loc = bbox(p)
                w = M.world_aabb(loc, tuple(p.get("rotation", (0, 0, 0))), tuple(p["position"]))
                boxes[p["name"]] = {"min": (w[0], w[1], w[2]), "max": (w[3], w[4], w[5])}
            placed_ok = True
            stages.append(_stage("mates", "ok", f"{n_m} mate(s) solved, {len(boxes)} part boxes"))
            checks.append({"name": "mates_solved", "status": "pass", "detail": f"{n_m} mate(s)"})
        except Exception as e:                                  # noqa: BLE001 - MateError or a kernel error
            stages.append(_stage("mates", "failed", f"{type(e).__name__}: {str(e)[:200]}"))
            checks.append({"name": "mates_solved", "status": "fail", "detail": f"{type(e).__name__}: {str(e)[:200]}"})

    # 4 ---- exploded view per assembly
    artifacts: list[tuple[str, Path]] = []
    if placed_ok:
        settings = ex.get("explode") or {}
        n_views = 0
        for asm, names in ex["assemblies"].items():
            ab = {n: boxes[n] for n in names if n in boxes}
            if len(ab) < 2:
                continue
            dirs = {k: v for k, v in (settings.get("directions") or {}).items() if k in ab}
            res = AE.explode(ab, fixed=ex["fixed"].get(asm) if ex["fixed"].get(asm) in ab else None, directions=dirs,
                             intended_contact=[tuple(x) for x in settings.get("intended_contact", []) if len(x) == 2])
            if not res["ok"]:
                checks.append({"name": f"exploded:{asm}", "status": "fail", "detail": "; ".join(res["errors"])})
                continue
            svg = work / f"exploded_{asm}.svg"
            svg.write_text(AE.to_svg(ab, res, view="front", title=f"{vname}: {asm}"),
                           encoding="utf-8")
            js = work / f"exploded_{asm}.json"
            js.write_text(json.dumps(AE.to_viewer_json(ab, res, f"{vname}: {asm}")), encoding="utf-8")
            artifacts += [("Drawings", svg), ("Assembly", js)]
            n_views += 1
            problems = []
            if res["unseparated"]:
                problems.append("still overlapping: " + ", ".join("/".join(p) for p in res["unseparated"]))
            if res["no_direction"]:
                problems.append("no direction (set explode.directions): " + ", ".join(res["no_direction"]))
            checks.append({"name": f"exploded:{asm}", "status": "fail" if problems else "pass",
                           "detail": "; ".join(problems) or f"{len(ab)} parts, factor {res['factor']}"})
        stages.append(_stage("exploded", "ok" if n_views else "skipped", f"{n_views} view set(s)"))
        limits.append("Exploded views are schematic (bounding boxes); external parts are not in them.")
    else:
        stages.append(_stage("exploded", "skipped", "parts are not placed"))

    # 5 ---- export
    requested: list[str] = []
    if export and placed_ok:
        fn = export_fn if export_fn is not None else kernel_export_fn()
        if fn is None:
            stages.append(_stage("export", "skipped", "no export function and no CAD kernel"))
            checks.append({"name": "export", "status": "not_run", "detail": "STEP/STL not produced"})
        else:
            requested = ["assembly.step", "assembly.stl"]
            try:
                made = fn(parts, work)
                artifacts += made
                stages.append(_stage("export", "ok", ", ".join(Path(p).name for _, p in made)))
            except Exception as e:                              # noqa: BLE001
                stages.append(_stage("export", "failed", f"{type(e).__name__}: {str(e)[:200]}"))
                checks.append({"name": "export", "status": "fail", "detail": f"{type(e).__name__}: {str(e)[:200]}"})
    else:
        stages.append(_stage("export", "skipped", "not requested" if not export else "parts are not placed"))

    # 6 ---- package
    pkg_parts = [dict(p) for p in parts]
    pkg_parts += [AP.as_part(f, f["part_name"]) for f in fetched]
    for f, part in zip(fetched, pkg_parts[len(parts):]):
        if f.get("position"):
            part["position"] = list(f["position"])
    params_out = {"vehicle": vname, "overrides": overrides or {}, "resolved": ex["params"],
                  "assemblies": ex["assemblies"]}
    manifest = PK.package_assembly(
        vname, pkg_parts, out, params=params_out, artifacts=artifacts,
        requested=requested, extra_checks=checks, limits=limits + ex["limits"], sources=fetched)
    stages.append(_stage("package", "ok", manifest["validation"]))
    return {"ok": manifest["validation"] != "FAIL", "validation": manifest["validation"], "stages": stages,
            "errors": [], "manifest": manifest, "positions": {p["name"]: p.get("position") for p in parts}}

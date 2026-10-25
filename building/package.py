"""
One call, one deliverable folder: drawings (SVG, PDF, DXF), schedules, compliance report, optional
preliminary foundation estimate, 3D parts, model, and a manifest that lists every file with its hash and
the status of what was and was not assessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .compliance import evaluate
from .furniture import furniture_parts, furniture_schedule, place_furniture
from .layout import design_building
from .model3d import build_parts
from .schedule import build_schedules, schedule_csv
from .sheet import to_dxf, to_pdf, to_svg
from .sheets import elevation_sheet, plan_sheet, section_defs, section_sheet
from .structure import foundation_parts, preliminary_foundation

GENERATOR = "NitoCAD building kernel v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in s).strip("_")


def generate_package(spec: dict[str, Any], out_dir: str | Path, *, soil_bearing_kpa: float | None = None,
                     founding_depth_mm: float = 900.0, ruleset: dict[str, Any] | None = None, furniture: bool = True,
                     furniture_3d: bool = False, formats: tuple[str, ...] = ("svg", "pdf", "dxf"),
                     step: bool = True) -> dict[str, Any]:
    result = design_building(spec)
    if not result["ok"]:
        return {"ok": False, "errors": result["errors"], "warnings": result["warnings"], "assumptions": result["assumptions"]}
    model = result["model"]
    out = Path(out_dir)
    (out / "drawings").mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    files: list[Path] = []

    items: list[dict[str, Any]] = []
    furn_warn: list[str] = []
    if furniture:
        items, furn_warn = place_furniture(model)

    sheets: list[tuple[str, Any]] = []
    for k in range(len(model["levels"])):
        label = "ground_floor_plan" if k == 0 else f"level_{k}_plan"
        sheets.append((f"A-10{k + 1}_{label}", plan_sheet(model, k, items)))
    for i, side in enumerate(("south", "east", "north", "west")):
        sheets.append((f"A-20{i + 1}_{side}_elevation", elevation_sheet(model, side)))
    for sd in section_defs(model):
        sheets.append((f"A-30{1 if sd['id'] == 'A-A' else 2}_section_{sd['id'].lower()}", section_sheet(model, sd)))

    for name, sh in sheets:
        if "svg" in formats:
            p = out / "drawings" / f"{name}.svg"
            p.write_text(to_svg(sh), encoding="utf-8")
            files.append(p)
        if "pdf" in formats:
            p = out / "drawings" / f"{name}.pdf"
            p.write_bytes(to_pdf(sh))
            files.append(p)
        if "dxf" in formats:
            try:
                p = out / "drawings" / f"{name}.dxf"
                p.write_text(to_dxf(sh), encoding="utf-8")
                files.append(p)
            except ImportError:
                notes.append("DXF skipped: ezdxf is not installed.")
                formats = tuple(f for f in formats if f != "dxf")

    sched = build_schedules(model)
    p = out / "schedule.csv"
    p.write_text(schedule_csv(sched), encoding="utf-8")
    files.append(p)
    (out / "quantities.json").write_text(json.dumps(sched["quantities"], indent=2), encoding="utf-8")
    files.append(out / "quantities.json")
    if items:
        fs = furniture_schedule(items)
        (out / "furniture_schedule.json").write_text(json.dumps(fs, indent=2), encoding="utf-8")
        files.append(out / "furniture_schedule.json")

    compliance = evaluate(model, ruleset)
    (out / "compliance_report.json").write_text(json.dumps(compliance, indent=2), encoding="utf-8")
    files.append(out / "compliance_report.json")

    foundation = preliminary_foundation(model, soil_bearing_kpa, founding_depth_mm)
    (out / "foundation_estimate.json").write_text(json.dumps(foundation, indent=2), encoding="utf-8")
    files.append(out / "foundation_estimate.json")

    parts = build_parts(model) + foundation_parts(model, foundation)
    if furniture_3d:
        parts += furniture_parts(items, model)
    (out / "parts.json").write_text(json.dumps(parts, indent=1), encoding="utf-8")
    (out / "model.json").write_text(json.dumps(model, indent=1, default=str), encoding="utf-8")
    files += [out / "parts.json", out / "model.json"]

    if step:
        try:
            import cadquery as cq

            from feature_builder import build_feature_tree
            shapes = []
            (out / "step").mkdir(exist_ok=True)
            for part in parts:
                body = build_feature_tree(part["parameters"]["features"])
                sp = out / "step" / f"{part['name']}.step"
                cq.exporters.export(body, str(sp))
                files.append(sp)
                shapes.append(body.val())
            bp = out / "step" / "building.step"
            cq.exporters.export(cq.Workplane("XY").newObject([cq.Compound.makeCompound(shapes)]), str(bp))
            files.append(bp)
        except ImportError:
            notes.append("STEP skipped: CadQuery is not installed.")

    manifest = {
        "generator": GENERATOR, "project": model["spec"]["name"], "status": "CONCEPT DESIGN, NOT FOR CONSTRUCTION",
        "assumptions": result["assumptions"], "warnings": result["warnings"] + furn_warn, "notes": notes,
        "compliance": {"overall": compliance["overall"], "building_code_compliance": compliance["building_code_compliance"],
                       "counts": compliance["counts"], "statement": compliance["statement"],
                       "unassessed_topics": sorted(compliance["unassessed_topics"])},
        "foundation": {"status": foundation["status"], "engineer_review_required": True},
        "files": [{"path": str(f.relative_to(out)), "bytes": f.stat().st_size, "sha256": _sha(f)} for f in files],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"ok": True, "out_dir": str(out), "manifest": manifest, "model": model, "furniture": items, "compliance": compliance,
            "foundation": foundation, "parts": parts}

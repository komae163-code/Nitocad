"""Needs CadQuery (a server or computer, not Termux). Builds the same vehicle without and with two downloaded STEP
parts, then reads both STEP files back and compares them.  Run:  python3 try_merge.py"""
import json
import shutil
from pathlib import Path

import cadquery as cq

import vehicle_build as VB


def spec_with(externals):
    spec = json.loads(Path("vehicles/examples/loco_0_4_0_core.json").read_text())
    spec["components"] = [c for c in spec["components"] if c["id"] != "coupling_pin"] + externals
    return spec


EXTERNALS = [
    {"id": "bearing_1", "assembly": "loco", "position": [0, 0, 20],
     "source": {"provider": "freecad_library", "query": "ball bearing"}},
    {"id": "nut_1", "assembly": "loco", "position": [40, 0, 20],
     "source": {"provider": "freecad_library", "query": "nut"}},
]


def build(name, externals):
    out = Path(name)
    shutil.rmtree(out, ignore_errors=True)
    r = VB.build_vehicle(spec_with(externals), out, work_dir=Path(name + "_work"))
    step = next(out.rglob("assembly.step"), None)
    solids = len(cq.importers.importStep(str(step)).solids().vals()) if step else 0
    checks = json.loads((out / "Validation" / "validation_report.json").read_text())["checks"]
    return r, solids, [c for c in checks if c["name"].startswith(("external_in_step", "file:assembly"))]


if __name__ == "__main__":
    _, base_solids, _ = build("merge_without", [])
    r, with_solids, checks = build("merge_with", EXTERNALS)
    print("solids without externals:", base_solids)
    print("solids with externals:   ", with_solids)
    for c in checks:
        print(" check", c["name"], c["status"], c["detail"][:100])
    print("RESULT:", "merged" if with_solids >= base_solids + len(EXTERNALS) else "NOT merged, see the checks above")

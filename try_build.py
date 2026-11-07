"""End to end: a vehicle spec with two external parts, built into a package. Uses the real network unless a
registry is passed in. Run:  python3 try_build.py"""
import json
import shutil
from pathlib import Path

import vehicle_build as VB


def run(registry=None, out="e2e_out"):
    spec = json.loads(Path("vehicles/examples/loco_0_4_0_core.json").read_text())
    spec["components"] = [c for c in spec["components"] if c["id"] != "coupling_pin"] + [
        {"id": "bearing_1", "assembly": "loco", "position": [0, 0, 20],
         "source": {"provider": "freecad_library", "query": "ball bearing"}},
        {"id": "cargo_box", "assembly": "loco", "position": [0, 0, 60],
         "source": {"provider": "nvidia_simready", "query": "cardboard box"}},
    ]
    shutil.rmtree(out, ignore_errors=True)
    r = VB.build_vehicle(spec, out, registry=registry, work_dir=Path(out + "_work"))
    print("overall:", r["validation"])
    for s in r["stages"]:
        print(" stage", s["stage"], s["status"], s.get("detail", ""))
    for p in sorted(Path(out).rglob("*")):
        if p.is_file() and ("External" in p.parts or p.name in ("NOTICES.txt", "sources.json")):
            print(" ", p.relative_to(out), p.stat().st_size)
    rep = json.loads((Path(out) / "Validation" / "validation_report.json").read_text())
    for c in rep["checks"]:
        if c["name"].startswith(("external", "file:")):
            print(" check", c["name"], c["status"], c["detail"][:90])
    return r


if __name__ == "__main__":
    run()

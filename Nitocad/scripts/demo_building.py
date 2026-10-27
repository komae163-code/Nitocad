#!/usr/bin/env python3
"""
Generate a complete building package from a description or a spec.

    python scripts/demo_building.py --text "two-storey house with three bedrooms, living room, dining, kitchen, two bathrooms"
    python scripts/demo_building.py my_house.json --out out/my_house --soil 150
    python scripts/demo_building.py                       # built-in two-storey example

Writes drawings/ (plans, 4 elevations, 2 sections as SVG, PDF and DXF at a true standard scale), schedule.csv,
furniture_schedule.json, compliance_report.json, foundation_estimate.json (UNKNOWN unless --soil is given),
parts.json, model.json, step/ (when CadQuery is installed) and manifest.json.

--soil KPA   allowable soil bearing capacity from a geotechnical report. Without it no foundation is estimated.
--ruleset F  a jurisdiction rule set (JSON, see building/compliance.py). Without it only rules of thumb are checked.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from building.compliance import load_ruleset  # noqa: E402
from building.intake import intake  # noqa: E402
from building.package import generate_package  # noqa: E402

EXAMPLE = {"name": "Two-storey house", "width_mm": 13000, "depth_mm": 10000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "dining"}, {"type": "kitchen"},
               {"type": "bedroom", "name": "Guest bedroom"}, {"type": "toilet"}, {"type": "store"}]},
    {"rooms": [{"type": "master_bedroom"}, {"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", help="JSON spec file")
    ap.add_argument("--text", help="describe the building in words")
    ap.add_argument("--out", default="building_out")
    ap.add_argument("--soil", type=float, default=None, help="allowable soil bearing capacity, kN/m2")
    ap.add_argument("--ruleset", default=None, help="jurisdiction rule set JSON")
    ap.add_argument("--no-furniture", action="store_true")
    ap.add_argument("--furniture-3d", action="store_true")
    args = ap.parse_args()

    if args.text:
        proposal = intake(args.text)
        print("PROPOSAL (not confirmed):")
        for a in proposal["assumptions"]:
            print("  assumed:", a)
        for q in proposal["questions"]:
            print("  question:", q)
        if not proposal["design_ok"]:
            print("The proposal does not lay out:", *proposal["errors"], sep="\n - ")
            return 1
        spec = proposal["spec"]
    else:
        spec = json.loads(Path(args.spec).read_text()) if args.spec else EXAMPLE

    rules = None
    if args.ruleset:
        rules, errs = load_ruleset(args.ruleset)
        if errs:
            print("Rule set rejected:", *errs, sep="\n - ")
            return 2
    res = generate_package(spec, args.out, soil_bearing_kpa=args.soil, ruleset=rules,
                           furniture=not args.no_furniture, furniture_3d=args.furniture_3d)
    if not res["ok"]:
        print("Cannot lay out the building:", *res["errors"], sep="\n - ")
        return 1
    m = res["manifest"]
    print(f"\nwrote {len(m['files'])} files to {args.out}/")
    for n in m["notes"]:
        print("note:", n)
    for w in m["warnings"]:
        print("warning:", w)
    print("compliance:", m["compliance"]["statement"])
    print("foundation:", m["foundation"]["status"], "(engineer review required)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

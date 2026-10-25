#!/usr/bin/env python3
"""
Acceptance gate for the automotive domain pack. Run on a machine with the
project's real dependencies (CadQuery/OCCT):

    python scripts/verify_automotive.py            # full run
    python scripts/verify_automotive.py --quick    # fewer random cases

For every part AND for the assembled wheel corner it checks, on REAL OCCT geometry:
  * defaults and named variants build, are valid, and are ONE connected solid
  * volume is positive; bounding box matches the expected envelope
  * STEP export -> re-import round-trips to valid solid(s); STL is non-empty
  * N random parameter sets that pass the validator also build and are valid
  * the wheel corner module (arm + knuckle + hub + rotor) resolves, builds,
    has no solid overlaps, touches where it should, exports to STEP, and the
    deliberately-colliding variant IS caught
  * build time per part (flags anything over --slow seconds)

Writes verify_automotive_report.json and exits non-zero on any failure, so it
can gate CI/deploy. Paste the printed FAILURES section back if anything fails.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cadquery as cq  # noqa: E402

import domain_packs  # noqa: E402
from cad_templates import TEMPLATES  # noqa: E402
from validator import validate_parameters  # noqa: E402

# (part, label, params, expected bbox (x, y, z) or None, tolerance mm)
VARIANTS = [
    ("brake_rotor", "defaults", {}, (300, None, 40), 0.5),
    ("brake_rotor", "solid", {"vented": False}, (300, None, 40), 0.5),
    ("brake_rotor", "330mm 6-stud", {"outer_diameter_mm": 330, "thickness_mm": 32, "vane_count": 48,
                                     "bolt_count": 6, "bolt_pcd_mm": 139.7, "hat_diameter_mm": 170}, (330, None, 40), 0.5),
    ("wheel_hub", "defaults", {}, (140, None, 64), 0.5),
    ("wheel_hub", "lobed", {"lobed": True}, None, 0),
    ("wheel_hub", "no barrel/pilot", {"barrel_length_mm": 0, "pilot_height_mm": 0}, None, 0),
    ("piston", "defaults", {}, (86, None, 62), 0.5),
    ("piston", "dish", {"crown_style": "dish"}, (86, None, 62), 0.5),
    ("piston", "no rings", {"ring_groove_count": 0}, None, 0),
    ("piston", "92mm", {"ring_groove_count": 2, "diameter_mm": 92, "height_mm": 70, "compression_height_mm": 36}, (92, None, 70), 0.5),
    ("flywheel", "defaults", {}, (300, None, 37), 0.5),
    ("flywheel", "132 teeth", {"ring_gear_teeth": 132}, None, 0),
    ("flywheel", "lightening", {"lightening_hole_count": 6, "clutch_bolt_count": 0}, None, 0),
    ("control_arm", "defaults", {}, (285, 427, 38), 1.0),
    ("control_arm", "braced", {"cross_brace": True, "link_hole_diameter_mm": 0}, None, 0),
    ("steering_knuckle", "defaults", {}, (120, 170, 62), 0.5),
    ("steering_knuckle", "offset seat", {"ball_joint_x_mm": 20.0, "leg_width_mm": 60.0}, None, 0),
    ("ball_joint", "defaults", {}, (42, 42, 62), 0.5),
    ("subframe", "defaults", {}, (700, 760, 110), 0.5),
    ("subframe", "wide", {"pivot_y_mm": 420.0, "length_mm": 900.0}, None, 0),
    ("battery_bracket", "defaults", {}, (60, 120, 80), 0.5),
    ("battery_bracket", "slotted", {"slotted": True}, None, 0),
    ("battery_bracket", "sharp bend", {"bend_radius_mm": 0, "gusset_count": 0}, None, 0),
    ("battery_bracket", "3 gussets/holes", {"gusset_count": 3, "upright_hole_count": 3, "base_hole_count": 3, "width_mm": 120}, None, 0),
]


def check_solid(wp, expect_single=True):
    solids = wp.solids().vals()
    problems = []
    if not solids:
        return ["no solids"], {}
    if expect_single and len(solids) != 1:
        problems.append(f"{len(solids)} disconnected solids")
    if not all(s.isValid() for s in solids):
        problems.append("OCCT isValid() is False")
    vol = sum(s.Volume() for s in solids)
    if vol <= 0:
        problems.append(f"non-positive volume {vol}")
    return problems, {"solids": len(solids), "volume_mm3": round(vol, 1)}


def check_export(wp, tmp: Path, tag: str):
    problems = []
    step, stl = tmp / f"{tag}.step", tmp / f"{tag}.stl"
    try:
        cq.exporters.export(wp, str(step))
        cq.exporters.export(wp, str(stl))
        if step.stat().st_size < 1000:
            problems.append("STEP suspiciously small")
        if stl.stat().st_size < 1000:
            problems.append("STL suspiciously small")
        back = cq.importers.importStep(str(step))
        solids = back.solids().vals()
        if not solids or not all(s.isValid() for s in solids):
            problems.append("STEP re-import produced no/invalid solids")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"export/import raised {exc!r}")
    return problems


def rnd_params(spec, rng):
    out = {}
    for k, p in spec.items():
        if rng.random() > 0.6:
            continue
        if p.kind == "bool":
            out[k] = rng.random() < 0.5
        elif p.kind == "choice":
            out[k] = rng.choice(p.choices)
        else:
            lo = p.lo if p.lo is not None else 0.0
            hi = p.hi if p.hi is not None else (p.default * 3 if p.default else 100.0)
            v = p.default * rng.uniform(0.7, 1.4) if (p.default is not None and rng.random() < 0.7) else rng.uniform(lo, hi)
            v = min(max(v, lo), hi)
            out[k] = int(round(v)) if p.kind == "int" else round(v, 2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--random", type=int, default=None, help="random cases per part")
    ap.add_argument("--slow", type=float, default=30.0, help="flag builds slower than this (s)")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    n_random = args.random if args.random is not None else (8 if args.quick else 40)

    tmp = Path(tempfile.mkdtemp(prefix="verify_automotive_"))
    report, failures = [], []

    def record(part, label, ok, problems, info, secs):
        report.append({"part": part, "case": label, "ok": ok, "problems": problems, "seconds": round(secs, 2), **info})
        flag = "OK  " if ok else "FAIL"
        slow = "  SLOW" if secs > args.slow else ""
        print(f"{flag} {part:16s} {label:28s} {secs:6.2f}s {info.get('volume_mm3', '')}{slow}")
        if not ok:
            failures.append((part, label, problems))

    print(f"== named variants ({len(VARIANTS)})")
    for part, label, params, bbox, tol in VARIANTS:
        t0 = time.time()
        try:
            wp = TEMPLATES[part](params)
            problems, info = check_solid(wp)
            if bbox:
                b = wp.val().BoundingBox()
                for axis, want, got in zip("xyz", bbox, (b.xlen, b.ylen, b.zlen)):
                    if want is not None and abs(got - want) > tol:
                        problems.append(f"bbox {axis}: expected {want} +/-{tol}, got {got:.2f}")
            problems += check_export(wp, tmp, f"{part}_{label.replace(' ', '_').replace('/', '_')}")
        except Exception as exc:  # noqa: BLE001
            problems, info = [f"raised {exc!r}", traceback.format_exc(limit=2)], {}
        record(part, label, not problems, problems, info, time.time() - t0)

    print(f"\n== random validator-approved parameter sets ({n_random}/part)")
    rng = random.Random(args.seed)
    specs = {}
    for pack in domain_packs.get_packs():
        specs.update(pack.param_specs)
    for part, spec in specs.items():
        built = rejected = 0
        worst = 0.0
        attempts = 0
        while built < n_random and attempts < n_random * 40:
            attempts += 1
            params = rnd_params(spec, rng)
            fixed, res = validate_parameters(part, params)
            if not res.is_valid:
                rejected += 1
                continue
            t0 = time.time()
            try:
                wp = TEMPLATES[part](fixed)
                problems, info = check_solid(wp)
            except Exception as exc:  # noqa: BLE001
                problems, info = [f"raised {exc!r}"], {}
            secs = time.time() - t0
            worst = max(worst, secs)
            built += 1
            if problems:
                record(part, "random " + json.dumps(params, sort_keys=True)[:90], False, problems, info, secs)
        print(f"     {part:16s} built {built}, validator-rejected {rejected}, slowest {worst:.1f}s")

    # ---- assemblies -------------------------------------------------------------
    print("\n== wheel corner module (assembly)")
    import assembly as asm
    import assembly_spec as AS
    import assembly_templates as AT

    def corner(**per_part):
        spec = AT.corner_module()
        for p in spec["parts"]:
            p["parameters"].update(per_part.get(p["id"], {}))
        return spec

    corner_cases = [
        ("defaults", corner(), True),
        ("lobed hub", corner(hub={"lobed": True}), True),
        ("solid 330mm rotor", corner(rotor={"vented": False, "outer_diameter_mm": 330}), True),
        ("450mm arm", corner(arm={"arm_length_mm": 450}), True),
        ("rotor hat 60mm (must COLLIDE)", corner(rotor={"hat_offset_mm": 60.0}), False),
        ("6-stud hub+rotor", corner(hub={"bolt_count": 6, "bolt_pcd_mm": 139.7, "flange_diameter_mm": 170},
                                    rotor={"bolt_count": 6, "bolt_pcd_mm": 139.7, "hat_diameter_mm": 190}), True),
    ]
    for label, spec, should_pass in corner_cases:
        t0 = time.time()
        problems, info = [], {}
        try:
            resolved = AS.resolve_assembly(spec)
            if not resolved.ok:
                problems.append("interface errors: " + "; ".join(f.message for f in resolved.errors))
            else:
                built = asm.build_parts(resolved)
                fit = asm.check_interference(resolved, built)
                errs = [f for f in fit if f.severity == "error"]
                inconclusive = [f for f in fit if f.code == "interference_inconclusive"]
                if inconclusive:
                    problems.append("boolean inconclusive: " + "; ".join(f.message for f in inconclusive))
                if should_pass and errs:
                    problems += [f.message for f in errs]
                if not should_pass and not errs:
                    problems.append("expected a collision but none was detected")
                if should_pass and not errs:
                    assy = asm.assemble_resolved(resolved, built)
                    step = tmp / f"corner_{label.replace(' ', '_').replace('/', '_')}.step"
                    assy.save(str(step))
                    back = cq.importers.importStep(str(step))
                    if not back.solids().vals():
                        problems.append("assembly STEP re-import has no solids")
                    info["step_kb"] = round(step.stat().st_size / 1024)
        except Exception as exc:  # noqa: BLE001
            problems = [f"raised {exc!r}", traceback.format_exc(limit=3)]
        record("assembly", label, not problems, problems, info, time.time() - t0)

    # ---- analytic envelope vs real solid -----------------------------------------
    print("\n== analytic envelope volume vs OCCT volume (the measurement model's own check)")
    import domain_packs
    import envelope as E
    for part, params, step, tol in [("brake_rotor", {"vented": False}, 5.0, 0.06), ("wheel_hub", {}, 3.0, 0.06),
                                    ("steering_knuckle", {}, 3.0, 0.06), ("ball_joint", {}, 1.5, 0.06),
                                    ("control_arm", {}, 3.0, 0.06), ("subframe", {}, 8.0, 0.10)]:
        t0 = time.time()
        try:
            solid = TEMPLATES[part](params).val().Volume()
            est = E.volume(domain_packs.part_envelope(part, params), step)
            dev = (est - solid) / solid
            problems = [] if abs(dev) <= tol else [f"envelope {est:,.0f} vs solid {solid:,.0f} mm3 ({dev:+.1%}, limit {tol:.0%})"]
            info = {"envelope_deviation": f"{dev:+.1%}"}
        except Exception as exc:  # noqa: BLE001
            problems, info = [f"raised {exc!r}"], {}
        record("envelope", part, not problems, problems, info, time.time() - t0)

    out = Path("verify_automotive_report.json")
    out.write_text(json.dumps(report, indent=2))
    print(f"\nreport: {out.resolve()}")
    if failures:
        print(f"\n== FAILURES ({len(failures)}) - paste this section back")
        for part, label, problems in failures:
            print(f"- {part} / {label}")
            for p in problems:
                print(f"    {p}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

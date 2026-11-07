"""
Writes the piston engine modules (vehicles/modules/piston_engine_{1,2,4}.json) and one example vehicle for each
(vehicles/examples/mini_engine_*.json). Run from the repository root:  python tools/make_engine_modules.py

Why a script: the cylinders sit at different crank angles, and a spec can only repeat a component with a fixed step, so each
cylinder is written out. The script keeps the three modules identical apart from their cylinder count.

Layout (module coordinates, millimetres). The crankshaft lies along +X, starting at x = 0 (its front nose is at x < 0).
The cylinders stand along +Z above it, and the first cylinder is nearest the nose. The crank template builds along its own
+Z, so it is turned with rotation [0, 90, 0]. That sends a rod journal at crank angle a to
(y, z) = (r sin a, -r cos a), so a = 0 is bottom dead centre and a = 180 is top dead centre.

Each rod is placed from its small end (the piston pin). With the journal at (jy, jz) and the rod's centre distance L, the pin
is on the cylinder axis at height pz = jz + sqrt(L^2 - jy^2) and the rod leans by atan2(jy, sqrt(L^2 - jy^2)) about X, so
its big end lands on the journal. The rod template lies in its local XY plane with the big end on +x; rotation
[lean, 90, 0] turns that plane into Y-Z.

Part origins this relies on (read from cad_templates): shaft, tube and spacer are built from z = 0 upwards, flat_plate is a
centred box, connecting_rod has its small end at the origin with the big end on +x and its thickness on +z, and the
crankshaft's first main journal starts at z = 0 with the nose at negative z.

Simplifications, on purpose: pistons are solid slugs and the pin is not drilled through them, there is no crankcase, no
valves or camshaft, and the flywheel is a plain ring. This is the moving core of an engine, drawn to be looked at.
"""
from __future__ import annotations

import json
from pathlib import Path

PHASES = {1: [0], 2: [0, 180], 4: [0, 180, 180, 0]}
NAMES = {1: "single cylinder", 2: "inline two cylinder", 4: "inline four cylinder"}


def _p(default, lo, hi):
    return {"default": default, "min": lo, "max": hi}


def build_module(n: int) -> dict:
    phases = PHASES[n]
    params = {
        "bore": _p(30, 8, 120), "stroke": _p(30, 6, 120), "rod_cd": _p(56, 14, 300),
        "main_dia": _p(16, 4, 60), "rodj_dia": _p(14, 4, 55), "web": _p(5, 2, 20),
        "main_len": _p(20, 3, 60), "rodj_len": _p(10, 3, 40), "piston_h": _p(30, 6, 100),
        "liner_wall": _p(3, 1, 10), "crank_deg": _p(0, 0, 360),
    }
    d: dict[str, str] = {
        "r": "=stroke/2",
        "pitch": "=2*web + rodj_len + main_len",
        "crank_len": f"=main_len + {n}*(2*web + rodj_len + main_len)",
        "nose_len": "=main_dia*0.75", "flange_len": "=main_dia*0.4",
        "rod_t": "=rodj_len - 1", "pin_dia": "=bore*0.25", "pin_len": "=bore*0.85",
        "cw_r": "=main_dia/2 + stroke*0.15", "cw_off": "=stroke*0.3",
        "liner_od": "=bore + 2*liner_wall",
        "liner_z": "=rod_cd - r - 0.55*piston_h - 1",
        "liner_len": "=2*r + piston_h + 4",
        "head_t": "=6", "fly_t": "=main_dia*0.5",
        "x0": "=main_len + web + rodj_len/2",
    }
    for k, ph in enumerate(phases, start=1):
        d[f"xc{k}"] = f"=x0 + {k - 1}*pitch"
        d[f"ph{k}"] = f"={ph} + crank_deg"
        d[f"jy{k}"] = f"=r*sin(radians(ph{k}))"
        d[f"jz{k}"] = f"=-r*cos(radians(ph{k}))"
        d[f"dz{k}"] = f"=sqrt(rod_cd*rod_cd - jy{k}*jy{k})"
        d[f"pz{k}"] = f"=jz{k} + dz{k}"
        d[f"lean{k}"] = f"=degrees(atan2(jy{k}, dz{k}))"
    comps: list[dict] = [
        {"id": "crank", "type": "crankshaft", "material": "steel", "position": [0, 0, 0], "rotation": [0, 90, 0],
         "parameters": {"num_throws": n, "stroke_mm": "=stroke", "main_journal_diameter_mm": "=main_dia",
                        "main_journal_length_mm": "=main_len", "rod_journal_diameter_mm": "=rodj_dia",
                        "rod_journal_length_mm": "=rodj_len", "web_thickness_mm": "=web",
                        "counterweight_radius_mm": "=cw_r", "counterweight_offset_mm": "=cw_off",
                        "phase_angles_deg": [f"=ph{k}" for k in range(1, n + 1)],
                        "nose_length_mm": "=nose_len", "nose_diameter_mm": "=main_dia*0.7",
                        "flange_length_mm": "=flange_len", "flange_diameter_mm": "=main_dia*1.3"}},
        {"id": "flywheel", "type": "spacer", "material": "steel", "position": ["=crank_len", 0, 0], "rotation": [0, 90, 0],
         "parameters": {"outer_diameter_mm": "=stroke*2", "inner_diameter_mm": "=main_dia*0.5", "length_mm": "=fly_t"}},
    ]
    for k in range(1, n + 1):
        comps += [
            {"id": f"rod_{k}", "type": "connecting_rod", "material": "steel",
             "position": [f"=xc{k} - rod_t/2", 0, f"=pz{k}"], "rotation": [f"=lean{k}", 90, 0],
             "parameters": {"big_end_diameter_mm": "=rodj_dia + 0.2", "big_end_boss_diameter_mm": "=rodj_dia + 8",
                            "small_end_diameter_mm": "=pin_dia", "small_end_boss_diameter_mm": "=pin_dia + 6",
                            "center_distance_mm": "=rod_cd", "shank_width_mm": "=rodj_dia*0.6", "thickness_mm": "=rod_t",
                            "rib_height_mm": 0, "cap_bolt_diameter_mm": 0, "fillet_mm": 1.5}},
            {"id": f"piston_{k}", "type": "shaft", "material": "aluminium", "position": [f"=xc{k}", 0, f"=pz{k} - 0.55*piston_h"],
             "parameters": {"diameter_mm": "=bore - 0.3", "length_mm": "=piston_h"}},
            {"id": f"pin_{k}", "type": "shaft", "material": "steel", "position": [f"=xc{k} - pin_len/2", 0, f"=pz{k}"],
             "rotation": [0, 90, 0], "parameters": {"diameter_mm": "=pin_dia", "length_mm": "=pin_len"}},
            {"id": f"liner_{k}", "type": "tube", "material": "steel", "position": [f"=xc{k}", 0, "=liner_z"],
             "parameters": {"outer_diameter_mm": "=liner_od", "wall_thickness_mm": "=liner_wall", "length_mm": "=liner_len"}},
        ]
    comps.append({"id": "head", "type": "flat_plate", "material": "aluminium",
                  "position": [f"=x0 + {n - 1}*pitch/2", 0, "=liner_z + liner_len + head_t/2"],
                  "parameters": {"length_mm": f"={n - 1}*pitch + liner_od + 6", "width_mm": "=liner_od + 6",
                                 "thickness_mm": "=head_t"}})
    checks = [
        {"name": "rod_long_enough", "expr": "=rod_cd >= 1.5*stroke", "message": "a rod shorter than 1.5 strokes leans too hard on the cylinder wall"},
        {"name": "big_end_passes_bore", "expr": "=rodj_dia + 8 <= bore - 2", "message": "the rod's big end is wider than the cylinder"},
        {"name": "pin_shorter_than_bore", "expr": "=pin_len < bore - 2", "message": "the piston pin is longer than the piston"},
        {"name": "counterweight_clears_piston", "expr": "=cw_off + cw_r < rod_cd - r - 0.55*piston_h - 1",
         "message": "the crank counterweight would hit the piston at the bottom of its stroke"},
        {"name": "rod_journal_fits_stroke", "expr": "=rodj_dia/2 + main_dia/2 < stroke", "message": "the rod journal overlaps the main journal"},
        {"name": "piston_not_shorter_than_bore", "expr": "=piston_h >= bore", "message": "a piston shorter than its bore rocks in the cylinder"},
    ]
    if n > 1:
        checks.append({"name": "cylinders_do_not_overlap", "expr": "=pitch >= liner_od + 2",
                       "message": "the cylinders are closer together than their walls; lengthen main_len or shrink the bore"})
    return {"description": (f"Small {NAMES[n]} piston engine core: crankshaft with flywheel, connecting rods, pistons, pins, "
                            f"cylinder liners and a head plate. Crankshaft along +X from x = 0, cylinders upright along +Z, first "
                            f"cylinder nearest the nose. crank_deg sets the pose (0 = first piston at the bottom of its stroke). "
                            "No crankcase, valves or camshaft."),
            "params": params, "derived": d, "components": comps, "checks": checks}


def build_example(n: int) -> dict:
    return {
        "name": {1: "mini_engine_single_cylinder", 2: "mini_engine_inline_two", 4: "mini_engine_inline_four"}[n],
        "notes": (f"A small {NAMES[n]} piston engine, shown as its moving core: crankshaft and flywheel, rods, pistons and pins, "
                  "then the cylinder liners and head. Pistons are solid and there is no crankcase, valves or camshaft. "
                  "Change bore, stroke or crank_deg."),
        "params": {"bore": _p(30, 8, 120), "stroke": _p(30, 6, 120), "crank_deg": _p(0, 0, 360)},
        "components": [{"id": "engine", "module": f"piston_engine_{n}", "assembly": "engine", "position": [0, 0, 0],
                        "params": {"bore": "=bore", "stroke": "=stroke", "crank_deg": "=crank_deg",
                                   "rod_cd": "=stroke*1.9", "piston_h": "=bore", "main_dia": "=stroke*0.55",
                                   "rodj_dia": "=stroke*0.47", "web": "=stroke*0.17", "rodj_len": "=stroke*0.33",
                                   "main_len": "=bore*0.67"}}],
        "dev_notes": "Part origins are read from the template source and are not yet confirmed on real CadQuery.",
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    for n in PHASES:
        (root / "vehicles" / "modules" / f"piston_engine_{n}.json").write_text(json.dumps(build_module(n), indent=1) + "\n")
        ex = build_example(n)
        (root / "vehicles" / "examples" / f"{ex['name']}.json").write_text(json.dumps(ex, indent=1) + "\n")
        print("wrote engine", n)


if __name__ == "__main__":
    main()

"""
OUT OF DATE: this script no longer produces the shipped vehicles/examples/car.json. That file has since grown to 45
components with a gearbox, driveline, explode settings and spelled-out part names (exterior_door_front_left,
wheel_front_left_tyre, half_shaft_left); this script still writes the older 33-component car. It now refuses to
overwrite car.json unless run with --force, and writes to car_legacy.json otherwise. Edit vehicles/examples/car.json
directly until the generator is brought up to date.

Writes a hatchback built from panels, not one solid. Run from the repository root:
    python tools/make_car_example.py            (writes vehicles/examples/car_legacy.json)

Why panels: the old example was one body solid with wheel arches cut in, plus an exhaust. It could not be pulled apart, a solid
block of that size weighs tonnes, and the planner showed it to the model as a proven example, so new cars copied the mistake.

Layout (mm). Front is +X, Y is lateral (+Y right), Z up, wheels on the ground at z = 0. Sheet metal is `wall` thick.
  body       floor, two side panels (with wheel arches and door openings), front and rear end panels, nose and tail
             panels, hood, deck, roof, windscreen, rear window, two side glasses
  doors      two doors that fill the side-panel openings
  interior   two front seats and a rear bench (cushion and back each), fuel tank
  wheels     four wheel_on_axle modules (tyre, axle, two bearings each)
  powertrain inline four engine (piston_engine_4 module), then a gearbox ahead of it: input shaft, countershaft, two gear
             pairs with keyways and backlash, four bearings and a hollow case. The engine's flywheel faces forward, as in
             a front-wheel-drive car with the gearbox in front of the engine.
  driveline  the gearbox countershaft runs back at wheel-centre height to a hollow differential housing on the front axle,
             and a half shaft runs out to each wheel
  exhaust    pipe, muffler, tail pipe
Not modelled: the crown wheel and pinion and CV joints inside and at the ends of the driveline, suspension, steering,
pedals, lights, any electrical parts.
Source-read only: no CAD kernel was available. The sign of the XZ and YZ offsets follows feature_tree.py (XZ extrudes
toward -Y, YZ toward +X). Generate it once and look at it before trusting the panel positions.
"""
from __future__ import annotations

import json
from pathlib import Path

W = "=wall"


def poly(*pts):
    return {"type": "polygon", "points": [list(p) for p in pts]}


def feat_tree(cid, assembly, features, material="steel", **extra):
    return {"id": cid, "type": "feature_tree", "assembly": assembly, "material": material,
            "parameters": {"features": features}, **extra}


def plate_xy(cid, assembly, z, thick, x0, x1, y0, y1, **kw):
    return feat_tree(cid, assembly, [{"id": cid, "op": "extrude", "plane": "XY", "offset": z, "distance": thick,
                                      "profile": poly((x0, y0), (x1, y0), (x1, y1), (x0, y1))}], **kw)


def box_part(cid, assembly, x0, x1, y0, y1, z0, h, material="steel"):
    return plate_xy(cid, assembly, z0, h, x0, x1, y0, y1, material=material)


def _e(v) -> str:
    """A number or a "=formula" string as formula text without the leading ="""
    return v[1:] if isinstance(v, str) and v.startswith("=") else f"({v})"


def hollow_box(cid, assembly, plane, off, length, a0, a1, b0, b1, inset="=wall", material="steel"):
    """Outer block and an 'interior' cut that leaves walls of `inset`. `plane` XY: a is X, b is Y, length along Z.
    YZ: a is Y, b is Z, length along X."""
    t = _e(inset)
    outer = poly((a0, b0), (a1, b0), (a1, b1), (a0, b1))
    inner = poly((f"={_e(a0)} + {t}", f"={_e(b0)} + {t}"), (f"={_e(a1)} - {t}", f"={_e(b0)} + {t}"),
                 (f"={_e(a1)} - {t}", f"={_e(b1)} - {t}"), (f"={_e(a0)} + {t}", f"={_e(b1)} - {t}"))
    return feat_tree(cid, assembly, [
        {"id": "outer", "op": "extrude", "plane": plane, "offset": off, "distance": length, "profile": outer},
        {"id": "interior", "op": "extrude", "plane": plane, "offset": f"={_e(off)} + {t}",
         "distance": f"={_e(length)} - 2*{t}", "mode": "cut", "profile": inner}], material=material)


def build() -> dict:
    p = {
        "wheelbase": {"default": 2600, "min": 1800, "max": 4000}, "track": {"default": 1550, "min": 1100, "max": 2200},
        "body_len": {"default": 4200, "min": 3000, "max": 6000}, "body_w": {"default": 1780, "min": 1300, "max": 2300},
        "body_h": {"default": 1450, "min": 1100, "max": 2200}, "ground_clr": {"default": 150, "min": 80, "max": 400},
        "belt_z": {"default": 780, "min": 500, "max": 1100}, "wheel_dia": {"default": 640, "min": 400, "max": 900},
        "tyre_w": {"default": 205, "min": 125, "max": 355}, "rim_dia": {"default": 430, "min": 250, "max": 650},
        "wall": {"default": 3, "min": 1.5, "max": 6},
    }
    d = {
        "half_l": "=body_len/2", "half_w": "=body_w/2", "wheel_r": "=wheel_dia/2", "arch_dia": "=wheel_dia + 90",
        "arch_w": "=tyre_w + 140", "wheel_y": "=track/2", "front_x": "=wheelbase/2", "rear_x": "=-wheelbase/2",
        "cab_w": "=body_w - 220", "half_cab": "=(body_w - 220)/2", "cab_base_front": "=body_len/2 - 1100",
        "cab_base_rear": "=-body_len/2 + 380", "roof_front": "=cab_base_front - 480", "roof_rear": "=cab_base_rear + 420",
        "stub_len": "=tyre_w + 60", "door_x0": -300, "door_x1": 800, "door_z0": "=ground_clr + 200", "door_z1": "=belt_z - 20",
        "seat_z": "=ground_clr + 180", "crank_z": "=ground_clr + 280", "eng_x0": 1100, "gb_x0": "=eng_x0 + 470",
        "gm": 5, "zi1": 20, "zc1": 24, "zi2": 22, "zc2": 22, "cs_dist": 110, "gb_z": "=crank_z", "cs_len": "=gb_x0 + 190 - front_x",
        "dd_x0": "=front_x - 100", "hs_y0": 22, "hs_len": "=wheel_y - stub_len/2 - hs_y0",
        "ex_z": 95, "tank_z": "=ground_clr + 10",
    }
    arches = [{"id": f"arch_{n}", "op": "extrude", "plane": "XZ", "offset": "=OFF", "symmetric": True,
               "distance": "=4*wall", "mode": "cut",
               "profile": {"type": "circle", "diameter": "=arch_dia", "center": [f"={x}", "=wheel_r"]}}
              for n, x in (("f", "front_x"), ("r", "rear_x"))]
    door_cut = {"id": "door_opening", "op": "extrude", "plane": "XZ", "offset": "=OFF", "symmetric": True,
                "distance": "=4*wall", "mode": "cut",
                "profile": poly(("=door_x0", "=door_z0"), ("=door_x1", "=door_z0"), ("=door_x1", "=door_z1"),
                                ("=door_x0", "=door_z1"))}
    side_profile = poly(("=-half_l", "=ground_clr"), ("=half_l", "=ground_clr"), ("=half_l", "=belt_z - 110"),
                        ("=half_l - 380", "=belt_z"), ("=-half_l + 260", "=belt_z"), ("=-half_l", "=belt_z - 70"))

    def side(cid, off):
        feats = [{"id": "skin", "op": "extrude", "plane": "XZ", "offset": off, "distance": W, "profile": side_profile}]
        for f in arches + [door_cut]:
            g = dict(f); g["offset"] = off; g["id"] = f["id"] + "_" + cid[-1]; feats.append(g)
        return feat_tree(cid, "body", feats)

    def door(cid, off):
        return feat_tree(cid, "doors", [{"id": "skin", "op": "extrude", "plane": "XZ", "offset": off, "distance": W,
                                         "profile": poly(("=door_x0", "=door_z0"), ("=door_x1", "=door_z0"),
                                                         ("=door_x1", "=door_z1"), ("=door_x0", "=door_z1"))}])

    c: list[dict] = []
    floor = plate_xy("floor", "body", "=ground_clr", W, "=-half_l + wall", "=half_l - wall", "=-half_w + wall", "=half_w - wall")
    floor["fixed"] = True
    c.append(floor)
    c += [side("side_r", "=-half_w"), side("side_l", "=half_w - wall")]
    for cid, off, top in (("front_panel", "=half_l - wall", "=belt_z - 110"), ("rear_panel", "=-half_l", "=belt_z - 70")):
        c.append(feat_tree(cid, "body", [{"id": cid, "op": "extrude", "plane": "YZ", "offset": off, "distance": W,
                                          "profile": poly(("=-half_w + wall", "=ground_clr"), ("=half_w - wall", "=ground_clr"),
                                                          ("=half_w - wall", top), ("=-half_w + wall", top))}]))
    for cid, a, b in (("nose_panel", ("=half_l - 380", "=belt_z"), ("=half_l", "=belt_z - 110")),
                      ("tail_panel", ("=-half_l + 260", "=belt_z"), ("=-half_l", "=belt_z - 70"))):
        c.append(feat_tree(cid, "body", [{"id": cid, "op": "extrude", "plane": "XZ", "symmetric": True, "distance": "=body_w - 2*wall",
                                          "profile": poly(a, b, (b[0], f"={b[1][1:]} - wall"), (a[0], f"={a[1][1:]} - wall"))}]))
    c.append(plate_xy("hood", "body", "=belt_z - wall", W, "=cab_base_front", "=half_l - 380", "=-half_w + wall", "=half_w - wall"))
    c.append(plate_xy("deck", "body", "=belt_z - wall", W, "=-half_l + 260", "=cab_base_rear", "=-half_w + wall", "=half_w - wall"))
    c.append(plate_xy("roof", "body", "=body_h - wall", W, "=roof_rear", "=roof_front", "=-half_cab + 6", "=half_cab - 6"))
    c.append(feat_tree("windscreen", "body", [{"id": "glass", "op": "extrude", "plane": "XZ", "symmetric": True, "distance": "=cab_w - 12",
                       "profile": poly(("=cab_base_front", "=belt_z"), ("=roof_front", "=body_h"), ("=roof_front - 8", "=body_h"),
                                       ("=cab_base_front - 8", "=belt_z"))}], material="glass"))
    c.append(feat_tree("rear_window", "body", [{"id": "glass", "op": "extrude", "plane": "XZ", "symmetric": True, "distance": "=cab_w - 12",
                       "profile": poly(("=roof_rear", "=body_h"), ("=cab_base_rear", "=belt_z"), ("=cab_base_rear + 8", "=belt_z"),
                                       ("=roof_rear + 8", "=body_h"))}], material="glass"))
    cabin = poly(("=cab_base_rear", "=belt_z"), ("=cab_base_front", "=belt_z"), ("=roof_front", "=body_h"), ("=roof_rear", "=body_h"))
    c.append(feat_tree("glass_r", "body", [{"id": "glass", "op": "extrude", "plane": "XZ", "offset": "=-half_cab", "distance": 6, "profile": cabin}], material="glass"))
    c.append(feat_tree("glass_l", "body", [{"id": "glass", "op": "extrude", "plane": "XZ", "offset": "=half_cab - 6", "distance": 6, "profile": cabin}], material="glass"))
    c += [door("door_r", "=-half_w"), door("door_l", "=half_w - wall")]

    for cid, x0, x1, y0, y1 in (("seat_fr", 100, 600, 150, 630), ("seat_fl", 100, 600, -630, -150), ("bench", -700, -200, -640, 640)):
        c.append(box_part(f"{cid}_cushion", "interior", x0, x1, y0, y1, "=seat_z", 120, "foam"))
        c.append(box_part(f"{cid}_back", "interior", x0 - 60, x0, y0, y1, "=seat_z + 120", 400, "foam"))
    c.append(hollow_box("fuel_tank", "interior", "XY", "=tank_z", 140, -850, -450, -400, 400, material="plastic"))

    for wid, x, y in (("wheel_fr", "=front_x", "=wheel_y"), ("wheel_fl", "=front_x", "=-wheel_y"),
                      ("wheel_rr", "=rear_x", "=wheel_y"), ("wheel_rl", "=rear_x", "=-wheel_y")):
        c.append({"id": wid, "module": "wheel_on_axle", "assembly": "wheels",
                  "params": {"tyre_dia": "=wheel_dia", "rim_dia": "=rim_dia", "tyre_w": "=tyre_w", "axle_dia": 24, "axle_len": "=stub_len"},
                  "position": [x, y, "=wheel_r"]})

    c.append({"id": "engine", "module": "piston_engine_4", "assembly": "powertrain",
              "params": {"bore": 80, "stroke": 86, "rod_cd": 160, "main_dia": 50, "rodj_dia": 45, "web": 15, "main_len": 40,
                         "rodj_len": 28, "piston_h": 80, "liner_wall": 4},
              "position": ["=eng_x0", 0, "=crank_z"]})
    key = {"keyway_width_mm": 8, "keyway_depth_mm": 3.3}
    c.append({"id": "input_shaft", "type": "shaft", "assembly": "gearbox", "material": "steel",
              "parameters": {"diameter_mm": 30, "length_mm": 190, **key}, "position": ["=gb_x0", 0, "=gb_z"], "rotation": [0, 90, 0]})
    # The countershaft is the output: it runs back along the wheel-centre line (z = wheel_r) to the differential at the front axle.
    c.append({"id": "counter_shaft", "type": "shaft", "assembly": "gearbox", "material": "steel",
              "parameters": {"diameter_mm": 30, "length_mm": "=cs_len", **key}, "position": ["=front_x", 0, "=gb_z - cs_dist"],
              "rotation": [0, 90, 0]})
    for gid, teeth, shaft_dz, dx in (("gear_in_1", "zi1", 0, 40), ("gear_ct_1", "zc1", "-cs_dist", 40),
                                     ("gear_in_2", "zi2", 0, 100), ("gear_ct_2", "zc2", "-cs_dist", 100)):
        c.append({"id": gid, "type": "gear", "assembly": "gearbox", "material": "steel",
                  "parameters": {"module": "=gm", "teeth": f"={teeth}", "thickness_mm": 25, "bore_diameter_mm": 30, "backlash_mm": "=0.05*gm", **key},
                  "position": [f"=gb_x0 + {dx}", 0, f"=gb_z + {shaft_dz}" if shaft_dz else "=gb_z"], "rotation": [0, 90, 0]})
    for bid, dx, dz in (("brg_in_front", 10, 0), ("brg_in_rear", 165, 0), ("brg_ct_front", 10, "-cs_dist"), ("brg_ct_rear", 165, "-cs_dist")):
        c.append({"id": bid, "type": "bearing", "assembly": "gearbox", "material": "steel",
                  "parameters": {"inner_diameter_mm": 30, "outer_diameter_mm": 55, "width_mm": 13},
                  "position": [f"=gb_x0 + {dx}", 0, f"=gb_z + {dz}" if dz else "=gb_z"], "rotation": [0, 90, 0]})
    case = hollow_box("gearbox_case", "gearbox", "YZ", "=gb_x0 - 5", 200, -70, 70, "=gb_z - cs_dist - 72", "=gb_z + 66",
                      inset=6, material="aluminium")
    c.append(case)

    c.append(hollow_box("diff_housing", "driveline", "YZ", "=dd_x0", 200, -120, 120, "=wheel_r - 60", "=wheel_r + 38",
                        inset=6, material="aluminium"))
    for hid, sign, rot in (("halfshaft_r", 1, -90), ("halfshaft_l", -1, 90)):
        c.append({"id": hid, "type": "shaft", "assembly": "driveline", "material": "steel",
                  "parameters": {"diameter_mm": 30, "length_mm": "=hs_len"}, "position": ["=front_x", f"={sign}*hs_y0", "=wheel_r"],
                  "rotation": [rot, 0, 0]})
    c.append({"id": "exhaust_pipe", "type": "tube", "assembly": "exhaust", "material": "steel",
              "parameters": {"outer_diameter_mm": 50, "wall_thickness_mm": 2, "length_mm": 2650}, "position": [-1450, 300, "=ex_z"], "rotation": [0, 90, 0]})
    c.append({"id": "muffler", "type": "tube", "assembly": "exhaust", "material": "steel",
              "parameters": {"outer_diameter_mm": 100, "wall_thickness_mm": 2, "length_mm": 450}, "position": [-1900, 300, "=ex_z"], "rotation": [0, 90, 0]})
    c.append({"id": "tail_pipe", "type": "tube", "assembly": "exhaust", "material": "steel",
              "parameters": {"outer_diameter_mm": 50, "wall_thickness_mm": 2, "length_mm": 100}, "position": [-2000, 300, "=ex_z"], "rotation": [0, 90, 0]})

    checks = [
        {"name": "wheels_inside_body_width", "expr": "=track/2 + tyre_w/2 < body_w/2 + 60", "message": "wheels stick out too far past the body"},
        {"name": "wheelbase_fits_body", "expr": "=wheelbase + wheel_dia < body_len", "message": "wheels would hang off the ends of the body"},
        {"name": "rim_inside_tyre", "expr": "=rim_dia < wheel_dia - 2*tyre_w*0.25", "message": "rim leaves no tyre wall"},
        {"name": "roof_above_belt", "expr": "=body_h > belt_z + 300", "message": "not enough height for a cabin"},
        {"name": "arch_top_below_belt", "expr": "=wheel_r + arch_dia/2 < belt_z - 40", "message": "wheel arch cuts through the beltline"},
        {"name": "cabin_slopes_valid", "expr": "=roof_front > roof_rear + 300", "message": "roof is too short, shorten the slopes or lengthen the body"},
        {"name": "wheel_in_arch_width", "expr": "=tyre_w < arch_w", "message": "arch is narrower than the tyre"},
        {"name": "door_clears_arches", "expr": "=door_x1 < front_x - arch_dia/2 and door_x0 > rear_x + arch_dia/2", "message": "the door opening runs into a wheel arch"},
        {"name": "gear_pairs_mesh_on_one_spacing", "expr": "=approx(gm*(zi1 + zc1)/2, cs_dist, 0.01) and approx(gm*(zi2 + zc2)/2, cs_dist, 0.01)",
         "message": "both gear pairs must have centre distance equal to the shaft spacing"},
        {"name": "engine_and_gearbox_ahead_of_cabin", "expr": "=eng_x0 - 40 > cab_base_front and gb_x0 + 200 < half_l - 50",
         "message": "the engine and gearbox must sit between the windscreen and the front panel"},
        {"name": "output_shaft_on_axle_line", "expr": "=approx(gb_z - cs_dist, wheel_r, 0.01)",
         "message": "the gearbox output shaft must sit at wheel-centre height so the half shafts run straight"},
        {"name": "output_shaft_clears_flywheel", "expr": "=crank_z - 86 > wheel_r + 15 + 5",
         "message": "the output shaft would hit the engine flywheel"},
        {"name": "differential_clears_crank", "expr": "=wheel_r + 38 < crank_z - 65",
         "message": "the differential housing would hit the crankshaft"},
        {"name": "half_shaft_reaches_the_wheel", "expr": "=hs_len > 100", "message": "the half shafts are too short to reach the wheels"},
        {"name": "gearbox_clears_floor", "expr": "=gb_z - cs_dist - 72 > ground_clr + wall", "message": "the gearbox case would cut through the floor"},
    ]
    notes = ("Concept layout of a small front-wheel-drive hatchback (automobile, car, saloon, sedan), built from separate parts so it can "
             "be pulled apart, weighed and drawn. Front is +X, wheels on the ground at z = 0. Body: floor, side panels with wheel arches and "
             "door openings, end panels, hood, deck, roof, windscreen, rear window, side glasses, two doors, all sheet metal `wall` thick. "
             "Interior: seats and a fuel tank. Powertrain: an inline four engine and, ahead of it, a gearbox with two gear pairs on parallel "
             "shafts, keyways and backlash. Exhaust: pipe, muffler, tail pipe. Driveline: the countershaft runs back to a differential housing on the front axle, with a half shaft to each wheel. "
             "Not modelled: the crown wheel, pinion and CV joints, suspension, steering, pedals, lights, wiring. Not crash-rated or road-legal.")
    d = {k: (v if isinstance(v, str) else f"={v}") for k, v in d.items()}      # every derived value is a formula
    return {"name": "hatchback_car", "notes": notes, "params": p, "derived": d, "components": c, "checks": checks}


if __name__ == "__main__":
    import sys
    name = "car.json" if "--force" in sys.argv else "car_legacy.json"
    out = Path(__file__).resolve().parents[1] / "vehicles" / "examples" / name
    out.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
    print("wrote", out)

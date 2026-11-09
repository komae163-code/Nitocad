"""Car, wheelie bin, light airplane and mini engines: shipped specs and the two modules they added.
Pure Python, no CadQuery. These tests prove the layouts expand, pass their own checks and line up arithmetically.
They do NOT prove the solids build or look right: run the specs through the real kernel for that."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spec_library as SL
import vehicle_spec as V

NEW = {"car": 18, "wheelie_bin": 15, "light_airplane": 15, "mini_engine_single": 7, "mini_engine_twin": 10}


def load(name):
    return json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text(encoding="utf-8"))


def parts(name, overrides=None):
    r = V.expand_vehicle(load(name), overrides)
    assert r["ok"], (name, r["errors"])
    return {p["name"]: p for p in r["parts"]}, r


def test_new_examples_expand_with_every_design_check_passing():
    for name, n in NEW.items():
        r = V.expand_vehicle(load(name))
        assert r["ok"], (name, r["errors"])
        assert len(r["parts"]) == n, (name, len(r["parts"]))
        bad = [c for c in r["checks"] if c["status"] != "pass"]
        assert not bad, (name, bad)
        names = [p["name"] for p in r["parts"]]
        assert len(names) == len(set(names)), name


def test_new_modules_are_listed_and_shared():
    assert {"piston_slider", "landing_gear"} <= set(V.list_modules())
    uses = {n: [c["module"] for c in load(n)["components"] if "module" in c] for n in NEW}
    assert uses["car"].count("wheel_on_axle") == 4 and uses["wheelie_bin"].count("wheel_on_axle") == 2
    assert uses["light_airplane"].count("landing_gear") == 3
    assert uses["mini_engine_single"].count("piston_slider") == 1 and uses["mini_engine_twin"].count("piston_slider") == 2


def test_piston_slider_big_end_lands_on_the_crank_pin_at_any_crank_angle():
    for deg in (0, 37, 90, 180, 270, 311):
        s = {"name": "t", "components": [{"id": "c", "module": "piston_slider", "params": {"crank_deg": deg}}]}
        r = V.expand_vehicle(s)
        assert r["ok"], r["errors"]
        p = {x["name"]: x for x in r["parts"]}
        rod = p["c_rod"]
        phi = math.radians(rod["rotation"][2])
        big = (rod["position"][0] + 52 * math.cos(phi), rod["position"][1] + 52 * math.sin(phi))
        pin = (15 * math.cos(math.radians(deg)), 15 * math.sin(math.radians(deg)))
        assert math.dist(big, pin) < 1e-3, (deg, big, pin)   # expander rounds the rod angle
        assert abs(rod["position"][1]) < 1e-9                      # the small end stays on the cylinder axis


def test_engine_pistons_sit_on_their_crank_journals():
    p, _ = parts("mini_engine_twin")
    main_len, web, jrn = 10.0, 6.0, 10.0
    pitch = 2 * web + jrn + main_len
    for i in (0, 1):
        journal_mid = main_len + web + jrn / 2 + i * pitch
        assert abs(p[f"cyl{i + 1}_piston"]["position"][2] - journal_mid) < 1e-9
        assert abs(p[f"cyl{i + 1}_rod"]["position"][2] + 9 / 2 - journal_mid) < 1e-9   # rod is centred on the journal
    assert abs(p["flywheel"]["position"][2] - (main_len + 2 * pitch)) < 1e-9


def test_engine_numbers_flow_through_overrides():
    p, _ = parts("mini_engine_single", {"stroke": 40, "rod_len": 70})
    assert abs(p["cyl1_rod"]["position"][0] - (20 + 70)) < 1e-9        # TDC: crank radius + rod length
    bad = V.expand_vehicle(load("mini_engine_single"), {"bore": 60})
    assert not bad["ok"] or any(c["status"] != "pass" for c in bad["checks"])   # a bore that cannot fit is flagged, not built silently


def test_wheels_touch_the_ground_on_the_car_bin_and_airplane():
    p, _ = parts("car")
    assert abs(p["wheel_fr_axle"]["position"][2] - 320) < 1e-9
    p, _ = parts("wheelie_bin")
    assert abs(p["wheel_right_axle"]["position"][2] - 100) < 1e-9
    p, _ = parts("light_airplane")
    ground = -(1250 / 2 + 420)
    for g, tyre in (("gear_nose", 320), ("gear_main_r", 380), ("gear_main_l", 380)):
        assert abs(p[f"{g}_axle"]["position"][2] - tyre / 2 - ground) < 1e-6, g


def test_bin_wheels_clear_the_tub_and_the_handle_bar_joins_its_brackets():
    p, _ = parts("wheelie_bin")
    tyre_inner = abs(p["wheel_right_tyre"]["position"][1])
    tub_half_at_tyre_top = 480 / 2 + (580 - 480) / (2 * 1000) * 200
    assert tyre_inner > tub_half_at_tyre_top
    bracket, bar = p["handle_bracket_l"], p["handle_bar"]
    bracket_end = bracket["position"][0]                                # tube starts here and runs +X for 55 mm
    bar_front = bar["position"][0] + 16
    assert bar_front > bracket_end                                      # bracket ends are buried in the bar


def test_new_examples_seed_the_library_and_plain_words_find_them():
    lib = SL.SpecLibrary(SL.MemoryStore())
    r = lib.seed_from_dir(ROOT / "vehicles" / "examples")
    assert not r["failed"], r["failed"]
    top = lambda q: lib.search(q, k=1)[0]["name"]
    assert top("car") == "hatchback_car" and top("a small automobile") == "hatchback_car"
    assert top("rubbish bin") == "wheelie_bin" and top("trash can") == "wheelie_bin"
    assert top("airplane") == "light_airplane" and top("propeller aircraft") == "light_airplane"
    # Two engine families are shipped (mini_engine_single/twin and mini_engine_single_cylinder/inline_two/inline_four),
    # so a plain-words engine query may land on either family. It must land on the right cylinder count.
    assert top("single cylinder engine") in ("mini_engine_single", "mini_engine_single_cylinder")
    assert top("two cylinder engine") in ("mini_engine_twin", "mini_engine_inline_two")
    # the older vehicles still win their own words
    assert top("garden barrow with a wheel") == "wheelbarrow" and top("bike") == "bicycle" and top("cart") == "two_axle_cart"

"""The finished-model inspection: a fixed checklist, a capped fix loop, an honest visual pass."""
import vehicle_inspect as VI


def box(name, lo, hi):
    """A placed part whose local box IS its world box (position 0), so the tests read as coordinates."""
    return {"name": name, "part_type": "box", "parameters": {"_b": (*lo, *hi)}, "position": [0, 0, 0], "rotation": [0, 0, 0]}


def solid(name, lo, hi):
    """A simple_box the exact overlap model understands: centred on `position`, sized by length/width/height."""
    c = [(lo[i] + hi[i]) / 2 for i in range(3)]
    return {"name": name, "part_type": "simple_box", "position": c, "rotation": [0, 0, 0],
            "parameters": {"length_mm": hi[0] - lo[0], "width_mm": hi[1] - lo[1], "height_mm": hi[2] - lo[2]}}


def bbox(p):
    prm = p["parameters"]
    if "_b" in prm:
        return prm["_b"]
    if p["part_type"] == "shaft":
        r = prm["diameter_mm"] / 2
        return (-r, -r, 0, r, r, prm["length_mm"])
    if p["part_type"] == "bearing":
        r = prm["outer_diameter_mm"] / 2
        return (-r, -r, 0, r, r, prm["width_mm"])
    return (-prm["length_mm"] / 2, -prm["width_mm"] / 2, -prm["height_mm"] / 2, prm["length_mm"] / 2, prm["width_mm"] / 2, prm["height_mm"] / 2)


def car(**over):
    parts = {
        "body": box("body", (0, -900, 300), (4000, 900, 1100)),
        "front_left_wheel": box("front_left_wheel", (600, 750, 0), (1200, 950, 600)),
        "front_right_wheel": box("front_right_wheel", (600, -950, 0), (1200, -750, 600)),
        "rear_left_wheel": box("rear_left_wheel", (2800, 750, 0), (3400, 950, 600)),
        "rear_right_wheel": box("rear_right_wheel", (2800, -950, 0), (3400, -750, 600)),
    }
    parts.update(over)
    return list(parts.values())


def codes(found):
    return [f["code"] for f in found]


def test_a_sound_car_has_no_findings():
    assert VI.check_geometry(car(), bbox, kind="car") == []


def test_a_floating_wing_is_found_and_named():
    f = VI.check_geometry(car(rear_wing=box("rear_wing", (3900, -800, 1500), (4300, 800, 1540))), bbox, kind="car")
    fl = [x for x in f if x["code"] == "floating"]
    assert fl and fl[0]["parts"] == ["rear_wing"] and "rear_wing" in fl[0]["instruction"]


def test_a_support_that_reaches_the_wing_connects_it():
    parts = car(rear_wing=box("rear_wing", (3900, -800, 1500), (4300, 800, 1540)),
                wing_support=box("wing_support", (3950, -50, 1090), (4000, 50, 1510)))
    assert "floating" not in codes(VI.check_geometry(parts, bbox, kind="car"))


def test_wheels_off_the_ground_and_uneven_are_found():
    f = VI.check_geometry(car(rear_left_wheel=box("rear_left_wheel", (2800, 750, 200), (3400, 950, 800))), bbox, kind="car")
    assert "wheels" in codes(f) and "rear_left_wheel" in [n for x in f if x["code"] == "wheels" for n in x["parts"]]


def test_wheels_poking_out_of_the_body_are_a_note_not_a_fix():
    f = VI.check_geometry(car(front_left_wheel=box("front_left_wheel", (600, 1300, 0), (1200, 1900, 600))), bbox, kind="car")
    poke = [x for x in f if x["code"] == "wheel_poke"]
    assert poke and poke[0]["severity"] == "note"


def test_unmatched_left_and_right_parts_are_found():
    parts = car(side_blade_left=box("side_blade_left", (1500, 880, 400), (2000, 920, 600)),
                side_blade_right=box("side_blade_right", (1500, -920, 400), (2400, -880, 600)))
    assert "symmetry" in codes(VI.check_geometry(parts, bbox, kind="car"))


def test_parts_mostly_inside_each_other_are_found_but_a_fit_is_not():
    deep = car(a=solid("a", (1500, -100, 600), (1700, 100, 800)), b=solid("b", (1520, -90, 620), (1700, 90, 800)))
    assert "overlap" in codes(VI.check_geometry(deep, bbox, kind="car"))
    fit = car(a=solid("a", (1500, -100, 600), (1700, 100, 800)), b=solid("b", (1690, -100, 600), (1900, 100, 800)))
    assert "overlap" not in codes(VI.check_geometry(fit, bbox, kind="car"))


def test_proportions_only_flag_a_car_that_is_plainly_wrong():
    tall = car(roof=box("roof", (1000, -800, 1100), (3000, 800, 3200)))
    assert "proportions" in codes(VI.check_geometry(tall, bbox, kind="car"))
    assert "proportions" not in codes(VI.check_geometry(tall, bbox, kind="airplane"))


# ------------------------------------------------------------------------------ the loop
WING = box("rear_wing", (3900, -800, 1500), (4300, 800, 1540))


def run(revise, vision=None, max_passes=2):
    calls = []

    def expand(v):
        parts = car(**({"rear_wing": WING} if v.get("wing") == "floating" else {}))
        return parts, []

    def rev(v, instr):
        calls.append(instr)
        return revise(v)

    rep = VI.inspect_vehicle({"name": "car", "wing": "floating"}, expand=expand, bbox_fn=bbox, revise_fn=rev,
                             vision_fn=vision, kind="car", max_passes=max_passes)
    return rep, calls


def test_the_loop_fixes_what_it_finds_and_checks_again():
    rep, calls = run(lambda v: {**v, "wing": "attached"})
    assert rep["changed"] and rep["vspec"]["wing"] == "attached" and rep["findings"] == []
    assert len(calls) == 1 and "rear_wing" in calls[0]
    assert "Fixed" in rep["summary"] and "Nothing wrong" in rep["summary"]


def test_the_loop_never_makes_more_than_two_fixes():
    n = {"i": 0}

    def revise(v):
        n["i"] += 1
        return {**v, "tweak": n["i"]}                      # changes the spec but the wing stays loose: no improvement
    rep, calls = run(revise)
    assert len(calls) <= 2 and not rep["changed"]          # a change that does not improve the checklist is dropped
    assert rep["vspec"] == {"name": "car", "wing": "floating"} and "Still open" in rep["summary"]


def test_a_fix_that_makes_it_worse_is_dropped():
    def worse(v):
        return {**v, "wing": "floating", "rear_left": True}
    rep, _ = run(worse)
    assert not rep["changed"]


def test_with_no_ai_the_checklist_still_reports():
    rep = VI.inspect_vehicle({"name": "car", "wing": "floating"}, expand=lambda v: (car(rear_wing=WING), []), bbox_fn=bbox,
                             revise_fn=None, kind="car")
    assert not rep["changed"] and rep["findings"] and "Still open" in rep["summary"]


def test_the_summary_says_when_no_vision_model_looked():
    rep, _ = run(lambda v: {**v, "wing": "attached"})
    assert rep["visual"].startswith("skipped") and "looked at four renders" not in rep["summary"]


def test_a_failing_vision_model_never_blocks_the_checklist():
    def boom(prompt, imgs):
        raise RuntimeError("down")
    rep, _ = run(lambda v: {**v, "wing": "attached"}, vision=boom)
    assert rep["changed"] and rep["visual"].startswith("skipped")


def test_vision_replies_are_filtered_to_real_parts_and_real_text():
    names = {"body", "rear_wing"}
    ok = '{"issues":[{"parts":["rear_wing"],"problem":"wing floats","fix":"Attach the wing to the tail."},' \
         '{"parts":["ghost"],"problem":"x","fix":"y"},{"parts":["body"],"problem":"","fix":"z"}]}'
    got = VI.parse_vision("Sure! " + ok, names)
    assert [f["parts"] for f in got] == [["rear_wing"]] and got[0]["code"] == "visual"
    assert VI.parse_vision("not json", names) == [] and VI.parse_vision("", names) == []


def test_renders_are_made_when_matplotlib_exists(tmp_path):
    boxes = VI.world_boxes(car(), lambda p: bbox(p))
    paths = VI.render_views(boxes, tmp_path)
    assert paths == [] or [p.name for p in paths] == ["iso.png", "side.png", "front.png", "top.png"]
    assert all(p.stat().st_size > 2000 for p in paths)


# ------------------------------------------------------------------------------ real names: wheel_fl_tyre, wheel_fl_axle
def real_car(**over):
    """Wheel groups named the way vehicles/examples/car.json names them: an axle and a bearing sit above the tyre bottom."""
    parts = {"panels_floor": box("panels_floor", (0, -900, 300), (4000, 900, 400)),
             "panels_roof": box("panels_roof", (1000, -800, 1000), (3000, 800, 1100)),
             "panels_pillar": box("panels_pillar", (1000, -900, 400), (3000, 900, 1000))}
    for tag, x, y in (("fl", 600, 1), ("fr", 600, -1), ("rl", 2800, 1), ("rr", 2800, -1)):
        lo, hi = (750, 950) if y > 0 else (-950, -750)
        parts[f"wheel_{tag}_tyre"] = box(f"wheel_{tag}_tyre", (x, lo, 0), (x + 600, hi, 600))
        parts[f"wheel_{tag}_axle"] = box(f"wheel_{tag}_axle", (x + 250, lo, 250), (x + 350, hi, 350))
        parts[f"wheel_{tag}_bearing_l"] = box(f"wheel_{tag}_bearing_l", (x + 250, lo, 250), (x + 350, lo + 20, 350))
    parts.update(over)
    return list(parts.values())


def test_axles_and_bearings_named_wheel_are_not_wheels_for_the_ground_test():
    assert VI.check_geometry(real_car(), bbox, kind="car") == []


def test_a_lifted_tyre_is_still_found_with_real_names():
    f = VI.check_geometry(real_car(wheel_rl_tyre=box("wheel_rl_tyre", (2800, 750, 150), (3400, 950, 750))), bbox, kind="car")
    assert any(x["code"] == "wheels" and "wheel_rl_tyre" in x["parts"] for x in f)


def test_corner_names_pair_up_for_the_symmetry_check():
    parts = real_car(panels_door_fl=box("panels_door_fl", (1200, 880, 450), (1900, 920, 900)),
                     panels_door_fr=box("panels_door_fr", (1200, -920, 450), (2300, -880, 900)))
    f = VI.check_geometry(parts, bbox, kind="car")
    assert any(x["code"] == "symmetry" and set(x["parts"]) == {"panels_door_fl", "panels_door_fr"} for x in f)
    ok = real_car(panels_door_fl=box("panels_door_fl", (1200, 880, 450), (1900, 920, 900)),
                  panels_door_fr=box("panels_door_fr", (1200, -920, 450), (1900, -880, 900)))
    assert not any(x["code"] == "symmetry" for x in VI.check_geometry(ok, bbox, kind="car"))


def test_a_shaft_through_a_tyre_ring_is_not_an_overlap():
    shaft = {"name": "axle", "part_type": "shaft", "position": [900, 0, 300], "rotation": [-1.5708, 0, 0],
             "parameters": {"diameter_mm": 20, "length_mm": 1000}}
    tyre = {"name": "tyre", "part_type": "bearing", "position": [900, 0, 300], "rotation": [-1.5708, 0, 0],
            "parameters": {"inner_diameter_mm": 24, "outer_diameter_mm": 600, "width_mm": 200}}
    parts = car() + [shaft, tyre]
    assert "overlap" not in codes(VI.check_geometry(parts, bbox, kind="car"))


def test_a_locomotive_with_no_tyres_uses_its_wheel_parts():
    parts = [box("chassis", (0, -400, 200), (3000, 400, 300)), box("boiler", (200, -300, 300), (2500, 300, 900)),
             box("wheel_l_1", (300, 400, 0), (700, 440, 400)), box("wheel_l_2", (1200, 400, 150), (1600, 440, 550)),
             box("wheel_r_1", (300, -440, 0), (700, -400, 400)), box("wheel_r_2", (1200, -440, 0), (1600, -400, 400))]
    f = VI.check_geometry(parts, bbox, kind="locomotive")
    assert any(x["code"] == "wheels" and "wheel_l_2" in x["parts"] for x in f)

"""The finished-model inspection: a fixed checklist, a capped fix loop, an honest visual pass."""
import vehicle_inspect as VI


def box(name, lo, hi):
    """A placed part whose local box IS its world box (position 0), so the tests read as coordinates."""
    return {"name": name, "part_type": "box", "parameters": {"_b": (*lo, *hi)}, "position": [0, 0, 0], "rotation": [0, 0, 0]}


def bbox(p):
    return p["parameters"]["_b"]


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
    deep = car(a=box("a", (1500, -100, 600), (1700, 100, 800)), b=box("b", (1520, -90, 620), (1700, 90, 800)))
    assert "overlap" in codes(VI.check_geometry(deep, bbox, kind="car"))
    fit = car(a=box("a", (1500, -100, 600), (1700, 100, 800)), b=box("b", (1690, -100, 600), (1900, 100, 800)))
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


# ---- real naming: corner suffixes, hub parts, and wheels that are not road wheels -------------------------------
def real_named_car(**over):
    p = {"body": box("body", (0, -900, 300), (4000, 900, 1100)),
         "steering_wheel": box("steering_wheel", (1500, -200, 900), (1700, 0, 1000)),
         "crown_wheel": box("crown_wheel", (2900, -100, 330), (3000, 100, 430)),
         "engine_flywheel": box("engine_flywheel", (300, -100, 500), (400, 100, 600))}
    for tag, (x, y) in {"fl": (900, 850), "fr": (900, -850), "rl": (3100, 850), "rr": (3100, -850)}.items():
        p[f"wheel_{tag}_tyre"] = box(f"wheel_{tag}_tyre", (x - 300, y - 100, 0), (x + 300, y + 100, 600))
        p[f"wheel_{tag}_axle"] = box(f"wheel_{tag}_axle", (x - 10, y - 150, 290), (x + 10, y + 150, 310))
        p[f"wheel_{tag}_bearing_l"] = box(f"wheel_{tag}_bearing_l", (x - 20, y - 60, 280), (x + 20, y - 40, 320))
        p[f"wheel_{tag}_bearing_r"] = box(f"wheel_{tag}_bearing_r", (x - 20, y + 40, 280), (x + 20, y + 60, 320))
    p.update(over)
    return list(p.values())


def test_steering_crown_and_fly_wheels_are_not_road_wheels():
    assert VI.check_geometry(real_named_car(), bbox, kind="car") == []


def test_axles_and_bearings_do_not_count_as_wheels_for_levelness():
    f = VI.check_geometry(real_named_car(), bbox, kind="car")
    assert "wheels" not in codes(f)


def test_a_wheel_that_really_sits_high_is_still_found_by_its_tyre():
    f = VI.check_geometry(real_named_car(wheel_rl_tyre=box("wheel_rl_tyre", (2800, 750, 40), (3400, 950, 640))), bbox, kind="car")
    hit = [x for x in f if x["code"] == "wheels"]
    assert hit and hit[0]["parts"] == ["wheel_rl_tyre"]


def test_corner_names_are_checked_as_left_right_pairs():
    f = VI.check_geometry(real_named_car(panels_door_fl=box("panels_door_fl", (900, 880, 400), (1500, 900, 900)),
                                         panels_door_fr=box("panels_door_fr", (900, -900, 400), (1700, -880, 900))), bbox, kind="car")
    sym = [x for x in f if x["code"] == "symmetry"]
    assert sym and set(sym[0]["parts"]) == {"panels_door_fl", "panels_door_fr"}


def test_side_names():
    assert VI._side_key("side_glazing_1_left") == (("side", "glazing", "1", "*"), "L")
    assert VI._side_key("side_glazing_1_right")[1] == "R"
    assert VI._side_key("wheel_fl_bearing_l") is None
    assert VI._side_key("dark_glass_greenhouse") is None


# ---- render cost ---------------------------------------------------------------------------------------------
def sphere(n_lat, n_lon, r=50.0):
    import numpy as np
    lat, lon = np.meshgrid(np.linspace(0.01, np.pi - 0.01, n_lat), np.linspace(0, 2 * np.pi, n_lon, endpoint=False), indexing="ij")
    v = np.stack([r * np.sin(lat) * np.cos(lon), r * np.sin(lat) * np.sin(lon), r * np.cos(lat)], -1).reshape(-1, 3)
    idx = np.arange(n_lat * n_lon).reshape(n_lat, n_lon)
    a, b, c, d = idx[:-1, :], idx[1:, :], np.roll(idx[:-1, :], -1, 1), np.roll(idx[1:, :], -1, 1)
    t = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([c, b, d], -1).reshape(-1, 3)])
    return v, t


def test_a_heavy_mesh_is_simplified_and_keeps_its_outline():
    import numpy as np
    v, t = sphere(300, 300)                                     # about 180k triangles
    nv, nt = VI.decimate_mesh(v, t, 5000)
    assert 0 < len(nt) <= 5000 and nt.max() < len(nv)
    ext = lambda a: a.max(0) - a.min(0)                         # noqa: E731
    assert np.all(np.abs(ext(nv) - ext(v)) / ext(v) < 0.08)     # same size within 8 percent
    assert VI.decimate_mesh(v, t[:100], 5000)[1].shape[0] == 100


def test_over_the_budget_every_part_is_kept_and_the_heavy_ones_are_simplified():
    heavy, light = sphere(300, 300), sphere(8, 8)
    out = VI.within_triangle_budget({"gear": heavy, "panel": light}, budget=20000)
    assert set(out) == {"gear", "panel"}
    assert len(out["gear"][1]) <= 20000 and len(out["gear"][1]) < len(heavy[1])
    assert len(out["panel"][1]) == len(light[1])                # small parts are untouched
    assert set(VI.within_triangle_budget({"a": light}, budget=10 ** 6)) == {"a"} and VI.within_triangle_budget(None) == {}


def test_a_heavy_mesh_does_not_slow_the_render(tmp_path):
    import time
    b = {"a": (-50, -50, -50, 50, 50, 50), "b": (100, -50, -50, 200, 50, 50)}
    t0 = time.time()
    imgs = VI.render_views(b, tmp_path, meshes={"a": sphere(400, 400)})
    assert (len(imgs) == 4 and time.time() - t0 < 12) or imgs == []     # [] only when matplotlib is missing

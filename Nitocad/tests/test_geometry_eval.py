"""eval_geometry: the cases, the scoring and the numbers. No CadQuery needed: builds are faked."""
import importlib.util
import json
import math
import sys
from pathlib import Path

import eval_geometry as eg
import studio_templates as st

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_geometry_cases", ROOT / "tests" / "eval" / "make_geometry_cases.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

CASES, TOL = eg.load_cases()
BY_ID = {c["id"]: c for c in CASES}


def _out(case, **kw):
    """An outcome that matches the case exactly, with fields overridden."""
    e = case["expect"]
    base = dict(success=True, valid=True, size_mm=list(e["size_mm"]) if e["size_mm"] else None, volume_mm3=e["volume_mm3"], holes=e["holes"], seconds=1.0)
    base.update(kw)
    return eg.Outcome(**base)


# ---- the cases ----------------------------------------------------------------------------

def test_the_committed_cases_are_what_the_generator_writes():
    assert (ROOT / "tests" / "eval" / "geometry_cases.json").read_text() == gen.render()


def test_there_are_about_two_hundred_and_fifty_distinct_cases():
    assert len(CASES) >= 240
    assert len({c["id"] for c in CASES}) == len(CASES) and len({c["prompt"].lower() for c in CASES}) == len(CASES)


def test_every_case_is_complete_and_sane():
    for c in CASES:
        e = c["expect"]
        assert c["prompt"].strip() and c["part_type"]
        assert e["volume_mm3"] > 0 and e["holes"] in (None, *range(0, 40)), c["id"]
        if e["size_mm"] is not None:                                   # a sprocket has no size to check
            assert len(e["size_mm"]) == 3 and e["size_mm"] == sorted(e["size_mm"]) and all(x > 0 for x in e["size_mm"]), c["id"]
            # no part holds more material than the box around it
            assert e["volume_mm3"] <= math.prod(e["size_mm"]) * 1.0001, c["id"]
        assert set(c.get("tolerances", {})) <= {"size_rel", "size_abs_mm", "volume_rel"}, c["id"]


def test_the_cases_are_held_out_from_the_parser_eval_and_the_template_picker():
    parser_prompts = {c["prompt"].strip().lower() for c in json.loads((ROOT / "tests" / "eval" / "parser_cases.json").read_text())["cases"]}
    template_text = {t.title.strip().lower() for t in st.TEMPLATES} | {t.summary.strip().lower() for t in st.TEMPLATES} | {t.hint.strip().lower() for t in st.TEMPLATES}
    for c in CASES:
        p = c["prompt"].strip().lower()
        assert p not in parser_prompts and p not in template_text, c["id"]


def test_expected_numbers_match_hand_arithmetic():
    assert BY_ID["plate-plain-01"]["expect"] == {"size_mm": [4, 50, 80], "volume_mm3": 16000, "holes": 0}
    shaft = BY_ID["shaft-03"]["expect"]                                  # 10 mm x 80 mm
    assert shaft["size_mm"] == [10, 10, 80] and abs(shaft["volume_mm3"] - 6283.185) < 0.001
    assert abs(BY_ID["sphere-02"]["expect"]["volume_mm3"] - 4188.79) < 0.01       # 20 mm ball
    tube = BY_ID["tube-round-01"]["expect"]                                # 20 OD, 2 wall, 100 long
    assert abs(tube["volume_mm3"] - math.pi / 4 * (400 - 256) * 100) < 0.01 and tube["holes"] == 1
    plate = BY_ID["plate-grid-03"]["expect"]                               # 200x120x8, twelve 6 mm holes
    assert plate["holes"] == 12 and abs(plate["volume_mm3"] - (192000 - 12 * math.pi / 4 * 36 * 8)) < 0.01
    flange = BY_ID["flange-01"]["expect"]                                  # bore plus four bolt holes
    assert flange["holes"] == 5 and abs(flange["volume_mm3"] - (math.pi / 4 * (10000 - 2500) * 10 - 4 * math.pi / 4 * 25 * 10)) < 0.01
    assert BY_ID["box-open-01"]["expect"]["volume_mm3"] == 400000 - 94 * 74 * 47
    assert BY_ID["units-02"]["expect"]["size_mm"] == [6.35, 101.6, 152.4]


def test_holes_are_only_stated_where_the_prompt_states_them():
    assert BY_ID["plate-plain-05"]["expect"]["holes"] == 0 and "no holes" in BY_ID["plate-plain-05"]["prompt"].lower()
    assert BY_ID["tube-square-01"]["expect"]["holes"] is None and BY_ID["box-open-01"]["expect"]["holes"] is None


def test_the_generator_check_flag_agrees():
    assert gen.main(["--check"]) == 0


# ---- scoring ------------------------------------------------------------------------------

def test_a_perfect_build_is_correct():
    c = BY_ID["plate-grid-03"]
    s = eg.score_case(c, _out(c), TOL)
    assert s["completed"] and s["correct"] and s["size_ok"] and s["volume_ok"] and s["holes_ok"] and s["why"] == []


def test_a_failed_build_is_not_completed_and_says_why():
    c = BY_ID["shaft-01"]
    s = eg.score_case(c, eg.Outcome(success=False, error="Could not parse the shape"), TOL)
    assert not s["completed"] and not s["correct"] and s["why"] == ["Could not parse the shape"]


def test_a_solid_that_failed_verification_is_not_completed():
    c = BY_ID["shaft-01"]
    assert not eg.score_case(c, _out(c, valid=False), TOL)["completed"]
    assert not eg.score_case(c, _out(c, volume_mm3=0), TOL)["completed"]


def test_wrong_size_completes_but_is_not_correct():
    c = BY_ID["shaft-01"]                                                  # 6 x 40
    s = eg.score_case(c, _out(c, size_mm=[6, 6, 80], volume_mm3=c["expect"]["volume_mm3"] * 2), TOL)
    assert s["completed"] and not s["correct"] and s["size_ok"] is False and s["volume_ok"] is False


def test_size_ignores_which_axis_the_part_lies_along():
    c = BY_ID["shaft-01"]
    assert eg.score_case(c, _out(c, size_mm=[40, 6, 6]), TOL)["size_ok"] is True


def test_tolerances():
    c = BY_ID["plate-plain-01"]                                            # volume 16000
    assert eg.score_case(c, _out(c, volume_mm3=16000 * 1.014), TOL)["volume_ok"] is True
    assert eg.score_case(c, _out(c, volume_mm3=16000 * 1.02), TOL)["volume_ok"] is False
    assert eg.score_case(c, _out(c, size_mm=[4.05, 50.1, 80.3]), TOL)["size_ok"] is True
    assert eg.score_case(c, _out(c, size_mm=[4, 50, 81]), TOL)["size_ok"] is False


def test_the_wrong_number_of_holes_is_a_miss():
    c = BY_ID["plate-grid-03"]                                             # 12 holes
    s = eg.score_case(c, _out(c, holes=4), TOL)
    assert s["completed"] and not s["correct"] and s["holes_ok"] is False and "4 holes, expected 12" in s["why"][0]


def test_an_unreadable_hole_count_is_unscored_not_passed():
    c = BY_ID["plate-grid-03"]
    s = eg.score_case(c, _out(c, holes=None), TOL)
    assert s["holes_ok"] is None and s["correct"] is True                  # size and volume held; holes were not checked


def test_a_case_that_does_not_state_holes_does_not_score_them():
    c = BY_ID["box-open-01"]
    assert eg.score_case(c, _out(c, holes=99), TOL)["holes_ok"] is None


def test_nothing_measured_is_never_correct():
    c = BY_ID["shaft-01"]
    s = eg.score_case(c, _out(c, size_mm=None, volume_mm3=1.0), TOL)
    assert s["completed"] and not s["correct"]


# ---- counting holes -----------------------------------------------------------------------

def test_count_holes_counts_inward_faces_only():
    outer = (10.0, (0, 0, 0), (0, 0, 1), False)
    bore = (2.5, (0, 0, 0), (0, 0, 1), True)
    assert eg.count_holes([outer, bore]) == 1


def test_a_hole_the_kernel_split_in_two_is_one_hole():
    half = (2.5, (0, 0, 0), (0, 0, 1), True)
    assert eg.count_holes([half, half]) == 1


def test_holes_on_different_axes_are_different_holes():
    a = (2.5, (0, 0, 0), (0, 0, 1), True)
    b = (2.5, (30, 0, 0), (0, 0, 1), True)
    c = (2.5, (0, 30, 0), (0, 0, -1), True)                                # same direction, opposite sign
    assert eg.count_holes([a, b, c]) == 3


def test_an_axis_has_no_sign_and_no_position_along_itself():
    a = (2.5, (30, 0, 0), (0, 0, 1), True)
    b = (2.5, (30, 0, 17), (0, 0, -1), True)                               # the same hole, described from the other end
    assert eg.count_holes([a, b]) == 1


def test_different_radius_on_the_same_axis_is_a_counterbore_and_counts_twice():
    assert eg.count_holes([(2.5, (0, 0, 0), (0, 0, 1), True), (5, (0, 0, 0), (0, 0, 1), True)]) == 2


def test_no_cylinders_no_holes():
    assert eg.count_holes([]) == 0


# ---- the numbers --------------------------------------------------------------------------

def _run(builder, cases=None):
    return eg.run(cases or CASES[:10], builder, TOL)


def test_summary_rates_seconds_and_by_type():
    cases = CASES[:10]
    state = {"i": 0}

    def build(prompt):
        i, state["i"] = state["i"], state["i"] + 1
        c = cases[i]
        if i < 6:
            return _out(c, seconds=float(i + 1))                          # correct, 1..6 s
        if i < 8:
            return _out(c, seconds=10.0, volume_mm3=c["expect"]["volume_mm3"] * 2)   # completes, wrong
        return eg.Outcome(success=False, error="boom", seconds=20.0)       # fails

    s = eg.summarize(eg.run(cases, build, TOL))
    assert s["cases"] == 10 and s["completion_rate"] == 0.8 and s["correct_rate"] == 0.6
    assert s["seconds"]["mean"] == (21 + 20 + 40) / 10 and s["seconds"]["p50"] == 5.0 and s["seconds"]["p95"] == 20.0
    assert len(s["failures"]) == 4 and {f["completed"] for f in s["failures"]} == {True, False}
    assert sum(b["cases"] for b in s["by_type"].values()) == 10


def test_a_crashing_builder_fails_the_case_not_the_run():
    def build(prompt):
        raise RuntimeError("kernel died")

    results = _run(build, CASES[:3])
    s = eg.summarize(results)
    assert s["completion_rate"] == 0 and "RuntimeError: kernel died" in s["failures"][0]["why"][0]


def test_cost_needs_prices_and_is_per_part():
    cases = CASES[:4]
    results = eg.run(cases, lambda p: _out(BY_ID["shaft-01"], tokens_in=1000, tokens_out=500), TOL)
    s = eg.summarize(results, mode="llm")
    assert s["tokens"] == {"in": 4000, "out": 2000, "per_part": 1500.0} and s["cost_usd"] is None and s["cost_usd_per_part"] is None
    s = eg.summarize(results, price_in=0.30, price_out=1.20, mode="llm")
    assert abs(s["cost_usd"] - (4000 * 0.30 + 2000 * 1.20) / 1e6) < 1e-9 and abs(s["cost_usd_per_part"] - s["cost_usd"] / 4) < 1e-9


def test_holes_scored_counts_only_what_was_checked():
    cases = [BY_ID["plate-grid-03"], BY_ID["plate-grid-04"], BY_ID["box-open-01"]]
    outs = {c["prompt"]: _out(c, holes=(None if c["id"] == "plate-grid-04" else c["expect"]["holes"])) for c in cases}
    s = eg.summarize(eg.run(cases, lambda p: outs[p], TOL))
    assert s["holes_scored"] == 1 and s["holes_rate"] == 1.0


def test_compare_with_a_baseline():
    a = eg.summarize(_run(lambda p: eg.Outcome(success=False, error="x", seconds=3.0)))
    good = lambda p: _out(CASES[0], seconds=1.0)  # noqa: E731
    b = eg.summarize(eg.run(CASES[:1] * 4, good, TOL))
    d = eg.compare(b, a)
    assert d["completion_rate"] == 1.0 and d["seconds_mean"] == -2.0 and d["cost_usd_per_part"] is None


def test_markdown_carries_the_headline_numbers():
    s = eg.summarize(eg.run(CASES[:4], lambda p: _out(BY_ID["shaft-01"], seconds=1.5), TOL), label="v17")
    md = eg.markdown(s)
    assert "Geometry benchmark v17" in md and "Completion (valid single solid) | 100.0%" in md and "$0 (no AI used)" in md
    assert "| shaft |" in md or "| flat_plate |" in md


# ---- the command line ---------------------------------------------------------------------

def _patched_main(argv, builder):
    saved = eg.make_builder
    eg.make_builder = lambda mode, workdir, formats: builder
    try:
        return eg.main(argv)
    finally:
        eg.make_builder = saved


def _perfect(prompt):
    c = next(x for x in CASES if x["prompt"] == prompt) if prompt != "cube 10 mm" else CASES[0]
    return _out(c)


def test_main_writes_results_and_markdown_and_exits_zero():
    import tempfile
    d = Path(tempfile.mkdtemp())
    code = _patched_main(["--limit", "6", "--label", "t", "--out", str(d / "r.json"), "--markdown", str(d / "r.md")], _perfect)
    assert code == 0
    saved = json.loads((d / "r.json").read_text())
    assert saved["summary"]["cases"] == 6 and saved["summary"]["correct_rate"] == 1.0 and len(saved["cases"]) == 6
    assert saved["cases"][0]["measured"]["seconds"] == 1.0
    assert "Geometry benchmark t" in (d / "r.md").read_text()


def test_main_exits_one_below_a_threshold():
    broken = lambda p: eg.Outcome(success=False, error="x")  # noqa: E731
    assert _patched_main(["--limit", "4", "--fail-under-completion", "0.5"], broken) == 1
    assert _patched_main(["--limit", "4"], broken) == 0               # no threshold, no failure


def test_main_fails_on_regression_against_a_baseline():
    import tempfile
    d = Path(tempfile.mkdtemp())
    assert _patched_main(["--limit", "8", "--out", str(d / "good.json")], _perfect) == 0
    half = {"n": 0}

    def flaky(prompt):
        half["n"] += 1
        c = next((x for x in CASES if x["prompt"] == prompt), CASES[0])
        return _out(c) if half["n"] % 2 else _out(c, volume_mm3=c["expect"]["volume_mm3"] * 3)

    assert _patched_main(["--limit", "8", "--baseline", str(d / "good.json"), "--max-regression", "0.02"], flaky) == 1
    assert _patched_main(["--limit", "8", "--baseline", str(d / "good.json"), "--max-regression", "0.9"], flaky) == 0


def test_only_filters_by_part_type():
    seen = []

    def build(prompt):
        seen.append(prompt)
        return _perfect(prompt)

    _patched_main(["--only", "sphere"], build)
    spheres = [c["prompt"] for c in CASES if c["part_type"] == "sphere"]
    assert [p for p in seen if p != "cube 10 mm"] == spheres


def test_percentile_edges():
    assert eg._pct([], 0.5) == 0.0 and eg._pct([5.0], 0.95) == 5.0 and eg._pct([1, 2, 3, 4], 0.5) == 2


# ---- the cases added when the templates were fixed ---------------------------------------

def test_the_fixed_parts_have_cases():
    kinds = {c["part_type"] for c in CASES}
    assert {"gear", "pulley", "sprocket", "l_bracket", "angle"} <= kinds


def test_new_expected_numbers_match_hand_arithmetic():
    assert BY_ID["angle-01"]["expect"] == {"size_mm": [40, 60, 100], "volume_mm3": 47500, "holes": 0}              # (40 + 60 - 5) x 5 x 100
    lb = BY_ID["lbracket-01"]["expect"]                                                                            # 50 x 60 x 40, 3 thick, 2 holes of 2.5 in each leg
    assert lb["size_mm"] == [40, 50, 60] and lb["holes"] == 4
    assert abs(lb["volume_mm3"] - (50 * 3 * 97 - 4 * math.pi / 4 * 2.5 ** 2 * 3)) < 0.01
    p = BY_ID["pulley-01"]["expect"]                                                                               # 40 OD, 10 belt, 5 bore, 15 thick
    assert p["size_mm"] == [15, 44, 44] and abs(p["volume_mm3"] - math.pi * (400 * 15 + 336 - 111 * 10 - 6.25 * 15)) < 0.01
    g1 = BY_ID["gear-01"]                                                                                          # module 2, 20 teeth: root 17.5, tip 22
    assert g1["expect"]["size_mm"] == [10, 44, 44] and g1["tolerances"] == {"volume_rel": 0.03}
    assert abs(g1["expect"]["volume_mm3"] - (math.pi * (17.5 ** 2 + 0.5 * (22 ** 2 - 17.5 ** 2)) - math.pi / 4 * 64) * 10) < 0.01


def test_a_sprocket_without_tooth_spaces_would_be_a_big_miss():
    """The sprocket volume has z tooth spaces taken out. A disc (no spaces) is well over the tolerance, and so is a sprocket with
    half of its spaces, so the old template's half-as-many-spaces bug would fail these."""
    import re
    for i in range(1, 9):
        c = BY_ID[f"sprocket-{i:02d}"]
        z = int(re.search(r"(\d+) teeth", c["prompt"]).group(1))
        p = float(re.search(r"([\d.]+) mm pitch", c["prompt"]).group(1))
        rp = p / (2 * math.sin(math.pi / z))
        disc = math.pi * (rp + p / 4) ** 2
        spaces = z * gen.circle_overlap_area(rp, rp + p / 4, p / 3)
        assert disc / (disc - spaces) > 1.05, c["id"]                                   # no spaces at all
        assert (disc - spaces / 2) / (disc - spaces) > 1.02, c["id"]                    # half the spaces


def test_circle_overlap_area_matches_known_cases():
    assert gen.circle_overlap_area(0, 10, 3) == math.pi * 9                      # a small disc fully inside a big one
    assert gen.circle_overlap_area(20, 10, 3) == 0                               # apart
    assert abs(gen.circle_overlap_area(10, 10, 10) - (2 * math.pi / 3 - math.sqrt(3) / 2) * 100) < 1e-9     # two unit-spaced equal discs


def test_a_case_can_loosen_its_own_tolerance():
    c = BY_ID["gear-01"]
    out = _out(c, volume_mm3=c["expect"]["volume_mm3"] * 1.025)
    assert eg.score_case(c, out, TOL)["volume_ok"] is True                         # 2.5% is inside the gear's 3%
    plain = {"id": "x", "prompt": "x", "part_type": "shaft", "expect": dict(c["expect"])}
    assert eg.score_case(plain, out, TOL)["volume_ok"] is False                    # and outside everyone else's 1.5%


def test_a_case_with_no_size_is_correct_on_volume_alone():
    c = BY_ID["sprocket-01"]
    s = eg.score_case(c, _out(c), TOL)
    assert s["size_ok"] is None and s["holes_ok"] is None and s["correct"] is True
    assert eg.score_case(c, _out(c, volume_mm3=c["expect"]["volume_mm3"] * 1.2), TOL)["correct"] is False
    assert eg.score_case(c, _out(c, volume_mm3=None), TOL)["correct"] is False     # the volume it expects was never measured

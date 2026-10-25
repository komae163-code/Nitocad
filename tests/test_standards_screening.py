"""standards.py, materials.py, screening.py: tables checked against known engineering values,
formulas checked against hand calculation. Pure Python."""
import math

import pytest

import materials
import screening as sc
import standards as st
from validator import validate_parameters


# ------------------------------------------------------------- threads/fasteners ---

def test_stress_areas_match_the_published_values():
    published = {"M3": 5.03, "M4": 8.78, "M5": 14.2, "M6": 20.1, "M8": 36.6, "M10": 58.0, "M12": 84.3, "M16": 157, "M20": 245, "M24": 353}
    for size, area in published.items():
        assert st.metric_thread(size)["stress_area_mm2"] == pytest.approx(area, rel=0.01), size


def test_thread_basics_and_tap_drill():
    m8 = st.metric_thread("M8")
    assert (m8["pitch_mm"], m8["tap_drill_mm"], m8["clearance_hole_mm"]) == (1.25, 6.75, {"fine": 8.4, "medium": 9.0, "coarse": 10.0})
    assert st.metric_thread("m 8") is None or st.metric_thread("m 8")["size"] == "M8"
    assert st.clearance_hole("M10", "fine") == 10.5 and st.clearance_hole("M10", "loose") is None
    assert st.metric_thread("M7") is None and st.metric_thread(8) is None


def test_fastener_dimensions_from_the_standards():
    assert st.hex_bolt("M10", 40)["head_across_flats_mm"] == 16 and st.hex_bolt("M10", 40)["head_height_mm"] == 6.4
    assert st.socket_cap_screw("M6", 20)["head_diameter_mm"] == 10 and st.socket_cap_screw("M6", 20)["socket_across_flats_mm"] == 5
    assert st.hex_nut("M12")["height_mm"] == 10.8 and st.hex_nut("M12")["across_flats_mm"] == 18
    w = st.plain_washer("M8")
    assert (w["inner_diameter_mm"], w["outer_diameter_mm"], w["thickness_mm"]) == (8.4, 16, 1.6)
    for bad in (0, -5, float("nan"), True, "x"):
        assert st.hex_bolt("M8", bad) is None


def test_every_size_is_internally_consistent():
    for size in st.thread_sizes():
        d = float(size[1:])
        assert st.hex_nut(size)["across_flats_mm"] > d
        assert st.plain_washer(size)["inner_diameter_mm"] > d                # the washer must pass the bolt
        assert st.clearance_hole(size, "fine") <= st.clearance_hole(size, "medium") <= st.clearance_hole(size, "coarse")
        assert st.socket_cap_screw(size, 10)["head_diameter_mm"] > d


def test_bearings_by_designation():
    assert st.bearing("6204")["outer_diameter_mm"] == 47 and st.bearing("6204")["width_mm"] == 14
    assert st.bearing("608 2RS")["inner_diameter_mm"] == 8 and st.bearing("6205-ZZ")["inner_diameter_mm"] == 25
    assert st.bearing("9999") is None and st.bearing("hello") is None
    for des, (d, D, B) in st.BEARINGS.items():
        assert D > d and B > 0, des


def test_bearing_bore_follows_the_iso_pattern():
    # 6000-series and later: bore = 5 x last two digits for 04 and up
    for des, (d, *_rest) in st.BEARINGS.items():
        if len(des) == 4 and int(des[2:]) >= 4:
            assert d == 5 * int(des[2:]), des


def test_pipe_and_sections():
    p = st.pipe("2", 40)
    assert (p["outer_diameter_mm"], p["wall_thickness_mm"]) == (60.33, 3.91)
    assert st.pipe("2", "sch 80")["wall_thickness_mm"] == 5.54 and st.pipe("2", 10) is None and st.pipe("9", 40) is None
    for nps in ("1/2", "1", "4"):
        assert st.pipe(nps, 80)["wall_thickness_mm"] > st.pipe(nps, 40)["wall_thickness_mm"]
    s = st.section("IPE 200")
    assert (s["height_mm"], s["width_mm"], s["web_thickness_mm"], s["flange_thickness_mm"]) == (200, 100, 5.6, 8.5)
    assert st.section("upn120")["beam_type"] == "channel" and st.section("IPE999") is None


def test_find_standard_from_plain_text():
    cases = {
        "I need an M8 hex bolt 30mm long": ("hex_bolt", {"size": "M8", "length_mm": 30.0}),
        "M6 x 20 socket head cap screw": ("socket_cap_screw", {"size": "M6", "length_mm": 20.0}),
        "an M10 nut": ("hex_nut", {"size": "M10"}),
        "M5 washer": ("washer", {"outer_diameter_mm": 10, "inner_diameter_mm": 5.3, "thickness_mm": 1.0}),
        "a 6204 bearing": ("bearing", {"inner_diameter_mm": 20.0, "outer_diameter_mm": 47.0, "width_mm": 14.0}),
        "NPS 2 schedule 40 pipe": ("tube", {"shape": "round", "outer_diameter_mm": 60.33, "wall_thickness_mm": 3.91}),
        "IPE200 beam": ("structural_beam", {"beam_type": "i_beam", "height_mm": 200.0, "width_mm": 100.0, "thickness_mm": 8.5}),
    }
    for text, (ptype, params) in cases.items():
        got = st.find_standard(text)
        assert got and got["part_type"] == ptype and got["parameters"] == params, text


def test_find_standard_never_guesses():
    for text in ("an M8 bolt", "M99 bolt 20mm", "a bracket with 4 holes", "6204", "NPS 7 schedule 40", "M8 x"):
        assert st.find_standard(text) is None, text


# ------------------------------------------------------------------ materials ---

def test_material_lookup_and_generic_flag():
    m = materials.lookup("6061-T6 aluminium plate")
    assert m.key == "6061-T6" and not m.generic and m.yield_mpa == 276
    g = materials.lookup("aluminum")
    assert g.key == "6061-T6" and g.generic
    assert materials.lookup("304 stainless steel").key == "304 stainless"        # not plain steel
    assert materials.lookup("stainless").generic and materials.lookup("mild steel").key == "S235 steel"
    assert materials.lookup("unobtainium") is None and materials.lookup(None) is None and materials.lookup("") is None


def test_material_data_is_sane():
    for m in materials.MATERIALS.values():
        assert 0.3 < m.density_g_cm3 < 20 and (m.e_gpa is None or m.e_gpa > 0.5)
        if m.yield_mpa and m.uts_mpa:
            assert m.uts_mpa >= m.yield_mpa, m.key


def test_densities_agree_with_the_assembly_mass_table():
    import assembly_checks
    for text in ("6061 aluminum", "7075", "304 stainless", "PLA", "ABS", "brass", "copper", "titanium", "nylon"):
        assert materials.density_g_cm3(text) == pytest.approx(assembly_checks.density_for(text), rel=0.01), text


# ------------------------------------------------------------ section properties ---

def test_section_properties_against_formulas():
    r = sc.section_properties("rect", b=20, h=10)
    assert r["area_mm2"] == 200 and r["i_mm4"] == pytest.approx(20 * 1000 / 12) and r["c_mm"] == 5
    c = sc.section_properties("round", d=10)
    assert c["i_mm4"] == pytest.approx(math.pi * 10**4 / 64)
    t = sc.section_properties("tube", d_outer=20, d_inner=16)
    assert t["i_mm4"] == pytest.approx(math.pi * (20**4 - 16**4) / 64)
    rt = sc.section_properties("rect_tube", b=20, h=30, t=2)
    assert rt["i_mm4"] == pytest.approx((20 * 30**3 - 16 * 26**3) / 12)


def test_ibeam_section_matches_a_published_ipe200():
    s = st.section("IPE200")
    i = sc.section_properties("i_beam", h=s["height_mm"], b=s["width_mm"], tw=s["web_thickness_mm"], tf=s["flange_thickness_mm"])
    # Published Iy is 1943 cm^4. The root fillets between web and flange are not modelled, so ours is a few
    # percent LOW, which is the safe direction for a stiffness or stress check.
    assert i["i_mm4"] / 1e4 == pytest.approx(1943, rel=0.07)
    assert i["i_mm4"] / 1e4 < 1943


def test_bad_sections_are_refused():
    for kind, dims in (("rect", {"b": 0, "h": 5}), ("tube", {"d_outer": 10, "d_inner": 12}), ("i_beam", {"h": 10, "b": 5, "tw": 2, "tf": 6}),
                       ("rect_tube", {"b": 10, "h": 10, "t": 6}), ("oval", {})):
        with pytest.raises(sc.ScreeningError):
            sc.section_properties(kind, **dims)


# ----------------------------------------------------------------------- beams ---

def test_cantilever_matches_hand_calculation():
    sec = sc.section_properties("rect", b=20, h=10)
    r = sc.beam_check(sec, 100, 100, material="6061 aluminum")           # P = 100 N at the tip of a 100 mm cantilever
    stress = next(c for c in r["checks"] if c["check"] == "bending stress")
    defl = next(c for c in r["checks"] if c["check"] == "deflection")
    assert stress["value"] == pytest.approx(30.0) and stress["limit"] == pytest.approx(276 / 1.5)
    assert defl["value"] == pytest.approx(100 * 100**3 / (3 * 68900 * (20 * 1000 / 12)), rel=1e-3)
    assert r["status"] == "pass" and "Screening" in r["disclaimer"] and r["assumptions"]


def test_all_four_load_cases_use_the_textbook_coefficients():
    sec = sc.section_properties("rect", b=10, h=10)
    I, E = sec["i_mm4"], 68900
    for (support, load), (mc, dc) in {("cantilever", "end"): (1, 1 / 3), ("cantilever", "udl"): (1 / 2, 1 / 8),
                                       ("simply_supported", "center"): (1 / 4, 1 / 48), ("simply_supported", "udl"): (1 / 8, 5 / 384)}.items():
        r = sc.beam_check(sec, 200, 50, support=support, load=load, material="6061")
        assert r["max_moment_nmm"] == pytest.approx(mc * 50 * 200, abs=0.1)
        assert next(c for c in r["checks"] if c["check"] == "deflection")["value"] == pytest.approx(dc * 50 * 200**3 / (E * I), rel=2e-3, abs=2e-3)


def test_overloaded_beam_fails_and_the_utilisation_bands():
    sec = sc.section_properties("rect", b=10, h=3)                        # a thin strip
    assert sc.beam_check(sec, 200, 200, material="6061")["status"] == "fail"
    assert sc._status(0.8) == "pass" and sc._status(0.81) == "marginal" and sc._status(1.0) == "marginal" and sc._status(1.01) == "fail"


def test_material_without_yield_says_unknown_never_pass():
    r = sc.beam_check(sc.section_properties("rect", b=10, h=10), 100, 10, material="brass")
    stress = next(c for c in r["checks"] if c["check"] == "bending stress")
    assert stress["status"] == "unknown" and r["status"] in ("unknown", "fail", "marginal")


def test_beam_inputs_are_validated():
    sec = sc.section_properties("rect", b=10, h=10)
    for kw in ({"length_mm": 0}, {"load_n": -1}, {"load_n": float("nan")}, {"safety_factor": 0}, {"safety_factor": 99}):
        args = {"length_mm": 100, "load_n": 10, "material": "6061", **kw}
        with pytest.raises(sc.ScreeningError):
            sc.beam_check(sec, args.pop("length_mm"), args.pop("load_n"), **args)
    with pytest.raises(sc.ScreeningError):
        sc.beam_check(sec, 100, 10, material="unobtainium")
    with pytest.raises(sc.ScreeningError):
        sc.beam_check(sec, 100, 10, material="6061", support="fixed", load="end")


def test_beam_from_a_template_part():
    r = sc.beam_check_for_part("shaft", {"diameter_mm": 10, "length_mm": 100}, 50, material="4140 steel")
    assert r["kind"] == "beam" and r["assumptions"][0] == "solid round bar"
    with pytest.raises(sc.ScreeningError):
        sc.beam_check_for_part("gear", {}, 10, material="steel")


# ----------------------------------------------------------------------- bolts ---

def test_bolt_tension_matches_hand_calculation():
    r = sc.bolt_check("M8", "8.8", tensile_load_n=10000)
    t = r["checks"][0]
    assert t["limit"] == pytest.approx(580 * 36.6 / 2, rel=1e-3) and t["status"] == "marginal" and t["utilisation"] == pytest.approx(0.942, abs=0.002)


def test_bolt_shear_planes_and_combined():
    one = sc.bolt_check("M10", "10.9", shear_load_n=20000, shear_planes=1)["checks"][0]
    two = sc.bolt_check("M10", "10.9", shear_load_n=20000, shear_planes=2)["checks"][0]
    assert two["limit"] == pytest.approx(2 * one["limit"])
    comb = sc.bolt_check("M10", "8.8", tensile_load_n=15000, shear_load_n=15000)
    assert [c["check"] for c in comb["checks"]] == ["tension", "shear", "combined"]
    assert comb["checks"][2]["value"] == pytest.approx(math.hypot(comb["checks"][0]["utilisation"], comb["checks"][1]["utilisation"]), rel=1e-2)


def test_stronger_class_and_bigger_bolt_carry_more():
    cap = lambda size, cls: sc.bolt_check(size, cls, tensile_load_n=1000)["checks"][0]["limit"]  # noqa: E731
    assert cap("M8", "4.6") < cap("M8", "8.8") < cap("M8", "10.9") < cap("M8", "12.9")
    assert cap("M6", "8.8") < cap("M8", "8.8") < cap("M10", "8.8")


def test_bolt_bad_inputs():
    for args in (("M7", "8.8", 100, 0), ("M8", "99.9", 100, 0), ("M8", "8.8", 0, 0), ("M8", "8.8", -5, 0), ("M8", "8.8", 100, float("inf"))):
        with pytest.raises(sc.ScreeningError):
            sc.bolt_check(args[0], args[1], tensile_load_n=args[2], shear_load_n=args[3])
    with pytest.raises(sc.ScreeningError):
        sc.bolt_check("M8", "8.8", tensile_load_n=1, shear_planes=3)


# ------------------------------------------------------------- mass properties ---

def test_mass_and_centre_of_mass():
    r = sc.mass_properties([
        {"name": "a", "volume_mm3": 1000.0, "density_g_cm3": 2.7, "centroid": (0, 0, 0)},
        {"name": "b", "volume_mm3": 1000.0, "density_g_cm3": 2.7, "centroid": (10, 20, 30)}])
    assert r["mass_g"] == pytest.approx(5.4) and r["center_of_mass_mm"] == (5.0, 10.0, 15.0) and r["complete"]


def test_heavier_part_pulls_the_centre_of_mass():
    r = sc.mass_properties([{"name": "steel", "volume_mm3": 1000, "density_g_cm3": 7.85, "centroid": (0, 0, 0)},
                            {"name": "foam", "volume_mm3": 1000, "density_g_cm3": 0.05, "centroid": (100, 0, 0)}])
    assert r["center_of_mass_mm"][0] < 1.5


def test_missing_density_makes_the_result_partial_not_complete():
    r = sc.mass_properties([{"name": "a", "volume_mm3": 1000, "density_g_cm3": 2.7, "centroid": (0, 0, 0)},
                            {"name": "mystery", "volume_mm3": 1000, "density_g_cm3": None, "centroid": (9, 9, 9)}])
    assert not r["complete"] and r["parts_missing"] == ["mystery"] and r["center_of_mass_mm"] == (0.0, 0.0, 0.0)
    assert sc.mass_properties([])["complete"] is False


# ------------------------------------------------------- fastener validators ---

def test_fastener_validators():
    _, ok = validate_parameters("hex_bolt", {"size": "M8", "length_mm": 30})
    assert ok.is_valid and not ok.warnings
    for part, params in (("hex_bolt", {"size": "M7", "length_mm": 30}), ("socket_cap_screw", {"size": 8, "length_mm": 20}),
                         ("hex_nut", {"size": "banana"}), ("hex_bolt", {"size": "M8", "length_mm": -1}), ("hex_bolt", {"size": "M8", "length_mm": "x"})):
        assert not validate_parameters(part, params)[1].is_valid, (part, params)
    assert validate_parameters("hex_nut", {})[1].is_valid                       # defaults to M8
    assert validate_parameters("hex_bolt", {"size": "M8", "length_mm": 500})[1].warnings     # 62x its diameter
    assert validate_parameters("hex_bolt", {"size": "M8", "length_mm": 5})[1].warnings

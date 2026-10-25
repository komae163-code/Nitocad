"""Aerospace engineering core: pure Python, no CAD kernel needed."""
import math
import copy
import pytest

from aerospace.airfoil import polygon_section_properties, naca4_points
from aerospace import analysis as an
from aerospace.materials import LIBRARY, MaterialError, custom_material, resolve_material
from aerospace.requirements import Requirement, SpecError, State, can_transition, Sourced
from aerospace.evidence import revision_id, change_impact, external_evidence_status, consumed_snapshot
from aerospace.release import assess_design

B_BASIS = {"id": "TEST-B", "density_g_cc": 2.81, "e_mpa": 71700, "ftu_mpa": 490, "fty_mpa": 420,
           "basis": "B", "source": "test fixture - not a real allowable"}
FS = {"value": 1.5, "state": "confirmed", "source": "program spec"}


def wing_spec(**kw):
    s = {"design_id": "W1", "part_type": "airfoil_wing",
         "params": {"naca": "2412", "root_chord_mm": 200, "tip_chord_mm": 120, "span_mm": 600},
         "material": dict(B_BASIS), "factors": {"ultimate": dict(FS)},
         "loads": {"wing_limit_lift": {"value": 800, "state": "confirmed", "source": "loads memo", "distribution": "elliptic"}}}
    s.update(kw)
    return s


def with_fea(spec):
    rev = revision_id(spec["part_type"], spec["params"], spec["material"])
    spec["evidence"] = [{"id": "FEA-1", "kind": "fea", "result": "pass", "revision_id": rev,
                         "tool": "solver-x", "tool_version": "1.0", "report_ref": "RPT-001", "reviewer": "J. Stress",
                         "consumed_inputs": consumed_snapshot(spec, "wing_root_bending")}]
    return spec


class TestSection:
    def test_rectangle(self):
        r = polygon_section_properties([(0, 0), (10, 0), (10, 20), (0, 20)])
        assert r["area"] == pytest.approx(200) and r["ixx"] == pytest.approx(10 * 20 ** 3 / 12)

    def test_winding_invariant(self):
        a = polygon_section_properties([(0, 0), (10, 0), (10, 20), (0, 20)])
        b = polygon_section_properties([(0, 0), (0, 20), (10, 20), (10, 0)])
        assert a["ixx"] == pytest.approx(b["ixx"])

    def test_naca0012_area(self):
        assert polygon_section_properties(naca4_points("0012", 100))["area"] == pytest.approx(821, rel=0.01)


class TestAnalysis:
    def test_wing_matches_hand_calc(self):
        mat = LIBRARY["7075-T6"]
        p = {"naca": "0012", "root_chord_mm": 200, "tip_chord_mm": 200, "span_mm": 600}
        r = an.wing_root_bending(p, mat, 1000.0, "uniform", 1.5)
        sec = polygon_section_properties(naca4_points("0012", 200))
        sigma = 1000 * 300 * sec["c_max_y"] / sec["ixx"]
        assert r.outputs["sigma_limit_mpa"] == pytest.approx(sigma, rel=1e-3)
        assert r.outputs["ms_ultimate"] == pytest.approx(mat.ftu_mpa / (1.5 * sigma) - 1, abs=1e-3)

    def test_elliptic_arm(self):
        r = an.wing_root_bending({"span_mm": 600}, LIBRARY["7075-T6"], 100.0, "elliptic", 1.5)
        assert r.outputs["load_arm_mm"] == pytest.approx(4 * 600 / (3 * math.pi), abs=0.01)

    def test_missing_inputs_are_not_defaulted(self):
        assert an.wing_root_bending({}, LIBRARY["7075-T6"], None, "elliptic", 1.5).status == "not_evaluable"
        assert an.wing_root_bending({}, LIBRARY["7075-T6"], 100.0, "elliptic", None).status == "not_evaluable"

    def test_ring_hand_calc(self):
        p = {"outer_diameter_mm": 300, "ring_width_mm": 30, "thickness_mm": 2, "lightening_hole_count": 0}
        r = an.frame_diametral_ring(p, LIBRARY["2024-T3"], 100.0, 1.5)
        sigma = (100 * 135 / math.pi) * 15 / (2 * 30 ** 3 / 12)
        assert r.outputs["sigma_limit_mpa"] == pytest.approx(sigma, rel=1e-3)

    def test_ring_out_of_validity(self):
        p = {"outer_diameter_mm": 100, "ring_width_mm": 40}
        assert an.frame_diametral_ring(p, LIBRARY["2024-T3"], 100.0, 1.5).status == "not_evaluable"

    def test_holes_apply_kt(self):
        p = {"outer_diameter_mm": 300, "ring_width_mm": 30, "thickness_mm": 2,
             "lightening_hole_count": 8, "lightening_hole_diameter_mm": 15}
        assert an.frame_diametral_ring(p, LIBRARY["2024-T3"], 100.0, 1.5).outputs["kt_used"] == 3.0

    def test_negative_margin_fails(self):
        r = an.wing_root_bending({"span_mm": 600}, LIBRARY["6061-T6"], 5e6, "elliptic", 1.5)
        assert r.status == "fail"

    def test_bearing_needs_allowable(self):
        att = {"limit_load_n": 1000, "hole_diameter_mm": 5, "thickness_mm": 2}
        assert an.bearing(att, LIBRARY["6061-T6"], 1.5).status == "not_evaluable"
        m = custom_material(dict(B_BASIS, fbru_mpa=700))
        r = an.bearing(att, m, 1.5)
        assert r.outputs["bearing_stress_mpa"] == pytest.approx(100.0)
        assert r.outputs["ms_bearing"] == pytest.approx(700 / 150 - 1, abs=1e-3)

    def test_wing_volume_prismatoid(self):
        p = {"naca": "0012", "root_chord_mm": 100, "tip_chord_mm": 100, "span_mm": 500}
        area = polygon_section_properties(naca4_points("0012", 100))["area"]
        assert an.wing_volume_mm3(p) == pytest.approx(area * 500)

    def test_mass_needs_volume_for_non_wing(self):
        assert an.mass("wing_rib", {}, LIBRARY["6061-T6"], None).status == "not_evaluable"
        assert an.mass("wing_rib", {}, LIBRARY["6061-T6"], 1e6).outputs["mass_kg"] == pytest.approx(2.7)


class TestMaterialsAndRequirements:
    def test_library_is_typical_not_allowable(self):
        assert all(not m.is_design_allowable for m in LIBRARY.values())

    def test_custom_needs_source_and_basis(self):
        with pytest.raises(MaterialError):
            custom_material({"id": "x", "density_g_cc": 1, "e_mpa": 1, "ftu_mpa": 2, "fty_mpa": 1})
        with pytest.raises(MaterialError):
            custom_material(dict(B_BASIS, fty_mpa=999))

    def test_unknown_library_material(self):
        with pytest.raises(MaterialError):
            resolve_material({"id": "unobtainium"})

    def test_requirement_eval(self):
        r = Requirement.parse({"id": "R1", "parameter": "mass_kg", "comparator": "<=", "target": 5,
                               "state": "confirmed", "source": "SOW"})
        assert r.evaluate({"mass_kg": 4}) == "pass" and r.evaluate({"mass_kg": 6}) == "fail"
        assert r.evaluate({}) == "not_evaluable"

    def test_requirement_needs_source(self):
        with pytest.raises(SpecError):
            Requirement.parse({"id": "R1", "parameter": "m", "comparator": "<=", "target": 1})

    def test_sourced_needs_source(self):
        with pytest.raises(SpecError):
            Sourced.parse({"value": 1.5, "state": "confirmed"}, "x")

    def test_verified_requires_evidence(self):
        assert not can_transition(State.CONFIRMED, State.VERIFIED, False)
        assert can_transition(State.CONFIRMED, State.VERIFIED, True)
        assert not can_transition(State.DRAFT, State.VERIFIED, True)


class TestRevisionAndImpact:
    def test_revision_stable_and_sensitive(self):
        a = revision_id("airfoil_wing", {"span_mm": 600.0, "naca": "2412"}, {"id": "m"})
        assert a == revision_id("airfoil_wing", {"naca": "2412", "span_mm": 600.0}, {"id": "m"})
        assert a != revision_id("airfoil_wing", {"naca": "2412", "span_mm": 601.0}, {"id": "m"})

    def test_impact_reports_affected_methods(self):
        old = wing_spec()
        new = copy.deepcopy(old)
        new["params"]["span_mm"] = 700
        imp = change_impact(old, new)
        assert imp["revision_changed"]
        assert "wing_root_bending" in imp["analyses_to_rerun"] and "mass" in imp["analyses_to_rerun"]

    def test_impact_no_change(self):
        s = wing_spec()
        assert not change_impact(s, copy.deepcopy(s))["revision_changed"]


class TestReleaseGate:
    def test_geometry_only_without_inputs(self):
        s = wing_spec()
        del s["loads"], s["factors"]
        a = assess_design(s)
        assert a["status"] == "geometry_only" and a["gaps"]

    def test_typical_material_blocks(self):
        a = assess_design(wing_spec(material={"id": "7075-T6"}))
        assert any("design allowables" in b for b in a["blockers"])
        assert a["status"] != "ready_for_engineering_review"

    def test_draft_input_blocks(self):
        s = wing_spec()
        s["loads"]["wing_limit_lift"]["state"] = "draft"
        assert any("must be confirmed" in b for b in assess_design(s)["blockers"])

    def test_no_fea_evidence_blocks(self):
        a = assess_design(wing_spec())
        assert any("'fea'" in b for b in a["blockers"]) and a["status"] == "analysis_incomplete"

    def test_negative_margin_is_failed(self):
        s = wing_spec()
        s["loads"]["wing_limit_lift"]["value"] = 1e7
        assert assess_design(s)["status"] == "analysis_failed"

    def test_happy_path_reaches_review_only(self):
        s = with_fea(wing_spec())
        s["requirements"] = [{"id": "R1", "parameter": "mass_kg", "comparator": "<=", "target": 5,
                              "state": "confirmed", "source": "SOW"}]
        a = assess_design(s)
        assert a["blockers"] == [] and a["status"] == "ready_for_engineering_review"
        assert "flight" not in a["status"] and "certified" not in a["status"]

    def test_failed_requirement_blocks(self):
        s = with_fea(wing_spec())
        s["requirements"] = [{"id": "R1", "parameter": "mass_kg", "comparator": "<=", "target": 0.1,
                              "state": "confirmed", "source": "SOW"}]
        assert assess_design(s)["status"] != "ready_for_engineering_review"

    def test_fea_goes_stale_when_inputs_change(self):
        s = with_fea(wing_spec())
        s["params"]["span_mm"] = 650          # consumed input changed
        a = assess_design(s)
        assert any("'fea'" in b for b in a["blockers"])

    def test_fea_carried_forward_when_consumed_inputs_unchanged(self):
        s = with_fea(wing_spec())
        s["params"]["sweep_deg"] = 10         # new revision, but bending does not consume sweep
        ev = external_evidence_status(s["evidence"][0], revision_id(s["part_type"], s["params"], s["material"]), s)
        assert ev["current"] and ev["carried_forward"]

    def test_failed_external_evidence_blocks(self):
        s = with_fea(wing_spec())
        s["evidence"][0]["result"] = "fail"
        assert any("reports a failure" in b for b in assess_design(s)["blockers"])

    def test_bad_part_type(self):
        with pytest.raises(SpecError):
            assess_design({"part_type": "rocket"})

    def test_evidence_records_bound_to_revision(self):
        a = assess_design(wing_spec())
        assert a["evidence_records"] and all(r["revision_id"] == a["revision_id"] for r in a["evidence_records"])


# ---------------------------------------------------------------- parts and checks
from aerospace.assembly import assess_assembly, assembly_change_impact

B2 = dict(B_BASIS, id="TEST-B2", fsu_mpa=290, fbru_mpa=700, galvanic_group="aluminium")


def spar_spec(load=500):
    return {"design_id": "S1", "part_type": "wing_spar",
            "params": {"length_mm": 600, "height_mm": 40, "flange_width_mm": 30,
                       "flange_thickness_mm": 3, "web_thickness_mm": 2},
            "material": dict(B2), "factors": {"ultimate": dict(FS)},
            "loads": {"spar_tip_load": {"value": load, "state": "confirmed", "source": "memo"}}}


def lug_spec(load=10000, **p):
    params = {"width_mm": 30, "length_mm": 50, "thickness_mm": 6, "hole_diameter_mm": 10}
    params.update(p)
    return {"design_id": "L1", "part_type": "attachment_lug", "params": params,
            "material": dict(B2), "factors": {"ultimate": dict(FS)},
            "loads": {"lug_axial": {"value": load, "state": "confirmed", "source": "memo"}}}


class TestSparAndLug:
    def test_spar_section_hand_calc(self):
        from aerospace.sections import i_beam_points
        sec = polygon_section_properties(i_beam_points(40, 30, 3, 2))
        assert sec["ixx"] == pytest.approx(30 * 40 ** 3 / 12 - 28 * 34 ** 3 / 12)

    def test_spar_stress_and_web_shear(self):
        r = assess_design(spar_spec())["results"][0]
        assert r["outputs"]["sigma_limit_mpa"] == pytest.approx(500 * 600 * 20 / 68290.7, rel=1e-3)
        assert r["outputs"]["web_shear_mpa"] == pytest.approx(500 / (34 * 2), rel=1e-3)

    def test_spar_buckling_capped_conservatively(self):
        o = assess_design(spar_spec())["results"][0]["outputs"]
        assert o["tau_buckling_used_mpa"] <= 0.5 * 420 / math.sqrt(3) + 1e-6

    def test_spar_short_beam_not_evaluable(self):
        s = spar_spec()
        s["params"]["length_mm"] = 100
        assert assess_design(s)["results"][0]["status"] == "not_evaluable"

    def test_spar_overload_fails(self):
        assert assess_design(spar_spec(load=1e6))["status"] == "analysis_failed"

    def test_lug_three_modes_hand_calc(self):
        o = assess_design(lug_spec())["results"][0]["outputs"]
        assert o["net_tension_mpa"] == pytest.approx(3 * 10000 / (20 * 6))
        assert o["bearing_mpa"] == pytest.approx(10000 / 60, abs=0.01)
        assert o["shear_out_mpa"] == pytest.approx(10000 / (2 * 6 * (15 - 5 * math.cos(math.radians(40)))), rel=1e-4)

    def test_lug_needs_fsu_and_fbru(self):
        s = lug_spec()
        del s["material"]["fbru_mpa"]
        assert assess_design(s)["results"][0]["status"] == "not_evaluable"

    def test_lug_narrow_rejected(self):
        assert assess_design(lug_spec(width_mm=14))["results"][0]["status"] == "not_evaluable"


# ---------------------------------------------------------------- assemblies
RULES = {"min_edge_ratio": 1.4, "max_clearance_mm": 0.2, "source": "company joint standard (test fixture)"}


def interface(**kw):
    i = {"id": "I1", "a": {"part": "lug", "hole_diameter_mm": 10, "thickness_mm": 6, "edge_distance_mm": 15},
         "b": {"part": "frame", "hole_diameter_mm": 10, "thickness_mm": 6, "edge_distance_mm": 15},
         "fastener": {"diameter_mm": 9.9, "count": 1, "shear_planes": 2, "galvanic_group": "aluminium",
                      "shear_allowable_n": {"value": 40000, "state": "confirmed", "source": "fastener datasheet"}},
         "limit_load": {"value": 10000, "state": "confirmed", "source": "loads memo"}, "rules": dict(RULES)}
    i.update(kw)
    return i


def asm_spec(**kw):
    frame = {"design_id": "x", "part_type": "fuselage_frame",
             "params": {"outer_diameter_mm": 300, "ring_width_mm": 30, "thickness_mm": 6, "lightening_hole_count": 0},
             "material": dict(B2), "factors": {"ultimate": dict(FS)},
             "loads": {"frame_diametral": {"value": 500, "state": "confirmed", "source": "memo"}}}
    s = {"design_id": "ASM1", "parts": {"lug": lug_spec(), "frame": frame},
         "factors": {"ultimate": dict(FS)}, "interfaces": [interface()]}
    s.update(kw)
    return s


class TestAssembly:
    def test_interface_margins_hand_calc(self):
        a = assess_assembly(asm_spec())
        itf = a["interfaces"][0]
        assert itf["checks"]["bearing_a"]["outputs"]["bearing_stress_mpa"] == pytest.approx(10000 / 60, abs=0.01)
        assert itf["checks"]["fastener_shear"]["ms"] == pytest.approx(40000 / (1.5 * 5000) - 1, abs=1e-3)

    def test_rules_must_be_supplied_with_source(self):
        s = asm_spec()
        s["interfaces"][0]["rules"] = {}
        a = assess_assembly(s)
        assert any("rules" in g for g in a["gaps"]) and a["status"] != "ready_for_engineering_review"

    def test_edge_distance_rule(self):
        s = asm_spec()
        s["interfaces"][0]["a"]["edge_distance_mm"] = 10
        assert any("e/D" in b for b in assess_assembly(s)["blockers"])

    def test_clearance_rule(self):
        s = asm_spec()
        s["interfaces"][0]["fastener"]["diameter_mm"] = 9.0
        assert any("clearance" in b for b in assess_assembly(s)["blockers"])

    def test_hole_smaller_than_fastener(self):
        s = asm_spec()
        s["interfaces"][0]["fastener"]["diameter_mm"] = 10.5
        assert any("smaller than fastener" in b for b in assess_assembly(s)["blockers"])

    def test_galvanic_needs_isolation(self):
        s = asm_spec()
        s["interfaces"][0]["fastener"]["galvanic_group"] = "titanium"
        assert any("galvanic" in b for b in assess_assembly(s)["blockers"])
        s["interfaces"][0]["isolation"] = True
        assert not any("galvanic" in b for b in assess_assembly(s)["blockers"])

    def test_unknown_galvanic_group_is_a_gap(self):
        s = asm_spec()
        del s["interfaces"][0]["fastener"]["galvanic_group"]
        assert any("galvanic_group missing" in g for g in assess_assembly(s)["gaps"])

    def test_fastener_overload_fails(self):
        s = asm_spec()
        s["interfaces"][0]["limit_load"]["value"] = 200000
        assert assess_assembly(s)["status"] == "analysis_failed"

    def test_same_part_both_sides_rejected(self):
        s = asm_spec()
        s["interfaces"][0]["b"]["part"] = "lug"
        with pytest.raises(SpecError):
            assess_assembly(s)

    def test_unknown_part_rejected(self):
        s = asm_spec()
        s["interfaces"][0]["a"]["part"] = "ghost"
        with pytest.raises(SpecError):
            assess_assembly(s)

    def test_never_ready_without_assembly_evidence(self):
        a = assess_assembly(asm_spec())
        assert any("assembly-level" in b for b in a["blockers"])
        assert a["status"] != "ready_for_engineering_review"

    def test_mass_rolls_up_when_all_parts_known(self):
        s = asm_spec()
        s["parts"]["frame"]["geometry"] = {"volume_mm3": 1e6}
        s["parts"]["lug"]["geometry"] = {"volume_mm3": 1e5}
        assert assess_assembly(s)["outputs"]["mass_kg"] == pytest.approx(2.81 * 1.1, rel=1e-3)

    def test_impact_flags_interfaces_of_changed_part(self):
        old, new = asm_spec(), asm_spec()
        new["parts"]["lug"]["params"]["thickness_mm"] = 8
        imp = assembly_change_impact(old, new)
        assert imp["changed_parts"] == ["lug"] and imp["interfaces_to_recheck"][0]["interface"] == "I1"
        assert imp["assembly_evidence_stale"]

    def test_impact_no_change(self):
        assert not assembly_change_impact(asm_spec(), asm_spec())["assembly_evidence_stale"]


# ---------------------------------------------------------------- engines and aircraft
from aerospace.engine_cycle import turbofan_design_point, isa, CycleError
from aerospace.aircraft import aircraft_metrics

CYCLE = dict(altitude_m=0, mach=0.0, mdot0_kg_s=400, bpr=5, pi_f=1.6, pi_c=19, tt4_k=1600, pi_inlet=0.98,
             eta_f=0.9, eta_c=0.88, eta_b=0.99, pi_b=0.95, eta_t=0.9, eta_mech=0.99, pi_nozzle=0.99)


class TestEngineCycle:
    def test_isa_reference_points(self):
        assert isa(0) == (288.15, 101325.0)
        t, p = isa(11000)
        assert t == pytest.approx(216.65, abs=0.01) and p == pytest.approx(22632, rel=1e-3)
        assert isa(15000)[1] == pytest.approx(12045, rel=2e-3)

    def test_power_balance_closes(self):
        assert abs(turbofan_design_point(CYCLE)["power_balance_residual_w"]) < 1.0

    def test_plausible_sea_level_turbofan(self):
        r = turbofan_design_point(CYCLE)
        assert 100e3 < r["thrust_n"] < 180e3 and 9 < r["tsfc_g_per_kn_s"] < 15

    def test_turbojet_specific_thrust(self):
        r = turbofan_design_point(dict(CYCLE, bpr=0, pi_f=1.0, pi_c=12, mdot0_kg_s=60))
        assert 700 < r["specific_thrust_n_per_kg_s"] < 1300 and r["bypass_jet_velocity_m_s"] == 0.0

    def test_higher_tit_gives_more_thrust(self):
        a, b = (turbofan_design_point(dict(CYCLE, tt4_k=t))["thrust_n"] for t in (1400, 1800))
        assert b > a

    def test_higher_bpr_lowers_cruise_fuel_consumption(self):
        cr = lambda b, pf: turbofan_design_point(dict(CYCLE, altitude_m=10668, mach=0.8, mdot0_kg_s=120, bpr=b, pi_f=pf, pi_c=20))["tsfc_g_per_kn_s"]
        assert cr(2, 1.8) > cr(5, 1.6) > cr(9, 1.45)

    def test_thrust_scales_with_mass_flow(self):
        a = turbofan_design_point(CYCLE)["thrust_n"]
        assert turbofan_design_point(dict(CYCLE, mdot0_kg_s=800))["thrust_n"] == pytest.approx(2 * a, rel=1e-6)

    def test_inconsistent_inputs_rejected_not_guessed(self):
        with pytest.raises(CycleError):
            turbofan_design_point(dict(CYCLE, tt4_k=900))
        with pytest.raises(CycleError):
            turbofan_design_point({k: v for k, v in CYCLE.items() if k != "eta_c"})
        with pytest.raises(CycleError):
            turbofan_design_point(dict(CYCLE, mach=1.4))

    def test_release_requires_source_and_confirmed_state(self):
        s = {"design_id": "E", "part_type": "turbofan_engine", "params": {}, "cycle": dict(CYCLE)}
        b = assess_design(s)["blockers"]
        assert any("source is required" in x for x in b) and any("must be confirmed" in x for x in b)

    def test_engine_never_ready_without_fea_and_test_and_mass(self):
        s = {"design_id": "E", "part_type": "turbofan_engine", "params": {}, "cycle": dict(CYCLE, state="confirmed", source="study")}
        a = assess_design(s)
        assert a["status"] != "ready_for_engineering_review"
        assert any("'test'" in x for x in a["blockers"]) and any("mass_declared" in x for x in a["blockers"])

    def test_missing_cycle_is_a_gap(self):
        a = assess_design({"design_id": "E", "part_type": "turbofan_engine", "params": {}})
        assert any("cycle" in g for g in a["gaps"])


class TestAircraft:
    def test_default_airliner_is_in_reference_bands(self):
        out, warn = aircraft_metrics({}, 70000, 240000)
        assert warn == [] and 120 < out["wing_area_m2"] < 150 and 0.3 < out["thrust_to_weight"] < 0.4

    def test_wing_area_and_aspect_ratio_hand_calc(self):
        out, _ = aircraft_metrics({"wing_span_mm": 30000, "wing_root_chord_mm": 5000, "wing_tip_chord_mm": 1000})
        assert out["wing_area_m2"] == pytest.approx(90.0) and out["aspect_ratio"] == pytest.approx(10.0)

    def test_tail_in_front_of_wing_is_flagged(self):
        _, w = aircraft_metrics({"tail_x_ratio": 0.2})
        assert any("not behind" in x or "reference band" in x for x in w)

    def test_bad_geometry_rejected(self):
        with pytest.raises(ValueError):
            aircraft_metrics({"wing_tip_chord_mm": 99999})

    def test_oml_mass_must_be_declared_not_computed(self):
        a = assess_design({"design_id": "J", "part_type": "jet_aircraft", "params": {}})
        assert any("mass_declared" in b for b in a["blockers"])
        b = assess_design({"design_id": "J", "part_type": "jet_aircraft", "params": {},
                           "mass_properties": {"value": 42000, "state": "confirmed", "source": "weights report"}})
        assert next(r for r in b["results"] if r["method"] == "mass_declared")["outputs"]["mass_kg"] == 42000

    def test_draft_weights_block(self):
        a = assess_design({"design_id": "J", "part_type": "jet_aircraft", "params": {},
                           "weights": {"mtow_kg": {"value": 70000, "state": "draft", "source": "guess"}}})
        assert any("weights.mtow_kg" in b for b in a["blockers"])


# ---------------------------------------------------------------- off-design, mean-line, aero, performance
from aerospace.engine_cycle import solve_design
from aerospace.engine_offdesign import sweep, offdesign_point
from aerospace.engine_meanline import meanline, mfp, check_geometry
from aerospace import aero
from aerospace.performance import aircraft_performance, service_ceiling
from aerospace.mass_cg import rollup

ML = dict(fan_axial_mach=0.6, fan_hub_tip=0.3, fan_tip_speed_m_s=450, fan_psi_max=0.4, hpc_axial_mach=0.5,
          hpc_mean_speed_m_s=320, hpc_psi_max=0.35, hpt_mean_speed_m_s=380, hpt_psi_max=1.6,
          lpt_mean_speed_m_s=300, lpt_psi_max=2.0, turbine_exit_mach=0.45)
FLIGHT = {"tt4_max_k": 1650, "cruise": {"altitude_m": 10668, "mach": 0.78, "start_mass_kg": 70000, "end_mass_kg": 58000},
          "climb": {"altitude_m": 10668, "mach": 0.78, "mass_kg": 68000}}
CYC320 = dict(CYCLE, mdot0_kg_s=320)


class TestOffDesign:
    def test_reproduces_design_point(self):
        d, st = solve_design(CYCLE)
        p = offdesign_point(st, CYCLE["altitude_m"], CYCLE["mach"], CYCLE["tt4_k"])
        assert p["thrust_n"] == pytest.approx(d["thrust_n"], abs=0.2) and p["tsfc_g_per_kn_s"] == pytest.approx(d["tsfc_g_per_kn_s"], abs=0.001)

    def test_throttle_lowers_thrust_pressure_ratio_and_flow(self):
        _, st = solve_design(CYCLE)
        lo, hi = offdesign_point(st, 0, 0, 1300), offdesign_point(st, 0, 0, 1600)
        assert lo["thrust_n"] < hi["thrust_n"] and lo["pi_c"] < hi["pi_c"] and lo["mdot0_kg_s"] < hi["mdot0_kg_s"]

    def test_thrust_lapses_with_altitude_and_overspeed_is_flagged(self):
        _, st = solve_design(CYCLE)
        sl, hi = offdesign_point(st, 0, 0.8, 1600), offdesign_point(st, 10000, 0.8, 1600)
        assert hi["thrust_n"] < sl["thrust_n"] * 0.6
        assert hi["fan_corrected_flow_ratio"] > 1.05            # fixed Tt4 at altitude over-speeds the fan

    def test_speed_limited_lapse_matches_real_turbofan_range(self):
        from aerospace.performance import rating_tt4
        _, st = solve_design(CYCLE)
        t4 = rating_tt4(st, 10000, 0.8, 1650, 1.0)
        ratio = offdesign_point(st, 10000, 0.8, t4)["thrust_n"] / offdesign_point(st, 0, 0.8, 1600)["thrust_n"]
        assert 0.3 < ratio < 0.45 and offdesign_point(st, 10000, 0.8, t4)["fan_corrected_flow_ratio"] <= 1.0001

    def test_sweep_flags_limits(self):
        r = sweep(CYCLE, [0], [0.0], [1500, 1600], {"tt4_max_k": 1550})
        assert len(r["limits_exceeded"]) == 1 and r["limits_exceeded"][0]["tt4_k"] == 1600 and r["design_reproduced"]

    def test_sweep_size_capped(self):
        with pytest.raises(CycleError):
            sweep(CYCLE, list(range(0, 20000, 100)), [0.1, 0.2, 0.3], [1500])

    def test_too_cold_point_rejected_not_guessed(self):
        _, st = solve_design(CYCLE)
        with pytest.raises(CycleError):
            offdesign_point(st, 0, 0, 900)


class TestMeanline:
    def test_fan_area_matches_mass_flow_parameter(self):
        o = meanline(CYCLE, ML)
        _, st = solve_design(CYCLE)
        a = st["mdot0"] * math.sqrt(st["tt2"]) / (st["pt2"] * mfp(0.6, 1.4))
        assert o["fan_face_area_m2"] == pytest.approx(a, abs=1e-4)

    def test_higher_loading_means_fewer_stages(self):
        a = meanline(CYCLE, ML)["hpc_stages"]
        assert meanline(CYCLE, dict(ML, hpc_psi_max=0.5))["hpc_stages"] < a

    def test_missing_inputs_not_defaulted(self):
        with pytest.raises(CycleError):
            meanline(CYCLE, {k: v for k, v in ML.items() if k != "fan_psi_max"})

    def test_geometry_mismatch_warns(self):
        assert check_geometry(meanline(CYCLE, ML), 2400) and not check_geometry(meanline(CYCLE, ML), meanline(CYCLE, ML)["fan_tip_diameter_mm"])


class TestAero:
    def test_induced_drag_formula(self):
        dp = aero.drag_polar({}, 10668, 0.6, 0.5)
        assert dp["cd_induced"] == pytest.approx(0.25 / (math.pi * dp["cd0"] * 0 + math.pi * aero.geometry({})["ar"] * dp["oswald_e"]), rel=1e-6)

    def test_no_wave_drag_below_critical_mach(self):
        assert aero.drag_polar({}, 10668, 0.5, 0.5)["cd_wave"] == 0.0

    def test_supercritical_section_delays_drag_rise(self):
        a = aero.drag_polar({}, 10668, 0.78, 0.5)
        b = aero.drag_polar({"airfoil_kappa": 0.95}, 10668, 0.78, 0.5)
        assert b["m_crit"] > a["m_crit"] and b["cd"] < a["cd"]

    def test_default_airliner_in_plausible_bands(self):
        dp = aero.drag_polar({}, 10668, 0.6, 0.5)
        assert 0.014 < dp["cd0"] < 0.026 and 15 < aero.max_ld_incompressible({}) < 21

    def test_lift_slope_increases_with_aspect_ratio(self):
        assert aero.lift_curve_slope(10, 0.3, 0.3) > aero.lift_curve_slope(5, 0.3, 0.3)

    def test_level_flight_lift_equals_weight(self):
        lf = aero.level_flight({}, 10668, 0.78, 65000)
        a = aero.air(10668)
        q = 0.5 * a["rho"] * (0.78 * a["a"]) ** 2
        assert lf["cl"] * q * aero.geometry({})["s_ref"] == pytest.approx(65000 * 9.80665, rel=1e-3)

    def test_bad_inputs(self):
        with pytest.raises(ValueError):
            aero.drag_polar({}, 10668, 1.3, 0.5)
        with pytest.raises(ValueError):
            aero.drag_polar({"airfoil_kappa": 1.4}, 10668, 0.7, 0.5)


class TestPerformance:
    def test_cruise_sustainable_and_thrust_equals_drag(self):
        r = aircraft_performance({"airfoil_kappa": 0.95}, CYC320, FLIGHT)
        assert r["cruise_sustainable"] and 8e5 < r["range_km"] * 1000 < 1e7
        assert 2 * r["cruise_thrust_per_engine_n"] == pytest.approx(r["cruise"]["drag_n"], rel=1e-3)

    def test_more_fuel_more_range(self):
        a = aircraft_performance({"airfoil_kappa": 0.95}, CYC320, FLIGHT)["range_km"]
        fl = dict(FLIGHT, cruise=dict(FLIGHT["cruise"], end_mass_kg=54000))
        assert aircraft_performance({"airfoil_kappa": 0.95}, CYC320, fl)["range_km"] > a

    def test_underpowered_aircraft_cannot_cruise(self):
        r = aircraft_performance({}, dict(CYCLE, mdot0_kg_s=60), FLIGHT)
        assert r["cruise_sustainable"] is False

    def test_heavier_aircraft_has_lower_ceiling(self):
        _, st = solve_design(CYC320)
        c1 = service_ceiling({"airfoil_kappa": 0.95}, st, 2, 0.78, 60000, 1650)
        c2 = service_ceiling({"airfoil_kappa": 0.95}, st, 2, 0.78, 75000, 1650)
        assert c1 > c2

    def test_bad_cruise_masses_rejected(self):
        with pytest.raises(CycleError):
            aircraft_performance({}, CYC320, dict(FLIGHT, cruise=dict(FLIGHT["cruise"], end_mass_kg=80000)))


class TestRatingLimit:
    def test_cap_reduces_climb_and_ceiling(self):
        _, st = solve_design(CYC320)
        p = {"airfoil_kappa": 0.95}
        free = service_ceiling(p, st, 2, 0.78, 68000, 1650, cap=1.2)
        capped = service_ceiling(p, st, 2, 0.78, 68000, 1650, cap=1.0)
        assert capped < free

    def test_bad_cap_rejected(self):
        with pytest.raises(CycleError):
            aircraft_performance({}, CYC320, dict(FLIGHT, max_corrected_flow_ratio=2.0))


class TestMassCg:
    def test_sum_and_cg(self):
        o, b = rollup([{"name": "a", "value": 100, "state": "confirmed", "source": "s", "x_mm": 0},
                       {"name": "b", "value": 300, "state": "confirmed", "source": "s", "x_mm": 1000}])
        assert o["mass_kg"] == 400 and o["cg_x_mm"] == 750 and b == []

    def test_engine_mass_from_thrust_to_weight_not_volume(self):
        o, _ = rollup([{"name": "eng", "thrust_n": 100000, "thrust_to_weight": 5.0, "state": "confirmed", "source": "datasheet"}])
        assert o["mass_kg"] == pytest.approx(100000 / (9.80665 * 5.0), abs=0.01)

    def test_cg_percent_mac(self):
        o, _ = rollup([{"name": "a", "value": 10, "state": "confirmed", "source": "s", "x_mm": 1500}], 1000.0, 2000.0)
        assert o["cg_percent_mac"] == 25.0

    def test_draft_component_blocks_and_source_required(self):
        _, b = rollup([{"name": "a", "value": 10, "state": "draft", "source": "s"}])
        assert b
        with pytest.raises(SpecError):
            rollup([{"name": "a", "value": 10, "state": "confirmed"}])

    def test_no_cg_when_a_position_is_missing(self):
        o, _ = rollup([{"name": "a", "value": 10, "state": "confirmed", "source": "s"}])
        assert "cg_x_mm" not in o


class TestIntegratedRelease:
    def _engine(self):
        cyc = dict(CYC320, state="confirmed", source="cycle study",
                   offdesign={"altitudes_m": [0, 10668], "machs": [0, 0.8], "tt4_k": [1400, 1600], "tt4_max_k": 1700},
                   meanline=dict(ML))
        return {"design_id": "E", "part_type": "turbofan_engine", "params": {"fan_diameter_mm": 1500}, "cycle": cyc,
                "mass_components": [{"name": "engine", "thrust_n": 110000, "thrust_to_weight": 5.5, "state": "confirmed", "source": "datasheet"}]}

    def test_engine_full_analysis_has_no_gaps(self):
        a = assess_design(self._engine())
        assert a["gaps"] == [] and {r["method"] for r in a["results"]} >= {"engine_cycle", "engine_offdesign", "engine_meanline", "mass_rollup"}
        assert all(r["status"] == "pass" for r in a["results"])

    def test_engine_limit_exceeded_fails(self):
        s = self._engine()
        s["cycle"]["offdesign"]["tt4_max_k"] = 1500
        assert assess_design(s)["status"] == "analysis_failed"

    def test_engine_missing_blocks_are_gaps(self):
        s = self._engine()
        del s["cycle"]["offdesign"], s["cycle"]["meanline"]
        g = assess_design(s)["gaps"]
        assert any("offdesign" in x for x in g) and any("meanline" in x for x in g)

    def test_jet_needs_flight_and_cycle_for_performance(self):
        a = assess_design({"design_id": "J", "part_type": "jet_aircraft", "params": {}})
        assert any("flight" in g for g in a["gaps"])

    def test_jet_performance_and_cg_integrate(self):
        s = {"design_id": "J", "part_type": "jet_aircraft", "params": {"airfoil_kappa": 0.95},
             "cycle": dict(CYC320, state="confirmed", source="cycle study"),
             "flight": dict(FLIGHT, state="confirmed", source="mission"),
             "mass_components": [{"name": "all", "value": 62000, "state": "confirmed", "source": "weights", "x_mm": 15500}]}
        a = assess_design(s)
        r = {x["method"]: x for x in a["results"]}
        assert r["aircraft_performance"]["status"] == "pass" and "cg_percent_mac" in r["mass_rollup"]["outputs"]
        assert a["status"] != "ready_for_engineering_review"

    def test_jet_draft_flight_blocks(self):
        s = {"design_id": "J", "part_type": "jet_aircraft", "params": {}, "cycle": dict(CYC320, state="confirmed", source="x"),
             "flight": dict(FLIGHT, state="draft", source="x")}
        assert any("flight inputs are 'draft'" in b for b in assess_design(s)["blockers"])


# ---------------------------------------------------------------- stability, trim, takeoff and landing
from aerospace import stability as stab
from aerospace import fieldperf as fp

STAB = {"eta_tail": 0.95, "fuselage_nacelle_forward_shift_mac": 0.10, "sm_min": 0.05, "sm_max": 0.30, "tail_cl_limit": 0.9,
        "cases": [{"name": "fwd", "mass_kg": 70000, "cg_percent_mac": 18, "altitude_m": 10668, "mach": 0.78},
                  {"name": "nom", "mass_kg": 64000, "cg_percent_mac": 24, "altitude_m": 10668, "mach": 0.78},
                  {"name": "aft", "mass_kg": 58000, "cg_percent_mac": 36, "altitude_m": 10668, "mach": 0.78}]}
FIELD = dict(altitude_m=0, tt4_max_k=1650, takeoff_mass_kg=70000, landing_mass_kg=60000, cl_max_takeoff=2.2,
             cl_max_landing=2.6, cl_ground_roll=0.2, mu_roll=0.02, mu_brake=0.4, delta_cd0_takeoff=0.02,
             delta_cd0_landing=0.07, delta_cd0_oei=0.03, rotation_time_s=3.0)
KP = {"airfoil_kappa": 0.95}


class TestStability:
    def test_thin_airfoil_moment(self):
        assert stab.airfoil_cm_quarter_chord("0012") == pytest.approx(0.0, abs=1e-9)
        assert -0.07 < stab.airfoil_cm_quarter_chord("2412") < -0.04

    def test_trim_equilibrium_closes(self):
        for c in stab.analyze(KP, STAB)["cases"]:
            assert abs(c["trim_residual"]) < 1e-8

    def test_static_margin_is_neutral_point_minus_cg(self):
        c = stab.analyze(KP, STAB)["cases"][1]
        assert c["static_margin_percent_mac"] == pytest.approx(c["neutral_point_percent_mac"] - c["cg_percent_mac"], abs=0.01)

    def test_bigger_tail_moves_neutral_point_aft(self):
        a = stab.analyze(KP, STAB)["cases"][0]["neutral_point_percent_mac"]
        big = dict(KP, htail_span_mm=16000, htail_root_chord_mm=4000)
        assert stab.analyze(big, STAB)["cases"][0]["neutral_point_percent_mac"] > a

    def test_fuselage_shift_moves_neutral_point_forward_one_for_one(self):
        a = stab.analyze(KP, STAB)["cases"][0]["neutral_point_percent_mac"]
        b = stab.analyze(KP, dict(STAB, fuselage_nacelle_forward_shift_mac=0.15))["cases"][0]["neutral_point_percent_mac"]
        assert a - b == pytest.approx(5.0, abs=0.01)

    def test_forward_cg_needs_more_download_than_aft(self):
        r = stab.analyze(KP, STAB)["cases"]
        assert r[0]["trim_tail_cl"] < r[2]["trim_tail_cl"]

    def test_cg_behind_neutral_point_is_unstable_and_reported(self):
        s = dict(STAB, cases=[dict(STAB["cases"][1], cg_percent_mac=60)])
        r = stab.analyze(KP, s)
        assert any("below the minimum" in i for i in r["issues"]) and r["static_margin_min_percent_mac"] < 0

    def test_aft_cg_limit_is_neutral_point_minus_minimum_margin(self):
        r = stab.analyze(KP, STAB)
        assert r["aft_cg_limit_percent_mac"] == pytest.approx(r["cases"][0]["neutral_point_percent_mac"] - 5, abs=0.01)

    def test_missing_inputs_not_defaulted(self):
        with pytest.raises(CycleError):
            stab.analyze(KP, {k: v for k, v in STAB.items() if k != "eta_tail"})
        with pytest.raises(CycleError):
            stab.analyze(KP, dict(STAB, cases=[]))
        with pytest.raises(CycleError):
            stab.analyze(KP, dict(STAB, sm_min=0.4))

    def test_rollup_cg_used_when_case_says_rollup(self):
        s = dict(STAB, cases=[dict(STAB["cases"][1], cg_percent_mac="rollup")])
        assert stab.analyze(KP, s, 22.0)["cases"][0]["cg_percent_mac"] == 22.0
        with pytest.raises(CycleError):
            stab.analyze(KP, s, None)


class TestFieldPerformance:
    def test_ground_roll_matches_closed_form_for_constant_thrust(self):
        T, W, S, rho, cd, k, cl, mu, V = 2 * 110000, 70000 * 9.80665, 132.6, 1.225, 0.04, 0.045, 0.2, 0.02, 75
        d, _ = fp.ground_roll(lambda v: T, V, W, S, rho, cd, k, cl, mu)
        kt, ka = T / W - mu, rho / (2 * W / S) * (mu * cl - cd - k * cl ** 2)
        assert d == pytest.approx(1 / (2 * 9.80665 * ka) * math.log((kt + ka * V * V) / kt), rel=1e-4)

    def test_braking_without_aero_is_v_squared_over_two_mu_g(self):
        d = fp.braking(60.0, 50000 * 9.80665, 100.0, 1.2, 0.0, 0.0, 0.0, 0.4, 0.0)
        assert d == pytest.approx(60.0 ** 2 / (2 * 0.4 * 9.80665), rel=1e-4)

    def test_reverse_thrust_shortens_braking(self):
        a = fp.braking(60.0, 5e5, 100.0, 1.2, 0.1, 0.04, 0.0, 0.4, 0.0)
        assert fp.braking(60.0, 5e5, 100.0, 1.2, 0.1, 0.04, 0.0, 0.4, 0.1) < a

    def test_airliner_numbers_are_plausible(self):
        o = fp.analyze(KP, CYC320, FIELD)
        assert 600 < o["takeoff_ground_roll_m"] < 2000 and 1000 < o["takeoff_distance_aeo_m"] < 2800
        assert 900 < o["landing_distance_factored_m"] < 2200
        assert o["landing_distance_factored_m"] == pytest.approx(o["landing_distance_actual_m"] / 0.6, rel=2e-3)

    def test_heavier_aircraft_needs_more_runway(self):
        a = fp.analyze(KP, CYC320, FIELD)["takeoff_distance_aeo_m"]
        assert fp.analyze(KP, CYC320, dict(FIELD, takeoff_mass_kg=78000))["takeoff_distance_aeo_m"] > a

    def test_high_airport_needs_more_runway(self):
        a = fp.analyze(KP, CYC320, FIELD)["takeoff_distance_aeo_m"]
        assert fp.analyze(KP, CYC320, dict(FIELD, altitude_m=2000))["takeoff_distance_aeo_m"] > a

    def test_underpowered_aircraft_cannot_take_off(self):
        with pytest.raises(CycleError):
            fp.analyze(KP, dict(CYCLE, mdot0_kg_s=50), FIELD)

    def test_second_segment_reported_for_twin(self):
        o = fp.analyze(KP, CYC320, FIELD)
        assert o["second_segment_min_far25"] == 0.024 and isinstance(o["meets_second_segment_min"], bool)

    def test_missing_inputs_not_defaulted(self):
        with pytest.raises(CycleError):
            fp.analyze(KP, CYC320, {k: v for k, v in FIELD.items() if k != "cl_max_takeoff"})


class TestStabilityFieldRelease:
    def _spec(self, **kw):
        s = {"design_id": "J", "part_type": "jet_aircraft", "params": dict(KP),
             "cycle": dict(CYC320, state="confirmed", source="cycle study"),
             "stability": dict(STAB, state="confirmed", source="criteria"),
             "field": dict(FIELD, state="confirmed", source="airfield"),
             "mass_components": [{"name": "all", "value": 64000, "state": "confirmed", "source": "weights", "x_mm": 18000}]}
        s.update(kw)
        return s

    def test_methods_present_and_numbers_flow(self):
        a = assess_design(self._spec())
        r = {x["method"]: x for x in a["results"]}
        assert r["stability_trim"]["status"] in ("pass", "fail") and r["field_performance"]["status"] == "pass"
        assert "takeoff_distance_aeo_m" in r["field_performance"]["outputs"]

    def test_requirement_on_takeoff_distance(self):
        s = self._spec(requirements=[{"id": "R1", "parameter": "takeoff_distance_aeo_m", "comparator": "<=", "target": 100,
                                      "state": "confirmed", "source": "airline"}])
        assert any("requirement R1" in b for b in assess_design(s)["blockers"])

    def test_gaps_when_blocks_missing(self):
        s = self._spec()
        del s["stability"], s["field"]
        g = assess_design(s)["gaps"]
        assert any("stability" in x for x in g) and any("field" in x for x in g)

    def test_draft_blocks_stability_and_field(self):
        s = self._spec(stability=dict(STAB, state="draft", source="x"), field=dict(FIELD, state="assumed", source="x"))
        b = assess_design(s)["blockers"]
        assert any("stability inputs are 'draft'" in x for x in b) and any("field inputs are 'assumed'" in x for x in b)

    def test_unstable_cg_fails_with_a_clear_reason(self):
        s = self._spec(stability=dict(STAB, state="confirmed", source="c", cases=[dict(STAB["cases"][1], cg_percent_mac=70)]))
        a = assess_design(s)
        assert a["status"] == "analysis_failed" and any("below the minimum" in b for b in a["blockers"])

    def test_cg_outside_chord_warns(self):
        s = self._spec(mass_components=[{"name": "all", "value": 64000, "state": "confirmed", "source": "w", "x_mm": 5000}])
        a = assess_design(s)
        assert any("outside the wing mean chord" in w for r in a["results"] if r["method"] == "mass_rollup" for w in r["warnings"])


# ================================================================ high lift, balanced field, dynamics, elevator
from aerospace import highlift as hlm, dynamics as dyn, control as ctl
from aerospace.dynamics import charpoly, roots

HL = {"cl_max_airfoil": 1.6, "delta_cd0_gear": 0.015, "delta_cd0_engine_out": 0.004,
      "takeoff": {"flap": {"type": "slotted", "chord_ratio": 0.28, "flapped_area_ratio": 0.6, "deflection_deg": 15},
                  "slat": {"type": "slat", "area_ratio": 0.7, "extended_chord_ratio": 1.1}},
      "landing": {"flap": {"type": "fowler", "chord_ratio": 0.3, "flapped_area_ratio": 0.6, "deflection_deg": 40, "extended_chord_ratio": 1.25},
                  "slat": {"type": "slat", "area_ratio": 0.7, "extended_chord_ratio": 1.1}}}
FIELD2 = dict(FIELD, vmcg_m_s=50, reaction_time_s=2.0, delta_cd0_inoperative_engine=0.004, delta_cd0_oei=0.010,
              cl_max_takeoff=2.24, delta_cd0_takeoff=0.021, delta_cd0_landing=0.057, cl_max_landing=2.4, mu_brake_wet=0.2, tire_pressure_kpa=1400)
DYN = {"eta_tail": 0.95, "fuselage_nacelle_forward_shift_mac": 0.10, "cn_beta_fuselage": -0.07, "cl_beta": -0.10,
       "inertia": {"ix": 1.2e6, "iy": 3.0e6, "iz": 4.0e6, "ixz": 0.0},
       "criteria": {"phugoid_zeta_min": 0.01, "short_period_zeta_min": 0.3, "short_period_zeta_max": 2.0, "dutch_roll_zeta_min": 0.05,
                    "dutch_roll_wn_zeta_min": 0.05, "dutch_roll_wn_min": 0.4, "roll_tau_max_s": 1.4, "spiral_t2_min_s": 20},
       "cases": [{"name": "cruise", "mass_kg": 64000, "cg_percent_mac": 24, "altitude_m": 10668, "mach": 0.78}]}
ELE = {"chord_ratio": 0.3, "span_fraction": 0.9, "max_up_deg": 25, "max_down_deg": 15, "viscous_factor": 0.85,
       "stabilizer_incidence_deg": -1.0, "eta_tail": 0.95, "fuselage_nacelle_forward_shift_mac": 0.10,
       "cases": [{"name": "fwd", "mass_kg": 70000, "cg_percent_mac": 15, "altitude_m": 10668, "mach": 0.78},
                 {"name": "aft", "mass_kg": 58000, "cg_percent_mac": 34, "altitude_m": 10668, "mach": 0.78}]}


class TestHighLift:
    def test_values_in_airliner_range(self):
        o = hlm.analyze(KP, HL)
        assert 1.2 < o["cl_max_clean"] < 1.5 and 2.0 < o["cl_max_takeoff"] < o["cl_max_landing"] < 3.0
        assert 0.01 < o["delta_cd0_takeoff"] < 0.04 and 0.03 < o["delta_cd0_landing"] < 0.09

    def test_clean_cl_max_formula(self):
        g = aero.geometry(KP)
        assert hlm.clean_cl_max(KP, 1.6) == pytest.approx(0.9 * 1.6 * math.cos(g["lam_c4"]))

    def test_fowler_gives_more_lift_than_plain(self):
        f = lambda t: hlm.configuration(KP, 1.6, {"flap": {"type": t, "chord_ratio": 0.3, "flapped_area_ratio": 0.6, "deflection_deg": 30, "extended_chord_ratio": 1.25}})["delta_cl_max_flap"]
        assert f("fowler") > f("slotted") > f("plain")

    def test_more_deflection_more_drag_and_more_area_more_lift(self):
        base = {"type": "slotted", "chord_ratio": 0.3, "flapped_area_ratio": 0.5, "deflection_deg": 20}
        a = hlm.configuration(KP, 1.6, {"flap": base})
        assert hlm.configuration(KP, 1.6, {"flap": dict(base, deflection_deg=40)})["delta_cd0_flap"] > a["delta_cd0_flap"]
        assert hlm.configuration(KP, 1.6, {"flap": dict(base, flapped_area_ratio=0.8)})["delta_cl_max_flap"] > a["delta_cl_max_flap"]

    def test_slat_adds_lift(self):
        a = hlm.configuration(KP, 1.6, {})["cl_max"]
        assert hlm.configuration(KP, 1.6, {"slat": {"type": "slat", "area_ratio": 0.7, "extended_chord_ratio": 1.1}})["cl_max"] > a

    def test_bad_inputs_rejected(self):
        with pytest.raises(CycleError):
            hlm.configuration(KP, 1.6, {"flap": {"type": "barn_door", "chord_ratio": 0.3, "flapped_area_ratio": 0.5, "deflection_deg": 20}})
        with pytest.raises(CycleError):
            hlm.analyze(KP, {k: v for k, v in HL.items() if k != "cl_max_airfoil"})
        with pytest.raises(CycleError):
            hlm.configuration(KP, 1.6, {"flap": {"type": "plain", "chord_ratio": 0.9, "flapped_area_ratio": 0.5, "deflection_deg": 20}})


class TestBalancedField:
    def test_dry_and_wet_bfl_reported_and_ordered(self):
        o = fp.analyze(KP, CYC320, FIELD2)
        assert o["bfl_dry_limited_by"] == "balanced" and o["bfl_wet_m"] > o["bfl_dry_m"] > 800
        assert o["v1_wet_m_s"] < o["v1_dry_m_s"]

    def test_field_length_is_at_least_115_percent_of_aeo(self):
        o = fp.analyze(KP, CYC320, FIELD2)
        assert o["takeoff_field_length_dry_m"] >= o["takeoff_distance_aeo_x115_m"] - 1

    def test_go_equals_stop_at_the_balanced_v1(self):
        o = fp.analyze(KP, CYC320, FIELD2)
        o2 = fp.analyze(KP, CYC320, dict(FIELD2, reaction_time_s=4.0))
        assert o2["bfl_dry_m"] > o["bfl_dry_m"]                       # slower reaction lengthens the balanced field
        assert o2["v1_dry_m_s"] < o["v1_dry_m_s"]                     # and lowers V1

    def test_wet_landing_longer_than_dry(self):
        o = fp.analyze(KP, CYC320, FIELD2)
        assert o["landing_distance_wet_factored_m"] > o["landing_distance_factored_m"] * 1.1

    def test_hydroplaning_flag(self):
        assert fp.analyze(KP, CYC320, dict(FIELD2, tire_pressure_kpa=300))["touchdown_above_hydroplaning_speed"] is True
        assert fp.analyze(KP, CYC320, FIELD2)["touchdown_above_hydroplaning_speed"] is False

    def test_engine_out_that_cannot_climb_is_reported_not_guessed(self):
        o = fp.analyze(KP, CYC320, dict(FIELD2, delta_cd0_oei=0.5))
        assert "bfl_dry_unavailable" in o and "bfl_dry_m" not in o

    def test_missing_bfl_inputs_raise_and_vmcg_off_skips(self):
        with pytest.raises(CycleError):
            fp.analyze(KP, CYC320, {k: v for k, v in FIELD2.items() if k != "reaction_time_s"})
        assert "bfl_dry_m" not in fp.analyze(KP, CYC320, FIELD)

    def test_vmcg_must_be_below_liftoff_speed(self):
        with pytest.raises(CycleError):
            fp.analyze(KP, CYC320, dict(FIELD2, vmcg_m_s=200))

    def test_single_engine_has_no_second_segment_or_bfl(self):
        o = fp.analyze(dict(KP, engines=1), CYC320, FIELD2)
        assert "second_segment_gradient" not in o and "bfl_dry_m" not in o


class TestEigenSolver:
    def test_known_real_roots(self):
        a = [[0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1], [-24, -50, -35, -10]]
        assert sorted(round(r.real, 6) for r in roots(charpoly(a))) == [-4.0, -3.0, -2.0, -1.0]

    def test_second_order_frequency_and_damping(self):
        r = roots(charpoly([[0, 1], [-4, -0.8]]))
        assert abs(r[0]) == pytest.approx(2.0, abs=1e-6) and -r[0].real / abs(r[0]) == pytest.approx(0.2, abs=1e-6)

    def test_charpoly_coefficients(self):
        assert charpoly([[2, 0], [0, 3]]) == [1.0, -5.0, 6.0]


class TestDynamics:
    def test_modes_in_transport_ranges(self):
        c = dyn.analyze(KP, DYN)["cases"][0]
        assert 0.8 < c["short_period_wn"] < 3 and 0.2 < c["short_period_zeta"] < 0.8
        assert 0.04 < c["phugoid_wn"] < 0.15 and 0.0 < c["phugoid_zeta"] < 0.1
        assert 0.8 < c["dutch_roll_wn"] < 2.2 and 0.0 < c["dutch_roll_zeta"] < 0.2
        assert 0.2 < c["roll_mode_tau_s"] < 1.5 and c["cn_beta_total"] > 0

    def test_more_static_margin_raises_short_period_frequency(self):
        fwd = dict(DYN, cases=[dict(DYN["cases"][0], cg_percent_mac=14)])
        aft = dict(DYN, cases=[dict(DYN["cases"][0], cg_percent_mac=34)])
        assert dyn.analyze(KP, fwd)["cases"][0]["short_period_wn"] > dyn.analyze(KP, aft)["cases"][0]["short_period_wn"]

    def test_dutch_roll_frequency_tracks_yaw_stiffness(self):
        c = dyn.analyze(KP, DYN)["cases"][0]
        a = aero.air(10668)
        q = 0.5 * a["rho"] * (0.78 * a["a"]) ** 2
        g = aero.geometry(KP)
        n_beta = q * g["s_ref"] * g["span"] * c["cn_beta_total"] / 4.0e6
        assert c["dutch_roll_wn"] == pytest.approx(math.sqrt(n_beta), rel=0.35)

    def test_directionally_unstable_aircraft_is_flagged(self):
        d = dict(DYN, cn_beta_fuselage=-0.6)
        r = dyn.analyze(KP, d)
        assert any("not positive" in i for i in r["issues"])

    def test_overrides_are_applied_and_labelled(self):
        d = dict(DYN, overrides={"cn_r": -0.5})
        c = dyn.analyze(KP, d)["cases"][0]
        assert c["derivative_sources"]["cn_r"] == "declared override" and c["derivative_sources"]["cm_alpha"] == "estimated"
        with pytest.raises(CycleError):
            dyn.analyze(KP, dict(DYN, overrides={"bogus": 1}))

    def test_stronger_yaw_damping_improves_dutch_roll_damping(self):
        a = dyn.analyze(KP, DYN)["cases"][0]["dutch_roll_zeta"]
        b = dyn.analyze(KP, dict(DYN, overrides={"cn_r": -0.6}))["cases"][0]["dutch_roll_zeta"]
        assert b > a

    def test_criteria_violation_reported(self):
        d = dict(DYN, criteria=dict(DYN["criteria"], dutch_roll_zeta_min=0.9))
        assert any("dutch_roll_zeta" in i for i in dyn.analyze(KP, d)["issues"])

    def test_missing_and_inconsistent_inputs(self):
        with pytest.raises(CycleError):
            dyn.analyze(KP, {k: v for k, v in DYN.items() if k != "cl_beta"})
        with pytest.raises(CycleError):
            dyn.analyze(KP, dict(DYN, inertia={"ix": 1e6, "iy": 3e6, "iz": 4e6, "ixz": 3e6}))


class TestElevator:
    def test_thin_airfoil_values(self):
        assert ctl.thin_airfoil_tau(0.25) == pytest.approx(0.609, abs=0.001)
        assert math.degrees(stab.airfoil_alpha_l0("2412")) == pytest.approx(-2.08, abs=0.05)

    def test_zero_elevator_incidence_trims_the_case(self):
        r = ctl.analyze(KP, ELE)["cases"]
        i0 = r[0]["stabilizer_incidence_for_zero_elevator_deg"]
        again = ctl.analyze(KP, dict(ELE, stabilizer_incidence_deg=i0))["cases"][0]
        assert again["trim_elevator_deg"] == pytest.approx(0.0, abs=0.02)

    def test_forward_cg_needs_more_up_elevator(self):
        r = ctl.analyze(KP, ELE)["cases"]
        assert r[0]["trim_elevator_deg"] < r[1]["trim_elevator_deg"]

    def test_small_elevator_authority_fails(self):
        r = ctl.analyze(KP, dict(ELE, max_up_deg=1, max_down_deg=0.5, stabilizer_incidence_deg=3))
        assert r["issues"]

    def test_rotation_acceleration(self):
        rot = {"v_r_m_s": 68, "mass_kg": 70000, "iy_kgm2": 3.0e6, "altitude_m": 0, "accel_min_deg_s2": 4}
        o = ctl.analyze(KP, ELE, None, rot)
        assert o["rotation_pitch_accel_deg_s2"] > 4 and not o["issues"]
        assert ctl.analyze(KP, ELE, None, dict(rot, accel_min_deg_s2=500))["issues"]
        with pytest.raises(CycleError):
            ctl.analyze(KP, ELE, None, {"v_r_m_s": 68})

    def test_bad_geometry_rejected(self):
        with pytest.raises(CycleError):
            ctl.analyze(KP, dict(ELE, chord_ratio=0.8))
        with pytest.raises(CycleError):
            ctl.analyze(KP, {k: v for k, v in ELE.items() if k != "viscous_factor"})


# ================================================================ allowables, FEA/test evidence, certification
import hashlib
import random
from aerospace import allowables as alw, fea_evidence as fea, test_evidence as tev, certification as cert
from aerospace.evidence import digest

ACCEPT = {"state": "confirmed", "source": "stress group procedure", "reaction_tolerance": 0.01, "gci_max_percent": 5,
          "min_jacobian": 0.2, "max_aspect_ratio": 10, "screening_ratio_min": 0.5, "screening_ratio_max": 3}
MAT = {"e_mpa": 71700, "nu": 0.33, "ftu_mpa": 490, "fty_mpa": 420}


def fea_ev(**kw):
    e = {"id": "F1", "solver": "solver-x", "solver_version": "1.0", "model_hash": "abc123", "load_level": "limit",
         "material": {"e_mpa": 71700, "nu": 0.33},
         "meshes": [{"element_size_mm": h, "peak_stress_mpa": 25 + 0.05 * h * h} for h in (8, 4, 2)],
         "equilibrium": {"applied_n": [0, 0, 800], "reactions_n": [0, 0, -800.2]},
         "element_quality": {"min_jacobian": 0.5, "max_aspect_ratio": 4, "failed_elements": 0}}
    e.update(kw)
    return e


class TestAllowables:
    def test_tolerance_factors_match_published_tables(self):
        for n, kb in ((10, 2.355), (20, 1.926), (30, 1.777)):
            assert alw.tolerance_factor(n, "B") == pytest.approx(kb, abs=0.002)
        for n, ka in ((10, 3.981), (20, 3.295), (30, 3.064)):
            assert alw.tolerance_factor(n, "A") == pytest.approx(ka, abs=0.003)

    def test_factor_converges_toward_the_normal_quantile_from_above(self):
        ks = [alw.tolerance_factor(n, "B") for n in (100, 2000, 200000)]
        assert ks[0] > ks[1] > ks[2] > 1.2816          # always above z(0.90), shrinking with n
        assert ks[2] == pytest.approx(1.2816, abs=0.01)

    def test_factor_matches_published_exact_values(self):
        assert alw.tolerance_factor(10, "B") == pytest.approx(2.355, abs=0.005)
        assert alw.tolerance_factor(10, "A") == pytest.approx(3.981, abs=0.01)
        assert alw.tolerance_factor(30, "B") == pytest.approx(1.777, abs=0.005)

    def test_a_basis_is_below_b_basis(self):
        r = random.Random(7)
        data = [r.gauss(560, 18) for _ in range(40)]
        assert alw.allowable_from_coupons(data, "A")["value"] < alw.allowable_from_coupons(data, "B")["value"] < 560

    def test_normality_test_passes_normal_and_fails_skewed(self):
        r = random.Random(3)
        assert alw.anderson_darling_normal([r.gauss(0, 1) for _ in range(80)]) < alw.AD_CRIT_5PCT
        assert alw.anderson_darling_normal([r.expovariate(1.0) for _ in range(80)]) > alw.AD_CRIT_5PCT

    def test_lognormal_fallback(self):
        r = random.Random(11)
        data = [math.exp(r.gauss(6.3, 0.35)) for _ in range(120)]
        out = alw.allowable_from_coupons(data, "B")
        assert out["distribution"] == "lognormal" and out["value"] > 0

    def test_too_few_or_bad_data_rejected(self):
        with pytest.raises(CycleError):
            alw.allowable_from_coupons([500.0] * 10, "B")
        with pytest.raises(CycleError):
            alw.allowable_from_coupons([500.0] * 29 + [-1.0], "B")

    def test_material_record_is_user_certified_with_provenance(self):
        r = random.Random(5)
        tests = {"ftu_mpa": [r.gauss(560, 15) for _ in range(35)], "fty_mpa": [r.gauss(490, 14) for _ in range(35)]}
        m = alw.material_from_coupons("MY-ALLOY", 2.81, 71700, tests, "B", "Lab report LR-42")
        assert m["basis"] == "user_certified" and "LR-42" in m["source"] and m["fty_mpa"] < m["ftu_mpa"]
        mat = resolve_material(m)
        assert mat.is_design_allowable
        with pytest.raises(CycleError):
            alw.material_from_coupons("X", 2.8, 70000, tests, "B", "")


class TestFeaEvidence:
    def test_observed_order_and_extrapolation(self):
        c = fea.convergence([{"element_size_mm": h, "peak_stress_mpa": 100 + 0.5 * h * h} for h in (8, 4, 2)])
        assert c["ok"] and c["p"] == pytest.approx(2.0, abs=1e-6) and c["extrapolated"] == pytest.approx(100.0, abs=1e-6)

    def test_oscillating_and_unrefined_meshes_rejected(self):
        osc = [{"element_size_mm": 8, "peak_stress_mpa": 100}, {"element_size_mm": 4, "peak_stress_mpa": 120}, {"element_size_mm": 2, "peak_stress_mpa": 105}]
        assert not fea.convergence(osc)["ok"]
        assert not fea.convergence([{"element_size_mm": 2.0, "peak_stress_mpa": 1}, {"element_size_mm": 1.95, "peak_stress_mpa": 2}, {"element_size_mm": 1.9, "peak_stress_mpa": 3}])["ok"]
        assert not fea.convergence(osc[:2])["ok"]

    def test_good_evidence_is_accepted_with_margin(self):
        v = fea.evaluate(fea_ev(), ACCEPT, MAT, 1.5, 23.27)
        assert v["verdict"] == "accepted" and v["numbers"]["ms_min"] > 5 and 0.9 < v["numbers"]["fea_to_screening_ratio"] < 1.3

    def test_reaction_imbalance_rejected(self):
        v = fea.evaluate(fea_ev(equilibrium={"applied_n": [0, 0, 800], "reactions_n": [0, 0, -700]}), ACCEPT, MAT, 1.5, 23.27)
        assert v["verdict"] == "rejected" and any("balance" in i for i in v["issues"])

    def test_material_card_mismatch_rejected(self):
        assert fea.evaluate(fea_ev(material={"e_mpa": 200000, "nu": 0.3}), ACCEPT, MAT, 1.5, 23.27)["verdict"] == "rejected"

    def test_disagreement_with_hand_calc_rejected(self):
        meshes = [{"element_size_mm": h, "peak_stress_mpa": 250 + 0.05 * h * h} for h in (8, 4, 2)]
        v = fea.evaluate(fea_ev(meshes=meshes), ACCEPT, MAT, 1.5, 23.27)
        assert v["verdict"] == "rejected" and any("screening" in i for i in v["issues"])

    def test_negative_margin_rejected(self):
        meshes = [{"element_size_mm": h, "peak_stress_mpa": 450 + 0.05 * h * h} for h in (8, 4, 2)]
        v = fea.evaluate(fea_ev(meshes=meshes), ACCEPT, MAT, 1.5, None)
        assert v["verdict"] == "rejected" and any("margin" in i for i in v["issues"])

    def test_ultimate_load_level_is_divided_by_the_factor(self):
        meshes = [{"element_size_mm": h, "peak_stress_mpa": 37.5 + 0.075 * h * h} for h in (8, 4, 2)]
        v = fea.evaluate(fea_ev(load_level="ultimate", meshes=meshes), ACCEPT, MAT, 1.5, 25.0)
        assert v["numbers"]["extrapolated_limit_stress_mpa"] == pytest.approx(25.0, abs=0.01)

    def test_poor_element_quality_rejected(self):
        v = fea.evaluate(fea_ev(element_quality={"min_jacobian": 0.05, "max_aspect_ratio": 30, "failed_elements": 2}), ACCEPT, MAT, 1.5, 23.27)
        assert v["verdict"] == "rejected"

    def test_acceptance_criteria_must_be_declared_confirmed_and_sourced(self):
        assert fea.evaluate(fea_ev(), {}, MAT, 1.5)["verdict"] == "incomplete"
        assert fea.evaluate(fea_ev(), dict(ACCEPT, state="draft"), MAT, 1.5)["verdict"] == "incomplete"
        assert fea.evaluate(fea_ev(), dict(ACCEPT, source=""), MAT, 1.5)["verdict"] == "incomplete"

    def test_incomplete_evidence_and_bad_level(self):
        assert fea.evaluate({k: v for k, v in fea_ev().items() if k != "model_hash"}, ACCEPT, MAT, 1.5)["verdict"] == "incomplete"
        assert fea.evaluate(fea_ev(load_level="maybe"), ACCEPT, MAT, 1.5)["verdict"] == "incomplete"


def test_ev(**kw):
    e = {"id": "T1", "test_type": "static_proof", "lab": {"name": "Lab A", "accreditation": "ISO17025-123"},
         "article": {"serial": "SN1", "conformity_record_id": "CR-9", "design_revision_id": "REV"},
         "report_ref": "TR-5", "report_sha256": hashlib.sha256(b"report").hexdigest(), "calibration_refs": ["CAL-1"],
         "criteria": [{"name": "tip deflection", "comparator": "<=", "target": 25.0, "unit": "mm"},
                      {"name": "failure load", "comparator": ">=", "target": 12000.0, "unit": "N"}],
         "measurements": [{"name": "tip deflection", "value": 21.5, "unit": "mm"}, {"name": "failure load", "value": 13100.0, "unit": "N"}]}
    e.update(kw)
    return e


class TestTestEvidence:
    def test_accepted_when_all_criteria_pass(self):
        assert tev.evaluate(test_ev(), "REV")["verdict"] == "accepted"

    def test_failed_criterion_is_computed_not_trusted(self):
        v = tev.evaluate(test_ev(measurements=[{"name": "tip deflection", "value": 30.0, "unit": "mm"}, {"name": "failure load", "value": 13100.0, "unit": "N"}]), "REV")
        assert v["verdict"] == "rejected" and any("tip deflection" in i for i in v["issues"])

    def test_unit_mismatch_missing_measurement_and_bad_hash(self):
        assert tev.evaluate(test_ev(measurements=[{"name": "tip deflection", "value": 21.5, "unit": "in"}, {"name": "failure load", "value": 1, "unit": "N"}]), "REV")["verdict"] != "accepted"
        assert tev.evaluate(test_ev(measurements=[{"name": "tip deflection", "value": 21.5, "unit": "mm"}]), "REV")["verdict"] != "accepted"
        assert tev.evaluate(test_ev(report_sha256="nothex"), "REV")["verdict"] != "accepted"

    def test_article_must_match_the_design_revision(self):
        assert tev.evaluate(test_ev(), "OTHER")["verdict"] != "accepted"

    def test_lab_accreditation_and_conformity_required(self):
        assert tev.evaluate(test_ev(lab={"name": "Lab"}), "REV")["verdict"] != "accepted"
        assert tev.evaluate(test_ev(article={"serial": "S", "design_revision_id": "REV"}), "REV")["verdict"] != "accepted"


class TestEvidenceInRelease:
    def _wing(self, evidence):
        s = wing_spec()
        s["fea_acceptance"] = dict(ACCEPT)
        s["evidence"] = evidence
        return s

    def test_structured_fea_accepted_gives_ready(self):
        s = wing_spec()
        s["fea_acceptance"] = dict(ACCEPT)
        rev = revision_id(s["part_type"], s["params"], s["material"])
        s["evidence"] = [{"id": "F1", "kind": "fea", "revision_id": rev, "structured": fea_ev()}]
        a = assess_design(s)
        assert a["blockers"] == [] and a["status"] == "ready_for_engineering_review"
        assert a["external_evidence"][0]["verification"].startswith("structured FEA") and a["external_evidence"][0]["numbers"]["ms_min"] > 0

    def test_structured_fea_rejected_blocks_with_reason(self):
        s = wing_spec()
        s["fea_acceptance"] = dict(ACCEPT)
        rev = revision_id(s["part_type"], s["params"], s["material"])
        s["evidence"] = [{"id": "F1", "kind": "fea", "revision_id": rev, "structured": fea_ev(equilibrium={"applied_n": [0, 0, 800], "reactions_n": [0, 0, -500]})}]
        a = assess_design(s)
        assert a["status"] != "ready_for_engineering_review" and any("balance" in b for b in a["blockers"])

    def test_bare_pass_flag_no_longer_counts(self):
        s = wing_spec()
        rev = revision_id(s["part_type"], s["params"], s["material"])
        s["evidence"] = [{"id": "F1", "kind": "fea", "result": "pass", "revision_id": rev}]
        a = assess_design(s)
        assert any("manual evidence needs" in b for b in a["blockers"]) and a["status"] != "ready_for_engineering_review"

    def test_structured_test_evidence_counts_for_test_requirement(self):
        s = {"design_id": "E", "part_type": "turbofan_engine", "params": {}, "cycle": {}}
        rev = revision_id("turbofan_engine", {}, {})
        s["evidence"] = [{"id": "T1", "kind": "test", "revision_id": rev, "structured": test_ev(article={"serial": "SN1", "conformity_record_id": "CR", "design_revision_id": rev})}]
        a = assess_design(s)
        assert not any("'test'" in b for b in a["blockers"])


def _ready_assessment():
    s = wing_spec()
    s = with_fea(s)
    return s, assess_design(s)


CERT = {"authorized_signers": {"engineering_reviewer": ["0xrev"], "certification_lead": ["0xlead"], "authority_liaison": ["0xliaison"]},
        "compliance_items": [{"id": "C1", "requirement_ref": "REF-1", "title": "static strength", "moc": ["analysis", "test"], "evidence_ids": ["FEA-1"]}]}


class TestCertification:
    def _chain(self, a):
        recs = []
        recs.append(cert.append_record(recs, a, CERT, "0xowner", "engineering_review_accepted", "0xrev", "Reviewed the assessment and evidence."))
        recs.append(cert.append_record(recs, a, CERT, "0xowner", "submitted_for_certification", "0xlead", "Submission package sent.", reference="SUB-1"))
        return recs

    def test_full_ladder_and_status(self):
        _, a = _ready_assessment()
        recs = self._chain(a)
        recs.append(cert.append_record(recs, a, CERT, "0xowner", "authority_acceptance_recorded", "0xliaison", "Authority letter received.",
                                       reference="AUTH-77", document_sha256=hashlib.sha256(b"letter").hexdigest(), authority_name="Some Authority"))
        st = cert.current_stage(recs, a, CERT)
        assert st["stage"] == "authority_acceptance_recorded" and "does not verify" in st["notice"]
        assert cert.verify_chain(recs)[0]

    def test_owner_cannot_review_their_own_design(self):
        _, a = _ready_assessment()
        c = dict(CERT, authorized_signers=dict(CERT["authorized_signers"], engineering_reviewer=["0xowner"]))
        with pytest.raises(cert.ApprovalError):
            cert.append_record([], a, c, "0xowner", "engineering_review_accepted", "0xowner", "ok")

    def test_unauthorised_signer_and_wrong_order_rejected(self):
        _, a = _ready_assessment()
        with pytest.raises(cert.ApprovalError):
            cert.append_record([], a, CERT, "0xowner", "engineering_review_accepted", "0xstranger", "ok")
        with pytest.raises(cert.ApprovalError):
            cert.append_record([], a, CERT, "0xowner", "submitted_for_certification", "0xlead", "ok")

    def test_not_ready_design_cannot_be_approved(self):
        a = assess_design(wing_spec())
        with pytest.raises(cert.ApprovalError):
            cert.append_record([], a, CERT, "0xowner", "engineering_review_accepted", "0xrev", "ok")

    def test_change_to_the_design_makes_approvals_stale(self):
        s, a = _ready_assessment()
        recs = self._chain(a)
        s2 = copy.deepcopy(s)
        s2["params"]["span_mm"] = 610
        s2 = with_fea(s2)
        a2 = assess_design(s2)
        st = cert.current_stage(recs, a2, CERT)
        assert st["stage"] == "ready_for_engineering_review" and "submitted_for_certification" in st["stale_stages"]

    def test_tampering_breaks_the_chain(self):
        _, a = _ready_assessment()
        recs = self._chain(a)
        recs[0]["statement"] = "changed afterwards"
        assert not cert.verify_chain(recs)[0]
        assert cert.current_stage(recs, a, CERT)["stage"] == "chain_invalid"

    def test_submission_needs_a_satisfied_compliance_matrix(self):
        _, a = _ready_assessment()
        c = dict(CERT, compliance_items=[{"id": "C1", "requirement_ref": "REF-1", "moc": ["test"], "evidence_ids": ["MISSING"]}])
        recs = [cert.append_record([], a, c, "0xowner", "engineering_review_accepted", "0xrev", "ok")]
        with pytest.raises(cert.ApprovalError):
            cert.append_record(recs, a, c, "0xowner", "submitted_for_certification", "0xlead", "ok")

    def test_authority_record_needs_reference_and_document_hash(self):
        _, a = _ready_assessment()
        recs = self._chain(a)
        with pytest.raises(cert.ApprovalError):
            cert.append_record(recs, a, CERT, "0xowner", "authority_acceptance_recorded", "0xliaison", "letter", reference="X", document_sha256="bad", authority_name="A")

    def test_each_stage_needs_a_different_person(self):
        _, a = _ready_assessment()
        c = dict(CERT, authorized_signers=dict(CERT["authorized_signers"], certification_lead=["0xrev"]))
        recs = [cert.append_record([], a, c, "0xowner", "engineering_review_accepted", "0xrev", "ok")]
        with pytest.raises(cert.ApprovalError):
            cert.append_record(recs, a, c, "0xowner", "submitted_for_certification", "0xrev", "ok")

    def test_certification_block_appears_in_assessment(self):
        s, _ = _ready_assessment()
        s["certification"] = CERT
        a = assess_design(s)
        assert a["certification"]["stage"] == "ready_for_engineering_review" and a["certification"]["compliance"]["open"] == 0
        a2 = assess_design(s, approvals=[])
        assert "notice" in a2["certification"]

    def test_assessment_digest_changes_when_the_outcome_changes(self):
        s, a = _ready_assessment()
        s2 = copy.deepcopy(s)
        s2["loads"]["wing_limit_lift"]["value"] = 900
        assert cert.assessment_digest(assess_design(s2)) != cert.assessment_digest(a)


# ---------------------------------------------------------------- persisted approvals (real sqlite, stubbed app db module)
def _approval_store():
    import contextlib, importlib.util, os, sqlite3, sys, tempfile, types
    path = os.path.join(tempfile.mkdtemp(), "approvals.db")
    dbm = types.ModuleType("db")

    @contextlib.contextmanager
    def get_conn():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()
    dbm.get_conn = get_conn
    saved = sys.modules.get("db")
    sys.modules["db"] = dbm
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location("aero_store_under_test", os.path.join(here, "..", "aerospace", "store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("db", None)
        else:
            sys.modules["db"] = saved
    return mod


class TestApprovalStore:
    def _spec(self):
        s, _ = _ready_assessment()
        s["certification"] = CERT
        return s

    def _sign(self, store, spec, stage, signer, **kw):
        def build(records):
            a = assess_design(spec, created_by="0xowner", approvals=records)
            return cert.append_record(records, a, spec["certification"], "0xowner", stage, signer, "Reviewed and accepted.", **kw)
        return store.append_approval("0xowner", spec["design_id"], build)

    def test_chain_persists_and_the_stage_advances(self):
        st, s = _approval_store(), self._spec()
        self._sign(st, s, "engineering_review_accepted", "0xrev")
        self._sign(st, s, "submitted_for_certification", "0xlead", reference="SUB-1")
        self._sign(st, s, "authority_acceptance_recorded", "0xliaison", reference="AUTH-1", authority_name="Authority",
                   document_sha256=hashlib.sha256(b"letter").hexdigest())
        recs = st.list_approvals("0xowner", s["design_id"])
        assert [r["stage"] for r in recs] == ["engineering_review_accepted", "submitted_for_certification", "authority_acceptance_recorded"]
        assert cert.verify_chain(recs)[0]
        assert assess_design(s, "0xowner", approvals=recs)["certification"]["stage"] == "authority_acceptance_recorded"

    def test_database_rejects_a_forked_chain(self):
        st, s = _approval_store(), self._spec()
        self._sign(st, s, "engineering_review_accepted", "0xrev")
        a = assess_design(s, created_by="0xowner", approvals=[])

        def stale_build(_records):      # a writer that read the chain before the first record existed
            return cert.append_record([], a, s["certification"], "0xowner", "engineering_review_accepted", "0xrev", "Second genesis.")
        with pytest.raises(st.ChainConflict):
            st.append_approval("0xowner", s["design_id"], stale_build)
        assert len(st.list_approvals("0xowner", s["design_id"])) == 1

    def test_rules_still_enforced_when_persisting(self):
        st, s = _approval_store(), self._spec()
        with pytest.raises(cert.ApprovalError):
            self._sign(st, s, "engineering_review_accepted", "0xowner")            # owner cannot review own design
        with pytest.raises(cert.ApprovalError):
            self._sign(st, s, "submitted_for_certification", "0xlead")              # review must come first
        assert st.list_approvals("0xowner", s["design_id"]) == []

    def test_other_owners_cannot_see_the_chain(self):
        st, s = _approval_store(), self._spec()
        self._sign(st, s, "engineering_review_accepted", "0xrev")
        assert st.list_approvals("0xsomeoneelse", s["design_id"]) == []

    def test_a_design_change_drops_the_stage(self):
        st, s = _approval_store(), self._spec()
        self._sign(st, s, "engineering_review_accepted", "0xrev")
        s2 = copy.deepcopy(s)
        s2["params"]["span_mm"] = 650
        cur = assess_design(s2, "0xowner", approvals=st.list_approvals("0xowner", s["design_id"]))["certification"]
        assert cur["stage"] != "engineering_review_accepted" and "engineering_review_accepted" in cur["stale_stages"]

    def test_tampering_with_a_stored_record_is_detected(self):
        st, s = _approval_store(), self._spec()
        self._sign(st, s, "engineering_review_accepted", "0xrev")
        recs = st.list_approvals("0xowner", s["design_id"])
        recs[0]["statement"] = "Edited after the fact."
        assert not cert.verify_chain(recs)[0]


class TestFieldPerformanceSpeed:
    """Regression: thrust vs speed is tabulated once. Calling the engine model per integration
    step took 26 s per jet assessment and would time out the API."""

    def test_engine_model_call_budget(self):
        import aerospace.fieldperf as fpm
        calls = {"n": 0}
        real = fpm.offdesign_point

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)
        fpm.offdesign_point = counting
        try:
            f = dict(FIELD, vmcg_m_s=50, reaction_time_s=2.0, delta_cd0_inoperative_engine=0.004, mu_brake_wet=0.2)
            o = fpm.analyze(KP, CYC320, f)
        finally:
            fpm.offdesign_point = real
        assert "bfl_dry_m" in o and "bfl_wet_m" in o
        assert calls["n"] < 20000

    def test_results_are_deterministic(self):
        f = dict(FIELD, vmcg_m_s=50, reaction_time_s=2.0, delta_cd0_inoperative_engine=0.004, mu_brake_wet=0.2)
        assert fp.analyze(KP, CYC320, f) == fp.analyze(KP, CYC320, f)

    def test_balanced_field_is_longer_than_all_engines_and_wet_longer_than_dry(self):
        f = dict(FIELD, vmcg_m_s=50, reaction_time_s=2.0, delta_cd0_inoperative_engine=0.004, mu_brake_wet=0.2)
        o = fp.analyze(KP, CYC320, f)
        assert o["bfl_dry_m"] > o["takeoff_distance_aeo_m"] and o["bfl_wet_m"] >= o["bfl_dry_m"]

"""The Studio manufacturability check: dfm rules on the dimensions, plus checks read from a mesh. numpy only."""
import numpy as np
import pytest

import dfm
import manufacturability as mfg


def _outward(v, t):
    """Wind every triangle of a convex mesh so its normal points away from the centre."""
    c = v.mean(axis=0)
    t = np.array(t)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    flip = np.einsum("ij,ij->i", n, v[t].mean(axis=1) - c) < 0
    t[flip] = t[flip][:, ::-1]
    return t


BOX_TRIS = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
            [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]


def box(sx, sy, sz, origin=(0.0, 0.0, 0.0)):
    x, y, z = origin
    v = np.array([[x, y, z], [x + sx, y, z], [x + sx, y + sy, z], [x, y + sy, z],
                  [x, y, z + sz], [x + sx, y, z + sz], [x + sx, y + sy, z + sz], [x, y + sy, z + sz]], float)
    return v, _outward(v, BOX_TRIS)


def two_shells(a, b):
    (va, ta), (vb, tb) = a, b
    return np.vstack([va, vb]), np.vstack([ta, tb + len(va)])


def hollow_box(outer, wall):
    vo, to = box(outer, outer, outer)
    vi, ti = box(outer - 2 * wall, outer - 2 * wall, outer - 2 * wall, origin=(wall, wall, wall))
    return np.vstack([vo, vi]), np.vstack([to, ti[:, ::-1] + len(vo)])       # inner shell faces into the void


def frustum(base, top, height):
    o = (base - top) / 2
    v = np.array([[0, 0, 0], [base, 0, 0], [base, base, 0], [0, base, 0],
                  [o, o, height], [o + top, o, height], [o + top, o + top, height], [o, o + top, height]], float)
    return v, _outward(v, BOX_TRIS)


def by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_report_shape_and_worst_first():
    r = mfg.run("flat_plate", {"length_mm": 50, "width_mm": 50, "thickness_mm": 0.3}, "6061", "cnc", mesh=box(50, 50, 0.3))
    assert set(r) == {"process", "process_label", "headline", "checks", "assumptions", "disclaimer"}
    assert r["process_label"] == "CNC machining" and r["disclaimer"]
    ranks = [mfg._RANK[c["status"]] for c in r["checks"]]
    assert ranks == sorted(ranks)
    for c in r["checks"]:
        assert {"id", "title", "status", "detail"} <= set(c)
        assert c["status"] in {"pass", "warn", "fail", "info", "not_checked"}
    assert len({c["id"] for c in r["checks"]}) == len(r["checks"])


def test_headline_counts_match_the_checks():
    r = mfg.run("flat_plate", {"length_mm": 50, "width_mm": 50, "thickness_mm": 0.3}, "6061", "cnc", mesh=box(50, 50, 0.3))
    n = {s: sum(c["status"] == s for c in r["checks"]) for s in ("fail", "warn", "pass", "not_checked")}
    assert r["headline"] == f"{n['fail']} problem{'s' if n['fail'] != 1 else ''}, {n['warn']} to review, {n['pass']} passed, {n['not_checked']} not checked."


def test_thin_wall_from_dimensions_and_from_the_mesh_agree():
    r = mfg.run("flat_plate", {"length_mm": 50, "width_mm": 50, "thickness_mm": 0.3}, "6061", "cnc", mesh=box(50, 50, 0.3))
    c = by_id(r)
    assert c["min_wall"]["status"] == "fail"
    assert c["mesh_walls"]["status"] == "fail" and "0.3" in c["mesh_walls"]["measured"]


def test_a_sturdy_part_passes_wall_and_size_and_nothing_is_called_a_pass_without_evidence():
    r = mfg.run("flat_plate", {"length_mm": 50, "width_mm": 50, "thickness_mm": 5}, "6061", "cnc", mesh=box(50, 50, 5))
    c = by_id(r)
    assert c["min_wall"]["status"] == "pass" and c["mesh_walls"]["status"] == "pass"
    assert c["mesh_walls"]["measured"].startswith("thinnest 5")
    assert c["closed"]["status"] == "pass" and c["size"]["status"] == "pass"
    assert c["access"]["status"] == "not_checked"                       # always says what it does not cover


def test_mesh_wall_is_measured_without_the_dimensions():
    # an imported part has no dimensions to read, only a mesh
    r = mfg.run("imported", {"filename": "x.stl"}, None, "fdm", mesh=box(40, 40, 0.5))
    c = by_id(r)
    assert c["mesh_walls"]["status"] == "fail"
    assert "min_wall" not in c


def test_open_mesh_is_a_problem_for_printing_and_a_warning_elsewhere():
    v, t = box(20, 20, 20)
    t = t[:-1]
    assert by_id(mfg.run("shaft", {}, None, "fdm", mesh=(v, t)))["closed"]["status"] == "fail"
    assert by_id(mfg.run("shaft", {}, None, "resin", mesh=(v, t)))["closed"]["status"] == "fail"
    assert by_id(mfg.run("shaft", {}, None, "cnc", mesh=(v, t)))["closed"]["status"] == "warn"
    assert by_id(mfg.run("shaft", {}, None, "injection_molding", mesh=(v, t)))["closed"]["status"] == "warn"


def test_size_uses_the_same_limits_as_dfm():
    for proc, dproc in (("cnc", "cnc_milling"), ("fdm", "fdm_print"), ("resin", "resin_print"), ("injection_molding", "injection_molding")):
        lim = dfm.SIZE_LIMIT_MM[dproc]
        small = by_id(mfg.run("imported", {}, None, proc, mesh=box(lim - 1, 20, 20)))["size"]
        big = by_id(mfg.run("imported", {}, None, proc, mesh=box(lim + 10, 20, 20)))["size"]
        assert small["status"] == "pass" and big["status"] == "warn", proc
    # and the dimension rules in dfm really use those numbers
    f = dfm.check("shaft", {"diameter_mm": 10, "length_mm": 230}, "fdm_print")
    assert [x for x in f if x["rule"] == "build_volume"][0]["limit"] == dfm.SIZE_LIMIT_MM["fdm_print"]
    f = dfm.check("shaft", {"diameter_mm": 10, "length_mm": 200}, "resin_print")
    assert [x for x in f if x["rule"] == "build_volume"][0]["limit"] == dfm.SIZE_LIMIT_MM["resin_print"]


def test_mesh_size_replaces_the_dimension_size_rule_instead_of_doubling_it():
    r = mfg.run("shaft", {"diameter_mm": 10, "length_mm": 230}, None, "fdm", mesh=box(10, 10, 230))
    sizes = [c for c in r["checks"] if c["title"] == "Size"]
    assert len(sizes) == 1 and sizes[0]["status"] == "warn"


def test_fdm_overhang():
    plain = by_id(mfg.run("imported", {}, None, "fdm", mesh=box(30, 30, 30)))["overhang"]
    assert plain["status"] == "pass"
    post = box(10, 10, 10, origin=(10, 10, 0))
    cap = box(30, 30, 5, origin=(0, 0, 10))
    mushroom = by_id(mfg.run("imported", {}, None, "fdm", mesh=two_shells(post, cap)))["overhang"]
    assert mushroom["status"] == "warn" and "mm2" in mushroom["measured"]


def test_molding_draft():
    straight = by_id(mfg.run("imported", {}, None, "injection_molding", mesh=box(30, 30, 10)))["draft"]
    drafted = by_id(mfg.run("imported", {}, None, "injection_molding", mesh=frustum(30, 24, 10)))["draft"]
    assert straight["status"] == "warn" and "under 1 degree" in straight["measured"]
    assert drafted["status"] == "pass"


def test_resin_trapped_cavity():
    sealed = by_id(mfg.run("imported", {}, None, "resin", mesh=hollow_box(40, 3)))
    assert sealed["drain"]["status"] == "warn" and "cm3" in sealed["drain"]["measured"]
    assert sealed["closed"]["status"] == "pass"
    solid = by_id(mfg.run("imported", {}, None, "resin", mesh=box(40, 40, 40)))
    assert solid["drain"]["status"] == "pass"


def test_hollow_box_wall_is_measured_across_the_wall_not_through_the_void():
    r = by_id(mfg.run("imported", {}, None, "resin", mesh=hollow_box(40, 3)))
    assert r["mesh_walls"]["measured"].startswith("thinnest 3")


def test_inside_out_mesh_gives_the_same_answers():
    v, t = box(30, 30, 5)
    a = by_id(mfg.run("imported", {}, None, "fdm", mesh=(v, t)))
    b = by_id(mfg.run("imported", {}, None, "fdm", mesh=(v, t[:, ::-1])))
    assert a["mesh_walls"]["status"] == b["mesh_walls"]["status"] == "pass"
    assert a["overhang"]["status"] == b["overhang"]["status"]


def test_without_a_mesh_only_dimensions_are_checked_and_the_report_says_so():
    r = mfg.run("l_bracket", {"thickness_mm": 3, "width_mm": 40, "height_mm": 40}, "6061", "fdm", mesh=None)
    ids = by_id(r)
    assert "closed" not in ids and "mesh_walls" not in ids
    assert ids["overhang"]["status"] == "not_checked"
    assert any("mesh could not be read" in a for a in r["assumptions"])


def test_sheet_metal_does_not_use_the_mesh():
    r = mfg.run("l_bracket", {"thickness_mm": 2, "width_mm": 6, "height_mm": 30, "fillet_radius_mm": 1}, "6061", "sheet_metal", mesh=box(10, 10, 2))
    c = by_id(r)
    assert "closed" not in c and c["bend_radius"]["status"] == "warn" and c["flat"]["status"] == "not_checked"
    assert not any("mesh could not be read" in a for a in r["assumptions"])


def test_a_part_that_is_the_wrong_shape_for_the_process_fails_process_fit():
    r = mfg.run("gear", {"teeth": 20}, None, "sheet_metal")
    assert by_id(r)["process_fit"]["status"] == "fail"


def test_material_rules():
    assert by_id(mfg.run("simple_box", {}, "6061", "fdm"))["material_fit"]["status"] == "fail"
    assert by_id(mfg.run("simple_box", {}, "6061", "injection_molding"))["material_fit"]["status"] == "fail"
    assert by_id(mfg.run("simple_box", {}, "steel", "resin"))["material_fit"]["status"] == "fail"
    assert by_id(mfg.run("simple_box", {}, "ABS", "injection_molding"))["material_fit"]["status"] == "pass"
    r = mfg.run("simple_box", {}, None, "fdm")
    assert any("No material is set" in a for a in r["assumptions"])


def test_molding_and_resin_dimension_rules():
    f = dfm.check("flat_plate", {"thickness_mm": 12, "width_mm": 60, "length_mm": 60}, "injection_molding", "ABS")
    assert "thick_section" in {x["rule"] for x in f}
    f = dfm.check("flat_plate", {"thickness_mm": 0.4, "width_mm": 60, "length_mm": 60}, "injection_molding", "ABS")
    assert [x for x in f if x["rule"] == "min_wall"][0]["severity"] == "error"
    f = dfm.check("flat_plate", {"thickness_mm": 0.6, "width_mm": 60, "length_mm": 60}, "resin_print")
    assert [x for x in f if x["rule"] == "min_wall"][0]["severity"] == "warning"
    f = dfm.check("flat_plate", {"thickness_mm": 0.3, "width_mm": 60, "length_mm": 60}, "resin_print")
    assert [x for x in f if x["rule"] == "min_wall"][0]["severity"] == "error"
    f = dfm.check("flange", {"outer_diameter_mm": 60, "inner_diameter_mm": 20, "thickness_mm": 20, "hole_diameter_mm": 3, "hole_count": 4}, "injection_molding")
    assert "hole_depth" in {x["rule"] for x in f}


def test_the_original_four_processes_are_unchanged_for_chat_questions():
    assert dfm.PROCESSES == ("cnc_milling", "turning", "fdm_print", "sheet_metal")
    assert {r["process"] for r in dfm.best_fit("l_bracket", {"thickness_mm": 3, "width_mm": 40, "height_mm": 40}, "6061")} <= set(dfm.PROCESSES)
    with pytest.raises(ValueError):
        dfm.check("gear", {}, "laser_sintering")


def test_check_and_check_with_passes_agree_on_the_findings():
    p = {"thickness_mm": 2, "width_mm": 6, "height_mm": 30, "fillet_radius_mm": 1, "hole_diameter_mm": 1, "hole_count": 1}
    for proc in dfm.ALL_PROCESSES:
        try:
            plain = dfm.check("l_bracket", p, proc, "6061")
        except ValueError:
            continue
        both, passed = dfm.check_with_passes("l_bracket", p, proc, "6061")
        assert plain == both
        assert all(x["severity"] == "ok" for x in passed)
        assert not ({x["rule"] for x in passed} & {x["rule"] for x in both})


def test_cnc_on_a_rotational_part_includes_turning_rules():
    r = mfg.run("shaft", {"diameter_mm": 10, "length_mm": 250}, "6061", "cnc")
    assert by_id(r)["slenderness"]["status"] == "fail"


def test_refusals_and_unknown_process():
    assert "single parts" in mfg.refusal("assembly")
    assert mfg.refusal("pcb_board") and mfg.refusal("building") and mfg.refusal("l_bracket") is None
    with pytest.raises(ValueError):
        mfg.run("l_bracket", {}, None, "laser")
    assert mfg.is_process("cnc") and not mfg.is_process("turning")


def test_the_same_part_always_gives_the_same_report():
    m = hollow_box(40, 3)
    assert mfg.run("imported", {}, None, "resin", mesh=m) == mfg.run("imported", {}, None, "resin", mesh=m)


def test_a_broken_mesh_degrades_to_dimensions_instead_of_raising():
    r = mfg.run("l_bracket", {"thickness_mm": 3, "width_mm": 40, "height_mm": 40}, "6061", "fdm",
                mesh=(np.zeros((0, 3)), np.zeros((0, 3), dtype=int)))
    assert any("could not be analysed" in a for a in r["assumptions"])

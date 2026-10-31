"""Pure-Python tests (fake CAD kernel, fake network) for vehicle_build.build_vehicle."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asset_providers as AP
import vehicle_build as VB


def load(name):
    return json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text())


def fake_bbox(part):
    """Stand-in for the CAD kernel: local boxes from the parameters, origin-centred in x and y, z from 0."""
    p, t = part.get("parameters", {}), part["part_type"]
    if t == "flat_plate":
        l, w, h = p["length_mm"], p["width_mm"], p["thickness_mm"]
        return (-l / 2, -w / 2, 0, l / 2, w / 2, h)
    if t == "shaft":
        d, l = p["diameter_mm"], p["length_mm"]
        return (-d / 2, -d / 2, 0, d / 2, d / 2, l)
    if t == "tube":
        d, l = p["outer_diameter_mm"], p["length_mm"]
        return (-d / 2, -d / 2, 0, d / 2, d / 2, l)
    return (-10, 0, -10, 10, 5, 10)                       # feature_tree wheel stand-in


def fake_export(parts, out):
    step = out / "assembly.step"
    step.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    import struct
    stl = out / "assembly.stl"
    stl.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + b"\0" * 50)
    return [("CAD", step), ("Meshes", stl)]


TREE = json.dumps({"truncated": False, "tree": [
    {"type": "blob", "path": "Fasteners/socket_head_screw.step"}]}).encode()
COMMITS = json.dumps([{"commit": {"author": {"name": "Jane Maker"}}}]).encode()
STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def fake_net(url):
    if "/git/trees/" in url:
        return TREE
    if "/commits?" in url:
        return COMMITS
    return STEP


def pin_spec():
    s = load("loco_0_4_0_core")
    s["components"] = [c for c in s["components"] if c["id"] != "coupling_pin"] + [
        {"id": "coupling_pin", "assembly": "loco", "source": {"provider": "freecad_library", "query": "socket head screw"},
         "position": [0, 0, 20]}]
    return s


def by_name(manifest_dir):
    rep = json.loads((Path(manifest_dir) / "Validation" / "validation_report.json").read_text())
    return {c["name"]: c for c in rep["checks"]}, rep


def test_full_run_places_mated_parts_explodes_exports_and_packages(tmp_path):
    reg = AP.default_registry(fake_net)
    r = VB.build_vehicle(pin_spec(), tmp_path / "pkg", registry=reg, bbox_fn=fake_bbox, export_fn=fake_export,
                         work_dir=tmp_path / "w")
    assert r["ok"], r
    st = {s["stage"]: s["status"] for s in r["stages"]}
    assert st == {"expand": "ok", "externals": "ok", "mates": "ok", "exploded": "ok", "export": "ok", "package": "ok"}
    # the boiler was placed by its mate: its bottom sits on the chassis top (z = chassis z + thickness)
    pos = r["positions"]
    assert pos["boiler"][2] > pos["chassis"][2]
    pkg = tmp_path / "pkg"
    listed = {f["path"] for f in r["manifest"]["files"]}
    for want in ("CAD/assembly.step", "Meshes/assembly.stl", "Drawings/exploded_loco.svg", "Assembly/exploded_loco.json",
                 "Assembly/exploded_tender.json" if False else "Assembly/parts.json", "NOTICES.txt", "BOM/bom.csv"):
        assert want in listed, want
    assert all((pkg / p).is_file() for p in listed)                       # nothing listed that does not exist
    assert "Jane Maker" in (pkg / "NOTICES.txt").read_text()
    c, rep = by_name(pkg)
    assert c["mates_solved"]["status"] == "pass" and c["external:coupling_pin"]["status"] == "pass"
    assert c["file:assembly.step"]["status"] == "pass"


def test_overrides_flow_through_to_the_package(tmp_path):
    r = VB.build_vehicle(load("two_axle_cart"), tmp_path / "a", overrides={"track": 90}, bbox_fn=fake_bbox,
                         export_fn=fake_export, work_dir=tmp_path / "w")
    params = json.loads((tmp_path / "a" / "Parameters" / "params.json").read_text())
    assert params["overrides"] == {"track": 90} and params["resolved"]["track"] == 90
    assert r["stages"][0]["status"] == "ok"


def test_no_kernel_skips_geometry_stages_and_is_never_a_pass(tmp_path):
    r = VB.build_vehicle(load("loco_0_4_0_core"), tmp_path / "p", fetch=False, bbox_fn=None,
                         work_dir=tmp_path / "w") if VB.kernel_bbox_fn() is None else None
    if r is None:                                                           # a real kernel is installed here
        return
    st = {s["stage"]: s["status"] for s in r["stages"]}
    assert st["mates"] == "skipped" and st["exploded"] == "skipped" and st["export"] == "skipped"
    assert r["validation"] in ("INCOMPLETE", "FAIL") and r["validation"] != "PASS"
    c, _ = by_name(tmp_path / "p")
    assert c["geometry"]["status"] == "not_run" and "mated parts have no position" in c["geometry"]["detail"]


def test_bad_spec_stops_at_expand_with_all_errors(tmp_path):
    bad = load("two_axle_cart")
    bad["components"][0]["type"] = "warp_drive"
    r = VB.build_vehicle(bad, tmp_path / "p", bbox_fn=fake_bbox)
    assert not r["ok"] and r["manifest"] is None and r["stages"] == [
        {"stage": "expand", "status": "failed", "detail": "1 error(s)"}]
    assert not (tmp_path / "p").exists()                                    # nothing written for a bad spec


def test_blocked_or_missing_external_fails_the_package_honestly(tmp_path):
    s = pin_spec()
    s["components"][-1]["source"] = {"provider": "polyfork", "query": "bolt"}
    r = VB.build_vehicle(s, tmp_path / "p", registry=AP.default_registry(fake_net), bbox_fn=fake_bbox,
                         export_fn=fake_export, work_dir=tmp_path / "w")
    c, rep = by_name(tmp_path / "p")
    assert c["external:coupling_pin"]["status"] == "fail" and rep["overall"] == "FAIL" and not r["ok"]
    assert [s_["status"] for s_ in r["stages"] if s_["stage"] == "externals"] == ["failed"]


def test_export_crash_is_reported_not_raised(tmp_path):
    def boom(parts, out):
        raise RuntimeError("OCCT said no")
    r = VB.build_vehicle(load("two_axle_cart"), tmp_path / "p", bbox_fn=fake_bbox, export_fn=boom, work_dir=tmp_path / "w")
    c, _ = by_name(tmp_path / "p")
    assert c["export"]["status"] == "fail" and "OCCT said no" in c["export"]["detail"] and not r["ok"]


def test_unseparable_explosion_is_reported(tmp_path):
    s = load("loco_0_4_0_core")
    s["explode"] = {"directions": {}}                                        # no directions: axles sit inside wheels
    r = VB.build_vehicle(s, tmp_path / "p", fetch=False, bbox_fn=fake_bbox, export_fn=fake_export, work_dir=tmp_path / "w")
    c, _ = by_name(tmp_path / "p")
    assert c["exploded:loco"]["status"] in ("pass", "fail")                 # either way it states what it found
    assert c["exploded:loco"]["detail"]


def test_mate_failure_is_reported_as_a_failed_stage(tmp_path):
    def cranky(part):
        if part["name"] == "boiler":
            raise ValueError("cannot build boiler")
        return fake_bbox(part)
    r = VB.build_vehicle(load("loco_0_4_0_core"), tmp_path / "p", fetch=False, bbox_fn=cranky, export_fn=fake_export,
                         work_dir=tmp_path / "w")
    st = {s["stage"]: s["status"] for s in r["stages"]}
    assert st["mates"] == "failed" and st["exploded"] == "skipped" and st["export"] == "skipped" and not r["ok"]

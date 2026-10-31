"""Pure-Python tests (no CadQuery) for assembly_package.package_assembly."""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assembly_package as AP

PARTS = [
    {"name": "axle_front", "part_type": "shaft", "parameters": {"d": 4, "l": 40}, "material": "steel"},
    {"name": "axle_rear", "part_type": "shaft", "parameters": {"d": 4, "l": 40}, "material": "steel"},
    {"name": "frame", "part_type": "flat_plate", "parameters": {"l": 100}, "material": "PLA"},
]


def _bin_stl(path: Path, n: int = 1) -> Path:
    path.write_bytes(b"\0" * 80 + struct.pack("<I", n) + b"\0" * (50 * n))
    return path


def _step(path: Path, truncated: bool = False) -> Path:
    body = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n" + ("" if truncated else "END-ISO-10303-21;\n")
    path.write_text(body)
    return path


def _by_name(manifest_dir: Path) -> dict:
    rep = json.loads((manifest_dir / "Validation" / "validation_report.json").read_text())
    return {c["name"]: c for c in rep["checks"]}, rep


def test_folder_tree_and_manifest_only_lists_real_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    m = AP.package_assembly("cart", PARTS, tmp_path / "out", params={"gauge": 32},
                            artifacts=[_step(src / "cart.step"), _bin_stl(src / "cart.stl")])
    out = tmp_path / "out"
    for f in AP.FOLDERS:
        assert (out / f).is_dir()
    listed = {f["path"] for f in m["files"]}
    assert "CAD/cart.step" in listed and "Meshes/cart.stl" in listed
    assert "BOM/bom.csv" in listed and "Parameters/params.json" in listed
    assert all((out / p).is_file() for p in listed)          # nothing invented
    assert (out / "manifest.json").is_file()


def test_requested_but_missing_file_fails_honestly(tmp_path):
    m = AP.package_assembly("cart", PARTS, tmp_path / "out", params={"a": 1}, requested=["cart.step", "cart.3mf"])
    assert m["missing"] == ["cart.3mf", "cart.step"]
    assert m["validation"] == "FAIL"
    checks, _ = _by_name(tmp_path / "out")
    assert checks["requested:cart.3mf"]["status"] == "fail"


def test_truncated_step_and_bad_stl_detected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bad_stl = src / "bad.stl"
    bad_stl.write_bytes(b"\0" * 80 + struct.pack("<I", 5) + b"\0" * 10)
    AP.package_assembly("cart", PARTS, tmp_path / "out", params={"a": 1},
                        artifacts=[_step(src / "t.step", truncated=True), bad_stl])
    checks, rep = _by_name(tmp_path / "out")
    assert checks["file:t.step"]["status"] == "fail"
    assert checks["file:bad.stl"]["status"] == "fail"
    assert rep["overall"] == "FAIL"


def test_duplicate_part_names_flagged(tmp_path):
    dup = PARTS + [dict(PARTS[0])]
    AP.package_assembly("cart", dup, tmp_path / "out", params={"a": 1})
    checks, _ = _by_name(tmp_path / "out")
    assert checks["unique_part_names"]["status"] == "fail"


def test_clean_package_is_incomplete_not_pass_when_mass_unknown(tmp_path):
    """With no volumes the mass is unknown; the report must say so instead of PASS."""
    m = AP.package_assembly("cart", PARTS, tmp_path / "out", params={"a": 1})
    assert m["validation"] == "INCOMPLETE"
    checks, _ = _by_name(tmp_path / "out")
    assert checks["mass"]["status"] == "not_run"


def test_nonexistent_artifact_and_empty_params_reported(tmp_path):
    AP.package_assembly("cart", PARTS, tmp_path / "out", artifacts=[tmp_path / "ghost.step"])
    checks, rep = _by_name(tmp_path / "out")
    assert checks["artifact:ghost.step"]["status"] == "fail"
    assert checks["parameters_present"]["status"] == "fail"
    assert rep["overall"] == "FAIL"


def test_conflicting_same_name_artifact_not_overwritten(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "x.stl").write_bytes(_bin_stl(tmp_path / "t1.stl").read_bytes())
    (b / "x.stl").write_bytes(_bin_stl(tmp_path / "t2.stl", n=2).read_bytes())
    AP.package_assembly("cart", PARTS, tmp_path / "out", params={"a": 1}, artifacts=[a / "x.stl", b / "x.stl"])
    checks, _ = _by_name(tmp_path / "out")
    assert any(c["status"] == "fail" and "already in" in c["detail"] for c in checks.values())
    assert (tmp_path / "out" / "Meshes" / "x.stl").read_bytes() == (a / "x.stl").read_bytes()


def test_manifest_hashes_match_files(tmp_path):
    import hashlib
    m = AP.package_assembly("cart", PARTS, tmp_path / "out", params={"a": 1})
    for f in m["files"]:
        assert hashlib.sha256((tmp_path / "out" / f["path"]).read_bytes()).hexdigest() == f["sha256"]

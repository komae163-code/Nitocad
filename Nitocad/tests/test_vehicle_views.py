from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vehicle_chat as VC
import vehicle_views as VV


def example(n):
    return json.loads((ROOT / "vehicles" / "examples" / f"{n}.json").read_text())


def box(part):
    """A stand-in for the CAD kernel: a block sized from the part's own length/diameter-like numbers."""
    nums = [float(v) for v in (part.get("parameters") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    s = max(nums + [10.0]) / 2
    return (-s, -s / 2, -s / 4, s, s / 2, s / 4)


def placed(name):
    return VC.to_chat_spec(example(name), bbox_fn=box)["spec"]["parameters"]["parts"]


def test_summary_lists_sizes_groups_and_checks():
    s = VV.summary(example("wheelbarrow"))
    assert s["ok"] and s["parts"] == 13 and set(s["groups"]) == {"tray", "frame", "wheel"}
    wd = next(x for x in s["sizes"] if x["name"] == "wheel_dia")
    assert wd["value"] == 400 and wd["min"] == 200 and wd["label"] == "wheel dia"
    assert all(c["status"] == "pass" for c in s["checks"]) and s["limits"]


def test_summary_reports_a_broken_spec_without_raising():
    assert VV.summary({"name": "x", "components": "nope"})["ok"] is False


def test_exploded_whole_vehicle_and_one_group():
    v = example("wheelbarrow")
    r = VV.exploded(v, placed("wheelbarrow"), box)
    assert r["ok"] and len(r["viewer"]["parts"]) == 13 and r["groups"] == ["tray", "frame", "wheel"]
    g = VV.exploded(v, placed("wheelbarrow"), box, group="wheel")
    assert g["ok"] and 2 <= len(g["viewer"]["parts"]) < 13


def test_exploded_unknown_group_and_too_few_parts_are_plain_errors():
    v = example("wheelbarrow")
    assert VV.exploded(v, placed("wheelbarrow"), box, group="nope")["ok"] is False
    assert VV.exploded(v, placed("wheelbarrow")[:1], box)["ok"] is False


def test_package_without_a_kernel_is_zipped_and_never_marked_pass(tmp_path):
    r = VV.build_package(example("wheelbarrow"), tmp_path, bbox_fn=None, export_fn=None, fetch=False)
    # No CadQuery here, so the stages that need it are skipped and the package says so.
    assert r["ok"] and r["validation"] != "PASS"
    names = zipfile.ZipFile(r["zip"]).namelist()
    assert any(n.endswith("manifest.json") or "manifest" in n for n in names)


def test_package_with_a_box_function_includes_exploded_views(tmp_path):
    r = VV.build_package(example("wheelbarrow"), tmp_path, bbox_fn=box, export_fn=None, fetch=False)
    assert r["ok"]
    names = zipfile.ZipFile(r["zip"]).namelist()
    assert any("exploded" in n for n in names)

import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import asset_providers as AP
import test_vehicle_build as T
import vehicle_build as VB


class FakeAssembly:
    def __init__(self):
        self.added = []

    def add(self, shape, name=None, loc=None):
        if name == "boom":
            raise ValueError("bad shape")
        self.added.append((shape, name, loc))


def fake_cq():
    cq = types.ModuleType("cadquery")
    cq.Vector = lambda *a: ("vec",) + a
    cq.Location = lambda v: ("loc", v)
    cq.importers = types.SimpleNamespace(importStep=lambda path: ("shape", path))
    return cq


class with_module:
    def __init__(self, mod):
        self.mod, self.old = mod, sys.modules.get("cadquery", "absent")

    def __enter__(self):
        sys.modules["cadquery"] = self.mod

    def __exit__(self, *a):
        if self.old == "absent":
            sys.modules.pop("cadquery", None)
        else:
            sys.modules["cadquery"] = self.old


def ext(name, pos=(1, 2, 3)):
    return {"name": name, "file": f"{name}.step", "position": list(pos)}


class MergeTests(unittest.TestCase):
    def test_each_part_is_added_at_its_position(self):
        assy = FakeAssembly()
        with with_module(fake_cq()):
            rep = VB.merge_externals(assy, [ext("a")])
        self.assertEqual([r["status"] for r in rep], ["pass"])
        self.assertEqual(assy.added, [(("shape", "a.step"), "a", ("loc", ("vec", 1.0, 2.0, 3.0)))])

    def test_a_short_position_is_padded_with_zeros(self):
        assy = FakeAssembly()
        with with_module(fake_cq()):
            VB.merge_externals(assy, [ext("a", (5,))])
        self.assertEqual(assy.added[0][2], ("loc", ("vec", 5.0, 0.0, 0.0)))

    def test_one_bad_part_does_not_stop_the_rest(self):
        assy = FakeAssembly()
        with with_module(fake_cq()):
            rep = VB.merge_externals(assy, [ext("boom"), ext("ok")])
        self.assertEqual([r["status"] for r in rep], ["fail", "pass"])
        self.assertIn("bad shape", rep[0]["detail"])
        self.assertEqual([a[1] for a in assy.added], ["ok"])

    def test_no_kernel_fails_every_part_instead_of_raising(self):
        with with_module(None):
            rep = VB.merge_externals(FakeAssembly(), [ext("a"), ext("b")])
        self.assertEqual([r["status"] for r in rep], ["fail", "fail"])

    def test_nothing_to_merge_needs_no_kernel(self):
        with with_module(None):
            self.assertEqual(VB.merge_externals(FakeAssembly(), []), [])

    def test_only_export_functions_that_accept_externals_get_them(self):
        plain = lambda parts, work: "plain"
        modern = lambda parts, work, externals=None: ("modern", externals)
        self.assertEqual(VB._call_export(plain, [], Path("."), [ext("a")]), "plain")
        self.assertEqual(VB._call_export(modern, [], Path("."), [ext("a")]), ("modern", [ext("a")]))
        self.assertEqual(VB._call_export(modern, [], Path("."), []), ("modern", None))


class BuildWiringTests(unittest.TestCase):
    def build(self, status):
        seen = {}

        def export(parts, out, externals=None):
            seen["externals"] = externals
            export.merge_report = [{"name": e["name"], "status": status, "detail": "test"} for e in externals]
            return T.fake_export(parts, out)

        export.merge_report = []
        tmp = Path(tempfile.mkdtemp())
        r = VB.build_vehicle(T.pin_spec(), tmp / "pkg", registry=AP.default_registry(T.fake_net),
                             bbox_fn=T.fake_bbox, export_fn=export, work_dir=tmp / "w")
        return r, seen, tmp / "pkg"

    def test_step_externals_reach_the_export_and_show_up_as_checks(self):
        r, seen, pkg = self.build("pass")
        self.assertEqual([e["name"] for e in seen["externals"]], ["coupling_pin"])
        self.assertTrue(seen["externals"][0]["file"].endswith(".step"))
        self.assertEqual(seen["externals"][0]["position"], [0, 0, 20])
        checks, _ = T.by_name(pkg)
        self.assertEqual(checks["external_in_step:coupling_pin"]["status"], "pass")
        self.assertTrue(r["ok"])

    def test_a_part_that_could_not_be_merged_fails_the_package(self):
        r, seen, pkg = self.build("fail")
        checks, _ = T.by_name(pkg)
        self.assertEqual(checks["external_in_step:coupling_pin"]["status"], "fail")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()

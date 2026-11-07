import json
import tempfile
import unittest
from pathlib import Path

import assembly_package as PK
import asset_providers as AP

STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
AID = "box_cardboard_a01"


def step_source():
    f = Path(tempfile.mkdtemp()) / "freecad_library__Mechanical_Parts_Bearings_608ZZ.step"
    f.write_bytes(STEP)
    rec = {"id": "Mechanical Parts/Bearings/608ZZ.step", "name": "608ZZ", "provider": "freecad_library",
           "kind": "cad_file", "format": "step", "licence": "CC-BY-3.0", "attribution_text": "608ZZ by A Maker, CC BY 3.0"}
    return {"record": rec, "file": str(f), "source": {"sha256": "x"}}


def nvidia_source():
    listing = json.dumps([{"type": "file", "size": 5, "path": f"{AID}/{p}"} for p in (
        ".metadata/LICENSE.txt", f"simready_usd/sm_{AID}_01.usd", "simready_usd/payloads/geometries.usd")]).encode()

    def get(url):
        return listing if "/tree/main/" in url else b"DATA " + url.encode()

    n = AP.NvidiaSimReady(http_get=get)
    rec = AP.AssetRecord(id=AID, name=AID, provider=n.name, kind="mesh", format="usd", licence="CC-BY-4.0", url="u",
                         attribution_text="NVIDIA SimReady Assets, CC BY 4.0")
    return n.fetch(rec, Path(tempfile.mkdtemp()))


def package(sources, parts):
    out = Path(tempfile.mkdtemp()) / "out"
    return out, PK.package_assembly("demo", parts, out, params={"a": 1}, sources=sources)


def statuses(out):
    rep = json.loads((out / "Validation" / "validation_report.json").read_text())
    return {c["name"]: c["status"] for c in rep["checks"]}


class PackageExternalsTests(unittest.TestCase):
    def test_step_part_is_delivered_with_a_relative_path(self):
        src = step_source()
        name = Path(src["file"]).name
        out, m = package([src], [AP.as_part(src, "bearing_1")])
        self.assertEqual((out / "External" / name).read_bytes(), STEP)
        self.assertIn("External/" + name, {f["path"] for f in m["files"]})
        part = json.loads((out / "Assembly" / "parts.json").read_text())[0]
        self.assertEqual(part["parameters"]["file"], "External/" + name)
        listed = json.loads((out / "Assembly" / "sources.json").read_text())[0]
        self.assertEqual(listed["file"], "External/" + name)
        self.assertEqual(statuses(out)["external_file:608ZZ"], "pass")

    def test_multi_file_asset_keeps_its_folder_layout_and_licence(self):
        src = nvidia_source()
        out, m = package([src], [AP.as_part(src, "cargo_box")])
        base = out / "External" / AID
        self.assertTrue((base / "simready_usd" / "payloads" / "geometries.usd").is_file())
        self.assertTrue((base / ".metadata" / "LICENSE.txt").is_file())
        part = json.loads((out / "Assembly" / "parts.json").read_text())[0]
        self.assertEqual(part["parameters"]["file"], f"External/{AID}/simready_usd/sm_{AID}_01.usd")

    def test_missing_download_fails_the_package(self):
        src = step_source()
        Path(src["file"]).unlink()
        out, m = package([src], [AP.as_part(src, "bearing_1")])
        self.assertEqual(m["validation"], "FAIL")
        self.assertEqual(statuses(out)["external_file:608ZZ"], "fail")

    def test_unsafe_paths_are_refused_and_nothing_escapes(self):
        root = Path(tempfile.mkdtemp()) / "asset"
        (root / "simready_usd").mkdir(parents=True)
        entry = root / "simready_usd" / "x.usd"
        entry.write_bytes(b"x")
        src = {"record": {"id": "asset", "name": "asset", "provider": "nvidia_simready", "kind": "mesh",
                          "licence": "CC-BY-4.0", "attribution_text": "a"},
               "file": str(entry), "files": [{"path": "../evil.txt"}]}
        out, m = package([src], [{"name": "asset", "part_type": "external_mesh", "parameters": {}}])
        self.assertEqual(statuses(out)["external_file:asset"], "fail")
        self.assertFalse((out.parent / "evil.txt").exists() or (out / "External" / "evil.txt").exists())

    def test_recipe_only_source_has_nothing_to_copy(self):
        src = {"record": {"id": "HexNut", "name": "HexNut", "provider": "cq_warehouse", "kind": "parametric",
                          "licence": "Apache-2.0", "attribution_text": "cq_warehouse"},
               "file": None, "recipe": "from cq_warehouse.fastener import HexNut"}
        out, m = package([src], [{"name": "nut", "part_type": "external_parametric", "parameters": {}}])
        self.assertFalse((out / "External").exists())
        self.assertFalse([n for n in statuses(out) if n.startswith("external_file")])


if __name__ == "__main__":
    unittest.main()

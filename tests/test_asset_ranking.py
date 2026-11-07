import json
import tempfile
import unittest
from pathlib import Path

import asset_providers as AP

A = "box_cardboard_a01"
TREE = [{"type": "file", "path": f"{A}/{p}", "size": 10} for p in (
    ".metadata/LICENSE.txt", f"simready_usd/sm_{A}_01.usd", "simready_usd/payloads/geometries.usd")]


class Fake:
    def __init__(self, dirs=()):
        self.calls, self.dirs = [], list(dirs)

    def __call__(self, url):
        self.calls.append(url)
        if "/tree/main/" in url:
            return json.dumps(TREE).encode()
        if "/tree/main" in url:
            return json.dumps([{"type": "directory", "path": d} for d in self.dirs]).encode()
        return b"DATA " + url.encode()


class AssetRankingTests(unittest.TestCase):
    def test_closest_name_comes_first(self):
        n = AP.NvidiaSimReady(http_get=Fake(["airSystem_air_filter_box_a01", "box_blue_a01", A]))
        self.assertEqual(n.search("box")[0].id, "box_blue_a01")
        self.assertEqual(n.search("cardboard box")[0].id, A)

    def test_no_double_extension(self):
        rec = AP.AssetRecord(id="Mechanical Parts/Bearings/Ball bearing 608.step", name="Ball bearing 608",
                             provider="freecad_library", kind="cad_file", format="step", licence="CC-BY-3.0", url="u")
        self.assertTrue(AP._cache_name(rec).endswith("608.step"))
        self.assertNotIn(".step.step", AP._cache_name(rec))

    def test_second_fetch_reads_the_disk(self):
        get = Fake([A])
        n = AP.NvidiaSimReady(http_get=get)
        rec = n.search(A)[0]
        d = Path(tempfile.mkdtemp())
        first = n.fetch(rec, d)
        calls = len(get.calls)
        second = n.fetch(rec, d)
        self.assertEqual(len(get.calls), calls)
        self.assertTrue(second.get("cached"))
        self.assertEqual(first["file"], second["file"])

    def test_unsafe_asset_id_is_refused(self):
        n = AP.NvidiaSimReady(http_get=Fake())
        rec = AP.AssetRecord(id="../x", name="x", provider=n.name, kind="mesh", format="usd",
                             licence="CC-BY-4.0", url="u")
        with self.assertRaises(AP.FetchError):
            n.fetch(rec, Path(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()

"""Pure-Python tests (no network, no CadQuery) for asset_providers and its packager hook."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asset_providers as AP
import assembly_package as PK

TREE = json.dumps([
    {"type": "directory", "path": "engineComponent_crankshaft_b01"},
    {"type": "directory", "path": "socket_b01"},
    {"type": "directory", "path": "food_produce_banana_j01"},
    {"type": "file", "path": "README.md"},
    {"type": "directory", "path": "../evil"},
]).encode()


def fake_get(calls):
    def get(url):
        calls.append(url)
        return TREE if "/api/datasets/" in url else b"#usda 1.0\n(fake)"
    return get


def test_search_finds_editable_parts_first_and_splits_camel_case():
    reg = AP.default_registry(fake_get([]))
    res = reg.search("bearing")
    assert res["results"] and all(r.kind == "parametric" for r in res["results"])
    assert any("Polyfork" in n or "polyfork" in n for n in res["notes"])          # blocked provider is reported, not hidden
    crank = reg.search("crankshaft")["results"]
    assert [r.name for r in crank] == ["engineComponent_crankshaft_b01"] and crank[0].kind == "mesh"


def test_unsafe_index_entries_are_ignored():
    names = [r.name for r in AP.NvidiaSimReady(fake_get([])).search("evil")]
    assert names == []


def test_fetch_writes_file_and_source_sidecar_with_licence(tmp_path):
    reg = AP.default_registry(fake_get([]))
    rec = reg.search("socket", kinds=("mesh",))["results"][0]
    got = reg.fetch(rec, tmp_path)
    f = Path(got["file"])
    assert f.is_file() and f.name.endswith(".usd")
    side = json.loads(Path(str(f) + ".source.json").read_text())
    assert side["licence"] == "CC-BY-4.0" and len(side["sha256"]) == 64 and "NVIDIA" in side["attribution_text"]


def test_polyfork_is_blocked_even_if_called_directly(tmp_path):
    p = AP.Polyfork()
    rec = AP.AssetRecord(id="x", name="x", provider="polyfork", kind="mesh", format="glb", licence="UNCONFIRMED",
                         url="https://polyfork.dev/cdn/x.glb")
    try:
        p.fetch(rec, tmp_path)
    except AP.LicenceBlocked as e:
        assert "licence" in str(e)
    else:
        raise AssertionError("must be blocked")
    assert not list(tmp_path.iterdir())                                           # nothing downloaded


def test_default_http_blocks_non_https_and_other_hosts():
    for url in ("http://huggingface.co/x", "https://evil.example/x", "file:///etc/passwd"):
        try:
            AP.default_http_get(url, allowed_hosts=("huggingface.co",))
        except AP.FetchError:
            continue
        raise AssertionError(url)


def test_parametric_fetch_returns_recipe_not_a_file(tmp_path):
    reg = AP.default_registry()
    rec = reg.search("socket head")["results"][0]
    got = reg.fetch(rec, tmp_path)
    assert got["file"] is None and got["recipe"].startswith("from cq_warehouse.fastener import")


def test_as_part_marks_meshes_reference_only(tmp_path):
    reg = AP.default_registry(fake_get([]))
    rec = reg.search("socket", kinds=("mesh",))["results"][0]
    part = AP.as_part(reg.fetch(rec, tmp_path), "decor_socket")
    assert part["reference_only"] is True and part["part_type"] == "external_mesh" and part["material"] is None


def test_packager_writes_notices_and_flags_reference_meshes(tmp_path):
    reg = AP.default_registry(fake_get([]))
    rec = reg.search("crankshaft")["results"][0]
    fetched = reg.fetch(rec, tmp_path / "cache")
    parts = [{"name": "decor", "part_type": "external_mesh", "parameters": {}, "material": None}]
    m = PK.package_assembly("demo", parts, tmp_path / "out", params={"a": 1}, sources=[fetched])
    out = tmp_path / "out"
    assert "CC BY 4.0" in (out / "NOTICES.txt").read_text()
    rep = json.loads((out / "Validation" / "validation_report.json").read_text())
    c = {x["name"]: x for x in rep["checks"]}
    assert c["external_reference_meshes"]["status"] == "not_run"
    assert {"NOTICES.txt", "Assembly/sources.json"} <= {f["path"] for f in m["files"]}


def test_packager_fails_on_unconfirmed_licence(tmp_path):
    fetched = {"record": {"id": "x", "name": "x", "provider": "p", "kind": "cad_file", "format": "step",
                          "licence": "UNCONFIRMED", "attribution_text": ""}, "file": None}
    m = PK.package_assembly("demo", [{"name": "a", "part_type": "x", "parameters": {}}], tmp_path / "o",
                            params={"a": 1}, sources=[fetched])
    assert m["validation"] == "FAIL"


# ---- FreeCAD-library and the gated sources -----------------------------------------------------------
STEP_BODY = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
GH_TREE = json.dumps({"truncated": False, "tree": [
    {"type": "blob", "path": "Mechanical Parts/Bearings/bearing_608.step"},
    {"type": "blob", "path": "Mechanical Parts/Fasteners/hex_bolt_M6.stp"},
    {"type": "blob", "path": "Mechanical Parts/Fasteners/readme.md"},
    {"type": "blob", "path": "Mechanical Parts/../../etc/evil.step"},
    {"type": "tree", "path": "Mechanical Parts"},
]}).encode()
GH_COMMITS = json.dumps([
    {"commit": {"author": {"name": "Jane Maker"}}},
    {"commit": {"author": {"name": "Jane Maker"}}},
    {"commit": {"author": {"name": "Raj Patel"}}},
]).encode()


def gh_get(calls, commits=GH_COMMITS, body=STEP_BODY, tree=GH_TREE):
    def get(url):
        calls.append(url)
        if "/git/trees/" in url:
            return tree
        if "/commits?" in url:
            if commits is None:
                raise OSError("rate limited")
            return commits
        return body
    return get


def test_freecad_search_returns_only_safe_step_files():
    recs = AP.FreeCADLibrary(gh_get([])).search("bearing")
    assert [r.name for r in recs] == ["bearing_608"] and recs[0].kind == "cad_file" and recs[0].format == "step"
    assert recs[0].licence == "CC-BY-3.0"
    assert AP.FreeCADLibrary(gh_get([])).search("evil") == []
    assert [r.name for r in AP.FreeCADLibrary(gh_get([])).search("bolt")] == ["hex_bolt_M6"]     # .stp counts, readme.md does not


def test_freecad_fetch_resolves_authors_into_credit_line(tmp_path):
    calls = []
    p = AP.FreeCADLibrary(gh_get(calls))
    got = p.fetch(p.search("bearing")[0], tmp_path)
    assert "Jane Maker, Raj Patel" in got["source"]["attribution_text"] and "CC BY 3.0" in got["source"]["attribution_text"]
    assert Path(got["file"]).read_bytes() == STEP_BODY
    assert any("raw.githubusercontent.com" in c and "Mechanical%20Parts" in c for c in calls)


def test_freecad_unresolved_authors_fail_closed_in_the_package(tmp_path):
    p = AP.FreeCADLibrary(gh_get([], commits=None))               # commits API fails
    got = p.fetch(p.search("bearing")[0], tmp_path / "c")
    assert got["source"]["attribution_text"].startswith("UNRESOLVED")
    m = PK.package_assembly("demo", [{"name": "b", "part_type": "external_cad_file", "parameters": {}}],
                            tmp_path / "o", params={"a": 1}, sources=[got])
    assert m["validation"] == "FAIL"


def test_html_error_page_is_not_accepted_as_step(tmp_path):
    p = AP.FreeCADLibrary(gh_get([], body=b"<html>404 not found</html>"))
    try:
        p.fetch(p.search("bearing")[0], tmp_path)
    except AP.FetchError as e:
        assert "not a STEP" in str(e)
    else:
        raise AssertionError("must reject")
    assert not list(tmp_path.glob("*.step"))


def test_truncated_index_is_reported_in_registry_notes():
    tree = json.dumps({"truncated": True, "tree": []}).encode()
    reg = AP.Registry([AP.FreeCADLibrary(gh_get([], tree=tree))])
    assert any("truncated" in n for n in reg.search("bearing")["notes"])


def test_gated_sources_are_listed_with_reasons_and_cannot_fetch(tmp_path):
    reg = AP.default_registry(gh_get([]))
    notes = " | ".join(reg.search("bearing")["notes"])
    for name in ("polyfork", "openbuilds", "kicad_3d", "traceparts"):
        assert name in notes
    for prov in (AP.OpenBuilds(), AP.KiCad3D(), AP.TraceParts()):
        rec = AP.AssetRecord(id="x", name="x", provider=prov.name, kind="cad_file", format="step",
                             licence=prov.licence, url="https://example.com/x.step")
        try:
            prov.fetch(rec, tmp_path)
        except AP.LicenceBlocked:
            continue
        raise AssertionError(prov.name)
    assert not list(tmp_path.iterdir())


def test_editable_parts_rank_above_cad_files_above_meshes():
    reg = AP.default_registry(gh_get([]))
    kinds = [r.kind for r in reg.search("bearing")["results"]]
    assert kinds == sorted(kinds, key=lambda k: {"parametric": 0, "cad_file": 1, "mesh": 2}[k])
    assert "parametric" in kinds and "cad_file" in kinds

"""GLB, 3MF and URDF writers and the mesh maths behind them. Pure numpy, no CadQuery."""
import json
import struct
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pytest

import mesh_formats as mf


def box(sx=10.0, sy=10.0, sz=10.0, origin=(0.0, 0.0, 0.0), shared=True):
    """A closed, outward wound box. shared=False gives one vertex per face corner like a B-rep tessellation."""
    x0, y0, z0 = origin
    v = np.array([[x0, y0, z0], [x0 + sx, y0, z0], [x0 + sx, y0 + sy, z0], [x0, y0 + sy, z0],
                  [x0, y0, z0 + sz], [x0 + sx, y0, z0 + sz], [x0 + sx, y0 + sy, z0 + sz], [x0, y0 + sy, z0 + sz]], float)
    t = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    if shared:
        return v, t
    return v[t].reshape(-1, 3), np.arange(36).reshape(-1, 3)


def read_glb(path):
    data = open(path, "rb").read()
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    jlen, jtype = struct.unpack("<II", data[12:20])
    assert jtype == 0x4E4F534A and jlen % 4 == 0
    doc = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack("<II", data[20 + jlen:28 + jlen])
    assert btype == 0x004E4942 and blen % 4 == 0
    return doc, data[28 + jlen:28 + jlen + blen]


def test_weld_merges_shared_corners_and_drops_degenerate_triangles():
    v, t = box(shared=False)
    wv, wt = mf.weld(v, t)
    assert len(wv) == 8 and len(wt) == 12
    # a triangle with two identical corners disappears
    v2 = np.vstack([wv, wv[:1]])
    t2 = np.vstack([wt, [[0, 8, 1]]])
    assert len(mf.weld(v2, t2)[1]) == 12


def test_edge_report_closed_and_open():
    v, t = box()
    assert mf.edge_report(t) == {"edges": 18, "open": 0, "non_manifold": 0}
    assert mf.edge_report(t[:-1])["open"] == 3     # one triangle removed leaves a hole


def test_stl_round_trip_binary_and_ascii():
    v, t = box(sx=20, sy=5, sz=3)
    data = mf.stl_bytes(v, t)
    rv, rt = mf.read_stl(data)
    assert len(rv) == 8 and len(rt) == 12
    assert np.allclose(rv.max(axis=0) - rv.min(axis=0), [20, 5, 3], atol=1e-4)
    tri = v[t]
    ascii_stl = "solid x\n" + "".join(
        "facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {p[0]} {p[1]} {p[2]}\n" for p in tr) + "endloop\nendfacet\n"
        for tr in tri) + "endsolid x\n"
    av, at = mf.read_stl(ascii_stl.encode())
    assert len(av) == 8 and len(at) == 12


def test_stl_that_is_not_a_mesh_is_refused():
    with pytest.raises(mf.MeshError):
        mf.read_stl(b"not a mesh at all, just some text")


def test_mass_properties_of_a_cube():
    v, t = box(10, 10, 10, origin=(5, 5, 5))
    p = mf.mass_properties(v, t)
    assert p["volume_mm3"] == pytest.approx(1000.0)
    assert np.allclose(p["centroid_mm"], (10, 10, 10))
    # solid cube about its centre: I = m s^2 / 6, with unit density m = V
    expected = 1000.0 * 100.0 / 6.0
    assert np.allclose(np.diag(p["inertia_mm5"]), expected)
    assert np.allclose(p["inertia_mm5"] - np.diag(np.diag(p["inertia_mm5"])), 0, atol=1e-6)


def test_mass_properties_of_a_box_has_the_right_axes():
    v, t = box(20, 10, 4)
    p = mf.mass_properties(v, t)
    m = 20 * 10 * 4
    ixx = m * (10 ** 2 + 4 ** 2) / 12
    izz = m * (20 ** 2 + 10 ** 2) / 12
    assert p["inertia_mm5"][0, 0] == pytest.approx(ixx)
    assert p["inertia_mm5"][2, 2] == pytest.approx(izz)


def test_inside_out_mesh_is_flipped_not_refused():
    v, t = box()
    p = mf.mass_properties(v, t[:, ::-1])
    assert p["volume_mm3"] == pytest.approx(1000.0)


def test_glb_structure_units_and_axes():
    v, t = box(10, 20, 30, shared=False)
    out = mf.write_glb(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "a.glb", [("brk", v, t)])
    doc, blob = read_glb(out)
    assert doc["asset"]["version"] == "2.0"
    assert [n["name"] for n in doc["nodes"]] == ["brk"]
    prim = doc["meshes"][0]["primitives"][0]
    pos = doc["accessors"][prim["attributes"]["POSITION"]]
    # CAD z (30 mm) becomes glTF y (0.03 m), CAD y (20 mm) becomes glTF -z
    assert pos["max"][1] == pytest.approx(0.03, abs=1e-6) and pos["min"][1] == pytest.approx(0.0, abs=1e-6)
    assert pos["min"][2] == pytest.approx(-0.02, abs=1e-6) and pos["max"][2] == pytest.approx(0.0, abs=1e-6)
    assert pos["max"][0] == pytest.approx(0.01, abs=1e-6)
    idx = doc["accessors"][prim["indices"]]
    assert idx["count"] == 36 and idx["componentType"] == 5125
    for view in doc["bufferViews"]:
        assert view["byteOffset"] % 4 == 0 and view["byteOffset"] + view["byteLength"] <= len(blob)
    # the stored positions really are what the accessor says
    pv = doc["bufferViews"][pos["bufferView"]]
    arr = np.frombuffer(blob, dtype="<f4", count=pos["count"] * 3, offset=pv["byteOffset"]).reshape(-1, 3)
    assert np.allclose(arr.max(axis=0), pos["max"], atol=1e-6)
    # normals are unit length
    nor = doc["accessors"][prim["attributes"]["NORMAL"]]
    nv = doc["bufferViews"][nor["bufferView"]]
    narr = np.frombuffer(blob, dtype="<f4", count=nor["count"] * 3, offset=nv["byteOffset"]).reshape(-1, 3)
    assert np.allclose(np.linalg.norm(narr, axis=1), 1.0, atol=1e-5)


def test_glb_keeps_winding_so_faces_point_outward():
    v, t = box(shared=False)
    out = mf.write_glb(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "w.glb", [("p", v, t)])
    doc, blob = read_glb(out)
    prim = doc["meshes"][0]["primitives"][0]
    pa, ia = doc["accessors"][prim["attributes"]["POSITION"]], doc["accessors"][prim["indices"]]
    pv, iv = doc["bufferViews"][pa["bufferView"]], doc["bufferViews"][ia["bufferView"]]
    pos = np.frombuffer(blob, dtype="<f4", count=pa["count"] * 3, offset=pv["byteOffset"]).reshape(-1, 3).astype(float)
    idx = np.frombuffer(blob, dtype="<u4", count=ia["count"], offset=iv["byteOffset"]).reshape(-1, 3)
    centre = pos.mean(axis=0)
    tri = pos[idx]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    outward = np.einsum("ij,ij->i", n, tri.mean(axis=1) - centre)
    assert (outward > 0).all()


def test_glb_several_parts_become_several_nodes_and_flat_shading_works():
    a, b = box(), box(origin=(20, 0, 0))
    out = mf.write_glb(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "m.glb",
                       [("plate", *a), ("bracket", *b)], flat=True)
    doc, _ = read_glb(out)
    assert [n["name"] for n in doc["nodes"]] == ["plate", "bracket"]
    assert len(doc["meshes"]) == 2 and len(doc["scenes"][0]["nodes"]) == 2


def test_glb_with_nothing_in_it_is_refused():
    with pytest.raises(mf.MeshError):
        mf.write_glb(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "e.glb", [])


def test_3mf_package_is_valid_and_welded():
    v, t = box(10, 20, 30, shared=False)
    out = mf.write_3mf(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "a.3mf", [("part <1>", v, t)])
    with zipfile.ZipFile(out) as z:
        assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        ET.fromstring(z.read("[Content_Types].xml"))
        ET.fromstring(z.read("_rels/.rels"))
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    assert root.get("unit") == "millimeter"
    obj = root.find("m:resources/m:object", ns)
    assert obj.get("name") == "part <1>"                      # special characters survive escaping
    assert len(obj.findall("m:mesh/m:vertices/m:vertex", ns)) == 8
    assert len(obj.findall("m:mesh/m:triangles/m:triangle", ns)) == 12
    assert len(root.findall("m:build/m:item", ns)) == 1
    nverts = 8
    for tri in obj.findall("m:mesh/m:triangles/m:triangle", ns):
        assert all(0 <= int(tri.get(k)) < nverts for k in ("v1", "v2", "v3"))


def test_urdf_zip_has_mass_inertia_and_mesh():
    v, t = box(10, 10, 10, shared=False)
    out = mf.write_urdf_zip(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "a.zip", "My Bracket!", v, t,
                            density_g_cm3=2.7, material="6061-T6")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names == ["My_Bracket/urdf/My_Bracket.urdf", "My_Bracket/meshes/My_Bracket.stl", "My_Bracket/README.txt"]
        root = ET.fromstring(z.read("My_Bracket/urdf/My_Bracket.urdf"))
        stl = z.read("My_Bracket/meshes/My_Bracket.stl")
    assert root.get("name") == "My_Bracket"
    link = root.find("link")
    mass = float(link.find("inertial/mass").get("value"))
    assert mass == pytest.approx(0.0027, rel=1e-4)                     # 1 cm3 of 2.7 g/cm3
    ixx = float(link.find("inertial/inertia").get("ixx"))
    assert ixx == pytest.approx(0.0027 * 0.01 ** 2 / 6, rel=1e-4)      # m s^2 / 6 in kg m^2
    origin = [float(x) for x in link.find("inertial/origin").get("xyz").split()]
    assert origin == pytest.approx([0.005, 0.005, 0.005], abs=1e-9)
    assert link.find("visual/geometry/mesh").get("scale") == "0.001 0.001 0.001"
    assert link.find("visual/geometry/mesh").get("filename") == "../meshes/My_Bracket.stl"
    assert len(mf.read_stl(stl)[1]) == 12


def test_urdf_without_a_material_says_the_density_was_assumed():
    v, t = box()
    out = mf.write_urdf_zip(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "a.zip", "x", v, t, density_g_cm3=None)
    with zipfile.ZipFile(out) as z:
        urdf = z.read("x/urdf/x.urdf").decode()
        readme = z.read("x/README.txt").decode()
    assert "No material was set" in urdf and "No material was set" in readme
    assert float(ET.fromstring(urdf).find("link/inertial/mass").get("value")) == pytest.approx(0.0027, rel=1e-4)


def test_urdf_needs_a_closed_mesh():
    v, t = box()
    with pytest.raises(mf.MeshError):
        mf.write_urdf_zip(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "a.zip", "x", v, t[:-1], density_g_cm3=1.0)


def test_safe_name():
    assert mf.safe_name("Gear <20 teeth>") == "Gear_20_teeth"
    assert mf.safe_name("???") == "part"

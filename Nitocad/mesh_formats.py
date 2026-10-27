"""
Mesh file formats the exporters add on top of STEP, STL, IGES, DXF and PDF:

    glb   binary glTF 2.0, for web viewers, AR and game engines
    3mf   3D Manufacturing Format, for slicers and print services
    urdf  a zip with a URDF robot description, its STL mesh and a README, for ROS and simulators

Everything here works on a plain triangle mesh (a float array of vertices and an int array of
triangles, in millimetres) and needs only numpy and the standard library, so it can be tested without
CadQuery. The one function that touches CadQuery, mesh_from_shape, imports it when it is called.

Units and axes, because every format disagrees:
  * The mesh is in millimetres with Z up, like the CAD model.
  * GLB is metres with Y up. Positions are scaled by 0.001 and rotated about X so the part stands up
    the way it did in the CAD window.
  * 3MF states its unit in the file, so it stays in millimetres.
  * URDF is metres and kilograms. The link keeps the CAD axes (ROS is Z up), and the STL keeps
    millimetres with a scale of 0.001 on the mesh tag.
"""
from __future__ import annotations

import io
import json
import math
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import quoteattr

import numpy as np

Mesh = tuple[np.ndarray, np.ndarray]
NamedMesh = tuple[str, np.ndarray, np.ndarray]

# Used when a part has no material, so a URDF still carries a mass. Said plainly in the file.
ASSUMED_DENSITY_G_CM3 = 2.70


class MeshError(ValueError):
    """The mesh cannot be turned into the requested file. The message is safe to show."""


# ------------------------------------------------------------------ building a mesh ---

def mesh_from_shape(obj: Any, tolerance: float = 0.1, angular_tolerance: float = 0.1) -> Mesh:
    """Triangles for a CadQuery Workplane, Shape or Assembly. Vertices are not shared between faces,
    so call weld() before anything that needs a closed surface."""
    import cadquery as cq

    if isinstance(obj, cq.Workplane):
        vals = obj.vals()
        shape = vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    elif hasattr(obj, "toCompound"):
        shape = obj.toCompound()
    else:
        shape = obj
    verts, tris = shape.tessellate(tolerance, angular_tolerance)
    if not verts or not tris:
        raise MeshError("The part has no surface to export.")
    v = np.array([(p.x, p.y, p.z) for p in verts], dtype=np.float64)
    t = np.array(tris, dtype=np.int64)
    return v, t


def weld(verts: np.ndarray, tris: np.ndarray, decimals: int = 4) -> Mesh:
    """Merge vertices that sit in the same place (to `decimals` places of a millimetre) and drop
    triangles that collapse to a line or a point."""
    if len(verts) == 0 or len(tris) == 0:
        return verts, tris
    keys = np.round(verts, decimals)
    # -0.0 and 0.0 must hash the same
    keys = keys + 0.0
    uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    t = inverse[tris]
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[ok]
    if len(t):
        a, b, c = uniq[t[:, 0]], uniq[t[:, 1]], uniq[t[:, 2]]
        area2 = np.linalg.norm(np.cross(b - a, c - a), axis=1)
        t = t[area2 > 1e-12]
    return uniq.astype(np.float64), t.astype(np.int64)


def read_stl(data: bytes) -> Mesh:
    """Triangles from a binary or ASCII STL, welded."""
    if len(data) < 15:
        raise MeshError("The STL file is empty.")
    n = struct.unpack("<I", data[80:84])[0] if len(data) >= 84 else -1
    if len(data) >= 84 and 84 + 50 * n == len(data) and n > 0:
        rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n, offset=84)
        tri_pts = rec["v"].astype(np.float64).reshape(-1, 3)
    else:
        text = data.decode("ascii", errors="ignore")
        nums = re.findall(r"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", text)
        if not nums or len(nums) % 3:
            raise MeshError("The STL file could not be read.")
        tri_pts = np.array(nums, dtype=np.float64)
    if not np.isfinite(tri_pts).all():
        raise MeshError("The STL file has invalid numbers.")
    verts = tri_pts
    tris = np.arange(len(verts), dtype=np.int64).reshape(-1, 3)
    return weld(verts, tris)


def stl_bytes(verts: np.ndarray, tris: np.ndarray, header: str = "NitoCAD") -> bytes:
    """A binary STL."""
    tri_pts = verts[tris]
    normals = np.cross(tri_pts[:, 1] - tri_pts[:, 0], tri_pts[:, 2] - tri_pts[:, 0])
    ln = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, ln, out=np.zeros_like(normals), where=ln > 0)
    out = io.BytesIO()
    out.write(header.encode("ascii", "replace")[:80].ljust(80, b" "))
    out.write(struct.pack("<I", len(tris)))
    rec = np.zeros(len(tris), dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"] = normals
    rec["v"] = tri_pts
    out.write(rec.tobytes())
    return out.getvalue()


def edge_report(tris: np.ndarray) -> dict[str, int]:
    """How the triangles share edges, on a welded mesh. A closed surface has every edge shared by exactly
    two triangles. `open` counts edges used once (a hole or a gap), `non_manifold` edges used three or
    more times."""
    if len(tris) == 0:
        return {"edges": 0, "open": 0, "non_manifold": 0}
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e.sort(axis=1)
    n = int(e.max()) + 1
    key = e[:, 0].astype(np.int64) * n + e[:, 1]
    _, counts = np.unique(key, return_counts=True)
    return {"edges": int(len(counts)), "open": int((counts == 1).sum()), "non_manifold": int((counts > 2).sum())}


# ------------------------------------------------------------------ mass properties ---

def mass_properties(verts: np.ndarray, tris: np.ndarray) -> dict[str, Any]:
    """Volume, centroid and inertia of a closed triangle mesh, for unit density.

    Returns volume_mm3, centroid_mm (x, y, z) and inertia_mm5, the 3x3 inertia tensor about the centroid
    for a density of 1 (multiply by density to get mass units). The mesh must be closed and consistently
    wound, which edge_report() can confirm. Raises MeshError when it has no volume."""
    if len(tris) == 0:
        raise MeshError("The part has no surface.")
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    d = np.einsum("ij,ij->i", a, np.cross(b, c))          # 6 x the signed volume of each tetrahedron with the origin
    vol = float(d.sum() / 6.0)
    sign = 1.0
    if vol < 0:                                           # wound inside out: flip it rather than refuse
        sign, vol = -1.0, -vol
    d = d * sign
    if vol < 1e-9:
        raise MeshError("The part has no volume.")
    v_t = d / 6.0
    centroid = (v_t[:, None] * (a + b + c) / 4.0).sum(axis=0) / vol
    # second moments about the origin: V/20 * (sum p_i p_j + (sum p_i)(sum p_j)) over the four tetra vertices (one is 0)
    s = a + b + c
    cov = np.zeros((3, 3))
    for i in range(3):
        for j in range(3):
            pij = a[:, i] * a[:, j] + b[:, i] * b[:, j] + c[:, i] * c[:, j]
            cov[i, j] = float((v_t / 20.0 * (pij + s[:, i] * s[:, j])).sum())
    cov_cm = cov - vol * np.outer(centroid, centroid)
    inertia = np.trace(cov_cm) * np.eye(3) - cov_cm
    return {"volume_mm3": vol, "centroid_mm": tuple(float(x) for x in centroid), "inertia_mm5": inertia}


# ------------------------------------------------------------------ glTF binary ---

_GLB_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
# CAD is Z up and glTF is Y up. (x, y, z) -> (x, z, -y) is a rotation, so triangle winding is unchanged.
_CAD_TO_GLTF = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def _vertex_normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    tri_pts = verts[tris]
    fn = np.cross(tri_pts[:, 1] - tri_pts[:, 0], tri_pts[:, 2] - tri_pts[:, 0])   # area weighted
    normals = np.zeros_like(verts)
    for k in range(3):
        np.add.at(normals, tris[:, k], fn)
    ln = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, ln, out=np.zeros_like(normals), where=ln > 0)


def _flat(verts: np.ndarray, tris: np.ndarray) -> Mesh:
    """One vertex per triangle corner, so every face keeps its own flat normal."""
    return verts[tris].reshape(-1, 3), np.arange(len(tris) * 3, dtype=np.int64).reshape(-1, 3)


def write_glb(path: Path, meshes: Sequence[NamedMesh], *, flat: bool = False, generator: str = "NitoCAD") -> Path:
    """One glTF node and mesh per named part. `flat` gives hard-edged faces, which suits meshes that came
    from an STL. B-rep tessellations already keep a separate vertex per face, so smooth normals there
    only round the inside of a curved face, never across an edge."""
    if not meshes:
        raise MeshError("Nothing to export.")
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []
    gl_meshes: list[dict] = []
    nodes: list[dict] = []

    def add_view(raw: bytes, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(raw), "target": target})
        blob.extend(raw)
        return len(views) - 1

    for name, verts, tris in meshes:
        if len(tris) == 0:
            continue
        v, t = _flat(verts, tris) if flat else (verts, tris)
        normals = _vertex_normals(v, t)
        pos = (v @ _CAD_TO_GLTF.T * 0.001).astype(np.float32)
        nor = (normals @ _CAD_TO_GLTF.T).astype(np.float32)
        idx = t.astype(np.uint32).reshape(-1)
        i_view = add_view(idx.tobytes(), 34963)
        p_view = add_view(pos.tobytes(), 34962)
        n_view = add_view(nor.tobytes(), 34962)
        base = len(accessors)
        accessors.append({"bufferView": i_view, "componentType": 5125, "count": int(idx.size), "type": "SCALAR",
                          "min": [int(idx.min())], "max": [int(idx.max())]})
        accessors.append({"bufferView": p_view, "componentType": 5126, "count": int(len(pos)), "type": "VEC3",
                          "min": [float(x) for x in pos.min(axis=0)], "max": [float(x) for x in pos.max(axis=0)]})
        accessors.append({"bufferView": n_view, "componentType": 5126, "count": int(len(nor)), "type": "VEC3"})
        gl_meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": base + 1, "NORMAL": base + 2},
                                                        "indices": base, "material": 0, "mode": 4}]})
        nodes.append({"name": name, "mesh": len(gl_meshes) - 1})
    if not nodes:
        raise MeshError("Nothing to export.")
    while len(blob) % 4:
        blob.append(0)

    doc = {
        "asset": {"version": "2.0", "generator": generator,
                  "extras": {"units": "metres", "note": "Converted from millimetres, Z up in CAD to Y up here."}},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": [{"name": "part", "pbrMetallicRoughness": {"baseColorFactor": [0.78, 0.8, 0.84, 1.0],
                                                                 "metallicFactor": 0.1, "roughnessFactor": 0.6}}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    out = bytearray()
    out += struct.pack("<III", _GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(js), _CHUNK_JSON) + js
    out += struct.pack("<II", len(blob), _CHUNK_BIN) + bytes(blob)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


# ------------------------------------------------------------------ 3MF ---

_CT = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
       '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
         'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
         '</Relationships>')


def write_3mf(path: Path, meshes: Sequence[NamedMesh], *, title: str = "NitoCAD part") -> Path:
    """A 3MF package in millimetres. Vertices are welded first so a slicer sees a closed, shared surface."""
    objects: list[str] = []
    items: list[str] = []
    for i, (name, verts, tris) in enumerate(meshes, start=1):
        v, t = weld(verts, tris)
        if len(t) == 0:
            continue
        vx = "".join(f'<vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>' for x, y, z in v)
        tx = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
        objects.append(f'<object id="{i}" type="model" name={quoteattr(name)}><mesh><vertices>{vx}</vertices>'
                       f'<triangles>{tx}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}"/>')
    if not objects:
        raise MeshError("Nothing to export.")
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{quoteattr(title)[1:-1]}</metadata>'
             '<metadata name="Application">NitoCAD</metadata>'
             f'<resources>{"".join(objects)}</resources><build>{"".join(items)}</build></model>')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
    return path


# ------------------------------------------------------------------ URDF ---

def safe_name(text: str, fallback: str = "part") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", text or "").strip("_")
    return s[:40] or fallback


def write_urdf_zip(path: Path, name: str, verts: np.ndarray, tris: np.ndarray, *,
                   density_g_cm3: float | None, material: str | None = None) -> Path:
    """A zip holding `<name>/urdf/<name>.urdf`, `<name>/meshes/<name>.stl` and a README.

    One rigid link, no joints: the part as a body for a simulator. Mass and inertia come from the closed
    mesh and the material's density. With no material the density of aluminium is used and the file and
    README say so."""
    robot = safe_name(name)
    v, t = weld(verts, tris)
    edges = edge_report(t)
    if edges["open"] or edges["non_manifold"]:
        raise MeshError("The part's surface has gaps, so its mass and inertia cannot be worked out.")
    props = mass_properties(v, t)
    assumed = density_g_cm3 is None or density_g_cm3 <= 0
    rho = ASSUMED_DENSITY_G_CM3 if assumed else float(density_g_cm3)
    vol_mm3 = props["volume_mm3"]
    mass_kg = vol_mm3 / 1000.0 * rho / 1000.0
    scale = rho * 1e-12                                    # density-1 mm^5 -> kg m^2
    inertia = props["inertia_mm5"] * scale
    cx, cy, cz = (c * 0.001 for c in props["centroid_mm"])
    why = (f"Material {material}, density {rho:g} g/cm3." if not assumed and material else
           f"Density {rho:g} g/cm3." if not assumed else
           f"No material was set, so the density of aluminium ({rho:g} g/cm3) was assumed. "
           "Set a material in the chat and export again for a real mass.")
    mesh_rel = f"../meshes/{robot}.stl"
    urdf = (
        '<?xml version="1.0"?>\n'
        f"<!-- {why} -->\n"
        f'<robot name="{robot}">\n'
        f'  <link name="{robot}">\n'
        "    <inertial>\n"
        f'      <origin xyz="{cx:.6g} {cy:.6g} {cz:.6g}" rpy="0 0 0"/>\n'
        f'      <mass value="{mass_kg:.6g}"/>\n'
        f'      <inertia ixx="{inertia[0, 0]:.6g}" ixy="{inertia[0, 1]:.6g}" ixz="{inertia[0, 2]:.6g}" '
        f'iyy="{inertia[1, 1]:.6g}" iyz="{inertia[1, 2]:.6g}" izz="{inertia[2, 2]:.6g}"/>\n'
        "    </inertial>\n"
        "    <visual>\n"
        '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
        f'      <geometry><mesh filename="{mesh_rel}" scale="0.001 0.001 0.001"/></geometry>\n'
        '      <material name="part"><color rgba="0.78 0.8 0.84 1"/></material>\n'
        "    </visual>\n"
        "    <collision>\n"
        '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
        f'      <geometry><mesh filename="{mesh_rel}" scale="0.001 0.001 0.001"/></geometry>\n'
        "    </collision>\n"
        "  </link>\n"
        "</robot>\n")
    readme = (
        f"{robot}: URDF from NitoCAD\n\n"
        f"urdf/{robot}.urdf   one rigid link with mass, inertia, visual and collision\n"
        f"meshes/{robot}.stl  the part in millimetres (the URDF scales it by 0.001)\n\n"
        f"Mass {mass_kg:.4g} kg. {why}\n"
        "The link frame is the CAD origin. The inertia is computed from the mesh about the centre of mass.\n"
        "The mesh path is relative to the URDF file. Loaders that need a package:// path need it edited.\n"
        "The collision shape is the full mesh, which many simulators slow down on. Replace it with a\n"
        "convex hull or a simple box if needed. This is one link with no joints.\n")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{robot}/urdf/{robot}.urdf", urdf)
        z.writestr(f"{robot}/meshes/{robot}.stl", stl_bytes(v, t))
        z.writestr(f"{robot}/README.txt", readme)
    return path

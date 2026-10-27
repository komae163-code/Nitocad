"""
Building the 3D solid for an attached file that is being edited.

  * STEP / IGES / BREP: the stored shape.
  * DXF / SVG / KiCad / Gerber: the flat shapes (profiles.py) extruded to a thickness.
  * Then the edit list (import_ops.py), in order.

Meshes (STL / OBJ) are handled by build_mesh in the same way with meshes.py.

CadQuery is imported inside the functions that need it, so importing this module is cheap.
Errors are GenerationError with a message that names the edit that failed.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import import_ops
import import_store
import meshes
import profiles
from exceptions import GenerationError


def _record(parameters: dict[str, Any]):
    user = import_store.current_owner()
    if not user:
        raise GenerationError("This part is made from an attached file, and no account was given to look it up")
    rec = import_store.get(user, str(parameters.get("import_id") or ""))
    if rec is None:
        raise GenerationError("The attached file is no longer stored. Attach it again to keep editing")
    return rec


def profile_to_workplane(profile: profiles.Profile, thickness_mm: float):
    import cadquery as cq

    def outline_solid(o: dict[str, Any], height: float, z0: float):
        wp = cq.Workplane("XY").workplane(offset=z0)
        if o["type"] == "circle":
            return wp.center(o["cx"], o["cy"]).circle(o["r"]).extrude(height)
        return wp.polyline([tuple(p) for p in o["points"]]).close().extrude(height)

    result = None
    for shape in profile.shapes:
        solid = outline_solid(shape["outer"], thickness_mm, 0.0)
        for hole in shape["holes"]:
            solid = solid.cut(outline_solid(hole, thickness_mm + 2.0, -1.0))
        result = solid if result is None else result.union(solid)
    if result is None:
        raise GenerationError("The drawing has no shape to extrude")
    return result


def _brep_to_workplane(data: bytes):
    import cadquery as cq
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    fd, path = tempfile.mkstemp(suffix=".brep")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, path, BRep_Builder()):
            raise GenerationError("The stored shape could not be read back")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return cq.Workplane(obj=cq.Shape.cast(shape))


def base_workplane(rec, parameters: dict[str, Any]):
    if rec.kind in import_ops.PROFILE_KINDS:
        prof = profiles.Profile.from_json(json.loads(rec.data))
        return profile_to_workplane(prof, float(parameters.get("thickness_mm") or prof.suggested_thickness_mm))
    if rec.kind in import_ops.BREP_KINDS:
        return _brep_to_workplane(rec.data)
    raise GenerationError("This file is a mesh and has no solid to build")


def _scale(wp, factor: float):
    import cadquery as cq
    from exporters import _scale_shape
    return cq.Workplane(obj=_scale_shape(wp.val(), factor))


def _select_edges(wp, which: str):
    if which == "vertical":
        return wp.edges("|Z")
    if which == "top":
        return wp.faces(">Z").edges()
    if which == "bottom":
        return wp.faces("<Z").edges()
    return wp.edges()


def apply_edit(wp, e: dict[str, Any], number: int):
    import cadquery as cq

    op = e["op"]
    bb = wp.val().BoundingBox()
    try:
        if op == "scale":
            return _scale(wp, float(e["factor"]))
        if op == "scale_to":
            dims = {"x": bb.xlen, "y": bb.ylen, "z": bb.zlen}
            dim = max(dims.values()) if e["axis"] == "largest" else dims[e["axis"]]
            if dim <= 1e-9:
                raise GenerationError(f"Edit {number}: the part has no size along {e['axis']}, so it cannot be scaled to a size")
            return _scale(wp, float(e["mm"]) / dim)
        if op == "hole":
            cx, cy = (bb.xmin + bb.xmax) / 2.0 + e["x"], (bb.ymin + bb.ymax) / 2.0 + e["y"]
            r = e["diameter_mm"] / 2.0
            if e["depth_mm"] is None:
                z0, h = bb.zmin - 1.0, bb.zlen + 2.0
            elif e["face"] == "top":
                z0, h = bb.zmax - e["depth_mm"], e["depth_mm"] + 1.0
            else:
                z0, h = bb.zmin - 1.0, e["depth_mm"] + 1.0
            before = wp.val().Volume()
            out = wp.cut(cq.Workplane("XY").workplane(offset=z0).center(cx, cy).circle(r).extrude(h))
            if abs(out.val().Volume() - before) < 1e-9:
                raise GenerationError(f"Edit {number}: the hole at x {e['x']:g}, y {e['y']:g} does not touch the part")
            return out
        if op in ("fillet", "chamfer"):
            size = e["radius_mm"] if op == "fillet" else e["size_mm"]
            sel = _select_edges(wp, e["edges"])
            return sel.fillet(size) if op == "fillet" else sel.chamfer(size)
        if op == "translate":
            return wp.translate((e["x"], e["y"], e["z"]))
        if op == "rotate":
            axis = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[e["axis"]]
            return wp.rotate((0, 0, 0), axis, float(e["degrees"]))
        if op == "mirror":
            return wp.mirror(e["plane"].upper())
    except GenerationError:
        raise
    except Exception as exc:                                  # noqa: BLE001 - OCCT raises many types
        what = {"fillet": "a fillet of that size does not fit this shape. Try a smaller radius",
                "chamfer": "a chamfer of that size does not fit this shape. Try a smaller size"}.get(op, str(exc))
        raise GenerationError(f"Edit {number} ({import_ops.describe_edit(e).lower()}): {what}") from exc
    raise GenerationError(f"Edit {number}: '{op}' cannot be done on a solid")


def build_imported_workplane(parameters: dict[str, Any]):
    """The finished solid for an imported part's parameters (as cleaned by clean_import_params)."""
    rec = _record(parameters)
    wp = base_workplane(rec, parameters)
    for i, e in enumerate(import_ops.clean_edits(parameters.get("edits"), rec.kind), start=1):
        wp = apply_edit(wp, e, i)
    solid = wp.val()
    if not solid.isValid():
        raise GenerationError("The edits produced an invalid shape. Undo the last edit and try something smaller")
    return wp


def build_imported_mesh(parameters: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Binary STL bytes for an imported mesh after its edits, and its measurements."""
    rec = _record(parameters)
    if rec.kind not in import_ops.MESH_KINDS:
        raise GenerationError("This file is not a mesh")
    tris = meshes.parse_stl(rec.data)
    try:
        tris = meshes.apply_edits(tris, import_ops.clean_edits(parameters.get("edits"), rec.kind))
    except meshes.MeshError as exc:
        raise GenerationError(str(exc)) from exc
    return meshes.write_stl(tris), meshes.stats(tris)

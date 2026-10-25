"""
Executes a VALIDATED feature tree (see feature_tree.py) with CadQuery.

Every feature is followed by solid checks (valid, non-empty; a cut must remove material;
the final result must be exactly one connected solid) so a failure names the exact feature
instead of exporting a broken model. Errors are FeatureTreeError and carry the feature id,
which the planner/LLM retry loop can feed back.

UNVERIFIED in the environment this was written in (no CadQuery kernel available):
covered by tests/test_feature_builder.py, which must be run where cadquery is installed.
The CadQuery calls used (Workplane.pushPoints/rect/circle/ellipse/slot2D/polygon/polyline,
extrude(combine=False), revolve(combine=False), Solid.makeLoft, translate/rotate/mirror,
union/cut/intersect) are standard CadQuery 2.x API.
"""
from __future__ import annotations

from typing import Any

import cadquery as cq

from feature_tree import airfoil_points, expand_path, profile_frame, validate_feature_tree

VOLUME_EPS = 1e-6


class FeatureTreeError(ValueError):
    def __init__(self, feature_id: str, message: str):
        super().__init__(f"feature '{feature_id}': {message}")
        self.feature_id = feature_id


# --------------------------------------------------------------- sketching
def _base_plane(plane: str, offset: float) -> cq.Workplane:
    return cq.Workplane(plane).workplane(offset=offset)


def _draw(w: cq.Workplane, prof: dict[str, Any]) -> cq.Workplane:
    t = prof["type"]
    cx, cy = prof["center"]
    if t == "rect":
        return w.pushPoints([(cx, cy)]).rect(prof["width"], prof["height"])
    if t == "circle":
        return w.pushPoints([(cx, cy)]).circle(prof["diameter"] / 2)
    if t == "ellipse":
        return w.pushPoints([(cx, cy)]).ellipse(prof["width"] / 2, prof["height"] / 2)
    if t == "slot":
        return w.pushPoints([(cx, cy)]).slot2D(prof["length"], prof["width"], prof["angle"])
    if t == "regular_polygon":
        return w.pushPoints([(cx, cy)]).polygon(prof["sides"], prof["diameter"])
    pts = airfoil_points(prof["naca"], prof["chord"]) if t == "airfoil" else prof["points"]
    return w.polyline([(x + cx, y + cy) for x, y in pts]).close()


def _sketch(plane: str, offset: float, prof: dict[str, Any]) -> cq.Workplane:
    w = _draw(_base_plane(plane, offset), prof)
    for hole in prof.get("holes", []):
        w = _draw(w, hole)
    return w


def _tool_for(feature: dict[str, Any]) -> cq.Workplane:
    op = feature["op"]
    if op == "extrude":
        w = _sketch(feature["plane"], feature["offset"], feature["profile"])
        d = feature["distance"]
        if feature["symmetric"]:
            return w.extrude(d / 2, both=True, combine=False)
        return w.extrude(-d if feature["reverse"] else d, combine=False)
    if op == "revolve":
        w = _sketch(feature["plane"], feature["offset"], feature["profile"])
        end = (1, 0, 0) if feature["axis"] == "x" else (0, 1, 0)
        return w.revolve(feature["angle"], (0, 0, 0), end, combine=False)
    if op == "sweep":
        return _sweep_tool(feature)
    # loft: each section is its own wire in global coordinates
    wires: list[cq.Wire] = []
    for sec in feature["sections"]:
        sw = _sketch(sec["plane"], sec["offset"], sec["profile"])
        found = [o for o in sw.vals() if isinstance(o, cq.Wire)]
        if not found:
            raise FeatureTreeError(feature["id"], "a loft section produced no closed wire")
        wires.append(found[0])
    solid = cq.Solid.makeLoft(wires, feature["ruled"])
    return cq.Workplane("XY").newObject([solid])


def _sweep_tool(feature: dict[str, Any]) -> cq.Workplane:
    """Sweep the profile along the 3D path (lines plus tangent arcs when bend_radius > 0)."""
    path = feature["path"]
    segments, errors = expand_path(path, feature["bend_radius"])
    if errors:  # already rejected by validation; kept as a guard for direct callers
        raise FeatureTreeError(feature["id"], "; ".join(errors))
    edges = []
    for seg in segments:
        if seg[0] == "line":
            edges.append(cq.Edge.makeLine(cq.Vector(*seg[1]), cq.Vector(*seg[2])))
        else:
            edges.append(cq.Edge.makeThreePointArc(cq.Vector(*seg[1]), cq.Vector(*seg[2]), cq.Vector(*seg[3])))
    path_wire = cq.Wire.assembleEdges(edges)

    p0, p1 = path[0], path[1]
    direction = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    d_len = (direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) ** 0.5
    d = (direction[0] / d_len, direction[1] / d_len, direction[2] / d_len)
    x_dir, _ = profile_frame(d)
    plane = cq.Plane(origin=tuple(p0), xDir=x_dir, normal=d)

    w = _draw(cq.Workplane(plane), feature["profile"])
    for hole in feature["profile"].get("holes", []):
        w = _draw(w, hole)
    transition = "transformed" if feature["bend_radius"] > 0 else "right"
    return w.sweep(cq.Workplane("XY").newObject([path_wire]), isFrenet=False, combine=False, transition=transition)


# ----------------------------------------------------------------- booleans
def _volume(w: cq.Workplane) -> float:
    return float(sum(s.Volume() for s in w.solids().vals()))


def _combine(body: cq.Workplane | None, tool: cq.Workplane, mode: str, fid: str) -> cq.Workplane:
    if mode == "new":
        result = tool
    else:
        if body is None:
            raise FeatureTreeError(fid, "no body exists yet to combine with")
        before = _volume(body)
        if mode == "add":
            result = body.union(tool)
        elif mode == "cut":
            result = body.cut(tool)
            if abs(before - _volume(result)) <= VOLUME_EPS * max(1.0, before):
                raise FeatureTreeError(fid, "the cut removed no material; check its plane, offset, "
                                            "direction and distance against the body")
        else:
            result = body.intersect(tool)
    solids = result.solids().vals()
    if not solids:
        raise FeatureTreeError(fid, "the operation produced no solid")
    if not all(s.isValid() for s in solids):
        raise FeatureTreeError(fid, "the operation produced an invalid solid")
    if _volume(result) <= VOLUME_EPS:
        raise FeatureTreeError(fid, "the operation produced a zero-volume solid")
    return result


def _instances(tool: cq.Workplane, feature: dict[str, Any]) -> list[cq.Workplane]:
    if feature["op"] == "mirror":
        o = feature["offset"]
        base = {"XY": (0, 0, o), "YZ": (o, 0, 0), "XZ": (0, o, 0)}[feature["plane"]]
        return [tool.mirror(feature["plane"], cq.Vector(*base))]
    n = feature["count"]
    if feature["kind"] == "linear":
        dx, dy, dz = feature["spacing"]
        return [tool.translate((dx * i, dy * i, dz * i)) for i in range(1, n)]
    ax = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[feature["axis"]]
    p = feature["axis_point"]
    end = (p[0] + ax[0], p[1] + ax[1], p[2] + ax[2])
    total = feature["angle"]
    step = total / n if total >= 360 - 1e-9 else total / (n - 1)
    return [tool.rotate(tuple(p), end, step * i) for i in range(1, n)]


# ------------------------------------------------------------------- build
def build_feature_tree(spec: Any) -> cq.Workplane:
    """Validate and build. Raises FeatureTreeError (with the feature id) on any failure."""
    features, errors = validate_feature_tree(spec)
    if errors:
        raise FeatureTreeError("(schema)", "; ".join(errors[:5]))

    body: cq.Workplane | None = None
    tools: dict[str, tuple[cq.Workplane, str]] = {}
    for feat in features:
        fid, op = feat["id"], feat["op"]
        try:
            if op in ("extrude", "revolve", "loft", "sweep"):
                tool = _tool_for(feat)
                tools[fid] = (tool, feat["mode"])
                body = _combine(body, tool, feat["mode"], fid)
            else:
                src_tool, src_mode = tools[feat["feature"]]
                for inst in _instances(src_tool, feat):
                    body = _combine(body, inst, src_mode, fid)
        except FeatureTreeError:
            raise
        except Exception as exc:  # noqa: BLE001 - OCC raises many exception types
            raise FeatureTreeError(fid, f"CadQuery failed: {exc}") from exc

    assert body is not None
    n = len(body.solids().vals())
    if n != 1:
        raise FeatureTreeError("(result)", f"the result is {n} disconnected solids; make every feature overlap or touch the body")
    return body

"""building template: a house or small building from flat Studio parameters (see building/flat.py).

Returns ONE compound of the building's parts (slabs, walls per level, stair, gable ends, roof), each built
by the strict feature-tree builder, so STL / STEP / IGES go through the normal export pipeline unchanged.
"""
import cadquery as cq

from building.flat import resolve_flat
from building.model3d import build_parts
from feature_builder import build_feature_tree


def generate_building(params: dict) -> cq.Workplane:
    r = resolve_flat(params)
    if not r["ok"]:
        raise ValueError("; ".join(r["errors"]))
    shapes = [build_feature_tree(p["parameters"]["features"]).val() for p in build_parts(r["model"])]
    return cq.Workplane("XY").newObject([cq.Compound.makeCompound(shapes)])

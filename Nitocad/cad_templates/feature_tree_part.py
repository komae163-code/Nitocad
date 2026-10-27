"""feature_tree template: builds one solid from a validated feature-tree spec.

Expected params:
- features: list of features (see feature_tree.py for the DSL: extrude, revolve, loft,
  pattern, mirror with rect/circle/ellipse/slot/polygon/regular_polygon/airfoil profiles)
"""
import cadquery as cq

from feature_builder import build_feature_tree


def generate_feature_tree(params: dict) -> cq.Workplane:
    return build_feature_tree(params.get("features"))

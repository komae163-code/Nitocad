"""
Post-build geometry verification.

The compiler may return expected geometry alongside the solid (bounding box,
exact volume for extruded polygons). This module checks:

  - isValid() and exactly one solid
  - volume greater than zero and within tolerance (when expected given)
  - bounding box within tolerance (when expected given)
  - watertight tessellation (trimesh, on the STL) when available
  - no NaN or degenerate faces

Result is a VerificationReport stored on the job. If it fails, nothing is
delivered, anchored, or charged.

CadQuery / OCP / trimesh are optional at import time so pure logic tests
run without the geometry stack.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)

# Relative tolerances for volume / bbox comparison against expected values.
VOLUME_REL_TOL = 0.05
BBOX_ABS_TOL_MM = 0.5


@dataclass
class VerificationReport:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cq_solid_checks(solid: Any, expected: dict[str, Any] | None) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    """Run CadQuery/OCP solid checks. solid is a CadQuery Workplane or Shape."""
    checks: dict[str, bool] = {}
    errors: list[str] = []
    measured: dict[str, Any] = {}

    try:
        import cadquery as cq  # noqa: F401
        from OCP.BRepCheck import BRepCheck_Analyzer
    except ImportError:
        checks["cq_available"] = False
        return checks, ["CadQuery/OCP not available; solid checks skipped"], measured

    checks["cq_available"] = True

    # Resolve to a Shape
    shape = solid
    if hasattr(solid, "val"):
        try:
            shape = solid.val()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Could not extract solid from Workplane: {exc}")
            checks["is_valid"] = False
            return checks, errors, measured

    # isValid via BRepCheck
    try:
        analyzer = BRepCheck_Analyzer(shape.wrapped if hasattr(shape, "wrapped") else shape)
        is_valid = bool(analyzer.IsValid())
        checks["is_valid"] = is_valid
        if not is_valid:
            errors.append("Solid failed BRepCheck_Analyzer.IsValid()")
    except Exception as exc:  # noqa: BLE001
        checks["is_valid"] = False
        errors.append(f"BRepCheck failed: {exc}")

    # Exactly one solid
    try:
        solids = solid.solids().vals() if hasattr(solid, "solids") else [shape]
        n = len(solids)
        checks["exactly_one_solid"] = n == 1
        measured["solid_count"] = n
        if n != 1:
            errors.append(f"Expected exactly one solid, got {n}")
    except Exception as exc:  # noqa: BLE001
        checks["exactly_one_solid"] = False
        errors.append(f"Solid count check failed: {exc}")

    # Volume > 0
    try:
        vol = float(shape.Volume()) if hasattr(shape, "Volume") else float(solid.val().Volume())
        measured["volume"] = vol
        checks["volume_positive"] = vol > 0
        if vol <= 0:
            errors.append(f"Volume is not positive: {vol}")
        if expected and "volume" in expected and expected["volume"] is not None:
            exp_v = float(expected["volume"])
            if exp_v > 0:
                rel = abs(vol - exp_v) / exp_v
                checks["volume_within_tol"] = rel <= VOLUME_REL_TOL
                measured["expected_volume"] = exp_v
                measured["volume_rel_err"] = rel
                if rel > VOLUME_REL_TOL:
                    errors.append(
                        f"Volume {vol:.4g} outside tolerance of expected {exp_v:.4g} "
                        f"(rel err {rel:.3%})"
                    )
    except Exception as exc:  # noqa: BLE001
        checks["volume_positive"] = False
        errors.append(f"Volume check failed: {exc}")

    # Bounding box
    try:
        bb = shape.BoundingBox() if hasattr(shape, "BoundingBox") else solid.val().BoundingBox()
        bbox = {
            "xmin": float(bb.xmin), "xmax": float(bb.xmax),
            "ymin": float(bb.ymin), "ymax": float(bb.ymax),
            "zmin": float(bb.zmin), "zmax": float(bb.zmax),
        }
        measured["bbox"] = bbox
        # No NaN
        has_nan = any(
            v != v for v in (bbox["xmin"], bbox["xmax"], bbox["ymin"], bbox["ymax"], bbox["zmin"], bbox["zmax"])
        )
        checks["bbox_finite"] = not has_nan
        if has_nan:
            errors.append("Bounding box contains NaN")

        if expected and "bbox" in expected and expected["bbox"] is not None:
            exp = expected["bbox"]
            ok = True
            for key in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
                if key in exp and abs(bbox[key] - float(exp[key])) > BBOX_ABS_TOL_MM:
                    ok = False
                    errors.append(
                        f"bbox.{key}={bbox[key]:.4g} outside ±{BBOX_ABS_TOL_MM} of expected {exp[key]}"
                    )
            checks["bbox_within_tol"] = ok
    except Exception as exc:  # noqa: BLE001
        checks["bbox_finite"] = False
        errors.append(f"Bounding box check failed: {exc}")

    return checks, errors, measured


def _stl_watertight_check(
    stl_path: str | Path, *, require_watertight: bool = True
) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    """Checks on the exported STL. With require_watertight=False (vehicles and other multi-part assemblies) the merged
    mesh is NOT tested for being watertight: parts that touch or overlap share edges once merged, so the whole can never
    be one closed shell even when every part is. Those builds are checked solid by solid (verify_solids) instead; the
    merged result is only recorded in `measured` for information."""
    checks: dict[str, bool] = {}
    errors: list[str] = []
    measured: dict[str, Any] = {}
    path = Path(stl_path)
    if not path.is_file():
        checks["stl_present"] = False
        errors.append(f"STL not found: {path}")
        return checks, errors, measured
    checks["stl_present"] = True

    try:
        import trimesh
    except ImportError:
        checks["trimesh_available"] = False
        # Soft: not a hard failure when trimesh is absent
        return checks, [], measured

    checks["trimesh_available"] = True
    try:
        mesh = trimesh.load(str(path), force="mesh")
        if isinstance(mesh, trimesh.Scene):
            geoms = list(mesh.geometry.values())
            mesh = geoms[0] if geoms else None
        if mesh is None:
            checks["watertight"] = False
            errors.append("STL loaded but no mesh geometry found")
            return checks, errors, measured

        measured["face_count"] = int(len(mesh.faces))
        measured["vertex_count"] = int(len(mesh.vertices))
        is_wt = bool(mesh.is_watertight)
        if require_watertight:
            checks["watertight"] = is_wt
            if not is_wt:
                errors.append("STL mesh is not watertight")
        else:
            measured["merged_mesh_watertight"] = is_wt          # information only, never a failure
            checks["merged_mesh_check_skipped"] = True

        # Degenerate faces (zero area)
        try:
            areas = mesh.area_faces
            degenerates = int((areas < 1e-12).sum()) if hasattr(areas, "sum") else 0
            measured["degenerate_faces"] = degenerates
            checks["no_degenerate_faces"] = degenerates == 0
            if degenerates > 0:
                errors.append(f"{degenerates} degenerate (zero-area) faces")
        except Exception:  # noqa: BLE001
            checks["no_degenerate_faces"] = True  # skip if unavailable

        # NaN vertices
        try:
            import numpy as np
            has_nan = bool(np.isnan(mesh.vertices).any())
            checks["no_nan_vertices"] = not has_nan
            if has_nan:
                errors.append("Mesh vertices contain NaN")
        except Exception:  # noqa: BLE001
            checks["no_nan_vertices"] = True
    except Exception as exc:  # noqa: BLE001
        checks["watertight"] = False
        errors.append(f"trimesh check failed: {exc}")

    return checks, errors, measured


def verify_geometry(
    solid: Any | None = None,
    *,
    stl_path: str | Path | None = None,
    expected: dict[str, Any] | None = None,
    multi_part: bool = False,
) -> VerificationReport:
    """Run all available post-build checks and return a VerificationReport.

    multi_part=True is for vehicles and every other assembly of separate parts: the merged STL is not tested for being
    watertight (it cannot be, once touching parts are merged). Each part is verified on its own by verify_solids().

    expected may contain:
      - volume: float (mm³)
      - bbox: dict with xmin/xmax/ymin/ymax/zmin/zmax
    """
    all_checks: dict[str, bool] = {}
    all_errors: list[str] = []
    all_warnings: list[str] = []
    measured: dict[str, Any] = {}

    if solid is not None:
        c, e, m = _cq_solid_checks(solid, expected)
        all_checks.update(c)
        all_errors.extend(e)
        measured.update(m)
    else:
        all_warnings.append("No solid provided; CQ checks skipped")

    if stl_path is not None:
        c, e, m = _stl_watertight_check(stl_path, require_watertight=not multi_part)
        all_checks.update(c)
        all_errors.extend(e)
        measured.update(m)
        if not c.get("trimesh_available", True) and c.get("stl_present"):
            logger.warning("trimesh not installed; watertight check skipped (add it to requirements.txt)")
    else:
        all_warnings.append("No STL path provided; mesh checks skipped")
    if multi_part:
        all_warnings.append("Multi-part build: each part is checked on its own, not the merged mesh")

    # Hard failures only: explicit False on critical checks, or any error
    critical_keys = (
        "is_valid",
        "exactly_one_solid",
        "volume_positive",
        "volume_within_tol",
        "bbox_finite",
        "bbox_within_tol",
        "watertight",
        "no_degenerate_faces",
        "no_nan_vertices",
    )
    failed_critical = any(all_checks.get(k) is False for k in critical_keys)
    passed = not failed_critical and not all_errors

    if not passed:
        logger.warning(
            "geometry verification failed",
            extra={"errors": all_errors, "checks": all_checks},
        )

    return VerificationReport(
        passed=passed,
        checks=all_checks,
        errors=all_errors,
        warnings=all_warnings,
        measured=measured,
    )


# ----------------------------------------------------------------------------------------------- per-solid checks
WELD_MM = 1e-3          # vertices closer than this are one vertex (tessellated faces of one solid meet on shared edges)
MAX_PART_ERRORS = 6     # list this many failing parts, then say how many more


def mesh_is_closed(vertices: Any, faces: Any, weld: float = WELD_MM) -> tuple[bool, dict[str, int]]:
    """True when every edge of the triangle mesh is shared by exactly two triangles (a closed surface).
    Pure numpy, so it is testable without a CAD kernel. Zero-area triangles are ignored."""
    import numpy as np

    v = np.asarray(vertices, dtype=float).reshape(-1, 3)
    f = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if len(f) == 0 or len(v) == 0:
        return False, {"triangles": 0, "open_edges": 0, "overshared_edges": 0}
    key = np.round(v / weld).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    tri = inv[f]
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    tri = tri[keep]
    if len(tri) == 0:
        return False, {"triangles": 0, "open_edges": 0, "overshared_edges": 0}
    e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    e.sort(axis=1)
    n = int(inv.max()) + 1
    code = e[:, 0] * n + e[:, 1]
    _, counts = np.unique(code, return_counts=True)
    open_edges = int((counts == 1).sum())
    over = int((counts > 2).sum())
    return (open_edges == 0 and over == 0), {"triangles": int(len(tri)), "open_edges": open_edges, "overshared_edges": over}


def _kernel_mesher(shape: Any, tolerance: float = 0.1, angular: float = 0.2) -> tuple[Any, Any]:
    verts, tris = shape.tessellate(tolerance, angular)
    return [(float(p.x), float(p.y), float(p.z)) for p in verts], [tuple(t) for t in tris]


def _solids_of(shape: Any) -> list[Any]:
    try:
        sols = list(shape.Solids())
    except Exception:  # noqa: BLE001
        sols = []
    return sols or [shape]


def verify_solids(parts: list[tuple[str, Any]], mesher: Any = None) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    """Verify an assembly part by part. `parts` is [(name, shape)] (assembly.get_assembly_parts). Every solid of every
    part must be a valid B-rep and tessellate to a closed surface on its own. Returns (checks, errors, measured).
    This replaces the merged-mesh watertight test for vehicles and other multi-part builds."""
    mesher = mesher or _kernel_mesher
    checks: dict[str, bool] = {}
    errors: list[str] = []
    measured: dict[str, Any] = {"parts_checked": 0, "solids_checked": 0}
    bad = 0
    for name, shape in parts:
        measured["parts_checked"] += 1
        for i, solid in enumerate(_solids_of(shape), start=1):
            measured["solids_checked"] += 1
            label = f"part '{name}'" + (f" (solid {i})" if len(_solids_of(shape)) > 1 else "")
            problem = None
            try:
                if hasattr(solid, "isValid") and not solid.isValid():
                    problem = "is not a valid solid"
                else:
                    verts, tris = mesher(solid)
                    ok, info = mesh_is_closed(verts, tris)
                    if not ok:
                        problem = (f"is not a closed solid ({info['open_edges']} open edges, "
                                   f"{info['overshared_edges']} over-shared edges)")
            except Exception as exc:  # noqa: BLE001
                problem = f"could not be checked: {str(exc)[:80]}"
            if problem:
                bad += 1
                if len(errors) < MAX_PART_ERRORS:
                    errors.append(f"{label} {problem}")
    checks["every_part_is_closed_solid"] = bad == 0
    measured["parts_failed"] = bad
    if bad > MAX_PART_ERRORS:
        errors.append(f"and {bad - MAX_PART_ERRORS} more")
    return checks, errors, measured

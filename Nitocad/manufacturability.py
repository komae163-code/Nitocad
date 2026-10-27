"""
The Studio's manufacturability check: one report for one part version and one process.

It runs no AI. It reads two things:

  * the part's dimensions, through the rules in dfm.py (wall, hole, slenderness, sheet-metal and
    molding limits), and
  * the part's triangle mesh, when there is one: whether the surface is closed, how thin the walls
    measure, how much of the part overhangs (FDM), whether the faces have draft (molding) and whether
    there is a sealed cavity that traps resin (resin).

Every check carries a status:

    pass         looked at it, and it holds
    warn         makeable, but costly, fragile or likely to disappoint
    fail         cannot be made by this process as it stands
    info         worth knowing
    not_checked  needs something this check does not have, and the report says what

It is guidance from rules of thumb. It is not a certified review and not a shop quote, and the report
says so. A check that could not run is reported as not_checked, never as a pass.

numpy and the standard library only, so it can be tested without CadQuery.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

import dfm
import materials
import mesh_formats as mf
from logging_config import get_logger

logger = get_logger(__name__)

# id -> label shown on the button and in the report
PROCESSES: dict[str, str] = {
    "cnc": "CNC machining",
    "sheet_metal": "sheet metal",
    "injection_molding": "injection molding",
    "fdm": "FDM 3D printing",
    "resin": "resin 3D printing",
}

# The dfm.py processes each one is checked against
_DFM = {"cnc": ("cnc_milling",), "sheet_metal": ("sheet_metal",), "injection_molding": ("injection_molding",),
        "fdm": ("fdm_print",), "resin": ("resin_print",)}

# Parts this check makes no sense for
NOT_CHECKABLE = {
    "assembly": "The manufacturability check runs on single parts. Assemblies are not checked yet.",
    "pcb_board": "A circuit board is made by a board house from its Gerber files, so this check does not apply to it.",
    "building": "A building is not made by a shop process, so this check does not apply to it.",
    "road_segment": "A road or culvert is not made by a shop process, so this check does not apply to it.",
    "box_culvert": "A road or culvert is not made by a shop process, so this check does not apply to it.",
    "retaining_wall": "A road or culvert is not made by a shop process, so this check does not apply to it.",
}

_TITLES = {
    "process_fit": "Suits this process", "material_fit": "Material", "min_wall": "Wall thickness",
    "thick_section": "Thick sections", "min_hole": "Small holes", "hole_depth": "Hole depth to diameter",
    "work_envelope": "Size", "build_volume": "Size", "tool_size": "Size", "flatness": "Flat plates",
    "slenderness": "Slender shape", "anisotropy": "Layer strength", "thickness": "Sheet thickness",
    "bend_radius": "Bend radius", "flange_length": "Flange length", "fillet": "Corner radius",
}
_STATUS = {"error": "fail", "warning": "warn", "info": "info", "ok": "pass"}
_RANK = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "not_checked": 4}
_SIZE_RULES = {"work_envelope", "build_volume", "tool_size"}

# (fail below, warn below) in mm, for walls measured on the mesh. Same numbers as dfm.py.
_WALL = {"cnc": (0.4, 0.8), "fdm": (0.8, 1.2), "injection_molding": (0.5, 1.0), "resin": (0.4, 0.8)}
_WALL_POLYMER_CNC = (0.75, 1.5)

_MAX_TRIANGLES_FOR_RAYS = 120_000
_RAYS = 240

DISCLAIMER = "Guidance from general rules of thumb. It is not a certified review or a shop quote."


def is_process(process: str) -> bool:
    return process in PROCESSES


def refusal(part_type: str | None) -> str | None:
    """Why this part cannot be checked, or None when it can."""
    return NOT_CHECKABLE.get(part_type or "")


# ----------------------------------------------------------------------- the report ---

def run(part_type: str, params: dict | None, material: str | None, process: str,
        mesh: tuple[np.ndarray, np.ndarray] | None = None) -> dict[str, Any]:
    """The report for one part and one process. `mesh` is (vertices, triangles) in millimetres, or None
    when it could not be read."""
    if process not in PROCESSES:
        raise ValueError(f"process must be one of {', '.join(PROCESSES)}, got {process!r}")
    params = params or {}
    mat = materials.lookup(material)
    label = PROCESSES[process]
    checks: list[dict[str, Any]] = []
    assumptions: list[str] = []

    dim_checks = _dimension_checks(part_type, params, material, process)
    analysed: dict[str, Any] | None = None
    if mesh is not None and process != "sheet_metal":
        try:
            analysed = _analyse(mesh)
        except Exception:  # noqa: BLE001 - a bad mesh must never take the whole report down
            logger.exception("mesh analysis failed")
            analysed = None

    if analysed is not None:
        dim_checks = [c for c in dim_checks if c["rule"] not in _SIZE_RULES]
        checks += _mesh_checks(analysed, process, mat)
    checks += [{k: v for k, v in c.items() if k != "rule"} for c in dim_checks]
    checks += _not_checked(process, mesh_available=analysed is not None, sheet=process == "sheet_metal")

    # one entry per id, worst first
    seen: dict[str, dict] = {}
    for c in checks:
        prev = seen.get(c["id"])
        if prev is None or _RANK[c["status"]] < _RANK[prev["status"]]:
            seen[c["id"]] = c
    ordered = sorted(seen.values(), key=lambda c: _RANK[c["status"]])

    if mat is None:
        assumptions.append("No material is set, so material rules were skipped.")
    elif mat.generic:
        assumptions.append(f"The material was read as {mat.key}, the usual grade for that name.")
    if mesh is None and process != "sheet_metal":
        assumptions.append("The part's mesh could not be read, so only its dimensions were checked.")
    elif mesh is not None and process != "sheet_metal" and analysed is None:
        assumptions.append("The part's mesh could not be analysed, so only its dimensions were checked.")
    assumptions += _process_assumptions(process)

    n = {s: sum(c["status"] == s for c in ordered) for s in _RANK}
    headline = (f"{n['fail']} problem{'s' if n['fail'] != 1 else ''}, {n['warn']} to review, "
                f"{n['pass']} passed, {n['not_checked']} not checked.")
    return {"process": process, "process_label": label, "headline": headline, "checks": ordered,
            "assumptions": assumptions, "disclaimer": DISCLAIMER}


def _process_assumptions(process: str) -> list[str]:
    return {
        "cnc": ["Limits are typical for a 3-axis shop mill: a 500 mm longest side and walls of 0.8 mm in metal."],
        "sheet_metal": ["Limits are typical for press-brake work: 0.5 to 6 mm sheet."],
        "injection_molding": ["Limits are typical for thermoplastics: walls of 1 to 3 mm. A mold costs a lot, so this pays off in volume."],
        "fdm": ["Limits are typical for a desktop printer with a 0.4 mm nozzle and a 220 mm bed.",
                "The part is taken as modelled: Z up, with its lowest face on the bed."],
        "resin": ["Limits are typical for a desktop resin printer: a build volume near 145 x 145 x 185 mm."],
    }[process]


# ------------------------------------------------------------------ checks from dimensions ---

def _dimension_checks(part_type: str, params: dict, material: str | None, process: str) -> list[dict[str, Any]]:
    findings: list[dict] = []
    passes: list[dict] = []
    procs = list(_DFM[process])
    if process == "cnc" and part_type in dfm._ROTATIONAL:   # noqa: SLF001 - one list of rotational types
        procs.append("turning")
    for p in procs:
        f, ok = dfm.check_with_passes(part_type, params, p, material)
        findings += f
        passes += ok
    out: list[dict[str, Any]] = []
    flagged = {f["rule"] for f in findings}
    for f in findings:
        out.append(_row(f))
    for p in passes:
        if p["rule"] not in flagged:
            out.append(_row(p))
    return out


def _row(f: dict) -> dict[str, Any]:
    rule = f["rule"]
    row: dict[str, Any] = {"rule": rule, "id": rule, "title": _TITLES.get(rule, rule.replace("_", " ").capitalize()),
                           "status": _STATUS[f["severity"]], "detail": f["message"]}
    if f.get("value") is not None:
        row["measured"] = f"{f['value']:g}" if isinstance(f["value"], (int, float)) else str(f["value"])
    if f.get("limit") is not None:
        row["limit"] = f"{f['limit']:g}" if isinstance(f["limit"], (int, float)) else str(f["limit"])
    return row


# ----------------------------------------------------------------------- mesh analysis ---

def _analyse(mesh: tuple[np.ndarray, np.ndarray]) -> dict[str, Any]:
    verts, tris = mf.weld(np.asarray(mesh[0], float), np.asarray(mesh[1], np.int64), decimals=3)
    if len(tris) == 0:
        raise ValueError("empty mesh")
    edges = mf.edge_report(tris)
    closed = edges["open"] == 0 and edges["non_manifold"] == 0
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    cross = np.cross(b - a, c - a)
    area2 = np.linalg.norm(cross, axis=1)
    info: dict[str, Any] = {"verts": verts, "tris": tris, "edges": edges, "closed": closed,
                            "bbox": verts.max(axis=0) - verts.min(axis=0), "zmin": float(verts[:, 2].min())}
    d = np.einsum("ij,ij->i", a, np.cross(b, c))
    comp_vol = None
    if closed:
        labels = _components(tris, len(verts))
        comp_vol = np.bincount(labels, weights=d, minlength=int(labels.max()) + 1) / 6.0
        biggest = comp_vol[np.argmax(np.abs(comp_vol))]
        sign = -1.0 if biggest < 0 else 1.0
        comp_vol = comp_vol * sign
        info["shells"] = int((comp_vol > 1e-6).sum())
        info["cavities"] = [float(v) for v in -comp_vol[comp_vol < -1.0]]
        info["volume_mm3"] = float(comp_vol.sum())
        if sign < 0:                                   # inside out: flip so normals point out of the material
            tris = tris[:, ::-1]
            info["tris"] = tris
            cross = -cross
    else:
        # decide the winding from the overall signed volume, which is still a good guide for a small gap
        if d.sum() < 0:
            tris = tris[:, ::-1]
            info["tris"] = tris
            cross = -cross
    with np.errstate(invalid="ignore", divide="ignore"):
        normals = np.where(area2[:, None] > 0, cross / np.where(area2[:, None] > 0, area2[:, None], 1.0), 0.0)
    info["normals"] = normals
    info["areas"] = area2 / 2.0
    info["centroids"] = (a + b + c) / 3.0
    return info


def _components(tris: np.ndarray, n: int) -> np.ndarray:
    """Which connected shell each triangle belongs to (union-find over shared vertices)."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in tris.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        ra, rc = find(a), find(c)
        if ra != rc:
            parent[rc] = ra
    roots = np.array([find(int(t)) for t in tris[:, 0]])
    _, labels = np.unique(roots, return_inverse=True)
    return labels.reshape(-1)


def _sample_wall(info: dict[str, Any], rays: int = _RAYS) -> tuple[float, int] | None:
    """Thickness measured by shooting rays inward from sampled faces to the next surface they meet.
    Returns (thinnest, number of rays that found a far side), or None when the mesh is too fine to sample."""
    verts, tris = info["verts"], info["tris"]
    m = len(tris)
    if m == 0 or m > _MAX_TRIANGLES_FOR_RAYS:
        return None
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    e1, e2 = b - a, c - a
    areas, normals, cents = info["areas"], info["normals"], info["centroids"]
    usable = areas > 1e-6
    if not usable.any():
        return None
    p = np.where(usable, areas, 0.0)
    p = p / p.sum()
    rng = np.random.default_rng(0)
    pick = rng.choice(m, size=min(rays, int(usable.sum())), replace=False, p=p)
    diag = float(np.linalg.norm(info["bbox"]))
    eps = max(1e-4 * diag, 1e-4)
    thinnest, found = math.inf, 0
    for i in pick:
        d = -normals[i]
        orig = cents[i] + d * eps
        pvec = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, pvec)
        ok = np.abs(det) > 1e-12
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        tvec = orig - a
        u = np.einsum("ij,ij->i", tvec, pvec) * inv
        q = np.cross(tvec, e1)
        v = (q @ d) * inv
        t = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > eps)
        # only a surface seen from inside the material counts: its normal points along the ray
        hit &= (normals @ d) > 0.3
        if hit.any():
            thinnest = min(thinnest, float(t[hit].min()) + eps)
            found += 1
    if found == 0:
        return None
    return thinnest, found


def _mesh_checks(info: dict[str, Any], process: str, mat) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    e, closed = info["edges"], info["closed"]
    strict = process in ("fdm", "resin")
    if closed:
        out.append({"id": "closed", "title": "Mesh is closed", "status": "pass",
                    "detail": "Every edge is shared by two triangles, so the surface has no gaps."})
    else:
        bits = []
        if e["open"]:
            bits.append(f"{e['open']} open edge{'s' if e['open'] != 1 else ''}")
        if e["non_manifold"]:
            bits.append(f"{e['non_manifold']} edge{'s' if e['non_manifold'] != 1 else ''} shared by more than two faces")
        out.append({"id": "closed", "title": "Mesh is closed", "status": "fail" if strict else "warn",
                    "detail": f"The surface has {' and '.join(bits)}. "
                              + ("A slicer cannot tell inside from outside." if strict else "Other checks on it are less reliable.")})

    # size
    limit = dfm.SIZE_LIMIT_MM[{"cnc": "cnc_milling", "fdm": "fdm_print", "resin": "resin_print",
                               "injection_molding": "injection_molding"}[process]]
    big = float(info["bbox"].max())
    dims = " x ".join(f"{x:.4g}" for x in info["bbox"])
    ok = big <= limit
    out.append({"id": "size", "title": "Size", "status": "pass" if ok else "warn",
                "detail": (f"The longest side is {big:.4g} mm, inside the typical {limit:g} mm." if ok else
                           f"At {big:.4g} mm the longest side is over the typical {limit:g} mm for this process. It may need splitting or a larger machine."),
                "measured": f"{dims} mm", "limit": f"{limit:g} mm longest side"})

    # walls measured on the mesh
    lim = _WALL_POLYMER_CNC if (process == "cnc" and mat is not None and mat.category == "polymer") else _WALL[process]
    sampled = _sample_wall(info)
    if sampled is None:
        out.append({"id": "mesh_walls", "title": "Thin spots in the mesh", "status": "not_checked",
                    "detail": "The mesh was too fine, or too open, to measure wall thickness on."})
    else:
        thin, n = sampled
        status = "fail" if thin < lim[0] else "warn" if thin < lim[1] else "pass"
        word = {"fail": "is too thin for", "warn": "is thin for", "pass": "is fine for"}[status]
        out.append({"id": "mesh_walls", "title": "Thin spots in the mesh", "status": status,
                    "detail": f"The thinnest spot found {word} this process. It is measured at {n} sampled points, so a thinner spot between them is possible.",
                    "measured": f"thinnest {thin:.2g} mm", "limit": f"{lim[1]:g} mm"})

    if process == "fdm":
        out.append(_overhang(info))
    if process == "injection_molding":
        out.append(_draft(info))
    if process == "resin":
        out.append(_cavity(info))
    return out


def _overhang(info: dict[str, Any]) -> dict[str, Any]:
    n, area, cz = info["normals"], info["areas"], info["centroids"][:, 2]
    down = (n[:, 2] < -math.cos(math.radians(45.0))) & (cz > info["zmin"] + 0.2)
    total = float(area.sum())
    bad = float(area[down].sum())
    share = bad / total if total else 0.0
    ok = share < 0.01 or bad < 4.0
    return {"id": "overhang", "title": "Overhangs", "status": "pass" if ok else "warn",
            "detail": ("Almost nothing faces down more steeply than 45 degrees off the bed." if ok else
                       "Some faces lean out more than 45 degrees, so they will need support or a different orientation."),
            "measured": f"{bad:.3g} mm2, {share * 100:.0f}% of the surface", "limit": "45 degrees"}


def _draft(info: dict[str, Any]) -> dict[str, Any]:
    n, area = info["normals"], info["areas"]
    best = None
    for axis, name in enumerate("XYZ"):
        s = np.abs(n[:, axis])
        side = s < math.sin(math.radians(82.0))          # faces roughly parallel to the pull
        side_area = float(area[side].sum())
        if side_area <= 0:
            return {"id": "draft", "title": "Draft angle", "status": "pass",
                    "detail": "No faces run parallel to the pull direction, so none needs draft."}
        bare = float(area[side & (s < math.sin(math.radians(1.0)))].sum())
        frac = bare / side_area
        if best is None or frac < best[0]:
            best = (frac, name)
    frac, axis = best
    ok = frac < 0.05
    return {"id": "draft", "title": "Draft angle", "status": "pass" if ok else "warn",
            "detail": ("The walls lean at least 1 degree along the best pull direction, so the part will release." if ok else
                       f"Most walls along the best pull direction ({axis}) are straight. Add 1 to 2 degrees of draft so the part releases from the mold."),
            "measured": f"{frac * 100:.0f}% of wall area under 1 degree, pulling along {axis}", "limit": "1 degree"}


def _cavity(info: dict[str, Any]) -> dict[str, Any]:
    if not info["closed"]:
        return {"id": "drain", "title": "Trapped resin", "status": "not_checked",
                "detail": "This needs a closed mesh."}
    cav = info.get("cavities") or []
    if not cav:
        return {"id": "drain", "title": "Trapped resin", "status": "pass",
                "detail": "No sealed hollow inside the part, so no resin is trapped."}
    return {"id": "drain", "title": "Trapped resin", "status": "warn",
            "detail": f"The part has {len(cav)} sealed hollow{'s' if len(cav) != 1 else ''}. Uncured resin stays inside. Add a drain hole, or make it solid.",
            "measured": f"{sum(cav) / 1000.0:.3g} cm3 sealed"}


# ------------------------------------------------------------------ what this does not cover ---

def _not_checked(process: str, *, mesh_available: bool, sheet: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def nc(id_: str, title: str, detail: str) -> None:
        out.append({"id": id_, "title": title, "status": "not_checked", "detail": detail})

    if process == "cnc":
        nc("access", "Tool access, setups and tolerances", "These need a machining plan.")
    elif process == "sheet_metal":
        nc("flat", "Flat pattern, bend order and relief", "These need a sheet-metal model of the part.")
    elif process == "injection_molding":
        nc("tool", "Undercuts, parting line and gate", "These need a tool design.")
        if not mesh_available:
            nc("draft", "Draft angle", "This needs the part's mesh.")
    elif process == "fdm":
        nc("strength", "Strength across layers", "This depends on orientation and settings in the slicer.")
        if not mesh_available:
            nc("overhang", "Overhangs", "This needs the part's mesh.")
    elif process == "resin":
        nc("supports", "Support placement and orientation", "These are set in the slicer.")
        if not mesh_available:
            nc("drain", "Trapped resin", "This needs the part's mesh.")
    return out

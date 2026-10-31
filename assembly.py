"""
Assembly support for multi-part CAD generation.
"""
import math
import re
from typing import Any

import cadquery as cq

import assembly_checks
import materials
import screening
from cad_templates import TEMPLATES
from logging_config import get_logger

logger = get_logger(__name__)

# No part cap. Set MAX_ASSEMBLY_PARTS to an int to bring one back; None means unlimited.
MAX_ASSEMBLY_PARTS = None
RUNAWAY_PARTS = 20_000   # not a design limit: stops a typo such as "1000000000 bolts" from hanging the server
_MAX_COORD_MM = 1_000_000.0


def normalize_assembly_spec(raw: Any) -> list[dict[str, Any]]:
    """Check a parts list (fresh from the parser or read back from a stored job)
    and return it in the shape generate_assembly expects. Values are not changed,
    so the same list always builds the same assembly. Raises ValueError with a
    plain message if the list is unusable."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("the assembly has no parts")
    limit = MAX_ASSEMBLY_PARTS if MAX_ASSEMBLY_PARTS is not None else RUNAWAY_PARTS
    if len(raw) > limit:
        raise ValueError(f"the assembly has more than {limit} parts")
    out: list[dict[str, Any]] = []
    for i, part in enumerate(raw, start=1):
        if not isinstance(part, dict):
            raise ValueError(f"part {i} is not a part description")
        if not isinstance(part.get("part_type"), str):
            raise ValueError(f"part {i} has no part_type")
        params = part.get("parameters", {})
        if not isinstance(params, dict):
            raise ValueError(f"part {i} parameters must be a dictionary")
        spec = dict(part)
        spec["name"] = str(part.get("name") or f"part_{i}")[:80]
        spec["parameters"] = params
        for key in ("position", "rotation"):
            vec = part.get(key, (0, 0, 0))
            try:
                nums = tuple(float(v) for v in vec)
            except (TypeError, ValueError):
                raise ValueError(f"part {i} {key} must be three numbers") from None
            if len(nums) != 3 or not all(math.isfinite(v) and abs(v) <= _MAX_COORD_MM for v in nums):
                raise ValueError(f"part {i} {key} must be three reasonable numbers")
            spec[key] = nums
        out.append(spec)
    return out


def generate_assembly(parts_spec: list[dict[str, Any]]) -> cq.Assembly:
    """
    Generate an assembly from multiple parts.
    
    Each part spec should have:
    - name: Part name
    - part_type: Type of part
    - parameters: Part parameters
    - position: (x, y, z) position
    - rotation: (rx, ry, rz) rotation in degrees (optional)
    - constraints: List of constraints (optional)
    """
    assy = cq.Assembly()
    
    for part_spec in parts_spec:
        name = part_spec.get('name', 'part')
        part_type = part_spec.get('part_type')
        params = part_spec.get('parameters', {})
        position = part_spec.get('position', (0, 0, 0))
        rotation = part_spec.get('rotation', (0, 0, 0))
        
        # Generate the part
        if part_type in TEMPLATES:
            template_func = TEMPLATES[part_type]
            workplane = template_func(params)
            
            # Add to assembly with position and rotation
            loc = cq.Location(
                cq.Vector(*position),
                cq.Vector(1, 0, 0),
                rotation[0]
            ) * cq.Location(
                cq.Vector(0, 0, 0),
                cq.Vector(0, 1, 0),
                rotation[1]
            ) * cq.Location(
                cq.Vector(0, 0, 0),
                cq.Vector(0, 0, 1),
                rotation[2]
            )
            
            assy.add(workplane, name=name, loc=loc)
        else:
            # Previously silent - a typo'd or unsupported part_type in an
            # assembly spec just vanished from the output with no signal
            # anywhere. Now at least logged, so a shrinking assembly is
            # diagnosable instead of mysterious.
            logger.warning(
                "assembly part %r has unsupported part_type %r, skipping",
                name, part_type,
            )

    return assy


def get_assembly_parts(assy: cq.Assembly) -> list[tuple[str, cq.Shape]]:
    """Flatten an Assembly into (name, shape) pairs for every leaf part,
    with each part's geometry moved into its assembly-world location -
    so a bracket that was built at the origin and placed at (0, 0, 5)
    comes back already translated there.

    This exists for the multi-part DXF/PDF export path in exporters.py:
    export_all(compound, ...) on the merged compound gives one drawing
    of the whole assembly, but a real fabrication drawing set needs each
    sub-part's own front/top/side views, individually labeled - which
    means walking the assembly tree rather than flattening straight to
    one compound like STL/IGES/section-DXF already did.
    """
    parts: list[tuple[str, cq.Shape]] = []

    def _walk(node: cq.Assembly, parent_loc: cq.Location) -> None:
        world_loc = parent_loc * node.loc
        if node.obj is not None:
            shape = node.obj.val() if isinstance(node.obj, cq.Workplane) else node.obj
            parts.append((node.name, shape.moved(world_loc)))
        for child in node.children:
            _walk(child, world_loc)

    _walk(assy, cq.Location())
    return parts


_TRIGGER_PHRASES = re.compile(
    r"\b(?:an?\s+)?assembly\s+of\b|\bassembl(?:y|e|ed)\b|\bmultiple\s+parts\b|\btogether\b"
    r"|\binto\s+(?:one|a\s+single)\s+(?:unit|assembly)\b", re.I)
_SPLITTERS = re.compile(r"\s*(?:[,;]|\band\b|\bplus\b|\bwith\b|\bthen\b)\s*", re.I)
_COUNT_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_LEADING_COUNT = re.compile(r"^\s*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?!(?:mm|cm|m|in|inch|x|by)\b)", re.I)
_NAME_SKIP = {"a", "an", "the", "of", "mm", "cm", "m", "x", "by", "wide", "long", "thick", "tall", "high",
              "diameter", "with", "for", "in", "on", "to", "each"}
ASSEMBLY_PART_GAP_MM = 10.0
MAX_COUNT_PER_PART = None   # per-line count in a text description; None means unlimited (RUNAWAY_PARTS still applies)


def legacy_assembly_example() -> list[dict[str, Any]]:
    """The fixed two-part example that assembly jobs were built from before the
    description was read (a base plate with an L-bracket on it). Kept so a job saved
    in that period, which has no stored parts list, can still be rebuilt exactly."""
    return [
        {
            'name': 'base_plate',
            'part_type': 'flat_plate',
            'parameters': {
                'length_mm': 100.0, 'width_mm': 80.0, 'thickness_mm': 5.0,
                'hole_pattern': 'rectangular', 'hole_count_x': 4, 'hole_count_y': 3,
                'hole_diameter_mm': 3.2, 'corner_fillet_mm': 3.0,
            },
            'position': (0, 0, 0),
            'rotation': (0, 0, 0),
        },
        {
            'name': 'bracket',
            'part_type': 'l_bracket',
            'parameters': {
                'width_mm': 40.0, 'height_mm': 50.0, 'depth_mm': 30.0, 'thickness_mm': 3.0,
                'hole_count': 2, 'hole_diameter_mm': 3.2, 'fillet_radius_mm': 2.0,
            },
            'position': (0, 0, 5.0),
            'rotation': (0, 0, 0),
        },
    ]


def _recognized_part_type(text: str) -> str | None:
    """The part type named in `text`, or None when it names no part (for example
    "4 holes" or "5 mm fillets", which describe the part before them)."""
    from smart_parser import infer_part_type
    pt = infer_part_type(text, [], [])
    if pt in ("assembly", "freeform"):
        return None
    if pt == "flat_plate" and not re.search(r"\b(plate|blank|flat)\b", text, re.I):
        return None            # infer_part_type falls back to flat_plate when it finds no keyword
    return pt


def split_assembly_description(description: str) -> list[tuple[str, int]]:
    """Cut a description into (text for one part, how many) pieces. A piece that
    names no part is added to the part before it, so "plate 100 x 80 with 4 holes
    and an l bracket" gives the plate its holes."""
    cleaned = _TRIGGER_PHRASES.sub(" ", description or "")
    parts: list[dict[str, Any]] = []
    for seg in _SPLITTERS.split(cleaned):
        seg = seg.strip(" .")
        if not seg:
            continue
        count, body = 1, seg
        m = _LEADING_COUNT.match(seg)
        if m:
            tok = m.group(1).lower()
            count = int(tok) if tok.isdigit() else _COUNT_WORDS[tok]
            body = seg[m.end():].strip()
        else:
            first = seg.split(None, 1)[0].lower()
            if first in ("a", "an", "the"):
                body = seg.split(None, 1)[1] if len(seg.split(None, 1)) > 1 else ""
        if re.search(r"\bbrackets?\b", body, re.I) and not re.search(r"\b(?:l|t|tee|angle|channel)[- ]?brackets?\b", body, re.I):
            body = re.sub(r"\bbrackets?\b", "l bracket", body, flags=re.I)   # a plain "bracket" means the L-bracket template
        if _recognized_part_type(body):
            parts.append({"text": body, "count": max(1, min(count, MAX_COUNT_PER_PART or RUNAWAY_PARTS))})
        elif parts:
            parts[-1]["text"] += " " + seg
    return [(p["text"], p["count"]) for p in parts]


def _part_name(text: str, part_type: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", text.lower()) if w not in _NAME_SKIP]
    name = "_".join(words[:3])[:40].strip("_")
    return name or part_type


def _template_bbox(part_type: str, params: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    """(xmin, xmax, ymin, ymax, zmin, zmax) of one part built at the origin."""
    bb = TEMPLATES[part_type](dict(params)).val().BoundingBox()
    return bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax


def _check_params(part_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """The part's values after the same validator a single part goes through."""
    from validator import validate_parameters
    corrected, _ = validate_parameters(part_type, dict(params))
    return corrected


def _unique_name(base: str, used: dict[str, int]) -> str:
    used[base] = used.get(base, 0) + 1
    return base if used[base] == 1 else f"{base}_{used[base]}"


def _snake(text: Any, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:limit]


def layout_in_a_row(
    pieces: list[tuple[str, str, dict[str, Any]]],
    *,
    bbox_of=None,
    gap_mm: float = ASSEMBLY_PART_GAP_MM,
) -> list[dict[str, Any]]:
    """Parts side by side along X with a gap, centred on Y and sitting on z = 0, so
    none of them overlap. `pieces` is (name, part_type, parameters) in order. Pass
    `bbox_of` to measure parts some other way (tests do, to avoid the CAD kernel)."""
    bbox_of = bbox_of or _template_bbox
    specs: list[dict[str, Any]] = []
    cursor = 0.0
    for name, part_type, params in pieces:
        try:
            xmin, xmax, ymin, ymax, zmin, _zmax = bbox_of(part_type, params)
        except Exception as exc:  # noqa: BLE001 - any kernel error
            raise ValueError(f"could not build '{name}': {exc}") from exc
        specs.append({
            "name": name, "part_type": part_type, "parameters": dict(params),
            "position": (round(cursor - xmin, 3), round(-(ymin + ymax) / 2.0, 3), round(-zmin, 3)),
            "rotation": (0, 0, 0),
        })
        cursor += (xmax - xmin) + gap_mm
    return specs


def assembly_spec_from_parts(
    raw_parts: Any,
    *,
    layout: str = "row",
    bbox_of=None,
    known_types=None,
) -> list[dict[str, Any]]:
    """A parts list from something that already names the parts, such as the chat
    planner: [{"part_type", "parameters", "name"?, "count"?, "position"?, "rotation"?}].

    layout "row" (the default) places the parts side by side, ignoring any positions
    given, so a bigger plate never overlaps its neighbour after an edit. layout
    "custom" keeps the positions and rotations given, and needs one for every part.
    Each part's values go through the validator. Raises ValueError with a plain message."""
    known = TEMPLATES if known_types is None else known_types
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("the assembly has no parts")
    if layout not in ("row", "custom"):
        raise ValueError("layout must be 'row' or 'custom'")
    expanded: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    used: dict[str, int] = {}
    for i, part in enumerate(raw_parts, start=1):
        if not isinstance(part, dict):
            raise ValueError(f"part {i} is not a part description")
        ptype = str(part.get("part_type") or "").strip().lower()
        if ptype in ("", "assembly", "freeform", "unknown"):
            raise ValueError(f"part {i} has type '{ptype or 'none'}', which cannot be used in an assembly")
        if ptype not in known:
            raise ValueError(f"part {i} has an unknown type '{ptype}'")
        params = part.get("parameters", {})
        if not isinstance(params, dict):
            raise ValueError(f"part {i} parameters must be a dictionary")
        try:
            count = int(part.get("count", 1))
        except (TypeError, ValueError):
            raise ValueError(f"part {i} count must be a whole number") from None
        count_limit = MAX_COUNT_PER_PART or RUNAWAY_PARTS
        if not 1 <= count <= count_limit:
            raise ValueError(f"part {i} count must be from 1 to {count_limit}")
        corrected = _check_params(ptype, params)
        base = _snake(part.get("name") or "") or _snake(ptype)
        for _ in range(count):
            expanded.append((_unique_name(base, used), ptype, corrected, part))
    if len(expanded) < 2:
        raise ValueError("Describe at least two parts, for example: a base plate and an L bracket")
    part_limit = MAX_ASSEMBLY_PARTS if MAX_ASSEMBLY_PARTS is not None else RUNAWAY_PARTS
    if len(expanded) > part_limit:
        raise ValueError(f"The assembly would have more than {part_limit} parts")

    if layout == "custom":
        out = []
        for name, ptype, params, src in expanded:
            if "position" not in src:
                raise ValueError("custom layout needs a position for every part")
            out.append({"name": name, "part_type": ptype, "parameters": dict(params),
                        "position": src["position"], "rotation": src.get("rotation", (0, 0, 0))})
        return normalize_assembly_spec(out)
    return normalize_assembly_spec(layout_in_a_row([(n, t, p) for n, t, p, _ in expanded], bbox_of=bbox_of))


def parse_assembly_description(description: str, *, bbox_of=None) -> list[dict[str, Any]]:
    """Read an assembly description into a parts list.

    "a base plate 100 x 80 mm with 4 holes and an l bracket" gives two parts. Each
    piece is read with the same parser a single part uses, and its values go through
    the same validator. Parts are laid out in a row along X with a small gap and
    sitting on z = 0, so they never overlap. How the parts mate is not guessed:
    positions and rotations can be edited in the stored list.

    Raises ValueError with a plain message when fewer than two parts are found.
    """
    from smart_parser import parse_description

    pieces = split_assembly_description(description)
    raw: list[dict[str, Any]] = []
    for text, count in pieces:
        parsed = parse_description(text)
        raw.append({"part_type": parsed.part_type, "parameters": dict(parsed.parameters),
                    "name": _part_name(text, parsed.part_type), "count": count})
    return assembly_spec_from_parts(raw, bbox_of=bbox_of)


# ==========================================================================

def transform_to_location(t) -> "cq.Location":
    """mating.Transform -> cq.Location. Location(Plane) maps local coordinates
    to the plane's frame: global = origin + x*xDir + y*yDir + z*zDir, with
    yDir = normal x xDir, i.e. exactly the columns of the rotation matrix."""
    origin, x_dir, normal = t.plane_args()
    return cq.Location(cq.Plane(origin=origin, xDir=x_dir, normal=normal))


def build_parts(resolved) -> dict:
    """pid -> Workplane built from each part's validated parameters."""
    from cad_templates import TEMPLATES
    built = {}
    for pid in resolved.order:
        p = resolved.parts[pid]
        if p.part_type not in TEMPLATES:
            raise ValueError(f"unsupported part type {p.part_type!r} for part {pid!r}")
        built[pid] = TEMPLATES[p.part_type](p.parameters)
    return built


def assemble_resolved(resolved, built: dict) -> "cq.Assembly":
    assy = cq.Assembly(name=resolved.name)
    for pid in resolved.order:
        assy.add(built[pid], name=pid, loc=transform_to_location(resolved.parts[pid].placement))
    return assy


def _placed_shape(wp, t):
    shape = wp.val()
    loc = transform_to_location(t)
    return shape.moved(loc) if hasattr(shape, "moved") else shape.located(loc)


def check_interference(resolved, built: dict, tol_mm3: float = 1.0) -> list:
    """Boolean-intersect every pair of placed solids. Surfaces that merely
    touch (a face seated on a face, a line-to-line fit) have ~zero common
    volume and pass; anything above tol_mm3 is real overlap. Returns
    mating.Finding objects; an OCCT failure on a pair is reported as an
    inconclusive WARNING, never silently treated as 'no overlap'."""
    from mating import Finding
    placed = {pid: _placed_shape(built[pid], resolved.parts[pid].placement) for pid in resolved.order}
    boxes = {pid: s.BoundingBox() for pid, s in placed.items()}
    out = []
    ids = list(resolved.order)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            ba, bb = boxes[a], boxes[b]
            if (ba.xmax < bb.xmin or bb.xmax < ba.xmin or ba.ymax < bb.ymin
                    or bb.ymax < ba.ymin or ba.zmax < bb.zmin or bb.zmax < ba.zmin):
                continue
            try:
                vol = placed[a].intersect(placed[b]).Volume()
            except Exception as exc:  # noqa: BLE001 - OCCT boolean failures are varied
                out.append(Finding("warning", "interference_inconclusive",
                                   f"could not evaluate overlap of {a} and {b}: {exc}", a, b))
                continue
            if vol > tol_mm3:
                out.append(Finding("error", "interference",
                                   f"{a} and {b} overlap by {vol:,.1f} mm3: the parts physically collide", a, b))
    return out


# ==========================================================================
# Which engine builds an assembly
#
# Two engines share part_type "assembly". The spec decides, never the wording:
#   mated - the spec carries mates (or is a stored, resolved mate spec): parts are
#           placed by solving their mates and generation is gated on interface
#           fit and real-solid overlap (assembly_spec.py, mating.py).
#   row   - a plain parts list: parts laid out side by side (assembly_spec_from_parts).
# ==========================================================================

def assembly_route(params: dict | None) -> str:
    """"mated" or "row" for an assembly's parameters."""
    p = params or {}
    if isinstance(p.get("assembly"), dict) and p["assembly"]:
        return "mated"
    mates = p.get("mates")
    if (isinstance(mates, list) and mates) or p.get("layout") == "mated":
        return "mated"
    return "row"


def mated_spec(params: dict) -> dict:
    """The mate-engine spec for an assembly's parameters: either the stored spec
    ({"assembly": {...}}) or the chat planner's mated shape
    ({"parts": [{"id", "part_type", "parameters", "placement"?}], "mates": [...], "root"?, "name"?})."""
    given = params.get("assembly")
    if isinstance(given, dict) and given:
        return given
    parts = []
    for p in params.get("parts") or []:
        item = {"id": p.get("id"), "part_type": p.get("part_type"), "parameters": dict(p.get("parameters") or {})}
        if p.get("placement") is not None:
            item["placement"] = p["placement"]
        parts.append(item)
    spec: dict[str, Any] = {"name": params.get("name") or "assembly", "parts": parts,
                            "mates": [dict(m) for m in params.get("mates") or []]}
    if params.get("root"):
        spec["root"] = params["root"]
    return spec


# ==========================================================================
# Geometric checks for any built assembly (parts list, overlaps, floating parts, mass)
#
# Works on the built cq.Assembly plus the part specs that made it (names must match the
# names the parts were added under), so it serves both engines: the mate engine passes
# the resolved parts (name = part id) and the row layout passes its parts list.
# ==========================================================================

def _exact_box(shape: "cq.Shape") -> dict[str, tuple[float, float, float]]:
    """Bounding box from the exact geometry, never a mesh (see exporters._exact_bounds)."""
    from exporters import _exact_bounds

    x0, y0, z0, x1, y1, z1 = _exact_bounds(shape)
    return {"min": (x0, y0, z0), "max": (x1, y1, z1)}


def assembly_checks_report(assembly: "cq.Assembly", placed: list[dict[str, Any]], *,
                           check_floating: bool = True) -> dict[str, Any]:
    """Interference, floating-part, parts list and mass for a built assembly.

    `placed` is a list of {name, part_type, parameters, material?}. `check_floating` is False
    for the side-by-side row layout, where parts are spaced apart on purpose and "does not
    touch the rest" would be a false alarm."""
    shapes = dict(get_assembly_parts(assembly))
    boxes = {name: _exact_box(shape) for name, shape in shapes.items()}
    volumes = {name: float(shape.Volume()) for name, shape in shapes.items()}

    def common_volume(a: str, b: str) -> float:
        return float(shapes[a].intersect(shapes[b]).Volume())

    report = assembly_checks.build_report(placed, boxes, common_volume, volumes)
    if not check_floating and report.get("disconnected_groups"):
        report["disconnected_groups"] = []
        report["notes"] = [n for n in report.get("notes", []) if "separate groups" not in n]
        report["clean"] = not (report.get("interferences") or report.get("unchecked_pairs"))
    # mass and centre of mass: each part's own centroid, weighted by volume x density
    materials_by_name = {p["name"]: p.get("material") for p in placed}
    centroids = {}
    for name, shape in shapes.items():
        try:
            centroids[name] = tuple(shape.Center().toTuple())
        except Exception:  # noqa: BLE001 - a part we cannot locate is reported as missing, not guessed
            centroids[name] = None
    report["mass_properties"] = screening.mass_properties([
        {"name": n, "volume_mm3": volumes.get(n), "density_g_cm3": materials.density_g_cm3(materials_by_name.get(n)),
         "centroid": centroids.get(n)}
        for n in shapes])
    report["bom_csv"] = assembly_checks.bom_to_csv(report["bom"])
    report["parts"] = _parts_table(placed, boxes, volumes, materials_by_name)
    return report


def _parts_table(placed: list[dict[str, Any]], boxes: dict, volumes: dict, material_by_name: dict) -> list[dict[str, Any]]:
    """One row per part for the Studio table: where it sits, how big it is, what it weighs. Position is
    the one the spec placed it at, or the middle of its box when it was placed by mates. Advisory: any
    part that cannot be measured is left out rather than guessed."""
    rows: list[dict[str, Any]] = []
    try:
        for p in placed:
            name = p.get("name") or p.get("id")
            box = boxes.get(name)
            if box is None:
                continue
            lo, hi = box["min"], box["max"]
            pos = p.get("position")
            if not pos:
                pos = [(a + b) / 2.0 for a, b in zip(lo, hi)]
            dens = materials.density_g_cm3(material_by_name.get(name))
            vol = volumes.get(name)
            rows.append({
                "name": str(name), "part_type": p.get("part_type"),
                "position_mm": [round(float(x), 3) for x in pos],
                "size_mm": [round(float(b - a), 3) for a, b in zip(lo, hi)],
                "mass_g": round(vol / 1000.0 * dens, 3) if (vol and dens) else None,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("assembly parts table failed: %s", exc)
    return rows

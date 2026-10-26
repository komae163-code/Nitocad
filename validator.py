"""
Geometric validation and auto-correction for CAD parameters.
"""
import re
from typing import Any


class ValidationError(Exception):
    """Raised when geometry is invalid."""
    pass

class ValidationResult:
    """Result of validation with corrections."""
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.corrections: dict[str, Any] = {}
        # Sizes the user typed that were kept as typed: {param: {'value', 'suggested'}}.
        self.flags: dict[str, Any] = {}
        self.is_valid: bool = True
    
    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)
    
    def add_correction(self, param: str, old_value: Any, new_value: Any):
        self.corrections[param] = {'old': old_value, 'new': new_value}

_NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(mm|cm|m|inches|inch|in|ft|feet|\"|')?(?![\w])", re.I)
_UNIT_TO_MM = {"cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4, "ft": 304.8, "feet": 304.8, "'": 304.8}
# The trailing "what we did" part of a warning: ", reducing to 2.5mm" (the older validators) or " Changed hole_diameter_mm
# from 12 to 10." (the ones written with _fix). When the person typed the size and it is kept, that part is cut off so the
# note does not claim a change that did not happen.
_ACTION = re.compile(r",?\s*(?:(?:reducing|capping|increasing|raising|clamping|limiting|lowering|setting|adjusting|using)\b|Changed\s+\w+\s+from\b)[^;]*$", re.I)


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.finditer(text or ""):
        try:
            value = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        out.append(value)
        scale = _UNIT_TO_MM.get((m.group(2) or "").lower())
        if scale:
            out.append(value * scale)
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


_COUNT_NOUN = re.compile(
    r"^\s*(?:(?:per|on|in|each)\s+\w+\s+)?(?:holes?|bolts?|screws?|teeth|tooth|pcs|pieces?|legs?|ribs?|slots?|segments?|lanes?|barrels?|cells?|plates?|parts?)\b",
    re.I,
)
_COUNT_KEYS = ("count", "teeth", "num", "lane", "barrel", "segment")


def typed_keys(description: str | None, params: dict[str, Any]) -> set[str]:
    """Numeric parameters whose value appears in the user's own words (any common
    length unit). A value that is not there came from a default, so it is safe to adjust.
    A number followed by a counting word ("2 holes") can only be a count, so it never
    claims a length that happens to equal it."""
    text = re.sub(r"(?<=\d)\s*[x\u00d7*]\s*(?=\d)", " ", description or "", flags=re.I)  # 50x60x40
    lengths: list[float] = []
    counts: list[float] = []
    for m in _NUMBER.finditer(text):
        try:
            value = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if not m.group(2) and _COUNT_NOUN.match(text[m.end():m.end() + 32]):
            counts.append(value)
            continue
        lengths.append(value)
        scale = _UNIT_TO_MM.get((m.group(2) or "").lower())
        if scale:
            lengths.append(value * scale)
    out: set[str] = set()
    for k, val in params.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        is_count_key = any(s in k.lower() for s in _COUNT_KEYS)
        pool = counts + lengths if is_count_key else lengths
        if any(_close(float(val), n) for n in pool):
            out.add(k)
    return out


def _as_flag(message: str, suggested: Any) -> str:
    base = _ACTION.sub("", message).rstrip(" ,;.")
    shown = f"{suggested:g}" if isinstance(suggested, (int, float)) else str(suggested)
    return f"{base}. Kept as you typed; a safer value is {shown}."


def merge_notes(warnings: list[str], corrections: dict[str, Any]) -> list[str]:
    """One user-facing note per change: a warning that already states a correction's old and
    new value covers it, so the correction is not listed a second time."""
    notes = list(dict.fromkeys(str(w) for w in warnings))
    for key, c in (corrections or {}).items():
        try:
            old, new = float(c["old"]), float(c["new"])
        except (KeyError, TypeError, ValueError):
            notes.append(f"{key} adjusted")
            continue
        if not any(any(_close(old, n) for n in _numbers_in(w)) and any(_close(new, n) for n in _numbers_in(w)) for w in notes):
            notes.append(f"{key}: {c['old']} to {c['new']}")
    return notes


def validate_motor_mount(params: dict[str, Any]) -> ValidationResult:
    """Validate motor mount parameters."""
    result = ValidationResult()
    
    thickness = params.get('thickness_mm', 3.0)
    hole_diameter = params.get('hole_diameter_mm', 3.2)
    fillet_radius = params.get('fillet_radius_mm', 1.0)
    motor_size = params.get('motor_size_mm', 50.0)
    
    # Hole diameter must be reasonable
    if hole_diameter > 20:
        result.add_warning(f"Hole diameter {hole_diameter}mm seems large, capping at 20mm")
        result.corrections['hole_diameter_mm'] = {'old': hole_diameter, 'new': 20.0}
    
    # Fillet radius must be less than half thickness
    if fillet_radius >= thickness / 2:
        new_fillet = thickness / 2 - 0.1
        result.add_warning(f"Fillet radius {fillet_radius}mm too large for {thickness}mm thickness, reducing to {new_fillet:.1f}mm")
        result.corrections['fillet_radius_mm'] = {'old': fillet_radius, 'new': new_fillet}
    
    # Motor size sanity check
    if motor_size < 20 or motor_size > 200:
        result.add_warning(f"Motor size {motor_size}mm unusual, using default 50mm")
        result.corrections['motor_size_mm'] = {'old': motor_size, 'new': 50.0}
    
    return result

def validate_l_bracket(params: dict[str, Any]) -> ValidationResult:
    """Validate L-bracket parameters."""
    result = ValidationResult()
    
    width = params.get('width_mm', 50.0)
    height = params.get('height_mm', 60.0)
    depth = params.get('depth_mm', 40.0)
    thickness = params.get('thickness_mm', 3.0)
    hole_diameter = params.get('hole_diameter_mm', 3.2)
    fillet_radius = params.get('fillet_radius_mm', 1.0)
    
    # All dimensions must be positive
    for dim_name, dim_value in [('width', width), ('height', height), ('depth', depth), ('thickness', thickness)]:
        if dim_value <= 0:
            result.add_error(f"{dim_name} must be positive, got {dim_value}")
    
    # Thickness must be reasonable relative to other dimensions
    if thickness > min(width, height, depth) / 3:
        new_thickness = min(width, height, depth) / 3
        result.add_warning(f"Thickness {thickness}mm too large, reducing to {new_thickness:.1f}mm")
        result.corrections['thickness_mm'] = {'old': thickness, 'new': new_thickness}
    
    # Fillet radius must be less than half thickness
    if fillet_radius >= thickness / 2:
        new_fillet = thickness / 2 - 0.1
        result.add_warning(f"Fillet radius {fillet_radius}mm too large, reducing to {new_fillet:.1f}mm")
        result.corrections['fillet_radius_mm'] = {'old': fillet_radius, 'new': new_fillet}

    # A hole does not have to be thinner than the metal (a 6 mm hole in 5 mm plate is ordinary). It has to fit
    # in its leg: 2 mm of metal either side across the width, and clear of the bend, where the template puts
    # the first hole 10 mm from the end of the leg. The row has to fit along the shorter leg too.
    eff_thickness = result.corrections.get('thickness_mm', {}).get('new', thickness)
    if all(isinstance(x, (int, float)) and x > 0 for x in (width, height, depth, eff_thickness)):
        _bracket_holes(result, params, min(height, depth - eff_thickness), 'hole_count', 'hole_diameter_mm', 2, 3.2,
                       min(width - 4.0, 2 * (10.0 - eff_thickness - 2.0)),
                       f"A hole in a {width} mm wide bracket with {eff_thickness:g} mm legs needs 2 mm of metal each side, and to clear the bend.")

    return result

def validate_flat_plate(params: dict[str, Any]) -> ValidationResult:
    """Validate flat plate parameters."""
    result = ValidationResult()
    
    length = params.get('length_mm', 100.0)
    width = params.get('width_mm', 80.0)
    thickness = params.get('thickness_mm', 5.0)
    hole_diameter = params.get('hole_diameter_mm', 3.2)
    corner_fillet = params.get('corner_fillet_mm', 3.0)
    hole_count_x = params.get('hole_count_x', 0)
    hole_count_y = params.get('hole_count_y', 0)
    
    # Dimensions must be positive
    if length <= 0 or width <= 0 or thickness <= 0:
        result.add_error("All dimensions must be positive")
    
    # A hole does not have to be thinner than the plate. It has to fit: the template keeps the outer holes' centres
    # 10 mm in from the edges whenever there is more than one in a direction, so 2 mm of metal left at the edge means
    # at most 16 mm across. A single hole sits in the middle and can be as wide as the plate less 2 mm each side.
    pattern = params.get('hole_pattern', 'rectangular')
    count_x, count_y = params.get('hole_count_x', 0), params.get('hole_count_y', 0)
    has_holes = count_x > 0 and (count_y > 0 or pattern == 'circular')
    if has_holes and length > 0 and width > 0 and thickness > 0:
        if pattern == 'circular':
            limit = 16.0 if count_x > 1 else min(length, width) - 4.0
        else:
            limit = min(16.0 if count_x > 1 else length - 4.0, 16.0 if count_y > 1 else width - 4.0)
        if hole_diameter > limit:
            new_hole = max(limit, 0.5)
            result.add_warning(f"Hole diameter {hole_diameter}mm does not fit a {length:g} x {width:g} mm plate, reducing to {new_hole:.1f}mm")
            result.corrections['hole_diameter_mm'] = {'old': hole_diameter, 'new': new_hole}
            hole_diameter = new_hole
        # Neighbouring holes need 2 mm between them. Fewer holes if they do not fit in the row.
        for key, count, span in (('hole_count_x', count_x, length - 20), ('hole_count_y', count_y, width - 20)):
            if pattern == 'rectangular' and count > 1 and span > 0:
                fit = int(span // (hole_diameter + 2.0)) + 1
                if count > fit:
                    result.add_warning(f"{count} holes of {hole_diameter:g} mm do not fit in a row, using {max(fit, 1)}")
                    result.corrections[key] = {'old': count, 'new': max(fit, 1)}

    # Corner fillet must be less than quarter of smallest dimension
    max_fillet = min(length, width) / 4
    if corner_fillet >= max_fillet:
        new_fillet = max_fillet - 0.1
        result.add_warning(f"Corner fillet {corner_fillet}mm too large, reducing to {new_fillet:.1f}mm")
        result.corrections['corner_fillet_mm'] = {'old': corner_fillet, 'new': new_fillet}
    
    # Hole count sanity check. Zero in both directions means a plate with no holes, which is a real request
    # ("plain plate, no holes"), so it is left alone. A negative count, or zero in only one direction, is
    # still corrected.
    if hole_count_x == 0 and hole_count_y == 0:
        pass
    elif hole_count_x < 1 or hole_count_y < 1:
        result.add_warning("Hole count must be at least 1")
        result.corrections['hole_count_x'] = {'old': hole_count_x, 'new': max(1, hole_count_x)}
        result.corrections['hole_count_y'] = {'old': hole_count_y, 'new': max(1, hole_count_y)}
    
    return result

def validate_shaft(params: dict[str, Any]) -> ValidationResult:
    """Validate shaft parameters."""
    result = ValidationResult()
    
    diameter = params.get('diameter_mm', 10.0)
    length = params.get('length_mm', 50.0)
    
    if diameter <= 0 or length <= 0:
        result.add_error("Diameter and length must be positive")
    
    # Length should be at least diameter
    if length < diameter:
        result.add_warning(f"Length {length}mm < diameter {diameter}mm, adjusting")
        result.corrections['length_mm'] = {'old': length, 'new': diameter * 2}
    
    return result

def validate_gear(params: dict[str, Any]) -> ValidationResult:
    """Validate gear parameters."""
    result = ValidationResult()
    
    module = params.get('module', 2.0)
    teeth = params.get('teeth', 20)
    bore = params.get('bore_diameter_mm', 5.0)
    
    if teeth < 3:
        result.add_error(f"A gear needs at least 3 teeth, got {teeth}")
    elif teeth < 10:
        result.add_warning(f"Gear with {teeth} teeth may have undercut, minimum recommended is 17")

    # Past about 25 degrees the flanks get so wide at the base circle that neighbouring teeth run into each
    # other, and below 14.5 they are too steep to be a standard gear. 14.5, 20 and 25 are the standard angles.
    pressure = params.get('pressure_angle', 20.0)
    if not 14.5 <= pressure <= 25.0:
        new_pressure = min(25.0, max(14.5, pressure))
        result.add_warning(f"Pressure angle {pressure}° is outside the 14.5° to 25° range, using {new_pressure}°")
        result.corrections['pressure_angle'] = {'old': pressure, 'new': new_pressure}
    
    if module <= 0:
        result.add_error("Module must be positive")
    
    # Bore must be smaller than pitch diameter
    pitch_diameter = module * teeth
    if bore >= pitch_diameter * 0.8:
        new_bore = pitch_diameter * 0.5
        result.add_warning(f"Bore {bore}mm too large for gear, reducing to {new_bore:.1f}mm")
        result.corrections['bore_diameter_mm'] = {'old': bore, 'new': new_bore}
        bore = new_bore

    # The teeth stand on the root circle (pitch diameter - 2.5 module). A bore that gets within 1 mm of it
    # cuts the roots and splits the gear into pieces.
    if module > 0 and teeth >= 3:
        max_bore = module * teeth - 2.5 * module - 2.0
        if max_bore <= 0:
            result.add_error(f"A {teeth}-tooth gear of module {module:g} is too small to carry a bore.")
        elif bore > max_bore:
            result.add_warning(f"Bore {bore:g}mm would cut into the tooth roots, reducing to {max_bore:.1f}mm")
            result.corrections['bore_diameter_mm'] = {'old': params.get('bore_diameter_mm', 5.0), 'new': max_bore}

    return result

def validate_bearing(params: dict[str, Any]) -> ValidationResult:
    """Validate bearing parameters."""
    result = ValidationResult()
    
    inner = params.get('inner_diameter_mm', 10.0)
    outer = params.get('outer_diameter_mm', 20.0)
    width = params.get('width_mm', 5.0)
    
    if outer <= inner:
        result.add_error(f"Outer diameter {outer}mm must be greater than inner {inner}mm")
        result.corrections['outer_diameter_mm'] = {'old': outer, 'new': inner * 2}
    
    if width <= 0:
        result.add_error("Width must be positive")
    
    return result

def validate_simple_box(params: dict[str, Any]) -> ValidationResult:
    """Validate box parameters."""
    result = ValidationResult()
    
    length = params.get('length_mm', 100.0)
    width = params.get('width_mm', 80.0)
    height = params.get('height_mm', 50.0)
    wall = params.get('wall_thickness_mm', 3.0)
    
    # Wall thickness must be less than half of smallest dimension
    min_dim = min(length, width, height)
    if wall >= min_dim / 2:
        new_wall = min_dim / 2 - 0.5
        result.add_warning(f"Wall thickness {wall}mm too large, reducing to {new_wall:.1f}mm")
        result.corrections['wall_thickness_mm'] = {'old': wall, 'new': new_wall}
    
    return result

def validate_connecting_rod(params: dict[str, Any]) -> ValidationResult:
    """Validate connecting rod parameters."""
    result = ValidationResult()

    center_distance = params.get('center_distance_mm', 120.0)
    if center_distance <= 0:
        result.add_error("center_distance_mm must be positive")

    big_bore = params.get('big_end_diameter_mm', 24.0)
    big_boss = params.get('big_end_boss_diameter_mm', 40.0)
    small_bore = params.get('small_end_diameter_mm', 12.0)
    small_boss = params.get('small_end_boss_diameter_mm', 22.0)

    for bore_name, bore_val in (('big_end_diameter_mm', big_bore), ('small_end_diameter_mm', small_bore)):
        if bore_val <= 0:
            result.add_error(f"{bore_name} must be positive")

    # A boss must have enough material around its own bore to be a
    # boss at all - same failure mode as validate_bearing's
    # outer-not-greater-than-inner check, applied to each end.
    if big_boss > 0 and big_bore > 0 and big_boss <= big_bore:
        new_boss = big_bore * 1.6
        result.add_error(f"big_end_boss_diameter_mm ({big_boss}mm) must exceed big_end_diameter_mm ({big_bore}mm)")
        result.add_correction('big_end_boss_diameter_mm', big_boss, new_boss)
    if small_boss > 0 and small_bore > 0 and small_boss <= small_bore:
        new_boss = small_bore * 1.6
        result.add_error(f"small_end_boss_diameter_mm ({small_boss}mm) must exceed small_end_diameter_mm ({small_bore}mm)")
        result.add_correction('small_end_boss_diameter_mm', small_boss, new_boss)

    # If the two bosses (as currently sized) would physically overlap
    # given the requested center distance, shrink neither silently -
    # this is exactly the kind of infeasible-input case the shaft/gear
    # validators handle by widening the separating dimension instead of
    # guessing which of two conflicting sizes the caller cared about.
    corrected_big_boss = result.corrections.get('big_end_boss_diameter_mm', {}).get('new', big_boss)
    corrected_small_boss = result.corrections.get('small_end_boss_diameter_mm', {}).get('new', small_boss)
    min_center_distance = (corrected_big_boss + corrected_small_boss) / 2
    if center_distance > 0 and center_distance < min_center_distance:
        new_distance = min_center_distance + 5.0
        result.add_warning(
            f"center_distance_mm ({center_distance}mm) is too short for the boss sizes "
            f"given - the two ends would overlap. Increasing to {new_distance:.1f}mm."
        )
        result.add_correction('center_distance_mm', center_distance, new_distance)

    shank_width = params.get('shank_width_mm', 14.0)
    if shank_width <= 0:
        result.add_error("shank_width_mm must be positive")

    thickness = params.get('thickness_mm', 12.0)
    if thickness <= 0:
        result.add_error("thickness_mm must be positive")

    return result

def validate_crankshaft(params: dict[str, Any]) -> ValidationResult:
    """Validate crankshaft parameters."""
    result = ValidationResult()

    num_throws = params.get('num_throws', 4)
    if not isinstance(num_throws, int) or num_throws < 1:
        result.add_error("num_throws must be a positive integer")
    elif num_throws > 12:
        # Not a hard geometric limit like the others here - past this,
        # build time and OCCT boolean-union robustness (many discrete
        # bodies unioned in sequence) both degrade badly enough that a
        # warning belongs here even though it would technically build.
        result.add_warning(
            f"num_throws={num_throws} is unusually high and will be slow to build "
            "and more likely to hit an OCCT union failure - verify this is intentional."
        )

    stroke = params.get('stroke_mm', 80.0)
    if stroke <= 0:
        result.add_error("stroke_mm must be positive")

    main_dia = params.get('main_journal_diameter_mm', 50.0)
    rod_dia = params.get('rod_journal_diameter_mm', 45.0)
    for name, val in (('main_journal_diameter_mm', main_dia), ('rod_journal_diameter_mm', rod_dia)):
        if val <= 0:
            result.add_error(f"{name} must be positive")

    # This isn't a hard geometric failure mode - CadQuery's union handles
    # overlapping solids fine regardless of how close the rod journal's
    # offset is to the main axis. It's a plausibility check: a stroke
    # small enough that the rod journal barely clears the main/rod
    # journal envelopes doesn't read as an actual crank throw anymore,
    # same spirit as validate_gear()'s "few teeth -> undercut" warning.
    crank_radius = stroke / 2
    min_radius = (main_dia + rod_dia) / 4 + 5.0
    if crank_radius > 0 and crank_radius < min_radius:
        new_stroke = min_radius * 2
        result.add_warning(
            f"stroke_mm ({stroke}mm) is too small for the given journal diameters - "
            f"the rod journal would overlap the main journal. Increasing to {new_stroke:.1f}mm."
        )
        result.add_correction('stroke_mm', stroke, new_stroke)

    phase_angles = params.get('phase_angles_deg')
    if phase_angles is not None and isinstance(num_throws, int) and len(phase_angles) != num_throws:
        result.add_warning(
            f"phase_angles_deg has {len(phase_angles)} entries but num_throws is "
            f"{num_throws} - the template will fall back to even spacing."
        )

    return result


# ---------------------------------------------------------------------------
# Civil elements (road_segment, box_culvert, retaining_wall)
#
# Geometry only. These checks keep the solid buildable and the numbers sane.
# They do not check a design code, loads, hydraulics or soil, so every civil
# result carries the notice below.
# ---------------------------------------------------------------------------

CIVIL_NOTICE = (
    "Preliminary concept geometry. Not checked against any design code and not "
    "for construction. Have a licensed engineer verify the design."
)
CIVIL_MAX_LENGTH_MM = 500_000.0      # 500 m, keeps the OCCT kernel and exports sane
CIVIL_MIN_FEATURE_MM = 10.0


def _num(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _clamp(result: "ValidationResult", params: dict[str, Any], key: str, default: float,
           lo: float, hi: float, label: str) -> float:
    """Read a number and, if it is outside [lo, hi], record a correction."""
    value = _num(params, key, default)
    if value < lo or value > hi:
        new = min(max(value, lo), hi)
        result.add_warning(f"{label} {value:g}mm is outside {lo:g} to {hi:g}mm, using {new:g}mm")
        result.corrections[key] = {'old': value, 'new': new}
        return new
    return value


def validate_road_segment(params: dict[str, Any]) -> ValidationResult:
    """Road segment: positive sizes, and a pavement that stays thick enough
    after the crossfalls take height off at the edges."""
    result = ValidationResult()
    result.add_warning(CIVIL_NOTICE)

    _clamp(result, params, 'length_mm', 20000.0, 1000.0, CIVIL_MAX_LENGTH_MM, "Length")
    lane_w = _clamp(result, params, 'lane_width_mm', 3500.0, 2000.0, 6000.0, "Lane width")
    if not 2700.0 <= lane_w <= 4000.0:
        result.add_warning(f"Lane width {lane_w:g}mm is outside the usual 2700 to 4000mm range")

    lanes = _num(params, 'lane_count', 2)
    if lanes != int(lanes) or lanes < 1 or lanes > 8:
        new = int(min(max(round(lanes), 1), 8))
        result.add_warning(f"Lane count {lanes:g} is not a whole number from 1 to 8, using {new}")
        result.corrections['lane_count'] = {'old': params.get('lane_count'), 'new': new}
        lanes = new
    lanes = int(lanes)

    shoulder = _clamp(result, params, 'shoulder_width_mm', 1500.0, 0.0, 5000.0, "Shoulder width")
    thick = _clamp(result, params, 'pavement_thickness_mm', 300.0, 100.0, 2000.0, "Pavement thickness")
    cross = _clamp(result, params, 'crossfall_percent', 2.5, 0.0, 8.0, "Crossfall percent")
    sh_cross = _clamp(result, params, 'shoulder_crossfall_percent', 4.0, 0.0, 10.0, "Shoulder crossfall percent")

    # Height left at the shoulder edge after both falls.
    drop = (lanes * lane_w / 2.0) * cross / 100.0 + shoulder * sh_cross / 100.0
    if thick - drop < 50.0:
        # Keep the requested falls and thicken the pavement just enough.
        new_thick = round(drop + 50.0, 1)
        result.add_warning(
            f"Pavement thickness {thick:g}mm leaves under 50mm at the edge after the crossfall, "
            f"increasing it to {new_thick:g}mm"
        )
        result.corrections['pavement_thickness_mm'] = {'old': thick, 'new': new_thick}

    if params.get('has_kerbs'):
        _clamp(result, params, 'kerb_width_mm', 150.0, 50.0, 500.0, "Kerb width")
        _clamp(result, params, 'kerb_height_mm', 150.0, 25.0, 400.0, "Kerb height")
    return result


def validate_box_culvert(params: dict[str, Any]) -> ValidationResult:
    """Box culvert: whole number of cells, walls and slabs thick enough to
    be solid, openings that fit inside the outer size."""
    result = ValidationResult()
    result.add_warning(CIVIL_NOTICE)

    _clamp(result, params, 'length_mm', 6000.0, 500.0, CIVIL_MAX_LENGTH_MM, "Length")
    cw = _clamp(result, params, 'clear_width_mm', 1500.0, 300.0, 6000.0, "Clear width")
    ch = _clamp(result, params, 'clear_height_mm', 1200.0, 300.0, 6000.0, "Clear height")

    n = _num(params, 'barrel_count', 1)
    if n != int(n) or n < 1 or n > 4:
        new = int(min(max(round(n), 1), 4))
        result.add_warning(f"Barrel count {n:g} is not a whole number from 1 to 4, using {new}")
        result.corrections['barrel_count'] = {'old': params.get('barrel_count'), 'new': new}

    wall = _clamp(result, params, 'wall_thickness_mm', 200.0, 100.0, 1000.0, "Wall thickness")
    slab = _clamp(result, params, 'slab_thickness_mm', 250.0, 100.0, 1000.0, "Slab thickness")
    if wall > cw:
        result.add_warning(f"Wall thickness {wall:g}mm is greater than the clear width {cw:g}mm, check the sizes")
    if slab > ch:
        result.add_warning(f"Slab thickness {slab:g}mm is greater than the clear height {ch:g}mm, check the sizes")
    return result


def validate_retaining_wall(params: dict[str, Any]) -> ValidationResult:
    """Retaining wall: the stem must sit on the base slab with a heel behind it,
    and must not get thicker toward the top."""
    result = ValidationResult()
    result.add_warning(CIVIL_NOTICE)

    _clamp(result, params, 'length_mm', 10000.0, 1000.0, CIVIL_MAX_LENGTH_MM, "Length")
    height = _clamp(result, params, 'stem_height_mm', 3000.0, 500.0, 12000.0, "Stem height")
    base_t = _clamp(result, params, 'base_thickness_mm', 400.0, 150.0, 2000.0, "Base thickness")
    toe = _clamp(result, params, 'toe_length_mm', 600.0, 0.0, 5000.0, "Toe length")
    stem_base = _clamp(result, params, 'stem_thickness_base_mm', 400.0, 150.0, 2000.0, "Stem thickness at base")
    stem_top = _num(params, 'stem_thickness_top_mm', 250.0)

    if stem_top > stem_base:
        result.add_warning(f"Stem is {stem_top:g}mm at the top and {stem_base:g}mm at the base, setting the top to {stem_base:g}mm")
        result.corrections['stem_thickness_top_mm'] = {'old': stem_top, 'new': stem_base}
        stem_top = stem_base
    elif stem_top < 150.0:
        result.add_warning(f"Stem thickness at top {stem_top:g}mm is below 150mm, using 150mm")
        result.corrections['stem_thickness_top_mm'] = {'old': stem_top, 'new': 150.0}

    base_w = _num(params, 'base_width_mm', 2200.0)
    min_base = toe + stem_base + 300.0   # at least 300mm of heel behind the stem
    if base_w < min_base:
        result.add_warning(f"Base width {base_w:g}mm leaves no heel behind the stem, increasing it to {min_base:g}mm")
        result.corrections['base_width_mm'] = {'old': base_w, 'new': min_base}
        base_w = min_base
    elif base_w > 20000.0:
        result.add_warning(f"Base width {base_w:g}mm is above 20000mm, using 20000mm")
        result.corrections['base_width_mm'] = {'old': base_w, 'new': 20000.0}
    if base_w < 0.4 * (height + base_t):
        result.add_warning("Base width is under 0.4 times the total height, which is narrow for a cantilever wall")
    return result


# ---------------------------------------------------------------------------
# Validators for the remaining part types (spacer ... channel_bracket).
#
# Every limit below comes from what the matching template in cad_templates/ needs
# in order to build a valid solid, not from a rule of thumb. Three outcomes:
#   error       the request cannot be built as stated (non-numeric, zero or negative
#               size, unknown type). The build is refused and nobody is charged.
#   correction  a value is clamped to the nearest buildable one, with a warning that
#               says what changed and why. The corrected value is what gets built.
#   warning     buildable but worth a second look; nothing is changed.
# ---------------------------------------------------------------------------

import math as _math

_MIN_WALL_MM = 0.5          # thinnest wall we will accept on a machined or printed part
_WARN_LARGE_MM = 5000.0     # beyond this a unit mix-up (metres typed as mm) is likely


def _read_num(params, key, default, result, *, integer=False, allow_zero=False):
    """Read a numeric parameter. Missing/None gives `default`. A bool, string, NaN or
    infinity is an error (recorded on `result`) and returns None."""
    value = params.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _math.isfinite(value):
        result.add_error(f"{key} must be a number, got {value!r}")
        return None
    if integer:
        if value != int(value):
            result.add_error(f"{key} must be a whole number, got {value}")
            return None
        value = int(value)
    if value < 0 or (value == 0 and not allow_zero):
        result.add_error(f"{key} must be {'zero or more' if allow_zero else 'greater than zero'}, got {value}")
        return None
    if not integer and value > _WARN_LARGE_MM and key.endswith("_mm"):
        result.add_warning(f"{key} of {value} mm is very large. Check the units.")
    return value


def _fix(result, key, old, new, why):
    new = round(new, 2) if isinstance(new, float) else new
    if old == new:
        return new
    result.add_warning(f"{why} Changed {key} from {old} to {new}.")
    result.add_correction(key, old, new)
    return new


def _hole_row_capacity(span, hole_diameter, spacing_factor=1.5):
    """How many holes of `hole_diameter` fit in a row of centres spanning `span` mm with
    at least spacing_factor x diameter between neighbouring centres."""
    if hole_diameter <= 0:
        return 1
    if span <= 0:
        return 1
    return max(1, int(span // (hole_diameter * spacing_factor)) + 1)


def _got(*values):
    """True when every value was read successfully (none is None)."""
    return all(v is not None for v in values)


def validate_spacer(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    outer = _read_num(params, 'outer_diameter_mm', 10.0, result)
    inner = _read_num(params, 'inner_diameter_mm', 5.0, result)
    length = _read_num(params, 'length_mm', 10.0, result)
    if _got(outer, inner) and inner > outer - 2 * _MIN_WALL_MM:
        _fix(result, 'inner_diameter_mm', inner, max(outer - 2 * _MIN_WALL_MM, outer * 0.5) if outer > 2 * _MIN_WALL_MM else outer * 0.5,
             f"Bore {inner} mm leaves less than {_MIN_WALL_MM} mm of wall in a {outer} mm spacer.")
    return result


def validate_washer(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    outer = _read_num(params, 'outer_diameter_mm', 10.0, result)
    inner = _read_num(params, 'inner_diameter_mm', 5.0, result)
    thickness = _read_num(params, 'thickness_mm', 1.5, result)
    if _got(outer, inner) and inner > outer - 2 * _MIN_WALL_MM:
        _fix(result, 'inner_diameter_mm', inner, max(outer - 2 * _MIN_WALL_MM, outer * 0.5) if outer > 2 * _MIN_WALL_MM else outer * 0.5,
             f"Hole {inner} mm leaves less than {_MIN_WALL_MM} mm of rim on a {outer} mm washer.")
    if _got(outer, thickness) and thickness > outer / 2:
        result.add_warning(f"A {thickness} mm thick washer is thicker than half its {outer} mm diameter. That is a spacer, not a washer.")
    return result


def validate_sphere(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    _read_num(params, 'diameter_mm', 20.0, result)
    return result


def validate_pulley(params: dict[str, Any]) -> ValidationResult:
    """The template cuts the groove between two 2 mm flanges, down to (outer/2 - groove_depth)."""
    result = ValidationResult()
    outer = _read_num(params, 'outer_diameter_mm', 40.0, result)
    belt = _read_num(params, 'belt_width_mm', 10.0, result)
    bore = _read_num(params, 'bore_diameter_mm', 5.0, result, allow_zero=True)
    thickness = _read_num(params, 'thickness_mm', 15.0, result)
    depth = _read_num(params, 'groove_depth_mm', 3.0, result, allow_zero=True)
    flange = 2.0

    if _got(thickness, belt) and belt > thickness - 2 * flange:
        if thickness > 2 * flange + 1:
            belt = _fix(result, 'belt_width_mm', belt, thickness - 2 * flange,
                        f"The belt groove must sit between two {flange:g} mm flanges on a {thickness} mm wide pulley.")
        else:
            result.add_error(f"A pulley {thickness} mm wide has no room for a groove between its {flange:g} mm flanges. Make it at least {2 * flange + 1:g} mm wide.")
    if _got(outer, bore, depth):
        hub_radius = bore / 2 + 1.0
        if outer / 2 - depth < hub_radius:
            depth = _fix(result, 'groove_depth_mm', depth, max(0.0, outer / 2 - hub_radius),
                         f"A {depth} mm deep groove would cut into the hub around the {bore} mm bore.")
        if bore >= outer - 2:
            result.add_error(f"Bore {bore} mm does not fit inside a {outer} mm pulley.")
    return result


def validate_sprocket(params: dict[str, Any]) -> ValidationResult:
    """Pitch radius = pitch / (2 sin(pi/teeth)); the template cuts a pitch/3 circle at each pitch point."""
    result = ValidationResult()
    teeth = _read_num(params, 'teeth', 18, result, integer=True)
    pitch = _read_num(params, 'chain_pitch_mm', 12.7, result)
    thickness = _read_num(params, 'thickness_mm', 8.0, result)
    bore = _read_num(params, 'bore_diameter_mm', 8.0, result, allow_zero=True)
    if teeth is not None:
        if teeth < 6:
            result.add_error(f"A sprocket needs at least 6 teeth to hold a chain, got {teeth}")
        elif teeth < 9:
            result.add_warning(f"{teeth} teeth gives very uneven chain motion. 11 or more is normal.")
    if _got(pitch, bore) and teeth is not None and teeth >= 6:
        pitch_radius = pitch / (2 * _math.sin(_math.pi / teeth))
        max_bore = 2 * (pitch_radius - pitch / 3 - 1.0)
        if max_bore <= 0:
            result.add_error(f"{teeth} teeth at {pitch} mm pitch leave no room for a bore.")
        elif bore > max_bore:
            _fix(result, 'bore_diameter_mm', bore, max_bore,
                 f"Bore {bore} mm would break into the tooth spaces of a {teeth}-tooth sprocket.")
    return result


def validate_structural_beam(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    beam_type = params.get('beam_type', 'i_beam')
    if beam_type not in ('i_beam', 'channel'):
        result.add_error(f"beam_type must be 'i_beam' or 'channel', got {beam_type!r}")
    height = _read_num(params, 'height_mm', 100.0, result)
    width = _read_num(params, 'width_mm', 50.0, result)
    _read_num(params, 'length_mm', 200.0, result)
    thickness = _read_num(params, 'thickness_mm', 5.0, result)
    if _got(height, width, thickness):
        limit = min(height / 2.5, width / 2.5) if beam_type == 'i_beam' else min(height / 2.5, width / 2.5)
        if thickness > limit:
            _fix(result, 'thickness_mm', thickness, limit,
                 f"A {thickness} mm plate thickness leaves no web or opening in a {height} x {width} mm section.")
    return result


def validate_angle(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    leg1 = _read_num(params, 'leg1_mm', 50.0, result)
    leg2 = _read_num(params, 'leg2_mm', 50.0, result)
    thickness = _read_num(params, 'thickness_mm', 5.0, result)
    _read_num(params, 'length_mm', 100.0, result)
    if _got(leg1, leg2, thickness) and thickness > min(leg1, leg2) / 2:
        _fix(result, 'thickness_mm', thickness, min(leg1, leg2) / 2,
             f"Thickness {thickness} mm is more than half of the shorter leg ({min(leg1, leg2)} mm).")
    return result


def validate_tube(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    shape = params.get('shape', 'round')
    if shape not in ('round', 'square', 'rectangular'):
        result.add_error(f"shape must be 'round', 'square' or 'rectangular', got {shape!r}")
    outer = _read_num(params, 'outer_diameter_mm', 20.0, result)
    wall = _read_num(params, 'wall_thickness_mm', 2.0, result)
    _read_num(params, 'length_mm', 100.0, result)
    smallest = outer
    if shape == 'rectangular' and outer is not None:
        width = _read_num(params, 'width_mm', outer, result)
        height = _read_num(params, 'height_mm', outer, result)
        smallest = min(width, height) if _got(width, height) else None
    if _got(smallest, wall) and wall > smallest / 2 - _MIN_WALL_MM:
        if smallest / 2 - _MIN_WALL_MM <= 0:
            result.add_error(f"A {smallest} mm tube is too small to be hollow.")
        else:
            _fix(result, 'wall_thickness_mm', wall, smallest / 2 - _MIN_WALL_MM,
                 f"A {wall} mm wall leaves no hollow in a {smallest} mm tube.")
    return result


def validate_pipe_fitting(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    fitting = params.get('fitting_type', 'pipe')
    if fitting not in ('pipe', 'elbow', 'tee'):
        result.add_warning(f"Unknown fitting_type {fitting!r}. Building a straight pipe instead.")
    outer = _read_num(params, 'outer_diameter_mm', 20.0, result)
    wall = _read_num(params, 'wall_thickness_mm', 2.0, result)
    _read_num(params, 'length_mm', 50.0, result)
    if _got(outer, wall) and wall > outer / 2 - _MIN_WALL_MM:
        if outer / 2 - _MIN_WALL_MM <= 0:
            result.add_error(f"A {outer} mm pipe is too small to be hollow.")
        else:
            _fix(result, 'wall_thickness_mm', wall, outer / 2 - _MIN_WALL_MM,
                 f"A {wall} mm wall leaves no bore in a {outer} mm pipe.")
    if fitting == 'elbow':
        angle = _read_num(params, 'angle_deg', 90, result)
        if angle is not None and angle > 180:
            _fix(result, 'angle_deg', angle, 180, "An elbow cannot bend more than 180 degrees.")
        if outer is not None:
            radius = _read_num(params, 'bend_radius_mm', outer * 2, result)
            if radius is not None and radius < outer * 0.75:
                _fix(result, 'bend_radius_mm', radius, outer * 1.5,
                     f"A bend radius of {radius} mm is tighter than the {outer} mm pipe can follow without folding on itself.")
    return result


def validate_flange(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    outer = _read_num(params, 'outer_diameter_mm', 100.0, result)
    inner = _read_num(params, 'inner_diameter_mm', 50.0, result, allow_zero=True)
    _read_num(params, 'thickness_mm', 10.0, result)
    count = _read_num(params, 'hole_count', 4, result, integer=True, allow_zero=True)
    hole = _read_num(params, 'hole_diameter_mm', 5.0, result, allow_zero=True)
    circle = _read_num(params, 'bolt_circle_mm', 75.0, result)

    if _got(outer, inner) and inner > outer - 2 * 3.0:
        inner = _fix(result, 'inner_diameter_mm', inner, outer * 0.5, f"Bore {inner} mm leaves almost no ring on a {outer} mm flange.")
    if _got(outer, inner, hole, circle) and count:
        lo, hi = inner + hole + 2.0, outer - hole - 2.0   # bolt circle diameter limits, 1 mm of metal each side
        if hi <= lo:
            result.add_error(f"There is no room for {hole} mm bolt holes between a {inner} mm bore and a {outer} mm rim.")
        elif not lo <= circle <= hi:
            circle = _fix(result, 'bolt_circle_mm', circle, min(max(circle, lo), hi),
                          f"A {circle} mm bolt circle puts the holes off the flange face or into the bore.")
        if count > 1 and circle > 0:
            chord = circle * _math.sin(_math.pi / count)
            if chord < hole * 1.5:
                fits = max(1, int(_math.pi / _math.asin(min(1.0, hole * 1.5 / circle))))
                _fix(result, 'hole_count', count, fits, f"{count} holes of {hole} mm do not fit on a {circle} mm bolt circle without touching.")
    return result


def validate_hinge(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    length = _read_num(params, 'length_mm', 100.0, result)
    width = _read_num(params, 'width_mm', 30.0, result)
    thickness = _read_num(params, 'thickness_mm', 2.0, result)
    knuckles = _read_num(params, 'knuckle_count', 5, result, integer=True)
    pin = _read_num(params, 'pin_diameter_mm', 3.0, result)
    if _got(length, knuckles, pin) and length / knuckles < pin:
        _fix(result, 'knuckle_count', knuckles, max(1, int(length // pin)),
             f"Knuckles shorter than the {pin} mm pin cannot be made.")
    if _got(width, pin) and pin > width / 2:
        result.add_warning(f"A {pin} mm pin is large for a {width} mm leaf.")
    if _got(thickness, pin) and pin > thickness * 4:
        result.add_warning(f"A {pin} mm pin on {thickness} mm leaves is a very weak joint.")
    return result


def validate_cam(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    base = _read_num(params, 'base_radius_mm', 20.0, result)
    lift = _read_num(params, 'lift_mm', 10.0, result, allow_zero=True)
    _read_num(params, 'thickness_mm', 5.0, result)
    bore = _read_num(params, 'bore_diameter_mm', 5.0, result, allow_zero=True)
    profile = params.get('lift_profile', 'simple')
    if profile not in ('simple', 'harmonic', 'cycloidal'):
        result.add_warning(f"Unknown lift_profile {profile!r}. Using 'simple'.")
    if _got(base, bore) and bore > base * 1.2:
        _fix(result, 'bore_diameter_mm', bore, base * 1.2, f"Bore {bore} mm leaves too little hub on a cam with a {base} mm base radius.")
    if _got(base, lift) and lift > base:
        result.add_warning(f"A lift of {lift} mm is larger than the {base} mm base radius. The follower will see very steep slopes.")
    return result


def validate_hex_standoff(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    flats = _read_num(params, 'across_flats_mm', 6.0, result)
    inner = _read_num(params, 'inner_diameter_mm', 3.2, result, allow_zero=True)
    _read_num(params, 'length_mm', 10.0, result)
    if _got(flats, inner) and inner > flats - 2 * _MIN_WALL_MM:
        _fix(result, 'inner_diameter_mm', inner, max(0.0, flats - 2 * _MIN_WALL_MM),
             f"A {inner} mm bore breaks through the flats of a {flats} mm hex.")
    return result


def _bracket_holes(result, params, length, count_key, dia_key, count_default, dia_default, limit_dia, limit_why):
    """Holes in a row, spaced by the template as (length - 20) / (n - 1)."""
    count = _read_num(params, count_key, count_default, result, integer=True, allow_zero=True)
    dia = _read_num(params, dia_key, dia_default, result, allow_zero=True)
    if not _got(length, count, dia) or count == 0 or dia == 0:
        return
    if dia > limit_dia:
        dia = _fix(result, dia_key, dia, max(limit_dia, 0.5), limit_why)
    if count > 1:
        capacity = _hole_row_capacity(length - 20, dia)
        if length <= 20 or count > capacity:
            _fix(result, count_key, count, 1 if length <= 20 else capacity,
                 f"{count} holes of {dia} mm do not fit along a {length} mm length with 10 mm kept clear at each end.")


def validate_t_bracket(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    length = _read_num(params, 'length_mm', 60.0, result)
    cap = _read_num(params, 'cap_width_mm', 40.0, result)
    _read_num(params, 'stem_height_mm', 30.0, result)
    thickness = _read_num(params, 'thickness_mm', 4.0, result)
    fillet = _read_num(params, 'fillet_radius_mm', 1.5, result, allow_zero=True)
    if _got(cap, thickness) and thickness > cap / 3:
        thickness = _fix(result, 'thickness_mm', thickness, cap / 3, f"Thickness {thickness} mm is too heavy for a {cap} mm cap.")
    if _got(thickness, fillet) and fillet >= thickness / 2:
        _fix(result, 'fillet_radius_mm', fillet, max(0.0, thickness / 2 - 0.1), f"Fillet {fillet} mm is too large for {thickness} mm material.")
    if cap is not None and thickness is not None:
        # The holes go in the flat each side of the stem: (cap - thickness) / 2 wide, with 2 mm of metal each side.
        flange = (cap - thickness) / 2
        _bracket_holes(result, params, length, 'hole_count', 'hole_diameter_mm', 2, 3.2, flange - 4.0,
                       f"The holes go in the {flange:g} mm flat on each side of the stem and need 2 mm of metal each side.")
    return result


def validate_channel_bracket(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    length = _read_num(params, 'length_mm', 60.0, result)
    width = _read_num(params, 'width_mm', 20.0, result)
    height = _read_num(params, 'height_mm', 15.0, result)
    wall = _read_num(params, 'wall_thickness_mm', 2.0, result)
    if _got(length, width, height, wall):
        smallest = min(length, width, height)
        if wall >= smallest / 2:
            wall = _fix(result, 'wall_thickness_mm', wall, smallest / 2 - 0.5, f"A {wall} mm wall cannot be shelled out of a part only {smallest} mm in its smallest direction.")
    if _got(height, wall):
        _bracket_holes(result, params, length, 'mount_hole_count', 'mount_hole_diameter_mm', 2, 4.0, height - 2 * wall,
                       f"The holes go through the base of a {height} mm high channel with {wall} mm walls.")
    return result


def _fastener_size(params, result):
    import standards
    key = standards.normalize_size(str(params.get('size', 'M8'))) if isinstance(params.get('size', 'M8'), str) else None
    if key is None:
        result.add_error(f"size must be a metric thread from {', '.join(standards.thread_sizes())}, got {params.get('size')!r}")
    return key


def _fastener_length(params, result, size):
    length = _read_num(params, 'length_mm', 30.0, result)
    if length is not None and size is not None:
        d = float(size[1:])
        if length > 20 * d:
            result.add_warning(f"A {length} mm long {size} is {length / d:.0f} times its diameter. That is unusual.")
        if length < d:
            result.add_warning(f"A {length} mm long {size} is shorter than its own diameter and cannot hold a thread.")
    return length


def validate_hex_bolt(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    _fastener_length(params, result, _fastener_size(params, result))
    return result


def validate_socket_cap_screw(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    _fastener_length(params, result, _fastener_size(params, result))
    return result


def validate_hex_nut(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    _fastener_size(params, result)
    return result


def validate_fastener(params: dict[str, Any]) -> ValidationResult:
    """Standard + size must exist in the ISO tables; screw length snaps to the standard series."""
    from standards_data import (
        LENGTH_STANDARDS, lookup_fastener, nearest_standard_length,
    )
    result = ValidationResult()
    try:
        spec = lookup_fastener(params.get('standard', 'ISO_4762'), params.get('size', 'M8'))
    except ValueError as exc:
        result.add_error(str(exc))
        return result
    if 'standard' in params and params['standard'] != spec['standard']:
        result.add_correction('standard', params['standard'], spec['standard'])
    if 'size' in params and params['size'] != spec['size']:
        result.add_correction('size', params['size'], spec['size'])
    if spec['standard'] in LENGTH_STANDARDS and 'length_mm' in params:
        length = params['length_mm']
        nearest = nearest_standard_length(length)
        if length <= 0:
            result.add_error("length_mm must be positive for a screw.")
        elif length != nearest:
            result.add_warning(f"{length} mm is not a standard length; using {nearest} mm.")
            result.add_correction('length_mm', length, float(nearest))
    return result


def validate_std_bearing(params: dict[str, Any]) -> ValidationResult:
    from standards_data import lookup_bearing
    result = ValidationResult()
    try:
        lookup_bearing(params.get('designation', '6205'))
    except ValueError as exc:
        result.add_error(str(exc))
    return result



def validate_key(params: dict[str, Any]) -> ValidationResult:
    """Shaft diameter must be in the DIN 6885 range; length snaps to a standard length allowed for that key width."""
    from standards_data import lookup_key, standard_key_length
    result = ValidationResult()
    try:
        spec = lookup_key(params.get('shaft_diameter_mm', 25.0))
    except ValueError as exc:
        result.add_error(str(exc))
        return result
    form = str(params.get('form', 'A')).strip().upper()
    if form not in ('A', 'B'):
        result.add_error("key form must be 'A' (round ends) or 'B' (square ends).")
    elif 'form' in params and params['form'] != form:
        result.add_correction('form', params['form'], form)
    if 'length_mm' in params:
        length = params['length_mm']
        if length <= 0:
            result.add_error("length_mm must be positive for a key.")
        else:
            snapped = float(standard_key_length(length, spec['length_min'], spec['length_max']))
            if length != snapped:
                result.add_warning(
                    f"Key length {length} mm is not a standard DIN 6885 length for a {spec['b']:g}x{spec['h']:g} key "
                    f"(allowed {spec['length_min']}-{spec['length_max']} mm); using {snapped:g} mm.")
                result.add_correction('length_mm', length, snapped)
    return result


def validate_feature_tree_params(params: dict[str, Any]) -> ValidationResult:
    from feature_tree import validate_feature_tree
    result = ValidationResult()
    _, errors = validate_feature_tree(params.get('features'))
    for message in errors:
        result.add_error(message)
    return result


def validate_asme_flange(params: dict[str, Any]) -> ValidationResult:
    from standards_data import lookup_flange
    result = ValidationResult()
    try:
        spec = lookup_flange(params.get('standard', 'ASME_B16_5_150'), params.get('nps', '2'),
                             params.get('flange_type', 'slip_on'))
    except ValueError as exc:
        result.add_error(str(exc))
        return result
    for key, canon in (('standard', spec['standard']), ('nps', spec['nps']), ('flange_type', spec['flange_type'])):
        if key in params and params[key] != canon:
            result.add_correction(key, params[key], canon)
    return result


def validate_o_ring(params: dict[str, Any]) -> ValidationResult:
    """Standard cross-section series; flag installations below the usual minimum bore ratio."""
    from standards_data import O_RING_CROSS_SECTIONS, is_standard_o_ring_cross_section
    result = ValidationResult()
    cs = params.get('cross_section_mm', 3.53)
    inner = params.get('inner_diameter_mm', 20.0)
    if inner <= 0:
        result.add_error("inner_diameter_mm must be positive.")
    if not is_standard_o_ring_cross_section(cs):
        result.add_error(f"cross_section_mm {cs} is not a standard O-ring cross-section. "
                         f"Valid: {', '.join(f'{c:g}' for c in O_RING_CROSS_SECTIONS)} mm.")
    elif inner > 0 and inner < 2 * cs:
        result.add_warning(f"Inside diameter {inner} mm is under 2x the cross-section ({cs} mm); "
                           "check the supplier's minimum size for this cord.")
    return result


def validate_building_params(params: dict[str, Any]) -> ValidationResult:
    """A building is valid when its room program lays out. The footprint is filled in when missing."""
    from building.flat import resolve_flat
    result = ValidationResult()
    r = resolve_flat(params)
    for message in r["errors"]:
        result.add_error(message)
    for message in r["warnings"]:
        result.add_warning(message)
    for name, (old, new) in r["corrections"].items():
        result.add_correction(name, old, new)
    return result


# ---------------------------------------------------------------------------
# PCB board (pcb_board): the pcb package decides whether the circuit can be built
# ---------------------------------------------------------------------------

def validate_pcb_board(params: dict[str, Any]) -> ValidationResult:
    """A board is valid when the pcb pack can plan it, pass its electrical checks (voltage ranges,
    shorts, unpowered nets) and place it. The reason it cannot is returned in plain words."""
    result = ValidationResult()
    from pcb.model3d import design_from_params, design_warnings

    try:
        design, notes = design_from_params(params)
    except ValueError as exc:
        result.add_error(str(exc))
        return result
    for w in design_warnings(design, notes):
        result.add_warning(w)
    width = design.board[2] - design.board[0]
    height = design.board[3] - design.board[1]
    if max(width, height) > 200.0:
        result.add_warning(f"The board is {max(width, height):g} mm long, which is large for a low-cost two-layer fab")
    return result


def _aero_notice(result):
    from aerospace_pack import GEOMETRY_ONLY_NOTICE
    result.add_warning(GEOMETRY_ONLY_NOTICE)


def validate_airfoil_wing(params: dict[str, Any]) -> ValidationResult:
    """Validate wing parameters (geometry plausibility only)."""
    result = ValidationResult()
    naca = str(params.get('naca', '2412'))
    if len(naca) != 4 or not naca.isdigit():
        result.add_error("naca must be a 4-digit code such as '2412'")
    elif int(naca[2:]) < 6:
        result.add_error("NACA thickness below 6% is too thin to model as a solid")
    for name in ('root_chord_mm', 'tip_chord_mm', 'span_mm'):
        if params.get(name, 1.0) <= 0:
            result.add_error(f"{name} must be positive")
    cr, ct = params.get('root_chord_mm', 200.0), params.get('tip_chord_mm', 120.0)
    if cr > 0 and ct > cr * 1.5:
        result.add_warning("tip_chord_mm is much larger than root_chord_mm - unusual planform")
    if not -60 <= params.get('sweep_deg', 0.0) <= 60:
        result.add_error("sweep_deg must be between -60 and 60")
    _aero_notice(result)
    return result


def validate_wing_rib(params: dict[str, Any]) -> ValidationResult:
    """Validate wing rib parameters."""
    result = ValidationResult()
    naca = str(params.get('naca', '2412'))
    if len(naca) != 4 or not naca.isdigit():
        result.add_error("naca must be a 4-digit code such as '2412'")
    if params.get('chord_mm', 200.0) <= 0:
        result.add_error("chord_mm must be positive")
    if params.get('thickness_mm', 2.0) <= 0:
        result.add_error("thickness_mm must be positive")
    ratio = params.get('lightening_hole_ratio', 0.5)
    if ratio > 0.6:
        result.add_warning("lightening_hole_ratio capped at 0.6 to keep rib caps intact")
        result.add_correction('lightening_hole_ratio', ratio, 0.6)
    _aero_notice(result)
    return result


def validate_fuselage_frame(params: dict[str, Any]) -> ValidationResult:
    """Validate fuselage frame (ring bulkhead) parameters."""
    result = ValidationResult()
    od = params.get('outer_diameter_mm', 300.0)
    width = params.get('ring_width_mm', 40.0)
    if od <= 0 or width <= 0:
        result.add_error("outer_diameter_mm and ring_width_mm must be positive")
    elif width >= od / 2:
        result.add_error("ring_width_mm must be smaller than the outer radius")
    hd = params.get('lightening_hole_diameter_mm', 0.0)
    if width > 0 and hd > width * 0.7:
        result.add_warning("Lightening holes this large leave very thin web edges")
    _aero_notice(result)
    return result


def validate_wing_spar(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    h, tf = params.get('height_mm', 40.0), params.get('flange_thickness_mm', 3.0)
    if params.get('length_mm', 600.0) <= 0 or h <= 0:
        result.add_error("length_mm and height_mm must be positive")
    if 2 * tf >= h:
        result.add_error("flange_thickness_mm x 2 must be smaller than height_mm")
    if params.get('web_thickness_mm', 2.0) >= params.get('flange_width_mm', 30.0):
        result.add_error("web_thickness_mm must be smaller than flange_width_mm")
    _aero_notice(result)
    return result


def validate_attachment_lug(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    w, d = params.get('width_mm', 30.0), params.get('hole_diameter_mm', 10.0)
    if w <= 0 or d <= 0 or params.get('thickness_mm', 6.0) <= 0:
        result.add_error("width_mm, hole_diameter_mm and thickness_mm must be positive")
    elif d >= w:
        result.add_error("hole_diameter_mm must be smaller than width_mm")
    elif w / d < 1.5:
        result.add_warning("w/D below 1.5: very little material around the hole")
    if params.get('length_mm', 50.0) < w:
        result.add_error("length_mm must be at least width_mm")
    _aero_notice(result)
    return result


def validate_fuselage(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    L, D = params.get('length_mm', 30000.0), params.get('diameter_mm', 3500.0)
    if L <= 0 or D <= 0:
        result.add_error("length_mm and diameter_mm must be positive")
    elif not 3 <= L / D <= 25:
        result.add_warning("fineness ratio (length / diameter) outside 3..25 is unusual")
    if params.get('nose_ratio', 0.12) + params.get('tail_ratio', 0.25) >= 1:
        result.add_error("nose_ratio + tail_ratio must be below 1")
    if params.get('tail_end_diameter_mm', 500.0) >= D > 0:
        result.add_error("tail_end_diameter_mm must be smaller than diameter_mm")
    _aero_notice(result)
    return result


def validate_tail_surface(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if str(params.get('orientation', 'horizontal')).lower() not in ('horizontal', 'vertical'):
        result.add_error("orientation must be 'horizontal' or 'vertical'")
    if min(params.get('root_chord_mm', 4000.0), params.get('tip_chord_mm', 2000.0), params.get('span_mm', 6000.0)) <= 0:
        result.add_error("chords and span must be positive")
    _aero_notice(result)
    return result


def validate_turbofan_engine(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    fd = params.get('fan_diameter_mm', 2000.0)
    if fd <= 0:
        result.add_error("fan_diameter_mm must be positive")
    if not 0 < params.get('hub_ratio', 0.3) < params.get('core_ratio', 0.45) < 1:
        result.add_error("require 0 < hub_ratio < core_ratio < 1")
    if params.get('compressor_stages', 0) + params.get('turbine_stages', 0) > 40 or params.get('blades_per_row', 30) > 80:
        result.add_error("compressor_stages + turbine_stages must be <= 40 and blades_per_row <= 80")
    if params.get('fan_blades', 18) > 60:
        result.add_error("fan_blades above 60 is not supported")
    result.add_warning("Engine geometry is an external envelope with simplified fan blades; it has no real internals.")
    _aero_notice(result)
    return result


def validate_jet_aircraft(params: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    if params.get('engines', 2) not in (0, 2, 4):
        result.add_error("engines must be 0, 2 or 4")
    if min(params.get('fuselage_length_mm', 37000.0), params.get('fuselage_diameter_mm', 3950.0),
           params.get('wing_span_mm', 34000.0)) <= 0:
        result.add_error("fuselage and wing dimensions must be positive")
    if not 0.2 <= params.get('wing_x_ratio', 0.38) <= 0.7 or not 0.6 <= params.get('tail_x_ratio', 0.84) <= 0.95:
        result.add_warning("wing_x_ratio or tail_x_ratio is outside the usual layout range")
    _aero_notice(result)
    return result


def validate_parameters(part_type, params, description=None):
    """
    Validate parameters for any part type and return (parameters, result).

    Values the user typed are kept and flagged, never changed. Values that came from
    defaults are still corrected when they would break the geometry. `description`
    is the user's text, used to tell the two apart; without it every correction applies.
    """
    validators = {
        'motor_mount': validate_motor_mount,
        'l_bracket': validate_l_bracket,
        'flat_plate': validate_flat_plate,
        'shaft': validate_shaft,
        'gear': validate_gear,
        'bearing': validate_bearing,
        'simple_box': validate_simple_box,
        'connecting_rod': validate_connecting_rod,
        'crankshaft': validate_crankshaft,
        'road_segment': validate_road_segment,
        'box_culvert': validate_box_culvert,
        'retaining_wall': validate_retaining_wall,
        'spacer': validate_spacer,
        'washer': validate_washer,
        'sphere': validate_sphere,
        'pulley': validate_pulley,
        'sprocket': validate_sprocket,
        'structural_beam': validate_structural_beam,
        'angle': validate_angle,
        'tube': validate_tube,
        'pipe_fitting': validate_pipe_fitting,
        'flange': validate_flange,
        'hinge': validate_hinge,
        'cam': validate_cam,
        'hex_standoff': validate_hex_standoff,
        't_bracket': validate_t_bracket,
        'channel_bracket': validate_channel_bracket,
        'hex_bolt': validate_hex_bolt,
        'socket_cap_screw': validate_socket_cap_screw,
        'hex_nut': validate_hex_nut,
        'fastener': validate_fastener,
        'std_bearing': validate_std_bearing,
        'key': validate_key,
        'asme_flange': validate_asme_flange,
        'o_ring': validate_o_ring,
        'building': validate_building_params,
        'feature_tree': validate_feature_tree_params,
        'pcb_board': validate_pcb_board,
        'airfoil_wing': validate_airfoil_wing,
        'wing_rib': validate_wing_rib,
        'fuselage_frame': validate_fuselage_frame,
        'wing_spar': validate_wing_spar,
        'attachment_lug': validate_attachment_lug,
        'fuselage': validate_fuselage,
        'tail_surface': validate_tail_surface,
        'turbofan_engine': validate_turbofan_engine,
        'jet_aircraft': validate_jet_aircraft,
    }

    # Domain-pack validators (domain_packs/); imported lazily so this module
    # stays import-light and free of cycles.
    from domain_packs import pack_validators
    validators.update(pack_validators())
    
    validator = validators.get(part_type)
    if validator:
        result = validator(params)
        
        explicit = typed_keys(description, params)
        corrected_params = params.copy()
        for param, correction in list(result.corrections.items()):
            if param in explicit:
                # The user typed this size: keep it, say what would be safer.
                result.flags[param] = {'value': correction['old'], 'suggested': correction['new']}
                del result.corrections[param]
                old_nums = [correction['old']] if isinstance(correction['old'], (int, float)) else []
                hit = False
                for i, w in enumerate(result.warnings):
                    if old_nums and any(_close(float(old_nums[0]), n) for n in _numbers_in(w)) and _ACTION.search(w):
                        result.warnings[i] = _as_flag(w, correction['new'])
                        hit = True
                if not hit:
                    result.warnings.append(f"{param} is {correction['old']}, as you typed. A safer value is {correction['new']}.")
            else:
                corrected_params[param] = correction['new']

        return corrected_params, result
    else:
        # No validator, return as-is
        return params, ValidationResult()

"""
Smart parser with regex-based number extraction and constraint inference.
Much more capable than simple keyword matching.
"""
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from domain_packs import extract_parameters as extract_pack_parameters, is_pack_part


@dataclass
class ExtractedDimension:
    """A dimension extracted from text with context."""
    value: float
    unit: str  # 'mm', 'inch', 'unitless'
    context: str  # what it refers to
    raw_text: str

@dataclass
class ExtractedPattern:
    """A hole or feature pattern."""
    pattern_type: str  # 'rectangular', 'circular', 'linear'
    count: int
    diameter: float | None = None
    spacing: float | None = None
    layout: str | None = None

class ParsedParameters(BaseModel):
    """Structured parameters extracted from natural language."""
    part_type: str
    parameters: dict[str, Any]
    material: str | None = None
    operations: list[dict[str, Any]] = Field(default_factory=list)
    assembly_parts: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    # Parser provenance is part of the job record so a UI card can tell
    # whether the geometry came from the LLM, the deterministic fallback,
    # or a previously validated cache entry.
    parser: str = "regex_fallback"
    model: str | None = None
    prompt_version: str | None = None
    retry_count: int = 0
    parse_hash: str | None = None

# Unit conversion
UNIT_TO_MM = {
    'mm': 1.0,
    'millimeter': 1.0,
    'millimeters': 1.0,
    'cm': 10.0,
    'centimeter': 10.0,
    'm': 1000.0,
    'meter': 1000.0,
    'inch': 25.4,
    'inches': 25.4,
    'in': 25.4,
    '"': 25.4,
    "'": 304.8,
    'ft': 304.8,
    'foot': 304.8,
    'feet': 304.8,
}

# Standard part sizes (for inference)
STANDARD_SIZES = {
    'nema_17': {'face': 42.0, 'shaft': 5.0, 'holes': 31.0},
    'nema_23': {'face': 57.0, 'shaft': 6.35, 'holes': 47.0},
    'nema_34': {'face': 86.0, 'shaft': 14.0, 'holes': 71.5},
    'm3': {'diameter': 3.0, 'tap_drill': 2.5, 'clearance': 3.2},
    'm4': {'diameter': 4.0, 'tap_drill': 3.3, 'clearance': 4.3},
    'm5': {'diameter': 5.0, 'tap_drill': 4.2, 'clearance': 5.3},
    'm6': {'diameter': 6.0, 'tap_drill': 5.0, 'clearance': 6.4},
    'm8': {'diameter': 8.0, 'tap_drill': 6.8, 'clearance': 8.4},
    '1/4-20': {'diameter': 6.35, 'tap_drill': 5.1, 'clearance': 7.0},
    '3/8-16': {'diameter': 9.525, 'tap_drill': 8.0, 'clearance': 10.5},
}

def extract_all_dimensions(text: str) -> list[ExtractedDimension]:
    """Extract all dimensions with units from text using regex."""
    dimensions = []

    # Extract fractional inches
    for match in re.finditer(r'(\d+)?-?(\d+)/(\d+)\s*(["\']|inch|inches|in)', text, re.IGNORECASE):
        whole = int(match.group(1)) if match.group(1) else 0
        num = int(match.group(2))
        den = int(match.group(3))
        value = whole + num/den
        dimensions.append(ExtractedDimension(
            value=value * 25.4,
            unit='inch',
            context='',
            raw_text=match.group(0)
        ))
    
    # Extract decimal with units
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(mm|millimeters?|cm|centimeters?|m|meters?|metres?|inch|inches|in|ft|feet|foot)\b', text, re.IGNORECASE):
        value = float(match.group(1))
        unit = match.group(2).lower()
        # Normalize unit
        if unit in ['mm', 'millimeter', 'millimeters']:
            unit_mm = 'mm'
        elif unit in ['cm', 'centimeter', 'centimeters']:
            unit_mm = 'cm'
        elif unit in ['m', 'meter', 'meters', 'metre', 'metres']:
            unit_mm = 'm'
        elif unit in ['inch', 'inches', 'in']:
            unit_mm = 'inch'
        elif unit in ['ft', 'feet', 'foot']:
            unit_mm = 'ft'
        else:
            unit_mm = unit
        
        dimensions.append(ExtractedDimension(
            value=value,
            unit=unit_mm,
            context='',
            raw_text=match.group(0)
        ))
    
    # Extract bare numbers with context
    context_patterns = [
        (r'(width|wide|w)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'width'),
        (r'(height|tall|h)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'height'),
        (r'(depth|deep|d)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'depth'),
        (r'(length|long|l)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'length'),
        (r'(thick(?:ness)?)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'thickness'),
        (r'(diameter|dia|diam)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'diameter'),
        (r'(radius|r)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'radius'),
        (r'(module)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'module'),
        (r'(teeth|tooth\s*count)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'teeth'),
        (r'(bore)\s*(?:diameter\s*)?(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'bore'),
        (r'(throws|num\s*throws)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'throws'),
        (r'(cylinders?)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'cylinders'),
        (r'(stroke)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', 'stroke'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:thick|thickness)', 'thickness'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:wide|width)', 'width'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:tall|height)', 'height'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:deep|depth)', 'depth'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:long|length)', 'length'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:\w+\s+){0,2}(?:diameter|dia)', 'diameter'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?radius', 'radius'),
        (r'(\d+(?:\.\d+)?)\s*teeth', 'teeth'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?bore(?:\s*diameter)?', 'bore'),
        (r'(\d+(?:\.\d+)?)\s*throws?', 'throws'),
        (r'(\d+(?:\.\d+)?)\s*-?\s*cylinders?', 'cylinders'),
        (r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?stroke', 'stroke'),
    ]
    
    for pattern, context in context_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            # Find the number in the match
            num_match = re.search(r'(\d+(?:\.\d+)?)', match.group(0))
            if num_match:
                value = float(num_match.group(1))
                # Check if unit follows
                unit_match = re.search(r'(mm|cm|m|inch|in|ft)', match.group(0)[num_match.end():], re.IGNORECASE)
                unit = unit_match.group(1).lower() if unit_match else 'mm'
                
                dimensions.append(ExtractedDimension(
                    value=value,
                    unit=unit,
                    context=context,
                    raw_text=match.group(0)
                ))
    
    return dimensions

def extract_hole_patterns(text: str) -> list[ExtractedPattern]:
    """Extract hole patterns from text."""
    patterns = []
    text_lower = text.lower()
    
    # Pattern: "4 holes", "6 holes", etc.
    hole_count_match = re.search(r'(\d+)\s*(?:mounting\s*)?holes?', text_lower)
    hole_diameter = None
    hole_spacing = None
    
    # Extract hole diameter. Two orderings show up in practice:
    # "hole diameter of 6mm" / "holes with 6mm dia" (keyword before number)
    # and "6mm hole diameter" (number before keyword) - the original regex
    # only caught the first, so "6mm hole diameter" fell through silently.
    dia_match = re.search(r'(?:hole|holes)\s*(?:of|with|dia(?:meter)?)\s*(\d+(?:\.\d+)?)', text_lower)
    if dia_match:
        hole_diameter = float(dia_match.group(1))
    else:
        dia_match_rev = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?holes?\s*dia(?:meter)?', text_lower)
        if dia_match_rev:
            hole_diameter = float(dia_match_rev.group(1))
        else:
            # Check for M-size
            m_match = re.search(r'\b(m\d+)\b', text_lower)
            if m_match:
                m_size = m_match.group(1).upper()
                if m_size in STANDARD_SIZES:
                    hole_diameter = STANDARD_SIZES[m_size]['clearance']
    
    # Extract spacing
    spacing_match = re.search(r'(?:spacing|pitch|center)\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)', text_lower)
    if spacing_match:
        hole_spacing = float(spacing_match.group(1))
    
    # Determine pattern type
    if 'circular' in text_lower or 'bolt circle' in text_lower or 'bcd' in text_lower:
        pattern_type = 'circular'
    elif 'rectangular' in text_lower or 'grid' in text_lower or 'array' in text_lower:
        pattern_type = 'rectangular'
    elif 'linear' in text_lower or 'row' in text_lower:
        pattern_type = 'linear'
    else:
        pattern_type = 'rectangular'  # default
    
    if hole_count_match:
        count = int(hole_count_match.group(1))
        patterns.append(ExtractedPattern(
            pattern_type=pattern_type,
            count=count,
            diameter=hole_diameter,
            spacing=hole_spacing
        ))
    
    # Check for X by Y pattern (e.g., "4x3 holes", "4 by 3 pattern")
    grid_match = re.search(r'(\d+)\s*(?:x|by|×)\s*(\d+)\s*(?:holes?|pattern|grid|array)', text_lower)
    if grid_match:
        count_x = int(grid_match.group(1))
        count_y = int(grid_match.group(2))
        patterns.append(ExtractedPattern(
            pattern_type='rectangular',
            count=count_x * count_y,
            diameter=hole_diameter,
            spacing=hole_spacing,
            layout=f'{count_x}x{count_y}'
        ))
    
    return patterns

def extract_operations(text: str) -> list[dict[str, Any]]:
    """Extract CAD operations like fillets, chamfers, shells."""
    operations = []
    text_lower = text.lower()
    
    # Fillets
    fillet_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?fillet', text_lower)
    if fillet_match:
        operations.append({
            'type': 'fillet',
            'radius': float(fillet_match.group(1)),
            'edges': 'all'
        })
    
    # Chamfers
    chamfer_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?chamfer', text_lower)
    if chamfer_match:
        operations.append({
            'type': 'chamfer',
            'size': float(chamfer_match.group(1)),
            'edges': 'all'
        })
    
    # Shell
    shell_match = re.search(r'shell(?:ed)?\s*(?:with|of|is|=|:)?\s*(\d+(?:\.\d+)?)', text_lower)
    if shell_match or 'shell' in text_lower or 'hollow' in text_lower:
        thickness = float(shell_match.group(1)) if shell_match else 2.0
        operations.append({
            'type': 'shell',
            'thickness': thickness,
            'open_top': 'lid' in text_lower or 'open' in text_lower
        })
    
    # Revolve
    if 'revolve' in text_lower or 'lathe' in text_lower or 'turned' in text_lower:
        operations.append({'type': 'revolve'})
    
    # Sweep
    if 'sweep' in text_lower or 'extrude along' in text_lower:
        operations.append({'type': 'sweep'})
    
    # Loft
    if 'loft' in text_lower:
        operations.append({'type': 'loft'})
    
    return operations


# ---------------------------------------------------------------------------
# Civil elements (road_segment, box_culvert, retaining_wall)
#
# The regex fallback reads only what the text says, in metres, centimetres or
# millimetres (British and US spelling), and returns millimetres. Anything it
# does not find is left out so the template's own default applies. The AI
# parser is the main path for these; this keeps them working without it.
# ---------------------------------------------------------------------------

_CIVIL_UNIT_MM = {'mm': 1.0, 'cm': 10.0, 'm': 1000.0, 'meter': 1000.0, 'meters': 1000.0,
                  'metre': 1000.0, 'metres': 1000.0}
_CIVIL_NUM = r'(\d+(?:\.\d+)?)'
_CIVIL_UNIT = r'(mm|cm|metres?|meters?|m)\b'
_COUNT_WORDS = {'one': 1, 'single': 1, 'two': 2, 'twin': 2, 'double': 2, 'three': 3,
                'triple': 3, 'four': 4, 'six': 6}


def _detect_building_type(text_lower: str) -> bool:
    """A house or small building: a dwelling word, or bedrooms together with another room."""
    if re.search(r'\b(house|bungalow|duplex|villa|residential building|dwelling|storey|story|storeys|stories)\b', text_lower):
        return True
    return bool(re.search(r'\bbedrooms?\b', text_lower) and re.search(r'\b(bathroom|kitchen|living room|parlou?r|toilet)\b', text_lower))


def _building_params_from_text(text: str) -> dict[str, Any]:
    from building.intake import parse_description as _parse_building

    c = _parse_building(text)["counts"]
    out: dict[str, Any] = {"floors": c["levels"], "bedrooms": c["bedrooms"], "master_bedroom": int(c["master"]),
                           "bathrooms": c["bathrooms"], "dining_room": int(c["dining"]), "study": int(c["study"]),
                           "store": int(c["store"])}
    if c["toilets"]:
        out["toilets"] = c["toilets"]
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|metres|meters)?\s*(?:x|by|×)\s*(\d+(?:\.\d+)?)\s*(?:m|metres|meters)\b", text)
    if m and 5 <= min(float(m.group(1)), float(m.group(2))) and max(float(m.group(1)), float(m.group(2))) <= 40:
        out["width_mm"] = max(float(m.group(1)), float(m.group(2))) * 1000
        out["depth_mm"] = min(float(m.group(1)), float(m.group(2))) * 1000
    return out


def _detect_civil_type(text_lower: str) -> str | None:
    if re.search(r'\bculverts?\b', text_lower):
        return 'box_culvert'
    if re.search(r'\bretaining\s+walls?\b', text_lower):
        return 'retaining_wall'
    if re.search(r'\b(roads?|roadways?|carriageways?|highways?|streets?|driveways?)\b', text_lower):
        return 'road_segment'
    return None


def _mm(value: str, unit: str) -> float:
    return float(value) * _CIVIL_UNIT_MM[unit.lower()]


def _first_mm(text: str, patterns: list[str]) -> float | None:
    """First pattern with two groups (number, unit) that matches, as mm."""
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return _mm(m.group(1), m.group(2))
    return None


def _civil_params_from_text(part_type: str, text: str) -> dict[str, Any]:
    n, u = _CIVIL_NUM, _CIVIL_UNIT
    out: dict[str, Any] = {}

    def put(key: str, value: float | None) -> None:
        if value is not None:
            out[key] = value

    put('length_mm', _first_mm(text, [
        rf'{n}\s*{u}\s*(?:long|length)',
        rf'length\s*(?:of|is|=|:)?\s*{n}\s*{u}',
    ]))

    if part_type == 'road_segment':
        put('lane_width_mm', _first_mm(text, [
            rf'{n}\s*{u}\s*(?:wide\s*)?lanes?\b',
            rf'lane\s*width\s*(?:of|is|=|:)?\s*{n}\s*{u}',
        ]))
        put('shoulder_width_mm', _first_mm(text, [
            rf'{n}\s*{u}\s*(?:wide\s*)?shoulders?',
            rf'shoulders?\s*(?:width\s*)?(?:of|is|=|:)?\s*{n}\s*{u}',
        ]))
        put('pavement_thickness_mm', _first_mm(text, [
            rf'{n}\s*{u}\s*(?:thick|pavement)',
            rf'pavement\s*thickness\s*(?:of|is|=|:)?\s*{n}\s*{u}',
        ]))
        m = re.search(r'(?<![\d.])(\d+|one|two|three|four|six)[\s-]*lanes?\b', text) \
            or re.search(rf'\b(one|two|three|four|six|\d+)\s+{n}\s*{u}\s*(?:wide\s*)?lanes?\b', text)
        if m:
            tok = m.group(1)
            out['lane_count'] = int(tok) if tok.isdigit() else _COUNT_WORDS[tok]
        m = re.search(rf'{n}\s*%\s*(?:cross\s*-?\s*fall|cross\s*slope|camber|crown)', text) \
            or re.search(rf'(?:cross\s*-?\s*fall|cross\s*slope|camber)\s*(?:of|is|=|:)?\s*{n}\s*%', text)
        if m:
            out['crossfall_percent'] = float(m.group(1))
        if re.search(r'\b(kerbs?|curbs?)\b', text):
            out['has_kerbs'] = True

    elif part_type == 'box_culvert':
        m = re.search(rf'{n}\s*(?:{u})?\s*(?:x|by|\u00d7)\s*{n}\s*{u}', text)
        if m:
            unit = m.group(4)
            first_unit = m.group(2) or unit
            out['clear_width_mm'] = _mm(m.group(1), first_unit)
            out['clear_height_mm'] = _mm(m.group(3), unit)
        m = re.search(r'\b(\d+|single|twin|double|triple|two|three|four)[\s-]*(?:cells?|barrels?)\b', text)
        if m:
            tok = m.group(1)
            out['barrel_count'] = int(tok) if tok.isdigit() else _COUNT_WORDS[tok]
        elif re.search(r'\b(twin|double)\b', text):
            out['barrel_count'] = 2
        put('wall_thickness_mm', _first_mm(text, [
            rf'wall\s*thickness\s*(?:of|is|=|:)?\s*{n}\s*{u}',
            rf'{n}\s*{u}\s*(?:thick\s*)?walls?',
        ]))

    elif part_type == 'retaining_wall':
        put('stem_height_mm', _first_mm(text, [
            rf'{n}\s*{u}\s*(?:high|tall)',
            rf'height\s*(?:of|is|=|:)?\s*{n}\s*{u}',
        ]))
        put('base_width_mm', _first_mm(text, [
            rf'base\s*(?:width\s*)?(?:of|is|=|:)?\s*{n}\s*{u}',
            rf'{n}\s*{u}\s*(?:wide\s*)?base',
        ]))

    return out

def infer_part_type(text: str, dimensions: list[ExtractedDimension], operations: list[dict]) -> str:
    """Infer part type from text and extracted data."""
    text_lower = text.lower()
    
    # Check for assembly keywords
    if any(word in text_lower for word in ['assembly', 'assemble', 'multiple parts', 'together']):
        return 'assembly'
    
    # Check for freeform operations
    has_freeform = any(op['type'] in ['revolve', 'sweep', 'loft'] for op in operations)
    
    # Houses before civil: 'a house with a driveway' is a building, not a road.
    if _detect_building_type(text_lower):
        return 'building'

    # Civil elements first: 'box culvert' would otherwise hit the generic 'box'
    # check and 'drainage channel' the 'channel' check further down.
    civil_type = _detect_civil_type(text_lower)
    if civil_type:
        return civil_type

    # Domain packs (automotive, ...): specific multi-word phrases only, so
    # they win over the generic keywords below ("brake rotor" must not fall
    # through to 'rotor'/'disc' heuristics, "battery enclosure bracket" must
    # not become a simple_box) without ever shadowing core keywords on bare
    # generic words.
    from domain_packs import infer_part_type as _pack_infer
    pack_hit = _pack_infer(text)
    if pack_hit:
        return pack_hit
    

    # Aerospace pack. Word-boundary regexes on purpose: plain substring 'wing'
    # would hijack "swing arm" / "drawing", and 'rib' would match "ribbon".
    if re.search(r'\b(?:wing|airfoil|aerofoil|naca)\s*-?\s*(?:rib|section rib)\b|\brib\b.*\b(?:wing|airfoil|naca)\b', text_lower):
        return 'wing_rib'
    if re.search(r'\b(?:turbofan|turbojet|jet\s+engine|turbine\s+engine|engine\s+nacelle)\b', text_lower):
        return 'turbofan_engine'
    if re.search(r'\b(?:jet\s*(?:aircraft|airliner|plane)|airliner|business\s+jet|passenger\s+jet|fighter\s+jet|(?:twin|four)[- ]engine\s+(?:jet|aircraft))\b', text_lower):
        return 'jet_aircraft'
    if re.search(r'\b(?:horizontal|vertical)\s+(?:stabili[sz]er|tail)|\btail\s+(?:plane|fin|surface)\b', text_lower):
        return 'tail_surface'
    if re.search(r'\bfuselage\b(?!\s+(?:frame|bulkhead|ring))', text_lower):
        return 'fuselage'
    if re.search(r'\b(?:wing\s+spar|spar\s+beam|i-?beam\s+spar|\bspar\b)', text_lower):
        return 'wing_spar'
    if re.search(r'\b(?:attachment\s+lug|clevis\s+lug|lug\s+fitting|aircraft\s+lug|aerospace\s+lug)\b', text_lower):
        return 'attachment_lug'
    if re.search(r'\b(?:fuselage\s+(?:frame|bulkhead|ring)|bulkhead|aircraft\s+frame)\b', text_lower):
        return 'fuselage_frame'
    if re.search(r'\b(?:wing(?!\s*-?\s*nut|\s*-?\s*bolt)|airfoil|aerofoil|naca\s*\d{4})\b', text_lower):
        return 'airfoil_wing'

    # Part type detection with priority
    if any(word in text_lower for word in ['motor', 'stepper', 'servo']):
        return 'motor_mount'
    # These three are checked early, before the shorter/more generic
    # 'standoff' and 'channel' keywords below, which would otherwise
    # shadow them (e.g. "hex standoff" contains "standoff", "channel
    # bracket" contains "channel" - substring checks need the more
    # specific phrase tested first).
    elif any(word in text_lower for word in ['hex standoff', 'hex spacer', 'hexagonal standoff']):
        return 'hex_standoff'
    elif any(word in text_lower for word in ['t-bracket', 't bracket', 'tee bracket']):
        return 't_bracket'
    elif any(word in text_lower for word in ['channel bracket', 'cable channel', 'cable clamp', 'cable clip', 'mounting channel']):
        return 'channel_bracket'
    # Same shadowing concern as the three above: 'crankshaft' contains
    # 'shaft' and 'connecting rod'/'conrod' would otherwise match the
    # much more generic 'rod'/'pin' keywords in the plain shaft check
    # further down - both need to be tested first.
    elif any(word in text_lower for word in ['crankshaft', 'crank shaft', 'crank']):
        return 'crankshaft'
    elif any(word in text_lower for word in ['connecting rod', 'conrod', 'con-rod', 'con rod', 'piston rod']):
        return 'connecting_rod'
    elif any(word in text_lower for word in ['l-bracket', 'l bracket', 'angle bracket']):
        return 'l_bracket'
    elif any(word in text_lower for word in ['shaft', 'axle', 'rod', 'pin']):
        return 'shaft'
    elif any(word in text_lower for word in ['sprocket', 'chain sprocket']):
        return 'sprocket'
    elif any(word in text_lower for word in ['gear']):
        return 'gear'
    elif any(word in text_lower for word in ['pulley', 'belt']):
        return 'pulley'
    elif any(word in text_lower for word in ['bearing', 'bushing', 'bush']):
        return 'bearing'
    elif any(word in text_lower for word in ['pipe', 'tube', 'elbow', 'fitting']):
        return 'pipe_fitting'
    elif any(word in text_lower for word in ['flange']):
        return 'flange'
    elif any(word in text_lower for word in ['beam', 'i-beam', 'channel', 'c-channel']):
        return 'structural_beam'
    elif any(word in text_lower for word in ['hinge', 'piano hinge']):
        return 'hinge'
    elif any(word in text_lower for word in ['spacer', 'standoff']):
        return 'spacer'
    elif any(word in text_lower for word in ['washer']):
        return 'washer'
    elif any(word in text_lower for word in ['cam', 'cam profile']):
        return 'cam'
    elif any(word in text_lower for word in ['box', 'enclosure', 'case', 'housing', 'cube']) or 'solid block' in text_lower:
        return 'simple_box'
    elif any(word in text_lower for word in ['plate', 'flange', 'blank']):
        return 'flat_plate'
    elif has_freeform:
        return 'freeform'
    else:
        # Default based on dimension count
        if len(dimensions) <= 2:
            return 'flat_plate'
        elif len(dimensions) <= 4:
            return 'l_bracket'
        else:
            return 'flat_plate'

_TRIPLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|m)?\s*(?:x|by|\u00d7)\s*"
    r"(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|m)?\s*(?:x|by|\u00d7)\s*"
    r"(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|m)?\b")
_TRIPLE_UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4}


def _triple_dims_mm(text_lower: str) -> tuple[float, float, float] | None:
    """"200 x 100 x 6 mm", "4in x 2in x 0.25in", "120 by 80 by 5": three sizes in the order written, in mm.
    A unit given once, after the last number, applies to all three. No unit means millimetres."""
    m = _TRIPLE_RE.search(text_lower)
    if not m:
        return None
    nums = [float(m.group(1)), float(m.group(3)), float(m.group(5))]
    units = [m.group(2), m.group(4), m.group(6)]
    fallback = next((u for u in reversed(units) if u), "mm")
    return tuple(round(n * _TRIPLE_UNIT_MM[u or fallback], 4) for n, u in zip(nums, units))  # type: ignore[return-value]


def plate_grid_for_count(count: int, length: float, width: float) -> tuple[int, int]:
    """Holes in X and Y whose product is exactly `count`, as square as the count allows, with the larger
    number along the plate's longer side. (The parser used to give min(4, count) by count // 4, so 6 holes
    became 4 and 7 became 4.)"""
    count = max(1, int(count))
    best = (count, 1)
    for a in range(1, int(count ** 0.5) + 1):
        if count % a == 0:
            best = (count // a, a)           # the last divisor pair found is the most square
    big, small = best
    return (big, small) if length >= width else (small, big)


_HOLES_MENTIONED_RE = re.compile(r"\bholes?\b|\bdrilled\b|\bmounting\b|\bbolt\b")
_NO_HOLES_RE = re.compile(r"\b(?:no|without|zero)\s+(?:any\s+|mounting\s+|drilled\s+)?holes?\b|\b(?:solid|plain|blank)\s+(?:rectangular\s+)?plate\b")
_HOLLOW_RE = re.compile(r"\b(?:enclosure|hollow|open[- ]top|walls?|tray|container|housing|shell|case)\b")
# "walls of 4 mm" first. Then "3 mm walls", but not a number that is the last of a "100 x 80 x 50" size.
_WALL_OF_RE = re.compile(r"\bwalls?\s*(?:of|thickness|=|:)\s*(\d+(?:\.\d+)?)")
_WALL_RE = re.compile(r"(?<![\d.])(?<![x\u00d7])(?<![x\u00d7]\s)(?<!by\s)(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:thick\s*)?walls?\b")


def infer_missing_dimensions(part_type: str, dimensions: list[ExtractedDimension], patterns: list[ExtractedPattern], description: str = "", operations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Infer missing dimensions based on part type and context."""
    params = {}
    text_lower = description.lower()
    operations = operations or []
    
    # A fillet phrased naturally ("8mm fillets on all edges") is caught by
    # extract_operations' fillet regex, not by the 'radius' context pattern
    # below (which needs the literal word "radius"). Prefer that parsed
    # value for the template's own fillet_radius_mm/corner_fillet_mm so the
    # part isn't built with a silent 2.0/3.0mm default while the real
    # request sits unused in `operations` and later fails as a redundant
    # second fillet pass on already-rounded edges.
    fillet_op_radius = next((op['radius'] for op in operations if op.get('type') == 'fillet'), None)
    
    # Map extracted dimensions to parameters
    dim_map = {}
    for dim in dimensions:
        # Convert to mm
        value_mm = dim.value * UNIT_TO_MM.get(dim.unit, 1.0)
        if dim.context:
            dim_map[dim.context] = value_mm
        elif 'width' not in dim_map:
            dim_map['width'] = value_mm
        elif 'height' not in dim_map:
            dim_map['height'] = value_mm
        elif 'length' not in dim_map:
            dim_map['length'] = value_mm
        elif 'thickness' not in dim_map:
            dim_map['thickness'] = value_mm
    
    # Part-specific inference
    if part_type == 'motor_mount':
        motor_size = dim_map.get('width', dim_map.get('diameter', 50))
        params['motor_size_mm'] = motor_size
        params['thickness_mm'] = dim_map.get('thickness', 3.0)
        params['hole_diameter_mm'] = dim_map.get('diameter', 3.2)
        params['fillet_radius_mm'] = fillet_op_radius if fillet_op_radius is not None else dim_map.get('radius', 2.0)
        
    elif part_type == 'l_bracket':
        params['width_mm'] = dim_map.get('width', 50.0)
        params['height_mm'] = dim_map.get('height', 60.0)
        params['depth_mm'] = dim_map.get('depth', 40.0)
        params['thickness_mm'] = dim_map.get('thickness', 3.0)
        params['hole_count'] = patterns[0].count if patterns else 2
        params['hole_diameter_mm'] = dim_map.get('diameter', 3.2)
        params['fillet_radius_mm'] = fillet_op_radius if fillet_op_radius is not None else dim_map.get('radius', 2.0)
        
    elif part_type == 'flat_plate':
        params['length_mm'] = dim_map.get('length', 100.0)
        params['width_mm'] = dim_map.get('width', 80.0)
        params['thickness_mm'] = dim_map.get('thickness', 5.0)
        triple = _triple_dims_mm(text_lower)
        if triple:                                  # "200 x 100 x 6 mm" is length, width, thickness
            params['length_mm'], params['width_mm'], params['thickness_mm'] = triple
        params['hole_pattern'] = 'rectangular'
        # An explicit grid ("3 by 2 grid") wins over a bare count ("6 holes") from the same sentence.
        chosen = next((q for q in patterns if q.layout), patterns[0] if patterns else None)
        if _NO_HOLES_RE.search(text_lower):
            params['hole_count_x'] = 0
            params['hole_count_y'] = 0
        elif chosen is not None:
            p = chosen
            if p.layout:
                parts = p.layout.split('x')
                params['hole_count_x'] = int(parts[0])
                params['hole_count_y'] = int(parts[1])
            elif p.pattern_type == 'circular':
                params['hole_pattern'] = 'circular'
                params['hole_count_x'] = max(1, p.count)
                params['hole_count_y'] = 1
            else:
                params['hole_count_x'], params['hole_count_y'] = plate_grid_for_count(p.count, params['length_mm'], params['width_mm'])
            if p.diameter:
                params['hole_diameter_mm'] = p.diameter
        elif _HOLES_MENTIONED_RE.search(text_lower):
            # "with holes", "mounting holes", "drilled": holes were asked for but not counted, so the usual grid.
            params['hole_count_x'] = 4
            params['hole_count_y'] = 3
            params['hole_diameter_mm'] = dim_map.get('diameter', 3.2)
        else:
            # Nothing about holes: a plain plate. (It used to get the 4 x 3 grid whatever the prompt said.)
            params['hole_count_x'] = 0
            params['hole_count_y'] = 0
        params['corner_fillet_mm'] = fillet_op_radius if fillet_op_radius is not None else dim_map.get('radius', 3.0)
        
    elif part_type == 'shaft':
        params['diameter_mm'] = dim_map.get('diameter', 10.0)
        params['length_mm'] = dim_map.get('length', 50.0)
        if 'chamfer' in [d.context for d in dimensions]:
            params['chamfer_mm'] = dim_map.get('chamfer', 0.5)
        
    elif part_type == 'gear':
        params['module'] = dim_map.get('module', 2.0)
        params['teeth'] = int(dim_map.get('teeth', 20))
        params['thickness_mm'] = dim_map.get('thickness', 10.0)
        params['bore_diameter_mm'] = dim_map.get('bore', 5.0)
        
    elif part_type == 'sprocket':
        params['teeth'] = int(dim_map.get('teeth', 18))
        params['chain_pitch_mm'] = dim_map.get('pitch', 12.7)
        params['thickness_mm'] = dim_map.get('thickness', 8.0)
        params['bore_diameter_mm'] = dim_map.get('bore', 8.0)
        
    elif part_type == 'pulley':
        params['outer_diameter_mm'] = dim_map.get('diameter', 40.0)
        params['belt_width_mm'] = dim_map.get('width', 10.0)
        params['bore_diameter_mm'] = dim_map.get('bore', 5.0)
        params['thickness_mm'] = dim_map.get('thickness', 15.0)
        
    elif part_type == 'bearing':
        params['inner_diameter_mm'] = dim_map.get('inner_diameter', dim_map.get('diameter', 10.0))
        params['outer_diameter_mm'] = dim_map.get('outer_diameter', 20.0)
        params['width_mm'] = dim_map.get('width', 5.0)
        
    elif part_type == 'pipe_fitting':
        params['outer_diameter_mm'] = dim_map.get('diameter', 20.0)
        params['wall_thickness_mm'] = dim_map.get('thickness', 2.0)
        params['length_mm'] = dim_map.get('length', 50.0)
        
    elif part_type == 'flange':
        params['outer_diameter_mm'] = dim_map.get('diameter', 100.0)
        params['inner_diameter_mm'] = dim_map.get('inner_diameter', 50.0)
        params['thickness_mm'] = dim_map.get('thickness', 10.0)
        params['hole_count'] = 4
        params['hole_diameter_mm'] = 5.0
        
    elif part_type == 'structural_beam':
        params['height_mm'] = dim_map.get('height', 100.0)
        params['width_mm'] = dim_map.get('width', 50.0)
        params['length_mm'] = dim_map.get('length', 200.0)
        params['thickness_mm'] = dim_map.get('thickness', 5.0)
        # Previously never set at all, so every regex-parsed structural
        # beam silently defaulted to i_beam regardless of what the
        # description said - "c-channel beam" would generate an I-beam.
        params['beam_type'] = 'channel' if any(
            kw in text_lower for kw in ['channel', 'c-channel']
        ) else 'i_beam'
        
    elif part_type == 'hinge':
        params['length_mm'] = dim_map.get('length', 100.0)
        params['width_mm'] = dim_map.get('width', 30.0)
        params['thickness_mm'] = dim_map.get('thickness', 2.0)
        params['knuckle_count'] = 5
        
    elif part_type == 'spacer':
        params['outer_diameter_mm'] = dim_map.get('diameter', 10.0)
        params['inner_diameter_mm'] = dim_map.get('inner_diameter', 5.0)
        params['length_mm'] = dim_map.get('length', 10.0)
        
    elif part_type == 'washer':
        params['outer_diameter_mm'] = dim_map.get('diameter', 10.0)
        params['inner_diameter_mm'] = dim_map.get('inner_diameter', 5.0)
        params['thickness_mm'] = dim_map.get('thickness', 1.5)
        
    elif part_type == 'cam':
        params['base_radius_mm'] = dim_map.get('radius', 20.0)
        params['lift_mm'] = dim_map.get('lift', 10.0)
        params['thickness_mm'] = dim_map.get('thickness', 5.0)
        params['bore_diameter_mm'] = dim_map.get('bore', 5.0)
        
    elif part_type == 'simple_box':
        params['length_mm'] = dim_map.get('length', 100.0)
        params['width_mm'] = dim_map.get('width', 80.0)
        params['height_mm'] = dim_map.get('height', 50.0)
        triple = _triple_dims_mm(text_lower)
        if triple:                                  # "100 x 80 x 50 mm" is length, width, height
            params['length_mm'], params['width_mm'], params['height_mm'] = triple
        elif re.search(r"\bcube\b", text_lower) and len(dimensions) == 1:
            params['length_mm'] = params['width_mm'] = params['height_mm'] = dimensions[0].value * UNIT_TO_MM.get(dimensions[0].unit, 1.0)
        wall = _WALL_OF_RE.search(text_lower) or _WALL_RE.search(text_lower)
        params['wall_thickness_mm'] = float(wall.group(1)) if wall else dim_map.get('thickness', 3.0)
        # An enclosure, a box with walls, or an open-top box is meant to be hollow. Anything else is a solid block.
        params['hollow'] = bool(_HOLLOW_RE.search(text_lower))

    elif part_type in ('turbofan_engine', 'jet_aircraft', 'fuselage', 'tail_surface'):
        _num = r'(\d+(?:\.\d+)?)\s*(mm|cm|m|ft|feet|meters?|metres?)\b'
        _unit = {'mm': 1, 'cm': 10, 'm': 1000, 'ft': 304.8, 'feet': 304.8}

        def _dim(label):
            m = (re.search(label + r'\s*(?:of|is|=|:)?\s*' + _num, text_lower)
                 or re.search(_num + r'\s*(?:\w+\s+)?' + label, text_lower))
            return float(m.group(1)) * _unit.get(m.group(2), 1000) if m else None
        if part_type == 'turbofan_engine':
            v = _dim(r'fan\s+diameter')
            if v: params['fan_diameter_mm'] = v
        elif part_type == 'jet_aircraft':
            for key, label in (('wing_span_mm', r'(?:wing\s*span|span)'),
                               ('fuselage_length_mm', r'(?:fuselage\s+length|length)')):
                v = _dim(label)
                if v: params[key] = v
            if re.search(r'\b(?:twin|two)[- ]engine', text_lower): params['engines'] = 2
            elif re.search(r'\b(?:four|quad)[- ]engine', text_lower): params['engines'] = 4
            sw = re.search(r'(\d+(?:\.\d+)?)\s*(?:deg(?:rees?)?|\u00b0)\s*sweep', text_lower)
            if sw: params['wing_sweep_deg'] = float(sw.group(1))
        elif part_type == 'fuselage':
            v = _dim(r'(?:length|long)')
            if v: params['length_mm'] = v
            v = _dim(r'diameter')
            if v: params['diameter_mm'] = v
        else:
            if re.search(r'\bvertical\b', text_lower): params['orientation'] = 'vertical'
    elif part_type in ('airfoil_wing', 'wing_rib', 'fuselage_frame'):
        def _num(pattern):
            m = re.search(pattern, text_lower)
            return float(m.group(1)) if m else None
        naca = re.search(r'naca\s*-?\s*(\d{4})', text_lower)
        if naca:
            params['naca'] = naca.group(1)
        if part_type == 'airfoil_wing':
            v = _num(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:semi[- ]?)?span') or _num(r'span\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)')
            if v: params['span_mm'] = v
            v = _num(r'root\s*chord\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)') or _num(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?root\s*chord')
            if v: params['root_chord_mm'] = v
            v = _num(r'tip\s*chord\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)') or _num(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?tip\s*chord')
            if v: params['tip_chord_mm'] = v
            v = _num(r'(\d+(?:\.\d+)?)\s*(?:deg(?:rees?)?|\u00b0)\s*sweep') or _num(r'sweep\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)')
            if v: params['sweep_deg'] = v
            if re.search(r'full wing|both halves|both sides', text_lower):
                params['both_halves'] = True
        elif part_type == 'wing_rib':
            v = _num(r'chord\s*(?:of|is|=|:)?\s*(\d+(?:\.\d+)?)') or _num(r'(\d+(?:\.\d+)?)\s*(?:mm\s*)?chord')
            if v: params['chord_mm'] = v
            if 'thickness' in dim_map: params['thickness_mm'] = dim_map['thickness']
        else:
            v = dim_map.get('diameter')
            if v: params['outer_diameter_mm'] = v
            if 'thickness' in dim_map: params['thickness_mm'] = dim_map['thickness']
            v = _num(r'(\d+)\s*(?:lightening\s*)?holes')
            if v: params['lightening_hole_count'] = int(v)

    elif is_pack_part(part_type):
        # Domain-pack part: the pack owns its own parameter names and
        # extraction; only stated values are set, templates default the rest.
        params.update(extract_pack_parameters(part_type, description, dim_map))

    elif part_type == 'connecting_rod':
        params['center_distance_mm'] = dim_map.get('length', dim_map.get('center_distance', 120.0))
        params['big_end_diameter_mm'] = dim_map.get('bore', dim_map.get('diameter', 24.0))
        params['small_end_diameter_mm'] = dim_map.get('inner_diameter', 12.0)
        params['thickness_mm'] = dim_map.get('thickness', 12.0)

    elif part_type == 'crankshaft':
        if 'throws' in dim_map or 'cylinders' in dim_map:
            params['num_throws'] = int(dim_map.get('throws', dim_map.get('cylinders', 4)))
        params['stroke_mm'] = dim_map.get('stroke', dim_map.get('length', 80.0))
        params['main_journal_diameter_mm'] = dim_map.get('diameter', 50.0)

    elif part_type == 'freeform':
        # Two bugs found via live testing, not just one:
        # (1) profile_points was set to an explicit [] here, which defeats
        #     the template's own `params.get('profile_points', [default])`
        #     fallback - a present-but-empty key is not a missing key, so
        #     the template crashed on profile_points[0] instead of using
        #     its default shape. Omitting the key when we have nothing
        #     real to put there lets that fallback actually work.
        # (2) generate_freeform() dispatches on params['operation'], not
        #     on separate sweep/loft/revolve booleans - setting those
        #     booleans (as this branch previously did) was dead code that
        #     nothing downstream reads. The real dispatch key was never
        #     set at all, so it silently always defaulted to 'revolve'.
        op_types = [op.get('type') for op in operations if op.get('type') in ('sweep', 'loft', 'revolve')]
        params['operation'] = op_types[0] if op_types else 'revolve'

    elif part_type == 'building':
        params.update(_building_params_from_text(text_lower))

    elif part_type in ('road_segment', 'box_culvert', 'retaining_wall'):
        params.update(_civil_params_from_text(part_type, text_lower))

    return params

def extract_material(text: str) -> str | None:
    """Extract material from text."""
    text_lower = text.lower()
    
    materials = {
        'aluminum': 'aluminum_6061',
        'aluminium': 'aluminum_6061',
        '6061': 'aluminum_6061',
        '6063': 'aluminum_6063',
        '7075': 'aluminum_7075',
        'steel': 'steel_1018',
        '1018': 'steel_1018',
        '4140': 'steel_4140',
        'stainless': 'stainless_304',
        '304': 'stainless_304',
        '316': 'stainless_316',
        'brass': 'brass_c360',
        'copper': 'copper_c110',
        'bronze': 'bronze_phosphor',
        'titanium': 'titanium_ti6al4v',
        'plastic': 'plastic_abs',
        'abs': 'plastic_abs',
        'pla': 'plastic_pla',
        'nylon': 'plastic_nylon',
        'delrin': 'plastic_delrin',
        'acetal': 'plastic_delrin',
        'wood': 'wood_plywood',
        'plywood': 'wood_plywood',
        'mdf': 'wood_mdf',
    }
    
    for keyword, material in materials.items():
        if keyword in text_lower:
            return material
    
    return None

def parse_description(description: str, use_claude: bool = False, api_key: str = None) -> ParsedParameters:
    """
    Main parsing function with smart extraction.
    """
    warnings = []

    # A circuit board is described by what it does, not by dimensions: hand the sentence to the pcb
    # pack (see cad_templates/pcb.py). Mechanical parts that merely mention a PCB are not affected.
    from pcb.intent import is_pcb_request
    if is_pcb_request(description):
        return ParsedParameters(
            part_type='pcb_board', parameters={'description': description.strip()},
            material=None, operations=[], assembly_parts=[], confidence=0.8, warnings=[]
        )

    # Extract all data
    dimensions = extract_all_dimensions(description)
    patterns = extract_hole_patterns(description)
    operations = extract_operations(description)
    material = extract_material(description)
    
    # Infer part type
    part_type = infer_part_type(description, dimensions, operations)
    
    # Infer missing dimensions
    parameters = infer_missing_dimensions(part_type, dimensions, patterns, description, operations)
    
    # Templates that expose their own fillet_radius_mm/corner_fillet_mm
    # already apply the parsed fillet value directly (see infer_missing_dimensions
    # above). Leaving the matching entry in `operations` would make
    # cad_generator._apply_operations attempt a second, redundant fillet
    # pass on edges that are already rounded, which fails and gets
    # silently dropped. Drop it here once it's been consumed by the template.
    consumed_fillet_radius = parameters.get('fillet_radius_mm', parameters.get('corner_fillet_mm'))
    if consumed_fillet_radius is not None:
        operations = [
            op for op in operations
            if not (op.get('type') == 'fillet' and op.get('radius') == consumed_fillet_radius)
        ]
    
    # Same double-application problem for freeform parts: infer_missing_dimensions
    # already consumed the sweep/loft/revolve entry into parameters['operation']
    # to pick which generator function to call. Leaving it in `operations` too
    # makes apply_operations() try to treat it as a post-build op it doesn't
    # recognize (it only knows fillet/chamfer/shell), logging a harmless but
    # noisy "unknown operation type 'revolve' ignored" warning downstream.
    consumed_operation = parameters.get('operation')
    if consumed_operation is not None:
        operations = [op for op in operations if op.get('type') != consumed_operation]
    
    # Add operations to parameters
    if operations:
        parameters['operations'] = operations
    
    # Validate and warn
    if not dimensions:
        warnings.append("No dimensions found in description, using defaults")
    
    if part_type == 'freeform' and not parameters.get('profile_points'):
        warnings.append("Freeform part requires profile points, using default shape")
    
    # Confidence scoring
    confidence = 0.5
    if dimensions:
        confidence += 0.2
    if patterns:
        confidence += 0.1
    if material:
        confidence += 0.1
    if part_type != 'flat_plate':  # More specific than default
        confidence += 0.1
    
    return ParsedParameters(
        part_type=part_type,
        parameters=parameters,
        material=material,
        operations=operations,
        assembly_parts=[],
        confidence=min(confidence, 1.0),
        warnings=warnings
    )

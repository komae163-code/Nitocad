"""
Named, deterministic assembly templates. Used when a description names a known
assembly (e.g. "wheel corner module"): no LLM is needed or involved, so the
result is reproducible. Anything not recognised goes to the LLM assembly
parser (assembly_parser.py); if that is unavailable the request fails with a
clear message rather than silently substituting a different assembly.

Parameter overrides are deliberately limited to values that are shared
between mating parts or unambiguous in a combined description (bolt pattern,
rotor diameter, vented/solid, lobed hub, arm length). Everything else uses
each part's defaults, which are mutually compatible by construction (and
checked by tests/test_assembly.py).
"""
from __future__ import annotations

import copy
import re

CORNER_PHRASES = (
    "corner module", "wheel corner", "suspension corner", "corner assembly",
    "brake corner", "front corner", "rear corner", "wheel end module",
)

_CORNER_MATES = [
    # chassis: the arm hangs between the subframe's clevis ears. The front
    # mate positions the arm; the rear mate is a closing mate that VERIFIES
    # the two pivot spacings agree.
    {"a": "subframe.left_pivot_front", "b": "arm.pivot_front"},
    {"a": "subframe.left_pivot_rear", "b": "arm.pivot_rear"},
    # the ball joint presses into the arm's bore
    {"a": "arm.ball_joint_socket", "b": "ball_joint.housing"},
    # its tapered stud seats in the knuckle's tapered bore
    {"a": "ball_joint.stud", "b": "knuckle.ball_joint_seat"},
    # the hub barrel seats in the knuckle's bearing bore
    {"a": "knuckle.bearing_bore", "b": "hub.bearing_journal"},
    # the rotor seats on the hub flange face
    {"a": "hub.rotor_face", "b": "rotor.hub_face"},
    # closing mates: verified, not used for placement
    {"a": "hub.rotor_pilot", "b": "rotor.hub_bore"},
    {"a": "hub.stud_pattern", "b": "rotor.bolt_pattern"},
]

NAMED_ASSEMBLIES = {"wheel_corner_module": CORNER_PHRASES}


def match_named_assembly(description: str) -> str | None:
    t = re.sub(r"[\s_\-]+", " ", description.lower())
    for name, phrases in NAMED_ASSEMBLIES.items():
        if any(p in t for p in phrases):
            return name
    return None


def corner_module(overrides: dict[str, dict] | None = None, name: str = "wheel_corner_module") -> dict:
    """Subframe + control arm + ball joint + steering knuckle + wheel hub +
    brake rotor: one front-left wheel corner, hung from its chassis."""
    parts = [
        {"id": "subframe", "part_type": "subframe", "parameters": {}},
        {"id": "arm", "part_type": "control_arm", "parameters": {}},
        {"id": "ball_joint", "part_type": "ball_joint", "parameters": {}},
        {"id": "knuckle", "part_type": "steering_knuckle", "parameters": {}},
        {"id": "hub", "part_type": "wheel_hub", "parameters": {}},
        {"id": "rotor", "part_type": "brake_rotor", "parameters": {}},
    ]
    for p in parts:
        p["parameters"].update((overrides or {}).get(p["id"], {}))
    return {"name": name, "root": "subframe", "parts": parts, "mates": copy.deepcopy(_CORNER_MATES)}


def build_named(name: str, description: str) -> dict:
    if name != "wheel_corner_module":
        raise KeyError(name)
    from domain_packs.automotive.standards import parse_bolt_pattern
    from domain_packs.automotive.parsing import extract_parameters

    t = description.lower()
    over: dict[str, dict] = {"subframe": {}, "arm": {}, "ball_joint": {}, "knuckle": {}, "hub": {}, "rotor": {}}

    bp = parse_bolt_pattern(t)
    if bp:  # one pattern, applied to BOTH sides of the mate
        for pid in ("hub", "rotor"):
            over[pid]["bolt_count"], over[pid]["bolt_pcd_mm"] = bp

    rotor = extract_parameters("brake_rotor", description, {})
    for k in ("outer_diameter_mm", "vented"):
        if k in rotor:
            over["rotor"][k] = rotor[k]
    if "lobed" in t:
        over["hub"]["lobed"] = True
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:long\s*)?(?:control\s*)?arm|arm\s*(?:length\s*)?(?:of\s*)?(\d+(?:\.\d+)?)\s*mm", t)
    if m:
        over["arm"]["arm_length_mm"] = float(m.group(1) or m.group(2))
    return corner_module(over)

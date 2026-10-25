"""
Automotive domain pack.

Scope (what this pack is):
  parametric, validated dimensional geometry for six common automotive
  components, produced through the same parse -> validate -> build -> export
  -> anchor pipeline as every other part type.

Parts: brake_rotor, wheel_hub, piston, flywheel, control_arm, steering_knuckle,
ball_joint, subframe, battery_bracket. Rotor, hub, knuckle and control arm also declare mating
interfaces (see mating.py), so they can be assembled into a checked wheel
corner module (assembly_templates.py).

Scope (what this pack is NOT):
  it does not perform or imply structural, fatigue, thermal, NVH, crash or
  durability analysis, does not check regulatory/standards compliance
  (FMVSS/ECE/ISO/SAE/IATF), and does not model materials beyond the label
  attached to a job. A valid solid with sensible proportions is not a
  qualified automotive part. See each part module's docstring for exactly
  which features are modelled and which are deliberately not.
"""
from __future__ import annotations

from domain_packs import DomainPack, register_pack

from . import (ball_joint, battery_bracket, brake_rotor, control_arm, flywheel,
               piston, steering_knuckle, subframe, wheel_hub)
from .common import prepare
from .envelopes import ENVELOPES
from .parsing import extract_parameters, infer_part_type

_PARTS = {
    "brake_rotor":     (brake_rotor,     "Vented or solid brake disc with hat, bore and stud holes"),
    "wheel_hub":       (wheel_hub,       "Wheel hub flange with stud holes, barrel, bore and pilot (round or lobed)"),
    "piston":          (piston,          "Piston with ring grooves, optional dish, two-stage cavity, pin bosses and bore"),
    "flywheel":        (flywheel,        "Clutch flywheel with crank/clutch bolt circles, spigot bore, optional ring-gear teeth"),
    "control_arm":     (control_arm,     "Wishbone/A-arm with inboard bushing sleeves, ball-joint boss, optional brace"),
    "steering_knuckle": (steering_knuckle, "Wheel upright: bearing-bore housing, leg and tapered ball-joint seat"),
    "ball_joint":      (ball_joint,      "Press-in ball joint: housing, flange and tapered stud (outside geometry only)"),
    "subframe":        (subframe,        "Front subframe/cradle: rails, crossmembers and four control-arm clevises"),
    "battery_bracket": (battery_bracket, "Formed L-bracket for EV battery enclosure mounting: bend radius, slots, gussets"),
}

PACK = DomainPack(
    name="automotive",
    title="Automotive",
    version="1.2.0",
    summary="Brake, driveline, engine, suspension and EV-enclosure mounting components.",
    scope_note=(
        "Generates parametric dimensional geometry with geometric feasibility checks. "
        "It does not establish structural adequacy, fatigue life, thermal performance, "
        "crash behaviour or regulatory/standards compliance."
    ),
    templates={
        "brake_rotor":     brake_rotor.generate_brake_rotor,
        "wheel_hub":       wheel_hub.generate_wheel_hub,
        "piston":          piston.generate_piston,
        "flywheel":        flywheel.generate_flywheel,
        "control_arm":     control_arm.generate_control_arm,
        "steering_knuckle": steering_knuckle.generate_steering_knuckle,
        "ball_joint":      ball_joint.generate_ball_joint,
        "subframe":        subframe.generate_subframe,
        "battery_bracket": battery_bracket.generate_battery_bracket,
    },
    validators={
        "brake_rotor":     brake_rotor.validate_brake_rotor,
        "wheel_hub":       wheel_hub.validate_wheel_hub,
        "piston":          piston.validate_piston,
        "flywheel":        flywheel.validate_flywheel,
        "control_arm":     control_arm.validate_control_arm,
        "steering_knuckle": steering_knuckle.validate_steering_knuckle,
        "ball_joint":      ball_joint.validate_ball_joint,
        "subframe":        subframe.validate_subframe,
        "battery_bracket": battery_bracket.validate_battery_bracket,
    },
    param_specs={name: mod.SPEC for name, (mod, _) in _PARTS.items()},
    infer_part_type=infer_part_type,
    extract_parameters=extract_parameters,
    part_descriptions={name: desc for name, (_, desc) in _PARTS.items()},
    interfaces={
        "brake_rotor":      brake_rotor.interfaces_brake_rotor,
        "wheel_hub":        wheel_hub.interfaces_wheel_hub,
        "control_arm":      control_arm.interfaces_control_arm,
        "steering_knuckle": steering_knuckle.interfaces_steering_knuckle,
        "ball_joint":       ball_joint.interfaces_ball_joint,
        "subframe":         subframe.interfaces_subframe,
    },
    envelopes=ENVELOPES,
    value_resolvers={
        name: (lambda p, m=mod: prepare(p, m.SPEC, m._derive, m._check))
        for name, (mod, _) in _PARTS.items() if hasattr(mod, "_derive") and hasattr(mod, "_check")
    },
)

register_pack(PACK)

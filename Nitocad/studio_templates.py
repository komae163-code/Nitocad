"""
The Studio's template picker: known parts and small assemblies to start from.

A template is a finished part spec (the same {part_type, parameters, material, operations} the chat
planner writes), so starting one builds a free preview through the normal generator with no AI. After
that it is an ordinary version in the chat, and the person changes it by talking to it.

Every spec is held to two rules, both tested:
  * it passes validator.validate_parameters with no warnings and no corrections, so what the picker
    shows is what gets built, and
  * it uses only part types the CAD engine has (cad_templates.TEMPLATES).

Assemblies use layout "custom" with explicit positions. Shafts, gears, pulleys and bearings are built
along Z from the origin, so a part that sits on a shaft is placed by its Z offset. Bores come from
assembly_relations.fit_bore: 0.2 mm over the shaft diameter, a running clearance, so the two solids never
share a surface. Once built, edits to the shaft carry through to those bores and positions on their own.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from assembly_relations import fit_bore

CATEGORIES = [
    ("standard_parts", "Standard parts"),
    ("fasteners", "Fasteners"),
    ("brackets", "Brackets and plates"),
    ("drive", "Gears and drives"),
    ("enclosures", "Enclosures and tubes"),
    ("assemblies", "Assemblies"),
    ("vehicles", "Vehicles"),
]
_CATEGORY_LABEL = dict(CATEGORIES)


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    category: str
    summary: str
    tags: tuple[str, ...]
    hint: str
    spec: dict[str, Any]

    @property
    def parts(self) -> int:
        return len(self.spec["parameters"]["parts"]) if self.spec["part_type"] == "assembly" else 1


def _part(part_type: str, parameters: dict, material: str | None = None) -> dict[str, Any]:
    return {"part_type": part_type, "parameters": parameters, "material": material, "operations": []}


def _assembly(parts: list[dict]) -> dict[str, Any]:
    return {"part_type": "assembly", "parameters": {"parts": parts, "layout": "custom"}, "material": None, "operations": []}


def _on(name: str, part_type: str, parameters: dict, z: float = 0.0) -> dict[str, Any]:
    return {"name": name, "part_type": part_type, "parameters": parameters, "count": 1, "position": [0.0, 0.0, z]}


_T = Template
_BORE_8 = fit_bore(8)  # a bore on the 8 mm shafts below; later edits keep it fitted (assembly_relations)
TEMPLATES: tuple[Template, ...] = (
    # ---- standard parts
    _T("bearing-608", "608 ball bearing", "standard_parts", "8 mm bore, 22 mm outside, 7 mm wide.", ("Standard size",),
       "Try: make the bore 10 mm", _part("bearing", {"inner_diameter_mm": 8, "outer_diameter_mm": 22, "width_mm": 7})),
    _T("bearing-6205", "6205 deep groove bearing", "standard_parts", "25 mm bore, 52 mm outside, 15 mm wide, from the bearing table.",
       ("Standard size",), "Try: use a 6206 instead", _part("std_bearing", {"designation": "6205"})),
    _T("o-ring-20", "O-ring 20 x 3.53", "standard_parts", "20 mm inside diameter, 3.53 mm cross-section.", ("Standard size",),
       "Try: make the inside diameter 30 mm", _part("o_ring", {"cross_section_mm": 3.53, "inner_diameter_mm": 20})),
    _T("key-25", "Parallel key for a 25 mm shaft", "standard_parts", "Key sized from the standard for a 25 mm shaft.", ("Standard size",),
       "Try: use a 30 mm shaft", _part("key", {"shaft_diameter_mm": 25})),
    _T("flange-nps2", "ASME slip-on flange, NPS 2", "standard_parts", "Class 150 slip-on pipe flange, from the ASME B16.5 table.",
       ("Standard size", "Pipe"), "Try: use NPS 3", _part("asme_flange", {"standard": "ASME_B16_5_150", "nps": "2", "flange_type": "slip_on"})),
    # ---- fasteners
    _T("screw-m8", "M8 socket cap screw", "fasteners", "ISO 4762, 30 mm long.", ("Standard size", "Metric"),
       "Try: make it 40 mm long", _part("socket_cap_screw", {"size": "M8", "length_mm": 30})),
    _T("bolt-m8", "M8 hex bolt", "fasteners", "ISO 4017, 40 mm long.", ("Standard size", "Metric"),
       "Try: use M10", _part("hex_bolt", {"size": "M8", "length_mm": 40})),
    _T("nut-m8", "M8 hex nut", "fasteners", "ISO 4032, to the standard's dimensions.", ("Standard size", "Metric"),
       "Try: use M12", _part("hex_nut", {"size": "M8"})),
    _T("standoff-m3", "Hex standoff for M3", "fasteners", "6 mm across the flats, 10 mm long, with a 3.2 mm hole.", ("Electronics",),
       "Try: make it 20 mm long", _part("hex_standoff", {"across_flats_mm": 6, "inner_diameter_mm": 3.2, "length_mm": 10})),
    _T("spacer-10", "Round spacer", "fasteners", "10 mm outside, 5 mm bore, 10 mm long.", (),
       "Try: make the bore 6 mm", _part("spacer", {"outer_diameter_mm": 10, "inner_diameter_mm": 5, "length_mm": 10})),
    _T("washer-8", "Flat washer for M8", "fasteners", "16 mm outside, 8.4 mm hole, 1.6 mm thick.", ("Metric",),
       "Try: make it 2 mm thick", _part("washer", {"outer_diameter_mm": 16, "inner_diameter_mm": 8.4, "thickness_mm": 1.6})),
    # ---- brackets and plates
    _T("l-bracket", "L bracket", "brackets", "Two flanges at a right angle, with a hole in each.", ("Mounting",),
       "Try: make it thicker", _part("l_bracket", {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 4,
                                                   "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.5}, "6061")),
    _T("t-bracket", "T bracket", "brackets", "A flat cap on a stem, with two mounting holes.", ("Mounting",),
       "Try: add 2 more holes", _part("t_bracket", {"length_mm": 60, "cap_width_mm": 40, "stem_height_mm": 30, "thickness_mm": 4,
                                                    "fillet_radius_mm": 1.5, "hole_count": 2, "hole_diameter_mm": 3.2}, "6061")),
    _T("channel-bracket", "Channel bracket", "brackets", "A U channel with two mounting holes through its base.", ("Mounting",),
       "Try: make it 100 mm long", _part("channel_bracket", {"length_mm": 60, "width_mm": 20, "height_mm": 15, "wall_thickness_mm": 2,
                                                            "mount_hole_count": 2, "mount_hole_diameter_mm": 4}, "6061")),
    _T("plate-holes", "Mounting plate", "brackets", "100 x 80 x 5 mm with a 4 by 3 grid of holes.", ("Mounting",),
       "Try: make it 150 mm long", _part("flat_plate", {"length_mm": 100, "width_mm": 80, "thickness_mm": 5, "hole_diameter_mm": 3.2,
                                                        "corner_fillet_mm": 3, "hole_count_x": 4, "hole_count_y": 3}, "6061")),
    _T("motor-mount", "Motor mount", "brackets", "A plate for a 50 mm motor, with four mounting holes.", ("Mounting",),
       "Try: make it for a 60 mm motor", _part("motor_mount", {"thickness_mm": 3, "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.0,
                                                               "motor_size_mm": 50}, "6061")),
    # ---- gears and drives
    _T("gear-20", "Spur gear, 20 teeth", "drive", "Module 2, 10 mm thick, 5 mm bore.", ("Gear",),
       "Try: use 30 teeth", _part("gear", {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 5})),
    _T("pulley-40", "V-belt pulley", "drive", "40 mm outside, 10 mm belt, 5 mm bore.", ("Pulley",),
       "Try: make the bore 8 mm", _part("pulley", {"outer_diameter_mm": 40, "belt_width_mm": 10, "bore_diameter_mm": 5,
                                                    "thickness_mm": 15, "groove_depth_mm": 3})),
    _T("sprocket-18", "Chain sprocket, 18 teeth", "drive", "12.7 mm pitch, 8 mm thick, 8 mm bore.", ("Sprocket",),
       "Try: use 24 teeth", _part("sprocket", {"teeth": 18, "chain_pitch_mm": 12.7, "thickness_mm": 8, "bore_diameter_mm": 8})),
    _T("shaft-10", "Shaft", "drive", "10 mm diameter, 100 mm long, with a 1 mm chamfer.", ("Shaft",),
       "Try: add a keyway", _part("shaft", {"diameter_mm": 10, "length_mm": 100, "chamfer_mm": 1}, "C45 steel")),
    # ---- enclosures and tubes
    _T("box-100", "Box", "enclosures", "100 x 80 x 50 mm with 3 mm walls.", ("Enclosure",),
       "Try: add a vent slot", _part("simple_box", {"length_mm": 100, "width_mm": 80, "height_mm": 50, "wall_thickness_mm": 3}, "ABS")),
    _T("tube-20", "Round tube", "enclosures", "20 mm outside, 2 mm wall, 100 mm long.", ("Tube",),
       "Try: make it square", _part("tube", {"outer_diameter_mm": 20, "wall_thickness_mm": 2, "length_mm": 100}, "6061")),
    # ---- assemblies
    _T("asm-gear-shaft", "Gear on a shaft", "assemblies", "A 20 tooth gear fitted on an 8 mm shaft.", ("2 parts",),
       "Try: use 30 teeth", _assembly([
           _on("shaft", "shaft", {"diameter_mm": 8, "length_mm": 60}),
           _on("gear", "gear", {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": _BORE_8}, z=20)])),
    _T("asm-bearing-shaft", "Bearing on a shaft", "assemblies", "A 608 size bearing fitted on an 8 mm shaft.", ("2 parts",),
       "Try: make the shaft 80 mm long", _assembly([
           _on("shaft", "shaft", {"diameter_mm": 8, "length_mm": 40}),
           _on("bearing", "bearing", {"inner_diameter_mm": _BORE_8, "outer_diameter_mm": 22, "width_mm": 7}, z=15)])),
    _T("asm-pulley-shaft", "Pulley on a shaft", "assemblies", "A 40 mm pulley fitted on an 8 mm shaft.", ("2 parts",),
       "Try: make the pulley 50 mm", _assembly([
           _on("shaft", "shaft", {"diameter_mm": 8, "length_mm": 50}),
           _on("pulley", "pulley", {"outer_diameter_mm": 40, "belt_width_mm": 10, "bore_diameter_mm": _BORE_8,
                                    "thickness_mm": 15, "groove_depth_mm": 3}, z=15)])),
    _T("asm-shaft-bearings", "Shaft between two bearings", "assemblies", "A 60 mm shaft with a bearing near each end.", ("3 parts",),
       "Try: move the bearings closer", _assembly([
           _on("shaft", "shaft", {"diameter_mm": 8, "length_mm": 60}),
           _on("bearing_left", "bearing", {"inner_diameter_mm": _BORE_8, "outer_diameter_mm": 22, "width_mm": 7}, z=5),
           _on("bearing_right", "bearing", {"inner_diameter_mm": _BORE_8, "outer_diameter_mm": 22, "width_mm": 7}, z=48)])),
)

_BY_ID = {t.id: t for t in TEMPLATES}


def get(template_id: str) -> Template | None:
    return _BY_ID.get(template_id)


def spec_of(template: Template) -> dict[str, Any]:
    """A fresh copy of the spec, safe to hand to the generator (which may change it)."""
    return copy.deepcopy(template.spec)


def listing() -> dict[str, Any]:
    """What GET /templates returns: only the categories that have templates, and the cards."""
    used = {t.category for t in TEMPLATES}
    return {
        "categories": [{"id": cid, "label": label} for cid, label in CATEGORIES if cid in used],
        "templates": [{
            "id": t.id, "title": t.title, "category": t.category, "category_label": _CATEGORY_LABEL[t.category],
            "summary": t.summary, "tags": list(t.tags), "hint": t.hint, "part_type": t.spec["part_type"], "parts": t.parts,
        } for t in TEMPLATES],
    }


def reply_for(template: Template) -> str:
    """The assistant's message when a template is started."""
    what = (f"{template.title}, an assembly of {template.parts} parts" if template.parts > 1 else template.title)
    return (f"Started from the template: {what}. This is a free preview, built with no AI. "
            f"{template.hint}, or press Generate when it looks right.")


def _vehicle_templates() -> tuple[Template, ...]:
    """Vehicle starting points, built from the shipped vehicle specs (vehicles/examples). Each keeps its link to the
    vehicle spec, so a chat edit such as "make the tray 900 mm long" changes the vehicle's own parameters. A problem
    here must never take the other templates down, so it logs and returns nothing."""
    try:
        import json
        from pathlib import Path

        import vehicle_chat
        base = Path(__file__).resolve().parent / "vehicles" / "examples"
        cards = (
            ("wheelbarrow", "veh-wheelbarrow", "Wheelbarrow", "A one-wheel garden barrow: tray, frame, legs and a wheel on a bearing axle.",
             ("Vehicle", "Concept layout"), "Try: make the tray 900 mm long"),
            ("bicycle", "veh-bicycle", "Bicycle", "A road-bike layout: frame tubes, two wheels, cranks and chainring, bars and saddle.",
             ("Vehicle", "Concept layout"), "Try: make the wheel diameter 700 mm"),
            ("two_axle_cart", "veh-two-axle-cart", "Two-axle cart", "A bare two-wheel axle set: an axle and two wheels, the smallest vehicle layout.",
             ("Vehicle", "Concept layout"), "Try: make the wheel diameter 300 mm"),
            ("rc_helicopter", "veh-rc-helicopter", "RC helicopter", "An electric RC helicopter with its internals: motor, gears, rotor head, tail and skids.",
             ("Vehicle", "Internals", "Concept layout"), "Try: make the rotor span 500 mm"),
        )
        out = []
        for stem, tid, title, summary, tags, hint in cards:
            built = vehicle_chat.to_chat_spec(json.loads((base / f"{stem}.json").read_text(encoding="utf-8")))
            if not built["ok"]:
                continue
            out.append(Template(tid, title, "vehicles", summary, tags, hint, built["spec"]))
        return tuple(out)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("vehicle templates could not be built")
        return ()


TEMPLATES = TEMPLATES + _vehicle_templates()
_BY_ID = {t.id: t for t in TEMPLATES}          # rebuilt: the vehicle starters are added after the first lookup is made

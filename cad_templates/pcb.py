"""
PCB board as a 3D part: a 1.6 mm FR-4 slab with every component drawn as a block at its real
footprint size and a typical height.

All the electronics (blocks, ERC, placement) lives in the `pcb` package; this template only turns
its placed design into a CadQuery solid so a board flows through the normal CAD pipeline: free
STL preview, STEP / IGES / DXF / PDF export, credits and anchoring. See pcb/model3d.py for what the
solid does and does not model (no copper, vias or holes - those are in the KiCad package).

Expected params:
- description: plain-English description of the board (read by the offline planner)
- spec: optional validated block spec (stored when an AI planned the board); wins over description
"""
import cadquery as cq

from pcb.model3d import design_from_params, solid_boxes


def generate_pcb_board(params: dict) -> cq.Workplane:
    design, _notes = design_from_params(params)        # ValueError with a plain reason if it cannot be built
    result = None
    for b in solid_boxes(design):
        box = (
            cq.Workplane("XY")
            .box(b["sx"], b["sy"], b["sz"], centered=(True, True, False))
            .translate((b["cx"], b["cy"], b["z0"]))
        )
        result = box if result is None else result.union(box)
    return result

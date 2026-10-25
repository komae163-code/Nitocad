"""
NitoCAD PCB domain pack.

Design rule for this whole package: the language model never draws a
board. It only chooses pre-designed circuit *blocks* from blocks.py and fills
in their parameters. Everything after that - netlist, placement, routing,
checks, KiCad files - is deterministic code, and the final word on whether
a board is manufacturable belongs to KiCad's own DRC, not to us.
"""
PACK_VERSION = "pcb-pack-0.1"

"""Does a message ask for a circuit board? Shared by the CAD parser and the Studio chat planner.

Deliberately conservative: "hex standoff for PCB mounting" and "enclosure for a PCB" are mechanical
parts that merely mention a board, so any mechanical noun cancels the match.
"""
from __future__ import annotations

import re

_PCB = re.compile(
    r"\b(pcbs?|circuit[\s-]boards?|printed[\s-]circuit|schematics?|atmega\w*|555[\s-]timer|i2c|"
    r"mosfet|buck[\s-]converter|voltage[\s-]regulator|microcontroller[\s-]board)\b", re.I)
_MECHANICAL = re.compile(
    r"\b(standoffs?|spacers?|brackets?|mount(?:s|ing)?|enclosures?|cases?|housings?|boxes|box|plates?|"
    r"holders?|bolts?|screws?|stands?|clips?|flanges?|gears?|shafts?)\b", re.I)
_REMOVAL = re.compile(r"\b(remove|delete|drop|get rid of|take out|without)\b", re.I)


def is_pcb_request(text: str) -> bool:
    return bool(_PCB.search(text or "")) and not _MECHANICAL.search(text or "")


def mentions_mechanical_part(text: str) -> bool:
    return bool(_MECHANICAL.search(text or ""))


def wants_removal(text: str) -> bool:
    return bool(_REMOVAL.search(text or ""))


_QUESTION_START = re.compile(
    r"^\s*(what|why|how|which|who|when|where|does|do|is|are|can|could|should|would|explain|tell me|describe)\b", re.I)


def is_question(text: str) -> bool:
    """A question or request for an explanation. It must never change a board: "what is a 555?" is not
    a request to add a 555."""
    t = (text or "").strip()
    return t.endswith("?") or bool(_QUESTION_START.match(t))


# ------------------------------------------------------------------ board width ---
BOARD_WIDTH_RANGE = (20.0, 200.0)     # mm, the range pcb.compose accepts for board.max_width_mm

_W = r"(\d+(?:\.\d+)?)\s*(mm|cm)?"
_NOT_FEATURE = r"(?!\s+(?:trace|track|via|pad|hole|slot|wire|copper))"   # "5 mm wide trace" is not the board
_WIDTH_PATTERNS = (
    # "max width 60 mm", "maximum board width of 60mm", "board width: 60", "width of the board is 60 mm"
    re.compile(r"(?:max(?:imum)?\.?\s+(?:board\s+)?width|board\s+(?:max(?:imum)?\s+)?width|"
               r"width\s+of\s+(?:the\s+)?board|board\s+max)\D{0,20}?" + _W, re.I),
    # "board 60 mm wide", "pcb no more than 60 mm wide"
    re.compile(r"(?:board|pcb)(?:(?!trace|track|via|pad|hole|slot|wire)\D){0,25}?" + _W + r"\s*(?:wide|width)\b" + _NOT_FEATURE, re.I),
    # "no wider than 60 mm", "at most 60 mm wide", "under 60mm wide"
    re.compile(r"(?:no\s+wider\s+than|not\s+wider\s+than|narrower\s+than|at\s+most|up\s+to|under|within)\s*"
               + _W + r"\s*(?:wide|width)\b" + _NOT_FEATURE, re.I),
)


def board_width_mm(text: str) -> float | None:
    """The board width limit the user asked for, in mm, or None. The latest mention wins, so a later
    message can change an earlier one. A bare number counts as mm. Values outside 20-200 mm are
    clamped, because that is the range the placer and router work in."""
    best: tuple[int, float] | None = None
    for pat in _WIDTH_PATTERNS:
        for m in pat.finditer(text or ""):
            value = float(m.group(1)) * (10.0 if (m.group(2) or "").lower() == "cm" else 1.0)
            if best is None or m.start() >= best[0]:
                best = (m.start(), value)
    if best is None:
        return None
    lo, hi = BOARD_WIDTH_RANGE
    return min(max(best[1], lo), hi)

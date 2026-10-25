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

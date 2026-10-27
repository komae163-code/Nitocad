"""
Dimensional tolerances: ISO 286 limits & fits for diametral mates, ISO 2768-m
general tolerances for linear dimensions, and worst-case / RSS combination.

DATA PROVENANCE: the IT-grade and fundamental-deviation tables below are the
ISO 286-1 values for nominal sizes up to 250 mm, transcribed here by hand.
Cross-check them against your copy of the standard before relying on a
number for a drawing. Only the classes used by the mating rules are provided
(holes H7, H8; shafts g6, h6, h7); other classes raise KeyError instead of
guessing.
"""
from __future__ import annotations

import math

# nominal size ranges (mm, lower exclusive, upper inclusive) -> values in micrometres
_RANGES = [(0, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50), (50, 80), (80, 120), (120, 180), (180, 250)]
_IT = {
    6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29],
    7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46],
    8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72],
}
# upper fundamental deviation of shaft position g (micrometres, negative)
_G_ES = [-2, -4, -5, -6, -7, -9, -10, -12, -14, -15]

MAX_SIZE_MM = 250.0


def _row(size: float) -> int:
    if not (0 < size <= MAX_SIZE_MM):
        raise ValueError(f"ISO 286 table covers 0 < size <= {MAX_SIZE_MM:g} mm (got {size:g})")
    for i, (lo, hi) in enumerate(_RANGES):
        if lo < size <= hi:
            return i
    raise ValueError(size)


def it_grade_um(size_mm: float, grade: int) -> float:
    return float(_IT[grade][_row(size_mm)])


def hole_limits(nominal_mm: float, cls: str) -> tuple[float, float]:
    """(min, max) hole diameter in mm for class H7 / H8."""
    grade = {"H7": 7, "H8": 8}[cls]
    return nominal_mm, nominal_mm + it_grade_um(nominal_mm, grade) / 1000.0


def shaft_limits(nominal_mm: float, cls: str) -> tuple[float, float]:
    """(min, max) shaft diameter in mm for class g6 / h6 / h7."""
    grade, kind = {"g6": (6, "g"), "h6": (6, "h"), "h7": (7, "h")}[cls]
    it = it_grade_um(nominal_mm, grade)
    es = _G_ES[_row(nominal_mm)] if kind == "g" else 0
    return nominal_mm + (es - it) / 1000.0, nominal_mm + es / 1000.0


def fit_clearance(hole_nominal: float, hole_cls: str, shaft_nominal: float, shaft_cls: str) -> tuple[float, float]:
    """Diametral clearance range (min, max) in mm; negative = interference."""
    h_lo, h_hi = hole_limits(hole_nominal, hole_cls)
    s_lo, s_hi = shaft_limits(shaft_nominal, shaft_cls)
    return h_lo - s_hi, h_hi - s_lo


# ISO 2768-m general linear tolerances (+/- mm) by nominal size range
_2768_M = [(0.5, 3, 0.1), (3, 6, 0.1), (6, 30, 0.2), (30, 120, 0.3), (120, 400, 0.5), (400, 1000, 0.8), (1000, 2000, 1.2)]


def general_tolerance_mm(size_mm: float) -> float:
    """ISO 2768-m +/- tolerance for a linear dimension."""
    s = abs(size_mm)
    for lo, hi, tol in _2768_M:
        if s <= hi:
            return tol
    return _2768_M[-1][2]


def worst_case(contributions: list[float]) -> float:
    return sum(abs(c) for c in contributions)


def rss(contributions: list[float]) -> float:
    return math.sqrt(sum(c * c for c in contributions))

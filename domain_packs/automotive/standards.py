"""
Reference data for automotive wheel/hub interfaces.

These are common published bolt-circle patterns (stud count x pitch-circle
diameter in mm). Used only to (a) recognise "5x114.3" in a description and
(b) sanity-check the pattern. A recognised pattern is NOT a claim that a
given vehicle uses it - fitment is the user's responsibility.
"""
from __future__ import annotations

import re

COMMON_BOLT_PATTERNS: dict[str, tuple[int, float]] = {
    "3x112":    (3, 112.0),
    "4x98":     (4, 98.0),
    "4x100":    (4, 100.0),
    "4x108":    (4, 108.0),
    "4x114.3":  (4, 114.3),
    "5x100":    (5, 100.0),
    "5x108":    (5, 108.0),
    "5x110":    (5, 110.0),
    "5x112":    (5, 112.0),
    "5x114.3":  (5, 114.3),
    "5x120":    (5, 120.0),
    "5x127":    (5, 127.0),
    "5x130":    (5, 130.0),
    "5x139.7":  (5, 139.7),
    "5x150":    (5, 150.0),
    "6x114.3":  (6, 114.3),
    "6x127":    (6, 127.0),
    "6x135":    (6, 135.0),
    "6x139.7":  (6, 139.7),
    "8x165.1":  (8, 165.1),
    "8x170":    (8, 170.0),
    "8x180":    (8, 180.0),
}

_PATTERN_RE = re.compile(r"\b(\d{1,2})\s*[x×*]\s*(\d{2,3}(?:\.\d+)?)\b", re.IGNORECASE)


def parse_bolt_pattern(text: str) -> tuple[int, float] | None:
    """Find a '5x114.3'-style stud pattern. Accepts any count 3..10 with a
    plausible PCD 80..250 mm (not only the table above) so uncommon patterns
    still work; returns (count, pcd_mm) or None."""
    for m in _PATTERN_RE.finditer(text):
        count, pcd = int(m.group(1)), float(m.group(2))
        if 3 <= count <= 10 and 80.0 <= pcd <= 250.0:
            return count, pcd
    return None

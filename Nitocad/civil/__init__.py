"""
NitoCAD civil engineering calculations.

Terrain from survey points, horizontal and vertical alignments, earthworks and
material quantities, drainage hydraulics, retaining wall loads and stability,
and a configurable criteria check.

Everything here is a screening-level calculation built on textbook methods
(triangulated surfaces, average end area volumes, Manning, the rational method,
Rankine earth pressure). It does not replace a design by a licensed engineer.
No result is a statement of compliance with a design code: the criteria sets are
editable inputs, and the built-in one is a generic starting point, not a
standard. SI units throughout (metres, kN, kPa, m3/s) unless a name says mm.
"""

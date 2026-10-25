#!/usr/bin/env python3
"""
Writes tests/eval/geometry_cases.json: the prompts eval_geometry.py builds and scores.

    python tests/eval/make_geometry_cases.py            rewrite the JSON
    python tests/eval/make_geometry_cases.py --check    exit 1 if the JSON is not what this script writes

Every expected number is computed here from the dimensions in the prompt, by the formula for the
solid a person would mean by it, so nothing is typed in by hand and a wrong number can be traced to
one line. The JSON is committed so a release is always scored against the same cases, and a test
(tests/test_geometry_eval.py) fails if the two ever differ.

What a case expects, all measured on the finished solid:
  size_mm      the three bounding-box edges, smallest first (which axis a part lies along is not the
               person's concern)
  volume_mm3   from the formula
  holes        the number of round holes (through bores and drilled holes), when the prompt states them.
               null means the case does not score holes.

What a case means by the prompt, and not by the template's defaults: "no holes" means no holes, and "6
holes" means six. A part built with holes nobody asked for is a miss, which is the point of scoring
what a person gets and not what the parser returned.

Left out on purpose: anything with a fillet, chamfer or keyway in the prompt, and the parts whose geometry
is a stand-in for the real thing (hinge, cam, T-bracket, channel bracket, pipe fittings).

Gears, pulleys, sprockets, L-brackets and angles were left out until their templates were fixed (the gear
had no teeth, the pulley had its hub cut out, the sprocket had half its tooth spaces, the L-bracket was a T).
They are in now. The gear and the sprocket cases have a looser or a design-based volume:
  gear      volume from the area gear theory gives (teeth fill about half the ring between the root and the tip
            circles), within 3%. It catches a gear with missing teeth, no teeth, or a disc.
  sprocket  volume from the template's own simplified tooth space (a circle of a third of the pitch, centred on
            the pitch circle), so it checks that every tooth space is there. It does not check the sprocket
            against a real chain. Size and holes are not scored: the tooth spaces are round cuts, and the
            hole counter would count each one.

These prompts are held out: none is a template picker prompt and none is a case in the parser eval
(tests/eval/parser_cases.json). Tune the parser on that set; keep this one for scoring.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

OUT = Path(__file__).with_name("geometry_cases.json")
TOLERANCES = {"size_rel": 0.005, "size_abs_mm": 0.1, "volume_rel": 0.015}

PI = math.pi
_cases: list[dict] = []


def _r(x: float) -> float:
    x = round(x, 3)
    return int(x) if float(x).is_integer() else x


def g(x: float) -> str:
    """A number as a person would type it."""
    return f"{x:g}"


def add(cid: str, prompt: str, part_type: str, size, volume: float, holes: int | None, tolerances: dict | None = None) -> None:
    case = {
        "id": cid,
        "prompt": prompt,
        "part_type": part_type,
        "expect": {"size_mm": sorted(_r(s) for s in size) if size else None, "volume_mm3": _r(volume), "holes": holes},
    }
    if tolerances:
        case["tolerances"] = tolerances
    _cases.append(case)


def pick(options: list[str], i: int) -> str:
    return options[i % len(options)]


def cyl(d: float, h: float) -> float:
    return PI / 4 * d * d * h


# ------------------------------------------------------------------------------ plates
def plates() -> None:
    plain = [(80, 50, 4), (120, 80, 5), (150, 100, 6), (200, 120, 8), (90, 60, 3), (250, 150, 10), (100, 100, 5), (160, 90, 4),
             (300, 200, 12), (75, 75, 3), (140, 70, 6), (180, 110, 5), (220, 140, 8), (110, 85, 4), (130, 130, 6), (260, 180, 10),
             (95, 65, 3), (170, 95, 5), (240, 160, 12), (85, 85, 4)]
    forms = ["{L} x {W} x {T} mm flat plate, no holes",
             "solid plate {L} mm long, {W} mm wide and {T} mm thick, without any holes",
             "plain rectangular plate {L} by {W} by {T}, no holes",
             "{T} mm thick plate, {L} long by {W} wide, no holes in it"]
    for i, (L, W, T) in enumerate(plain):
        add(f"plate-plain-{i + 1:02d}", pick(forms, i).format(L=g(L), W=g(W), T=g(T)), "flat_plate", (L, W, T), L * W * T, 0)

    grid = [(120, 80, 5, 3, 2, 5), (150, 100, 6, 4, 2, 6), (200, 120, 8, 4, 3, 6), (100, 70, 4, 2, 2, 4), (160, 90, 5, 3, 3, 5),
            (180, 100, 6, 5, 2, 5), (140, 100, 5, 2, 3, 6), (220, 130, 8, 5, 3, 6), (130, 80, 4, 3, 2, 4.5), (170, 110, 6, 4, 3, 5),
            (250, 150, 10, 6, 3, 8), (110, 75, 5, 2, 2, 5)]
    forms = ["plate {L} x {W} x {T} mm with {n} holes of {d} mm in a {nx} by {ny} grid",
             "{L} by {W} by {T} mm plate, {nx}x{ny} hole pattern, {d} mm holes"]
    for i, (L, W, T, nx, ny, d) in enumerate(grid):
        assert (L - 20) / (nx - 1) > d and (W - 20) / (ny - 1) > d, ("holes touch", L, W, nx, ny, d)
        n = nx * ny
        add(f"plate-grid-{i + 1:02d}", pick(forms, i).format(L=g(L), W=g(W), T=g(T), n=n, nx=nx, ny=ny, d=g(d)),
            "flat_plate", (L, W, T), L * W * T - n * cyl(d, T), n)

    circ = [(100, 6, 6, 5), (120, 8, 8, 6), (80, 5, 4, 5), (150, 10, 8, 8), (90, 4, 5, 4), (100, 5, 3, 6), (130, 6, 6, 6), (110, 8, 4, 8)]
    for i, (S, T, n, d) in enumerate(circ):
        r = (S - 20) / 2
        assert 2 * r * math.sin(PI / n) > d and r > d / 2, ("holes touch", S, n, d)
        add(f"plate-circle-{i + 1:02d}",
            f"square plate {g(S)} x {g(S)} x {g(T)} mm with {n} holes of {g(d)} mm on a bolt circle",
            "flat_plate", (S, S, T), S * S * T - n * cyl(d, T), n)


# ------------------------------------------------------------------------------ round stock
def shafts() -> None:
    dims = [(6, 40), (8, 60), (10, 80), (12, 100), (15, 150), (16, 120), (20, 200), (25, 250), (30, 300), (5, 30), (10, 200), (12, 50),
            (14, 90), (18, 160), (22, 180), (8, 100), (24, 220), (32, 320), (40, 400), (9, 70), (11, 85), (13, 110), (35, 350), (45, 450), (50, 500)]
    forms = ["shaft {d} mm diameter, {L} mm long", "a {L} mm long round rod, {d} mm in diameter", "steel axle {d}mm dia x {L}mm"]
    for i, (d, L) in enumerate(dims):
        add(f"shaft-{i + 1:02d}", pick(forms, i).format(d=g(d), L=g(L)), "shaft", (d, d, L), cyl(d, L), 0)


def tubes() -> None:
    dims = [(20, 2, 100), (25, 2.5, 150), (30, 3, 200), (40, 3, 120), (50, 4, 250), (16, 1.5, 80), (32, 2, 180), (60, 5, 300), (22, 2, 90),
            (35, 3.5, 140), (45, 4, 160), (28, 2, 110), (48, 3, 200), (55, 5, 220), (38, 3, 130), (70, 6, 350), (24, 2.5, 100), (42, 4, 170),
            (36, 3, 150), (65, 5, 260)]
    forms = ["round tube {D}mm OD, {w}mm wall, {L}mm long", "hollow pipe, outer diameter {D}, wall thickness {w}, length {L}", "round tube {D} OD x {w} wall x {L}"]
    for i, (D, w, L) in enumerate(dims):
        assert D - 2 * w > 0
        add(f"tube-round-{i + 1:02d}", pick(forms, i).format(D=g(D), w=g(w), L=g(L)), "tube", (D, D, L), cyl(D, L) - cyl(D - 2 * w, L), 1)
    sq = [(25, 2, 200), (30, 2.5, 150), (40, 3, 250), (20, 1.5, 100), (50, 4, 300), (35, 3, 180)]
    for i, (s, w, L) in enumerate(sq):
        add(f"tube-square-{i + 1:02d}", f"square tube {g(s)} mm, {g(w)} mm wall, {g(L)} mm long", "tube", (s, s, L), (s * s - (s - 2 * w) ** 2) * L, None)


def rings() -> None:
    spacers = [(10, 5, 8), (12, 6, 10), (16, 8, 12), (20, 10, 15), (25, 12, 30), (8, 4, 6), (14, 7, 9), (18, 9, 20), (22, 11, 25), (30, 16, 40),
               (15, 8, 10), (24, 12, 18), (32, 20, 22), (40, 25, 50), (13, 6.5, 8), (26, 13, 16), (36, 20, 28), (11, 5.5, 7), (28, 14, 35), (45, 30, 24)]
    forms = ["spacer {D} mm outside diameter, {d} mm bore, {L} mm long", "bushing OD {D} ID {d} length {L}"]
    for i, (D, d, L) in enumerate(spacers):
        add(f"spacer-{i + 1:02d}", pick(forms, i).format(D=g(D), d=g(d), L=g(L)), "spacer", (D, D, L), cyl(D, L) - cyl(d, L), 1)

    washers = [(10, 5.3, 1), (12, 6.4, 1.6), (16, 8.4, 1.6), (20, 10.5, 2), (24, 13, 2.5), (30, 17, 3), (37, 21, 3), (8, 4.3, 0.8), (18, 9, 2),
               (22, 12, 2), (28, 15, 2.5), (34, 19, 3), (44, 25, 4), (15, 8, 1.5), (26, 14, 2.5)]
    forms = ["flat washer {D} mm OD, {d} mm ID, {T} mm thick", "washer with a {d} mm hole, {D} mm outer diameter, thickness {T} mm"]
    for i, (D, d, T) in enumerate(washers):
        add(f"washer-{i + 1:02d}", pick(forms, i).format(D=g(D), d=g(d), T=g(T)), "washer", (D, D, T), cyl(D, T) - cyl(d, T), 1)

    bearings = [(8, 22, 7), (10, 26, 8), (12, 28, 8), (15, 35, 11), (17, 40, 12), (20, 47, 14), (25, 52, 15), (30, 62, 16), (35, 72, 17),
                (40, 80, 18), (6, 19, 6), (5, 16, 5), (9, 24, 7), (22, 50, 14), (45, 85, 19)]
    forms = ["bearing with a {d} mm bore, {D} mm outside diameter and {W} mm width", "ball bearing {d} x {D} x {W}"]
    for i, (d, D, W) in enumerate(bearings):
        add(f"bearing-{i + 1:02d}", pick(forms, i).format(D=g(D), d=g(d), W=g(W)), "bearing", (D, D, W), cyl(D, W) - cyl(d, W), 1)


def spheres() -> None:
    for i, d in enumerate([10, 20, 25, 40, 50, 60, 75, 80, 120, 150]):
        add(f"sphere-{i + 1:02d}", pick(["sphere {d} mm in diameter", "a solid ball, {d}mm across"], i).format(d=g(d)), "sphere", (d, d, d), PI / 6 * d ** 3, 0)


def flanges() -> None:
    dims = [(100, 50, 10, 4, 5, 75), (120, 60, 12, 6, 9, 90), (150, 80, 15, 8, 11, 120), (80, 40, 8, 4, 6, 60), (90, 45, 10, 6, 6, 68),
            (110, 55, 10, 4, 8, 82), (130, 65, 12, 6, 9, 98), (140, 70, 14, 8, 10, 105), (160, 90, 16, 8, 12, 125), (70, 30, 8, 4, 5, 52),
            (100, 60, 8, 6, 5, 80), (125, 75, 12, 6, 8, 100), (180, 100, 18, 8, 14, 140), (200, 120, 20, 12, 14, 160), (95, 50, 9, 4, 7, 72),
            (115, 60, 10, 5, 7, 88), (135, 80, 12, 8, 9, 108), (105, 55, 10, 6, 6, 80), (145, 85, 14, 6, 10, 115), (170, 110, 16, 8, 11, 140)]
    forms = ["pipe flange {D} mm outer diameter, {d} mm bore, {T} mm thick, {n} bolt holes of {h} mm on a {bc} mm bolt circle",
             "flange OD {D}, bore {d}, thickness {T}, {n} holes {h} mm on a {bc} mm circle"]
    for i, (D, d, T, n, h, bc) in enumerate(dims):
        r = bc / 2
        assert r - h / 2 > d / 2 and r + h / 2 < D / 2 and 2 * r * math.sin(PI / n) > h, ("flange holes do not fit", D, d, n, h, bc)
        add(f"flange-{i + 1:02d}", pick(forms, i).format(D=g(D), d=g(d), T=g(T), n=n, h=g(h), bc=g(bc)),
            "flange", (D, D, T), cyl(D, T) - cyl(d, T) - n * cyl(h, T), 1 + n)


def boxes() -> None:
    solid = [(60, 40, 30), (80, 80, 80), (120, 90, 60), (150, 100, 50), (200, 120, 80), (90, 60, 45), (250, 160, 100), (70, 70, 35),
             (130, 95, 65), (180, 110, 70), (100, 100, 20), (160, 120, 90)]
    for i, (L, W, H) in enumerate(solid):
        if L == W == H:
            prompt = f"cube {g(L)} mm"
        else:
            prompt = pick(["solid block {L} x {W} x {H} mm", "box {L} mm long, {W} mm wide, {H} mm high"], i).format(L=g(L), W=g(W), H=g(H))
        add(f"box-solid-{i + 1:02d}", prompt, "simple_box", (L, W, H), L * W * H, 0)
    shells = [(100, 80, 50, 3), (120, 90, 60, 2.5), (150, 100, 70, 4), (80, 60, 40, 2), (200, 140, 80, 5), (110, 70, 45, 3), (90, 90, 60, 3), (160, 110, 55, 4)]
    for i, (L, W, H, w) in enumerate(shells):
        add(f"box-open-{i + 1:02d}", f"open top box {g(L)} x {g(W)} x {g(H)} mm with {g(w)} mm walls", "simple_box", (L, W, H),
            L * W * H - (L - 2 * w) * (W - 2 * w) * (H - w), None)


# ------------------------------------------------------------------------------ transmission and brackets
def circle_overlap_area(d: float, big: float, small: float) -> float:
    """Area shared by a disc of radius `big` at the origin and a disc of radius `small` centred d from it."""
    if d + small <= big:
        return PI * small * small
    if d >= big + small:
        return 0.0
    a = small * small * math.acos((d * d + small * small - big * big) / (2 * d * small))
    b = big * big * math.acos((d * d + big * big - small * small) / (2 * d * big))
    c = 0.5 * math.sqrt((-d + small + big) * (d + small - big) * (d - small + big) * (d + small + big))
    return a + b - c


def gears() -> None:
    dims = [(2, 20, 10, 8), (1, 24, 8, 6), (1.5, 32, 12, 10), (2, 28, 10, 10), (3, 24, 12, 12), (2.5, 36, 15, 12), (1, 40, 6, 8),
            (2, 48, 10, 12), (4, 20, 20, 16), (1.25, 60, 8, 10), (3, 32, 15, 14), (2, 40, 12, 10)]
    forms = ["spur gear, module {m}, {z} teeth, {T} mm thick, {b} mm bore", "gear with {z} teeth, module {m}, {T}mm wide, bore {b}mm"]
    for i, (m, z, T, b) in enumerate(dims):
        assert z % 4 == 0, "a multiple of four puts a tooth on every axis, so the bounding box is the tip circle"
        rp = m * z / 2
        rf, ra = rp - 1.25 * m, rp + m
        assert b < 2 * rf - 2, ("bore too big for the roots", m, z, b)
        area = PI * (rf ** 2 + 0.5 * (ra ** 2 - rf ** 2))          # teeth fill about half the ring between the root and tip circles
        add(f"gear-{i + 1:02d}", pick(forms, i).format(m=g(m), z=z, T=g(T), b=g(b)), "gear", (T, 2 * ra, 2 * ra),
            (area - PI / 4 * b * b) * T, 1, {"volume_rel": 0.03})


def pulleys() -> None:
    dims = [(40, 10, 5, 15), (50, 12, 8, 18), (60, 14, 10, 20), (80, 20, 12, 26), (32, 8, 6, 14), (45, 10, 8, 16), (100, 25, 16, 32), (70, 16, 10, 22)]
    forms = ["pulley {D} mm outer diameter, {w} mm belt width, {b} mm bore, {T} mm thick", "belt pulley OD {D}, bore {b}, belt {w} mm, {T} mm wide overall"]
    for i, (D, w, b, T) in enumerate(dims):
        R, flange, depth = D / 2, 2.0, 3.0                          # the template's flange height and default groove depth
        assert w <= T - 2 * flange - 1 and R - depth - b / 2 >= 1, ("pulley does not fit", D, w, b, T)
        volume = PI * R * R * T + 2 * flange * PI * ((R + flange) ** 2 - R * R) - PI * (R * R - (R - depth) ** 2) * w - PI / 4 * b * b * T
        add(f"pulley-{i + 1:02d}", pick(forms, i).format(D=g(D), w=g(w), b=g(b), T=g(T)), "pulley", (T, D + 2 * flange, D + 2 * flange), volume, 1)


def sprockets() -> None:
    dims = [(18, 12.7, 8, 8), (12, 12.7, 8, 8), (24, 12.7, 8, 10), (30, 12.7, 10, 12), (16, 8, 6, 6), (20, 15.875, 10, 10), (25, 12.7, 8, 10), (36, 12.7, 8, 12)]
    forms = ["sprocket {z} teeth for a {p} mm pitch chain, {T} mm thick, {b} mm bore", "chain sprocket, {z} teeth, {p} mm pitch, {b} mm bore, {T} mm thick"]
    for i, (z, p, T, b) in enumerate(dims):
        rp = p / (2 * math.sin(PI / z))
        r_out, r_space = rp + p / 4, p / 3                          # the template's tooth space: a circle of pitch / 3 on the pitch circle
        assert rp - r_space > b / 2 + 1, ("bore reaches the tooth spaces", z, p, b)
        area = PI * r_out ** 2 - z * circle_overlap_area(rp, r_out, r_space) - PI / 4 * b * b
        add(f"sprocket-{i + 1:02d}", pick(forms, i).format(z=z, p=g(p), T=g(T), b=g(b)), "sprocket", None, area * T, None)


def l_brackets() -> None:
    dims = [(50, 60, 40, 3, 2, 2.5), (40, 40, 40, 4, 3, 3), (70, 45, 45, 4, 2, 5), (60, 80, 50, 3, 2, 6), (30, 30, 30, 2, 1, 1.5), (80, 50, 60, 4, 3, 3),
            (100, 60, 50, 4, 2, 5), (45, 70, 40, 3, 2, 4), (55, 55, 45, 4, 2, 3), (90, 80, 70, 4, 3, 6), (35, 50, 35, 2.5, 2, 3), (65, 90, 55, 4, 2, 4)]
    forms = ["L-bracket {w} mm wide, {h} mm tall, {d} mm deep, {t} mm thick, with {n} holes of {hd} mm in each leg",
             "L bracket width {w}, height {h}, depth {d}, thickness {t}, {n} holes in each leg, {hd} mm holes"]
    for i, (w, h, d, t, n, hd) in enumerate(dims):
        # Holes can be wider than the metal. They must fit the leg: 2 mm of metal each side across the width, and clear of the bend.
        assert t <= min(w, h, d) / 3 and t < 4.01 and hd <= min(w - 4, 2 * (10 - t - 2)), ("bracket dimensions the validator would change", w, h, d, t, hd)
        assert (d - t) > 20 or n == 1, ("holes would not fit on the horizontal leg", d, t, n)
        volume = w * t * (h + d - t) - 2 * n * cyl(hd, t)
        add(f"lbracket-{i + 1:02d}", pick(forms, i).format(w=g(w), h=g(h), d=g(d), t=g(t), n=n, hd=g(hd)), "l_bracket", (w, h, d), volume, 2 * n)


def angles() -> None:
    dims = [(40, 60, 5, 100), (50, 50, 5, 120), (30, 30, 3, 80), (60, 40, 6, 200), (25, 25, 3, 60), (75, 50, 6, 150), (100, 100, 8, 250), (20, 40, 2, 50)]
    forms = ["angle iron with legs {a} mm and {b} mm, {t} mm thick, {L} mm long", "steel angle section {a} x {b} x {t} mm, length {L} mm"]
    for i, (a, b, t, L) in enumerate(dims):
        add(f"angle-{i + 1:02d}", pick(forms, i).format(a=g(a), b=g(b), t=g(t), L=g(L)), "angle", (a, b, L), (a + b - t) * t * L, 0)


def units() -> None:
    inch, cm = 25.4, 10.0
    add("units-01", "shaft 0.5 inch diameter, 4 inches long", "shaft", (0.5 * inch, 0.5 * inch, 4 * inch), cyl(0.5 * inch, 4 * inch), 0)
    add("units-02", "plate 6 in x 4 in x 0.25 in, no holes", "flat_plate", (6 * inch, 4 * inch, 0.25 * inch), 6 * 4 * 0.25 * inch ** 3, 0)
    add("units-03", "round tube 1 inch OD, 0.1 inch wall, 8 inches long", "tube", (inch, inch, 8 * inch),
        cyl(inch, 8 * inch) - cyl(inch - 0.2 * inch, 8 * inch), 1)
    add("units-04", "cylinder 3 cm diameter, 12 cm long", "shaft", (3 * cm, 3 * cm, 12 * cm), cyl(3 * cm, 12 * cm), 0)
    add("units-05", "sphere 2 inches in diameter", "sphere", (2 * inch, 2 * inch, 2 * inch), PI / 6 * (2 * inch) ** 3, 0)
    add("units-06", "spacer 1 inch outside diameter, 0.5 inch bore, 0.75 inch long", "spacer", (inch, inch, 0.75 * inch),
        cyl(inch, 0.75 * inch) - cyl(0.5 * inch, 0.75 * inch), 1)
    add("units-07", "box 10 cm x 6 cm x 4 cm", "simple_box", (10 * cm, 6 * cm, 4 * cm), 10 * 6 * 4 * cm ** 3, 0)
    add("units-08", "washer 1 inch OD, 0.4 inch ID, 0.08 inch thick", "washer", (inch, inch, 0.08 * inch), cyl(inch, 0.08 * inch) - cyl(0.4 * inch, 0.08 * inch), 1)
    add("units-09", "shaft 2.5 cm diameter and 20 cm long", "shaft", (2.5 * cm, 2.5 * cm, 20 * cm), cyl(2.5 * cm, 20 * cm), 0)
    add("units-10", "solid block 5 in x 3 in x 2 in", "simple_box", (5 * inch, 3 * inch, 2 * inch), 5 * 3 * 2 * inch ** 3, 0)


def build_cases() -> dict:
    _cases.clear()
    for fn in (plates, shafts, tubes, rings, spheres, flanges, boxes, gears, pulleys, sprockets, l_brackets, angles, units):
        fn()
    ids = [c["id"] for c in _cases]
    assert len(ids) == len(set(ids)), "duplicate case id"
    return {"version": 1, "tolerances": TOLERANCES, "cases": list(_cases)}


def render() -> str:
    return json.dumps(build_cases(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.name} is not what make_geometry_cases.py writes. Run it again and commit the result.")
            return 1
        print(f"{OUT.name} is up to date ({len(_cases)} cases).")
        return 0
    OUT.write_text(text)
    print(f"wrote {len(_cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

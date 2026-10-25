"""Aerospace vehicle templates: outer-mold-line (OML) concept geometry.

fuselage, tail_surface, turbofan_engine and jet_aircraft model external shape
for layout, packaging and visualisation. They are solids of the OUTER ENVELOPE:
they carry no internal structure, systems or real engine internals, so mass is
never computed from their volume. See aerospace_pack.GEOMETRY_ONLY_NOTICE.

Axes: +X aft (nose at x = 0), +Y right wing, +Z up. Dimensions in mm.
"""
import math
import cadquery as cq

from cad_templates.aerospace import generate_airfoil_wing

_AXIS = ((0, 0, 0), (1, 0, 0))


def _revolve_x(points):
    """Revolve a closed (x, r) profile 360 degrees about the X axis."""
    return cq.Workplane("XY").polyline(points).close().revolve(360, *_AXIS)


def _fuse(parts):
    """Union solids; fall back to a compound if a boolean fails (never raise)."""
    base = parts[0]
    for p in parts[1:]:
        try:
            base = base.union(p)
        except Exception:
            comp = cq.Compound.makeCompound([x.val() for x in parts])
            return cq.Workplane("XY").newObject([comp])
    return base


def generate_fuselage(params: dict) -> cq.Workplane:
    """Params: length_mm, diameter_mm, nose_ratio (nose length / length),
    tail_ratio (tail cone length / length), tail_end_diameter_mm."""
    length = float(params.get("length_mm", 30000.0))
    r = float(params.get("diameter_mm", 3500.0)) / 2
    nl = length * float(params.get("nose_ratio", 0.12))
    tl = length * float(params.get("tail_ratio", 0.25))
    r_end = float(params.get("tail_end_diameter_mm", 500.0)) / 2
    if nl + tl >= length or r_end >= r:
        raise ValueError("nose_ratio + tail_ratio must be < 1 and tail_end_diameter_mm < diameter_mm")
    n = 16
    top = [(0.0, 0.0)]
    for k in range(1, n + 1):                         # elliptical nose
        x = nl * k / n
        top.append((x, r * math.sqrt(max(0.0, 1 - ((nl - x) / nl) ** 2))))
    top.append((length - tl, r))
    top.append((length, r_end))
    return _revolve_x(top + [(length, 0.0)])


def generate_tail_surface(params: dict) -> cq.Workplane:
    """Horizontal or vertical stabiliser. Params: orientation ('horizontal' |
    'vertical'), naca, root_chord_mm, tip_chord_mm, span_mm (semi-span for
    horizontal, height for vertical), sweep_deg, both_halves (horizontal)."""
    vertical = str(params.get("orientation", "horizontal")).lower() == "vertical"
    wing = generate_airfoil_wing({
        "naca": params.get("naca", "0012"),
        "root_chord_mm": params.get("root_chord_mm", 4000.0),
        "tip_chord_mm": params.get("tip_chord_mm", 2000.0),
        "span_mm": params.get("span_mm", 6000.0),
        "sweep_deg": params.get("sweep_deg", 30.0),
        "both_halves": bool(params.get("both_halves", not vertical)) and not vertical,
    })
    return wing.rotate((0, 0, 0), (1, 0, 0), -90) if vertical else wing   # -Y span -> +Z


def generate_turbofan_engine(params: dict) -> cq.Workplane:
    """Params: fan_diameter_mm, length_mm, nacelle_thickness_mm, hub_ratio,
    core_ratio (core diameter / fan diameter), fan_blades (0 = omit blades),
    compressor_stages / turbine_stages (0 = envelope only; > 0 builds a hollow
    core with rotor and stator rows), blades_per_row."""
    fd = float(params.get("fan_diameter_mm", 2000.0))
    length = float(params.get("length_mm", fd * 1.9))
    nt = float(params.get("nacelle_thickness_mm", fd * 0.04))
    hub_r = fd / 2 * float(params.get("hub_ratio", 0.3))
    core_r = fd / 2 * float(params.get("core_ratio", 0.45))
    n_blades = int(params.get("fan_blades", 18))
    fr = fd / 2
    if not 0 < hub_r < core_r < fr:
        raise ValueError("require 0 < hub_ratio < core_ratio < 1")

    # nacelle: ring section between an outer cowl and the inner flow-path wall
    r_out_max = fr + nt + fd * 0.06
    nacelle = _revolve_x([
        (0.0, fr), (length * 0.05, fr + nt + fd * 0.03), (length * 0.25, r_out_max),
        (length * 0.70, r_out_max * 0.85), (length * 0.88, r_out_max * 0.62),
        (length * 0.88, r_out_max * 0.62 - nt), (length * 0.70, r_out_max * 0.85 - nt),
        (length * 0.25, fr + fd * 0.01), (length * 0.05, fr + 0.0),
    ])
    # core: spinner, gas generator casing, tail cone/plug (one revolved solid)
    core = _revolve_x([
        (length * 0.00, 0.0), (length * 0.06, hub_r * 0.8), (length * 0.14, hub_r),
        (length * 0.32, core_r), (length * 0.78, core_r * 0.95),
        (length * 0.95, core_r * 0.55), (length * 1.08, 0.0),
    ])
    c_stages = int(params.get("compressor_stages", 0))
    t_stages = int(params.get("turbine_stages", 0))
    if c_stages + t_stages > 0:
        return _core_with_stages(params, nacelle, fd, length, hub_r, core_r, n_blades, c_stages, t_stages)
    parts = [nacelle, core]
    if n_blades > 0:
        span = fr - hub_r
        chord = fd * 0.07
        blade = (cq.Workplane("XY").box(chord, span, fd * 0.008)
                 .translate((length * 0.16, hub_r + span / 2, 0)))
        parts += [blade.rotate((0, 0, 0), (1, 0, 0), 360.0 * i / n_blades) for i in range(n_blades)]
    return _fuse(parts)


def _row(x, r_in, r_out, chord, thick, n):
    """One blade row: n thin radial blades between r_in and r_out at axial position x."""
    blade = cq.Workplane("XY").box(chord, r_out - r_in, thick).translate((x, (r_in + r_out) / 2, 0))
    return [blade.rotate((0, 0, 0), (1, 0, 0), 360.0 * k / n).val() for k in range(n)]


def _core_with_stages(params, nacelle, fd, length, hub_r, core_r, n_fan, c_stages, t_stages):
    """Hollow gas-generator casing, hub drum and alternating rotor/stator rows.

    Preliminary visual layout: stage counts and row spacing only (use the
    engine_meanline result to choose counts). Blade shapes are flat radial plates.
    Parts are returned as an unfused compound for speed and robustness."""
    n = max(4, min(int(params.get("blades_per_row", 30)), 80))
    wall = fd * 0.02
    cin = core_r * 0.9
    x0, xc, xt0, x1 = length * 0.14, length * 0.50, length * 0.52, length * 0.80
    hub_pts = [(0.0, 0.0), (length * 0.06, hub_r * 0.8), (x0, cin * 0.45)]
    n_rows_c, n_rows_t = 2 * c_stages, 2 * t_stages

    def hub_at(x):
        if x <= xc:
            return cin * (0.45 + 0.35 * (x - x0) / max(1e-9, xc - x0))
        return cin * (0.75 - 0.20 * (x - xt0) / max(1e-9, x1 - xt0))

    hub_pts += [(xc, cin * 0.80), (xt0, cin * 0.75), (x1, cin * 0.55), (length * 0.95, cin * 0.4), (length * 1.08, 0.0)]
    drum = _revolve_x(hub_pts)
    shell = _revolve_x([(x0, cin), (x1, cin), (x1, cin + wall), (x0, cin + wall)])
    solids = [nacelle.val(), shell.val(), drum.val()]
    fr = fd / 2
    if n_fan > 0:
        solids += _row(length * 0.16, hub_r, fr, fd * 0.07, fd * 0.008, n_fan)
    for rows, a, b in ((n_rows_c, x0, xc), (n_rows_t, xt0, x1)):
        for k in range(rows):
            x = a + (b - a) * (k + 0.5) / rows
            chord = (b - a) / max(1, rows) * 0.55
            solids += _row(x, hub_at(x), cin, chord, max(0.5, fd * 0.004 if k % 2 else fd * 0.006), n)
    return cq.Workplane("XY").newObject([cq.Compound.makeCompound(solids)])


def generate_jet_aircraft(params: dict) -> cq.Workplane:
    """Complete concept airframe. Params (mm unless noted): fuselage_length_mm,
    fuselage_diameter_mm, wing_span_mm (full), wing_root_chord_mm,
    wing_tip_chord_mm, wing_sweep_deg, wing_x_ratio (wing root LE / fuselage
    length), wing_z_ratio (vertical offset / fuselage radius, negative = low
    wing), htail_span_mm (full), htail_root_chord_mm, htail_tip_chord_mm,
    vtail_height_mm, vtail_root_chord_mm, vtail_tip_chord_mm, tail_x_ratio,
    engines (0, 2 or 4), engine_fan_diameter_mm, engine_span_ratio,
    engine_x_offset_mm (engine nose ahead of wing LE), engine_z_offset_mm."""
    L = float(params.get("fuselage_length_mm", 37000.0))
    D = float(params.get("fuselage_diameter_mm", 3950.0))
    fus = generate_fuselage({"length_mm": L, "diameter_mm": D,
                             "nose_ratio": params.get("nose_ratio", 0.10),
                             "tail_ratio": params.get("tail_ratio", 0.25),
                             "tail_end_diameter_mm": D * 0.14})
    span = float(params.get("wing_span_mm", 34000.0))
    cr = float(params.get("wing_root_chord_mm", 6000.0))
    sweep = float(params.get("wing_sweep_deg", 25.0))
    wx = L * float(params.get("wing_x_ratio", 0.38))
    wz = D / 2 * float(params.get("wing_z_ratio", -0.6))
    wing = generate_airfoil_wing({
        "naca": params.get("wing_naca", "2412"), "root_chord_mm": cr,
        "tip_chord_mm": params.get("wing_tip_chord_mm", 1800.0),
        "span_mm": span / 2, "sweep_deg": sweep, "both_halves": True,
    }).translate((wx, 0, wz))
    parts = [fus, wing]

    tx = L * float(params.get("tail_x_ratio", 0.84))
    parts.append(generate_tail_surface({
        "orientation": "horizontal", "root_chord_mm": params.get("htail_root_chord_mm", 3200.0),
        "tip_chord_mm": params.get("htail_tip_chord_mm", 1300.0),
        "span_mm": float(params.get("htail_span_mm", 12500.0)) / 2,
        "sweep_deg": params.get("htail_sweep_deg", 30.0),
    }).translate((tx + L * 0.05, 0, D * 0.18)))
    parts.append(generate_tail_surface({
        "orientation": "vertical", "root_chord_mm": params.get("vtail_root_chord_mm", 5500.0),
        "tip_chord_mm": params.get("vtail_tip_chord_mm", 2200.0),
        "span_mm": float(params.get("vtail_height_mm", 6300.0)),
        "sweep_deg": params.get("vtail_sweep_deg", 35.0),
    }).translate((tx, 0, D * 0.35)))

    n_eng = int(params.get("engines", 2))
    if n_eng:
        fd = float(params.get("engine_fan_diameter_mm", 1800.0))
        ys = {2: [1], 4: [1, 2]}.get(n_eng)
        if ys is None:
            raise ValueError("engines must be 0, 2 or 4")
        semi = span / 2
        eng = generate_turbofan_engine({"fan_diameter_mm": fd, "fan_blades": params.get("engine_fan_blades", 18),
                                        "compressor_stages": params.get("engine_compressor_stages", 0),
                                        "turbine_stages": params.get("engine_turbine_stages", 0)})
        ex = wx - float(params.get("engine_x_offset_mm", 1500.0))
        ez = wz - float(params.get("engine_z_offset_mm", fd * 0.65))
        base_ratio = float(params.get("engine_span_ratio", 0.34))
        for k in ys:
            ratio = base_ratio if k == 1 else min(0.9, base_ratio * 1.9)
            y = semi * ratio
            xk = ex + math.tan(math.radians(sweep)) * (y - D / 2)
            for sgn in (1, -1):
                parts.append(eng.translate((xk, sgn * y, ez)))
    return _fuse(parts)

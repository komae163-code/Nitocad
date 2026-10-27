"""Conceptual airframe metrics from jet_aircraft parameters (stdlib only).

Pure geometry arithmetic plus textbook reference bands. The bands are
guidance, not requirements: leaving one produces a warning, never a failure.
Reference: tail volume coefficients and fineness ratios as commonly tabulated
in conceptual-design texts (e.g. Raymer, Aircraft Design: A Conceptual Approach).
"""
import math

G = 9.80665

DEFAULTS = {
    "fuselage_length_mm": 37000.0, "fuselage_diameter_mm": 3950.0, "wing_span_mm": 34000.0,
    "wing_root_chord_mm": 6000.0, "wing_tip_chord_mm": 1800.0, "wing_sweep_deg": 25.0,
    "wing_x_ratio": 0.38, "htail_span_mm": 12500.0, "htail_root_chord_mm": 3200.0,
    "htail_tip_chord_mm": 1300.0, "htail_sweep_deg": 30.0, "vtail_height_mm": 6300.0,
    "vtail_root_chord_mm": 5500.0, "vtail_tip_chord_mm": 2200.0, "vtail_sweep_deg": 35.0,
    "tail_x_ratio": 0.84,
}

BANDS = {  # name: (low, high)
    "horizontal_tail_volume": (0.4, 1.2),
    "vertical_tail_volume": (0.04, 0.10),
    "fuselage_fineness": (6.0, 14.0),
    "aspect_ratio": (5.0, 12.0),
}


def _g(p, k):
    return float(p.get(k, DEFAULTS[k]))


def _mac(cr, ct):
    lam = ct / cr
    return (2 / 3) * cr * (1 + lam + lam * lam) / (1 + lam), (1 + 2 * lam) / (3 * (1 + lam))


def aircraft_metrics(p: dict, mtow_kg=None, static_thrust_total_n=None):
    """Returns (outputs, warnings)."""
    L, D, b = _g(p, "fuselage_length_mm"), _g(p, "fuselage_diameter_mm"), _g(p, "wing_span_mm")
    cr, ct, sw = _g(p, "wing_root_chord_mm"), _g(p, "wing_tip_chord_mm"), _g(p, "wing_sweep_deg")
    if min(L, D, b, cr, ct) <= 0 or ct > cr * 1.5:
        raise ValueError("lengths and chords must be positive and tip chord <= 1.5x root chord")
    s_w = (cr + ct) / 2 * b
    mac, ymac_frac = _mac(cr, ct)
    ac_w = L * _g(p, "wing_x_ratio") + (b / 2) * ymac_frac * math.tan(math.radians(sw)) + 0.25 * mac
    tx = L * _g(p, "tail_x_ratio")

    hr, ht, hb = _g(p, "htail_root_chord_mm"), _g(p, "htail_tip_chord_mm"), _g(p, "htail_span_mm")
    s_h = (hr + ht) / 2 * hb
    mac_h, yh = _mac(hr, ht)
    ac_h = tx + L * 0.05 + (hb / 2) * yh * math.tan(math.radians(_g(p, "htail_sweep_deg"))) + 0.25 * mac_h
    vr, vt, vh = _g(p, "vtail_root_chord_mm"), _g(p, "vtail_tip_chord_mm"), _g(p, "vtail_height_mm")
    s_v = (vr + vt) / 2 * vh
    mac_v, yv = _mac(vr, vt)
    ac_v = tx + vh * yv * math.tan(math.radians(_g(p, "vtail_sweep_deg"))) + 0.25 * mac_v

    out = {
        "wing_area_m2": round(s_w / 1e6, 3), "aspect_ratio": round(b * b / s_w, 3),
        "taper_ratio": round(ct / cr, 3), "mean_aero_chord_mm": round(mac, 1),
        "horizontal_tail_volume": round(s_h * (ac_h - ac_w) / (s_w * mac), 3),
        "vertical_tail_volume": round(s_v * (ac_v - ac_w) / (s_w * b), 4),
        "tail_arm_mm": round(ac_h - ac_w, 1),
        "fuselage_fineness": round(L / D, 2),
    }
    warn = []
    for key, (lo, hi) in BANDS.items():
        if not lo <= out[key] <= hi:
            warn.append(f"{key} = {out[key]} is outside the textbook reference band {lo}..{hi}; confirm against your requirements")
    if out["tail_arm_mm"] <= 0:
        warn.append("tail is not behind the wing aerodynamic centre: check tail_x_ratio and wing_x_ratio")
    if mtow_kg:
        out["wing_loading_kg_m2"] = round(mtow_kg / (s_w / 1e6), 1)
        if static_thrust_total_n:
            out["thrust_to_weight"] = round(static_thrust_total_n / (mtow_kg * G), 3)
            if out["thrust_to_weight"] < 0.2:
                warn.append("thrust_to_weight below 0.2 is unusually low for a jet; check weights and thrust")
    return out, warn


def mac_position(p: dict):
    """(x of mean aerodynamic chord leading edge, MAC length) in mm, from model parameters."""
    L, b = _g(p, "fuselage_length_mm"), _g(p, "wing_span_mm")
    cr, ct, sw = _g(p, "wing_root_chord_mm"), _g(p, "wing_tip_chord_mm"), _g(p, "wing_sweep_deg")
    mac, ymac_frac = _mac(cr, ct)
    return L * _g(p, "wing_x_ratio") + (b / 2) * ymac_frac * math.tan(math.radians(sw)), mac


def positions(p: dict) -> dict:
    """Aerodynamic-centre x positions (mm) and reference areas (m2) for wing, horizontal and vertical tail."""
    L, b = _g(p, "fuselage_length_mm"), _g(p, "wing_span_mm")
    cr, ct, sw = _g(p, "wing_root_chord_mm"), _g(p, "wing_tip_chord_mm"), _g(p, "wing_sweep_deg")
    mac, yf = _mac(cr, ct)
    x_le = L * _g(p, "wing_x_ratio") + (b / 2) * yf * math.tan(math.radians(sw))
    tx = L * _g(p, "tail_x_ratio")
    hr, ht, hb = _g(p, "htail_root_chord_mm"), _g(p, "htail_tip_chord_mm"), _g(p, "htail_span_mm")
    mac_h, yh = _mac(hr, ht)
    vr, vt, vh = _g(p, "vtail_root_chord_mm"), _g(p, "vtail_tip_chord_mm"), _g(p, "vtail_height_mm")
    mac_v, yv = _mac(vr, vt)
    return {
        "mac_le_x": x_le, "mac": mac, "x_ac_w": x_le + 0.25 * mac,
        "x_ac_h": tx + L * 0.05 + (hb / 2) * yh * math.tan(math.radians(_g(p, "htail_sweep_deg"))) + 0.25 * mac_h,
        "x_ac_v": tx + vh * yv * math.tan(math.radians(_g(p, "vtail_sweep_deg"))) + 0.25 * mac_v,
        "s_w": (cr + ct) / 2 * b / 1e6, "s_h": (hr + ht) / 2 * hb / 1e6, "s_v": (vr + vt) / 2 * vh / 1e6,
        "b": b / 1e3, "vtail_height": vh / 1e3, "vtail_root_chord": vr / 1e3, "vtail_tip_chord": vt / 1e3,
        "vtail_sweep_deg": _g(p, "vtail_sweep_deg"),
    }

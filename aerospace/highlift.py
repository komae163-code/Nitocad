"""High-lift aerodynamics: flap and slat increments (stdlib only).

Empirical conceptual-design estimates (Raymer-style): maximum-lift increments
from flap/slat type and flapped area, flap profile-drag increment from flap
chord ratio, area and deflection. Typical accuracy is +-20..30%: use them to
size and compare configurations, and replace them with wind-tunnel or CFD data
when you have it. The clean airfoil maximum lift coefficient is an INPUT
(airfoil data at your Reynolds number): it is not estimated here.
Landing-gear and inoperative-engine drag increments are also inputs.
"""
import math

from aerospace import aero
from aerospace.engine_cycle import CycleError

# type: (airfoil delta cl_max, scales with extended chord ratio c'/c)
FLAP_DCL = {"plain": (0.9, False), "split": (0.9, False), "slotted": (1.3, False),
            "fowler": (1.3, True), "double_slotted": (1.6, True), "triple_slotted": (1.9, True)}
SLAT_DCL = {"fixed_slot": (0.2, False), "kruger": (0.3, False), "slat": (0.4, True)}
FLAP_DRAG_F = {"plain": 1.7, "split": 1.7, "slotted": 0.9, "fowler": 0.9, "double_slotted": 0.9, "triple_slotted": 0.9}


def clean_cl_max(p: dict, cl_max_airfoil: float) -> float:
    return 0.9 * cl_max_airfoil * math.cos(aero.geometry(p)["lam_c4"])


def _flap(p, flap):
    t = flap.get("type")
    if t not in FLAP_DCL:
        raise CycleError(f"flap type must be one of {', '.join(FLAP_DCL)}")
    try:
        cf, area, dd = float(flap["chord_ratio"]), float(flap["flapped_area_ratio"]), float(flap["deflection_deg"])
    except (KeyError, TypeError, ValueError):
        raise CycleError("flap needs chord_ratio, flapped_area_ratio and deflection_deg")
    cp = float(flap.get("extended_chord_ratio", 1.0))
    if not (0.05 <= cf <= 0.45 and 0 < area <= 1 and 0 <= dd <= 65 and 1.0 <= cp <= 1.6):
        raise CycleError("flap values outside the supported range (chord_ratio 0.05..0.45, flapped_area_ratio 0..1, deflection 0..65 deg, extended_chord_ratio 1..1.6)")
    sweep = math.radians(float(flap.get("hinge_sweep_deg", math.degrees(aero.geometry(p)["lam_c4"]))))
    dcl, scales = FLAP_DCL[t]
    d_cl = 0.9 * dcl * (cp if scales else 1.0) * area * math.cos(sweep)
    d_cd = FLAP_DRAG_F[t] * cf ** 1.38 * area * math.sin(math.radians(dd)) ** 2
    return d_cl, d_cd


def _slat(p, slat):
    t = slat.get("type")
    if t not in SLAT_DCL:
        raise CycleError(f"slat type must be one of {', '.join(SLAT_DCL)}")
    area = float(slat.get("area_ratio", 0))
    cp = float(slat.get("extended_chord_ratio", 1.0))
    if not (0 < area <= 1 and 1.0 <= cp <= 1.3):
        raise CycleError("slat area_ratio must be 0..1 and extended_chord_ratio 1..1.3")
    dcl, scales = SLAT_DCL[t]
    return 0.9 * dcl * (cp if scales else 1.0) * area * math.cos(aero.geometry(p)["lam_c4"])


def configuration(p: dict, cl_max_airfoil: float, cfg: dict) -> dict:
    d_cl_f = d_cd_f = d_cl_s = 0.0
    if cfg.get("flap"):
        d_cl_f, d_cd_f = _flap(p, cfg["flap"])
    if cfg.get("slat"):
        d_cl_s = _slat(p, cfg["slat"])
    clean = clean_cl_max(p, cl_max_airfoil)
    return {"cl_max_clean": round(clean, 3), "delta_cl_max_flap": round(d_cl_f, 3), "delta_cl_max_slat": round(d_cl_s, 3),
            "cl_max": round(clean + d_cl_f + d_cl_s, 3), "delta_cd0_flap": round(d_cd_f, 5)}


def analyze(p: dict, hl: dict) -> dict:
    """hl: {cl_max_airfoil, delta_cd0_gear, delta_cd0_engine_out, takeoff:{flap,slat}, landing:{flap,slat}}"""
    miss = [k for k in ("cl_max_airfoil", "delta_cd0_gear", "delta_cd0_engine_out", "takeoff", "landing") if hl.get(k) is None]
    if miss:
        raise CycleError("highlift inputs missing: " + ", ".join(miss))
    clmax = float(hl["cl_max_airfoil"])
    if not 0.8 <= clmax <= 2.2:
        raise CycleError("cl_max_airfoil must be 0.8..2.2")
    gear, eng = float(hl["delta_cd0_gear"]), float(hl["delta_cd0_engine_out"])
    to, ld = configuration(p, clmax, hl["takeoff"]), configuration(p, clmax, hl["landing"])
    return {
        "cl_max_clean": to["cl_max_clean"],
        "takeoff": to, "landing": ld,
        "cl_max_takeoff": to["cl_max"], "cl_max_landing": ld["cl_max"],
        "delta_cd0_takeoff": round(to["delta_cd0_flap"] + gear, 5),
        "delta_cd0_landing": round(ld["delta_cd0_flap"] + gear, 5),
        "delta_cd0_oei": round(to["delta_cd0_flap"] + eng, 5),
    }

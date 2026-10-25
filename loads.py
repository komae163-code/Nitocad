"""
Static load SCREENING for the wheel corner. This is hand-calculation level
engineering, implemented so the numbers are repeatable and checked against the
actual part dimensions - it is NOT structural validation.

What it computes (rigid-flange, no preload, quasi-static):
  * wheel tyre forces -> loads at the hub flange (axial, shear, overturning moment)
  * peak tension and shear per hub stud for the bolt circle (n studs on PCD)
  * stud utilisation against proof strength for an ISO metric property class
  * optional: shear utilisation of a pin/bolt for a load you supply (ball-joint
    stud, pivot bolt in double shear)

What it does NOT do: clamp-load/preload and joint separation, prying, fatigue,
dynamic factors, thread/bearing stresses in the parts, wheel-nut torque,
brake/drive torque reaction, load sharing from friction, deflections, the
suspension's load distribution (the corner has no spring/damper/upper arm, so
ball-joint and bushing reactions are NOT derived - supply them). Safety factors
and the load cases are the user's responsibility.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ISO 898-1 property classes (MPa): proof strength Sp, yield/0.2% proof Rp0.2, tensile Rm
PROPERTY_CLASSES = {
    "8.8":  {"proof": 580.0, "yield": 640.0, "tensile": 800.0},
    "10.9": {"proof": 830.0, "yield": 940.0, "tensile": 1040.0},
    "12.9": {"proof": 970.0, "yield": 1100.0, "tensile": 1220.0},
}
# tensile stress area As (mm^2), ISO metric coarse (and two common fine pitches)
STRESS_AREA_MM2 = {"M8": 36.6, "M10": 58.0, "M12": 84.3, "M12x1.5": 88.1, "M14": 115.0,
                   "M14x1.5": 125.0, "M16": 157.0, "M20": 245.0}
_NOMINAL = {"M8": 8, "M10": 10, "M12": 12, "M14": 14, "M16": 16, "M20": 20}

UTIL_HIGH = 0.67
UTIL_OVER = 1.0

DISCLAIMER = ("Screening calculation only: rigid flange, no preload, no fatigue, no dynamic factors, "
              "no suspension load distribution. Not a substitute for analysis, testing or a design review.")


@dataclass(frozen=True)
class WheelLoadCase:
    name: str
    fz_N: float = 0.0                # vertical tyre load
    fy_N: float = 0.0                # lateral tyre load
    fx_N: float = 0.0                # longitudinal tyre load
    tyre_radius_mm: float = 330.0    # loaded radius: height of the contact patch below the hub centre
    wheel_offset_mm: float = 40.0    # ET: distance between the hub flange face and the tyre centreline

    def validate(self) -> list[str]:
        errs = []
        for k in ("fz_N", "fy_N", "fx_N", "tyre_radius_mm", "wheel_offset_mm"):
            v = getattr(self, k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                errs.append(f"{k} must be a finite number")
        if not errs:
            if not (50.0 <= self.tyre_radius_mm <= 800.0):
                errs.append("tyre_radius_mm must be between 50 and 800")
            if abs(self.wheel_offset_mm) > 200.0:
                errs.append("wheel_offset_mm must be within +/-200")
            if max(abs(self.fz_N), abs(self.fy_N), abs(self.fx_N)) > 1e6:
                errs.append("tyre forces above 1 MN are not credible for a road-vehicle corner")
        return errs


def thread_for_hole(d_mm: float) -> str | None:
    for name, nom in _NOMINAL.items():
        if abs(d_mm - nom) <= 1.0:
            return name
    return None


def hub_flange_loads(case: WheelLoadCase) -> dict:
    """Tyre contact-patch forces -> loads at the hub flange face. Worst-case
    combination: the three moment contributions are added in quadrature with
    all signs assumed aligned."""
    shear = math.hypot(case.fx_N, case.fz_N)
    axial = abs(case.fy_N)
    et = abs(case.wheel_offset_mm)
    m = math.sqrt((case.fy_N * case.tyre_radius_mm) ** 2 + (case.fz_N * et) ** 2 + (case.fx_N * et) ** 2)
    return {"axial_N": axial, "shear_N": shear, "moment_Nmm": m}


def stud_group_loads(n: int, pcd_mm: float, axial_N: float, shear_N: float, moment_Nmm: float) -> dict:
    """Rigid flange on n studs at radius r = pcd/2. A moment M is carried by
    studs in proportion to their distance from the neutral axis; the peak stud
    load is 2M/(n r). Axial load shares equally; shear shares equally."""
    if n < 1 or pcd_mm <= 0:
        raise ValueError("need at least one stud on a positive PCD")
    r = pcd_mm / 2.0
    return {"peak_tension_N": axial_N / n + 2.0 * moment_Nmm / (n * r), "shear_per_stud_N": shear_N / n}


def stud_utilisation(tension_N: float, shear_N: float, property_class: str, thread: str) -> dict:
    if property_class not in PROPERTY_CLASSES:
        raise ValueError(f"unknown property class {property_class!r}; known: {sorted(PROPERTY_CLASSES)}")
    if thread not in STRESS_AREA_MM2:
        raise ValueError(f"unknown thread {thread!r}; known: {sorted(STRESS_AREA_MM2)}")
    a = STRESS_AREA_MM2[thread]
    sigma, tau = tension_N / a, shear_N / a              # stress-area basis (conservative for shear)
    eq = math.sqrt(sigma ** 2 + 3.0 * tau ** 2)
    proof = PROPERTY_CLASSES[property_class]["proof"]
    return {"tension_stress_MPa": round(sigma, 1), "shear_stress_MPa": round(tau, 1),
            "equivalent_stress_MPa": round(eq, 1), "proof_strength_MPa": proof,
            "utilisation": round(eq / proof, 3)}


def pin_shear_utilisation(force_N: float, diameter_mm: float, shear_planes: int, property_class: str) -> dict:
    """Bolt/pin in shear on its nominal (unthreaded) area; allowable = 0.6 x yield."""
    if force_N < 0 or diameter_mm <= 0 or shear_planes < 1:
        raise ValueError("force must be >= 0, diameter > 0, shear_planes >= 1")
    area = math.pi * diameter_mm ** 2 / 4.0
    tau = force_N / (shear_planes * area)
    allow = 0.6 * PROPERTY_CLASSES[property_class]["yield"]
    return {"shear_stress_MPa": round(tau, 1), "allowable_MPa": round(allow, 1), "utilisation": round(tau / allow, 3)}


def _flag(u: float) -> str:
    return "exceeds_proof" if u > UTIL_OVER else ("high" if u > UTIL_HIGH else "ok")


def screen_corner(resolved, case: WheelLoadCase, stud_class: str = "10.9",
                  ball_joint_force_N: float | None = None, pivot_force_N: float | None = None,
                  pivot_bolt_class: str = "10.9") -> dict:
    """Screen the corner's fasteners for one load case. Uses the hub's actual
    stud count / PCD / stud-hole diameter from the assembly."""
    errs = case.validate()
    if errs:
        raise ValueError("; ".join(errs))
    hubs = [p for p in resolved.parts.values() if p.part_type == "wheel_hub"]
    if not hubs:
        raise ValueError("this assembly has no wheel_hub to screen")
    v = hubs[0].values or hubs[0].parameters
    fl = hub_flange_loads(case)
    n, pcd, hole = int(v["bolt_count"]), float(v["bolt_pcd_mm"]), float(v["stud_hole_diameter_mm"])
    grp = stud_group_loads(n, pcd, fl["axial_N"], fl["shear_N"], fl["moment_Nmm"])
    thread = thread_for_hole(hole)
    out: dict = {"case": case.name, "disclaimer": DISCLAIMER, "flange_loads": {k: round(x, 1) for k, x in fl.items()},
                 "hub_studs": {"count": n, "pcd_mm": pcd, "stud_hole_mm": hole, **{k: round(x, 1) for k, x in grp.items()}},
                 "checks": [], "not_derived": []}
    if thread is None:
        out["hub_studs"]["thread"] = None
        out["checks"].append({"item": "hub studs", "flag": "unknown",
                              "note": f"no standard metric thread matches a {hole:g}mm stud hole"})
    else:
        u = stud_utilisation(grp["peak_tension_N"], grp["shear_per_stud_N"], stud_class, thread)
        out["hub_studs"].update({"thread": thread, "property_class": stud_class, **u})
        out["checks"].append({"item": f"hub studs {thread} class {stud_class}", "utilisation": u["utilisation"], "flag": _flag(u["utilisation"])})

    if ball_joint_force_N is not None:
        bj = next((p for p in resolved.parts.values() if p.part_type == "ball_joint"), None)
        if bj is None:
            out["checks"].append({"item": "ball-joint stud", "flag": "unknown", "note": "no ball_joint part in this assembly"})
        else:
            d = float((bj.values or bj.parameters)["stud_base_diameter_mm"])
            u = pin_shear_utilisation(ball_joint_force_N, d, 1, "10.9")
            out["checks"].append({"item": f"ball-joint stud base {d:g}mm (shear, class 10.9 steel assumed)",
                                  "utilisation": u["utilisation"], "flag": _flag(u["utilisation"]), **u})
    else:
        out["not_derived"].append("ball-joint reaction: needs a suspension model (spring/damper/upper arm); supply ball_joint_force_N")
    if pivot_force_N is not None:
        sf = next((p for p in resolved.parts.values() if p.part_type == "subframe"), None)
        if sf is None:
            out["checks"].append({"item": "pivot bolt", "flag": "unknown", "note": "no subframe part in this assembly"})
        else:
            d = float((sf.values or sf.parameters)["ear_bore_diameter_mm"])
            u = pin_shear_utilisation(pivot_force_N, d, 2, pivot_bolt_class)
            out["checks"].append({"item": f"pivot bolt {d:g}mm, double shear, class {pivot_bolt_class} (bolt assumed to fill the ear bore)",
                                  "utilisation": u["utilisation"], "flag": _flag(u["utilisation"]), **u})
    else:
        out["not_derived"].append("pivot-bolt reaction: needs a suspension model; supply pivot_force_N")
    out["not_derived"] += ["preload / joint separation", "fatigue", "stresses inside the parts (flange, knuckle, arm)"]
    return out

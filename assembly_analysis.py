"""
Measured analysis of a RESOLVED assembly, in pure Python (no CadQuery):

  * measure_clearances()  - distance between every pair of placed parts, from
                            analytic envelopes (see envelope.py)
  * kinematic_sweep()     - rotate the suspension about its pivot axis (jounce /
                            rebound) and measure clearances at each position
  * stack_up()            - axial clearance sensitivity to part tolerances,
                            worst-case and RSS (see tolerance.py)

These are MEASUREMENTS of simplified envelopes, not a substitute for the OCCT
overlap check; they exist so clearance claims are numbers rather than hand
arithmetic, and so what-if analysis is fast.
"""
from __future__ import annotations

import copy
import functools
import json
import math

import domain_packs
import envelope as E
import mating as M
import tolerance
from assembly_spec import ResolvedAssembly, resolve_assembly

TIGHT_CLEARANCE_MM = 1.0
CONTACT_TOL_MM = 0.05


@functools.lru_cache(maxsize=512)
def _cached_envelope(part_type: str, params_json: str):
    """Envelope per (part type, parameters). Shapes cache their own surface
    samples, so re-placing the same part (kinematic sweep, stack-up) is cheap."""
    return domain_packs.part_envelope(part_type, json.loads(params_json))


def placed_envelopes(resolved: ResolvedAssembly, transforms: dict | None = None) -> dict[str, E.Placed | None]:
    out: dict[str, E.Placed | None] = {}
    for pid in resolved.order:
        p = resolved.parts[pid]
        env = _cached_envelope(p.part_type, json.dumps(p.parameters, sort_keys=True, default=str))
        T = (transforms or {}).get(pid, p.placement)
        out[pid] = E.Placed(env, T) if env is not None else None
    return out


def _mated_pairs(resolved: ResolvedAssembly) -> set[frozenset]:
    pairs = set()
    for m in resolved.mates:
        a, b = m["a"].split(".")[0], m["b"].split(".")[0]
        pairs.add(frozenset((a, b)))
    return pairs


def measure_clearances(resolved: ResolvedAssembly, h: float = 3.0, cutoff: float = 25.0,
                       transforms: dict | None = None, only_moving_vs_static: tuple[set, set] | None = None) -> dict:
    envs = placed_envelopes(resolved, transforms)
    mated = _mated_pairs(resolved)
    rows, findings = [], []
    ids = [i for i in resolved.order if envs[i] is not None]
    skipped = [i for i in resolved.order if envs[i] is None]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if only_moving_vs_static is not None:
                mv, st = only_moving_vs_static
                if not ((a in mv and b in st) or (b in mv and a in st)):
                    continue
            d, pts = E.clearance(envs[a], envs[b], h=h, cutoff=cutoff)
            is_mated = frozenset((a, b)) in mated
            rows.append({"a": a, "b": b, "mated": is_mated,
                         "clearance_mm": round(d, 3) if math.isfinite(d) else None,
                         "beyond_cutoff": pts is None})
            if is_mated:
                if d < -CONTACT_TOL_MM:
                    findings.append(M.Finding("error", "analytic_penetration",
                                              f"mating parts {a} and {b} penetrate by {-d:.2f}mm (envelope measurement)", a, b))
            else:
                if d < 0:
                    findings.append(M.Finding("error", "analytic_collision",
                                              f"{a} and {b} collide: {-d:.2f}mm penetration (envelope measurement)", a, b))
                elif d < TIGHT_CLEARANCE_MM:
                    findings.append(M.Finding("warning", "tight_clearance",
                                              f"only {d:.2f}mm between non-mating parts {a} and {b}", a, b))
    for pid in skipped:
        findings.append(M.Finding("info", "no_envelope", f"{pid} has no analytic envelope; not measured", pid))
    nm = [r["clearance_mm"] for r in rows if not r["mated"] and r["clearance_mm"] is not None]
    return {"pairs": rows, "findings": findings,
            "min_non_mating_clearance_mm": min(nm) if nm else None}


# --- kinematics -------------------------------------------------------------------------

def _rot_about(axis_point: M.Vec, axis_dir: M.Vec, deg: float) -> M.Transform:
    """Rotation by `deg` about the line through axis_point along axis_dir."""
    a = M.unit(axis_dir)
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    K = ((0, -a[2], a[1]), (a[2], 0, -a[0]), (-a[1], a[0], 0))
    K2 = M.mat_mul(K, K)
    R = tuple(tuple((1.0 if i == j else 0.0) + s * K[i][j] + (1 - c) * K2[i][j] for j in range(3)) for i in range(3))
    t = M.sub(axis_point, M.mat_vec(R, axis_point))
    return M.Transform(R, t)  # type: ignore[arg-type]


def find_revolute_joint(resolved: ResolvedAssembly) -> dict | None:
    """The first positioning mate whose interface is a clevis/bushing pair:
    the arm swings about that shared axis. Returns the pivot axis in world
    coordinates and the sets of moving/static part ids."""
    for i, m in enumerate(resolved.mates):
        pa, ia = m["a"].split(".")
        pb, ib = m["b"].split(".")
        kinds = {resolved.parts[pa].interfaces[ia].kind, resolved.parts[pb].interfaces[ib].kind}
        if kinds != {"clevis", "bushing_bore"}:
            continue
        child = pb if resolved.parts[pb].placed_by == f"mate:{i}" else (pa if resolved.parts[pa].placed_by == f"mate:{i}" else None)
        if child is None:
            continue
        parent = pa if child == pb else pb
        ci = ib if child == pb else ia
        frame = resolved.parts[child].interfaces[ci].frame.transformed(resolved.parts[child].placement)
        # moving = child + everything positioned (transitively) through it
        moving = {child}
        grew = True
        while grew:
            grew = False
            for j, mm in enumerate(resolved.mates):
                a_, b_ = mm["a"].split(".")[0], mm["b"].split(".")[0]
                for par, ch in ((a_, b_), (b_, a_)):
                    if par in moving and ch not in moving and resolved.parts[ch].placed_by == f"mate:{j}":
                        moving.add(ch)
                        grew = True
        static = set(resolved.order) - moving
        return {"mate": i, "parent": parent, "child": child, "axis_point": frame.origin, "axis_dir": frame.axis,
                "moving": moving, "static": static}
    return None


def kinematic_sweep(resolved: ResolvedAssembly, range_deg: tuple[float, float] = (-10.0, 10.0),
                    steps: int = 9, h: float = 4.0, track: str | None = None) -> dict:
    """Swing the suspension (everything hanging off the pivot) about the pivot
    axis. For each angle: the minimum clearance between moving and static
    parts, and the vertical/lateral position of the wheel centre (`track`
    = hub part id; default: the part named 'hub' if present).

    LIMITS: a single swing arm. With only a lower arm and a rigid knuckle there
    is no upper arm to control camber, so wheel camber equals the swing angle;
    steering and spring/damper are not modelled."""
    joint = find_revolute_joint(resolved)
    if joint is None:
        return {"joint": None, "findings": [M.Finding("info", "no_joint", "no clevis/bushing pivot found; nothing to sweep")]}
    track = track or ("hub" if "hub" in resolved.parts else None)
    out_rows, findings = [], []
    worst = (float("inf"), None)
    for k in range(steps):
        ang = range_deg[0] + (range_deg[1] - range_deg[0]) * k / max(1, steps - 1)
        R = _rot_about(joint["axis_point"], joint["axis_dir"], ang)
        transforms = {}
        for pid in resolved.order:
            T0 = resolved.parts[pid].placement
            if pid in joint["moving"]:
                transforms[pid] = M.Transform(M.mat_mul(R.R, T0.R), R.point(T0.t))
        res = measure_clearances(resolved, h=h, cutoff=20.0, transforms=transforms,
                                 only_moving_vs_static=(joint["moving"], joint["static"]))
        mc = min([r["clearance_mm"] for r in res["pairs"] if r["clearance_mm"] is not None] or [float("inf")])
        row = {"angle_deg": round(ang, 3), "min_moving_vs_static_clearance_mm": None if mc == float("inf") else round(mc, 3)}
        if track and track in transforms:
            c = transforms[track].t
            row["wheel_centre_mm"] = [round(x, 2) for x in c]
        out_rows.append(row)
        if mc < worst[0]:
            worst = (mc, ang)
    if worst[0] < 0:
        findings.append(M.Finding("error", "sweep_collision",
                                  f"the suspension collides with the chassis at {worst[1]:+.1f} deg of travel ({-worst[0]:.2f}mm penetration)"))
    elif worst[0] < TIGHT_CLEARANCE_MM:
        findings.append(M.Finding("warning", "sweep_tight",
                                  f"only {worst[0]:.2f}mm clearance at {worst[1]:+.1f} deg of travel"))
    return {"joint": {"parent": joint["parent"], "child": joint["child"],
                      "axis_point": [round(x, 3) for x in joint["axis_point"]],
                      "axis_dir": [round(x, 3) for x in joint["axis_dir"]]},
            "range_deg": list(range_deg), "rows": out_rows,
            "min_clearance_mm": None if worst[0] == float("inf") else round(worst[0], 3),
            "worst_angle_deg": worst[1], "findings": findings}


# --- tolerance stack-up -----------------------------------------------------------------

def _numeric_params(resolved: ResolvedAssembly, part_ids: list[str]) -> list[tuple[str, str, float]]:
    out = []
    for pid in part_ids:
        for k, v in (resolved.parts[pid].values or resolved.parts[pid].parameters).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v and k.endswith("_mm"):
                out.append((pid, k, float(v)))
    return out


def _placement_path(resolved: ResolvedAssembly, a: str, b: str) -> list[str]:
    """Parts on the placement chain between a and b (both included). Parts
    outside it only translate/rotate a and b together, so they cannot change
    the clearance between them."""
    parent: dict[str, str] = {}
    for i, m in enumerate(resolved.mates):
        pa, pb = m["a"].split(".")[0], m["b"].split(".")[0]
        for par, ch in ((pa, pb), (pb, pa)):
            if resolved.parts[ch].placed_by == f"mate:{i}":
                parent[ch] = par

    def chain(x: str) -> list[str]:
        out = [x]
        while out[-1] in parent:
            out.append(parent[out[-1]])
        return out

    ca, cb = chain(a), chain(b)
    common = next((x for x in ca if x in cb), None)
    path = ca[:ca.index(common) + 1] + cb[:cb.index(common)] if common else ca + cb
    seen, out = set(), []
    for x in path:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def stack_up(spec: dict, a: str, b: str, tolerances: dict | None = None, h: float = 4.0,
             cutoff: float = 12.0) -> dict:
    """Sensitivity of the clearance between parts `a` and `b` to every
    dimensional parameter of every part, using ISO 2768-m general tolerances
    (or `tolerances`: {"part.param": +/-mm} overrides). Each parameter is
    perturbed by its +/- tolerance and the assembly is re-solved and
    re-measured, so the result includes placement chains, not just a
    hand-picked dimension loop.

    Returns the nominal clearance, the worst-case (sum of |effects|) and RSS
    minimum clearance, and contributors ranked by effect. Parameters that make
    the assembly invalid when perturbed are reported, not silently skipped."""
    base = resolve_assembly(spec)
    if not base.ok:
        raise ValueError("stack-up needs a valid assembly; fix: " + "; ".join(f.message for f in base.errors[:3]))
    envs0 = placed_envelopes(base)
    if envs0.get(a) is None or envs0.get(b) is None:
        raise ValueError(f"no measurable clearance between {a!r} and {b!r} (missing envelope?)")
    nominal, _ = E.clearance(envs0[a], envs0[b], h=h, cutoff=cutoff)
    if nominal >= cutoff:
        raise ValueError(f"{a} and {b} are more than {cutoff:g}mm apart: stack-up is for close-fitting pairs "
                         "(raise `cutoff` to analyse them anyway)")

    def pair_clearance(sp: dict) -> float | None:
        r = resolve_assembly(sp)
        if not r.ok:
            return None
        envs = placed_envelopes(r)
        d, _ = E.clearance(envs[a], envs[b], h=h, cutoff=cutoff)
        return d

    effects, invalid = [], []
    # only parameters of parts the chain between a and b actually passes through
    # are needed, but the dependency graph is the authority; use all parts of
    # a and b plus everything that locates them.
    involved = _placement_path(base, a, b)
    for pid, k, val in _numeric_params(base, involved):
        tol = (tolerances or {}).get(f"{pid}.{k}", tolerance.general_tolerance_mm(val))
        worst_delta = 0.0
        for sign in (+1, -1):
            sp = copy.deepcopy(spec)
            for p in sp["parts"]:
                if p["id"] == pid:
                    p["parameters"] = {**p.get("parameters", {}), k: val + sign * tol}
            d = pair_clearance(sp)
            if d is None:
                invalid.append(f"{pid}.{k} {'+' if sign > 0 else '-'}{tol:g}")
                continue
            worst_delta = max(worst_delta, abs(d - nominal))
        if worst_delta > 0.005:
            effects.append({"param": f"{pid}.{k}", "tolerance_mm": tol, "effect_mm": round(worst_delta, 4)})
    effects.sort(key=lambda e: -e["effect_mm"])
    vals = [e["effect_mm"] for e in effects]
    return {"pair": [a, b], "nominal_mm": round(nominal, 3),
            "worst_case_min_mm": round(nominal - tolerance.worst_case(vals), 3),
            "rss_min_mm": round(nominal - tolerance.rss(vals), 3),
            "contributors": effects[:15], "perturbations_invalid": invalid,
            "method": "finite-difference on the resolved assembly with analytic-envelope clearance; ISO 2768-m default tolerances"}


# --- one entry point ----------------------------------------------------------------------

MAX_SWEEP_STEPS = 25
MAX_SWEEP_DEG = 30.0


def analyze_assembly(spec: dict, sweep: dict | None = None, stack_up_pair: dict | None = None,
                     load_case: dict | None = None) -> dict:
    """Resolve + measure + (optionally) sweep, stack up and screen loads.
    Invalid assemblies return the resolve report only: analysing a design
    whose interfaces do not fit would produce meaningless numbers."""
    import loads
    resolved = resolve_assembly(spec)
    out = resolved.to_dict()
    if not resolved.ok:
        out["analysis_skipped"] = "interface errors: fix them first"
        return out
    meas = measure_clearances(resolved)
    out["clearances"] = {"pairs": meas["pairs"], "min_non_mating_clearance_mm": meas["min_non_mating_clearance_mm"],
                         "findings": [f.to_dict() for f in meas["findings"]]}
    if sweep is not None:
        lo, hi = float(sweep.get("min_deg", -10.0)), float(sweep.get("max_deg", 10.0))
        steps = int(sweep.get("steps", 9))
        if not (-MAX_SWEEP_DEG <= lo < hi <= MAX_SWEEP_DEG) or not (2 <= steps <= MAX_SWEEP_STEPS):
            raise ValueError(f"sweep needs -{MAX_SWEEP_DEG:g} <= min_deg < max_deg <= {MAX_SWEEP_DEG:g} and 2..{MAX_SWEEP_STEPS} steps")
        ks = kinematic_sweep(resolved, (lo, hi), steps)
        ks["findings"] = [f.to_dict() for f in ks["findings"]]
        out["kinematics"] = ks
    if stack_up_pair is not None:
        a, b = stack_up_pair.get("a"), stack_up_pair.get("b")
        if a not in resolved.parts or b not in resolved.parts:
            raise ValueError("stack_up needs part ids 'a' and 'b' that exist in the assembly")
        out["stack_up"] = stack_up(spec, a, b, stack_up_pair.get("tolerances"))
    if load_case is not None:
        lc = loads.WheelLoadCase(**{k: load_case[k] for k in
                                    ("name", "fz_N", "fy_N", "fx_N", "tyre_radius_mm", "wheel_offset_mm") if k in load_case})
        out["load_screening"] = loads.screen_corner(
            resolved, lc, stud_class=load_case.get("stud_class", "10.9"),
            ball_joint_force_N=load_case.get("ball_joint_force_N"), pivot_force_N=load_case.get("pivot_force_N"))
    return out

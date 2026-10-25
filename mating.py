"""
Mating model: interfaces, compatibility rules and the rigid-mate solver.

Pure Python (no CadQuery) so interface checking, placement solving and
change-impact analysis run anywhere, instantly, without building geometry.

CONVENTIONS
  * Every Interface has a Frame in its part's local coordinates:
        origin  a reference point on the mating plane/axis
        axis    unit vector pointing OUT of the part, into where the mating
                partner sits (a boss's protrusion direction; a bore's
                opening direction; a face's outward normal)
        ref     unit vector perpendicular to axis - the "angle zero" used to
                clock bolt patterns
  * Mating two interfaces places B so that
        B.origin = A.origin + gap * A.axis        (gap >= 0: separated)
        B.axis   = -A.axis                        (they face each other)
        B.ref    =  A.ref                         (clocked, when clock=True)
    which fixes all six degrees of freedom. (Bolt-pattern phase: because the
    axes oppose, a hole at phase p on B lies at angle -p about A's axis, so two
    patterns line up when phaseA + phaseB is a multiple of the pitch angle.)
  * Rules compare the numeric data both sides declare (diameters, counts,
    depths) and return Findings plus the parameter Couplings they imply, which
    feed the dependency graph.

HONESTY: these are dimensional compatibility checks on idealised features.
They do not model tolerances/ISO fits beyond the simple clearance bands
below, thread engagement, preload, or load paths.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

Vec = tuple[float, float, float]
Mat = tuple[Vec, Vec, Vec]  # rows

_EPS = 1e-9


# --- small vector / matrix helpers ------------------------------------------------

def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec) -> Vec:
    n = norm(a)
    if n < _EPS:
        raise ValueError("zero-length direction vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def mat_vec(m: Mat, v: Vec) -> Vec:
    return (dot(m[0], v), dot(m[1], v), dot(m[2], v))


def mat_mul(a: Mat, b: Mat) -> Mat:
    cols = [(b[0][j], b[1][j], b[2][j]) for j in range(3)]
    return tuple(tuple(dot(a[i], cols[j]) for j in range(3)) for i in range(3))  # type: ignore[return-value]


def col(m: Mat, j: int) -> Vec:
    return (m[0][j], m[1][j], m[2][j])


def outer_sum(pairs: list[tuple[Vec, Vec]]) -> Mat:
    """sum_k w_k * l_k^T"""
    rows = [[0.0] * 3 for _ in range(3)]
    for w, l in pairs:
        for i in range(3):
            for j in range(3):
                rows[i][j] += w[i] * l[j]
    return tuple(tuple(r) for r in rows)  # type: ignore[return-value]


IDENTITY: Mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((1, 0, 0), (0, c, -s), (0, s, c))


def rot_y(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, 0, s), (0, 1, 0), (-s, 0, c))


def rot_z(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, -s, 0), (s, c, 0), (0, 0, 1))


@dataclass(frozen=True)
class Transform:
    """Rigid transform: p_world = R @ p_local + t."""
    R: Mat = IDENTITY
    t: Vec = (0.0, 0.0, 0.0)

    def point(self, p: Vec) -> Vec:
        return add(mat_vec(self.R, p), self.t)

    def direction(self, d: Vec) -> Vec:
        return mat_vec(self.R, d)

    def plane_args(self) -> tuple[Vec, Vec, Vec]:
        """(origin, xDir, normal) for cq.Plane - maps local XY onto this frame."""
        return self.t, col(self.R, 0), col(self.R, 2)

    def close_to(self, other: "Transform", tol: float = 1e-6) -> bool:
        return (all(abs(self.t[i] - other.t[i]) <= tol for i in range(3))
                and all(abs(self.R[i][j] - other.R[i][j]) <= tol for i in range(3) for j in range(3)))

    def to_dict(self) -> dict:
        return {"R": [list(r) for r in self.R], "t": list(self.t)}


def transform_from_pose(position: Vec = (0, 0, 0), rotation_deg: Vec = (0, 0, 0)) -> Transform:
    """Explicit placement, same composition as the legacy assembly code
    (Location(X) * Location(Y) * Location(Z)  =>  R = Rx @ Ry @ Rz)."""
    r = mat_mul(mat_mul(rot_x(rotation_deg[0]), rot_y(rotation_deg[1])), rot_z(rotation_deg[2]))
    return Transform(r, (float(position[0]), float(position[1]), float(position[2])))


@dataclass(frozen=True)
class Frame:
    origin: Vec
    axis: Vec
    ref: Vec = (1.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        a = unit(self.axis)
        r = self.ref
        r = sub(r, scale(a, dot(r, a)))            # project ref off the axis
        if norm(r) < 1e-6:                         # ref was parallel to axis
            r = (0.0, 0.0, 1.0) if abs(a[2]) < 0.9 else (1.0, 0.0, 0.0)
            r = sub(r, scale(a, dot(r, a)))
        object.__setattr__(self, "axis", a)
        object.__setattr__(self, "ref", unit(r))
        object.__setattr__(self, "origin", (float(self.origin[0]), float(self.origin[1]), float(self.origin[2])))

    def transformed(self, t: Transform) -> "Frame":
        return Frame(t.point(self.origin), t.direction(self.axis), t.direction(self.ref))

    def basis(self) -> tuple[Vec, Vec, Vec]:
        return self.ref, cross(self.axis, self.ref), self.axis


@dataclass(frozen=True)
class Interface:
    name: str
    kind: str
    frame: Frame
    data: Mapping[str, Any] = field(default_factory=dict)
    # data key -> the part parameter it is derived from (dependency graph)
    sources: Mapping[str, str] = field(default_factory=dict)
    # parameters that move this interface's frame (placement dependency)
    frame_sources: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Finding:
    severity: str                 # "error" | "warning" | "info"
    code: str
    message: str
    a: str | None = None          # "part.interface"
    b: str | None = None

    def key(self) -> tuple:
        return (self.severity, self.code, self.a, self.b)

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "a": self.a, "b": self.b}


@dataclass(frozen=True)
class Coupling:
    """a_key on interface A and b_key on interface B are tied by `relation`."""
    a_key: str
    b_key: str
    relation: str


# --- solver -----------------------------------------------------------------------

def solve_mate(a_world: Frame, b_local: Frame, gap: float = 0.0, clock: bool = True) -> Transform:
    """Transform that places part B so b_local mates with the already-placed
    a_world (see module docstring)."""
    a_t = scale(a_world.axis, -1.0)
    if clock:
        r_t = a_world.ref
    else:
        # any direction perpendicular to the target axis
        r_t = b_local.ref
        r_t = sub(r_t, scale(a_t, dot(r_t, a_t)))
        if norm(r_t) < 1e-6:
            r_t = (0.0, 0.0, 1.0) if abs(a_t[2]) < 0.9 else (1.0, 0.0, 0.0)
            r_t = sub(r_t, scale(a_t, dot(r_t, a_t)))
        r_t = unit(r_t)
    w = (r_t, cross(a_t, r_t), a_t)
    l = b_local.basis()
    R = outer_sum([(w[k], l[k]) for k in range(3)])
    target_origin = add(a_world.origin, scale(a_world.axis, gap))
    t = sub(target_origin, mat_vec(R, b_local.origin))
    return Transform(R, t)


def verify_mate_world(a_world: Frame, b_world: Frame, gap: float = 0.0, clock: bool = True,
                      a_name: str = "", b_name: str = "") -> list[Finding]:
    """For mates that do not position anything (both parts already placed by
    other mates): do the two frames actually coincide? Catches a loop of
    mates that disagree."""
    out: list[Finding] = []
    want = add(a_world.origin, scale(a_world.axis, gap))
    d = norm(sub(b_world.origin, want))
    if d > 0.05:
        out.append(Finding("error", "mate_position_mismatch",
                           f"interfaces are {d:.3f}mm apart where they should coincide "
                           f"(gap {gap:g}mm): the parts positioned by other mates do not line up here",
                           a_name, b_name))
    if dot(a_world.axis, b_world.axis) > -0.9999:
        out.append(Finding("error", "mate_axis_mismatch",
                           "interface axes are not anti-parallel: the parts are not oriented to face each other",
                           a_name, b_name))
    elif clock and dot(a_world.ref, b_world.ref) < 0.9999:
        out.append(Finding("error", "mate_clock_mismatch",
                           "interfaces are rotated relative to each other about the shared axis", a_name, b_name))
    return out


# --- rules --------------------------------------------------------------------------

RuleResult = tuple[list[Finding], list[Coupling]]
RuleFn = Callable[[Interface, Interface, str, str], RuleResult]
_RULES: dict[frozenset, tuple[str, str, RuleFn]] = {}

CLEARANCE_OK_MM = 0.5       # slip/locating fit band: 0 .. 0.5 mm diametral clearance
CLEARANCE_MAX_MM = 1.0      # beyond this a locating feature no longer locates
BOLT_PCD_WARN_MM = 0.2
BOLT_PCD_ERR_MM = 0.5
CONTACT_MIN_MM = 6.0        # minimum radial width of a bolted contact annulus
ENVELOPE_MARGIN_MM = 0.5


def register_rule(kind_a: str, kind_b: str, fn: RuleFn) -> None:
    """fn(a, b, a_name, b_name) where a.kind == kind_a and b.kind == kind_b."""
    _RULES[frozenset((kind_a, kind_b))] = (kind_a, kind_b, fn)


def rule_pairs() -> list[tuple[str, str]]:
    """Every mateable (kind, kind) pair, e.g. ('bolt_pattern', 'bolt_pattern'),
    ('pilot_bore', 'pilot_boss')."""
    out = []
    for key in _RULES:
        items = sorted(key)
        out.append((items[0], items[0] if len(items) == 1 else items[1]))
    return sorted(out)


def known_kinds() -> set[str]:
    return {k for key in _RULES for k in key}


def mates_with(kind: str) -> list[str]:
    out = []
    for key in _RULES:
        if kind in key:
            out.extend(sorted(key - {kind}) or [kind])
    return sorted(set(out))


def check_pair(a: Interface, b: Interface, a_name: str, b_name: str) -> RuleResult:
    key = frozenset((a.kind, b.kind))
    entry = _RULES.get(key)
    if entry is None:
        return ([Finding("error", "incompatible_interfaces",
                         f"a '{a.kind}' interface cannot mate with a '{b.kind}' interface",
                         a_name, b_name)], [])
    first, _second, fn = entry
    if a.kind == first:
        return fn(a, b, a_name, b_name)
    # swap so the rule always sees (first_kind, second_kind); couplings are
    # returned relative to the swapped order, so flip them back
    findings, couplings = fn(b, a, b_name, a_name)
    return findings, [Coupling(c.b_key, c.a_key, c.relation) for c in couplings]


def _f(sev, code, msg, a, b):
    return Finding(sev, code, msg, a, b)


PRESS_FIT_MIN_MM = -0.12    # max diametral interference for a press fit (nominal)
PRESS_FIT_MAX_MM = 0.05     # looser than this and it is not a press fit


def _fit(male: Interface, female: Interface, what: str, a: str, b: str) -> list[Finding]:
    """Diametral fit check between a male and a female cylindrical feature.

    * both sides carry an ISO 286 class (data['tol_class'], e.g. 'g6' / 'H7'):
      the WORST-CASE clearance range from the limit dimensions is checked, so
      a fit that only works at nominal size is still caught
    * male.data['press_fit']: nominal interference up to PRESS_FIT_MIN_MM is
      expected and allowed
    * otherwise: nominal clearance bands (CLEARANCE_OK_MM / CLEARANCE_MAX_MM)
    """
    md, fd = float(male.data["diameter_mm"]), float(female.data["diameter_mm"])
    mc, fc = male.data.get("tol_class"), female.data.get("tol_class")
    if mc and fc:
        import tolerance
        try:
            cmin, cmax = tolerance.fit_clearance(fd, fc, md, mc)
        except (KeyError, ValueError) as exc:
            return [_f("warning", "tolerance_unavailable", f"{what}: cannot evaluate {fc}/{mc}: {exc}", a, b)]
        rng = f"{fc}/{mc} gives {cmin * 1000:+.0f}..{cmax * 1000:+.0f} um diametral clearance"
        if cmin < -1e-6:
            return [_f("error", "interference_fit",
                       f"{what}: worst case interferes ({rng}) - may not assemble", a, b)]
        if cmax > CLEARANCE_MAX_MM:
            return [_f("error", "loose_fit", f"{what}: worst case is too loose to locate ({rng})", a, b)]
        if cmax > CLEARANCE_OK_MM:
            return [_f("warning", "sloppy_fit", f"{what}: worst case exceeds the {CLEARANCE_OK_MM:g}mm locating band ({rng})", a, b)]
        return [_f("info", "fit_ok", f"{what}: {rng}", a, b)]
    c = fd - md
    if male.data.get("press_fit"):
        if c < PRESS_FIT_MIN_MM - 1e-9:
            return [_f("error", "interference_fit",
                       f"{what}: {-c:.3f}mm interference exceeds the {-PRESS_FIT_MIN_MM:g}mm press-fit allowance", a, b)]
        if c > PRESS_FIT_MAX_MM + 1e-9:
            return [_f("error", "loose_fit", f"{what}: {c:.3f}mm clearance - too loose for a press fit", a, b)]
        return []
    if c < -1e-6:
        return [_f("error", "interference_fit",
                   f"{what}: the {md:g}mm male feature is {-c:.3f}mm larger than the {fd:g}mm bore - it cannot assemble", a, b)]
    if c > CLEARANCE_MAX_MM:
        return [_f("error", "loose_fit",
                   f"{what}: {c:.2f}mm diametral clearance ({fd:g}mm bore on {md:g}mm) - too loose to locate", a, b)]
    if c > CLEARANCE_OK_MM:
        return [_f("warning", "sloppy_fit",
                   f"{what}: {c:.2f}mm diametral clearance is above the {CLEARANCE_OK_MM:g}mm locating band", a, b)]
    return []


def _rule_bolt_pattern(a: Interface, b: Interface, an: str, bn: str) -> RuleResult:
    out: list[Finding] = []
    ca, cb = int(a.data["count"]), int(b.data["count"])
    if ca != cb:
        out.append(_f("error", "bolt_count_mismatch", f"stud/bolt count differs: {ca} vs {cb}", an, bn))
    else:
        dpcd = abs(a.data["pcd_mm"] - b.data["pcd_mm"])
        if dpcd > BOLT_PCD_ERR_MM:
            out.append(_f("error", "bolt_pcd_mismatch",
                          f"bolt circle diameters differ by {dpcd:.2f}mm ({a.data['pcd_mm']:g} vs {b.data['pcd_mm']:g})", an, bn))
        elif dpcd > BOLT_PCD_WARN_MM:
            out.append(_f("warning", "bolt_pcd_tolerance",
                          f"bolt circle diameters differ by {dpcd:.2f}mm; studs will bind", an, bn))
        pitch = 360.0 / ca
        phase = (float(a.data.get("phase_deg", 0.0)) + float(b.data.get("phase_deg", 0.0))) % pitch
        if min(phase, pitch - phase) > 0.1:
            out.append(_f("error", "bolt_phase_mismatch",
                          f"hole patterns are clocked {min(phase, pitch - phase):.2f} deg apart and will not line up", an, bn))
    roles = {a.data.get("role"), b.data.get("role")}
    if roles == {"press", "clearance"}:
        press, clr = (a, b) if a.data.get("role") == "press" else (b, a)
        if clr.data["hole_dia_mm"] < press.data["hole_dia_mm"] + 0.2:
            out.append(_f("error", "stud_clearance",
                          f"the {clr.data['hole_dia_mm']:g}mm clearance hole cannot pass the {press.data['hole_dia_mm']:g}mm stud", an, bn))
    return out, [Coupling("count", "count", "equal"), Coupling("pcd_mm", "pcd_mm", "equal")]


def _rule_pilot(boss: Interface, bore: Interface, bn: str, rn: str) -> RuleResult:
    out = _fit(boss, bore, "locating pilot", bn, rn)
    if boss.data.get("height_mm") is not None and bore.data.get("depth_mm") is not None \
            and boss.data["height_mm"] + 1e-6 < bore.data["depth_mm"]:
        out.append(_f("warning", "pilot_too_short",
                      f"the {boss.data['height_mm']:g}mm pilot does not protrude through the {bore.data['depth_mm']:g}mm "
                      "mounting plate, so the next part (wheel) cannot centre on it", bn, rn))
    return out, [Coupling("diameter_mm", "diameter_mm", "bore_ge_boss")]


def _rule_face(a: Interface, b: Interface, an: str, bn: str) -> RuleResult:
    out: list[Finding] = []
    lo = max(a.data["inner_radius_mm"], b.data["inner_radius_mm"])
    hi = min(a.data["outer_radius_mm"], b.data["outer_radius_mm"])
    if hi - lo < CONTACT_MIN_MM:
        out.append(_f("error", "contact_area",
                      f"the mating faces overlap by only {max(0.0, hi - lo):.1f}mm radially (need {CONTACT_MIN_MM:g}mm)", an, bn))
    for x in (a, b):
        pcd, hd = x.data.get("bolt_pcd_mm"), x.data.get("bolt_hole_dia_mm")
        if pcd and hd and not (lo <= pcd / 2 - hd / 2 and pcd / 2 + hd / 2 <= hi):
            out.append(_f("error", "bolts_off_contact",
                          f"the bolt circle ({pcd:g}mm PCD, {hd:g}mm holes) is not fully on the contact face "
                          f"(contact spans r={lo:g}..{hi:g}mm)", an, bn))
            break
    for body, cavity in ((a, b), (b, a)):
        br, cr = body.data.get("body_radius_mm"), cavity.data.get("cavity_radius_mm")
        if br is not None and cr is not None and br > cr - ENVELOPE_MARGIN_MM:
            out.append(_f("error", "body_in_cavity",
                          f"the part's {br:g}mm body radius does not fit inside the {cr:g}mm cavity", an, bn))
    return out, [Coupling("outer_radius_mm", "outer_radius_mm", "overlap")]


def _rule_journal(j: Interface, bore: Interface, jn: str, bn: str) -> RuleResult:
    out = _fit(j, bore, "bearing seat", jn, bn)
    if j.data.get("length_mm") is not None and bore.data.get("depth_mm") is not None \
            and j.data["length_mm"] + 1e-6 < bore.data["depth_mm"]:
        out.append(_f("warning", "journal_short",
                      f"the {j.data['length_mm']:g}mm journal is shorter than the {bore.data['depth_mm']:g}mm bore: "
                      "the bearing seat is not fully supported", jn, bn))
    return out, [Coupling("diameter_mm", "diameter_mm", "bore_ge_boss")]


def _rule_pin(pin: Interface, sock: Interface, pn: str, sn: str) -> RuleResult:
    out = _fit(pin, sock, "pin in socket", pn, sn)
    if pin.data.get("length_mm") is not None and sock.data.get("depth_mm") is not None \
            and pin.data["length_mm"] + 1e-6 < sock.data["depth_mm"]:
        out.append(_f("warning", "pin_short",
                      f"the {pin.data['length_mm']:g}mm pin does not pass through the {sock.data['depth_mm']:g}mm boss", pn, sn))
    return out, [Coupling("diameter_mm", "diameter_mm", "bore_ge_boss")]


TAPER_RATIO_TOL = 0.002     # diametral taper per unit length
TAPER_DIA_ERR_MM = 0.5
TAPER_DIA_WARN_MM = 0.1
STUD_LONG_TOL_MM = 0.2      # the (blind) seat must be at least as deep as the stud
CLEVIS_PLAY_MIN_MM = 0.2
CLEVIS_PLAY_WARN_MM = 3.0
CLEVIS_PLAY_MAX_MM = 6.0
PIVOT_BORE_WARN_MM = 0.5
PIVOT_BORE_ERR_MM = 2.0


def _rule_taper(stud: Interface, bore: Interface, sn: str, bn: str) -> RuleResult:
    out: list[Finding] = []
    dt = abs(stud.data["taper_ratio"] - bore.data["taper_ratio"])
    if dt > TAPER_RATIO_TOL:
        out.append(_f("error", "taper_mismatch",
                      f"stud taper {stud.data['taper_ratio']:g} and seat taper {bore.data['taper_ratio']:g} differ: "
                      "the cones will not seat", sn, bn))
    dd = abs(stud.data["base_diameter_mm"] - bore.data["base_diameter_mm"])
    if dd > TAPER_DIA_ERR_MM:
        out.append(_f("error", "taper_diameter_mismatch",
                      f"stud base {stud.data['base_diameter_mm']:g}mm vs seat {bore.data['base_diameter_mm']:g}mm: "
                      f"the cones seat {dd / max(stud.data['taper_ratio'], 1e-6):.1f}mm away from the face", sn, bn))
    elif dd > TAPER_DIA_WARN_MM:
        out.append(_f("warning", "taper_seat_shift",
                      f"stud/seat base diameters differ by {dd:.2f}mm, shifting the seated position", sn, bn))
    if stud.data["length_mm"] > bore.data["depth_mm"] + STUD_LONG_TOL_MM:
        out.append(_f("error", "stud_too_long",
                      f"the {stud.data['length_mm']:g}mm stud is longer than the {bore.data['depth_mm']:g}mm blind seat: "
                      "its tip would run into solid metal", sn, bn))
    return out, [Coupling("base_diameter_mm", "base_diameter_mm", "equal"),
                 Coupling("taper_ratio", "taper_ratio", "equal")]


def _rule_clevis(bush: Interface, clevis: Interface, bn: str, cn: str) -> RuleResult:
    out: list[Finding] = []
    play = clevis.data["gap_mm"] - bush.data["length_mm"]
    if play < CLEVIS_PLAY_MIN_MM:
        out.append(_f("error", "clevis_too_tight",
                      f"the {bush.data['length_mm']:g}mm bushing sleeve does not fit the {clevis.data['gap_mm']:g}mm clevis gap "
                      f"(needs at least {CLEVIS_PLAY_MIN_MM:g}mm axial play)", bn, cn))
    elif play > CLEVIS_PLAY_MAX_MM:
        out.append(_f("error", "clevis_too_loose",
                      f"{play:.1f}mm axial play between the sleeve and the clevis ears", bn, cn))
    elif play > CLEVIS_PLAY_WARN_MM:
        out.append(_f("warning", "clevis_play",
                      f"{play:.1f}mm axial play between the sleeve and the clevis ears", bn, cn))
    db = abs(bush.data["diameter_mm"] - clevis.data["bore_diameter_mm"])
    if db > PIVOT_BORE_ERR_MM:
        out.append(_f("error", "pivot_bore_mismatch",
                      f"sleeve bore {bush.data['diameter_mm']:g}mm vs clevis ear bore {clevis.data['bore_diameter_mm']:g}mm: "
                      "one pivot bolt cannot fit both", bn, cn))
    elif db > PIVOT_BORE_WARN_MM:
        out.append(_f("warning", "pivot_bore_step",
                      f"sleeve bore and ear bore differ by {db:.2f}mm; the pivot bolt will be loose in one", bn, cn))
    return out, [Coupling("length_mm", "gap_mm", "gap_ge_length"),
                 Coupling("diameter_mm", "bore_diameter_mm", "equal")]


register_rule("bolt_pattern", "bolt_pattern", _rule_bolt_pattern)
register_rule("taper_stud", "taper_bore", _rule_taper)
register_rule("bushing_bore", "clevis", _rule_clevis)
register_rule("pilot_boss", "pilot_bore", _rule_pilot)
register_rule("face", "face", _rule_face)
register_rule("journal", "bearing_bore", _rule_journal)
register_rule("pin", "socket", _rule_pin)

# interface kinds that exist but have no mating partner modelled yet
OPEN_ONLY_KINDS = {"body_mount"}

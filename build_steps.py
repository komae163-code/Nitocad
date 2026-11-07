"""Build in steps, for everything the chat can build, written for a beginner.

vehicle_chat.py already builds a vehicle one group of parts at a time: "Step 1 of 4: the frame. Next is the wheel.
Say next to add it." This module gives the same walk-through to the other things the chat builds:

  * brackets, plates, flanges and motor mounts: the body, then the holes, then the rounded edges
  * buildings: the floors and walls, then the roof (and the ground floor first when the rooms still fit)
  * assemblies of three or more parts: the base parts first, then one part at a time
  * circuit boards: the power supply, then the parts that do the work, then the connectors
  * roads and culverts: the road surface, then the kerbs; one barrel, then the extra barrels

A staged spec is the step that is showing, plus spec["stage"] = {"k", "of", "target"}. The target is the finished
design. Every step is a view of the target, so a change typed mid-way is planned against the finished design and the
view is cut again. Nothing is charged for a step. Generate makes what is showing, the same rule the vehicles use.

A kind of build with nothing to split (a gear, a shaft, a plain box) has no steps and builds in one go. A step is only
offered when every step passes the same checks the build itself uses. If one fails, the build is not staged.
Never raises into the chat: every public function falls back to "not staged".
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Callable, NamedTuple

logger = logging.getLogger(__name__)

STAGE_KEY = "stage"
_MISSING = object()


class Step(NamedTuple):
    title: str                                   # "the holes"
    why: str                                     # one plain sentence a beginner can use
    view: Callable[[dict], dict]                 # target spec -> the spec for this step (no "stage" key)
    adds: tuple = ()                             # parameters that only start to matter at this step (for the "not yet" note)


# --------------------------------------------------------------------------------------------- the spec carrier
def stage_of(spec: dict | None) -> dict | None:
    st = (spec or {}).get(STAGE_KEY)
    if isinstance(st, dict) and isinstance(st.get("target"), dict) and isinstance(st.get("k"), int) and isinstance(st.get("of"), int):
        return st
    return None


def target_of(spec: dict | None) -> dict | None:
    """The finished design behind a staged spec, or the spec itself when it is not staged."""
    st = stage_of(spec)
    return copy.deepcopy(st["target"]) if st else spec


def _plain(spec: dict) -> dict:
    return {"part_type": spec.get("part_type"), "parameters": copy.deepcopy(spec.get("parameters") or {}),
            "material": spec.get("material"), "operations": copy.deepcopy(spec.get("operations") or [])}


def _with_stage(view: dict, target: dict, k: int, n: int) -> dict:
    out = _plain(view)
    out[STAGE_KEY] = {"k": k, "of": n, "target": _plain(target)}
    return out


# ------------------------------------------------------------------------------------------------- the words
_WHY_BODY = ("This is the solid shape everything else is cut into, so it always comes first. Check its size and "
             "proportions before adding any detail.")
_WHY_HOLES = ("Holes go in once the body exists, so you can see exactly where they land. Every hole takes metal away, "
              "so it also makes the part a little weaker.")
_WHY_EDGES = ("Rounding the edges comes last, because it changes every corner you already built. It makes the part "
              "safer to handle and stronger at the corners.")

_NICE = {"l_bracket": "L bracket", "t_bracket": "T bracket", "channel_bracket": "channel bracket",
         "motor_mount": "motor mount", "flat_plate": "plate", "flange": "flange", "building": "building",
         "pcb_board": "circuit board", "assembly": "assembly", "road_segment": "road", "box_culvert": "culvert"}


def nice_name(part_type: str) -> str:
    return _NICE.get(part_type, str(part_type).replace("_", " "))


# ------------------------------------------------------------------------------------------ checks per kind
def _valid(view: dict) -> bool:
    """Would this step build? Uses the same checks the real build uses. Anything doubtful counts as no."""
    try:
        pt = view.get("part_type")
        params = view.get("parameters") or {}
        if pt == "building":
            from building.flat import resolve_flat
            return bool(resolve_flat(params)["ok"])
        if pt == "pcb_board":
            from pcb import compose
            return not compose.validate_spec(copy.deepcopy(params.get("spec") or {}))[1]
        if pt == "assembly":
            return isinstance(params.get("parts"), list) and len(params["parts"]) >= 2
        import validator
        _, result = validator.validate_parameters(pt, copy.deepcopy(params))
        return not list(getattr(result, "errors", []) or [])
    except Exception:  # noqa: BLE001 - if a check cannot run, do not stage
        logger.debug("step check failed", exc_info=True)
        return False


# ---------------------------------------------------- brackets, plates, flanges, mounts: body, holes, edges
# Only keys whose templates skip the feature at zero are listed, so a hidden feature really is absent.
_HOLE_KEYS = ("hole_count", "hole_count_x", "hole_count_y", "mount_hole_count", "hole_diameter_mm",
              "mount_hole_diameter_mm", "bolt_circle_mm", "hole_pattern")
_EDGE_KEYS = ("fillet_radius_mm", "corner_fillet_mm")
_FEATURES: dict[str, dict[str, Any]] = {
    "l_bracket": {"holes": {"hole_count": 2}, "edges": {"fillet_radius_mm": 2.0}},
    "t_bracket": {"holes": {"hole_count": 2}, "edges": {"fillet_radius_mm": 1.5}},
    "channel_bracket": {"holes": {"mount_hole_count": 2}},
    "motor_mount": {"edges": {"fillet_radius_mm": 2.0}},
    "flat_plate": {"holes": {"hole_count_x": 0, "hole_count_y": 0}, "edges": {"corner_fillet_mm": 3.0}},
    "flange": {"holes": {"hole_count": 4}},
}


def _has(params: dict, keys: dict[str, Any]) -> bool:
    for k, default in keys.items():
        v = params.get(k, default)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return True
    return False


def _zero(spec: dict, keys: dict[str, Any]) -> dict:
    out = copy.deepcopy(spec)
    for k in keys:
        out["parameters"][k] = 0
    return out


def _feature_steps(target: dict) -> list[Step]:
    table = _FEATURES.get(str(target.get("part_type")))
    if not table:
        return []
    params = target.get("parameters") or {}
    holes = table.get("holes") if table.get("holes") and _has(params, table["holes"]) else None
    edges = table.get("edges") if table.get("edges") and _has(params, table["edges"]) else None
    if not (holes or edges):
        return []
    name = nice_name(str(target["part_type"]))
    steps = [Step(f"the main body of the {name}", _WHY_BODY,
                  lambda t, h=holes, e=edges: _zero(_zero(t, h or {}), e or {}))]
    if holes:
        steps.append(Step("the holes", _WHY_HOLES, lambda t, e=edges: _zero(t, e or {}), _HOLE_KEYS))
    if edges:
        steps.append(Step("the rounded edges", _WHY_EDGES, lambda t: copy.deepcopy(t), _EDGE_KEYS))
    return steps


# ----------------------------------------------------------------------------------------------- buildings
def _building_steps(target: dict) -> list[Step]:
    if target.get("part_type") != "building":
        return []
    try:
        from building.flat import resolve_flat
    except Exception:  # noqa: BLE001
        return []
    p = target.get("parameters") or {}
    r = resolve_flat(p)
    if not r["ok"]:
        return []
    geo = r["model"]["geometry"]
    wd = {"width_mm": float(p.get("width_mm") or geo["W"]), "depth_mm": float(p.get("depth_mm") or geo["D"])}
    floors = int(p.get("floors") or 1)
    roof = str(p.get("roof_type") or "gable")

    def pinned(**over: Any) -> Callable[[dict], dict]:
        def f(t: dict) -> dict:
            out = copy.deepcopy(t)
            out["parameters"].update(wd)         # the same footprint at every step, so the building does not jump
            out["parameters"].update(over)
            return out
        return f

    identity: Callable[[dict], dict] = lambda t: copy.deepcopy(t)
    steps: list[Step] = []
    ground = None
    if floors > 1:
        ground = pinned(floors=1, roof_type="flat")
        if _valid(ground(target)):          # the rooms may not all fit on one floor: then there is no ground-floor step
            steps.append(Step("the ground floor and its walls",
                              "Start with the ground floor. It sets the footprint and the room layout, and every other "
                              "floor sits on it.", ground))
        else:
            ground = None
    if roof == "gable":
        flat_top = pinned(roof_type="flat")
        if _valid(flat_top(target)):
            if ground is not None:
                steps.append(Step("the upper floors", "Add the upper floors. Each one sits on the floor below it, so the "
                                  "walls line up.", flat_top))
            else:
                steps.append(Step("the floors and walls", "Start with the floors and walls. They set the footprint and "
                                  "the rooms, and the roof depends on them.", flat_top))
        steps.append(Step("the roof", "The roof goes on last, because its shape depends on the walls under it. A pitched "
                          "roof sheds water, a flat roof is simpler to build.", identity))
    elif ground is not None:
        steps.append(Step("the upper floors", "Add the upper floors. Each one sits on the floor below it, so the walls "
                          "line up.", identity))
    return steps if len(steps) >= 2 else []


# ------------------------------------------------------------------------------------------------ assemblies
def _part_name(p: dict) -> str:
    return str(p.get("name") or p.get("part_type") or "part").replace("_", " ")


def _assembly_steps(target: dict) -> list[Step]:
    p = target.get("parameters") or {}
    parts = p.get("parts")
    if target.get("part_type") != "assembly" or not isinstance(parts, list) or len(parts) < 3:
        return []
    if p.get("mates") or p.get("vehicle") is not None or str(p.get("layout") or "row") not in ("row", "custom"):
        return []                                  # a mated assembly is placed by the kernel, which is not run per step
    rest = len(parts) - 2
    size = 1 if rest <= 4 else -(-rest // 3)
    cuts = [2]
    while cuts[-1] < len(parts):
        cuts.append(min(len(parts), cuts[-1] + size))

    def prefix(m: int) -> Callable[[dict], dict]:
        def f(t: dict) -> dict:
            out = copy.deepcopy(t)
            out["parameters"]["parts"] = out["parameters"]["parts"][:m]
            return out
        return f

    steps: list[Step] = []
    for i, m in enumerate(cuts):
        prev = cuts[i - 1] if i else 0
        names = [_part_name(x) for x in parts[prev:m]]
        title = "the " + " and the ".join(names) if len(names) <= 2 else f"the next {len(names)} parts"
        why = ("Start with the part everything else sits on or turns around. Get its size right before adding the rest."
               if i == 0 else
               "Now add a part that fits onto what is already there. Check that it lines up with the part before it.")
        steps.append(Step(title, why, prefix(m)))
    return steps


# ------------------------------------------------------------------------------------------ circuit boards
_PCB_POWER = ("power", "ldo", "buck", "reg", "supply")
_PCB_CONN = ("header", "breakout", "connector", "terminal_out", "usb")


def _pcb_group(block: dict) -> int:
    t = str(block.get("type") or "").lower()
    if t.startswith(_PCB_POWER):
        return 0
    if any(w in t for w in _PCB_CONN):
        return 2
    return 1


_PCB_WHY = ["Every board starts with how power gets in and how it is made safe for the chips. Nothing else works without it.",
            "Now the parts that do the work, powered from the supply you just added.",
            "Last come the connectors and headers, where the board talks to the rest of your project."]
_PCB_TITLE = ["the power supply", "the parts that do the work", "the connectors and headers"]


def _pcb_steps(target: dict) -> list[Step]:
    spec = (target.get("parameters") or {}).get("spec")
    if target.get("part_type") != "pcb_board" or not isinstance(spec, dict):
        return []
    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or len(blocks) < 3:
        return []
    groups = sorted({_pcb_group(b) for b in blocks if isinstance(b, dict)})
    if len(groups) < 2:
        return []

    def upto(g: int) -> Callable[[dict], dict]:
        def f(t: dict) -> dict:
            out = copy.deepcopy(t)
            sp = out["parameters"]["spec"]
            sp["blocks"] = [b for b in sp["blocks"] if _pcb_group(b) <= g]
            return out
        return f

    return [Step(_PCB_TITLE[g], _PCB_WHY[g], upto(g)) for g in groups]


# ------------------------------------------------------------------------------------- roads and culverts
def _civil_steps(target: dict) -> list[Step]:
    pt, p = target.get("part_type"), target.get("parameters") or {}
    if pt == "road_segment" and p.get("has_kerbs"):
        def bare(t: dict) -> dict:
            out = copy.deepcopy(t)
            out["parameters"]["has_kerbs"] = False
            return out
        return [Step("the road surface", "Start with the road surface and its lanes. Everything else follows its width.", bare),
                Step("the kerbs", "Kerbs go on last. They mark the road edge and guide the water to the drains.",
                     lambda t: copy.deepcopy(t))]
    if pt == "box_culvert" and isinstance(p.get("barrel_count"), int) and p["barrel_count"] > 1:
        def one(t: dict) -> dict:
            out = copy.deepcopy(t)
            out["parameters"]["barrel_count"] = 1
            return out
        return [Step("the first barrel", "Start with one barrel, the opening the water passes through.", one),
                Step("the extra barrels", "Add the other barrels once the first one looks right.", lambda t: copy.deepcopy(t))]
    return []


# --------------------------------------------------------------------------------------------- the plan
_BUILDERS = (_feature_steps, _building_steps, _assembly_steps, _pcb_steps, _civil_steps)


def steps_for(target: dict) -> list[Step]:
    """The steps of a finished design, or [] when it has nothing to split or a step would not build."""
    try:
        t = _plain(target)
        for build in _BUILDERS:
            steps = build(t)
            if len(steps) < 2:
                continue
            views = [s.view(t) for s in steps]
            if all(_valid(v) for v in views):
                return steps
            logger.info("steps for %s dropped: a step does not validate", t.get("part_type"))
        return []
    except Exception:  # noqa: BLE001
        logger.exception("building steps failed")
        return []


def view_at(target: dict, steps: list[Step], k: int) -> dict:
    """The spec to show at step k (1-based). The last step is the finished design with no stage key."""
    k = max(1, min(k, len(steps)))
    view = steps[k - 1].view(_plain(target))
    return _plain(view) if k == len(steps) else _with_stage(view, target, k, len(steps))


# --------------------------------------------------------------------------------------------- talking
def _sizes(target: dict) -> str:
    p = target.get("parameters") or {}
    pt = target.get("part_type")
    if pt == "building":
        bits = [f"{p.get('floors', 1)} floor(s)"] + ([f"{p['bedrooms']} bedroom(s)"] if p.get("bedrooms") else [])
        return "Main choices: " + ", ".join(bits) + f", {p.get('roof_type', 'gable')} roof."
    if pt in ("assembly", "pcb_board"):
        return ""
    bits = []
    for k, v in p.items():
        if k.endswith("_mm") and isinstance(v, (int, float)) and not isinstance(v, bool) and not re.search(r"hole|fillet|chamfer", k):
            bits.append(f"{k[:-3].replace('_', ' ')} {v:g} mm")
        if len(bits) == 4:
            break
    return ("Main sizes: " + ", ".join(bits) + ".") if bits else ""


def describe(target: dict, steps: list[Step], k: int, *, first: bool = False, edited: str | None = None) -> str:
    name = nice_name(str(target.get("part_type")))
    n = len(steps)
    if edited:
        lead = f"{edited} Step {k} of {n} is showing: {steps[k - 1].title}."
    elif first:
        lead = f"Step 1 of {n} for your {name}: {steps[0].title}. {steps[0].why}"
    else:
        lead = f"Step {k} of {n}: added {steps[k - 1].title}. {steps[k - 1].why}"
    bits = [lead]
    if first and _sizes(target):
        bits.append(_sizes(target))
    if k < n:
        bits.append(f"Next is {steps[k].title}. Say next to add it, say finish to add everything, or tell me what to "
                    "change.")
        bits.append("This is a free preview and nothing is charged until you press Generate, which makes what is showing.")
    else:
        bits.append("That is every step. Tell me what to change, or press Generate to make the files.")
    return " ".join(b for b in bits if b)


# ---------------------------------------------------------------------------------------- the three moves
def start(plan: Any) -> Any:
    """A new build the person asked for in steps: show step 1 and explain it. The plan is returned as it was when the
    design has nothing to split."""
    try:
        if plan.intent != "build" or not isinstance(plan.spec, dict):
            return plan
        target = _plain(plan.spec)
        steps = steps_for(target)
        if not steps:
            return plan
        plan.spec = view_at(target, steps, 1)
        plan.reply = describe(target, steps, 1, first=True)
        plan.changes = []
        return plan
    except Exception:  # noqa: BLE001
        logger.exception("could not start steps")
        return plan


_NEXT = re.compile(r"^\W*(?:continue\W*)?(next(?: step)?|continue|go on|go ahead|keep going|carry on|yes|yep|yeah|ok|okay|"
                   r"sure|looks good|good|more|proceed|add (?:it|that|the next (?:part|step|one)))\W*$", re.I)
_FINISH = re.compile(r"\b(finish|everything|the rest|all of it|whole thing|all (?:the )?(?:parts|steps)|complete it|"
                     r"build it all|add all)\b", re.I)


def advance_turn(text: str, current: dict | None):
    """"next" or "finish" on a staged build: the next view, free, no AI. None when this is not that."""
    try:
        st = stage_of(current)
        if st is None:
            return None
        t = (text or "").strip()
        to_end = bool(_FINISH.search(t)) and len(t.split()) <= 6
        if not (to_end or _NEXT.match(t)):
            return None
        target = st["target"]
        steps = steps_for(target)
        if not steps:
            return None
        k = len(steps) if to_end else min(len(steps), st["k"] + 1)
        from conversation import Plan
        added = [f"added {steps[i].title}" for i in range(st["k"], k)]
        plan = Plan("edit", describe(target, steps, k, first=False), view_at(target, steps, k), "rules", changes=added)
        plan.stage_move = True
        return plan
    except Exception:  # noqa: BLE001
        logger.exception("could not advance steps")
        return None


def restage(plan: Any, current: dict | None) -> Any:
    """A change typed mid-way was planned against the finished design. Cut the view again at the same step."""
    try:
        st = stage_of(current)
        if st is None or plan.intent != "edit" or not isinstance(plan.spec, dict) or getattr(plan, "stage_move", False):
            return plan
        target = _plain(plan.spec)
        if target.get("part_type") != st["target"].get("part_type"):
            return plan                                         # it became a different part: staging is over
        steps = steps_for(target)
        if not steps:
            plan.spec = target
            return plan
        k = min(st["k"], len(steps))
        plan.spec = view_at(target, steps, k)
        # What the person changed, and whether it belongs to a step that has not arrived yet.
        old_p, new_p = st["target"].get("parameters") or {}, target.get("parameters") or {}
        changed = {x for x in set(old_p) | set(new_p) if old_p.get(x, _MISSING) != new_p.get(x, _MISSING)}
        later = {x for step in steps[k:] for x in step.adds}
        if changed and changed <= later:
            plan.reply = (plan.reply.rstrip() + " It will show when its step arrives.").strip()
        if k < len(steps):
            plan.reply = plan.reply.rstrip() + f" Step {k} of {len(steps)} is showing. Say next to add {steps[k].title}."
        return plan
    except Exception:  # noqa: BLE001
        logger.exception("could not re-cut steps")
        return plan


def keep_stage(planned: dict | None, built: dict) -> dict:
    """The generator keeps four fields. Put the stage back on the spec that is saved with the part. Never raises."""
    try:
        st = stage_of(planned)
        if st is not None and isinstance(built, dict):
            built = {**built, STAGE_KEY: copy.deepcopy(st)}
    except Exception:  # noqa: BLE001
        pass
    return built

"""
Whole vehicles and machines in the studio chat.

    "I want to create a wheelbarrow"  ->  the vehicle planner writes a spec, the chat shows the plan,
    "make the wheel 400 mm"           ->  the spec is revised and the preview rebuilt, still free.

How it plugs in
  conversation._plan_core calls plan_vehicle_turn() before the mechanical planner. It returns None when the
  message is not about a vehicle, so every other chat behaves exactly as before.

What a vehicle is inside the chat
  An ordinary `assembly` part (layout "custom", every part with a resolved position), so the existing preview,
  Generate, refund, BOM, mass and interference code all apply unchanged. Its parameters also carry
  `vehicle`: the vehicle spec the plan came from. That is what an edit starts from, and what the exploded view and
  the package are built from. (chat_service.restore_vehicle puts it back after the generator, which keeps only the
  parts list.)

Credits
  Nothing here charges. Planning and editing are free previews. A plan that an AI wrote is reported with
  source "llm", so chat_service counts it against the AI allowance exactly like any other AI message.
  Only Generate (chat_service.unlock_part) spends credits.

Not verified: no geometry is built here, and a valid plan is not a load-rated or safe design.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

import spec_library as SL
import vehicle_planner as VP
import vehicle_spec as VS

ROOT = Path(__file__).resolve().parent
LLM = Callable[[list[dict[str, str]]], str]
BBoxFn = Callable[[dict[str, Any]], tuple[float, float, float, float, float, float]]

# One word -> the kind of vehicle it names. Whole vehicles only: "bicycle crank" is a part, not a bicycle.
_KINDS = {
    "wheelbarrow": "wheelbarrow", "barrow": "wheelbarrow",
    "bicycle": "bicycle", "bike": "bicycle", "tricycle": "tricycle", "trike": "tricycle",
    "helicopter": "helicopter", "heli": "helicopter", "chopper": "helicopter",
    "locomotive": "locomotive", "loco": "locomotive", "train": "locomotive",
    "cart": "cart", "trolley": "cart", "wagon": "cart", "trailer": "cart",
    "vehicle": "vehicle", "buggy": "vehicle", "scooter": "scooter", "go-kart": "vehicle", "gokart": "vehicle",
    "car": "car", "sedan": "car", "truck": "truck", "lorry": "truck", "pickup": "truck",
    "bin": "bin", "dustbin": "bin", "wheelie bin": "bin",
    "airplane": "airplane", "aeroplane": "airplane", "plane": "airplane", "aircraft": "airplane", "jet": "airplane",
    "engine": "engine",
}
_KIND_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _KINDS), key=len, reverse=True)) + r")s?\b", re.I)
_COMPONENT_AFTER = re.compile(
    r"\b(?:" + "|".join(sorted(map(re.escape, _KINDS), key=len, reverse=True)) + r")s?[\s-]+"
    r"(?:frame|crank|hub|bracket|pedal|handle|handlebar|wheel|gear|sprocket|chain|seat|saddle|fork|axle|bearing|part|"
    r"component|tyre|tire|brake|spoke|rim|bell|basket|rack|mount|mounts|holder|housing|cover|lid|plate|adapter|clip|hook|cap|"
    r"door|panel|block|piston|valve|head|gasket|cylinder|flange|pump|filter|key|keys|seal|wiper|jack|stand|ramp)\b", re.I)
_MAKE = re.compile(r"\b(creat\w*|make|build|design|draw|model|generate|plan|want|need|wish|start|sketch|prepare|draft|"
                   r"construct|assemble|show me|give me|get me)\b|^\s*(?:an?|the)\s", re.I)
_EDIT_VERB = re.compile(r"\b(make|change|set|add|remove|delete|use|increase|reduce|decrease|resize|replace|swap|move|put|give|"
                        r"raise|lower|lengthen|shorten|widen|narrow|enlarge|shrink|drop|include|without|extend|bigger|"
                        r"smaller|longer|shorter|wider|taller|thicker|thinner)\b", re.I)
_QUESTION = re.compile(r"\?\s*$|^\s*(what|why|how|which|who|where|when|is|are|does|do|will|would|should|can|could)\b", re.I)
_FORCED = re.compile(r"^\s*(?:vehicle|machine)\s*:\s*(\S.*)$", re.I | re.S)
_NEW = re.compile(r"\b(new|another|different|start over|from scratch|instead)\b", re.I)
_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|millimet\w*|cm|centimet\w*|m|metres?|meters?|in|inch\w*|\"|kg|g|deg\w*|°|%)?", re.I)

# ------------------------------------------------------------------ test and deployment hooks
_lock = threading.Lock()
_library: SL.SpecLibrary | None = None
_bbox_fn: BBoxFn | None = None
_module_dir: Path | None = None


def set_library(lib: SL.SpecLibrary | None) -> None:
    global _library
    _library = lib


def set_bbox_fn(fn: BBoxFn | None) -> None:
    """Bounding boxes of single parts, used to place mated parts. None means: use CadQuery if installed."""
    global _bbox_fn
    _bbox_fn = fn


def _setting(name: str, default: Any) -> Any:
    """A value from config.settings, else the environment, else the default. The vehicle code must run without the
    web app's config (tests, the review tool), so a missing config is never an error."""
    try:
        from config import settings as _s
        v = getattr(_s, name, None)
        if v is not None:
            return v
    except Exception:  # noqa: BLE001
        pass
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() not in ("0", "false", "no", "off")
    try:
        return type(default)(raw)
    except (TypeError, ValueError):
        return default


def _plan_budget() -> float:
    """Seconds one planning message may take in total (VEHICLE_PLAN_TIME_BUDGET_S, default 120)."""
    return float(_setting("VEHICLE_PLAN_TIME_BUDGET_S", VP.DEFAULT_TIME_BUDGET_S))


def get_library() -> SL.SpecLibrary:
    """The shared spec library. A folder on disk when VEHICLE_LIBRARY_DIR (default data/spec_library) can be
    written, otherwise in memory (serverless hosts wipe the disk, so nothing learned there would last)."""
    global _library
    with _lock:
        if _library is None:
            store: SL.Store
            try:
                try:
                    from config import settings as _settings
                    configured = _settings.VEHICLE_LIBRARY_DIR
                except Exception:  # noqa: BLE001 - config is not needed to plan a vehicle
                    configured = ""
                folder = Path(configured or os.environ.get("VEHICLE_LIBRARY_DIR") or (ROOT / "data" / "spec_library"))
                folder.mkdir(parents=True, exist_ok=True)
                probe = folder / ".write_test"
                probe.write_text("x")
                probe.unlink()
                store = SL.FileStore(folder)
            except OSError:
                store = SL.MemoryStore()
            # A thumbs-up or Generate queues a design for review instead of making it an example other people's prompts
            # see (VEHICLE_LIBRARY_REVIEW, default on). Review with `python spec_library.py pending`.
            lib = SL.SpecLibrary(store, review_required=bool(_setting("VEHICLE_LIBRARY_REVIEW", True)))
            try:
                lib.seed_from_dir(ROOT / "vehicles" / "examples")
            except Exception:  # noqa: BLE001 - a missing examples folder must not break the chat
                pass
            _library = lib
        return _library


# ------------------------------------------------------------------------------ recognising a request
def vehicle_of(spec: Any) -> dict[str, Any] | None:
    """The vehicle spec carried by a chat part spec, or None."""
    if not isinstance(spec, dict) or spec.get("part_type") != "assembly":
        return None
    v = (spec.get("parameters") or {}).get("vehicle")
    return v if isinstance(v, dict) else None


def kind_of(text: str) -> str | None:
    m = _KIND_RE.search(text or "")
    return _KINDS[m.group(1).lower()] if m else None


def is_vehicle_request(text: str) -> bool:
    """A message that asks for a whole vehicle: names one and says to make it. Part names do not count."""
    t = text or ""
    if kind_of(t) is None or _COMPONENT_AFTER.search(t):
        return False
    return bool(_MAKE.search(t))


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[_\s]+", re.sub(r"_(mm|deg|kg|g|pct)$", "", name.lower())) if w]


# ------------------------------------------------------------------------------ spec -> chat part
def to_chat_spec(vspec: dict[str, Any], overrides: dict[str, Any] | None = None, *,
                 bbox_fn: BBoxFn | None = None, module_dir: Path | None = None,
                 library_id: str | None = None, stage: int | None = None) -> dict[str, Any]:
    """Expand a vehicle spec into the chat's assembly part. Returns {"ok", "spec", "errors", "notes", "expanded"}.
    With `stage` (1, 2, ...) only the parts of the first `stage` build steps are placed (see stage_plan); the result
    also carries "stage_plan" and, while steps remain, "stage": {"shown", "of"}."""
    ex = VS.expand_vehicle(vspec, overrides, None, module_dir or _module_dir)
    if not ex["ok"]:
        return {"ok": False, "errors": ex["errors"], "notes": [], "spec": None, "expanded": ex}
    parts = [dict(p) for p in ex["parts"]]
    notes: list[str] = []
    if any(p.get("mate") for p in parts):
        fn = bbox_fn or _bbox_fn
        if fn is None:
            import vehicle_build as VB
            fn = VB.kernel_bbox_fn()
        if fn is None:
            return {"ok": False, "notes": [], "spec": None, "expanded": ex,
                    "errors": ["Some parts are placed by how they connect, and that needs the CAD kernel, "
                               "which is not available on this server."]}
        import mates as M
        try:
            parts = M.resolve_mates(parts, lambda p: fn(p))
        except Exception as exc:  # noqa: BLE001 - MateError or a kernel error
            return {"ok": False, "notes": [], "spec": None, "expanded": ex,
                    "errors": [f"The connections could not be solved: {str(exc)[:200]}"]}
    if ex["external"]:
        names = ", ".join(e["name"] for e in ex["external"][:4])
        notes.append(f"{len(ex['external'])} downloaded part(s) ({names}) are not in the preview. They are added "
                     "to the package when you press Generate.")
    splan = stage_plan(vspec, ex) if stage is not None else []
    stage_info: dict[str, int] | None = None
    if splan:
        k = max(1, min(int(stage), len(splan)))
        if k < len(splan):
            shown = {n for _, members in splan[:k] for n in members}
            parts = [p for p in parts if p["name"] in shown]
            stage_info = {"shown": k, "of": len(splan)}
    out = []
    for p in parts:
        pos = p.get("position")
        if pos is None:
            return {"ok": False, "notes": [], "spec": None, "expanded": ex,
                    "errors": [f"Part {p.get('name')} has no position."]}
        item = {"name": p["name"], "part_type": p["part_type"], "parameters": p.get("parameters") or {},
                "position": [float(v) for v in pos], "rotation": [float(v) for v in (p.get("rotation") or (0, 0, 0))]}
        if isinstance(p.get("material"), str) and p["material"]:
            item["material"] = p["material"]          # the parts list and mass on the drawing read this
        out.append(item)
    if len(out) < 2:
        return {"ok": False, "notes": [], "spec": None, "expanded": ex,
                "errors": ["A vehicle needs at least two parts."]}
    spec = {"part_type": "assembly", "material": None, "operations": [],
            "parameters": {"layout": "custom", "parts": out, "vehicle": copy.deepcopy(vspec),
                           "name": str(vspec.get("name") or "vehicle")[:40]}}
    if isinstance(library_id, str) and re.match(r"^[a-z0-9_]+$", library_id):
        spec["parameters"]["library_id"] = library_id        # which library record this came from (see on_generated)
    if stage_info:
        spec["parameters"]["stage"] = stage_info
    return {"ok": True, "spec": spec, "errors": [], "notes": notes, "expanded": ex, "stage_plan": splan,
            "stage": stage_info}


def restore_vehicle(planned: dict[str, Any] | None, built: dict[str, Any]) -> dict[str, Any]:
    """The generator stores only the parts list. Put the vehicle spec back on the spec that is saved with the part."""
    v = vehicle_of(planned)
    if v is not None and isinstance(built.get("parameters"), dict):
        built["parameters"] = {**built["parameters"], "vehicle": v,
                               "name": (planned["parameters"].get("name") or v.get("name") or "vehicle")}
        lid = planned["parameters"].get("library_id")
        if isinstance(lid, str):
            built["parameters"]["library_id"] = lid
        st = planned["parameters"].get("stage")
        if isinstance(st, dict):
            built["parameters"]["stage"] = dict(st)
    return built


# ------------------------------------------------------------------------------ learning from Generate
def on_generated(spec: dict[str, Any] | None, library: SL.SpecLibrary | None = None) -> bool:
    """Pressing Generate is the clearest sign someone liked a version, so the library learns from it. If the version
    is the AI plan as it was first saved, that record is marked accepted. If the person changed it by chatting, the
    changed version is added as its own record and accepted. Seeds keep their status. Nothing the person typed is
    stored. Never raises: a library problem must not touch Generate."""
    try:
        v = vehicle_of(spec)
        if v is None:
            return False
        lib = library or get_library()
        lid = (spec.get("parameters") or {}).get("library_id")
        rec = lib.get(lid) if isinstance(lid, str) else None
        if rec is not None and rec.get("spec") == v:
            return bool(lib.feedback(lid, True).get("ok"))
        added = lib.add(v, "", status="candidate", source="generated")
        if not added.get("ok"):
            return False
        return bool(lib.feedback(added["id"], True).get("ok"))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------------------ building in steps
# Which parts belong to which step, by the start of the part's name. A part that matches nothing joins the last step.
_STAGES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "wheelbarrow": [("the tray and handles", ("tray", "rail", "handle")), ("the legs and crossbars", ("leg", "cross")),
                    ("the fork and wheel", ("fork", "front_wheel", "wheel"))],
    "engine": [("the crankshaft and flywheel", ("engine_crank", "engine_flywheel")),
               ("the rods and pistons", ("engine_rod", "engine_pin", "engine_piston")),
               ("the cylinders and head", ("engine_liner", "engine_head"))],
}
_NEXT = re.compile(r"^\W*(?:continue\W*)?(next(?: step)?|continue|go on|go ahead|keep going|carry on|yes|yep|yeah|ok|okay|"
                   r"sure|looks good|good|more|proceed|add (?:it|that|the next (?:part|step|one)))\W*$", re.I)
_FINISH = re.compile(r"\b(finish|everything|the rest|all of it|whole thing|all (?:the )?(?:parts|steps)|complete it|"
                     r"build it all|add all)\b", re.I)


# Build order for the groups of a vehicle with no stage table of its own: the running gear a beginner starts with,
# then the body, then what goes inside it, then the finishing. A group that matches nothing keeps its place after these.
_GROUP_ORDER = (r"chassis|frame|ladder|subframe", r"wheel|axle|tyre|tire|suspension|steer|brake", r"body|hull|shell|cabin|fuselage|bodywork|door|hood|panel",
                r"powertrain|engine|motor|gearbox|transmission|driveline|drive", r"exhaust|fuel|tank|cooling",
                r"interior|seat|cockpit", r"trim|glazing|glass|lights?|details?|finish")


def _group_rank(name: str, motor: bool = True) -> float:
    """Position of a group in the build order. Wheels go first only on a vehicle with a motor (a car, a truck): a
    wheelie bin or a cart is built tub first, wheels after."""
    for i, pat in enumerate(_GROUP_ORDER):
        if re.search(pat, str(name), re.I):
            return 2.5 if (i == 1 and not motor) else i
    return 2.2                  # an unknown group is built with the body, before the inside and the finishing


def stage_plan(vspec: dict[str, Any], expanded: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """The build steps of a vehicle as [(title, [part names])]. Every step has at least two parts, because a chat
    assembly needs two. Empty when the vehicle is too small to split."""
    names = [str(p["name"]) for p in expanded.get("parts") or []]
    kind = kind_of(str(vspec.get("name") or "").replace("_", " "))
    plan: list[tuple[str, list[str]]] = []
    left = list(names)
    for title, prefixes in _STAGES.get(kind or "", []):
        hit = [n for n in left if n.startswith(prefixes)]
        if hit:
            plan.append((title, hit))
            left = [n for n in left if n not in hit]
    if plan and left:
        plan[-1] = (plan[-1][0], plan[-1][1] + left)
    if len(plan) < 2:                                          # no table for this kind: one step per group
        plan, seen = [], set()
        asm = expanded.get("assemblies") or {}
        motor = any(re.search(r"powertrain|engine|gearbox|motor", g, re.I) for g in asm)
        groups = sorted(asm.items(), key=lambda kv: _group_rank(kv[0], motor))                           # stable
        for g, members in groups:
            mem = [m for m in members if m in names and m not in seen]
            seen.update(mem)
            if mem:
                plan.append((f"the {str(g).replace('_', ' ')}", mem))
        rest = [n for n in names if n not in seen]
        if rest and plan:
            plan[-1] = (plan[-1][0], plan[-1][1] + rest)
    merged: list[tuple[str, list[str]]] = []
    for title, members in plan:
        if merged and len(merged[-1][1]) < 2:                  # too small to stand alone: join it to the next
            t0, m0 = merged[-1]
            merged[-1] = (t0 + " and " + title.removeprefix("the "), m0 + members)
        else:
            merged.append((title, list(members)))
    if len(merged) > 1 and len(merged[-1][1]) < 2:
        t0, m0 = merged.pop()
        merged[-1] = (merged[-1][0] + " and " + t0.removeprefix("the "), merged[-1][1] + m0)
    return merged if len(merged) > 1 else []


def describe_stage(vspec: dict[str, Any], expanded: dict[str, Any], splan: list[tuple[str, list[str]]], k: int, *,
                   changes: list[str] | None = None, notes: list[str] | None = None, first: bool = False) -> str:
    name = str(vspec.get("name") or "vehicle").replace("_", " ")
    total = len(expanded.get("parts") or [])
    shown = sum(len(m) for _, m in splan[:k])
    if changes:
        lead = f"Updated the {name}: " + "; ".join(changes[:3]) + f". Step {k} of {len(splan)} is showing, {shown} of {total} parts."
    elif first:
        lead = f"Step 1 of {len(splan)} for your {name}: {splan[0][0]}. {shown} of {total} parts so far."
    else:
        lead = f"Step {k} of {len(splan)}: added {splan[k - 1][0]}. {shown} of {total} parts so far."
    bits = [lead]
    p = _param_defaults(vspec)
    if first and p:
        bits.append("Main sizes: " + ", ".join(f"{' '.join(_words(n))} {_fmt(v)}" for n, v in list(p.items())[:6]) + ".")
    for n in (notes or [])[:2]:
        bits.append(n)
    vn = str(vspec.get("notes") or "").strip()
    if vn and first:
        bits.append("Note: " + vn[:160] + ("..." if len(vn) > 160 else ""))
    bits.append(f"Next is {splan[k][0]}. Say next to add it, say finish to add everything, or tell me what to change, "
                "for example \"make the wheel 400 mm\".")
    bits.append("This is a free preview and nothing is charged until you press Generate, which makes the parts shown.")
    return " ".join(bits)


# ------------------------------------------------------------------------------ talking about a plan
def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:g}" if abs(v) < 1e6 else f"{v:.3g}"
    return str(v)


def _param_defaults(vspec: dict[str, Any]) -> dict[str, float]:
    out = {}
    for k, v in (vspec.get("params") or {}).items():
        d = v.get("default") if isinstance(v, dict) else v
        if isinstance(d, (int, float)) and not isinstance(d, bool):
            out[k] = float(d)
    return out


def describe_plan(vspec: dict[str, Any], expanded: dict[str, Any], *, edited: bool = False,
                  changes: list[str] | None = None, unresolved: list[dict[str, str]] | None = None,
                  notes: list[str] | None = None, from_example: bool = False) -> str:
    name = str(vspec.get("name") or "vehicle").replace("_", " ")
    groups = expanded.get("assemblies") or {}
    n_parts = len(expanded.get("parts") or [])
    lead = (f"Updated the {name}: " + "; ".join((changes or [])[:3]) + "." if edited and changes
            else f"Here is the plan for your {name}: {n_parts} parts" +
            (" in " + ", ".join(f"{g} ({len(m)})" for g, m in list(groups.items())[:6]) if groups else "") + ".")
    bits = [lead]
    p = _param_defaults(vspec)
    if p and not edited:
        shown = list(p.items())[:6]
        bits.append("Main sizes: " + ", ".join(f"{' '.join(_words(k))} {_fmt(v)}" for k, v in shown) + ".")
    checks = expanded.get("checks") or []
    if checks:
        failing = [c for c in checks if c["status"] != "pass"]
        bits.append(f"{len(checks) - len(failing)} of {len(checks)} design checks pass." if failing
                    else f"All {len(checks)} design checks pass.")
    for u in (unresolved or [])[:2]:
        bits.append(f"Still failing: {u['name']} ({(u.get('detail') or '')[:80]}).")
    if from_example:
        bits.append("This is the built-in example. Pick an AI below to have it fitted to your description.")
    for n in (notes or [])[:2]:
        bits.append(n)
    vn = str(vspec.get("notes") or "").strip()
    if vn and not edited:
        bits.append("Note: " + vn[:160] + ("..." if len(vn) > 160 else ""))
    bits.append("This is a free preview and nothing is charged until you press Generate. "
                "Tell me what to change, for example \"make the wheel 400 mm\" or \"add a second axle\".")
    return " ".join(bits)


def diff_vehicle(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Plain-words changes between two vehicle specs."""
    out: list[str] = []
    a, b = _param_defaults(old), _param_defaults(new)
    for k in b:
        if k in a and abs(a[k] - b[k]) > 1e-9:
            out.append(f"{' '.join(_words(k))} {_fmt(a[k])} to {_fmt(b[k])}")
        elif k not in a:
            out.append(f"new size {' '.join(_words(k))} {_fmt(b[k])}")
    for k in a:
        if k not in b:
            out.append(f"removed size {' '.join(_words(k))}")
    ids = lambda s: {str(c.get("id")) for c in (s.get("components") or []) if isinstance(c, dict)}  # noqa: E731
    for i in sorted(ids(new) - ids(old)):
        out.append(f"added {i.replace('_', ' ')}")
    for i in sorted(ids(old) - ids(new)):
        out.append(f"removed {i.replace('_', ' ')}")
    if not out and old != new:
        out.append("adjusted the layout")
    return out


# ------------------------------------------------------------------------------ rules (no AI)
_ABBR = {"w": "width", "l": "length", "h": "height", "d": "diameter", "dia": "diameter", "len": "length",
         "od": "outer", "id": "inner", "wt": "wall", "t": "thickness"}


def _word_hits(word: str, token: str) -> int:
    """2 for the same word, 1 for an abbreviation or a prefix of it (dia / diameter), else 0."""
    if word == token:
        return 2
    full = _ABBR.get(word)
    if full and (token == full or token.startswith(full)):
        return 1
    return 1 if len(word) >= 3 and token.startswith(word) else 0


_DIM_WORD = {"long": "length", "wide": "width", "tall": "height", "high": "height", "thick": "thickness", "deep": "depth"}
_RELATIVE = re.compile(r"\b(increase|decrease|raise|lower|reduce)\b.*\bby\b", re.I)
_DOWN = {"decrease", "lower", "reduce"}
# Words that may sit in a size-only sentence besides the setting's own name. Anything else ("add", "second",
# "with"...) means the message asks for more than a size, and the free path leaves it to the model.
_FILLER = {"the", "a", "an", "to", "of", "is", "be", "it", "its", "my", "at", "as", "so", "up", "down", "please", "can", "you",
           "could", "would", "now", "just", "also", "and", "then", "set", "make", "change", "resize", "adjust", "increase",
           "decrease", "raise", "lower", "reduce", "by", "mm", "cm", "m", "in", "inch", "inches", "millimetre", "millimetres",
           "millimeter", "millimeters", "centimetre", "centimetres", "centimeter", "centimeters", "metre", "metres", "meter",
           "meters", "long", "wide", "tall", "high", "thick", "deep", "length", "width", "height", "thickness", "depth"}


def _edit_rules(text: str, vspec: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str], int, int]:
    """Set named sizes from a sentence, no AI. Returns (new spec, changes, problems, unclear, understood).
    `unclear` counts clauses that are not a plain size edit (so the whole message is more than sizes);
    `understood` counts clauses that are one, whether or not they changed anything."""
    new = copy.deepcopy(vspec)
    changes: list[str] = []
    problems: list[str] = []
    unclear = understood = 0
    params = new.get("params") or {}
    for clause in re.split(r"\s*(?:,|;|\band\b|\bthen\b)\s*", text or "", flags=re.I):
        if not clause.strip():
            continue
        low = clause.lower()
        nums = list(_NUMBER.finditer(low))
        tokens = re.findall(r"[a-z]+", low)
        if not nums or not tokens:
            unclear += 1
            continue
        tokens = tokens + [_DIM_WORD[t] for t in tokens if t in _DIM_WORD]          # "900 long" also says "length"
        scored: list[tuple[int, str]] = []
        for k in params:
            words = _words(k)
            if not words:
                continue
            hits = []
            for w in words:
                best = max((_word_hits(w, t) for t in tokens), default=0)
                hits.append(best)
            if all(hits):
                scored.append((sum(hits) + 10 * len(words), k))
        if not scored:
            unclear += 1
            continue
        top = max(sc for sc, _ in scored)
        winners = [k for sc, k in scored if sc == top]
        if len(winners) > 1:
            problems.append("which size do you mean: " + ", ".join(' '.join(_words(k)) for k in winners[:4]))
            understood += 1
            continue
        k = winners[0]
        own = _words(k)
        if any(t not in _FILLER and not any(_word_hits(w, t) for w in own) for t in tokens):
            unclear += 1                                   # still applied below if the AI is unavailable
            plain = False
        else:
            plain = True
        m = nums[-1]
        val, unit = float(m.group(1)), (m.group(2) or "").lower()
        rel = _RELATIVE.search(low)
        if unit == "%" and not rel:
            if plain:
                unclear += 1
            continue                                       # "make it 10%" has no meaning here
        if unit.startswith("c") and unit != "c":
            val *= 10
        elif unit in ("m", "metre", "metres", "meter", "meters"):
            val *= 1000
        elif unit.startswith("in") or unit == '"':
            val *= 25.4
        spec = params[k]
        cur = spec.get("default") if isinstance(spec, dict) else spec
        if rel:
            if not isinstance(cur, (int, float)) or isinstance(cur, bool):
                unclear += 1
                continue
            delta = cur * val / 100.0 if unit == "%" else val
            val = cur - delta if rel.group(1).lower() in _DOWN else cur + delta
        val = round(val, 6)
        lo = spec.get("min") if isinstance(spec, dict) else None
        hi = spec.get("max") if isinstance(spec, dict) else None
        if isinstance(spec, dict) and spec.get("kind") == "int":
            if abs(val - round(val)) > 1e-9:
                problems.append(f"{' '.join(_words(k))} must be a whole number")
                understood += plain
                continue
            val = float(round(val))
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            problems.append(f"{' '.join(_words(k))} must stay between {_fmt(lo)} and {_fmt(hi)}")
            understood += plain
            continue
        understood += plain
        if isinstance(spec, dict):
            spec["default"] = val
        else:
            params[k] = val
        if cur != val:
            changes.append(f"{' '.join(_words(k))} {_fmt(float(cur))} to {_fmt(val)}")
    return new, changes, problems, unclear, understood


def apply_param_edit_rules(text: str, vspec: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Set a named size from a sentence: "make the wheel diameter 400 mm", "make the tray 900 long",
    "increase the tray length by 10%" (sizes are often short, wheel_dia). Every word of the size's name must appear.
    If two sizes match equally well nothing is changed and the problem says which.
    Returns (new spec, changes, problems)."""
    new, changes, problems, _unclear, _understood = _edit_rules(text, vspec)
    return new, changes, problems


# ------------------------------------------------------------------------------ asking before building
# "I want to build a wheelbarrow" is the start of a conversation, not a full order. With little to go on, the chat
# asks one short question at a time (tappable) and says where it would start. Once there is enough detail, or the
# person says to just build it, the vehicle is planned. Asking is free: no AI is called and nothing is charged.
_SCOPE_LEAD = "Before I build"
_ENOUGH = 3                      # concrete details (sizes, a material, a count) that count as a full order
_MAX_ROUNDS = 5                  # user messages in one scoping chat before it builds whatever it has
_SKIP = re.compile(r"\b(just (?:build|make|do) it|go ahead|you (?:decide|choose|pick)|surprise me|use (?:the )?defaults?|"
                   r"don'?t ask|no questions|skip (?:the |these |your )?questions|build it now|build it all|"
                   r"whatever you (?:like|think))\b", re.I)
_MATERIAL = re.compile(r"\b(steel|aluminium|aluminum|plastic|wood\w*|timber|titanium|carbon|fibre|fiber|abs|pla|nylon|"
                       r"brass|copper|iron|stainless|glass|rubber)\b", re.I)
_COUNT = re.compile(r"\b(single|one|two|twin|three|four|five|six|eight|dual|double|triple|\d+)[\s-]+"
                    r"(?:wheel\w*|axle\w*|cylinder\w*|seat\w*|door\w*|rotor\w*|blade\w*|engine\w*|wing\w*|compartment\w*|"
                    r"lid\w*|propeller\w*)\b", re.I)
_DIGITS = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?")

_INSIDE = ("Should the inside be modelled too?", ["Yes, engine and gearbox too", "Outer shape only"])
_GENERIC = [("What will it be used for?", ["Carrying loads", "Carrying people", "A display model"]),
            ("How big should it be?", ["Small, about 300 mm long", "Medium, about 1 m long", "Large, about 3 m long"])]
_SCOPE: dict[str, dict[str, Any]] = {
    "wheelbarrow": {"start": "the tray and handles first, then the legs, then the fork and wheel", "q": [
        ("What size should it be?", ["Small, about 40 litre", "Garden, about 90 litre", "Builder's, about 120 litre"]),
        ("What is it made of?", ["Steel tray and steel tube frame", "Plastic tray on a steel frame"]),
        ("How many wheels?", ["One wheel", "Two wheels"])]},
    "bicycle": {"start": "the frame first, then the wheels and fork, then the drivetrain", "q": [
        ("What kind of bike?", ["Road bike", "Mountain bike", "City bike"]),
        ("What is the frame made of?", ["Steel frame", "Aluminium frame"]),
        ("What wheel size?", ["700c wheels", "26 inch wheels", "20 inch wheels"])]},
    "helicopter": {"start": "the body and tail first, then the rotor, then the engine inside", "q": [
        ("How big is it?", ["RC model, about 600 mm rotor", "Drone size, about 300 mm rotor", "Small cabin, about 7 m rotor"]),
        ("How many rotors?", ["One main rotor", "Two main rotors"]), _INSIDE]},
    "car": {"start": "the chassis and wheels first, then the body, then the engine and gearbox", "q": [
        ("What kind of car?", ["Small city car", "Sedan", "Pickup"]),
        ("How big is it?", ["Toy, about 300 mm long", "Model, about 450 mm long", "Full size, about 4 m long"]), _INSIDE]},
    "truck": {"start": "the chassis and wheels first, then the cab, then the load bed and engine", "q": [
        ("What kind of truck?", ["Pickup", "Box truck", "Tipper"]),
        ("How big is it?", ["Toy, about 300 mm long", "Model, about 450 mm long", "Full size, about 6 m long"]), _INSIDE]},
    "bin": {"start": "the body first, then the lid, then the handle and wheels", "q": [
        ("What kind of bin?", ["Kitchen bin with a lid", "Wheelie bin", "Recycling bin with compartments"]),
        ("What size is it?", ["About 30 litre", "About 120 litre", "About 240 litre"]),
        ("What is it made of?", ["Plastic", "Steel"])]},
    "airplane": {"start": "the fuselage first, then the wings and tail, then the engine inside", "q": [
        ("What kind of aircraft?", ["Small prop plane", "RC model plane", "Jet airliner"]),
        ("What wingspan?", ["About 1 m", "About 10 m", "About 35 m"]), _INSIDE]},
    "engine": {"start": "the crankshaft first, then the rods and pistons, then the cylinders and head", "q": [
        ("What kind of engine?", ["Single cylinder", "Two cylinder inline", "Four cylinder inline"]),
        ("How big is it?", ["Tiny, bore about 20 mm", "Small, bore about 30 mm", "Car size, bore about 85 mm"]),
        ("What is it made of?", ["Steel and aluminium", "All steel"])]},
}
_ALL_QUESTIONS = sorted({q for v in list(_SCOPE.values()) + [{"q": _GENERIC}] for q, _ in v["q"]}, key=len, reverse=True)


def detail_score(text: str) -> int:
    """How many concrete details a message holds: up to 3 numbers, a material, a count of parts."""
    t = text or ""
    return (min(len(set(_DIGITS.findall(t))), 3) + (1 if _MATERIAL.search(t) else 0) + (1 if _COUNT.search(t) else 0))


def _scope_chain(history: list[dict[str, Any]]) -> list[str]:
    """The user messages of the scoping chat that ends the history, oldest first. A scoping chat is a request followed
    by our questions and the person's answers; any other assistant message ends it."""
    msgs, i, chain = list(history or []), len(history or []) - 1, []
    while i >= 0 and msgs[i].get("role") == "user":
        chain.append(str(msgs[i].get("content") or ""))
        if i >= 1 and msgs[i - 1].get("role") == "assistant" and str(msgs[i - 1].get("content") or "").startswith(_SCOPE_LEAD):
            i -= 2
        else:
            break
    chain.reverse()
    return chain


def scope_turn(history: list[dict[str, Any]], current_spec: dict[str, Any] | None):
    """None when the normal vehicle flow should handle the message (not a vehicle request, an edit to a vehicle on the
    bench, or a request detailed enough to build as it stands). Otherwise ("ask", Plan) with one tappable question, or
    ("build", description, staged) once the chat has enough, where staged says the build should come in steps.
    Never raises."""
    try:
        return _scope(history, current_spec)
    except Exception:  # noqa: BLE001 - a scoping problem must never break the chat; build as before
        import logging
        logging.getLogger(__name__).exception("vehicle scoping failed")
        return None


def _scope(history, current_spec):
    if vehicle_of(current_spec) is not None:
        return None                                       # editing a vehicle that exists: no questions
    chain = _scope_chain(history)
    if not chain:
        return None
    forced = bool(_FORCED.match(chain[0]))
    first = (_FORCED.match(chain[0]).group(1) if forced else chain[0]).strip()
    if len(chain) == 1 and not (forced or is_vehicle_request(first)):
        return None
    kind = kind_of(first)
    if kind is None:
        return None                                       # an unnamed machine goes to the planner as it always did
    raw = " ".join(chain)
    desc = raw
    for q in _ALL_QUESTIONS:
        desc = desc.replace(q, " ")
    desc = " ".join((_FORCED.match(desc).group(1) if _FORCED.match(desc) else desc).split())
    if forced:
        desc = "Vehicle: " + desc
    skip = bool(_SKIP.search(desc))
    if len(chain) == 1 and (skip or detail_score(desc) >= _ENOUGH):
        return None                                       # the person gave the full order: build it as before
    questions = (_SCOPE.get(kind) or {"q": _GENERIC})["q"]
    remaining = [qa for qa in questions if qa[0] not in raw]
    if skip or detail_score(desc) >= _ENOUGH or not remaining or len(chain) >= _MAX_ROUNDS:
        return ("build", desc, not skip)
    name = str(kind).replace("_", " ")
    start = (_SCOPE.get(kind) or {}).get("start") or "the main body first, then the moving parts, then the details"
    if len(chain) == 1:
        reply = (f"{_SCOPE_LEAD} the {name}, I need a few choices so it comes out right. I would start with {start}. "
                 "Tap an answer, type your own sizes, or say just build it to skip the questions.")
    else:
        reply = f"{_SCOPE_LEAD} the {name}, one more choice."
    q, opts = remaining[0]
    from conversation import Plan
    return ("ask", Plan("answer", reply, None, "rules", choices=[{"question": q, "options": list(opts)}]))


# ------------------------------------------------------------------------------ the turn
def _plan_obj(intent: str, reply: str, spec: dict | None, source: str, changes: list[str] | None = None):
    from conversation import Plan
    return Plan(intent, reply, spec, source, changes=list(changes or []))


def _answer(reply: str, source: str = "rules"):
    return _plan_obj("answer", reply, None, source)


def _summary_for_model(vspec: dict[str, Any], expanded: dict[str, Any]) -> str:
    checks = [f"{c['name']}: {c['status']}" + (f" ({c['detail']})" if c.get("detail") else "") for c in expanded.get("checks") or []]
    return json.dumps({"name": vspec.get("name"), "notes": vspec.get("notes"), "sizes": _param_defaults(vspec),
                       "groups": {g: len(m) for g, m in (expanded.get("assemblies") or {}).items()},
                       "parts": len(expanded.get("parts") or []), "checks": checks[:30]}, separators=(",", ":"))[:6000]


def _answer_question(text: str, vspec: dict[str, Any], llm: LLM | None):
    ex = VS.expand_vehicle(vspec, None, None, _module_dir)
    if llm is not None and ex["ok"]:
        try:
            reply = llm([
                {"role": "system", "content": "You answer questions about one vehicle design in plain words, in at most "
                 "four sentences. Use only the facts given. Say when something is a simplification. Never call it safe, "
                 "load-rated or flight-worthy. Do not return JSON."},
                {"role": "user", "content": "DESIGN: " + _summary_for_model(vspec, ex) + "\n\nQUESTION: " + text}])
            reply = " ".join(str(reply).split())
            if reply and not reply.startswith("{"):
                return _answer(reply[:900], "llm")
        except Exception:  # noqa: BLE001 - fall through to the rules answer
            pass
    if not ex["ok"]:
        return _answer("I could not read this vehicle's plan any more. Describe the vehicle again and I will plan it fresh.")
    p = _param_defaults(vspec)
    sizes = ", ".join(f"{' '.join(_words(k))} {_fmt(v)}" for k, v in list(p.items())[:8])
    failing = [c["name"] for c in ex["checks"] if c["status"] != "pass"]
    return _answer(f"The {str(vspec.get('name') or 'vehicle').replace('_', ' ')} has {len(ex['parts'])} parts. "
                   + (f"Sizes: {sizes}. " if sizes else "")
                   + (f"Design checks failing: {', '.join(failing[:4])}. " if failing else "All its design checks pass. ")
                   + "Pick an AI below if you want me to answer in more detail. Nothing was changed.")


def _build_plan(vspec: dict[str, Any], expanded_hint: dict[str, Any] | None, *, intent: str, source: str,
                changes: list[str] | None, previous: dict[str, Any] | None, unresolved: list[dict] | None,
                from_example: bool = False, library_id: str | None = None, stage: int | None = None):
    built = to_chat_spec(vspec, library_id=library_id, stage=stage)
    if not built["ok"]:
        return _answer("I planned it but could not place the parts. " + " ".join(built["errors"])[:300]
                       + " Nothing was built and nothing was charged.", source)
    ex = built["expanded"]
    edited = intent == "edit"
    if built.get("stage"):
        k = built["stage"]["shown"]
        reply = describe_stage(vspec, ex, built["stage_plan"], k, changes=changes if edited else None,
                               notes=built["notes"], first=(k == 1 and not edited))
        if from_example:
            reply += " This is the built-in example. Pick an AI below to have it fitted to your description."
    else:
        reply = describe_plan(vspec, ex, edited=edited, changes=changes, unresolved=unresolved,
                              notes=built["notes"], from_example=from_example)
    return _plan_obj(intent, reply, built["spec"], source, changes)


def plan_vehicle_turn(last_user: str, current_spec: dict[str, Any] | None, *, llm: LLM | None = None,
                      library: SL.SpecLibrary | None = None, staged: bool = False):
    """A Plan for this message when it is about a vehicle, otherwise None. Never raises. `staged` builds a new vehicle
    one step at a time (the chat sets it after asking its questions); without it a new vehicle is built whole."""
    try:
        return _turn(last_user, current_spec, llm, library, staged)
    except Exception:  # noqa: BLE001 - a vehicle problem must never break the chat; the normal planner takes over
        import logging
        logging.getLogger(__name__).exception("vehicle turn failed")
        return None


def _turn(text: str, current_spec: dict | None, llm: LLM | None, library: SL.SpecLibrary | None, staged: bool = False):
    text = (text or "").strip()
    if not text:
        return None
    current = vehicle_of(current_spec)
    forced = _FORCED.match(text)             # the Assembly panel's "Vehicle: ..." box: the person has said what it is
    if forced:
        text = forced.group(1).strip()
        current = None
    new_request = bool(forced) or is_vehicle_request(text)
    if current is not None and new_request and _NEW.search(text) and kind_of(text) != kind_of(str(current.get("name") or "").replace("_", " ")):
        current = None                                   # "now build a bicycle instead" starts a new vehicle
    if current is None and not new_request:
        return None                                      # not about a vehicle: no library, no model, nothing touched
    lib = library or get_library()
    if current is None:
        return _start(text, llm, lib, staged)
    params = (current_spec or {}).get("parameters") or {}
    lid = params.get("library_id") if forced is None else None
    lid = lid if isinstance(lid, str) else None
    st = params.get("stage")
    shown = st.get("shown") if isinstance(st, dict) and isinstance(st.get("shown"), int) else None
    if shown is not None and not new_request:                      # a build in steps is in progress
        finish = len(text.split()) <= 6 and bool(_FINISH.search(text))
        if finish or _NEXT.match(text):
            return _advance(current, shown, finish, lid)
    elif _NEXT.match(text):                                        # nothing left to add
        return _answer("Every part is already showing. Tell me what to change, or press Generate to make the files.")
    if _QUESTION.search(text) and not _EDIT_VERB.search(text):
        return _answer_question(text, current, llm)
    return _edit(text, current, llm, lib, lid, shown)


def _advance(current: dict[str, Any], shown: int, to_end: bool, library_id: str | None):
    """Add the next build step (or all that is left) to the vehicle on the bench. Free, no AI."""
    ex = VS.expand_vehicle(current, None, None, _module_dir)
    splan = stage_plan(current, ex) if ex["ok"] else []
    if not splan or shown >= len(splan):
        return _answer("Every part is already showing. Tell me what to change, or press Generate to make the files.")
    k = len(splan) if to_end else shown + 1
    added = [f"added {t}" for t, _ in splan[shown:k]]
    return _build_plan(current, None, intent="edit", source="rules", changes=added, previous=current, unresolved=None,
                       library_id=library_id, stage=k)


def _start(text: str, llm: LLM | None, lib: SL.SpecLibrary, staged: bool = False):
    first_stage = 1 if staged else None
    errors: list[str] = []
    timed_out = False
    if llm is not None:
        r = VP.plan_vehicle(text, llm, lib, time_budget_s=_plan_budget())
        if r["ok"]:
            return _build_plan(r["spec"], r["expanded"], intent="build", source="llm", changes=None, previous=None,
                               unresolved=r.get("unresolved_checks"), library_id=r.get("candidate_id"),
                               stage=first_stage)
        errors = r.get("errors") or []
        timed_out = bool(r.get("timed_out"))
    # No AI (Engine plan) or the AI failed: the closest built-in example, said plainly.
    hits = lib.search(text, k=1)
    want = kind_of(re.sub(r"^\s*(?:vehicle|machine)\s*:", "", text, flags=re.I))
    if hits and want:                                     # never hand back an example of a different kind of vehicle
        got = kind_of(str(hits[0]["spec"].get("name") or "").replace("_", " "))
        if got not in (None, want):
            hits = []
    if hits:
        plan = _build_plan(hits[0]["spec"], None, intent="build", source="rules", changes=None, previous=None,
                           unresolved=None, from_example=True, stage=first_stage)
        if errors and plan.spec:
            plan.reply = (("The AI took too long to plan that, so this is the closest built-in example. " if timed_out
                           else "The AI could not finish that plan, so this is the closest built-in example. ") + plan.reply)
        return plan
    if llm is None and want:
        return _answer(f"Without an AI I can only build the built-in examples: wheelbarrow, bicycle, two-axle cart, RC "
                       f"helicopter and small locomotive. Pick an AI in the box below to plan a {want}. "
                       "Nothing was built and nothing was charged.")
    why = (" " + errors[-1][:200] if timed_out else " " + errors[0][:200]) if errors else ""
    return _answer("I could not plan that vehicle." + why + " Nothing was built and nothing was charged. "
                   "Try one of: wheelbarrow, bicycle, two-axle cart, RC helicopter, small locomotive, "
                   "or describe the parts and sizes.", "llm" if llm is not None else "rules")


def _edit(text: str, current: dict[str, Any], llm: LLM | None, lib: SL.SpecLibrary, library_id: str | None = None,
          stage: int | None = None):
    # A message that is only size edits ("set tray_len to 900", "increase the wheel diameter by 10%") needs no AI:
    # it is answered by the rules, free and at once. Anything else goes to the model as before.
    new, changes, problems, unclear, understood = _edit_rules(text, current)
    if understood and not unclear:
        if changes:
            plan = _build_plan(new, None, intent="edit", source="rules", changes=changes, previous=current,
                               unresolved=None, library_id=library_id, stage=stage)
            if problems and plan.spec:
                plan.reply += " Left as it was: " + "; ".join(problems[:2]) + "."
            return plan
        if problems:
            return _answer("I did not change it: " + "; ".join(problems[:2]) + ".")
        return _answer("Those sizes already have those values. Nothing was changed.")
    if llm is not None:
        r = VP.revise_vehicle(current, text, llm, lib, time_budget_s=_plan_budget())
        if r["ok"]:
            changes = diff_vehicle(current, r["spec"])
            if not changes:
                return _answer("That does not change the design. Tell me what to change, for example "
                               "\"make the wheel 400 mm\" or \"add a second axle\".", "llm")
            return _build_plan(r["spec"], r["expanded"], intent="edit", source="llm", changes=changes,
                               previous=current, unresolved=r.get("unresolved_checks"), library_id=library_id, stage=stage)
        err = (r.get("errors") or ["the change could not be planned"])[0][:200]
        if r.get("timed_out"):
            err = "that took too long to plan"
        new, changes, problems = apply_param_edit_rules(text, current)
        if not changes:
            return _answer(f"I could not make that change: {err}. Nothing was changed and nothing was charged.", "llm")
        return _build_plan(new, None, intent="edit", source="rules", changes=changes, previous=current, unresolved=None,
                           library_id=library_id, stage=stage)
    new, changes, problems = apply_param_edit_rules(text, current)
    if changes:
        return _build_plan(new, None, intent="edit", source="rules", changes=changes, previous=current, unresolved=None,
                           library_id=library_id, stage=stage)
    if problems:
        return _answer("I did not change it: " + "; ".join(problems[:2]) + ".")
    sizes = ", ".join(' '.join(_words(k)) for k in list(_param_defaults(current))[:6])
    return _answer("Without an AI I can only set a named size, for example \"wheel diameter 400 mm\". "
                   + (f"Sizes you can set: {sizes}. " if sizes else "")
                   + "Pick an AI below to add, remove or rearrange parts.")

"""
The conversation planner for CAD Studio.

/prompt-refine only ever helped someone WRITE a description, and /generate only ever
read one sentence. This module is the missing middle: it reads the whole chat plus the
spec of the part currently on the bench and decides what the newest message means.

  build   a new part   -> a complete spec
  edit    change the current part -> the COMPLETE updated spec, so unchanged values
          carry over exactly
  answer  a question, design guidance, a clarification, or a request it cannot safely
          act on -> text only, nothing is built. It may carry tappable `choices`, short
          multiple-choice questions the person answers with one tap.

Two planners, same output:
  * LLM planner (DeepSeek, reusing the part catalogue from deepseek_parser so the two
    never drift apart).
  * Rules planner, used when no key is configured or the model fails twice. It handles
    the edits people actually type ("4 holes", "5mm thick", "make it thicker", "add 2
    more holes") and otherwise asks a question rather than guessing.

The planner never touches the CAD engine, the database or credits. It returns a Plan.
chat_service.py does the building and charging.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import assembly_gate
import assembly_relations
import import_ops

INTENTS = ("build", "edit", "answer")
MAX_REPLY_CHARS = 700
# Part types a chat spec cannot name directly. "assembly" is allowed: its parameters carry a
# parts list (see _clean_assembly_params).
UNSUPPORTED_TYPES: set[str] = set()
# Lines in the parts list, and the count on one line. None means no design limit (a vehicle has dozens of
# parts). RUNAWAY_ENTRIES only stops a typo from hanging the server. Set an int to bring a limit back.
MAX_ASSEMBLY_ENTRIES: int | None = None
MAX_PART_COUNT: int | None = None
RUNAWAY_ENTRIES = 20_000


class PlanError(ValueError):
    """The rules planner understood the request but it cannot be built. The message is
    written for the person and is shown as the reply."""

LLM = Callable[[list[dict]], str]


@dataclass
class Plan:
    intent: str                      # build | edit | answer
    reply: str                       # short text shown to the person
    spec: dict | None = None         # {part_type, parameters, material, operations}
    source: str = "rules"            # llm | rules
    changes: list[str] = field(default_factory=list)
    choices: list[dict] = field(default_factory=list)  # [{"question": str, "options": [str, ...]}]


# ------------------------------------------------------------------ prompt ---

_INSTRUCTIONS = """You are NitoCAD's design assistant inside a chat. The person talks to you to design one mechanical part, then keeps changing it. You read the whole conversation and the spec of the part currently built, and you decide what their newest message means.

Reply with ONLY a JSON object, no markdown fences:
{
  "intent": "build" | "edit" | "answer",
  "reply": "one or two short plain sentences to the person",
  "spec": { "part_type": "...", "parameters": { ... }, "material": "..." or null, "operations": [] } or null,
  "choices": [ { "question": "short question", "options": ["short option", "short option"] } ]
}

Rules:
- "build": they want a NEW part. Return a complete spec.
- "edit": they want to change the CURRENT part. Start from the current spec and return the COMPLETE updated spec. Change only what they asked. Every other value stays exactly as it was. "Thicker" with no number means about 20 percent more. Keep the same part_type unless they clearly ask for a different part.
- "answer": a question, a greeting, a request for design advice, or a request that is too vague to act on safely. Set spec to null. When they ask for guidance (which material, what tolerance, how thick, which part suits their job), give short practical advice in plain words and say what you would pick. When something essential is missing, ask instead of guessing. Never invent a dimension they gave no hint of when it would change the part a lot.
- Answer any normal question the person asks, about design, materials, tolerances, manufacturing, how NitoCAD works, or anything else you can help with, in the same plain words. Answering is free, so never ask them to build or generate something just to get an answer. If a question has nothing to do with engineering, answer it briefly and invite them back to their part. Do not quote prices or credit costs, the screen shows them.
- "choices": only with "answer", otherwise []. Use it when the person is better served by tapping than typing. Up to 3 questions, each with 2 to 5 short options (a few words each). Put the question itself in "question" and keep "reply" to one sentence that introduces them. Do not repeat the questions inside "reply".
- Use only parameter names from the catalogue below for the chosen part_type. Dimensions are millimetres. Leave out a parameter the person did not mention so the template default applies.
- "reply" says what you did or what you need, in plain words. No markdown, no bullet lists, no emoji.
- Never claim a file exists, was exported or was anchored. You only describe the part.
- IMPORTED PART: the person attached a file and is editing it. part_type is "imported", and the current spec holds import_id, kind and filename. Copy import_id, kind and filename UNCHANGED, never invent them. Return {"import_id", "kind", "filename", "thickness_mm" (only when kind is dxf, svg, kicad or gerber), "edits": [ ... ]} where edits is the COMPLETE ordered list. An edit is one of: {"op":"scale","factor":2}, {"op":"scale_to","axis":"largest|x|y|z","mm":150}, {"op":"hole","diameter_mm":6,"x":10,"y":20,"depth_mm":null,"face":"top|bottom"}, {"op":"fillet","radius_mm":2,"edges":"all|vertical|top|bottom"}, {"op":"chamfer","size_mm":1,"edges":"all|vertical|top|bottom"}, {"op":"translate","x":0,"y":0,"z":0}, {"op":"rotate","axis":"x|y|z","degrees":90}, {"op":"mirror","plane":"xy|yz|xz"}. A mesh (kind stl or obj) only takes scale, scale_to, translate, rotate, mirror, {"op":"ground"} and {"op":"center"}: tell them holes and fillets are not possible on a mesh. x and y in a hole are millimetres from the middle of the part. The current spec's bbox_mm is the part's size. If they ask for something else entirely, build that as a new part.
- ASSEMBLY: when they ask for several parts together, use part_type "assembly" with parameters {"parts": [ {"part_type": "flat_plate", "parameters": { ... }, "name": "base_plate", "count": 1}, ... ], "layout": "row"}. Each part uses a part_type and parameters from the catalogue. "count" repeats a part. Parts are placed side by side automatically, so leave out positions when the parts only sit next to each other. When parts fit on or in each other (a gear, pulley, sprocket, bearing or spacer on a shaft), plan the assembly as a tree of named parts with positions: set "layout" to "custom", give every part a unique snake_case "name" and a "position": [x, y, z] in millimetres. Use the positions the person gives. Shafts, gears, pulleys, sprockets, bearings, spacers and washers are built along +Z from their position, so a part on a shaft that stands on the origin axis has position [0, 0, z], where z is where it starts along the shaft and z plus its thickness stays inside the shaft length. A bore is the shaft diameter plus 0.2 mm. Every plan is checked before it is built: parts that overlap, or that sit past the end of their shaft, are sent back to you with the reason, and you return the full JSON again with only that fixed. Never put an assembly inside an assembly. When they change an assembly, return the COMPLETE parts list.
- MATED ASSEMBLY: when the person wants parts that connect (bolted, seated, mounted, fitted to each other) AND every part you need appears in the INTERFACE CATALOGUE at the end, use "layout": "mated" with parameters {"layout": "mated", "root": "<part id>", "parts": [ {"id": "snake_case_id", "part_type": "...", "parameters": { ... }} ], "mates": [ {"a": "part_id.interface", "b": "part_id.interface"} ]}. Every part needs a unique id and no count (list each copy as its own part). Use only interface names listed for that part type and only kinds that can mate. A dimension that must agree across a mate (bolt count and circle diameter, bore against pilot) is stated on BOTH parts. Exactly one root; every other part must be reachable from it by mates. If a part you need has no interfaces, use a normal row assembly instead and say so. Never invent interface names. When they change a mated assembly, return the COMPLETE parts and mates.
- MEASURED FACTS: when the prompt ends with them, they describe the version now built, read from the real solid: overall size, mass, overlapping parts, and whether each requirement passes. Use them. If an edit would break a requirement or add an overlap, still make the change the person asked for and say so in one sentence in "reply". Do not change values they did not ask about to repair it, unless they ask you to.
- FITTED PARTS: in an assembly with one shaft, the bore of a gear, pulley, sprocket, bearing or spacer fitted to it, and the position of the parts along it, follow the shaft's diameter and length automatically after your reply. When they change the shaft, change only the shaft. Do not rewrite those bores or positions unless they ask for a different fit or place.

CATALOGUE AND SHAPE RULES
"""

_RESPONSE_TAIL = "Respond with ONLY valid JSON"


def build_system_prompt(catalogue: str | None = None) -> str:
    if catalogue is None:
        try:
            import deepseek_parser

            catalogue = deepseek_parser.SYSTEM_PROMPT.split(_RESPONSE_TAIL)[0]
        except Exception:  # noqa: BLE001 - planner still works, just without the catalogue
            catalogue = ""
    return _INSTRUCTIONS + catalogue.strip() + _interface_catalogue()


def _interface_catalogue() -> str:
    """Which parts have connection points, and which kinds can mate (for MATED ASSEMBLY)."""
    try:
        import domain_packs
        import mating

        itf = domain_packs.interface_catalog()
        lines = [f"- {part}: " + ", ".join(f"{i['name']} ({i['kind']})" for i in items) for part, items in sorted(itf.items())]
        compat = "; ".join(f"{a} <-> {b}" for a, b in mating.rule_pairs())
    except Exception:  # noqa: BLE001 - the planner still works, with plain assemblies only
        return ""
    return "\n\nINTERFACE CATALOGUE (parts with connection points, for MATED ASSEMBLY)\n" + "\n".join(lines) + "\nKinds that can mate: " + compat + "\nParts not listed here have no connection points."


def facts_digest(facts: dict | None, requirements: list[dict] | None = None) -> dict | None:
    """The short, planner-sized version of a built part's facts: what was measured on the solid,
    which parts overlap, and where each requirement stands. None when there is nothing to say."""
    if not facts:
        return None
    out: dict[str, Any] = {}
    for key in ("size_mm", "mass_g", "min_wall_mm", "material"):
        if facts.get(key) is not None:
            out[key] = facts[key]
    asm = facts.get("assembly") or {}
    if asm:
        out["parts"] = (asm.get("bom") or {}).get("part_count")
        out["overlaps"] = [{k: i.get(k) for k in ("a", "b", "volume_mm3", "severity")}
                           for i in (asm.get("interferences") or [])[:8] if isinstance(i, dict)]
        out["separate_groups"] = (asm.get("disconnected_groups") or [])[:4]
        out["pairs_not_checked"] = len(asm.get("unchecked_pairs") or [])
    if requirements:
        out["requirements"] = [{"text": r.get("text"), "status": r.get("status"), "detail": str(r.get("detail") or "")[:90]}
                               for r in requirements[:10]]
    return out or None


def build_messages(history: list[dict], current_spec: dict | None, catalogue: str | None = None, hint: str | None = None,
                   facts: dict | None = None) -> list[dict]:
    """`history` is chat_store messages (oldest first), the newest being the user's turn.
    `facts` is facts_digest(...) of the current version, so the model sees what the edit
    did to the real solid and not only what the spec says."""
    system = build_system_prompt(catalogue)
    if current_spec:
        system += "\n\nCURRENT PART SPEC (latest version, millimetres):\n" + json.dumps(current_spec, sort_keys=True)
        if facts:
            system += "\n\nMEASURED FACTS of that version (from the built solid):\n" + json.dumps(facts, sort_keys=True, default=str)
    else:
        system += "\n\nNo part has been built in this chat yet."
    if hint:
        system += "\n\n" + hint
    msgs = [{"role": "system", "content": system}]
    for m in history:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])})
    return msgs


# ----------------------------------------------------------------- parsing ---

def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    try:
        v = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise
        v = json.loads(m.group(0))
    if not isinstance(v, dict):
        raise ValueError("planner did not return an object")
    return v


def _clean_spec(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("spec must be an object")
    ptype = str(raw.get("part_type") or "").strip().lower()
    if not ptype or ptype == "unknown":
        raise ValueError("spec has no part_type")
    if ptype in UNSUPPORTED_TYPES:
        raise ValueError(f"{ptype} is not supported in chat")
    params = raw.get("parameters")
    if not isinstance(params, dict):
        raise ValueError("spec.parameters must be an object")
    if ptype == "assembly":
        params = _clean_assembly_params(params)
    elif ptype == "imported":
        params = import_ops.clean_import_params(params)
    material = raw.get("material")
    ops = raw.get("operations")
    return {
        "part_type": ptype,
        "parameters": copy.deepcopy(params),
        "material": str(material) if material else None,
        "operations": copy.deepcopy(ops) if isinstance(ops, list) else [],
    }


_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MAX_MATES: int | None = None   # no mate cap; set an int to bring one back


def _clean_mates(raw: Any, ids: set[str]) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("mates must be a list")
    if _MAX_MATES is not None and len(raw) > _MAX_MATES:
        raise ValueError(f"an assembly can have at most {_MAX_MATES} mates")
    out = []
    for i, m in enumerate(raw, start=1):
        if not isinstance(m, dict) or not isinstance(m.get("a"), str) or not isinstance(m.get("b"), str):
            raise ValueError(f"mate {i} needs 'a' and 'b' as \"part_id.interface\"")
        for side in ("a", "b"):
            pid = m[side].split(".", 1)[0]
            if "." not in m[side] or pid not in ids:
                raise ValueError(f"mate {i} names '{m[side]}', which is not a part id with an interface")
        item = {"a": m["a"].strip(), "b": m["b"].strip()}
        if m.get("gap_mm") is not None:
            try:
                item["gap_mm"] = float(m["gap_mm"])
            except (TypeError, ValueError):
                raise ValueError(f"mate {i} gap_mm must be a number") from None
        out.append(item)
    return out


def _clean_assembly_params(params: dict) -> dict:
    """The shape of an assembly's parts list, checked without the CAD kernel. Whether each
    part type exists and its values are sensible is checked when it is built.

    Two shapes: a plain parts list ("row" or "custom" layout), and a mated assembly
    ("mated": every part has an id, and `mates` say which connection points meet). Whether
    the connection points exist and fit is decided by the mate engine when it is built, and
    a mated assembly that does not fit is reported, never turned into a row."""
    parts = params.get("parts")
    if not isinstance(parts, list) or len(parts) < 1:
        raise ValueError("an assembly needs a parts list")
    entry_limit = MAX_ASSEMBLY_ENTRIES if MAX_ASSEMBLY_ENTRIES is not None else RUNAWAY_ENTRIES
    if len(parts) > entry_limit:
        raise ValueError(f"an assembly can list at most {entry_limit} parts")
    has_mates = bool(params.get("mates"))
    layout = str(params.get("layout") or ("mated" if has_mates else "row")).lower()
    if layout not in ("row", "custom", "mated"):
        raise ValueError("layout must be row, custom or mated")
    if has_mates:
        layout = "mated"
    if layout == "mated" and not has_mates:
        raise ValueError("a mated assembly needs a mates list")
    out = []
    total = 0
    ids: set[str] = set()
    for i, p in enumerate(parts, start=1):
        if not isinstance(p, dict):
            raise ValueError(f"part {i} must be an object")
        ptype = str(p.get("part_type") or "").strip().lower()
        if not ptype or ptype in ("assembly", "unknown", "freeform"):
            raise ValueError(f"part {i} has no usable part_type")
        pp = p.get("parameters", {})
        if not isinstance(pp, dict):
            raise ValueError(f"part {i} parameters must be an object")
        try:
            count = int(p.get("count", 1))
        except (TypeError, ValueError):
            raise ValueError(f"part {i} count must be a whole number") from None
        count_limit = MAX_PART_COUNT if MAX_PART_COUNT is not None else RUNAWAY_ENTRIES
        if not 1 <= count <= count_limit:
            raise ValueError(f"part {i} count must be 1 to {count_limit}")
        total += count
        item: dict[str, Any] = {"part_type": ptype, "parameters": copy.deepcopy(pp), "count": count}
        if layout == "mated":
            pid = p.get("id")
            if not isinstance(pid, str) or not _ID_RE.match(pid):
                raise ValueError(f"part {i} needs an id in lowercase letters, digits and underscores")
            if pid in ids:
                raise ValueError(f"two parts share the id '{pid}'")
            if count != 1:
                raise ValueError(f"part '{pid}' has a count; in a mated assembly list each copy as its own part with its own id")
            ids.add(pid)
            item["id"] = pid
            item.pop("count")
        if p.get("name"):
            item["name"] = " ".join(str(p["name"]).split())[:40]
        for key in ("position", "rotation"):
            if p.get(key) is not None:
                try:
                    vec = [float(v) for v in p[key]]
                except (TypeError, ValueError):
                    raise ValueError(f"part {i} {key} must be three numbers") from None
                if len(vec) != 3:
                    raise ValueError(f"part {i} {key} must be three numbers")
                item[key] = vec
        if layout == "mated" and isinstance(p.get("placement"), dict):
            item["placement"] = copy.deepcopy(p["placement"])
        out.append(item)
    if total < 2:
        raise ValueError("an assembly needs at least two parts")
    if layout != "mated":
        return {"parts": out, "layout": layout}
    result: dict[str, Any] = {"parts": out, "layout": "mated", "mates": _clean_mates(params.get("mates"), ids)}
    root = params.get("root")
    if root is not None:
        if root not in ids:
            raise ValueError(f"root '{root}' is not one of the part ids")
        result["root"] = root
    if params.get("name"):
        result["name"] = " ".join(str(params["name"]).split())[:40]
    return result


def _clean_choices(raw: Any) -> list[dict]:
    """Keep at most 3 questions with 2 to 5 short text options each. Anything else is dropped,
    because choices are a convenience and must never make a plan fail."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:10]:
        if len(out) >= 3:
            break
        if not isinstance(item, dict):
            continue
        q = " ".join(str(item.get("question") or "").split())[:140]
        raw_opts = item.get("options") if isinstance(item.get("options"), list) else []
        opts: list[str] = []
        for o in raw_opts:
            t = " ".join(str(o).split())[:60]
            if t and t not in opts:
                opts.append(t)
        if q and len(opts) >= 2:
            out.append({"question": q, "options": opts[:5]})
    return out


def coerce_plan(raw: dict, current_spec: dict | None) -> Plan:
    """Validate what the model returned. Raises ValueError when it cannot be trusted."""
    intent = str(raw.get("intent") or "").strip().lower()
    if intent not in INTENTS:
        raise ValueError(f"bad intent {intent!r}")
    reply = " ".join(str(raw.get("reply") or "").split())[:MAX_REPLY_CHARS]
    if intent == "answer":
        if not reply:
            raise ValueError("answer without a reply")
        return Plan("answer", reply, None, "llm", choices=_clean_choices(raw.get("choices")))
    spec = _clean_spec(raw.get("spec"))
    if intent == "edit" and not current_spec:
        intent = "build"  # nothing to edit yet, so this is the first build
    return Plan(intent, reply, spec, "llm", changes=_diff(current_spec, spec) if intent == "edit" else [])


def _diff(old: dict | None, new: dict) -> list[str]:
    if not old:
        return []
    if old.get("part_type") == "assembly" and new.get("part_type") == "assembly":
        return _diff_assembly(old, new)
    if old.get("part_type") == "imported" and new.get("part_type") == "imported":
        return _diff_imported(old, new)
    out = []
    op, np_ = old.get("parameters", {}), new.get("parameters", {})
    for k in sorted(set(op) | set(np_)):
        if op.get(k) != np_.get(k):
            out.append(f"{k}: {op.get(k, 'default')} to {np_.get(k, 'default')}")
    if old.get("part_type") != new.get("part_type"):
        out.insert(0, f"part type: {old.get('part_type')} to {new.get('part_type')}")
    return out


def _diff_imported(old: dict, new: dict) -> list[str]:
    op, np_ = old.get("parameters") or {}, new.get("parameters") or {}
    out: list[str] = []
    if op.get("thickness_mm") != np_.get("thickness_mm") and np_.get("thickness_mm") is not None:
        out.append(f"thickness: {op.get('thickness_mm', 'default')} to {np_['thickness_mm']:g} mm")
    a, b = list(op.get("edits") or []), list(np_.get("edits") or [])
    common = 0
    while common < min(len(a), len(b)) and a[common] == b[common]:
        common += 1
    for e in a[common:]:
        out.append("removed: " + import_ops.describe_edit(e).lower())
    for e in b[common:]:
        out.append("added: " + import_ops.describe_edit(e).lower())
    return out


def diff_specs(old: dict | None, new: dict) -> list[str]:
    """What differs between two specs, one line each. Used on the spec that was actually built, so
    auto-corrections and follow-on changes show up too."""
    return _diff(old, new)


def label_changes(built: list[str], planned: list[str]) -> list[str]:
    """Keep each built change line, and borrow the planner's longer wording for it when it says why
    ("... (follows shaft diameter)")."""
    out = []
    for line in built:
        longer = next((c for c in planned if c.startswith(line + " (")), None)
        out.append(longer or line)
    return out


def _part_label(p: dict, i: int) -> str:
    return str(p.get("name") or p.get("id") or p.get("part_type") or f"part {i}")


def _diff_assembly(old: dict, new: dict) -> list[str]:
    a = (old.get("parameters") or {}).get("parts") or []
    b = (new.get("parameters") or {}).get("parts") or []
    out: list[str] = []
    if len(a) != len(b):
        out.append(f"parts: {len(a)} to {len(b)}")
    for i, (x, y) in enumerate(zip(a, b), start=1):
        label = _part_label(y, i)
        if x.get("part_type") != y.get("part_type"):
            out.append(f"{label}: type {x.get('part_type')} to {y.get('part_type')}")
            continue
        xp, yp = x.get("parameters") or {}, y.get("parameters") or {}
        for k in sorted(set(xp) | set(yp)):
            if xp.get(k) != yp.get(k):
                out.append(f"{label} {k}: {xp.get(k, 'default')} to {yp.get(k, 'default')}")
        if x.get("count", 1) != y.get("count", 1):
            out.append(f"{label} count: {x.get('count', 1)} to {y.get('count', 1)}")
        if x.get("position") != y.get("position"):
            out.append(f"{label} position: {assembly_relations.pos_text(x.get('position'))} to {assembly_relations.pos_text(y.get('position'))}")
    ma = (old.get("parameters") or {}).get("mates") or []
    mb = (new.get("parameters") or {}).get("mates") or []
    if ma != mb:
        out.append(f"mates: {len(ma)} to {len(mb)}")
    return out


# ------------------------------------------------------------- rules planner ---

# word in the sentence -> substrings that identify the parameter key
_WORD_TO_KEY = {
    "thick": ("thickness",), "thickness": ("thickness",),
    "wall": ("wall_thickness", "wall"),
    "long": ("length",), "length": ("length",),
    "wide": ("width",), "width": ("width",),
    "tall": ("height",), "high": ("height",), "height": ("height",),
    "deep": ("depth",), "depth": ("depth",),
    "diameter": ("diameter",), "dia": ("diameter",), "across": ("diameter",),
    "bore": ("bore_diameter", "inner_diameter"),
    "fillet": ("fillet",), "fillets": ("fillet",), "radius": ("radius", "fillet"),
    "chamfer": ("chamfer",), "teeth": ("teeth",), "module": ("module",),
    "pin": ("pin_diameter",),
}
_RELATIVE = {
    "thicker": ("thickness", +1), "thinner": ("thickness", -1),
    "longer": ("length", +1), "shorter": ("length", -1),
    "wider": ("width", +1), "narrower": ("width", -1),
    "taller": ("height", +1), "bigger": (None, +1), "larger": (None, +1), "smaller": (None, -1),
}
_NUM = r"(\d+(?:\.\d+)?)"


def _find_key(params: dict, needles: tuple[str, ...], hole_ok: bool = False) -> str | None:
    for needle in needles:
        exact = [k for k in params if k.startswith(needle) and not k.startswith("hole")]
        if exact:
            return sorted(exact, key=len)[0]
        loose = [k for k in params if needle in k and (hole_ok or "hole" not in k)]
        if loose:
            return sorted(loose, key=lambda k: (0 if "outer" in k else 1, len(k), k))[0]
    return None


def _num(v: float) -> float | int:
    v = round(v, 2)
    return int(v) if float(v).is_integer() else v


def apply_edit_rules(text: str, spec: dict) -> tuple[dict, list[str]]:
    """Apply the edits we can read with certainty. Returns (new_spec, change_descriptions)."""
    t = " ".join(text.lower().split())
    new = copy.deepcopy(spec)
    p = new["parameters"]
    changes: list[str] = []

    def setp(key: str, val: Any) -> None:
        if p.get(key) != val:
            changes.append(f"{key} to {val}")
            p[key] = val

    # hole counts: "add 2 more holes", "4 holes", "make it 6 holes"
    hole_key = next((k for k in p if k in ("hole_count", "mount_hole_count")), None) or (
        "hole_count" if spec["part_type"] in ("l_bracket", "t_bracket", "flange") else None)
    m = re.search(rf"add\s+(?:another\s+|an?\s+)?{_NUM}?\s*(?:more|extra|additional)?\s*holes?", t)
    if m and hole_key:
        add = int(float(m.group(1))) if m.group(1) else 1
        setp(hole_key, int(p.get(hole_key, 0)) + add)
    else:
        m = re.search(rf"\b{_NUM}\s*(?:mounting\s+)?holes?\b", t)
        if m and hole_key:
            setp(hole_key, int(float(m.group(1))))

    # teeth
    m = re.search(rf"\b{_NUM}\s*teeth\b", t)
    if m and "teeth" in p or (m and spec["part_type"] in ("gear", "sprocket")):
        setp("teeth", int(float(m.group(1))))

    # "<n> mm <word>"  e.g. "5mm thick", "120 mm long", "8mm bore", "2mm fillets"
    for m in re.finditer(rf"{_NUM}\s*mm\s*(thick|thickness|wall|long|length|wide|width|tall|high|height|deep|depth|diameter|dia|bore|fillets?|radius|chamfer|pin)\b", t):
        key = _find_key(p, _WORD_TO_KEY[m.group(2)], hole_ok=m.group(2) == "pin")
        if m.group(2) in ("bore",) and not key:
            key = _find_key(p, ("bore", "inner_diameter"))
        if key:
            setp(key, _num(float(m.group(1))))
    # "<word> to <n> mm", "<word> <n> mm", "<word> of <n>mm"
    for m in re.finditer(rf"\b(thickness|wall|length|width|height|depth|diameter|bore|fillet|radius|chamfer)\s*(?:to|of|=|:|at|be)?\s*{_NUM}\s*mm\b", t):
        key = _find_key(p, _WORD_TO_KEY[m.group(1)])
        if key:
            setp(key, _num(float(m.group(2))))

    # relative: "thicker", "20% longer", "a bit smaller"
    if not changes:
        for word, (needle, sign) in _RELATIVE.items():
            if re.search(rf"\b{word}\b", t):
                pct = re.search(rf"{_NUM}\s*%", t)
                factor = 1 + sign * (float(pct.group(1)) / 100 if pct else 0.2)
                if needle is None:  # scale every length-like value
                    for k, v in list(p.items()):
                        if k.endswith("_mm") and isinstance(v, (int, float)) and not isinstance(v, bool):
                            setp(k, _num(v * factor))
                else:
                    key = _find_key(p, (needle, "wall_thickness") if needle == "thickness" else (needle,))
                    if key and isinstance(p.get(key), (int, float)):
                        setp(key, _num(p[key] * factor))
                break
    return new, changes


def apply_assembly_edit_rules(text: str, spec: dict) -> tuple[dict, list[str]]:
    """Edit one part of an assembly from a plain sentence: "make the shaft 80 mm long", "use 30 teeth".
    Each clause is applied to the part it names (by name or by type). A clause that names no part
    applies only when it holds a number and exactly one part could take it. "bigger" and "smaller"
    are left to an AI. Anything unclear changes nothing."""
    new = copy.deepcopy(spec)
    parts = (new.get("parameters") or {}).get("parts") or []
    changes: list[str] = []
    for clause in re.split(r"[,;]|\band\b|\bthen\b", text.lower()):
        clause = " ".join(clause.split())
        if not clause:
            continue
        if re.search(r"\b(?:bigger|larger|smaller)\b", clause):
            continue  # these scale every length on a part, bores included, and would loosen a fit
        words = set(re.findall(r"[a-z0-9]+", clause))

        def named(p: dict) -> bool:
            name = str(p.get("name") or p.get("id") or "").lower().replace("_", " ")
            return bool(name) and all(w in words or w + "s" in words for w in name.split())

        targets = [p for p in parts if named(p)] or [
            p for p in parts if str(p.get("part_type") or "").replace("_", " ") in clause
            or str(p.get("part_type") or "") + "s" in words]
        cand: list[tuple[dict, dict, list[str]]] = []
        for p in (targets or parts):
            sub = {"part_type": p.get("part_type"), "parameters": p.get("parameters") or {}}
            edited, ch = apply_edit_rules(clause, sub)
            if ch:
                cand.append((p, edited, ch))
        # With no part named, only a clause with a number can be applied, and only to the one part that takes it.
        # "it", "this" or "the assembly" does not name a part, so those are never guessed.
        vague = not targets and re.search(r"\b(?:it|this|that|them|everything|whole|overall|assembly)\b", clause)
        if targets or (not vague and len(cand) == 1 and re.search(r"\d", clause)):
            for p, edited, ch in cand:
                label = _part_label(p, parts.index(p) + 1)
                p["parameters"] = edited["parameters"]
                changes.extend(f"{label} {c}" for c in ch)
    return new, changes


def standard_plan(text: str) -> Plan | None:
    """A standard part named in the message (a 6204 bearing, an M8 x 30 bolt, IPE200, NPS 2 sch 40)
    builds from the standard's own dimensions. Never guesses: unknown sizes return None."""
    import standards

    found = standards.find_standard(text)
    if not found:
        return None
    spec = {"part_type": found["part_type"], "parameters": dict(found["parameters"]), "material": None, "operations": []}
    reply = f"Using {found['label']} ({found['standard']}) dimensions."
    if found.get("notes"):
        reply += " " + " ".join(found["notes"])
    return Plan("build", reply, spec, "rules")


_QUESTION = re.compile(r"^\s*(?:what|how|why|which|who|when|where|can|could|does|do|is|are|should|will|would|explain|tell me)\b|\?\s*$", re.I)


def looks_like_question(text: str) -> bool:
    return bool(_QUESTION.search(text or ""))


def rules_plan(text: str, current_spec: dict | None, regex_parse: Callable[[str], Any] | None = None) -> Plan:
    """Deterministic fallback. `regex_parse` is smart_parser.parse_description (injected so this
    module stays importable on its own). For assemblies the injected function also fills in the
    parts list, and raises PlanError when the description names fewer than two parts."""
    std = standard_plan(text)
    if std is not None:
        return std
    editing_assembly = bool(current_spec) and current_spec.get("part_type") == "assembly"
    if current_spec and current_spec.get("part_type") == "imported":
        new, changes = import_ops.apply_import_edit_rules(text, current_spec)
        if changes:
            return Plan("edit", "Done. " + "; ".join(changes[:4]).capitalize() + ".", new, "rules", changes)
        # Not an edit. Build something new only when the words clearly name a different part.
        if regex_parse is not None:
            try:
                parsed = regex_parse(text)
                ptype = getattr(parsed, "part_type", "unknown")
                named = ptype not in ("unknown", "flat_plate", "assembly") or re.search(r"\bplate\b", text, re.I)
                if named and ptype not in ("unknown",) and ptype not in UNSUPPORTED_TYPES and ptype != "imported":
                    spec = {"part_type": ptype, "parameters": dict(parsed.parameters),
                            "material": getattr(parsed, "material", None),
                            "operations": list(getattr(parsed, "operations", []) or [])}
                    return Plan("build", "", spec, "rules")
            except PlanError as exc:
                return Plan("answer", str(exc), None, "rules")
            except Exception:  # noqa: BLE001
                pass
        return Plan("answer", import_ops.HELP_TEXT, None, "rules")
    if current_spec and not editing_assembly:
        new, changes = apply_edit_rules(text, current_spec)
        if changes:
            return Plan("edit", "Done. " + "; ".join(changes[:4]).capitalize() + ".", new, "rules", changes)
    if editing_assembly:
        new, changes = apply_assembly_edit_rules(text, current_spec)
        if changes:
            return Plan("edit", "Done. " + "; ".join(changes[:4]).capitalize() + ".", new, "rules", changes)
    if regex_parse is not None:
        try:
            parsed = regex_parse(text)
            ptype = getattr(parsed, "part_type", "unknown")
            if ptype and ptype not in ("unknown",) and ptype not in UNSUPPORTED_TYPES:
                if editing_assembly and ptype != "assembly":
                    raise PlanError("This is an assembly, and I can only change an assembly's parts when an AI is selected "
                                    "below. Pick an AI, or describe the whole assembly again, for example "
                                    "\"a base plate 120 mm long and two L brackets\".")
                spec = {"part_type": ptype, "parameters": dict(parsed.parameters),
                        "material": getattr(parsed, "material", None),
                        "operations": list(getattr(parsed, "operations", []) or [])}
                return Plan("build", "", spec, "rules")
        except PlanError as exc:
            return Plan("answer", str(exc), None, "rules")
        except Exception:  # noqa: BLE001 - fall through to a clarifying question
            pass
    if looks_like_question(text):
        return Plan("answer", "Engine mode builds parts from a description and cannot chat. Choose Basic or higher above the "
                    "message box to ask questions, and questions never cost credits. Or describe a part and I will "
                    "build a free preview, for example \"L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes\".", None, "rules")
    if editing_assembly:
        return Plan("answer", "I can only change an assembly's parts when an AI is selected below. Pick an AI, or "
                    "describe the whole assembly again, for example \"a base plate 120 mm long and two L brackets\".",
                    None, "rules")
    if current_spec:
        return Plan("answer", "I did not catch which value to change. Tell me the dimension and the new size, "
                    "for example \"make the thickness 5 mm\" or \"add 2 more holes\".", None, "rules")
    return Plan("answer", "I could not tell which part you want yet. Name the part and its main sizes, "
                "for example \"L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes\".", None, "rules")


# --------------------------------------------------------------- circuit boards ---

def _pcb_reply(spec: dict, previous: dict | None) -> tuple[str, list[str]]:
    """Plain-words reply and change list for a planned board."""
    from pcb.blocks import BLOCKS

    def titles(sp):
        return {b["id"]: (b["type"], BLOCKS[b["type"]].title if b["type"] in BLOCKS else b["type"]) for b in sp["blocks"]}

    now = titles(spec)
    if previous and previous.get("blocks"):
        before = titles(previous)
        added = [t[1] for i, t in now.items() if i not in before]
        removed = [t[1] for i, t in before.items() if i not in now]
        changed = [now[i][1] for i in now if i in before and any(
            b["id"] == i and b.get("params") != next((x.get("params") for x in previous["blocks"] if x["id"] == i), None)
            for b in spec["blocks"])]
        changes = [f"added {n}" for n in added] + [f"removed {n}" for n in removed] + [f"changed {n}" for n in changed]
        what = "; ".join(changes[:3]) or "your change"
        return f"Updated the board: {what}. This is a free preview.", changes
    names = ", ".join(t[1] for t in now.values())
    return (f"Here is a free preview of your circuit board with {len(now)} circuit blocks: {names}. "
            "The 3D model shows the board and its parts at real size."), []


def _pcb_turn(last_user: str, current_spec: dict | None, llm: "LLM | None") -> "Plan | None":
    """A circuit-board request or an edit to the board on the bench. None means this is not about a
    board and the normal mechanical planner should handle it."""
    from pcb import intent
    from pcb import planner as pp

    cur_pcb = bool(current_spec) and current_spec.get("part_type") == "pcb_board"
    if (cur_pcb or intent.is_pcb_request(last_user)) and intent.is_question(last_user):
        if llm is not None:
            return None                      # the AI answers questions in plain words; nothing is built
        return Plan("answer", "That sounds like a question, and nothing on the board was changed. I can add or "
                    "change circuit blocks, for example \"add an I2C sensor header\" or \"use a 12V supply\".",
                    None, "rules")
    if cur_pcb:
        if (intent.mentions_mechanical_part(last_user) and not intent.is_pcb_request(last_user)
                and not re.search(r"\b(board|circuit)\b", last_user, re.I)):
            return None                      # clearly a mechanical part: leave the board behind
        if intent.wants_removal(last_user):
            return Plan("answer", "Removing parts from a board is not supported yet. Describe the board you want and "
                        "I will build a new version, or ask me to add or change something.", None, "rules")
    elif not intent.is_pcb_request(last_user):
        return None

    prior = str(((current_spec or {}).get("parameters") or {}).get("description") or "") if cur_pcb else ""
    description = f"{prior} | {last_user}" if prior else last_user
    description = description.strip()[:3800]

    result, errors, from_llm = None, [], False
    if llm is not None:
        try:
            result = pp.llm_plan(description, llm)
            from_llm = True
        except pp.PlanError as exc:
            errors = exc.errors
        except Exception:  # noqa: BLE001 - transport failure: the offline planner still works
            errors = []
    if result is None:
        try:
            result = pp.keyword_plan(description)
        except pp.PlanError as exc:
            errors = errors or exc.errors
    # Same rule as the mechanical planner: "llm" only when the model's plan is what was used, so a
    # model failure that falls back to the rules is neither counted against the AI allowance nor
    # priced as an AI build.
    source = "llm" if from_llm else "rules"
    if result is None:
        reason = " ".join(errors)[:MAX_REPLY_CHARS - 60] or "I could not turn that into a board."
        return Plan("answer", reason + " Nothing was built and nothing was charged.", None, source)

    previous = ((current_spec or {}).get("parameters") or {}).get("spec") if cur_pcb else None
    if previous and previous == result.spec:
        return Plan("answer", "That does not change the board. Tell me what to add or change, for example "
                    "\"add an I2C sensor header\" or \"use a 12V supply\". Removing parts is not supported yet.",
                    None, source)
    reply, changes = _pcb_reply(result.spec, previous)
    for n in result.notes[:2]:
        reply += " " + n
    spec = {"part_type": "pcb_board", "parameters": {"description": description, "spec": result.spec},
            "material": None, "operations": []}
    return Plan("edit" if previous else "build", reply[:MAX_REPLY_CHARS], spec, source, changes=changes)


# ------------------------------------------------------------------- vehicles ---

def _vehicle_turn(last_user: str, current_spec: dict | None, llm: "LLM | None") -> "Plan | None":
    """A whole vehicle or machine (wheelbarrow, bicycle, ...) or an edit to the one on the bench. None means this
    message is not about a vehicle and the normal planner handles it. See vehicle_chat.py."""
    import vehicle_chat

    return vehicle_chat.plan_vehicle_turn(last_user, current_spec, llm=llm)


# -------------------------------------------------------------------- entry ---

def _carry_through(plan: Plan, current_spec: dict | None) -> Plan:
    """After any edit to an assembly, make the parts fitted to the shaft follow it, and say so.
    Never raises: a relation problem must not lose the person's edit."""
    if plan.intent != "edit" or not plan.spec or not current_spec:
        return plan
    if (plan.spec.get("parameters") or {}).get("vehicle") is not None:
        return plan                       # a vehicle's fits come from its own spec and checks, not from shaft bores
    try:
        res = assembly_relations.reconcile(current_spec, plan.spec)
    except Exception:  # noqa: BLE001
        return plan
    if not (res.changes or res.blocked):
        return plan
    plan.spec = res.spec
    plan.changes = list(plan.changes) + res.changes
    extra = ""
    if res.changes:
        extra += " Also updated to match: " + "; ".join(res.changes[:3]) + "."
    if res.blocked:
        extra += " Not carried through: " + "; ".join(res.blocked[:2]) + "."
    plan.reply = (plan.reply + extra).strip()
    return plan


def _gate_report(plan: Plan) -> assembly_gate.Report:
    """The pre-build check of the plan's assembly. Empty for anything that is not a custom-layout assembly."""
    if plan.intent in ("build", "edit") and plan.spec:
        return assembly_gate.check(plan.spec)
    return assembly_gate.Report()


def _note_warnings(plan: Plan, report: assembly_gate.Report) -> Plan:
    """The plan builds, but say what looks off, so it is not a surprise in the preview."""
    if report.warnings:
        plan.reply = (plan.reply + " Heads up: " + "; ".join(report.warnings[:2]) + ".").strip()
    return plan


def _rejected_plan(plan: Plan, report: assembly_gate.Report) -> Plan:
    """A plan that failed the gate twice is not built. The person gets the reasons instead of a preview
    that would only report the same overlap after the fact."""
    why = " ".join(report.errors[:3])
    return Plan("answer", "I did not build that, because the parts as planned do not fit together. " + why
                + " Tell me what to change and I will plan it again.", None, plan.source)


def plan_turn(
    history: list[dict],
    current_spec: dict | None,
    *,
    llm: LLM | None = None,
    regex_parse: Callable[[str], Any] | None = None,
    catalogue: str | None = None,
    attempts: int = 2,
    facts: dict | None = None,
) -> Plan:
    """Decide what the newest user message means. Never raises: any failure of the model
    drops to the rules planner. `facts` is facts_digest(...) of the current version. The model
    sees it; the rules planner does not need it."""
    plan = _plan_core(history, current_spec, llm=llm, regex_parse=regex_parse, catalogue=catalogue,
                      attempts=attempts, facts=facts)
    if plan.source == "llm":
        return plan                       # relations and the gate already ran inside the retry loop
    plan = _carry_through(plan, current_spec)
    report = _gate_report(plan)
    return _rejected_plan(plan, report) if report.errors else _note_warnings(plan, report)


def _plan_core(
    history: list[dict],
    current_spec: dict | None,
    *,
    llm: LLM | None,
    regex_parse: Callable[[str], Any] | None,
    catalogue: str | None,
    attempts: int,
    facts: dict | None,
) -> Plan:
    last_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
    try:
        vehicle_plan = _vehicle_turn(last_user, current_spec, llm)
    except Exception:  # noqa: BLE001 - a vehicle problem must never break the chat; fall through
        vehicle_plan = None
    if vehicle_plan is not None:
        return vehicle_plan
    try:
        pcb_plan = _pcb_turn(last_user, current_spec, llm)
    except Exception:  # noqa: BLE001 - a board problem must never break the chat; fall through
        pcb_plan = None
    if pcb_plan is not None:
        return pcb_plan
    if llm is not None:
        std = None
        try:
            import standards

            std = standards.find_standard(last_user)
        except Exception:  # noqa: BLE001
            std = None
        hint = None
        if std:
            hint = ("STANDARD PART: the person named " + std["label"] + " (" + std["standard"] + "). If they want it built, use "
                    "part_type " + std["part_type"] + " with EXACTLY these parameters: " + json.dumps(std["parameters"], sort_keys=True) + ".")
        msgs = build_messages(history, current_spec, catalogue, hint, facts)
        rejected: tuple[Plan, assembly_gate.Report] | None = None
        for _ in range(max(1, attempts)):
            raw = ""
            try:
                raw = llm(msgs)
                plan = _carry_through(coerce_plan(_extract_json(raw), current_spec), current_spec)
                report = _gate_report(plan)
                if report.errors:
                    rejected = (plan, report)
                    msgs = msgs + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": "That plan was checked before building and does not work: " + " ".join(report.errors[:4])
                         + " Return the full JSON object again with only that fixed."}]
                    continue
                return _note_warnings(plan, report)
            except Exception as exc:  # noqa: BLE001 - bad JSON, bad spec, transport error
                msgs = msgs + [{"role": "user", "content": f"That reply was unusable ({exc}). Return the JSON object again, exactly in the requested shape."}]
        if rejected:
            return _rejected_plan(*rejected)
    return rules_plan(last_user, current_spec, regex_parse)


def make_deepseek_llm(api_key: str, *, base_url: str, model: str, timeout: float) -> LLM:
    """An LLM callable backed by DeepSeek's OpenAI-compatible API."""
    def call(messages: list[dict]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.1,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""
    return call

"""Questions before building, for everything the Studio chat can build.

vehicle_chat.py already does this for whole vehicles: "I want to build a wheelbarrow" gets one tappable question at a
time, free, and the build starts once there are enough details. This module gives every other kind of build the same
conversation: brackets, plates, boxes, gears, round parts, flanges, beams, hinges, engine parts, aerospace parts,
roads and walls, buildings, circuit boards, fasteners and multi-part assemblies.

The rules are the ones the vehicles use, on purpose:
  * A request with enough concrete details (sizes, a material, a count) builds as it always did, with no questions.
  * "just build it", "use the defaults" and the other skip phrases build at once.
  * Each question is free. The reply is an "answer" plan, so nothing is charged and nothing is built.
  * After 5 messages the chat builds with whatever it has.
  * Something already on the bench is being edited, so there are no questions, unless the person asks for a
    "new" or "another" one.

The questions carry sizes in the same wording the regex parser reads ("50 mm wide", "20 teeth", "8 mm bore"), so a
tapped answer also works in Engine mode, where no AI is selected.

Adding a kind of build is one entry in FAMILIES. Domain packs that arrive later (architecture, civil, aerospace,
automotive) do the same.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import vehicle_chat as VC

logger = logging.getLogger(__name__)

_MATERIALS = ("What is it made of?", ["Steel", "Aluminium", "Plastic"])


def _f(words, start, questions, *, enough=2, needs_ai=False, label=None, content=None):
    return {"words": tuple(words), "start": start, "q": [tuple(q) for q in questions], "enough": enough,
            "needs_ai": needs_ai, "label": label, "content": re.compile(content, re.I) if content else None}


FAMILIES: dict[str, dict[str, Any]] = {
    "bracket": _f(("bracket", "motor mount"), "the base first, then the mounting holes, then the edge fillets", [
        ("What size should the bracket be?", ["Small, 30 mm wide, 30 mm tall, 3 mm thick",
                                              "Medium, 50 mm wide, 60 mm tall, 4 mm thick",
                                              "Large, 100 mm wide, 120 mm tall, 6 mm thick"]),
        ("How many mounting holes?", ["2 holes", "4 holes", "6 holes"]), _MATERIALS]),
    "plate": _f(("plate",), "the plain plate first, then the holes, then the corners", [
        ("What size should the plate be?", ["Small, 100 x 60 x 3 mm", "Medium, 200 x 100 x 6 mm", "Large, 400 x 300 x 10 mm"]),
        ("How many holes?", ["4 holes", "6 holes", "8 holes"]), _MATERIALS]),
    "box": _f(("box", "enclosure", "case", "housing", "tray", "container"),
              "the outer shell first, then the lid or openings, then the mounting details", [
        ("What size should it be?", ["Small, 80 x 60 x 40 mm", "Medium, 150 x 100 x 60 mm", "Large, 300 x 200 x 100 mm"]),
        ("How thick are the walls?", ["2 mm walls", "3 mm walls", "5 mm walls"]), _MATERIALS]),
    "gear": _f(("gear", "pulley", "sprocket", "cog"), "the toothed disc first, then the bore, then the hub or keyway", [
        ("What size should it be?", ["Small, module 1, 20 teeth, 6 mm bore", "Medium, module 2, 30 teeth, 10 mm bore",
                                     "Large, module 3, 50 teeth, 20 mm bore"]),
        ("How thick should it be?", ["5 mm thick", "10 mm thick", "20 mm thick"]),
        ("What is it made of?", ["Steel", "Brass", "Plastic"])]),
    "round": _f(("shaft", "bearing", "spacer", "washer", "standoff", "bushing", "sphere"),
                "the main body first, then the bore or ends, then the edge finishing", [
        ("How big should it be?", ["Small, 10 mm diameter, 50 mm long", "Medium, 25 mm diameter, 150 mm long",
                                   "Large, 60 mm diameter, 400 mm long"]),
        ("What is it made of?", ["Steel", "Stainless steel", "Brass"])]),
    "pipe": _f(("flange", "pipe", "pipe fitting", "elbow"), "the main body first, then the bolt circle, then the bore", [
        ("What size should it be?", ["Small, 50 mm diameter", "Medium, 100 mm diameter", "Large, 200 mm diameter"]),
        ("How many bolt holes?", ["4 holes", "8 holes", "12 holes"]),
        ("What is it made of?", ["Steel", "Stainless steel", "Plastic"])]),
    "beam": _f(("beam", "girder", "joist", "angle iron", "tube"), "the section first, then the length, then the end details", [
        ("What size should the section be?", ["Small, 40 mm wide, 40 mm tall, 1000 mm long",
                                              "Medium, 100 mm wide, 200 mm tall, 3000 mm long",
                                              "Large, 200 mm wide, 400 mm tall, 6000 mm long"]),
        ("What is it made of?", ["Steel", "Aluminium", "Timber"])]),
    "hinge": _f(("hinge", "cam"), "the main leaf first, then the pin, then the mounting holes", [
        ("What size should it be?", ["Small, 40 mm long, 3 mm thick", "Medium, 80 mm long, 4 mm thick",
                                     "Large, 150 mm long, 6 mm thick"]), _MATERIALS]),
    "engine_part": _f(("connecting rod", "con rod", "crankshaft"), "the main shape first, then the journals, then the oil holes", [
        ("How many cylinders is it for?", ["1 cylinder", "2 cylinders", "4 cylinders"]),
        ("How long is the stroke?", ["40 mm stroke", "80 mm stroke", "100 mm stroke"]),
        ("What is it made of?", ["Steel", "Aluminium"])]),
    "wing": _f(("wing", "airfoil", "wing rib", "wing spar", "spar"), "the wing section first, then the ribs and spar, then the fittings", [
        ("How big is the wing?", ["Model, 600 mm span", "Light aircraft, 10000 mm span", "Airliner, 35000 mm span"]),
        ("Which airfoil?", ["NACA 2412", "NACA 0012", "NACA 4412"]),
        ("What is it made of?", ["Aluminium", "Carbon fibre", "Balsa wood"])]),
    "airframe": _f(("fuselage", "tail fin", "tail surface", "stabilizer", "stabiliser"),
                   "the main shell first, then the frames, then the fittings", [
        ("How big is it?", ["Model, 600 mm long", "Light aircraft, 8000 mm long", "Airliner, 40000 mm long"]),
        ("What is it made of?", ["Aluminium", "Carbon fibre", "Balsa wood"])]),
    "road": _f(("road", "road segment", "highway", "street"), "the road surface first, then the shoulders, then the kerbs and drains", [
        ("How long is the road section?", ["100 m long", "500 m long", "2000 m long"]),
        ("How wide is it?", ["7 m wide, two lanes", "10 m wide, with shoulders", "14 m wide, dual lane"])]),
    "culvert": _f(("culvert",), "the barrel first, then the end walls, then the wing walls", [
        ("How big is the opening?", ["2 m wide, 2 m tall", "3 m wide, 2 m tall", "4 m wide, 3 m tall"]),
        ("How long is it?", ["8 m long", "12 m long", "20 m long"])]),
    "wall": _f(("retaining wall",), "the stem first, then the base slab, then the drainage", [
        ("How tall is the wall?", ["2 m tall", "4 m tall", "6 m tall"]),
        ("How long is it?", ["10 m long", "25 m long", "50 m long"])]),
    "building": _f(("house", "building", "bungalow", "villa", "cabin", "shed", "garage", "warehouse"),
                   "the walls and floor first, then the roof, then the doors and windows", [
        ("How many floors?", ["1 storey", "2 storeys", "3 storeys"]),
        ("How big is the footprint?", ["Small, 6 x 8 m", "Medium, 10 x 12 m", "Large, 15 x 20 m"]),
        ("What kind of roof?", ["Pitched roof", "Flat roof"])], enough=3),
    "fastener": _f(("bolt", "screw", "nut", "rivet"), "the shank or body first, then the head, then the thread", [
        ("What size should it be?", ["6 mm diameter, 20 mm long", "8 mm diameter, 30 mm long", "12 mm diameter, 50 mm long"]),
        ("What is it made of?", ["Steel", "Stainless steel", "Brass"])]),
    "pcb": _f((), "the board outline and power first, then the main chip, then the connectors", [
        ("What should the board do?", ["Sensor board with an I2C header", "Motor driver with a MOSFET",
                                       "Power board with a buck converter", "Microcontroller board"]),
        ("What supply voltage?", ["5 V supply", "12 V supply", "3.3 V supply"]),
        ("How big is the board?", ["Small, 30 x 30 mm", "Medium, 50 x 50 mm", "Large, 100 x 80 mm"])], label="circuit board",
        # A board is described by what it does, not by sizes: naming any circuit block or a voltage is a full order.
        content=r"\b(leds?|mosfets?|mcu|arduino|atmega\w*|esp\d*|microcontroller|i2c|spi|uart|sensors?|555|blink\w*|flash\w*|"
                r"oscillat\w*|switch\w*|loads?|breakouts?|headers?|test[- ]points?|buck|step[- ]?down|motors?|relays?|"
                r"solenoids?|coils?|wifi|oled|display|usb|battery|regulator|\d+(?:\.\d+)?\s*v(?:olts?)?)\b"),
    "assembly": _f(("assembly", "gearbox", "mechanism", "linkage"), "the base part first, then the parts that sit on it, then the fasteners", [
        ("How are the parts joined?", ["Bolted together", "Sitting on each other", "Fitted on a shaft"]),
        ("Roughly how big is it?", ["Small, about 100 mm", "Medium, about 300 mm", "Large, about 1000 mm"]),
        _MATERIALS], enough=3, needs_ai=True),
}

# One word -> the family that owns it. Longest words first so "retaining wall" wins over "wall".
_WORD_TO_FAMILY = {w: name for name, fam in FAMILIES.items() for w in fam["words"]}
_WORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _WORD_TO_FAMILY), key=len, reverse=True)) + r")s?\b", re.I)
_ALL_QUESTIONS = sorted({q for fam in FAMILIES.values() for q, _ in fam["q"]}, key=len, reverse=True)
# A beginner asking to be walked through it: build it in steps, with or without sizes in the message.
_STEPS = re.compile(r"\b(step[- ]by[- ]step|in steps|one step at a time|step at a time|walk me through|guide me|"
                    r"teach me|show me how to (?:build|make|design)|i(?:'m| am) a beginner|for a beginner|beginner)\b", re.I)
_COMPARATIVE = re.compile(r"\b(bigger|smaller|longer|shorter|wider|narrower|taller|thicker|thinner|larger|lighter|heavier)\b", re.I)
_SHORT_WORDS = 9


def family_of(text: str) -> str | None:
    """The kind of build a message names, or None. Whole vehicles belong to vehicle_chat, never here."""
    t = text or ""
    if VC.kind_of(t) is not None:
        return None
    try:
        from pcb.intent import is_pcb_request
        if is_pcb_request(t):
            return "pcb"
    except Exception:  # noqa: BLE001 - the board pack is optional here
        pass
    m = _WORD_RE.search(t)
    return _WORD_TO_FAMILY[m.group(1).lower()] if m else None


def names_several_kinds(text: str) -> bool:
    """"a gear on a shaft", "a plate with a bracket on top": the message already describes how things combine."""
    return len({m.group(1).lower() for m in _WORD_RE.finditer(text or "")}) >= 2


def is_build_request(text: str) -> bool:
    """A message that asks for something to be made: it names a buildable kind, says to make it (or is a short noun
    phrase such as "a bracket"), and is not a question or an edit."""
    t = (text or "").strip()
    if not t or VC._QUESTION.search(t) or _COMPARATIVE.search(t) or family_of(t) is None:
        return False
    return bool(VC._MAKE.search(t)) or len(t.split()) <= _SHORT_WORDS


def _is_standard(text: str) -> bool:
    try:
        import standards
        return bool(standards.find_standard(text))
    except Exception:  # noqa: BLE001
        return False


def scope_turn(history: list[dict[str, Any]], current_spec: dict[str, Any] | None, *, has_ai: bool = False):
    """None when the normal planner should handle the message. Otherwise ("ask", Plan) with one tappable question, or
    ("build", description, history, staged) once the chat has enough, where history is the conversation with the
    question chat folded into one user message so the planner sees a plain request, and staged says to show the
    build in steps (build_steps.py). Never raises."""
    try:
        return _scope(history, current_spec, has_ai)
    except Exception:  # noqa: BLE001 - a scoping problem must never break the chat; build as before
        logger.exception("build scoping failed")
        return None


def _fold(msgs, chain, desc, staged):
    """("build", description, history, staged): the question chat folded into one plain user message."""
    base = msgs[: len(msgs) - (2 * len(chain) - 1)]
    return ("build", desc, base + [{"role": "user", "content": desc}], bool(staged))


def _scope(history, current_spec, has_ai):
    msgs = list(history or [])
    chain = VC._scope_chain(msgs)
    if not chain:
        return None
    first = chain[0].strip()
    if VC.vehicle_of(current_spec) is not None:
        return None                                           # editing a vehicle: no questions
    if current_spec and not VC._NEW.search(first):
        return None                                           # editing what is on the bench: no questions
    want_steps = bool(_STEPS.search(first))
    fam_name = family_of(first)
    if fam_name is None or _is_standard(first):
        return None
    if not (is_build_request(first) or want_steps):
        return None
    fam = FAMILIES[fam_name]
    if fam["needs_ai"] and not has_ai:
        return None                                           # the rules planner cannot build these, so do not ask
    if names_several_kinds(first):
        # "a gear on a shaft" already says how things combine. No questions, but a beginner still gets the steps.
        return _fold(msgs, chain, first, True) if want_steps and len(chain) == 1 else None
    raw = " ".join(chain)
    desc = raw
    for q in _ALL_QUESTIONS:
        desc = desc.replace(q, " ")
    desc = " ".join(desc.split())
    skip = bool(VC._SKIP.search(desc))
    enough = int(fam["enough"])
    score = VC.detail_score(desc)
    if fam["content"] is not None and fam["content"].search(desc):
        score = max(score, enough)
    if len(chain) == 1 and (skip or score >= enough):
        # The person gave the full order. Build it whole, as before, unless they asked to be walked through it.
        return _fold(msgs, chain, desc, True) if want_steps and not skip else None
    remaining = [qa for qa in fam["q"] if qa[0] not in raw]
    if skip or score >= enough or not remaining or len(chain) >= VC._MAX_ROUNDS:
        # Questions were answered: show it in steps. "just build it" means all of it at once.
        return _fold(msgs, chain, desc, not skip)
    m = _WORD_RE.search(first)
    name = fam["label"] or (m.group(1).lower() if m else fam_name.replace("_", " "))
    if len(chain) == 1:
        reply = (f"{VC._SCOPE_LEAD} the {name}, I need a few choices so it comes out right. I would start with "
                 f"{fam['start']}. Tap an answer, type your own sizes, or say just build it to skip the questions.")
    else:
        reply = f"{VC._SCOPE_LEAD} the {name}, one more choice."
    q, opts = remaining[0]
    from conversation import Plan
    return ("ask", Plan("answer", reply, None, "rules", choices=[{"question": q, "options": list(opts)}]))

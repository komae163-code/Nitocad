"""Prompt to CadQuery code to solid, with a repair loop.

The model writes a small Python file (the contract is in codegen_check.py). The server validates it, runs it in a
sandbox (codegen_run.py), and if that fails the error goes back to the model for another try, up to `max_attempts`.
After that the sliders re-run the SAME code with new numbers and never call the model again.

The `llm` argument is the same callable the rest of the app uses (ai_models.make_llm): messages in, text out. It may
force JSON replies, so the reply format is a JSON object {"summary": "...", "code": "..."} and a fenced Python block
is accepted as a fallback.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

import codegen_check as CC

LLM = Callable[[list[dict]], str]
Runner = Callable[..., Awaitable[dict[str, Any]]]      # runner(code, overrides, exports, library_parts) -> result dict

SYSTEM_PROMPT = """You write CadQuery (Python, OpenCascade) code that builds ONE 3D model from a short request.
Reply with a single JSON object: {"summary": "<one or two plain sentences on what you built and any assumption>", "code": "<the python file>"}.

The code must follow this exact shape:

import cadquery as cq
import math

PARAMS = {
    "length": {"default": 120, "min": 40, "max": 400, "label": "Length", "unit": "mm"},
    "wall":   {"default": 3,   "min": 1,  "max": 10,  "label": "Wall thickness", "unit": "mm"},
}

def build(p):
    body = cq.Workplane("XY").box(p["length"], 60, 40)
    return body

Rules:
- PARAMS is a plain literal dict, lower_snake_case names, numbers only. Each has default, min, max and a short label.
  Expose the 4 to 12 dimensions a person would want to change with a slider. Keep every default inside its range.
  Use "unit": "deg" for angles and add "int": true for counts. Do not put expressions or variables inside PARAMS.
- build(p) takes the dict of values and returns a cq.Workplane, a cq.Shape, or a list of them. Everything is millimetres, Z is up.
- Allowed imports: cadquery and math. No other imports. No file access, no exporting, no printing, no classes, no `with`.
- Allowed built-ins: abs, all, any, bool, divmod, enumerate, float, int, len, list, map, max, min, pow, range, reversed, round, set, sorted, str, sum, tuple, zip, dict, isinstance.
- Never use names or attributes that start with an underscore, and never call anything named export, import or save: the server exports.
- Build real closed solids and make the result ONE connected solid unless the request is clearly an assembly of separate parts. Union parts that touch.
- Model what the request names with recognisable shapes: slopes, tapers, handles, wheels and holes where they belong. A plain box is wrong for anything that is not a box.
  For a hollow object, build the outer shape and cut the inside. For a tapered object use a loft or a tapered extrude. Position parts so they touch or overlap slightly.
- Keep it robust: avoid fillets and chamfers on tiny edges, keep wall thicknesses above the smallest slider value, and make sure no parameter combination inside the ranges gives a negative size.
- Parameters must not break the model across their full ranges: derive dependent sizes from the parameters instead of hard-coding them.
- Short comments for each section are welcome. Keep the file under 150 lines.

Standard parts. For standard hardware only (bearings, screws, nuts, bolts, standard connectors), you may ask for a part from an open
library instead of modelling it. Never use it for the main body or anything custom. Declare it above build, and take `lib` as a second argument:

    LIBRARY = {"bearing": {"query": "608 bearing"}}

    def build(p, lib):
        brg = lib["bearing"]                      # a cq.Workplane in millimetres, centred on the origin
        bb = brg.val().BoundingBox()              # bb.xlen, bb.ylen, bb.zlen: read its real size, do not guess it
        return body.union(brg.translate((0, 0, p["height"] / 2)))

- LIBRARY is a plain literal: names in lower_snake_case, a short "query" (2 to 80 letters, digits or spaces, such as "M3 socket screw"), and optionally "scale" (25.4 for a part drawn in inches).
- Library parts have fixed sizes. Build your own geometry around them using bb. Do not scale them to fit unless the query clearly needs it.
- Up to 6 parts. If a query finds nothing you will be told: then model that part yourself. Without a LIBRARY, build takes one argument.
"""

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def parse_reply(text: str) -> tuple[str, str]:
    """(summary, code) from the model's reply. Raises ValueError with a plain message."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The reply was empty.")
    code = summary = None
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                code = obj.get("code")
                summary = obj.get("summary")
        except ValueError:
            pass
    if not isinstance(code, str):
        m = _FENCE.search(text)
        if m:
            code = m.group(1)
    if not isinstance(code, str) or not code.strip():
        raise ValueError('The reply must be a JSON object with a "code" string.')
    return (summary.strip()[:400] if isinstance(summary, str) else ""), code.strip("\n") + "\n"


def _repair_message(problems: str) -> dict:
    return {"role": "user", "content": "That code failed:\n" + problems[:900] +
            '\nFix it and reply again with the full JSON object {"summary", "code"}. Return the complete file, not a patch.'}


async def generate(prompt: str, llm: LLM, runner: Runner, *, resolver: Any = None, max_attempts: int = 3,
                   previous_code: str | None = None, exports: tuple[str, ...] = ()) -> dict[str, Any]:
    """Build from a request, or revise `previous_code` when given (then `prompt` is the change to make).

    `resolver` (codegen_parts.PartResolver) fetches the standard parts the code asks for in LIBRARY. Returns
    {"ok": True, "code", "summary", "params", "library", "result", "attempts"} or {"ok": False, "errors": [...], "attempts"}.
    Infrastructure errors raised by the runner (busy, kernel missing) propagate: they are not the model's fault.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "errors": ["Describe what to build."], "attempts": 0}
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if previous_code:
        messages.append({"role": "user", "content": "Here is the current code:\n```python\n" + previous_code + "\n```\nChange it as asked and "
                         "keep everything else the same unless the change needs it. Keep PARAMS names that still apply so a person's slider "
                         "settings survive. The change: " + prompt})
    else:
        messages.append({"role": "user", "content": prompt})
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            reply = await asyncio.to_thread(llm, messages)
        except Exception:                                    # noqa: BLE001  provider failure: do not leak details
            return {"ok": False, "errors": ["The AI could not be reached. Try again."], "attempts": attempt, "infra": True}
        messages.append({"role": "assistant", "content": reply if isinstance(reply, str) else ""})
        try:
            summary, code = parse_reply(reply)
        except ValueError as e:
            errors.append(str(e))
            messages.append(_repair_message(str(e)))
            continue
        problems = CC.validate(code)
        if problems:
            errors.append(" ".join(problems[:3]))
            messages.append(_repair_message("\n".join(problems[:6])))
            continue
        wanted = CC.parse_library(code)
        parts: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        if wanted:
            if resolver is None:
                errors.append("Library parts are not available here.")
                messages.append(_repair_message("Library parts are not available here. Remove LIBRARY and model those parts yourself."))
                continue
            res = await resolver.resolve(wanted)
            if not res.get("ok"):
                errors.append(str(res.get("error")))
                messages.append(_repair_message(str(res.get("error"))))
                continue
            parts, entries = res["parts"], res["entries"]
        result = await runner(code, {}, exports, parts)
        if result.get("ok"):
            return {"ok": True, "code": code, "summary": summary, "params": CC.parse_params(code), "library": entries,
                    "result": result, "attempts": attempt}
        errors.append(str(result.get("error") or "The code did not build."))
        messages.append(_repair_message(errors[-1]))
    return {"ok": False, "errors": errors[-3:], "attempts": max_attempts}

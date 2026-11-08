"""The Studio chat's side of code-first CAD.

When a message goes to the code builder, what the saved chat part looks like, and the plain facts the Studio's
requirement checks read from it. Nothing in here needs FastAPI, CadQuery or a database. The code builder itself
(codegen_api, codegen_check, the sandbox) stays in its own modules. chat_service reaches it through three small
callables on its Deps, built by Bridge below.

A code part is an ordinary chat part with part_type "codegen" and no job:

    spec = {"part_type": "codegen",
            "parameters": {"codegen": {"id": <saved model id>, "values": {<slider name>: <number>}}},
            "material": <name or None>, "operations": []}

The shape itself lives in codegen_store. The chat part only points at it, the same way a vehicle part points at its
spec, so versions, requirements and the manufacturability check keep working on it.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import threading
from typing import Any

import materials

logger = logging.getLogger(__name__)

PART_TYPE = "codegen"
BRIDGE_TIMEOUT_S = 900          # writing code can take several model calls and a kernel run

_CODE_ASK = re.compile(
    r"\b(write|generate|use|build|make)\b[^.?!]{0,30}\b(code|script|program)\b|\bcodegen\b|\bcode[- ]first\b"
    r"|\bcustom (shape|part|geometry|model)\b|\bfrom code\b", re.I)
_BUILD = re.compile(r"\b(make|build|design|create|model|draw|generate|need|want|give me|i'd like|i would like)\b", re.I)
_DIMENSION = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|in|inch|inches)\b", re.I)
_QUESTION = re.compile(r"^\s*(what|why|how|which|who|when|where|can|could|does|do|is|are|will|would|should)\b", re.I)
_POLITE = re.compile(r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\b", re.I)
_ACTION = re.compile(
    r"\b(make|add|change|remove|put|give|set|use|turn|increase|decrease|reduce|enlarge|shrink|move|rotate|cut|drill|"
    r"extend|shorten|lengthen|widen|thicken|round|chamfer|fillet|scale|resize|replace|swap)\b", re.I)
_FILLER = frozenset("""make it use set change switch swap to in the a an from please material out of be made with using
let lets go instead as and make's can you could would will i want need should now just""".split())
_NEW_PART = re.compile(r"\b(new part|start over|start again|from scratch|another part|a different part|something else|instead)\b", re.I)


# ---------------------------------------------------------------------------------------------- the part

def ref(spec: dict | None) -> dict[str, Any] | None:
    """{"id", "values"} when this chat spec is a code part, else None."""
    if not isinstance(spec, dict) or spec.get("part_type") != PART_TYPE:
        return None
    r = (spec.get("parameters") or {}).get(PART_TYPE)
    if not isinstance(r, dict) or not r.get("id"):
        return None
    return {"id": str(r["id"]), "values": dict(r.get("values") or {})}


def is_part_spec(spec: dict | None) -> bool:
    return ref(spec) is not None


def make_spec(model_id: str, values: dict[str, Any] | None, material: str | None = None) -> dict[str, Any]:
    nums = {str(k): v for k, v in (values or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"part_type": PART_TYPE, "parameters": {PART_TYPE: {"id": str(model_id), "values": nums}},
            "material": material, "operations": []}


def title(out: dict[str, Any]) -> str:
    text = " ".join(str(out.get("summary") or "").split())
    return (text[:57] + "...") if len(text) > 60 else (text or "Custom part")


def reply(out: dict[str, Any], *, revised: bool) -> str:
    n = len(out.get("params") or [])
    lead = "Updated the code for this part." if revised else "No catalogue part fits that, so I wrote it as code."
    summary = " ".join(str(out.get("summary") or "").split())
    bits = [lead + (f" {summary}" if summary and not summary.endswith(".") else (f" {summary}" if summary else ""))]
    if n:
        bits.append(f"It has {n} adjustable value{'s' if n != 1 else ''}. Open the part to move them for free, no AI needed.")
    return " ".join(bits)


def _material_words() -> frozenset[str]:
    keys = list(materials.MATERIALS) + list(getattr(materials, "_ALIASES", {}))
    return frozenset(w for k in keys for w in re.findall(r"[a-z0-9]+", str(k).lower()))


def material_of(text: str) -> str | None:
    """The canonical material name when the whole message is only about the material ("make it stainless steel"),
    else None. A message that also changes the shape ("make the slot wider in steel") is not material-only."""
    t = (text or "").strip()
    if not t or len(t.split()) > 9 or is_question(t):
        return None
    words = re.findall(r"[a-z0-9]+", t.lower())
    left = [w for w in words if w not in _FILLER]
    known = _material_words()
    if not left or not all(w in known for w in left):
        return None
    found = materials.lookup(t)
    return found.key if found else None


def facts_from_stats(stats: dict[str, Any] | None, material: str | None = None) -> dict[str, Any]:
    """The measurable facts the requirement checks read, from the code builder's own solid measurements.
    Wall thickness is unknown here (there are no template rules for a free shape). The manufacturability check
    measures it on the mesh."""
    stats = stats or {}
    size, volume = stats.get("size"), stats.get("volume")
    facts: dict[str, Any] = {"material": material, "size_mm": None, "mass_g": None, "min_wall_mm": None, "mass_note": None,
                             "volume_mm3": round(float(volume), 3) if volume else None}
    if isinstance(size, (list, tuple)) and len(size) == 3:
        facts["size_mm"] = [round(float(v), 3) for v in size]
    density = materials.density_g_cm3(material)
    if volume and density:
        facts["mass_g"] = round(float(volume) / 1000.0 * density, 3)
    elif volume:
        facts["mass_note"] = "set a material to get mass"
    return facts


# ----------------------------------------------------------------------------------------------- routing

def is_question(text: str) -> bool:
    t = (text or "").strip()
    if _POLITE.match(t) and _ACTION.search(t):
        return False                      # "can you make the hole bigger?" is a request, not a question
    return t.endswith("?") or bool(_QUESTION.match(t))


def asks_for_code(text: str) -> bool:
    return bool(_CODE_ASK.search(text or ""))


def looks_like_build(text: str) -> bool:
    t = (text or "").strip()
    if len(t.split()) < 3 or is_question(t):
        return False
    return bool(_BUILD.search(t) or _DIMENSION.search(t))


_BARE_WORDS = frozenset("""yes yeah yep yup ok okay sure please do it go ahead write generate build make use as code script program
codegen first from and then now just thanks thank you that this""".split())
PROMPT_LIMIT = 2000             # GenerateBody.prompt allows 2000 characters
CONTEXT_MESSAGES = 20


def _answer_part(content: str) -> str:
    """A tapped answer arrives as "Question? Answer". Only the answer is the person's own words."""
    t = " ".join(str(content or "").split())
    return t.rsplit("? ", 1)[1].strip() if "? " in t else t


def start_prompt(text: str, history: list[dict] | None, limit: int = PROMPT_LIMIT) -> str:
    """The prompt for a NEW code part. A message that names its own shape goes as written. A bare "yes, write it as
    code" names nothing, and sent alone it made the builder invent an object, so the person's earlier messages since
    the last saved part go with it, oldest first: the request and the answers to our questions."""
    text = " ".join(str(text or "").split())
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _BARE_WORDS]
    if len(words) >= 3 or _DIMENSION.search(text):
        return text[:limit]
    earlier: list[str] = []
    for m in reversed(list(history or [])):
        if m.get("role") == "assistant" and m.get("part_id"):
            break                                              # a part was saved here: older messages are about it
        if m.get("role") == "user":
            content = " ".join(str(m.get("content") or "").split())
            if content and content != text:
                earlier.append(_answer_part(content))
        if len(earlier) >= CONTEXT_MESSAGES:
            break
    earlier.reverse()
    if not earlier:
        return text[:limit]
    head = "Write this as code. What the person asked for, in their own words, oldest first: "
    body = head + " | ".join(earlier)
    while len(body) > limit and len(earlier) > 2:              # keep the first request and the newest answers
        del earlier[1]
        body = head + " | ".join(earlier)
    return body[:limit]


def pre_plan_mode(text: str, current_spec: dict | None) -> str | None:
    """Decided before the template planner runs (it would only waste an AI call): "material" for a message that only
    sets the material of a code part (free, no code is rewritten), "revise" for any other edit to a code part, "start"
    when the person asks for code outright, else None."""
    if is_part_spec(current_spec):
        if is_question(text) or _NEW_PART.search(text or ""):
            return None
        return "material" if material_of(text) else "revise"
    return "start" if asks_for_code(text) else None


# ------------------------------------------------------------------------------------------------ bridge

def run_sync(coro: Any) -> Any:
    """Run a coroutine to the end from synchronous code. Only used when there is no server loop to hand it to."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:      # noqa: BLE001 - handed back to the caller below
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


class Bridge:
    """Synchronous callables for chat_service.Deps, over codegen_api's async service.

    chat_service.run_turn runs in a worker thread, and the code builder's kernel limit is tied to the server's own
    event loop. So when the server loop is given, each call is handed to that loop and the worker waits for it,
    rather than starting a second loop that would count kernel slots separately. Without a loop (tests) it runs
    the coroutine itself."""

    def __init__(self, service: Any, model_id: str | None, loop: Any = None):
        self.service, self.model_id, self.loop = service, model_id, loop

    def _run(self, coro: Any) -> Any:
        if self.loop is None:
            return run_sync(coro)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=BRIDGE_TIMEOUT_S)
        except (concurrent.futures.TimeoutError, TimeoutError):
            fut.cancel()               # the code builder refunds in its own finally block when nothing was saved
            raise

    def _answer(self, make_coro: Any) -> dict[str, Any]:
        try:
            p = self._run(make_coro())
        except Exception as e:         # noqa: BLE001
            # codegen_api raises FastAPI HTTPException for refusals (no credits, plan limit, bad code). Those carry a
            # status_code and a person-readable detail. Anything else is a real fault and goes up to the caller.
            status, detail = getattr(e, "status_code", None), getattr(e, "detail", None)
            if isinstance(status, int) and isinstance(detail, str):
                return {"ok": False, "message": detail[:600], "needs_credits": status == 402}
            raise
        return {"ok": True, "id": p["id"], "summary": p.get("summary", ""), "values": p.get("values") or {},
                "stats": p.get("stats") or {}, "params": p.get("params") or [], "charged": int(p.get("charged") or 0),
                "library": p.get("library") or []}

    def start(self, user_id: str, prompt: str) -> dict[str, Any]:
        return self._answer(lambda: self.service.generate(user_id, prompt, self.model_id, None))

    def revise(self, user_id: str, model_id: str, instruction: str) -> dict[str, Any]:
        async def go() -> Any:
            previous = self.service.owned(user_id, model_id)
            return await self.service.generate(user_id, instruction, self.model_id, previous)
        return self._answer(go)

    def mesh(self, user_id: str, spec_ref: dict[str, Any]) -> Any:
        """(vertices, triangles) in mm for the manufacturability check, rebuilt at the part's own slider values."""
        import mesh_formats

        async def go() -> Any:
            _entry, res, _library = await self.service.build(user_id, spec_ref["id"], spec_ref.get("values") or {}, ())
            return mesh_formats.read_stl(res["stl"])
        return self._run(go())

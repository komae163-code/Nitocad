"""
Plain-language request -> validated vehicle spec, with a retry loop and a library that learns from feedback.

    plan_vehicle("a two-wheel garden wheelbarrow with a 90 litre tray", llm, library)

`llm` is any function that takes chat messages [{"role","content"}, ...] and returns the reply text. It is injected
so this module has no network code and can be tested offline. Wire it to your real model in one small adapter.

What it does
  1. Builds a prompt: the spec format, the real part catalog, the real module library, the most similar PROVEN
     specs from the library as examples, and the most frequent past mistakes as warnings.
  2. Asks the model for a JSON spec, parses it, and validates it with vehicle_spec.expand_vehicle (which also runs
     the planner's own validator, so wrong part types, parameters, bounds, formulas and mates are all caught).
  3. If anything fails, sends the exact errors back and retries (default 3 attempts).
  4. A spec that fails its own design checks gets one more chance to be fixed; if attempts run out it is returned
     with the failing checks listed, never silently.
  5. A valid spec is stored as a CANDIDATE. It becomes an example for future requests only after you give
     positive feedback through library.feedback(id, True).

What it does not do
  * A valid spec is not a correct or safe design. Nothing here builds or measures geometry, and nothing checks
    strength. Safety-critical products (wheelchairs, aircraft, anything people ride or fly) need real engineering.
  * It cannot make parts the catalog and modules do not contain; it is told so and told to say so in `notes`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import spec_library as SL
import vehicle_spec as VS

LLM = Callable[[list[dict[str, str]]], str]
MAX_DESCRIPTION = 2000
MAX_EXAMPLE_CHARS = 9000
# Overall wall-clock budget for one plan (all attempts). The browser gives up at 170 s, so stay well under it.
DEFAULT_TIME_BUDGET_S = 120.0
# A retry is only started if at least this many seconds are left, since one model call can take that long.
MIN_SECONDS_FOR_RETRY = 25.0

FORMAT_GUIDE = """You write vehicle specs as ONE JSON object. Units are mm, X long, Y lateral, Z up. Reply with JSON only.

Keys: name (snake_case), notes (say what is a simplification), params {name: number | {default,min,max,kind}},
derived {name: "=expression"}, components [...], checks [{name, expr:"=...", message}], explode {directions:{part:[x,y,z]}}.

Strings starting with "=" are formulas: + - * / ** %, comparisons, and/or/not, and min max abs sqrt sin cos tan atan2
radians degrees round floor ceil hypot approx(a,b,tol), plus pi. Names are your params/derived values only.
Every derived value MUST be a string starting with "=" (write "=20", not 20).

A component is one of:
  {"id","type","parameters":{...},"position":[x,y,z],"rotation":[rx,ry,rz],"assembly","material","fixed"}
  with optional "repeat":{"n","step":[dx,dy,dz]} (ids get _1.._n; i and n usable in formulas)
  or "mirror":"y" (ids get _l/_r; only the y position flips, set rotation yourself)
  or "mate":{"type":"face"|"coaxial","to":"earlier_part",...} instead of position (quarter-turn rotations only)
  or "between":[[x,y,z],[x,y,z]] for tubes/shafts drawn from end point to end point (X-Z plane or along Y only)
  {"id","module","params":{...},"position":[x,y,z],"assembly"}  places a reusable module (translation only)
  {"id","source":{"provider","query"},"position":[x,y,z]}  an external part fetched later
ids are lowercase snake_case. A part needs `position` OR `mate` OR `between`, never two.
Use ONLY the part types and modules listed below, with their exact parameter names. If something you need does not
exist, approximate it with what does and say so in `notes`. Add `checks` that would catch an impossible layout."""


def _catalog_text(catalog: dict[str, dict[str, Any]]) -> str:
    lines = []
    for name, spec in sorted(catalog.items()):
        ps = spec.get("parameters") or spec
        names = list(ps) if isinstance(ps, dict) else []
        lines.append(f"- {name}: {', '.join(map(str, names))}")
    return "\n".join(lines)


def _modules_text(module_dir: Path | None) -> str:
    mods = VS.list_modules(module_dir)
    lines = []
    for n, m in mods.items():
        ps = []
        for k, v in (m["params"] or {}).items():
            ps.append(f"{k}({v['default']})" if isinstance(v, dict) and "default" in v else str(k))
        lines.append(f"- {n}: {m['description']} params: {', '.join(ps)}")
    return "\n".join(lines) or "(none)"


def build_messages(description: str, library: SL.SpecLibrary, catalog: dict[str, dict[str, Any]],
                   module_dir: Path | None, k_examples: int = 2) -> tuple[list[dict[str, str]], list[str]]:
    examples, used = [], []
    for rec in library.search(description, k=k_examples + 2):
        shown = {k: v for k, v in rec["spec"].items() if k not in ("notes", "dev_notes")}      # free text stays out of prompts
        text = json.dumps(shown, separators=(",", ":"))
        if len(text) <= MAX_EXAMPLE_CHARS and len(used) < k_examples:
            examples.append(f"Example ({rec['status']}) named \"{rec['name']}\":\n{text}")
            used.append(rec["id"])
    lessons = library.lessons()
    system = (FORMAT_GUIDE + "\n\nPART TYPES (exact names and parameters):\n" + _catalog_text(catalog)
              + "\n\nMODULES (reusable internals):\n" + _modules_text(module_dir))
    if lessons:
        system += "\n\nMISTAKES SEEN BEFORE, avoid them:\n" + "\n".join(f"- {l}" for l in lessons)
    if examples:
        system += "\n\nPROVEN EXAMPLES:\n" + "\n\n".join(examples)
    return [{"role": "system", "content": system}, {"role": "user", "content": description}], used


def extract_json(text: str) -> dict[str, Any]:
    """First JSON object in the reply (fences and chatter tolerated). Raises ValueError with a plain message."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the reply was empty")
    t = re.sub(r"```(?:json)?", "", text)
    i = t.find("{")
    if i < 0:
        raise ValueError("the reply contained no JSON object")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[i:])
    except ValueError as e:
        raise ValueError(f"the JSON could not be read: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("the JSON must be an object")
    return obj


def _feedback_message(errors: list[str]) -> str:
    shown = [e[:300] for e in errors[:15]]
    more = f"\n(and {len(errors) - 15} more)" if len(errors) > 15 else ""
    return ("Your spec has these problems:\n" + "\n".join(f"- {e}" for e in shown) + more
            + "\nReturn the corrected, complete JSON object only.")


def _run_loop(messages: list[dict[str, str]], llm: LLM, cat: dict[str, dict[str, Any]], module_dir: Path | None,
              max_attempts: int, time_budget_s: float | None = DEFAULT_TIME_BUDGET_S,
              clock: Callable[[], float] | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str], bool]:
    """Ask, parse, validate, and send the exact errors back until a spec is valid, attempts run out, or the time
    budget leaves too little for another try. The last value is True when the budget ended the loop.
    One model call is still bounded only by the model's own timeout (AI_TIMEOUT_SECONDS)."""
    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    last_errors: list[str] = []
    clock = clock or time.monotonic
    started = clock()
    timed_out = False
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and time_budget_s is not None and time_budget_s - (clock() - started) < MIN_SECONDS_FOR_RETRY:
            timed_out = True
            if best is None:
                last_errors = last_errors + [f"planning stopped after {int(clock() - started)} s to stay inside the "
                                             f"{int(time_budget_s)} s time limit"]
            break
        try:
            reply = llm(messages)
        except Exception as e:                                              # noqa: BLE001 - any model/network failure
            last_errors = [f"the model call failed: {type(e).__name__}: {str(e)[:200]}"]
            history.append({"attempt": attempt, "errors": last_errors})
            break
        messages.append({"role": "assistant", "content": reply if isinstance(reply, str) else ""})
        try:
            spec = extract_json(reply)
        except ValueError as e:
            last_errors = [str(e)]
            history.append({"attempt": attempt, "errors": last_errors})
            messages.append({"role": "user", "content": _feedback_message(last_errors)})
            continue
        ex = VS.expand_vehicle(spec, None, cat, module_dir)
        if not ex["ok"]:
            last_errors = ex["errors"]
            history.append({"attempt": attempt, "errors": last_errors})
            messages.append({"role": "user", "content": _feedback_message(last_errors)})
            continue
        bad_checks = [c for c in ex["checks"] if c["status"] != "pass"]
        best = {"spec": spec, "expanded": ex, "bad_checks": bad_checks}
        history.append({"attempt": attempt, "errors": [], "failed_checks": [c["name"] for c in bad_checks]})
        if not bad_checks:
            break
        msg = [f"design check {c['name']!r} did not pass: {c['detail'] or c['status']}" for c in bad_checks]
        messages.append({"role": "user", "content": _feedback_message(msg)})
    return best, history, last_errors, timed_out


def plan_vehicle(description: str, llm: LLM, library: SL.SpecLibrary, *, max_attempts: int = 3,
                 catalog: dict[str, dict[str, Any]] | None = None, module_dir: Path | None = None,
                 k_examples: int = 2, save_candidate: bool = True,
                 time_budget_s: float | None = DEFAULT_TIME_BUDGET_S,
                 clock: Callable[[], float] | None = None) -> dict[str, Any]:
    if not isinstance(description, str) or not 3 <= len(description.strip()) <= MAX_DESCRIPTION:
        return {"ok": False, "errors": [f"description must be 3 to {MAX_DESCRIPTION} characters"], "attempts": 0}
    if not callable(llm):
        return {"ok": False, "errors": ["llm must be a function"], "attempts": 0}
    max_attempts = max(1, min(int(max_attempts), 6))
    cat = catalog if catalog is not None else VS._default_catalog()
    messages, used = build_messages(description.strip(), library, cat, module_dir, k_examples)
    best, history, last_errors, timed_out = _run_loop(messages, llm, cat, module_dir, max_attempts, time_budget_s, clock)

    if best is None:
        library.record_failure(description, last_errors)
        return {"ok": False, "errors": last_errors, "attempts": len(history), "history": history, "examples_used": used,
                "timed_out": timed_out}

    cid = None
    if save_candidate:
        # The person's own words are NOT stored with an AI-planned spec: accepted specs are shown to other people as
        # examples, and one person's request must never reach another's prompt. Search uses the spec's name and parts.
        added = library.add(best["spec"], "", status="candidate")
        cid = added.get("id") if added.get("ok") else None
    if best["bad_checks"]:
        library.record_failure(description, [f"design check {c['name']} failed" for c in best["bad_checks"]])
    return {"ok": True, "spec": best["spec"], "expanded": best["expanded"], "attempts": len(history), "history": history,
            "examples_used": used, "candidate_id": cid, "timed_out": timed_out,
            "unresolved_checks": [{"name": c["name"], "detail": c["detail"]} for c in best["bad_checks"]],
            "note": "Valid against the part catalog and your own checks. Not a verified or load-rated design."}


def plan_and_build(description: str, llm: LLM, library: SL.SpecLibrary, out_dir: str | Path, **kw: Any) -> dict[str, Any]:
    """Plan, then run the whole pipeline (vehicle_build.build_vehicle). kw: planner options plus build options
    prefixed with `build_` (e.g. build_bbox_fn, build_registry, build_export_fn, build_overrides)."""
    import vehicle_build as VB
    build_kw = {k[6:]: v for k, v in kw.items() if k.startswith("build_")}
    plan_kw = {k: v for k, v in kw.items() if not k.startswith("build_")}
    plan = plan_vehicle(description, llm, library, **plan_kw)
    if not plan["ok"]:
        return {"ok": False, "stage": "plan", "plan": plan}
    built = VB.build_vehicle(plan["spec"], out_dir, **build_kw)
    return {"ok": built["ok"], "stage": "build", "plan": plan, "build": built}


def revise_vehicle(current_spec: dict[str, Any], instruction: str, llm: LLM, library: SL.SpecLibrary, *,
                   max_attempts: int = 3, catalog: dict[str, dict[str, Any]] | None = None,
                   module_dir: Path | None = None, time_budget_s: float | None = DEFAULT_TIME_BUDGET_S,
                   clock: Callable[[], float] | None = None) -> dict[str, Any]:
    """Change an existing vehicle spec from a plain sentence. The model returns the COMPLETE updated spec, which goes
    through the same validate-and-retry loop. Edits are not stored in the library (only brand-new plans are)."""
    if not isinstance(instruction, str) or not 3 <= len(instruction.strip()) <= MAX_DESCRIPTION:
        return {"ok": False, "errors": [f"the change must be 3 to {MAX_DESCRIPTION} characters"], "attempts": 0}
    if not isinstance(current_spec, dict) or not callable(llm):
        return {"ok": False, "errors": ["a current spec and a model function are needed"], "attempts": 0}
    cat = catalog if catalog is not None else VS._default_catalog()
    base, _ = build_messages(instruction.strip(), library, cat, module_dir, k_examples=0)
    current = json.dumps(current_spec, separators=(",", ":"))
    messages = [base[0], {"role": "user", "content": (
        "CURRENT SPEC:\n" + current + "\n\nCHANGE REQUESTED: " + instruction.strip()
        + "\n\nReturn the COMPLETE updated spec as one JSON object. Change only what was asked; keep every other "
          "id, parameter and check as it is. If the change needs a part the catalog lacks, approximate it and say so in `notes`.")}]
    best, history, last_errors, timed_out = _run_loop(messages, llm, cat, module_dir, max(1, min(int(max_attempts), 6)),
                                                      time_budget_s, clock)
    if best is None:
        library.record_failure(instruction, last_errors)
        return {"ok": False, "errors": last_errors, "attempts": len(history), "history": history, "timed_out": timed_out}
    return {"ok": True, "spec": best["spec"], "expanded": best["expanded"], "attempts": len(history), "history": history,
            "timed_out": timed_out,
            "unresolved_checks": [{"name": c["name"], "detail": c["detail"]} for c in best["bad_checks"]]}

"""
One turn of a CAD Studio chat, and the step that pays for the result.

Everything it needs from the outside world comes in through `Deps`, so the flow is the
same in production (web_app wires the real generator, credits and DeepSeek) and in
tests (fakes).

Preview first, pay when you generate. A message never costs credits:

  run_turn      1. persist the user's message   (so a crash never loses what they typed)
                2. plan what it means           (answers cost nothing)
                3. build a free preview         (limited per wallet per day)
                4. persist the part and the reply

  unlock_part   The only place credits are spent. The person looks at the preview, and when
                they press Generate it charges once for that version, builds the full files
                (STEP, IGES, DXF, PDF) and unlocks them. If the files cannot be built the
                charge is refunded. Pressing Generate twice never charges twice.

That way fixing a wrong dimension costs nothing, and the person pays only for a version
they are happy with.
"""
from __future__ import annotations

import hashlib
import json
import re
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Callable

import chat_store
import conversation
import dfm
import edit_diff
import import_ops
import import_store
import manufacturability
import requirements as reqmod
import studio_templates
from conversation import Plan
from validator import merge_notes

TITLE_CHARS = 44


class ChatNotFound(Exception):
    pass


class ChatBusy(Exception):
    """Another message in this chat is still being processed."""


class ImportNotFound(Exception):
    """The attached file is not stored any more (or never was for this account)."""


class PartNotFound(Exception):
    pass


class NeedCredits(Exception):
    def __init__(self, balance: int = 0, cost: int = 1):
        super().__init__("out of credits")
        self.balance = balance
        self.cost = cost


class FilesFailed(Exception):
    """Paying worked but the files could not be built. The charge has been refunded."""


class CheckRefused(Exception):
    """This part or process cannot be checked. The message is safe to show."""


class CheckFailed(Exception):
    """The check could not be run, so nothing was charged."""


class TemplateNotFound(Exception):
    pass


@dataclass
class Deps:
    # (history, current spec) -> Plan. A planner that also accepts `facts=` is given the measured facts
    # of the current version (see conversation.facts_digest); one that does not is called with two arguments.
    plan: Callable[..., Plan]
    generate: Callable[[str, dict, str, str], dict]  # (description, spec, user_id, source) -> generator result
    balance: Callable[[str], int]
    # Every message that calls an AI model uses part of a daily allowance for that AI tier.
    # ai_allowed says whether any is left, ai_used records one. Both are bound to the tier
    # the person picked when the Deps are built.
    ai_allowed: Callable[[str], bool] = lambda user_id: True
    ai_used: Callable[[str], None] = lambda user_id: None
    # What pressing Generate costs for this job (set by the AI tier that planned it).
    job_cost: Callable[[str], int] = lambda job_id: 1
    # Paying for one preview's full files (see unlock_part).
    charge_job: Callable[[str, str], tuple[str, str | None]] | None = None  # (job_id, user_id) -> (status, pool)
    refund_job: Callable[[str, str], Any] | None = None                      # (job_id, user_id)
    build_files: Callable[[str], None] | None = None                         # raises if the files cannot be built
    # Manufacturability check (see run_check). part_mesh gives (vertices, triangles) in mm or None.
    part_mesh: Callable[[str], Any] | None = None                            # (job_id) -> (verts, tris) | None
    check_cost: int = 5
    # What a plain answer costs (a question, advice, a clarifying reply) when an AI plan wrote it. Previews cost
    # nothing. uses_ai says whether the plan the person picked calls an AI model at all (Engine does not).
    answer_cost: int = 0
    uses_ai: bool = False
    charge_credits: Callable[[str, int], str | None] | None = None           # (user_id, amount) -> pool or None
    refund_credits: Callable[[str, str | None, int], Any] | None = None      # (user_id, pool, amount)
    history_limit: int = 30
    max_chats: int = 200
    max_messages: int = 400


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(chat_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(chat_id, threading.Lock())


def titleize(part_type: str) -> str:
    if part_type == "pcb_board":
        return "PCB board"
    return (part_type or "part").replace("_", " ").strip().capitalize()


def part_title(spec: dict) -> str:
    """An imported part is named after its file. Everything else after its type."""
    if spec.get("part_type") == "imported":
        name = str((spec.get("parameters") or {}).get("filename") or "").strip()
        if name:
            return name[:40]
    return titleize(spec.get("part_type"))


def _chat_title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= TITLE_CHARS else t[: TITLE_CHARS - 2].rstrip() + "..."


def _spec_from_result(result: dict, fallback: dict) -> dict:
    """What was actually built, including any auto-corrections the validator made, so the
    next edit starts from the real values and not from what was asked for."""
    p = result.get("parameters") or {}
    return {
        "part_type": p.get("part_type") or fallback["part_type"],
        "parameters": p.get("parameters") or fallback["parameters"],
        "material": p.get("material") or fallback.get("material"),
        "operations": p.get("operations") or fallback.get("operations") or [],
    }


def _keep_vehicle(planned: dict, built: dict) -> dict:
    """The generator keeps only an assembly's parts list. A vehicle part also keeps the vehicle spec it came from,
    so the next edit, the exploded view and the package start from it. Never raises."""
    try:
        import vehicle_chat
        return vehicle_chat.restore_vehicle(planned, built)
    except Exception:  # noqa: BLE001
        return built


def _notes_sentence(notes: list[str]) -> str:
    """Say what actually happened: kept-as-typed flags are not adjustments."""
    if not notes:
        return ""
    kept = [n for n in notes if "Kept as you typed" in n]
    if len(kept) == len(notes):
        return " I kept the sizes you typed and flagged a few that may cause problems. Details are under Specs."
    if kept:
        return " I adjusted some values and flagged others you typed. Details are under Specs."
    return " I adjusted a few values to keep it manufacturable. Details are under Specs."


def _notes(result: dict) -> list[str]:
    v = result.get("validation") or {}
    return merge_notes(v.get("warnings") or [], v.get("corrections") or {})[:12]


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _call_plan(deps: Deps, history: list[dict], spec: dict | None, facts: dict | None) -> Plan:
    try:
        params = inspect.signature(deps.plan).parameters
        takes_facts = "facts" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        takes_facts = False
    return deps.plan(history, spec, facts=facts) if takes_facts else deps.plan(history, spec)


def run_turn(deps: Deps, user_id: str, chat_id: str, text: str) -> dict[str, Any]:
    lock = _lock_for(chat_id)
    if not lock.acquire(blocking=False):
        raise ChatBusy()
    try:
        if chat_store.get_chat(user_id, chat_id) is None:
            raise ChatNotFound()

        user_msg = chat_store.add_message(user_id, chat_id, "user", text, max_messages=deps.max_messages)
        chat_store.set_title_if_default(user_id, chat_id, _chat_title(text))

        new_reqs = reqmod.parse_requirements(text)
        added = chat_store.add_requirements(user_id, chat_id, new_reqs) if new_reqs else []

        history = chat_store.recent_messages(user_id, chat_id, deps.history_limit)
        current = chat_store.latest_part(user_id, chat_id)
        current_spec = current["spec"] if current else None

        def evaluate(facts):
            return reqmod.evaluate(chat_store.list_requirements(user_id, chat_id), facts)

        plan: Plan | None = None

        def finish(content: str, *, part: dict | None = None, meta: dict | None = None, charged: int = 0, **extra) -> dict:
            msg = chat_store.add_message(user_id, chat_id, "assistant", content,
                                         part_id=part["id"] if part else None, meta=meta,
                                         max_messages=deps.max_messages + 1)
            return {"user_message": user_msg, "assistant_message": msg, "part": part,
                    "charged": charged, "balance": deps.balance(user_id), "needs_credits": False,
                    "intent": plan.intent if plan else "answer",
                    "plan_source": plan.source if plan else None,
                    "requirements": extra.pop("requirements", []), "new_requirements": added, **extra}

        # A question about making the current part ("can this be 3D printed?") is answered from the
        # part's real parameters by the manufacturability rules. It costs no AI message.
        manufacturing = dfm_answer(text, current) if current and not added else None
        if manufacturing:
            plan = Plan("answer", manufacturing, None, "rules")
            return finish(manufacturing, requirements=evaluate(current["facts"]))

        # Planning calls the AI, which costs real money. Someone with no credits left is asked to upgrade
        # before anything is spent. The Engine plan calls no AI, so it is never blocked here.
        if deps.uses_ai and deps.answer_cost > 0 and deps.balance(user_id) < deps.answer_cost:
            return finish("You have used all your credits. Upgrade to keep going. Nothing was changed.",
                          meta={"needs_credits": True}, needs_credits=True)
        if not deps.ai_allowed(user_id):      # kept for a plan that sets its own limit; the built-in ones have none
            return finish("You have used today's messages for this AI. Pick a different AI in the box below, "
                          "or try again later. Nothing was changed and nothing was charged.", meta={"limit": True})

        # The planner sees what the current version measured, so it knows what an edit would disturb.
        before_results = evaluate(current["facts"]) if current else []
        plan = _call_plan(deps, history, current_spec, conversation.facts_digest(current["facts"], before_results) if current else None)
        if plan.source == "llm":
            try:
                deps.ai_used(user_id)
            except Exception:  # noqa: BLE001 - a counting problem must never lose the person's answer
                import logging
                logging.getLogger(__name__).exception("ai usage log failed")

        if plan.intent == "answer" or plan.spec is None:
            reply = plan.reply or "Tell me a bit more about the part you want."
            results = evaluate(current["facts"]) if current else []
            if added:
                reply = "Noted. I will check every version against: " + "; ".join(reqmod.describe(r) for r in added) + "."
                if current:
                    reply += " " + _requirement_sentence(results, "The current version")
            # An answer the AI wrote costs credits. Noting a requirement, a rules reply and a failed AI call cost nothing.
            charged = 0
            if plan.source == "llm" and not added and deps.answer_cost > 0 and deps.charge_credits:
                try:
                    if deps.charge_credits(user_id, deps.answer_cost):
                        charged = deps.answer_cost
                except Exception:  # noqa: BLE001 - a billing hiccup must never lose the person's answer
                    import logging
                    logging.getLogger(__name__).exception("answer charge failed")
            return finish(reply, meta={"choices": plan.choices} if plan.choices else None, charged=charged, requirements=results)

        description = text.strip()
        try:
            result = deps.generate(description, plan.spec, user_id, plan.source)
        except Exception as exc:  # noqa: BLE001 - a failed preview must never break the chat
            return finish("That preview failed. You were not charged. " + (str(exc)[:200] or "Try rewording the change."),
                          meta={"error": True})

        if not result.get("success"):
            reason = (result.get("error") or "The preview did not complete.").strip()
            return finish(f"{reason} You were not charged." if "not charged" not in reason.lower() else reason,
                          meta={"error": True})

        spec = _spec_from_result(result, plan.spec)
        spec = _keep_vehicle(plan.spec, spec)
        facts = reqmod.facts_from_result(result, spec["part_type"])
        part = chat_store.add_part(
            user_id, chat_id, job_id=result.get("job_id"), title=part_title(spec),
            part_type=spec["part_type"], spec=spec, notes=_notes(result), facts=facts,
        )
        reply = plan.reply or _default_reply(plan, part)
        reply += _notes_sentence(part["notes"] or [])
        results = evaluate(facts)
        # What the edit did to the build, compared with the version before it. Built from the spec that
        # was actually made, so validator corrections and follow-on changes show up too.
        diff = None
        if plan.intent == "edit" and current:
            try:
                lines = conversation.label_changes(conversation.diff_specs(current["spec"], spec), plan.changes)
                diff = edit_diff.build(lines, current["facts"], facts, before_results, results, current.get("version"), part["version"])
            except Exception:  # noqa: BLE001 - a diff problem must never lose the person's new version
                diff = None
            reply += edit_diff.warning_sentence(diff, requirements=False)
        sentence = _requirement_sentence(results, f"Version {part['version']}")
        if sentence:
            reply += " " + sentence
        return finish(reply, part=part, requirements=results, meta={"diff": diff} if diff else None, diff=diff)
    finally:
        lock.release()


def unlock_part(deps: Deps, user_id: str, chat_id: str, part_id: str) -> dict[str, Any]:
    """Pay for one preview and unlock its full files. Spends at most one generation, once
    per version: calling it again for a version that is already paid returns charged=0."""
    if chat_store.get_chat(user_id, chat_id) is None:
        raise ChatNotFound()
    chat = chat_store.get_chat(user_id, chat_id)
    part = next((p for p in chat["parts"] if p["id"] == part_id), None)
    if part is None or not part.get("job_id"):
        raise PartNotFound()
    job_id = part["job_id"]
    if (part["spec"].get("parameters") or {}).get("kind") in import_ops.MESH_KINDS:
        # A mesh can only be exported as STL, and the STL is already free, so there is nothing to buy.
        return {"paid": True, "charged": 0, "balance": deps.balance(user_id), "job_id": job_id, "part_id": part_id,
                "mesh": True}

    lock = _lock_for("unlock:" + job_id)
    if not lock.acquire(blocking=False):
        raise ChatBusy()
    try:
        status, _pool = deps.charge_job(job_id, user_id)
        if status == "not_found":
            raise PartNotFound()
        if status == "insufficient":
            raise NeedCredits(deps.balance(user_id), deps.job_cost(job_id))
        try:
            deps.build_files(job_id)
        except Exception as exc:  # noqa: BLE001 - paying for nothing is never acceptable
            if status == "charged":
                _safe_refund_job(deps, user_id, job_id)
            raise FilesFailed(str(exc)[:200] or "The files could not be built.") from exc
        return {"paid": True, "charged": deps.job_cost(job_id) if status == "charged" else 0, "balance": deps.balance(user_id),
                "job_id": job_id, "part_id": part_id}
    finally:
        lock.release()


def start_import_part(deps: Deps, user_id: str, chat_id: str, import_id: str) -> dict[str, Any]:
    """Open a stored attached file as an editable part in the chat. Free, like every preview.
    The file is built as it came in. The person then edits it by chatting."""
    lock = _lock_for(chat_id)
    if not lock.acquire(blocking=False):
        raise ChatBusy()
    try:
        if chat_store.get_chat(user_id, chat_id) is None:
            raise ChatNotFound()
        rec = import_store.get(user_id, import_id)
        if rec is None:
            raise ImportNotFound()
        params: dict[str, Any] = {"import_id": rec.id, "kind": rec.kind, "filename": rec.filename, "edits": []}
        if rec.kind in import_ops.PROFILE_KINDS:
            params["thickness_mm"] = float(rec.meta.get("suggested_thickness_mm") or 3.0)
        spec = {"part_type": "imported", "parameters": params, "material": None, "operations": []}
        user_msg = chat_store.add_message(user_id, chat_id, "user", f"Edit {rec.filename}", max_messages=deps.max_messages)
        chat_store.set_title_if_default(user_id, chat_id, _chat_title(f"Edit {rec.filename}"))

        def finish(content: str, *, part: dict | None = None, meta: dict | None = None) -> dict:
            msg = chat_store.add_message(user_id, chat_id, "assistant", content, part_id=part["id"] if part else None,
                                         meta=meta, max_messages=deps.max_messages + 1)
            return {"user_message": user_msg, "assistant_message": msg, "part": part, "charged": 0,
                    "balance": deps.balance(user_id), "needs_credits": False, "intent": "build", "plan_source": "rules"}

        try:
            result = deps.generate(f"Edit {rec.filename}", spec, user_id, "rules")
        except Exception as exc:  # noqa: BLE001
            return finish("I could not open that file. " + (str(exc)[:200] or "Try attaching it again."), meta={"error": True})
        if not result.get("success"):
            return finish((result.get("error") or "I could not open that file.").strip(), meta={"error": True})
        built = _spec_from_result(result, spec)
        part = chat_store.add_part(user_id, chat_id, job_id=result.get("job_id"), title=part_title(built),
                                   part_type="imported", spec=built, notes=_notes(result),
                                   facts=reqmod.facts_from_result(result, "imported"))
        is_mesh = rec.kind in import_ops.MESH_KINDS
        how = ("You can scale, rotate, move, mirror, rest it on the ground or center it. Holes and fillets need a solid, "
               "so a mesh cannot take them." if is_mesh else
               "Try: scale it to 150 mm long, add a 6 mm hole at the center, fillet the edges 2 mm"
               + (", or make it 5 mm thick." if rec.kind in import_ops.PROFILE_KINDS else "."))
        return finish(f"Opened {rec.filename}. {how}", part=part)
    finally:
        lock.release()


def run_check(deps: Deps, user_id: str, chat_id: str, part_id: str, process: str) -> dict[str, Any]:
    """The manufacturability check for one part version and one process.

    Costs deps.check_cost credits once for that version and process. The report is stored, so opening it
    again returns it with charged=0. It runs no AI. The report is worked out first and the credits are
    taken after, so a check that cannot run never charges, and a charge is refunded if the report cannot
    be saved."""
    part = chat_store.get_part(user_id, chat_id, part_id)
    if part is None:
        raise PartNotFound()
    if not manufacturability.is_process(process):
        raise CheckRefused("Pick one of: " + ", ".join(manufacturability.PROCESSES) + ".")
    why = manufacturability.refusal(part["part_type"])
    if why:
        raise CheckRefused(why)

    saved = chat_store.get_check(user_id, chat_id, part_id, process)
    if saved is not None:
        return {"report": saved, "charged": 0, "balance": deps.balance(user_id), "part_id": part_id, "process": process}

    lock = _lock_for(f"check:{part_id}:{process}")
    if not lock.acquire(blocking=False):
        raise ChatBusy()
    try:
        saved = chat_store.get_check(user_id, chat_id, part_id, process)      # someone finished while we waited
        if saved is not None:
            return {"report": saved, "charged": 0, "balance": deps.balance(user_id), "part_id": part_id, "process": process}
        cost = deps.check_cost
        balance = deps.balance(user_id)
        if balance < cost:
            raise NeedCredits(balance, cost)
        spec = part["spec"]
        mesh = None
        if deps.part_mesh and part.get("job_id"):
            try:
                mesh = deps.part_mesh(part["job_id"])
            except Exception:  # noqa: BLE001 - no mesh means a dimensions-only check, which the report says
                import logging
                logging.getLogger(__name__).warning("part mesh unavailable for check", exc_info=True)
        try:
            report = manufacturability.run(part["part_type"], spec.get("parameters") or {}, spec.get("material"), process, mesh)
        except Exception as exc:  # noqa: BLE001 - never charge for a check that did not run
            raise CheckFailed(str(exc)[:200] or "The check could not run.") from exc

        pool = deps.charge_credits(user_id, cost) if (deps.charge_credits and cost > 0) else "free"
        if pool is None:
            raise NeedCredits(deps.balance(user_id), cost)
        try:
            stored = chat_store.save_check(user_id, chat_id, part_id, process, report, cost)
        except Exception as exc:  # noqa: BLE001
            _safe_refund_credits(deps, user_id, pool, cost)
            raise CheckFailed("I could not save the check, so you were not charged.") from exc
        if not stored:                                                         # lost a race: keep theirs, give the credits back
            _safe_refund_credits(deps, user_id, pool, cost)
            report = chat_store.get_check(user_id, chat_id, part_id, process) or report
            return {"report": report, "charged": 0, "balance": deps.balance(user_id), "part_id": part_id, "process": process}
        return {"report": report, "charged": cost if cost > 0 else 0, "balance": deps.balance(user_id),
                "part_id": part_id, "process": process}
    finally:
        lock.release()


def _safe_refund_credits(deps: Deps, user_id: str, pool: str | None, amount: int) -> None:
    if deps.refund_credits is None or pool in (None, "free"):
        return
    try:
        deps.refund_credits(user_id, pool, amount)
    except Exception:  # noqa: BLE001 - never mask the real error with a refund problem
        import logging
        logging.getLogger(__name__).exception("check refund failed")


def start_template_part(deps: Deps, user_id: str, chat_id: str, template_id: str) -> dict[str, Any]:
    """Start a part or assembly from a catalogue template. Free, like every preview, and no AI."""
    template = studio_templates.get(template_id)
    if template is None:
        raise TemplateNotFound()
    lock = _lock_for(chat_id)
    if not lock.acquire(blocking=False):
        raise ChatBusy()
    try:
        if chat_store.get_chat(user_id, chat_id) is None:
            raise ChatNotFound()
        text = f"Start from the template: {template.title}"
        user_msg = chat_store.add_message(user_id, chat_id, "user", text, max_messages=deps.max_messages)
        chat_store.set_title_if_default(user_id, chat_id, _chat_title(template.title))
        spec = studio_templates.spec_of(template)

        def finish(content: str, *, part: dict | None = None, meta: dict | None = None) -> dict:
            msg = chat_store.add_message(user_id, chat_id, "assistant", content, part_id=part["id"] if part else None,
                                         meta=meta, max_messages=deps.max_messages + 1)
            return {"user_message": user_msg, "assistant_message": msg, "part": part, "charged": 0,
                    "balance": deps.balance(user_id), "needs_credits": False, "intent": "build", "plan_source": "rules",
                    "requirements": evaluate_for(part), "new_requirements": []}

        def evaluate_for(part: dict | None) -> list[dict]:
            return reqmod.evaluate(chat_store.list_requirements(user_id, chat_id), part["facts"]) if part else []

        try:
            result = deps.generate(text, spec, user_id, "rules")
        except Exception as exc:  # noqa: BLE001 - a failed preview must never break the chat
            return finish("That template did not build. You were not charged. " + (str(exc)[:200] or "Try again."), meta={"error": True})
        if not result.get("success"):
            reason = (result.get("error") or "The template did not build.").strip()
            return finish(f"{reason} You were not charged." if "not charged" not in reason.lower() else reason, meta={"error": True})
        built = _spec_from_result(result, spec)
        part = chat_store.add_part(user_id, chat_id, job_id=result.get("job_id"), title=part_title(built),
                                   part_type=built["part_type"], spec=built, notes=_notes(result),
                                   facts=reqmod.facts_from_result(result, built["part_type"]))
        reply = studio_templates.reply_for(template) + _notes_sentence(part["notes"] or [])
        sentence = _requirement_sentence(evaluate_for(part), f"Version {part['version']}")
        return finish(reply + (" " + sentence if sentence else ""), part=part)
    finally:
        lock.release()


def _requirement_sentence(results: list[dict], subject: str) -> str:
    """One sentence on how a version stands against the requirements. Empty when there are none."""
    if not results:
        return ""
    failing = [r for r in results if r["status"] == "fail"]
    if failing:
        shown = "; ".join(f"{r['text']} ({r['detail']})" for r in failing[:3])
        more = f" and {len(failing) - 3} more" if len(failing) > 3 else ""
        return f"{subject} breaks: {shown}{more}."
    unknown = [r for r in results if r["status"] == "unknown"]
    if unknown and len(unknown) == len(results):
        return f"I could not measure {subject.lower()} against your requirements yet."
    return f"{subject} meets all {len(results) - len(unknown)} measurable requirement{'s' if len(results) - len(unknown) != 1 else ''}."


_ASK = re.compile(r"\?|^\s*(can|could|would|will|is|does|do|how|what|which|should)\b", re.I)
_PROCESS_WORDS = [
    ("fdm_print", re.compile(r"3d[\s-]?print|printable|\bprinted\b|\bfdm\b", re.I)),
    ("sheet_metal", re.compile(r"sheet[\s-]?metal|press brake|bent from", re.I)),
    ("turning", re.compile(r"\blathe\b|\bturned\b|\bturning\b", re.I)),
    ("cnc_milling", re.compile(r"\bcnc\b|\bmill(?:ed|ing)?\b|\bmachin(?:e|ed|ing)\b", re.I)),
]
_GENERIC = re.compile(r"manufactur|make this|be made|buildable|producible", re.I)


def dfm_answer(text: str, part: dict | None) -> str | None:
    """Answer 'can this be 3D printed / machined / bent from sheet?' from the current part, or None
    when the message is not that kind of question."""
    if not part or not _ASK.search(text):
        return None
    process = next((p for p, rx in _PROCESS_WORDS if rx.search(text)), None)
    if process is None and not _GENERIC.search(text):
        return None
    spec = part["spec"]
    params, material, ptype = spec.get("parameters") or {}, spec.get("material"), spec["part_type"]
    names = {"fdm_print": "3D printing", "sheet_metal": "sheet metal", "turning": "turning", "cnc_milling": "CNC milling"}
    tail = " These are rule-of-thumb checks, not a shop quote."
    if process:
        findings = dfm.check(ptype, params, process, material)
        issues = [f for f in findings if f["severity"] in ("error", "warning")]
        if not issues:
            return f"I found no problems with {names[process]} this part from its dimensions." + tail
        lines = " ".join(f["message"] for f in issues[:3])
        verdict = (f"It cannot be made by {names[process]} as it stands." if any(f["severity"] == "error" for f in issues)
                   else f"It can be made by {names[process]}, with caveats.")
        return f"{verdict} {lines}" + tail
    ranked = dfm.best_fit(ptype, params, material)
    parts = [f"{names[r['process']]} ({r['errors']} blocking, {r['warnings']} caution{'s' if r['warnings'] != 1 else ''})" for r in ranked]
    return "Best fit first: " + ", ".join(parts) + "." + tail


def _safe_refund_job(deps: Deps, user_id: str, job_id: str) -> None:
    try:
        deps.refund_job(job_id, user_id)
    except Exception:  # noqa: BLE001 - never mask the real error with a refund problem
        import logging
        logging.getLogger(__name__).exception("chat refund failed")


def _default_reply(plan: Plan, part: dict) -> str:
    if plan.intent == "edit":
        what = "; ".join(plan.changes[:3]) if plan.changes else "your change"
        return f"Updated to version {part['version']}: {what}. This is a free preview."
    if part["spec"].get("part_type") == "imported":
        return ("Here is your file with the change applied, as a free preview. Tell me what else to change"
                + ("." if (part["spec"].get("parameters") or {}).get("kind") in import_ops.MESH_KINDS
                   else ", then press Generate when it looks right."))
    if part["spec"].get("part_type") == "assembly":
        n = len((part["spec"].get("parameters") or {}).get("parts") or [])
        mated = (part["spec"].get("parameters") or {}).get("layout") == "mated"
        how = "placed by how they connect" if mated else "laid out side by side"
        return (f"Here is a free preview of your assembly with {n} part line{'s' if n != 1 else ''}, {how}. "
                "Tell me what to change, then press Generate when it looks right.")
    n = len(part["spec"].get("parameters") or {})
    return f"Here is a free preview of your {part['title'].lower()}, built with {n} parameter{'s' if n != 1 else ''}. Press Generate when it looks right."

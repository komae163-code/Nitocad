"""
Description -> assembly spec.

Two routes, tried in this order:
  1. Named assemblies (assembly_templates.py, e.g. "wheel corner module"):
     deterministic, no LLM, reproducible.
  2. LLM (DeepSeek): the model is given the part catalog, each part's mating
     interfaces and which interface kinds can mate, and returns parts + mates
     as JSON. The result is then validated by the SAME engine that gates
     generation (assembly_spec.resolve_assembly): unknown part types, invented
     interfaces, incompatible mates, unplaced parts and dimensional mismatches
     are all caught deterministically. A rejected result is retried once with
     the exact failures fed back; a second failure raises ParseError with the
     reasons. Nothing is ever silently replaced by a different assembly.

There is no regex route for arbitrary assemblies: free text cannot be turned
into mates reliably, and the previous behaviour (always returning the same
plate + bracket whatever was asked) was wrong.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable

import assembly_spec as AS
import assembly_templates as AT
import mating as M
from exceptions import ParseError
from logging_config import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "assembly-v1"
_KEYWORDS = ("assembly", "assemble", "multiple parts", "together")


@dataclass
class AssemblyParse:
    spec: dict
    parser: str                      # "template" | "deepseek"
    model: str | None
    prompt_version: str
    retry_count: int
    parse_hash: str
    warnings: list[str] = field(default_factory=list)


def is_assembly_request(description: str) -> bool:
    t = description.lower()
    return AT.match_named_assembly(description) is not None or any(k in t for k in _KEYWORDS)


def _hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- LLM prompt ------------------------------------------------------------------------

def _core_catalog_lines(exclude: set[str]) -> list[str]:
    """Single-part parameter lines for core (non-pack) templates, taken from
    the single-part parser's own prompt so they cannot drift."""
    try:
        import deepseek_parser
        text = deepseek_parser.SYSTEM_PROMPT
    except Exception:  # noqa: BLE001 - parser module needs the full runtime
        return []
    out = []
    for m in re.finditer(r"^- ([a-z_]+): (.+)$", text, re.M):
        if m.group(1) not in exclude and m.group(1) != "freeform":
            out.append(f"- {m.group(1)}: {m.group(2)}   [no interfaces: needs explicit placement]")
    return out


def build_system_prompt() -> str:
    import domain_packs
    from domain_packs.specs import prompt_lines
    itf = domain_packs.interface_catalog()
    lines = []
    for pack in domain_packs.get_packs():
        for part, spec in pack.param_specs.items():
            base = prompt_lines(part, spec)
            if part in itf:
                names = ", ".join(f"{i['name']} ({i['kind']})" for i in itf[part])
                lines.append(f"{base}\n    interfaces: {names}")
            else:
                lines.append(f"{base}\n    interfaces: none [needs explicit placement]")
    lines += _core_catalog_lines({t for p in domain_packs.get_packs() for t in p.templates})
    compat_txt = "; ".join(f"{a} <-> {b}" for a, b in M.rule_pairs())
    return (
        "You convert a description of a mechanical assembly into JSON. Output ONLY a JSON object:\n"
        '{"name": "snake_case", "root": "<part id>",\n'
        ' "parts": [{"id": "snake_case", "part_type": "<from catalog>", "parameters": {...}, '
        '"placement": {"position": [x,y,z], "rotation": [rx,ry,rz]}}],\n'
        ' "mates": [{"a": "part_id.interface", "b": "part_id.interface"}]}\n\n'
        "RULES\n"
        "- part ids are unique, lowercase, start with a letter. part_type MUST be from the catalog.\n"
        "- parameters: only values the description states, in millimetres; omit the rest (defaults are used).\n"
        "- A dimension that must agree across a mate (bolt count/PCD, bore vs pilot, bearing seat) is stated on BOTH parts.\n"
        "- Position parts with mates. A mate positions whichever side is not yet placed relative to the other. "
        "Exactly one part is the root (it needs no mate); every other part must be reachable from the root by mates, "
        "or have an explicit placement (millimetres, degrees).\n"
        "- Only use interface names listed for that part type, and only mate kinds that can mate: " + compat_txt + ".\n"
        "- Extra mates between parts that are already positioned are allowed: they are verified, not used for placement.\n"
        "- Do not invent part types, interfaces, or parameters. Do not add parts the description does not mention.\n\n"
        "CATALOG\n" + "\n".join(lines) + "\n"
    )


def _default_llm_call(api_key: str, model: str) -> Callable[[str, str], str]:
    from config import settings
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=settings.DEEPSEEK_BASE_URL, timeout=settings.DEEPSEEK_TIMEOUT_SECONDS)

    def call(system: str, user: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        return resp.choices[0].message.content.strip()

    return call


def _loads(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("response was not a JSON object")
    return data


def _problems(spec: dict) -> list[str]:
    """Everything wrong with a candidate spec, as readable strings."""
    try:
        resolved = AS.resolve_assembly(spec)
    except AS.AssemblySpecError as exc:
        return list(exc.messages)
    return [f"{f.code}: {f.message}" + (f" ({f.a}{' / ' + f.b if f.b else ''})" if f.a else "")
            for f in resolved.errors]


# --- entry point -------------------------------------------------------------------------

def parse_assembly_request(
    description: str,
    api_key: str | None = None,
    model: str = "deepseek-v4-flash",
    llm_call: Callable[[str, str], str] | None = None,
) -> AssemblyParse:
    named = AT.match_named_assembly(description)
    if named:
        spec = AT.build_named(named, description)
        return AssemblyParse(spec, "template", None, PROMPT_VERSION, 0, _hash(spec),
                             [f"Built from the '{named}' template"])

    if llm_call is None:
        if not api_key:
            raise ParseError(
                "Custom assemblies need the AI parser, which is not configured on this server. "
                "Named assemblies work without it: " + ", ".join(sorted(AT.NAMED_ASSEMBLIES)) + ".",
                details={"parser": None, "prompt_version": PROMPT_VERSION},
            )
        llm_call = _default_llm_call(api_key, model)

    system = build_system_prompt()
    last: list[str] = []
    for attempt in range(2):
        user = description
        if last:
            user += ("\n\nThe previous answer was rejected by deterministic assembly validation. "
                     "Fix these exact problems and return the complete JSON again:\n- " + "\n- ".join(last[:12]))
        try:
            spec = _loads(llm_call(system, user))
        except Exception as exc:  # noqa: BLE001 - network, timeout, bad JSON
            last = [f"response/API error: {exc}"]
            logger.warning("assembly parse attempt %s failed: %s", attempt + 1, exc)
            continue
        problems = _problems(spec)
        if not problems:
            return AssemblyParse(spec, "deepseek", model, PROMPT_VERSION, attempt, _hash(spec),
                                 [f"Assembly retry {attempt} applied"] if attempt else [])
        last = problems
        logger.warning("assembly parse rejected (attempt %s): %s", attempt + 1, problems)

    raise ParseError(
        "The AI could not produce an assembly that passes interface validation (" + "; ".join(last[:3]) + "). "
        "Name the parts and how they connect, and state shared dimensions such as bolt pattern.",
        details={"parser": "deepseek", "model": model, "prompt_version": PROMPT_VERSION,
                 "retry_count": 2, "assembly_errors": last},
    )

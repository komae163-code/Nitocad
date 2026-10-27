"""
Domain packs: self-contained discipline extensions on top of NitoCAD's
shared pipeline (parse -> validate -> template build -> export -> anchor).

A pack contributes, and ONLY contributes:
  * templates   - part_type -> generator(params) -> cq.Workplane
  * validators  - part_type -> validator(params) -> ValidationResult
  * param specs - part_type -> {param: spec}; the single source of truth for
                  parameter names, defaults and hard bounds (also used to
                  generate the LLM prompt lines, so prompt and code cannot
                  drift apart)
  * parser hooks- keyword part-type inference + regex parameter extraction
                  for the deterministic fallback parser

Everything else (jobs, payments, wallets, exports, provenance anchoring)
is shared infrastructure and is not duplicated per pack.

HONESTY CONTRACT: every pack carries a `scope_note` stating what it does and
does NOT establish. A pack produces parametric geometry and geometric
sanity checks. It does not, by itself, establish structural adequacy,
fatigue life, thermal performance, standards compliance or regulatory
approval of anything it generates. That is surfaced via GET
/api/domain-packs so no client has to guess.

Adding a pack: create domain_packs/<name>/__init__.py that builds a
DomainPack and calls register_pack(). Then add one import line below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DomainPack:
    name: str
    title: str
    version: str
    summary: str
    scope_note: str
    templates: Mapping[str, Callable[[dict], Any]]
    validators: Mapping[str, Callable[[dict], Any]]
    param_specs: Mapping[str, Mapping[str, Any]]
    infer_part_type: Callable[[str], str | None]
    extract_parameters: Callable[[str, str, dict], dict]
    part_descriptions: Mapping[str, str] = field(default_factory=dict)
    # part_type -> fn(params) -> list[mating.Interface]; the mating features
    # the part exposes (see mating.py). Optional: a part without interfaces
    # can still be placed explicitly in an assembly, just not mated.
    interfaces: Mapping[str, Callable[[dict], Any]] = field(default_factory=dict)
    # part_type -> fn(params) -> envelope.Shape (analytic volume envelope used
    # to MEASURE clearances without CadQuery; see envelope.py)
    envelopes: Mapping[str, Callable[[dict], Any]] = field(default_factory=dict)
    # part_type -> fn(params) -> dict of ALL resolved values (user values +
    # spec defaults + derived defaults), validated; raises ValueError if invalid
    value_resolvers: Mapping[str, Callable[[dict], dict]] = field(default_factory=dict)

    def part_types(self) -> list[str]:
        return sorted(self.templates)


_PACKS: dict[str, DomainPack] = {}


def register_pack(pack: DomainPack) -> None:
    if pack.name in _PACKS:
        raise ValueError(f"domain pack {pack.name!r} is already registered")
    clash = set(pack.templates) & {t for p in _PACKS.values() for t in p.templates}
    if clash:
        raise ValueError(f"domain pack {pack.name!r} redefines part types: {sorted(clash)}")
    missing = set(pack.templates) - set(pack.validators)
    if missing:
        raise ValueError(f"domain pack {pack.name!r} has templates without validators: {sorted(missing)}")
    _PACKS[pack.name] = pack


def get_packs() -> list[DomainPack]:
    return list(_PACKS.values())


def pack_templates() -> dict[str, Callable[[dict], Any]]:
    out: dict[str, Callable[[dict], Any]] = {}
    for p in _PACKS.values():
        out.update(p.templates)
    return out


def pack_validators() -> dict[str, Callable[[dict], Any]]:
    out: dict[str, Callable[[dict], Any]] = {}
    for p in _PACKS.values():
        out.update(p.validators)
    return out


def part_interfaces(part_type: str, params: dict | None = None) -> list:
    """Interfaces a part exposes for `params` (defaults when omitted).
    Empty list for parts that declare none."""
    for p in _PACKS.values():
        fn = p.interfaces.get(part_type)
        if fn is not None:
            return list(fn(dict(params or {})))
    return []


def resolved_values(part_type: str, params: dict | None = None) -> dict | None:
    """Every parameter of a part with defaults and derived values filled in,
    or None for part types without a resolver."""
    for p in _PACKS.values():
        fn = p.value_resolvers.get(part_type)
        if fn is not None:
            return fn(dict(params or {}))
    return None


def part_envelope(part_type: str, params: dict | None = None):
    """Analytic envelope for a part, or None if the part declares none."""
    for p in _PACKS.values():
        fn = p.envelopes.get(part_type)
        if fn is not None:
            return fn(dict(params or {}))
    return None


def interface_catalog() -> dict[str, list[dict]]:
    """part_type -> [{name, kind, description}] at default parameters.
    Used to tell the assembly parser which mating features exist."""
    out: dict[str, list[dict]] = {}
    for p in _PACKS.values():
        for part, fn in p.interfaces.items():
            out[part] = [{"name": i.name, "kind": i.kind, "description": i.description}
                         for i in fn({})]
    return out


def is_pack_part(part_type: str) -> bool:
    return any(part_type in p.templates for p in _PACKS.values())


def infer_part_type(text: str) -> str | None:
    """First pack that recognises the description wins. Packs must only
    match on specific phrases (never bare generic words) so they cannot
    shadow the core mechanical keywords."""
    for p in _PACKS.values():
        try:
            hit = p.infer_part_type(text)
        except Exception:  # noqa: BLE001 - a pack bug must never break parsing
            logger.exception("domain pack %s infer_part_type failed", p.name)
            continue
        if hit:
            return hit
    return None


def extract_parameters(part_type: str, text: str, dim_map: dict) -> dict:
    for p in _PACKS.values():
        if part_type in p.templates:
            try:
                return p.extract_parameters(part_type, text, dim_map)
            except Exception:  # noqa: BLE001
                logger.exception("domain pack %s extract_parameters failed", p.name)
                return {}
    return {}


def prompt_section() -> str:
    """Lines for the LLM system prompt, generated from each pack's specs."""
    from domain_packs.specs import prompt_lines
    lines: list[str] = []
    for p in _PACKS.values():
        for part, spec in p.param_specs.items():
            lines.append(prompt_lines(part, spec))
    return "\n".join(lines)


def part_type_names() -> list[str]:
    return sorted(t for p in _PACKS.values() for t in p.templates)


def describe_packs() -> list[dict]:
    return [
        {
            "name": p.name,
            "title": p.title,
            "version": p.version,
            "summary": p.summary,
            "scope_note": p.scope_note,
            "parts": [
                {"part_type": t, "description": p.part_descriptions.get(t, "")}
                for t in p.part_types()
            ],
        }
        for p in _PACKS.values()
    ]


# --- built-in packs ---------------------------------------------------------
from domain_packs import automotive  # noqa: E402,F401  (registers itself)

"""
LLM assembly planner: turns "a pillow-block shaft with two bearings and a gear"
into a validated parts list that assembly.generate_assembly() can build.

Design rule (same as the rest of NitoCAD): the LLM only plans; deterministic
code decides what is allowed.

- The parameter catalog is read from cad_templates/ with the ast module, so
  the planner can never drift from what the templates actually accept. No
  hand-maintained prompt list to forget to update.
- Every plan is validated before any geometry is built: known part types,
  known parameter names (a typo'd name is otherwise silently ignored by
  params.get(...) and the part is built with the wrong default), finite
  non-negative numbers, sane positions, unique part names, a part-count cap.
- A rejected plan is retried once with the exact failures fed back.

This module deliberately does NOT import cadquery, so the planning logic is
testable without the CAD kernel. UNVERIFIED against the live DeepSeek API in
the environment it was written in (no network); see tests/test_assembly_planner.py
for what is covered.
"""
from __future__ import annotations

import ast
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from feature_tree import DSL_HELP, validate_feature_tree
from mates import MATE_HELP, validate_mate

# No part-count cap. Set to an int to bring one back (tests do); None means unlimited.
MAX_PARTS: int | None = None
POSITION_LIMIT_MM = 10_000.0
ROTATION_LIMIT_DEG = 360.0
# freeform takes an open-ended nested spec; "assembly" would recurse.
EXCLUDED_TYPES = frozenset({"freeform", "assembly"})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

_TEMPLATES_DIR = Path(__file__).parent / "cad_templates"


# ---------------------------------------------------------------- catalog
def build_param_catalog(templates_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """{part_type: {param_name: default}} read from the template source."""
    root = Path(templates_dir) if templates_dir else _TEMPLATES_DIR
    init = ast.parse((root / "__init__.py").read_text(encoding="utf-8"))

    func_module: dict[str, str] = {}
    for node in init.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                func_module[alias.asname or alias.name] = node.module

    registry: dict[str, str] = {}
    for node in init.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TEMPLATES" for t in node.targets
        ) and isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                    registry[k.value] = v.id

    catalog: dict[str, dict[str, Any]] = {}
    for part_type, fn_name in registry.items():
        module = func_module.get(fn_name)
        if not module:
            continue
        tree = ast.parse((root / f"{module}.py").read_text(encoding="utf-8"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == fn_name), None)
        if fn is None:
            continue
        keys: dict[str, Any] = {}
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "params" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                default = None
                if len(n.args) > 1:
                    try:
                        default = ast.literal_eval(n.args[1])
                    except (ValueError, SyntaxError):
                        default = None
                keys[n.args[0].value] = default
            elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                  and n.value.id == "params" and isinstance(n.slice, ast.Constant)
                  and isinstance(n.slice.value, str)):
                keys.setdefault(n.slice.value, None)
        catalog[part_type] = keys
    return catalog


@lru_cache(maxsize=1)
def default_catalog() -> dict[str, dict[str, Any]]:
    cat = build_param_catalog()
    return {k: v for k, v in cat.items() if k not in EXCLUDED_TYPES}


# ------------------------------------------------------------- validation
def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _vec3(value: Any) -> tuple[float, float, float] | None:
    if (isinstance(value, (list, tuple)) and len(value) == 3 and all(_is_num(v) for v in value)):
        return (float(value[0]), float(value[1]), float(value[2]))
    return None


def validate_spec(spec: Any, catalog: dict[str, dict[str, Any]] | None = None
                  ) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (normalized_parts, errors). Parts are only trustworthy if errors is empty."""
    catalog = catalog if catalog is not None else default_catalog()
    errors: list[str] = []
    parts_in = spec.get("parts") if isinstance(spec, dict) else None
    if not isinstance(parts_in, list) or not parts_in:
        return [], ['Response must be a JSON object with a non-empty "parts" list.']
    if MAX_PARTS is not None and len(parts_in) > MAX_PARTS:
        errors.append(f"Too many parts ({len(parts_in)}); the limit is {MAX_PARTS}.")
        parts_in = parts_in[:MAX_PARTS]

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, p in enumerate(parts_in):
        label = f"parts[{i}]"
        if not isinstance(p, dict):
            errors.append(f"{label} must be an object.")
            continue
        name = p.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            errors.append(f"{label}.name must be lowercase snake_case (a-z, 0-9, _), got {name!r}.")
            name = f"part_{i}"
        elif name in seen:
            errors.append(f"{label}.name {name!r} is used twice; part names must be unique.")
        seen.add(name)
        label = f"part {name!r}"

        ptype = p.get("part_type")
        if ptype not in catalog:
            errors.append(f"{label}: part_type {ptype!r} is not supported. Use one of: {', '.join(sorted(catalog))}.")
            continue

        params = p.get("parameters", {})
        if not isinstance(params, dict):
            errors.append(f"{label}: parameters must be an object.")
            params = {}
        allowed = catalog[ptype]
        clean: dict[str, Any] = {}
        if ptype == "feature_tree":
            # Nested DSL: validated by its own strict validator, not the flat-parameter rules.
            extra = set(params) - {"features"}
            if extra:
                errors.append(f"{label}: feature_tree takes only a \"features\" parameter, got {sorted(extra)}.")
            feats, ferrs = validate_feature_tree(params.get("features"))
            errors.extend(f"{label}: {e}" for e in ferrs)
            clean = {"features": feats}
            params = {}
        for key, val in params.items():
            if key not in allowed:
                errors.append(f"{label}: unknown parameter {key!r} for {ptype}. Valid: {', '.join(sorted(allowed))}.")
                continue
            default = allowed[key]
            if isinstance(default, bool):
                ok = isinstance(val, bool)
            elif isinstance(default, (int, float)):
                ok = _is_num(val) and val >= 0
            elif isinstance(default, str):
                ok = isinstance(val, str)
            else:  # unknown default: accept any finite scalar
                ok = isinstance(val, (str, bool)) or (_is_num(val))
            if not ok:
                errors.append(f"{label}: parameter {key!r} has an invalid value {val!r} "
                              f"(expected like {default!r}).")
                continue
            clean[key] = val

        mate_in = p.get("mate")
        if mate_in is not None and "position" in p:
            errors.append(f"{label}: give either position or mate, not both.")
        pos = _vec3(p.get("position", [0, 0, 0])) if mate_in is None else (0.0, 0.0, 0.0)
        rot = _vec3(p.get("rotation", [0, 0, 0]))
        if pos is None or any(abs(c) > POSITION_LIMIT_MM for c in pos):
            errors.append(f"{label}: position must be [x, y, z] in mm within ±{POSITION_LIMIT_MM:g}.")
            pos = (0.0, 0.0, 0.0)
        if rot is None or any(abs(c) > ROTATION_LIMIT_DEG for c in rot):
            errors.append(f"{label}: rotation must be [rx, ry, rz] in degrees within ±{ROTATION_LIMIT_DEG:g}.")
            rot = (0.0, 0.0, 0.0)
        mate_norm = None
        if mate_in is not None:
            mate_norm = validate_mate(mate_in, label, errors, {x["name"]: x for x in out}, rot)

        # Reuse the project's own geometric validator where one exists.
        if clean:
            try:
                from validator import validate_parameters
                corrected, result = validate_parameters(ptype, clean)
                for msg in result.errors:
                    errors.append(f"{label}: {msg}")
                clean = corrected
            except ImportError:  # validator unavailable: structural checks above still apply
                pass

        out.append({"name": name, "part_type": ptype, "parameters": clean,
                    "position": pos, "rotation": rot, "mate": mate_norm})
    return out, errors


# ------------------------------------------------------------- LLM prompt
def planner_system_prompt(catalog: dict[str, dict[str, Any]] | None = None) -> str:
    catalog = catalog if catalog is not None else default_catalog()
    lines = []
    for ptype in sorted(catalog):
        params = ", ".join(f"{k}={v!r}" if v is not None else k for k, v in catalog[ptype].items())
        lines.append(f"- {ptype}: {params}")
    return (
        "You are an assembly planner for a parametric CAD system. Break the user's request into "
        "individual parts and place them relative to each other.\n\n"
        "Return ONLY a JSON object: {\"parts\": [{\"name\": str, \"part_type\": str, "
        "\"parameters\": {..}, \"position\": [x, y, z], \"rotation\": [rx, ry, rz]}]}\n\n"
        "Rules:\n"
        "- All lengths in millimetres, all angles in degrees. position is the part's origin in the assembly frame "
        "(or give a mate instead, see Placement).\n"
        "- part_type must be one of the types below; use ONLY the parameter names listed for that type "
        "(defaults shown; omit a parameter to keep its default).\n"
        "- Each part's local axis is +Z (shafts, tubes and plates extrude along +Z from their origin); "
        "use rotation to orient them.\n"
        "- name: unique lowercase snake_case. "
        + (f"Maximum {MAX_PARTS} parts. " if MAX_PARTS is not None else "")
        + "Parts must not overlap each other except where a fit is intended "
        "(shaft through bearing bore: bore diameter equal to shaft diameter).\n"
        "- If the request cannot be built from these part types, return {\"parts\": [], \"unsupported\": \"why\"}.\n\n"
        "Placement: " + MATE_HELP + "\n\n"
        "Part types:\n" + "\n".join(lines)
        + _hint_block(catalog)
    )


def _hint_block(catalog: dict[str, dict[str, Any]]) -> str:
    from standards_data import HINTS
    hints = dict(HINTS)
    hints["feature_tree"] = DSL_HELP
    notes = [f"- {t}: {h}" for t, h in hints.items() if t in catalog]
    return ("\n\nNotes on standard parts:\n" + "\n".join(notes)) if notes else ""


def deepseek_call_llm(system: str, user: str) -> dict:
    """Production LLM call, same transport settings as deepseek_parser."""
    from openai import OpenAI
    from config import settings

    client = OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL,
                    timeout=settings.DEEPSEEK_TIMEOUT_SECONDS * 3)
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_DEFAULT_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0,
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


class AssemblyPlanError(ValueError):
    """The planner could not produce a valid plan. .errors lists why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


def plan_assembly(description: str,
                  call_llm: Callable[[str, str], dict] | None = None,
                  catalog: dict[str, dict[str, Any]] | None = None,
                  max_attempts: int = 2) -> list[dict[str, Any]]:
    """Plan an assembly from text. Raises AssemblyPlanError instead of returning a bad plan."""
    catalog = catalog if catalog is not None else default_catalog()
    call = call_llm or deepseek_call_llm
    system = planner_system_prompt(catalog)
    user = description
    errors: list[str] = []
    for _ in range(max_attempts):
        try:
            data = call(system, user)
        except Exception as exc:  # noqa: BLE001 - network, timeout, bad JSON
            errors = [f"Planner call failed: {exc}"]
            continue
        if isinstance(data, dict) and data.get("unsupported"):
            raise AssemblyPlanError([f"Not buildable with current parts: {data['unsupported']}"])
        parts, errors = validate_spec(data, catalog)
        if not errors:
            return parts
        user = (description + "\n\nThe previous plan was rejected by deterministic validation. "
                "Fix these exact failures and return the complete JSON again:\n- " + "\n- ".join(errors))
    raise AssemblyPlanError(errors)

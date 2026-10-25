"""
Parameter specs shared by every domain pack.

One `P` per parameter: default, hard bounds and kind. A default of None means
"derived" (the part computes it from other parameters). Hard bounds are
physical/numerical limits - violating one is an ERROR, not a clamp. Softer
feasibility rules (a boss that overlaps a bore, a hole off the part) live in
each part's own check function and are reported as errors or as explicit,
logged corrections.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class P:
    default: Any = None
    lo: float | None = None
    hi: float | None = None
    kind: str = "float"          # float | int | bool | choice
    choices: tuple[str, ...] = ()


def _coerce(name: str, raw: Any, p: P) -> Any:
    if p.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("true", "yes", "1"):
                return True
            if s in ("false", "no", "0"):
                return False
        raise ValueError(f"{name} must be true or false")
    if p.kind == "choice":
        s = str(raw).strip().lower()
        if s not in p.choices:
            raise ValueError(f"{name} must be one of: {', '.join(p.choices)}")
        return s
    if isinstance(raw, bool):
        raise ValueError(f"{name} must be a number")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    if p.kind == "int":
        if abs(v - round(v)) > 1e-9:
            raise ValueError(f"{name} must be a whole number")
        return int(round(v))
    return v


def resolve(params: Mapping[str, Any], spec: Mapping[str, P]) -> tuple[dict, list[str]]:
    """Coerce `params` against `spec`. Returns (values, errors). Missing or
    null parameters take the spec default (None stays None = derived).
    Unknown keys are ignored (the parser also emits `operations`, etc.)."""
    values: dict[str, Any] = {}
    errors: list[str] = []
    for name, p in spec.items():
        raw = params.get(name)
        if raw is None:
            values[name] = p.default
            continue
        try:
            v = _coerce(name, raw, p)
        except ValueError as exc:
            errors.append(str(exc))
            values[name] = p.default
            continue
        if p.kind in ("float", "int"):
            if p.lo is not None and v < p.lo:
                errors.append(f"{name} must be at least {p.lo:g} (got {v:g})")
            if p.hi is not None and v > p.hi:
                errors.append(f"{name} must be at most {p.hi:g} (got {v:g})")
        values[name] = v
    return values, errors


def prompt_lines(part: str, spec: Mapping[str, P]) -> str:
    bits = []
    for name, p in spec.items():
        if p.kind == "bool":
            bits.append(f"{name} (bool)")
        elif p.kind == "choice":
            bits.append(f'{name} ({"|".join(chr(34) + c + chr(34) for c in p.choices)})')
        else:
            bits.append(name)
    return f"- {part}: " + ", ".join(bits)

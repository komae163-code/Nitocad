"""Plumbing shared by every automotive part: resolve -> derive -> check."""
from __future__ import annotations

import math
from typing import Callable, Mapping

from validator import ValidationResult
from domain_packs.specs import P, resolve


def floor3(x: float) -> float:
    """Round DOWN to 3 decimals. Corrections that cap a value must never
    round up past the cap, or re-validating the corrected value would
    trigger the same correction again."""
    return math.floor(x * 1000.0) / 1000.0


def fix(res: ValidationResult, v: dict, name: str, new, why: str) -> None:
    """Record an explicit, logged auto-correction and apply it locally so
    later checks in the same pass see the corrected value."""
    old = v.get(name)
    res.add_warning(f"{why} - {name} changed from {old:g} to {new:g}"
                    if isinstance(old, (int, float)) and not isinstance(old, bool)
                    else f"{why} - {name} corrected")
    res.add_correction(name, old, new)
    v[name] = new


def run_validation(
    params: Mapping, spec: Mapping[str, P],
    derive: Callable[[dict], dict],
    check: Callable[[dict, ValidationResult], None],
) -> ValidationResult:
    res = ValidationResult()
    v, errs = resolve(params, spec)
    for e in errs:
        res.add_error(e)
    if errs:
        return res
    v = derive(v)
    check(v, res)
    return res


def prepare(
    params: Mapping, spec: Mapping[str, P],
    derive: Callable[[dict], dict],
    check: Callable[[dict, ValidationResult], None],
) -> dict:
    """For templates: same logic as the validator, but raises on any error
    (the generator wraps it in GenerationError) and applies corrections."""
    v, errs = resolve(params, spec)
    if errs:
        raise ValueError("; ".join(errs))
    v = derive(v)
    res = ValidationResult()
    check(v, res)
    if res.errors:
        raise ValueError("; ".join(res.errors))
    return v

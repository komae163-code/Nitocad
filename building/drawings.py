"""Compatibility wrapper: plan_svg now renders through the shared Sheet pipeline (see sheets.py, sheet.py)."""
from __future__ import annotations

from typing import Any

from .sheet import to_svg
from .sheets import plan_sheet


def plan_svg(model: dict[str, Any], level: int, furniture: list[dict[str, Any]] | None = None, paper: str | None = None) -> str:
    return to_svg(plan_sheet(model, level, furniture), paper)

"""The building's drawing set, written from flat Studio parameters (used for the DXF and PDF downloads)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .flat import resolve_flat
from .furniture import place_furniture
from .sheet import Sheet, merge_sheets, to_dxf, to_pdf_multi
from .sheets import elevation_sheet, plan_sheet, section_defs, section_sheet


def building_sheets(model: dict[str, Any]) -> list[Sheet]:
    items, _ = place_furniture(model)
    out = [plan_sheet(model, k, items) for k in range(len(model["levels"]))]
    out += [elevation_sheet(model, s) for s in ("south", "east", "north", "west")]
    out += [section_sheet(model, sd) for sd in section_defs(model)]
    return out


def _model(params: dict[str, Any]) -> dict[str, Any]:
    r = resolve_flat(params)
    if not r["ok"]:
        raise ValueError("; ".join(r["errors"]))
    return r["model"]


def write_building_pdf(path: Path, params: dict[str, Any]) -> None:
    Path(path).write_bytes(to_pdf_multi(building_sheets(_model(params))))


def write_building_dxf(path: Path, params: dict[str, Any]) -> None:
    Path(path).write_text(to_dxf(merge_sheets(building_sheets(_model(params))), scale=50), encoding="utf-8")

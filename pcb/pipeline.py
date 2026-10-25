"""
Spec -> manufacturing package, with an honest report.

    build_board(spec, outdir) -> report dict (JSON-serialisable)

Stages (each recorded in report["stages"] so nothing fails silently):
  compose  -> checked netlist (ERC-lite)           [pure python]
  place    -> block-grouped placement              [pure python]
  route    -> 2-layer autorouter                   [pure python]
  drc_lite -> our own geometric checks             [pure python]
  write    -> .kicad_pcb / .kicad_pro / SVG / BOM  [pure python]
  kicad    -> zone fill, REAL KiCad DRC, Gerbers, drill, position file, STEP
              (only if kicad-cli is installed; otherwise skipped and said so)

The report's "verification" field is the honest summary:
  "kicad_drc_clean"       KiCad's own DRC ran and passed
  "kicad_drc_failed"      KiCad's own DRC ran and found problems
  "not_checked_by_kicad"  KiCad is not installed here - files are unverified
Nothing in this module claims a board "works": passing DRC means the board
is manufacturable to the fab rules, not that the circuit is correct.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections import OrderedDict
from pathlib import Path

from . import PACK_VERSION
from . import kicad_cli as kc
from .blocks import BLOCKS_HARDWARE_PROVEN
from .compose import SpecError, compose
from .drc_lite import run_drc_lite
from .kicad_io import write_board, write_project
from .model import RULES
from .place import place
from .render_svg import render_svg
from .route import route_design

# File kinds a caller may ask for -> (filename suffix, media type)
FILE_KINDS = {
    "svg": ("preview.svg", "image/svg+xml"),
    "kicad": ("kicad_project.zip", "application/zip"),
    "gerbers": ("gerbers.zip", "application/zip"),
    "bom": ("bom.csv", "text/csv"),
    "pos": ("positions.csv", "text/csv"),
    "step": ("board.step", "application/octet-stream"),
    "report": ("report.json", "application/json"),
}


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "board").lower()).strip("-")
    return (s or "board")[:40]


def make_bom(design) -> str:
    groups: OrderedDict = OrderedDict()
    for c in design.components:
        groups.setdefault((c.value, c.lib_id, c.mpn or ""), []).append(c)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Designators", "Qty", "Value", "Footprint", "MPN", "Needs datasheet review"])
    for (value, lib_id, mpn), comps in groups.items():
        w.writerow([",".join(c.ref for c in comps), len(comps), value, lib_id, mpn,
                    "YES" if any(c.review for c in comps) else ""])
    return buf.getvalue()


def _spec_notes(spec: dict) -> list[str]:
    """Plain-language facts derived from parameters (never invented)."""
    notes = []
    for b in spec.get("blocks", []):
        if b["type"] == "tlc555_blinker":
            from .blocks import nearest_e24
            p = b["params"]
            r1, r2, c = nearest_e24(p["r1_ohms"]), nearest_e24(p["r2_ohms"]), p["timing_cap_nf"] * 1e-9
            f = 1.44 / ((r1 + 2 * r2) * c)
            duty = (r1 + r2) / (r1 + 2 * r2) * 100
            notes.append(f"{b['id']}: oscillates at about {f:.3g} Hz ({duty:.0f}% duty) "
                         f"with R1={r1} ohm, R2={r2} ohm, C={p['timing_cap_nf']:g} nF")
    return notes


def _route_best(design, prefer_plane: bool):
    """Try the preferred ground strategy, fall back to the other if nets stay unrouted."""
    route_design(design, plane=prefer_plane)
    if design.unrouted:
        first = (design.tracks, design.vias, design.unrouted, design.ground_mode)
        route_design(design, plane=not prefer_plane)
        if len(design.unrouted) >= len(first[2]):
            design.tracks, design.vias, design.unrouted, design.ground_mode = first
    return design


def _zip_dir(zpath: Path, files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in files:
            z.write(src, arc)


def build_board(spec: dict, outdir: Path, *, run_kicad: bool = True) -> dict:
    """Raises SpecError for an invalid spec (nothing written). Any other
    outcome - including a board we could not route or that fails DRC - is
    returned as a report so the caller decides what to deliver/charge."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stages: dict = {}

    design = compose(spec)                       # may raise SpecError
    stages["compose"] = {"ok": True, "components": len(design.components), "nets": len(design.nets)}

    cli_version = kc.cli_version() if run_kicad else None
    pcbnew_py = kc.find_pcbnew_python() if cli_version else None
    # Ground pour needs pcbnew to fill zones for KiCad's DRC to see it; without
    # that, route ground as traces so the board is self-contained.
    prefer_plane = True if not cli_version else bool(pcbnew_py)

    place(design)
    stages["place"] = {"ok": True, "board_mm": [round(design.board[2] - design.board[0], 2),
                                                round(design.board[3] - design.board[1], 2)]}
    _route_best(design, prefer_plane)
    stages["route"] = {"ok": not design.unrouted, "tracks": len(design.tracks), "vias": len(design.vias),
                       "unrouted_nets": list(design.unrouted), "ground": design.ground_mode,
                       "wide_nets": dict(design.net_width)}
    lite = run_drc_lite(design)
    stages["drc_lite"] = {"ok": lite["passed"], "violations": lite["violations"],
                          "problems": {k: v[:5] for k, v in lite["problems"].items() if v}}

    slug = slugify(design.title)
    pcb_path = outdir / f"{slug}.kicad_pcb"
    pro_path = outdir / f"{slug}.kicad_pro"
    fmt = write_board(design, pcb_path)
    write_project(pro_path)
    (outdir / FILE_KINDS["svg"][0]).write_text(render_svg(design), encoding="utf-8")
    (outdir / FILE_KINDS["bom"][0]).write_text(make_bom(design), encoding="utf-8")
    _zip_dir(outdir / FILE_KINDS["kicad"][0], [(pcb_path, pcb_path.name), (pro_path, pro_path.name)])
    stages["write"] = {"ok": True, "kicad_format": fmt}

    files = {"svg", "bom", "kicad"}
    kicad: dict = {"available": bool(cli_version), "version": cli_version}
    verification = "not_checked_by_kicad"
    if cli_version:
        if design.ground_mode == "plane" and pcbnew_py:
            ok, msg = kc.fill_zones(pcb_path, pcbnew_py)
            kicad["zone_fill"] = {"ok": ok, "detail": "" if ok else msg[-300:]}
        drc = kc.run_drc(pcb_path, outdir / "kicad_drc.json")
        kicad["drc"] = drc
        verification = {"pass": "kicad_drc_clean", "fail": "kicad_drc_failed"}.get(drc["status"], "not_checked_by_kicad")
        if drc["status"] == "pass":
            g_ok, g_msg = kc.export_gerbers(pcb_path, outdir / "gerber")
            d_ok, d_msg = kc.export_drill(pcb_path, outdir / "gerber")
            kicad["gerbers"] = {"ok": g_ok, "detail": "" if g_ok else g_msg}
            kicad["drill"] = {"ok": d_ok, "detail": "" if d_ok else d_msg}
            if g_ok and d_ok:
                gdir = outdir / "gerber"
                _zip_dir(outdir / FILE_KINDS["gerbers"][0],
                         [(f, f.name) for f in sorted(gdir.iterdir()) if f.is_file()])
                files.add("gerbers")
            p_ok, p_msg = kc.export_pos(pcb_path, outdir / FILE_KINDS["pos"][0])
            kicad["pos"] = {"ok": p_ok, "detail": "" if p_ok else p_msg}
            if p_ok:
                files.add("pos")
            s_ok, s_msg = kc.export_step(pcb_path, outdir / FILE_KINDS["step"][0])
            kicad["step"] = {"ok": s_ok, "detail": "" if s_ok else s_msg}
            if s_ok:
                files.add("step")
    stages["kicad"] = kicad

    routed = not design.unrouted
    manufacturable = routed and lite["passed"] and verification == "kicad_drc_clean" and "gerbers" in files
    warnings = list(design.warnings)
    if not cli_version:
        warnings.append("KiCad is not installed on this server: the board was checked only by NitoCAD's own "
                        "built-in checks and no Gerber files were produced.")
    notes = _spec_notes(design._spec)                                    # type: ignore[attr-defined]
    review = list(design.review_items)
    if design.net_width:
        notes.append("Power nets routed with wide tracks for their current: " + ", ".join(
            f"{n} {w:g} mm ({design.net_current[n]:.2g} A)" for n, w in sorted(design.net_width.items())))
    if not BLOCKS_HARDWARE_PROVEN:
        warnings.append("These circuit blocks have not yet been built and tested on physical hardware. "
                        "Treat every board as a prototype: review the schematic logic and part pin-outs "
                        "against the datasheets before ordering.")
    if any(c.review for c in design.components):
        warnings.append("Some parts are flagged for datasheet review - see the BOM before ordering.")

    report = {
        "pack_version": PACK_VERSION,
        "title": design.title,
        "ok": routed and lite["passed"] and verification != "kicad_drc_failed",
        "routed": routed,
        "manufacturable": manufacturable,
        "verification": verification,
        "board_mm": stages["place"]["board_mm"],
        "components": len(design.components),
        "nets": len(design.nets),
        "fab_rules": RULES,
        "warnings": warnings,
        "review_items": review,
        "notes": notes,
        "disclaimer": ("Passing design-rule checks means the board can be manufactured to the stated fab "
                       "rules. It does not prove the circuit works - verify the design before building."),
        "stages": stages,
        "files": sorted(files | {"report"}),
    }
    (outdir / FILE_KINDS["report"][0]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def file_path_for(outdir: Path, kind: str) -> Path | None:
    """Whitelisted lookup used by the HTTP layer (no user-controlled paths)."""
    if kind not in FILE_KINDS:
        return None
    p = Path(outdir) / FILE_KINDS[kind][0]
    return p if p.is_file() else None


__all__ = ["build_board", "file_path_for", "make_bom", "FILE_KINDS", "SpecError"]

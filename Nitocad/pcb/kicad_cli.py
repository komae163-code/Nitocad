"""Thin wrappers around kicad-cli and pcbnew. Every call is failure-tolerant:
the pipeline records what worked and what didn't instead of crashing."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 180

FILL_SCRIPT = (
    "import sys, pcbnew\n"
    "b = pcbnew.LoadBoard(sys.argv[1])\n"
    "f = pcbnew.ZONE_FILLER(b)\n"
    "f.Fill(b.Zones())\n"
    "pcbnew.SaveBoard(sys.argv[1], b)\n"
)


def find_cli() -> str | None:
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    for cand in ("kicad-cli", "kicad-cli.exe"):
        p = shutil.which(cand)
        if p:
            return p
    for p in ("C:/Program Files/KiCad/9.0/bin/kicad-cli.exe", "C:/Program Files/KiCad/8.0/bin/kicad-cli.exe",
              "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"):
        if Path(p).exists():
            return p
    return None


def _run(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def cli_version() -> str | None:
    cli = find_cli()
    if not cli:
        return None
    ok, out = _run([cli, "--version"])
    return out.splitlines()[0] if ok and out else None


def find_pcbnew_python() -> str | None:
    cands = [os.environ.get("PCB_KICAD_PYTHON"), "/usr/bin/python3", sys.executable, "python3", "python",
             "C:/Program Files/KiCad/9.0/bin/python.exe", "C:/Program Files/KiCad/8.0/bin/python.exe"]
    for c in cands:
        if not c:
            continue
        ok, _ = _run([c, "-c", "import pcbnew"])
        if ok:
            return c
    return None


def fill_zones(board: Path, python: str) -> tuple[bool, str]:
    return _run([python, "-c", FILL_SCRIPT, str(board)])


def run_drc(board: Path, out_json: Path) -> dict:
    cli = find_cli()
    if not cli:
        return {"status": "unavailable", "detail": "kicad-cli not found"}
    ok, out = _run([cli, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm",
                    "--output", str(out_json), str(board)])
    if not ok or not out_json.exists():
        return {"status": "error", "detail": out[-800:]}
    try:
        data = json.loads(out_json.read_text())
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"unreadable DRC report: {exc}"}
    viol = data.get("violations", [])
    errors = [v for v in viol if v.get("severity") == "error"]
    warns = [v for v in viol if v.get("severity") != "error"]
    unconnected = data.get("unconnected_items", [])

    def short(v):
        return {"type": v.get("type"), "message": v.get("description"),
                "where": [i.get("description") for i in v.get("items", [])][:3]}

    return {"status": "pass" if not errors and not unconnected else "fail",
            "errors": [short(v) for v in errors], "warnings": [short(v) for v in warns],
            "unconnected": [short(v) for v in unconnected]}


def export_gerbers(board: Path, outdir: Path) -> tuple[bool, str]:
    cli = find_cli()
    if not cli:
        return False, "kicad-cli not found"
    outdir.mkdir(parents=True, exist_ok=True)
    last = ""
    for layers in ("F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
                   "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"):
        ok, last = _run([cli, "pcb", "export", "gerbers", "--output", str(outdir) + os.sep,
                         "--layers", layers, str(board)])
        if ok and any(outdir.iterdir()):
            return True, "ok"
    return False, last[-600:]


def export_drill(board: Path, outdir: Path) -> tuple[bool, str]:
    cli = find_cli()
    if not cli:
        return False, "kicad-cli not found"
    outdir.mkdir(parents=True, exist_ok=True)
    ok, out = _run([cli, "pcb", "export", "drill", "--output", str(outdir) + os.sep, "--format", "excellon",
                    "--excellon-units", "mm", str(board)])
    return ok and any(outdir.iterdir()), out[-600:]


def export_pos(board: Path, out: Path) -> tuple[bool, str]:
    cli = find_cli()
    if not cli:
        return False, "kicad-cli not found"
    ok, msg = _run([cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                    "--output", str(out), str(board)])
    return ok and out.exists(), msg[-600:]


def export_step(board: Path, out: Path) -> tuple[bool, str]:
    cli = find_cli()
    if not cli:
        return False, "kicad-cli not found"
    ok, msg = _run([cli, "pcb", "export", "step", "--force", "--output", str(out), str(board)])
    return ok and out.exists(), msg[-600:]

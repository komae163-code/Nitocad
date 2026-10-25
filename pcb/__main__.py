"""Command-line runner - test the PCB pack (with real KiCad) without the web server.

    python -m pcb "5V board with a 555 blinking LED" --out ./pcb_out
    python -m pcb "USB powered 3.3V sensor board" --ai          # AI planner (needs DEEPSEEK_API_KEY)
    python -m pcb --spec my_spec.json --out ./pcb_out           # skip the planner, build a spec directly
    python -m pcb --check-kicad                                  # what does this machine have?
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import kicad_cli as kc
from .compose import SpecError
from .footprints import library_dir
from .pipeline import build_board
from .planner import PlanError, deepseek_call, plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pcb")
    ap.add_argument("description", nargs="?", help="what the board should do")
    ap.add_argument("--out", default="./pcb_out", help="output folder")
    ap.add_argument("--ai", action="store_true", help="use the DeepSeek planner (DEEPSEEK_API_KEY)")
    ap.add_argument("--spec", help="build this JSON spec directly (skips planning)")
    ap.add_argument("--no-kicad", action="store_true", help="skip KiCad even if installed")
    ap.add_argument("--check-kicad", action="store_true", help="report KiCad availability and exit")
    a = ap.parse_args(argv)

    if a.check_kicad:
        print("kicad-cli       :", kc.find_cli() or "NOT FOUND", f"({kc.cli_version()})" if kc.find_cli() else "")
        print("pcbnew python   :", kc.find_pcbnew_python() or "NOT FOUND (copper pours will be routed as traces)")
        print("footprint library:", library_dir() or "NOT FOUND (only built-in footprints; USB-C / TQFP blocks unavailable)")
        return 0

    try:
        if a.spec:
            spec, source, notes = json.loads(Path(a.spec).read_text(encoding="utf-8")), "spec file", []
        else:
            if not a.description:
                ap.error("give a description, or --spec FILE")
            call = None
            if a.ai:
                key = os.environ.get("DEEPSEEK_API_KEY")
                if not key:
                    ap.error("--ai needs the DEEPSEEK_API_KEY environment variable")
                call = deepseek_call(key, os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                     os.environ.get("DEEPSEEK_DEFAULT_MODEL", "deepseek-v4-flash"))
            res = plan(a.description, call)
            spec, source, notes = res.spec, res.source, res.notes
        print(f"planner: {source}; blocks: {', '.join(b['type'] for b in spec['blocks'])}")
        for n in notes:
            print("  note:", n)
        report = build_board(spec, Path(a.out), run_kicad=not a.no_kicad)
    except (PlanError, SpecError) as exc:
        print("Could not build that:", file=sys.stderr)
        for e in exc.errors:
            print("  -", e, file=sys.stderr)
        return 2

    print(f"\nboard {report['board_mm'][0]} x {report['board_mm'][1]} mm, {report['components']} parts, routed={report['routed']}")
    print("verification:", report["verification"], "| manufacturable:", report["manufacturable"])
    for stage, info in report["stages"].items():
        print(f"  {stage:9}", json.dumps(info)[:300])
    for w in report["warnings"] + report["review_items"]:
        print("  !", w)
    print("files:", ", ".join(report["files"]), "->", Path(a.out).resolve())
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

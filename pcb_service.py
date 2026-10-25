"""
Builds the manufacturing package (KiCad project, Gerbers, BOM, ...) for a stored `pcb_board` job.

A Studio PCB part is an ordinary CAD job whose parameters hold the board description and the planned
block spec. The CAD side (STL / STEP / IGES / DXF / PDF) is the 3D board. This module builds the
electronics side from the same stored parameters, on demand, and caches it next to the other exports.

Deterministic: the same stored spec always gives the same board, so the package can be rebuilt after
Railway's ephemeral disk is wiped (see storage.py) without changing what the customer already saw.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from pcb.pipeline import build_board, file_path_for
from pcb.planner import keyword_plan

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class PackageUnavailable(Exception):
    """The board cannot be produced as a package (message is safe to show the owner)."""


def package_dir(output_dir: Path, job_id: str) -> Path:
    return Path(output_dir) / "pcb" / job_id


def _lock_for(job_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(job_id, threading.Lock())


def spec_for_job(job: dict) -> dict:
    """The block spec stored with the job, or - for a job made from a bare description - the one the
    free offline planner gives for it (deterministic)."""
    params = json.loads(job["parameters"]) if job.get("parameters") else {}
    spec = params.get("spec")
    if isinstance(spec, dict):
        return spec
    description = str(params.get("description") or "").strip()
    if not description:
        raise PackageUnavailable("This job has no board description to build from.")
    try:
        return keyword_plan(description).spec
    except Exception as exc:  # noqa: BLE001 - PlanError carries the reason
        raise PackageUnavailable(" ".join(getattr(exc, "errors", None) or [str(exc)])[:400]) from exc


def load_report(outdir: Path) -> dict | None:
    p = Path(outdir) / "report.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ensure_package(job: dict, output_dir: Path, *, run_kicad: bool = True) -> dict:
    """The package report, building it first if it is not on disk. Raises PackageUnavailable when the
    board cannot be built (invalid spec, cannot be routed, failed design-rule check)."""
    outdir = package_dir(output_dir, job["id"])
    with _lock_for(job["id"]):
        report = load_report(outdir)
        if report is not None:
            return report
        try:
            report = build_board(spec_for_job(job), outdir, run_kicad=run_kicad)
        except PackageUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - SpecError and anything unexpected
            raise PackageUnavailable(" ".join(getattr(exc, "errors", None) or [str(exc)])[:400]) from exc
        if not report.get("ok"):
            problems = report["stages"]["route"]["unrouted_nets"] or ["it failed the design-rule checks"]
            # Nothing usable: do not leave a half package behind to be served later.
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)
            raise PackageUnavailable("The board could not be produced cleanly: " + ", ".join(map(str, problems))[:300])
        return report


def file_for(job: dict, output_dir: Path, kind: str) -> Path | None:
    return file_path_for(package_dir(output_dir, job["id"]), kind)

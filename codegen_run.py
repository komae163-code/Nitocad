"""Runs model-written CadQuery code in a locked-down subprocess (codegen_worker.py) and returns what it built.

Same containment as file_import.py, which this reuses for the shared concurrency gate and error types:
  * the child gets NO inherited environment, so API keys and wallet secrets are not visible to it
  * memory, CPU and file-size limits, a wall-clock timeout, and a process-group kill on timeout
  * the code is validated (codegen_check.py) before a process is started, and again inside the worker
  * the code can only import cadquery and math. If your host allows it, also deny this container outbound traffic:
    a Python-level allowlist is defence in depth, not a guarantee, and this runs code an AI wrote from a stranger's prompt.

run() returns a dict, never raises for problems with the code (those are repairable):
  {"ok": True,  "stl": bytes, "step": bytes|None, "brep": bytes|None, "stats": {...}, "values": {...}}
  {"ok": False, "kind": "code", "error": "line 12: ..."}        the model can fix this
Infrastructure problems (busy, kernel missing) raise, so they are never fed back to the model as "your code is wrong".
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

import codegen_check as CC
import file_import as FI

WORKER = Path(__file__).with_name("codegen_worker.py")
TIMEOUT_S = float(os.environ.get("CODEGEN_TIMEOUT_S", "60"))
MEM_BYTES = int(os.environ.get("CODEGEN_MEM_MB", "2048")) * 1024 * 1024
EXPORTS = ("stl", "step", "brep")


class KernelUnavailable(FI.FileImportError):
    status_code = 503


def _child_limits() -> None:                         # runs in the child before exec (POSIX only)
    import resource
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (MEM_BYTES, MEM_BYTES))
    cpu = int(TIMEOUT_S) + 10
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FI.MAX_OUTPUT_BYTES, FI.MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


async def run(source: str, overrides: dict[str, Any] | None = None, exports: tuple[str, ...] | list[str] = (),
              library: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """`library` is {name: {"path": <file the server already downloaded and checked>, "scale": float}} from codegen_parts.
    Only the file's bytes enter the sandbox's work folder; the code never sees a path or a URL."""
    problems = CC.validate(source)
    if problems:
        return {"ok": False, "kind": "code", "error": " ".join(problems[:4])}
    exports = [e for e in exports if e in EXPORTS]
    sem = FI._semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=FI.QUEUE_WAIT_S)
    except asyncio.TimeoutError:
        raise FI.ServerBusy("The CAD kernel is busy. Please try again in a moment.")
    work = Path(tempfile.mkdtemp(prefix="codegen_"))
    try:
        lib_job: dict[str, Any] = {}
        for name, part in (library or {}).items():
            fname = f"lib_{name}.step"
            shutil.copyfile(part["path"], work / fname)
            lib_job[name] = {"file": fname, "scale": float(part.get("scale", 1.0))}
        (work / "job.json").write_text(json.dumps({"source": source, "overrides": overrides or {}, "exports": exports, "library": lib_job}))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "2",
               "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("PYTHONPATH", "LD_LIBRARY_PATH", "CONDA_PREFIX", "VIRTUAL_ENV"):     # needed to find the kernel, carries no secrets
            if k in os.environ:
                env[k] = os.environ[k]
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(WORKER), str(work), cwd=str(work), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=_child_limits if os.name == "posix" else None)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            await proc.wait()
            return {"ok": False, "kind": "code", "error": f"The code ran for more than {int(TIMEOUT_S)} seconds and was stopped. "
                                                          "Use fewer or simpler boolean operations, and avoid long loops."}
        res_path = work / "result.json"
        result = json.loads(res_path.read_text()) if res_path.exists() else {}
        if not result.get("ok"):
            if result.get("kind") == "infra":
                raise KernelUnavailable(str(result.get("error") or "The CAD kernel is not available on this server."))
            msg = result.get("error") or ("The code crashed the CAD kernel. Try simpler geometry, for example fewer fillets and booleans."
                                          if proc.returncode else "The code produced no result.")
            return {"ok": False, "kind": "code", "error": str(msg)[:600]}
        out: dict[str, Any] = {"ok": True, "stats": result.get("stats", {}), "values": result.get("values", {}), "step": None, "brep": None}
        for name in EXPORTS:
            p = work / f"out.{name}"
            if p.exists():
                if p.stat().st_size > FI.MAX_OUTPUT_BYTES:
                    return {"ok": False, "kind": "code", "error": "The result is too large to return. Simplify the model."}
                out[name] = p.read_bytes()
        if not out.get("stl"):
            return {"ok": False, "kind": "code", "error": "The code produced no geometry."}
        return out
    finally:
        sem.release()
        shutil.rmtree(work, ignore_errors=True)

"""Server-side import and editing of B-rep CAD files (STEP, IGES, BREP).

Everything else the import workspace opens (STL, OBJ, DXF, SVG, PDF, images, KiCad, Gerber) is handled in
the browser and never reaches the server. B-rep needs the OpenCascade kernel, so it comes here, but the
kernel runs in a separate, locked-down subprocess (cad_worker.py):
  * environment variables are NOT inherited, so server secrets are not visible to it
  * memory, CPU and output-size limits, a wall-clock timeout, and a process-group kill on timeout
  * at most CAD_WORKER_CONCURRENCY jobs at once; extra requests wait briefly, then get 503
  * input is size-capped, extension-checked and content-sniffed before anything is parsed
  * edits are a fixed list of validated operations; no user code is ever executed
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

from exceptions import NitocadError

CAD_EXTS = {"step": "step", "stp": "step", "iges": "iges", "igs": "iges", "brep": "brep", "brp": "brep"}
EXPORTS = ("stl", "step", "brep")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_BYTES = 150 * 1024 * 1024
MAX_OPS = 20
WORKER_TIMEOUT_S = float(os.environ.get("CAD_WORKER_TIMEOUT_S", "90"))
QUEUE_WAIT_S = float(os.environ.get("CAD_QUEUE_WAIT_S", "20"))
MAX_CONCURRENT = int(os.environ.get("CAD_WORKER_CONCURRENCY", "2"))
WORKER_MEM_BYTES = int(os.environ.get("CAD_WORKER_MEM_MB", "3072")) * 1024 * 1024
WORKER = Path(__file__).with_name("cad_worker.py")


class FileImportError(NitocadError):
    status_code = 400


class UnsupportedUpload(FileImportError):
    status_code = 415


class UploadTooLarge(FileImportError):
    status_code = 413


class CadProcessingError(FileImportError):
    status_code = 422


class CadTimeout(FileImportError):
    status_code = 504


class ServerBusy(FileImportError):
    status_code = 503


def format_of(filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in CAD_EXTS:
        raise UnsupportedUpload("Only STEP, IGES and BREP files can be opened on the server.")
    return CAD_EXTS[ext]


def sniff_cad(fmt: str, head: bytes) -> bool:
    """Extension alone is never trusted: the leading bytes must look like the claimed format."""
    if fmt == "step":
        return b"ISO-10303-21" in head[:2048]
    if fmt == "iges":
        first = head.split(b"\n", 1)[0].rstrip(b"\r")
        return len(first) >= 73 and first[72:73] == b"S"
    if fmt == "brep":
        return re.search(rb"DBRep_DrawableShape|CASCADE Topology|^Open CASCADE", head[:2048], re.M) is not None
    return False


def safe_stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", (filename or "model").rsplit(".", 1)[0]).strip("._") or "model"
    return stem[:60]


async def read_limited(upload: Any, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(f"This file is larger than the {limit // (1024 * 1024)} MB limit for server-side CAD files.")
        chunks.append(chunk)
    if total == 0:
        raise FileImportError("This file is empty.")
    return b"".join(chunks)


def _num(v: Any, name: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise FileImportError(f"{name} must be a number.")
    if not math.isfinite(x) or not lo <= x <= hi:
        raise FileImportError(f"{name} must be between {lo:g} and {hi:g}.")
    return x


def validate_ops(ops: Any) -> list[dict]:
    """Accepts only the fixed operations below. Returns a normalised copy; raises FileImportError otherwise."""
    if ops in (None, ""):
        return []
    if not isinstance(ops, list):
        raise FileImportError("ops must be a list.")
    if len(ops) > MAX_OPS:
        raise FileImportError(f"At most {MAX_OPS} edit operations are allowed per request.")
    out: list[dict] = []
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict) or "op" not in op:
            raise FileImportError(f"Operation {i} is not valid.")
        k = op["op"]
        if k == "translate":
            out.append({"op": k, **{a: _num(op.get(a, 0), a, -1e6, 1e6) for a in ("x", "y", "z")}})
        elif k == "rotate":
            if op.get("axis") not in ("x", "y", "z"):
                raise FileImportError("rotate needs axis x, y or z.")
            about = op.get("about", [0, 0, 0])
            if not isinstance(about, (list, tuple)) or len(about) != 3:
                raise FileImportError("rotate 'about' must be three numbers.")
            out.append({"op": k, "axis": op["axis"], "deg": _num(op.get("deg"), "deg", -3600, 3600), "about": [_num(a, "about", -1e6, 1e6) for a in about]})
        elif k == "scale":
            out.append({"op": k, "factor": _num(op.get("factor"), "factor", 1e-3, 1e3)})
        elif k == "mirror":
            if op.get("plane") not in ("XY", "YZ", "XZ"):
                raise FileImportError("mirror needs plane XY, YZ or XZ.")
            out.append({"op": k, "plane": op["plane"]})
        elif k == "center":
            out.append({"op": k})
        elif k == "fillet":
            out.append({"op": k, "radius": _num(op.get("radius"), "radius", 1e-3, 1000)})
        elif k == "chamfer":
            out.append({"op": k, "length": _num(op.get("length"), "length", 1e-3, 1000)})
        else:
            raise FileImportError(f"Unknown operation '{str(k)[:30]}'.")
    return out


def validate_exports(fmts: Any) -> list[str]:
    fmts = ["stl"] if not fmts else list(fmts)
    bad = [f for f in fmts if f not in EXPORTS]
    if bad:
        raise FileImportError("Export format must be stl, step or brep.")
    return sorted(set(fmts) | {"stl"})


_loop_sem: tuple[Any, int, asyncio.Semaphore] | None = None


def _semaphore() -> asyncio.Semaphore:
    """One semaphore per running event loop (an asyncio semaphore cannot be shared across loops)."""
    global _loop_sem
    loop = asyncio.get_running_loop()
    if _loop_sem is None or _loop_sem[0] is not loop or _loop_sem[1] != MAX_CONCURRENT:
        _loop_sem = (loop, MAX_CONCURRENT, asyncio.Semaphore(MAX_CONCURRENT))
    return _loop_sem[2]


def _child_limits() -> None:                      # runs in the child before exec (POSIX only)
    import resource
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEM_BYTES, WORKER_MEM_BYTES))
    cpu = int(WORKER_TIMEOUT_S) + 10
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


async def process_cad(data: bytes, fmt: str, ops: list[dict], exports: list[str]) -> dict:
    """Runs the worker. Returns {'stl': bytes, 'step': bytes|None, 'brep': bytes|None, 'stats': dict}."""
    sem = _semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=QUEUE_WAIT_S)
    except asyncio.TimeoutError:
        raise ServerBusy("The CAD converter is busy. Please try again in a moment.")
    work = Path(tempfile.mkdtemp(prefix="cadimp_"))
    try:
        (work / f"in.{fmt}").write_bytes(data)
        (work / "job.json").write_text(json.dumps({"fmt": fmt, "ops": ops, "exports": exports}))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(work), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "2", "PYTHONHASHSEED": "0"}
        for k in ("PYTHONPATH", "LD_LIBRARY_PATH", "CONDA_PREFIX", "VIRTUAL_ENV"):      # needed to find the kernel, carries no secrets
            if k in os.environ:
                env[k] = os.environ[k]
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(WORKER), str(work), cwd=str(work), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_child_limits if os.name == "posix" else None)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=WORKER_TIMEOUT_S)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            await proc.wait()
            raise CadTimeout("This file took too long to process. Try a simpler model or fewer edits.")
        res_path = work / "result.json"
        result = json.loads(res_path.read_text()) if res_path.exists() else {}
        if proc.returncode != 0 or not result.get("ok"):
            raise CadProcessingError(str(result.get("error") or "This file could not be processed. It may be damaged or use unsupported geometry.")[:300])
        out: dict[str, Any] = {"stats": result.get("stats", {}), "step": None, "brep": None}
        for name in ("stl", "step", "brep"):
            p = work / f"out.{name}"
            if p.exists():
                if p.stat().st_size > MAX_OUTPUT_BYTES:
                    raise CadProcessingError("The result is too large to return.")
                out[name] = p.read_bytes()
        if not out.get("stl"):
            raise CadProcessingError("The converter produced no geometry.")
        return out
    finally:
        sem.release()
        shutil.rmtree(work, ignore_errors=True)

"""Runs inside the restricted subprocess started by codegen_run.py. Usage: codegen_worker.py <workdir>

Reads <workdir>/job.json {"source", "overrides", "exports"}, runs the model's `build(p)`, and writes out.stl (always),
out.step / out.brep (if asked for) and result.json. Messages written to result.json are shown to people and fed back to
the model, so they never contain file paths: a failure inside the model's code is reported as `line N: <message>`.

Layers here (the process limits set by the parent are the real boundary):
  1. the source is validated again, never trusting the parent (codegen_check.validate)
  2. the code runs with a small allowlist of builtins and an import hook that only hands out `cadquery` and `math`
  3. the `cadquery` module the code sees has its file / interpreter-facing submodules removed
"""
from __future__ import annotations

import json
import math
import sys
import traceback
import types
from pathlib import Path

import codegen_check as CC

MAX_SOLIDS = 500
MAX_FACES = 200_000
MAX_DIAGONAL_MM = 20_000.0
MIN_DIAGONAL_MM = 0.05


class CodeError(Exception):
    """A problem with the model's code or its output. The message is safe to show and to feed back to the model."""


def done(work: Path, ok: bool, **kw) -> None:
    (work / "result.json").write_text(json.dumps({"ok": ok, **kw}))
    sys.exit(0 if ok else 2)


def safe_cadquery(cq) -> types.ModuleType:
    """A copy of the cadquery module without the names that read or write files or reach interpreter internals."""
    m = types.ModuleType("cadquery")
    for k, v in vars(cq).items():
        if k.startswith("_") or k in CC.HIDDEN_CQ_NAMES:
            continue
        setattr(m, k, v)
    return m


def namespace(cq) -> dict:
    scq = safe_cadquery(cq)
    allowed = {"cadquery": scq, "math": math}

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):      # noqa: A002
        if level or name not in allowed:
            raise ImportError(f"import of '{name}' is not allowed")
        return allowed[name]

    import builtins
    safe = {k: getattr(builtins, k) for k in CC.SAFE_BUILTINS if hasattr(builtins, k) and k != "print"}
    safe["print"] = lambda *a, **k: None
    safe["__import__"] = guarded_import
    return {"__builtins__": safe, "__name__": "model"}


def coerce_params(meta: dict, overrides: dict) -> dict:
    """Parameter values to build with: defaults, then overrides. Unknown names and out-of-range values are errors."""
    unknown = sorted(set(overrides) - set(meta))
    if unknown:
        raise CodeError(f"Unknown parameter(s): {', '.join(unknown)}.")
    values: dict = {}
    for name, m in meta.items():
        v = overrides.get(name, m["default"])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            raise CodeError(f"Parameter '{name}' must be a number.")
        if not m["min"] <= v <= m["max"]:
            raise CodeError(f"Parameter '{name}' must be between {m['min']} and {m['max']}.")
        values[name] = int(round(v)) if m.get("int") else v
    return values


def _user_line(exc: BaseException) -> int | None:
    line = None
    for fr in traceback.extract_tb(exc.__traceback__):
        if fr.filename == "<model_code>":
            line = fr.lineno
    return line


def load_library(work: Path, library: dict, cq) -> dict:
    """Import each library STEP file (a file in the work folder, never a path from the model) as a Workplane centred on the origin.
    Parts are in millimetres; `scale` multiplies them (for example 25.4 for a file in inches)."""
    out: dict = {}
    for name, spec in (library or {}).items():
        fname = str(spec.get("file", ""))
        if not CC._PARAM_NAME.match(name) or "/" in fname or "\\" in fname or ".." in fname:
            raise CodeError(f"Library part '{name}' is not valid.")
        path = work / fname
        try:
            objs = cq.importers.importStep(str(path)).vals()
            shape = objs[0] if len(objs) == 1 else cq.Compound.makeCompound(objs)
            scale = float(spec.get("scale", 1.0))
            if scale != 1.0:
                shape = shape.scale(scale)
            c = shape.BoundingBox().center
            shape = shape.translate(cq.Vector(-c.x, -c.y, -c.z))
        except Exception:                                                    # noqa: BLE001
            raise CodeError(f"The library part for '{name}' could not be read as a solid.") from None
        if not shape.Solids():
            raise CodeError(f"The library part for '{name}' has no solids.")
        out[name] = cq.Workplane("XY").newObject([shape])
    return out


def run_build(source: str, overrides: dict, cq, lib: dict | None = None):
    """Validate, execute and return (shape, parameter_values). Raises CodeError with a plain message."""
    problems = CC.validate(source)
    if problems:
        raise CodeError(" ".join(problems[:4]))
    meta = CC.parse_params(source)
    values = coerce_params(meta, overrides)
    ns = namespace(cq)
    try:
        exec(compile(source, "<model_code>", "exec"), ns)                    # noqa: S102  - validated, limited, sandboxed
        build = ns.get("build")
        if not callable(build):
            raise CodeError("`build` is not a function.")
        result = build(dict(values), lib or {}) if CC.parse_library(source) else build(dict(values))
    except CodeError:
        raise
    except MemoryError:
        raise CodeError("The model used too much memory. Use simpler geometry.") from None
    except RecursionError:
        raise CodeError("The code recursed too deeply.") from None
    except BaseException as e:                                               # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        line = _user_line(e)
        where = f"line {line}: " if line else ""
        raise CodeError(f"{where}{type(e).__name__}: {str(e)[:240]}") from None
    return to_shape(result, cq), values


def to_shape(result, cq):
    """One Compound from a Workplane, Shape, Assembly, or a list / tuple of those."""
    items = list(result) if isinstance(result, (list, tuple)) else [result]
    shapes = []
    for it in items:
        if isinstance(it, cq.Workplane):
            shapes.extend(v for v in it.vals() if isinstance(v, cq.Shape))
        elif isinstance(it, cq.Shape):
            shapes.append(it)
        elif isinstance(it, cq.Assembly):
            shapes.append(it.toCompound())
        else:
            raise CodeError("build(p) must return a cq.Workplane, a cq.Shape, or a list of them.")
    if not shapes:
        raise CodeError("build(p) returned nothing. The workplane stack is empty.")
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def check_shape(shape) -> dict:
    solids = shape.Solids()
    if not solids:
        raise CodeError("The result has no solids. Build a closed solid (extrude, revolve, loft or boolean), not just wires or faces.")
    if len(solids) > MAX_SOLIDS:
        raise CodeError(f"The result has {len(solids)} solids, more than {MAX_SOLIDS}. Merge or simplify.")
    faces = len(shape.Faces())
    if faces > MAX_FACES:
        raise CodeError(f"The result has {faces} faces, more than {MAX_FACES}. Simplify.")
    bb = shape.BoundingBox()
    diag = math.sqrt(bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2)
    if diag > MAX_DIAGONAL_MM:
        raise CodeError(f"The model is {diag:.0f} mm across. Check the units: everything is in millimetres.")
    if diag < MIN_DIAGONAL_MM:
        raise CodeError(f"The model is only {diag:.4f} mm across. Check the units: everything is in millimetres.")
    volume = sum(s.Volume() for s in solids)
    if not volume > 0:
        raise CodeError("The solids have no volume. Check that profiles are closed and extruded.")
    stats = {"size": [round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)], "min": [round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)],
             "solids": len(solids), "faces": faces, "volume": round(volume, 4), "area": round(shape.Area(), 4), "valid": bool(shape.isValid())}
    notes = []
    if not stats["valid"]:
        notes.append("The solid failed the kernel's validity check. Booleans may have produced bad geometry.")
    if len(solids) > 1:
        notes.append(f"The result is {len(solids)} separate solids. If this should be one part, union them.")
    stats["notes"] = notes
    return stats


def main() -> None:
    work = Path(sys.argv[1])
    job = json.loads((work / "job.json").read_text())
    exports = job.get("exports") or []
    try:
        import cadquery as cq
    except Exception:                                                        # noqa: BLE001
        done(work, False, kind="infra", error="The CAD kernel is not available on this server.")
    try:
        lib = load_library(work, job.get("library") or {}, cq)
        shape, values = run_build(job["source"], job.get("overrides") or {}, cq, lib)
        stats = check_shape(shape)
    except CodeError as e:
        done(work, False, kind="code", error=str(e)[:600])
    bb = shape.BoundingBox()
    diag = math.sqrt(bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2)
    tol = min(0.5, max(0.01, diag * 0.0005))
    wp = cq.Workplane("XY").newObject([shape])
    try:
        cq.exporters.export(wp, str(work / "out.stl"), exportType="STL", tolerance=tol, angularTolerance=0.2)
        if "step" in exports:
            cq.exporters.export(wp, str(work / "out.step"), exportType="STEP")
        if "brep" in exports:
            shape.exportBrep(str(work / "out.brep"))
    except Exception:                                                        # noqa: BLE001
        done(work, False, kind="code", error="The result could not be exported. The geometry may be invalid.")
    done(work, True, stats=stats, values=values)


if __name__ == "__main__":
    main()

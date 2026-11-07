"""Static checks for CadQuery code written by a model (see codegen_llm.py).

This is the first of several layers, and it is NOT the security boundary by itself. The boundary is the locked-down
subprocess in codegen_run.py (no secrets in its environment, memory / CPU / file-size limits, wall-clock kill). These
checks exist so that obviously unsafe or broken code is rejected cheaply, with a message the model can use to repair
its own code, before any process is started.

The contract the model must follow:

    import cadquery as cq
    PARAMS = {"length": {"default": 120, "min": 20, "max": 400, "label": "Length", "unit": "mm"}}
    def build(p):
        return cq.Workplane("XY").box(p["length"], 40, 10)

PARAMS must be a plain literal so the sliders can be shown without running anything. build(p) gets the values and
returns a Workplane, a Shape, or a list of them.

Optional: standard parts from open libraries (bearings, screws, connectors) are requested declaratively, never fetched by the code:

    LIBRARY = {"bearing": {"query": "608 bearing"}, "screw": {"query": "M3 screw", "scale": 1.0}}
    def build(p, lib):
        brg = lib["bearing"]        # a cq.Workplane, centred on the origin, in millimetres

The server searches the licence-checked providers (asset_providers.py), downloads the STEP file outside the sandbox, and hands
the imported solid to build(). If LIBRARY is present, build must take two arguments, and if it is absent, one.

Rules enforced here:
  * only `import cadquery` / `import math` (and `from ... import` of those two); no star imports
  * every name that is read must be a local, an import, or a safe builtin (no open, eval, exec, getattr, ...)
  * no attribute that starts with an underscore, and no attribute that touches files or interpreter internals
    (export*, import*, save, exporters, importers, occ_impl, frame / code attributes, str.format)
  * no classes, global / nonlocal, with, async, yield, or keyword arguments that name a file path
  * size caps on source text and on the syntax tree
"""
from __future__ import annotations

import ast
import re
from typing import Any

MAX_SOURCE_BYTES = 24_000
MAX_NODES = 5_000
MAX_PARAMS = 30
ALLOWED_MODULES = ("cadquery", "math")
# Names inside `cadquery` that must not be reachable from model code. Also removed from the module object at run time.
HIDDEN_CQ_NAMES = ("exporters", "importers", "occ_impl", "OCP", "utils", "cqgi", "vis", "func")

SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "divmod", "enumerate", "float", "int", "len", "list", "map", "max", "min",
    "pow", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "dict", "isinstance",
    "print", "True", "False", "None", "ValueError", "TypeError", "ZeroDivisionError", "Exception", "AssertionError",
    "IndexError", "KeyError", "RuntimeError",
}
_BANNED_ATTR_EXACT = {"format", "format_map", "mro", "save", "exporters", "importers", "occ_impl", "OCP"}
_BANNED_ATTR_PREFIX = ("_", "export", "import", "gi_", "cr_", "ag_", "f_", "tb_", "co_")
_BANNED_KEYWORDS = {"fontPath", "filename", "fileName", "file", "fname"}   # not "path": Workplane.sweep(path=...) is a wire, not a file
_BANNED_NODES = (ast.ClassDef, ast.Global, ast.Nonlocal, ast.With, ast.AsyncWith, ast.AsyncFor, ast.AsyncFunctionDef,
                 ast.Await, ast.Yield, ast.YieldFrom)
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name the code itself binds anywhere (scoping is ignored on purpose: this only has to rule out names the
    code never created, such as `open`)."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.Lambda)):
            a = n.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]:
                out.add(arg.arg)
            if isinstance(n, ast.FunctionDef):
                out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                out.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
    return out


def _attr_banned(name: str) -> bool:
    return name in _BANNED_ATTR_EXACT or name.startswith(_BANNED_ATTR_PREFIX)


def validate(source: str) -> list[str]:
    """Problems with the code, each a short plain sentence the model can act on. Empty list means it passed."""
    if not isinstance(source, str) or not source.strip():
        return ["The code is empty."]
    if len(source.encode("utf-8", "ignore")) > MAX_SOURCE_BYTES:
        return [f"The code is longer than {MAX_SOURCE_BYTES} bytes. Make it shorter."]
    try:
        tree = ast.parse(source, filename="<model_code>")
    except SyntaxError as e:
        return [f"Syntax error on line {e.lineno}: {e.msg}."]
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        return ["The code is too large or deeply nested. Make it simpler."]

    errors: list[str] = []

    def bad(node: ast.AST, msg: str) -> None:
        errors.append(f"Line {getattr(node, 'lineno', '?')}: {msg}")

    bound = _bound_names(tree)
    for n in ast.walk(tree):
        if isinstance(n, _BANNED_NODES):
            bad(n, f"{type(n).__name__} is not allowed.")
        elif isinstance(n, ast.Import):
            for al in n.names:
                if al.name not in ALLOWED_MODULES:
                    bad(n, f"Only these imports are allowed: {', '.join(ALLOWED_MODULES)}.")
        elif isinstance(n, ast.ImportFrom):
            if n.level or n.module not in ALLOWED_MODULES:
                bad(n, f"Only these imports are allowed: {', '.join(ALLOWED_MODULES)}.")
            for al in n.names:
                if al.name == "*":
                    bad(n, "Star imports are not allowed. Import names explicitly.")
                elif _attr_banned(al.name) or al.name in HIDDEN_CQ_NAMES:
                    bad(n, f"'{al.name}' cannot be imported.")
        elif isinstance(n, ast.Attribute):
            if _attr_banned(n.attr) or n.attr in HIDDEN_CQ_NAMES:
                bad(n, f"The attribute '{n.attr}' is not allowed. Exporting, importing and file access are handled by the server.")
        elif isinstance(n, ast.Name) and n.id.startswith("__"):
            bad(n, f"The name '{n.id}' is not allowed.")
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id.startswith("__"):
                bad(n, f"The name '{n.id}' is not allowed.")
            elif n.id not in bound and n.id not in SAFE_BUILTINS:
                bad(n, f"The name '{n.id}' is not defined or not allowed. Allowed built-ins: {', '.join(sorted(b for b in SAFE_BUILTINS if b[0].islower()))}.")
        elif isinstance(n, ast.keyword) and n.arg in _BANNED_KEYWORDS:
            bad(n.value, f"The keyword argument '{n.arg}' is not allowed (no file paths).")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith(("/", "~", "..")):
            bad(n, "Strings that look like file paths are not allowed.")
    if errors:
        return errors[:12]

    top = tree.body
    builds = [s for s in top if isinstance(s, ast.FunctionDef) and s.name == "build"]
    try:
        library = parse_library(source)
    except ValueError as e:
        library = None
        errors.append(str(e))
    if not builds or len(builds[-1].args.args) not in (1, 2):
        errors.append("Define `def build(p):` at the top level. It takes one argument, the dict of parameter values, and returns the model.")
    elif library is not None:
        want = 2 if library else 1
        if len(builds[-1].args.args) != want:
            errors.append("When LIBRARY has parts, define `def build(p, lib):`. Without LIBRARY, define `def build(p):`.")
    try:
        parse_params(source)
    except ValueError as e:
        errors.append(str(e))
    return errors


MAX_LIBRARY_PARTS = 6
_QUERY = re.compile(r"^[A-Za-z0-9 _.+\-]{2,80}$")


def parse_library(source: str) -> dict[str, dict[str, Any]]:
    """The optional top-level LIBRARY literal: {name: {"query": str, "scale": number}}. Empty dict when there is none."""
    try:
        tree = ast.parse(source, filename="<model_code>")
    except SyntaxError as e:
        raise ValueError(f"Syntax error on line {e.lineno}: {e.msg}.") from None
    node = None
    for s in tree.body:
        if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) and s.targets[0].id == "LIBRARY":
            node = s.value
    if node is None:
        return {}
    try:
        raw = ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        raise ValueError("LIBRARY must be a plain literal dict (strings and numbers only).") from None
    if not isinstance(raw, dict):
        raise ValueError("LIBRARY must be a dict of {name: {\"query\": ...}}.")
    if len(raw) > MAX_LIBRARY_PARTS:
        raise ValueError(f"LIBRARY has more than {MAX_LIBRARY_PARTS} parts. Model the simple ones yourself.")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not _PARAM_NAME.match(name):
            raise ValueError(f"LIBRARY name {name!r} must be lower_snake_case, up to 40 characters.")
        if not isinstance(spec, dict) or not isinstance(spec.get("query"), str) or not _QUERY.match(spec["query"]):
            raise ValueError(f"LIBRARY['{name}'] needs a \"query\" of 2 to 80 letters, digits, spaces or . _ + -.")
        scale = spec.get("scale", 1.0)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.001 <= scale <= 1000:
            raise ValueError(f"LIBRARY['{name}']['scale'] must be a number between 0.001 and 1000.")
        extra = set(spec) - {"query", "scale"}
        if extra:
            raise ValueError(f"LIBRARY['{name}'] has unknown keys: {', '.join(sorted(map(str, extra)))}.")
        out[name] = {"query": spec["query"].strip(), "scale": float(scale)}
    return out


def parse_params(source: str) -> dict[str, dict[str, Any]]:
    """Read the top-level PARAMS literal without running anything. Raises ValueError with a plain message."""
    try:
        tree = ast.parse(source, filename="<model_code>")
    except SyntaxError as e:
        raise ValueError(f"Syntax error on line {e.lineno}: {e.msg}.") from None
    node = None
    for s in tree.body:
        if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) and s.targets[0].id == "PARAMS":
            node = s.value
    if node is None:
        raise ValueError("Define a top-level `PARAMS = {...}` dict of named numeric parameters with default, min and max.")
    try:
        raw = ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        raise ValueError("PARAMS must be a plain literal dict (numbers and strings only, no expressions or variables).") from None
    if not isinstance(raw, dict) or not raw:
        raise ValueError("PARAMS must be a non-empty dict.")
    if len(raw) > MAX_PARAMS:
        raise ValueError(f"PARAMS has more than {MAX_PARAMS} entries.")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not _PARAM_NAME.match(name):
            raise ValueError(f"Parameter name {name!r} must be lower_snake_case, up to 40 characters.")
        if not isinstance(spec, dict):
            raise ValueError(f"PARAMS['{name}'] must be a dict with default, min and max.")
        nums = {}
        for k in ("default", "min", "max"):
            v = spec.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e7:
                raise ValueError(f"PARAMS['{name}']['{k}'] must be a number.")
            nums[k] = v
        if not nums["min"] < nums["max"]:
            raise ValueError(f"PARAMS['{name}'] needs min < max.")
        if not nums["min"] <= nums["default"] <= nums["max"]:
            raise ValueError(f"PARAMS['{name}'] default must be between min and max.")
        label = spec.get("label", name.replace("_", " ").capitalize())
        unit = spec.get("unit", "mm")
        if not isinstance(label, str) or not isinstance(unit, str) or len(label) > 60 or len(unit) > 12:
            raise ValueError(f"PARAMS['{name}'] label / unit must be short strings.")
        integer = bool(spec.get("int", False))
        out[name] = {"default": nums["default"], "min": nums["min"], "max": nums["max"], "label": label, "unit": unit, "int": bool(integer)}
    return out

"""
Vehicle spec layer: a vehicle is a JSON spec, not a codebase.

A loco, a bike and a helicopter are different specs run through the same expander. The expander turns a spec into
the flat parts list that assembly_planner / assembly_spec already understand, so nothing downstream changes.

Spec shape (all keys but `name` and `components` are optional):

  {
    "name": "mini_loco",
    "params":  {"gauge": {"default": 32, "min": 10, "max": 200}, "wheel_dia": 20},     # named, bounded, editable
    "derived": {"axle_len": "=gauge + 2*wheel_thk + 2*clearance"},                      # evaluated in order
    "components": [
      {"id": "chassis", "type": "flat_plate", "assembly": "loco", "fixed": true,
       "parameters": {"length_mm": "=body_len", "width_mm": 40, "thickness_mm": "=chassis_t"},
       "position": [0, 0, "=wheel_dia/2"], "material": "PLA"},
      {"id": "axle", "type": "shaft", "repeat": {"n": 2, "step": ["=axle_space", 0, 0]}, ...},
      {"id": "pin",  "source": {"provider": "cq_warehouse", "query": "socket head"}}   # fetched later, not modelled here
    ],
    "checks": [{"name": "fits_width", "expr": "=body_w <= max_w", "message": "..."}]
  }

Rules
  * A string that starts with "=" is an expression. Anything else is a literal. Expressions are parsed with a strict
    whitelist (arithmetic, a few math functions, comparisons); no attribute access, no calls to anything else.
  * `repeat` {n, step} makes id_1..id_n, offset by i*step (i counts from 0; i and n are usable in expressions).
  * `mirror: "y"` makes id_l and id_r with the y position negated. Rotation is NOT mirrored; set it yourself.
  * A component may give a `mate` instead of a `position` (face or coaxial, see mates.py). Numbers in a mate may be
    "=expressions". `to` names an EARLIER part; in a repeated or mirrored component use {i} (1-based) and {side}
    ("_l", "_r" or "") in it, e.g. "to": "axle_{i}". Mates need quarter-turn (multiples of 90 degrees) rotations on
    both parts; that is a limit of the mate solver, not of this layer.
  * Modules (reusable internals): {"id": "front", "module": "wheel_on_axle", "params": {"rim_dia": "=wheel_dia"},
    "position": [...], "assembly": "x"} places a module from `spec["modules"]` or vehicles/modules/<name>.json. A module
    has its own params/derived/components/checks and cannot see the vehicle's names (it is reusable). Its parts are
    named <id>_<component>. Modules are translated only (no rotation), cannot repeat or nest; list instances instead.
  * `between`: [[x1,y1,z1],[x2,y2,z2]] on a linear part (tube, shaft) sets position, rotation and length_mm from the two
    end points, so frames are drawn by their joints. Only tubes in the X-Z plane or along Y are supported. Assumes the
    part's axis is local Z and (origin "center", default) its origin is mid-length; use "origin": "base" if the origin
    is at the start. THESE TWO ASSUMPTIONS ARE NOT VERIFIED against the real templates; check them on CadQuery.
  * Parameters are checked against the part catalog by assembly_planner.validate_spec, so a bad parameter fails here.
  * No part-count cap. A guard on expansion size (MAX_EXPANDED) stops a typo such as n = 1e9 from hanging the server.
  * Nothing here builds geometry or measures it. `checks` are arithmetic on your parameters only.
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from domain_packs.specs import P, resolve

MAX_EXPANDED = 20000
MODULE_DIR = Path(__file__).resolve().parent / "vehicles" / "modules"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_MAX_EXPR_LEN, _MAX_NODES = 300, 120
_FUNCS: dict[str, Callable[..., float]] = {
    "min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "atan2": math.atan2, "radians": math.radians, "degrees": math.degrees, "round": round, "floor": math.floor,
    "ceil": math.ceil, "hypot": math.hypot,
    "approx": lambda a, b, tol=1e-6: abs(a - b) <= tol,
}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


class SpecError(ValueError):
    pass


def evaluate(expr: str, env: dict[str, float]) -> float | bool:
    """Evaluate one whitelisted expression. Raises SpecError with a plain message on anything else."""
    if not isinstance(expr, str) or len(expr) > _MAX_EXPR_LEN:
        raise SpecError(f"expression too long or not text (limit {_MAX_EXPR_LEN} characters)")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise SpecError(f"cannot read expression {expr!r}: {e.msg}") from None
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise SpecError(f"expression {expr!r} is too complicated")

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id == "pi":
                return math.pi
            if n.id not in env:
                raise SpecError(f"unknown name {n.id!r} in {expr!r}")
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            try:
                return _BIN[type(n.op)](a, b)
            except ZeroDivisionError:
                raise SpecError(f"division by zero in {expr!r}") from None
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            a, b = ev(n.left), ev(n.right)
            if abs(b) > 16:
                raise SpecError(f"exponent too large in {expr!r}")
            try:
                r = a ** b
            except (ZeroDivisionError, OverflowError, ValueError):
                raise SpecError(f"invalid power in {expr!r}") from None
            if isinstance(r, complex):
                raise SpecError(f"power gives a complex number in {expr!r}")
            return r
        if isinstance(n, ast.Compare):
            left, ok = ev(n.left), True
            for op, comp in zip(n.ops, n.comparators):
                if type(op) not in _CMP:
                    raise SpecError(f"comparison not allowed in {expr!r}")
                right = ev(comp)
                ok = ok and _CMP[type(op)](left, right)
                left = right
            return ok
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            try:
                return _FUNCS[n.func.id](*[ev(a) for a in n.args])
            except (ValueError, TypeError, OverflowError) as e:
                raise SpecError(f"{n.func.id}() failed in {expr!r}: {e}") from None
        raise SpecError(f"{expr!r} uses something that is not allowed (only arithmetic, comparisons and "
                        f"{', '.join(sorted(_FUNCS))})")

    out = ev(tree)
    if isinstance(out, float) and not math.isfinite(out):
        raise SpecError(f"{expr!r} is not a finite number")
    return out


def _resolve_value(v: Any, env: dict[str, float]) -> Any:
    """Walk a literal/expression tree: strings starting with '=' are evaluated, containers recurse."""
    if isinstance(v, str) and v.startswith("="):
        r = evaluate(v[1:], env)
        return float(r) if not isinstance(r, bool) else r
    if isinstance(v, list):
        return [_resolve_value(x, env) for x in v]
    if isinstance(v, dict):
        return {k: _resolve_value(x, env) for k, x in v.items()}
    return v


def _eval_derived(items: Any, env: dict[str, float], errors: list[str], prefix: str = "") -> None:
    """Evaluate derived values in dependency order, whatever order the dict arrives in. A spec saved in a database comes
    back with its keys sorted, so the order written in the file cannot be relied on. A value waits until the names it uses
    exist; anything still waiting when nothing more can be resolved is reported (unknown name or a cycle)."""
    pending: dict[str, str] = {}
    for k, expr in (items or {}).items():
        if not isinstance(expr, str) or not expr.startswith("="):
            errors.append(f"{prefix}derived {k!r} must be an expression starting with '='")
        elif k in env:
            errors.append(f"{prefix}derived {k!r} has the same name as an earlier value")
        else:
            pending[k] = expr
    waiting: dict[str, str] = {}
    while pending:
        progressed = False
        for k in list(pending):
            try:
                env[k] = float(evaluate(pending[k][1:], env))
                del pending[k]
                progressed = True
            except SpecError as e:
                if str(e).startswith("unknown name"):
                    waiting[k] = str(e)                      # may resolve once another value exists
                else:
                    errors.append(f"{prefix}derived {k!r}: {e}")
                    del pending[k]
                    progressed = True
        if not progressed:
            break
    for k in pending:
        errors.append(f"{prefix}derived {k!r}: {waiting.get(k, 'could not be resolved')}")


def _param_specs(raw: Any, errors: list[str]) -> dict[str, P]:
    out: dict[str, P] = {}
    for k, v in (raw or {}).items():
        if not isinstance(k, str) or not _NAME.match(k):
            errors.append(f"param name {k!r} must be lowercase snake_case")
            continue
        if isinstance(v, bool) or not (isinstance(v, (int, float, dict))):
            errors.append(f"param {k!r} must be a number or {{default, min, max}}")
            continue
        if isinstance(v, dict):
            out[k] = P(v.get("default"), v.get("min"), v.get("max"), v.get("kind", "float"))
            if v.get("default") is None:
                errors.append(f"param {k!r} needs a default")
        else:
            out[k] = P(float(v))
    return out


_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def load_module(name: str, inline: dict[str, Any] | None, module_dir: Path | None = None) -> dict[str, Any] | None:
    if inline and isinstance(inline.get(name), dict):
        return inline[name]
    if not _MODULE_NAME.match(name):
        return None
    f = (module_dir or MODULE_DIR) / f"{name}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except (OSError, ValueError):
        return None


def list_modules(module_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """{name: {"description", "params"}} for every module in the library folder."""
    out = {}
    for f in sorted((module_dir or MODULE_DIR).glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out[f.stem] = {"description": d.get("description", ""), "params": d.get("params", {})}
    return out


def _flatten(components: list[Any], env: dict[str, float], errors: list[str], inline: Any,
             module_dir: Path | None) -> tuple[list[Any], list[dict[str, Any]]]:
    """Replace module instances with their components (ids prefixed, scope and offset attached)."""
    out: list[Any] = []
    mod_checks: list[dict[str, Any]] = []
    for ci, comp in enumerate(components):
        if not (isinstance(comp, dict) and "module" in comp):
            out.append(comp)
            continue
        iid = comp.get("id")
        label = f"module instance {iid!r}" if isinstance(iid, str) else f"components[{ci}]"
        if not isinstance(iid, str) or not _NAME.match(iid):
            errors.append(f"{label}: needs an 'id' in lowercase snake_case")
            continue
        bad = [k for k in ("repeat", "mirror", "rotation", "mate", "type", "source") if k in comp]
        if bad:
            errors.append(f"{label}: modules are translated only; {', '.join(bad)} is not supported (list instances instead)")
            continue
        mdef = load_module(str(comp["module"]), inline if isinstance(inline, dict) else None, module_dir)
        if mdef is None:
            errors.append(f"{label}: unknown module {comp['module']!r}")
            continue
        perrs: list[str] = []
        mspecs = _param_specs(mdef.get("params"), perrs)
        try:
            given = {k: _resolve_value(v, env) for k, v in (comp.get("params") or {}).items()}
            offset = _resolve_value(comp.get("position", [0, 0, 0]), env)
        except SpecError as e:
            errors.append(f"{label}: {e}")
            continue
        unknown = [k for k in given if k not in mspecs]
        if unknown:
            perrs.append(f"unknown module param(s): {', '.join(unknown)}")
        if not (isinstance(offset, list) and len(offset) == 3):
            perrs.append("position must be [x, y, z]")
        vals, e2 = resolve({k: v for k, v in given.items() if k in mspecs}, mspecs)
        perrs += e2
        scope = {k: v for k, v in vals.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        _eval_derived(mdef.get("derived"), scope, perrs)
        if perrs:
            errors += [f"{label}: {e}" for e in perrs]
            continue
        local_ids = {c.get("id") for c in mdef.get("components", []) if isinstance(c, dict)}
        for mc in mdef.get("components", []):
            if not isinstance(mc, dict) or not isinstance(mc.get("id"), str) or "module" in mc:
                errors.append(f"{label}: module {comp['module']!r} has a bad component (needs a plain id; no nesting)")
                continue
            c2 = dict(mc)
            c2["id"] = f"{iid}_{mc['id']}"
            c2["assembly"] = comp.get("assembly", mc.get("assembly", "main"))
            c2["_scope"], c2["_offset"] = scope, [float(x) for x in offset]
            if isinstance(c2.get("mate"), dict) and isinstance(c2["mate"].get("to"), str):
                to = c2["mate"]["to"]
                for lid in local_ids:
                    if isinstance(lid, str) and (to == lid or to.startswith(lid + "_")):
                        c2["mate"] = {**c2["mate"], "to": f"{iid}_{to}"}
                        break
            out.append(c2)
        for ch in mdef.get("checks") or []:
            if isinstance(ch, dict):
                mod_checks.append({**ch, "name": f"{iid}:{ch.get('name', 'unnamed')}", "_scope": scope})
    return out, mod_checks


def expand_vehicle(spec: Any, overrides: dict[str, Any] | None = None,
                   catalog: dict[str, dict[str, Any]] | None = None,
                   module_dir: Path | None = None) -> dict[str, Any]:
    """Expand a vehicle spec. Never raises for a bad spec: returns ok=False with every error found."""
    errors: list[str] = []
    warnings: list[str] = []
    empty = {"ok": False, "errors": errors, "warnings": warnings, "params": {}, "parts": [], "external": [],
             "assemblies": {}, "checks": []}
    if not isinstance(spec, dict) or not isinstance(spec.get("components"), list) or not spec["components"]:
        errors.append("spec must be an object with a non-empty 'components' list")
        return empty
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        errors.append("spec needs a 'name'")

    # --- named parameters (bounded, overridable) ------------------------------------------------------
    pspecs = _param_specs(spec.get("params"), errors)
    for k in (overrides or {}):
        if k not in pspecs:
            errors.append(f"override {k!r} is not a declared param")
    given = {k: v for k, v in (overrides or {}).items() if k in pspecs}
    values, perrs = resolve(given, pspecs)
    errors += perrs
    env: dict[str, float] = {k: v for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

    # --- derived values, in order ---------------------------------------------------------------------
    _eval_derived(spec.get("derived"), env, errors)

    # --- components -----------------------------------------------------------------------------------
    parts: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    assemblies: dict[str, list[str]] = {}
    fixed: dict[str, str] = {}
    seen: set[str] = set()
    cat_types = catalog if catalog is not None else _default_catalog()

    components, mod_checks = _flatten(spec["components"], env, errors, spec.get("modules"), module_dir)
    for ci, comp in enumerate(components):
        label = f"components[{ci}]"
        if not isinstance(comp, dict) or not isinstance(comp.get("id"), str) or not _NAME.match(comp["id"]):
            errors.append(f"{label}: needs an 'id' in lowercase snake_case")
            continue
        cid = comp["id"]
        label = f"component {cid!r}"
        asm = comp.get("assembly", "main")
        if not isinstance(asm, str) or not _NAME.match(asm):
            errors.append(f"{label}: assembly must be lowercase snake_case")
            continue

        rep = comp.get("repeat")
        n = 1
        if rep is not None:
            try:
                n = int(_resolve_value(rep.get("n"), env)) if isinstance(rep, dict) else 0
            except (SpecError, TypeError, ValueError) as e:
                errors.append(f"{label}: repeat.n: {e}")
                continue
            if n < 1:
                errors.append(f"{label}: repeat needs {{n >= 1, step: [dx, dy, dz]}}")
                continue
        if n > MAX_EXPANDED or len(parts) + len(external) + n > MAX_EXPANDED:
            errors.append(f"{label}: expands to too many parts (guard is {MAX_EXPANDED}); check repeat.n")
            continue
        mirror = comp.get("mirror")
        if mirror not in (None, "y"):
            errors.append(f"{label}: mirror must be \"y\" or omitted")
            continue

        for i in range(n):
            local = {**(comp["_scope"] if "_scope" in comp else env), "i": float(i), "n": float(n)}
            try:
                step = _resolve_value(rep.get("step", [0, 0, 0]), local) if rep else [0, 0, 0]
                pos = _resolve_value(comp.get("position", [0, 0, 0]), local)
                rot = _resolve_value(comp.get("rotation", [0, 0, 0]), local)
                params = _resolve_value(comp.get("parameters", {}), local)
            except SpecError as e:
                errors.append(f"{label}: {e}")
                break
            if "between" in comp:
                if rep or "position" in comp or "rotation" in comp or "mate" in comp or "source" in comp:
                    errors.append(f"{label}: 'between' cannot be combined with repeat, position, rotation, mate or source")
                    break
                try:
                    ends = _resolve_value(comp["between"], local)
                    a, b = ends
                    d = [float(b[k]) - float(a[k]) for k in range(3)]
                except (SpecError, TypeError, ValueError, IndexError):
                    errors.append(f"{label}: between must be [[x,y,z],[x,y,z]]")
                    break
                length = math.sqrt(sum(c * c for c in d))
                if length < 1e-9 or "length_mm" in (params if isinstance(params, dict) else {}):
                    errors.append(f"{label}: between needs two different points and no length_mm of its own")
                    break
                if abs(d[1]) < 1e-9:
                    rot = [0.0, math.degrees(math.atan2(d[0], d[2])), 0.0]
                elif abs(d[0]) < 1e-9 and abs(d[2]) < 1e-9:
                    rot = [-90.0 if d[1] > 0 else 90.0, 0.0, 0.0]
                else:
                    errors.append(f"{label}: between supports tubes in the X-Z plane or along Y only")
                    break
                if comp.get("origin", "center") not in ("center", "base"):
                    errors.append(f"{label}: origin must be 'center' or 'base'")
                    break
                pos = [float(c) for c in (a if comp.get("origin") == "base" else [(float(a[k]) + float(b[k])) / 2 for k in range(3)])]
                params = {**(params if isinstance(params, dict) else {}), "length_mm": round(length, 4)}
            if not (isinstance(step, list) and len(step) == 3 and isinstance(pos, list) and len(pos) == 3
                    and isinstance(rot, list) and len(rot) == 3):
                errors.append(f"{label}: position, rotation and repeat.step must each be [x, y, z]")
                break
            off = comp.get("_offset", [0.0, 0.0, 0.0])
            base_pos = [float(pos[k]) + i * float(step[k]) + off[k] for k in range(3)]
            sides = [("", base_pos)] if mirror is None else [("_l", base_pos), ("_r", [base_pos[0], -base_pos[1], base_pos[2]])]
            for side_suffix, p3 in sides:
                mate = None
                if "mate" in comp:
                    if "position" in comp:
                        errors.append(f"{label}: give a position or a mate, not both")
                        break
                    if "source" in comp:
                        errors.append(f"{label}: external parts are placed by position, not by mate")
                        break
                    try:
                        mate = _resolve_value(comp["mate"], local)
                    except SpecError as e:
                        errors.append(f"{label}: mate: {e}")
                        break
                    if rep and any(abs(float(c)) > 1e-12 for c in step):
                        errors.append(f"{label}: repeat.step has no effect on a mated part; use {{i}} in mate.offset instead")
                        break
                    if isinstance(mate, dict) and isinstance(mate.get("to"), str):
                        mate["to"] = mate["to"].replace("{i}", str(i + 1)).replace("{side}", side_suffix)
                name = cid + (f"_{i + 1}" if rep else "") + side_suffix
                if name in seen:
                    errors.append(f"{label}: expanded id {name!r} is used twice")
                    continue
                seen.add(name)
                if "source" in comp:
                    external.append({"name": name, "source": comp["source"], "assembly": asm,
                                     "position": [round(c, 4) for c in p3], "rotation": rot})
                else:
                    parts.append({"name": name, "part_type": comp.get("type"), "parameters": params,
                                  **({"mate": mate} if mate is not None else {"position": [round(c, 4) for c in p3]}),
                                  "rotation": rot,
                                  **({"material": comp["material"]} if comp.get("material") else {})})
                assemblies.setdefault(asm, []).append(name)
                if comp.get("fixed"):
                    fixed.setdefault(asm, name)
        if "source" not in comp and comp.get("type") not in cat_types:
            errors.append(f"{label}: type {comp.get('type')!r} is not in the part catalog")

    # --- hand the parts to the existing validator ------------------------------------------------------
    if parts and not errors:
        from assembly_planner import validate_spec
        clean, verrs = validate_spec({"parts": parts}, cat_types)
        errors += [f"planner: {e}" for e in verrs]
        if not verrs:
            by = {p["name"]: p for p in clean}
            parts = [{**by[p["name"]], **({"material": p["material"]} if "material" in p else {})} for p in parts]
            if any(p.get("mate") for p in parts):
                warnings.append("some parts are placed by mates: their positions are only known after the mates are "
                                "solved against real geometry (vehicle_build does this)")

    # --- design checks (arithmetic on the parameters, nothing about geometry) --------------------------
    checks: list[dict[str, Any]] = []
    for ch in list(spec.get("checks") or []) + mod_checks:
        nm = ch.get("name", "unnamed") if isinstance(ch, dict) else "unnamed"
        expr = ch.get("expr") if isinstance(ch, dict) else None
        if not isinstance(expr, str) or not expr.startswith("="):
            checks.append({"name": nm, "status": "error", "detail": "expr must start with '='"})
            continue
        try:
            ok = evaluate(expr[1:], ch["_scope"] if "_scope" in ch else env)
            checks.append({"name": nm, "status": "pass" if ok else "fail",
                           "detail": "" if ok else ch.get("message", f"{expr[1:]} is false")})
        except SpecError as e:
            checks.append({"name": nm, "status": "error", "detail": str(e)})
    if any(c["status"] != "pass" for c in checks):
        warnings.append("one or more design checks did not pass")

    asm_decl = spec.get("assemblies") or {}
    for a in asm_decl:
        if a not in assemblies:
            errors.append(f"assemblies.{a}: no component belongs to it")

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "params": {**{k: env[k] for k in env}}, "parts": parts, "external": external,
            "assemblies": assemblies, "fixed": fixed, "checks": checks,
            "explode": spec.get("explode") if isinstance(spec.get("explode"), dict) else {},
            "limits": ["Arithmetic checks only: no geometry has been built or measured.",
                       "Mirror flips position only; rotation is yours to set."]}


def _default_catalog() -> dict[str, dict[str, Any]]:
    from assembly_planner import default_catalog
    return default_catalog()

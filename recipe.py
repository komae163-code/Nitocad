"""
Recipe export: the spec JSON plus a CadQuery script, next to the STEP file, in one zip.

    <job>_recipe.zip
        part.step    the solid, exactly as the STEP export builds it
        spec.json    part_type, material and the resolved parameters the part was built from
        build.py     a CadQuery script that rebuilds it and writes part.step and part.stl
        README.txt   how to run it

The script is SELF-CONTAINED when it can be: the template's own source and the fillet/chamfer helpers are
copied into it, so it needs only `pip install cadquery`. A few templates lean on other parts of this
project (standards tables, the building and PCB packages). For those the script is written in PACKAGE mode
instead: it imports `cad_templates` and needs this project on the path. The README says which one you got.

What a recipe cannot be, it refuses with a plain message and builds nothing: an attached mesh or STEP
solid (there is no recipe for a file someone else made) and mated assemblies, whose positions are solved
at build time. Assemblies from a parts list are supported; they carry the same normalized list the
generator builds from.

Nothing in the script is executed here. The text is built from stored parameters and the template source;
parameters are embedded as JSON and read back with json.loads, never pasted in as code.
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

FORMAT_TAG = "nitocad-recipe/1"
UNSUPPORTED_TYPES = {"imported"}
_ALLOWED_ROOTS = set(sys.stdlib_module_names) | {"cadquery"}


class RecipeUnsupported(Exception):
    """This part has no recipe. The message is written for the person."""


# ------------------------------------------------------------------ inlining ---

def _read_module(path: Path) -> dict | None:
    """Split a template module into plain imports, the helper names it takes from _safe_ops, and its body.
    None when the module needs anything beyond the standard library, cadquery and _safe_ops."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: list[str] = []
    body: list[str] = []
    defined: set[str] = set()
    for i, node in enumerate(tree.body):
        seg = ast.get_source_segment(src, node) or ""
        if i == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str):
            continue  # module docstring
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] not in _ALLOWED_ROOTS for a in node.names):
                return None
            imports.append(seg)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module == "__future__":
                continue
            if node.level == 1 and node.module == "_safe_ops":
                continue  # the helpers are copied in once, ahead of every template
            if node.level != 0 or (node.module or "").split(".")[0] not in _ALLOWED_ROOTS:
                return None
            imports.append(seg)
        else:
            if getattr(node, "decorator_list", None):
                return None  # a decorated definition's source segment omits the decorator; copy it exactly or not at all
            body.append(seg)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return {"imports": imports, "body": "\n\n".join(body), "defined": defined}


def _inline_bundle(funcs: dict[str, Any]) -> dict | None:
    """Everything the script needs to define `funcs` ({part_type: template function}) with no project imports.
    None when any of it cannot be copied faithfully."""
    paths: dict[Path, None] = {}
    for fn in funcs.values():
        src = inspect.getsourcefile(fn)
        if not src:
            return None
        paths[Path(src)] = None
    first = next(iter(paths))
    safe_path = first.with_name("_safe_ops.py")
    if not safe_path.exists():
        return None
    parts = [("_safe_ops", _read_module(safe_path))]
    parts += [(p.stem, _read_module(p)) for p in paths]
    if any(m is None for _, m in parts):
        return None
    seen: set[str] = set()
    for _, m in parts:
        if seen & m["defined"]:
            return None  # two modules define the same name; copying both would change behaviour
        seen |= m["defined"]
    imports = ["import json", "import cadquery as cq"]
    for _, m in parts:
        for line in m["imports"]:
            if line not in imports:
                imports.append(line)
    return {"imports": imports, "bodies": [(name, m["body"]) for name, m in parts]}


# -------------------------------------------------------------------- script ---

_OPERATIONS = '''
def apply_operations(workplane, operations):
    """The same fillet, chamfer and shell steps the generator applies after the template."""
    for op in operations or []:
        kind = op.get("type")
        if kind == "fillet":
            edges = op.get("edges", "all")
            workplane = safe_fillet(workplane, op.get("radius", 1.0), None if edges == "all" else edges)
        elif kind == "chamfer":
            edges = op.get("edges", "all")
            workplane = safe_chamfer(workplane, op.get("size", 1.0), None if edges == "all" else edges)
        elif kind == "shell":
            thickness = op.get("thickness", 2.0)
            if op.get("open_top", True):
                workplane = workplane.faces(">Z").shell(-thickness)
            else:
                workplane = workplane.shell(-thickness)
    return workplane
'''

_SINGLE_MAIN = '''
def build():
    result = TEMPLATES[PART_TYPE](PARAMS)
    return apply_operations(result, PARAMS.get("operations"))


if __name__ == "__main__":
    result = build()
    cq.exporters.export(result, "part.step")
    cq.exporters.export(result, "part.stl")
    print("wrote part.step and part.stl")
'''

_ASSEMBLY_MAIN = '''
def build():
    assy = cq.Assembly()
    for spec in PARTS:
        part = apply_operations(TEMPLATES[spec["part_type"]](spec["parameters"]), spec["parameters"].get("operations"))
        rx, ry, rz = spec["rotation"]
        loc = (cq.Location(cq.Vector(*spec["position"]), cq.Vector(1, 0, 0), rx)
               * cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 1, 0), ry)
               * cq.Location(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), rz))
        assy.add(part, name=spec["name"], loc=loc)
    return assy


if __name__ == "__main__":
    assy = build()
    assy.save("part.step")
    cq.exporters.export(cq.Workplane(obj=assy.toCompound()), "part.stl")
    print("wrote part.step and part.stl")
'''


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=list)


def _script(job_id: str, mode: str, part_type: str, data: Any, bundle: dict | None, funcs: dict[str, Any], is_assembly: bool) -> str:
    head = [
        f'"""Nitocad recipe for job {job_id}: rebuilds the part and writes part.step and part.stl.',
        "",
        ("Self-contained. Needs only: pip install cadquery" if bundle else
         "Package mode. Run it with the Nitocad project on the Python path (it imports cad_templates)."),
        'Run: python build.py"""',
    ]
    if bundle:
        out = head + [""] + bundle["imports"] + [""]
        for name, body in bundle["bodies"]:
            out += [f"# ---- {name}", body, ""]
        out.append("TEMPLATES = {" + ", ".join(f"{k!r}: {fn.__name__}" for k, fn in sorted(funcs.items())) + "}")
    else:
        out = head + ["", "import json", "import cadquery as cq", "from cad_templates import TEMPLATES",
                      "from cad_templates._safe_ops import safe_chamfer, safe_fillet"]
    out.append("")
    if is_assembly:
        out.append(f"PARTS = json.loads({_dumps(data)!r})")
    else:
        out += [f"PART_TYPE = {part_type!r}", f"PARAMS = json.loads({_dumps(data)!r})"]
    out.append(_OPERATIONS)
    out.append(_ASSEMBLY_MAIN if is_assembly else _SINGLE_MAIN)
    return "\n".join(out).rstrip() + "\n"


def _readme(job_id: str, mode: str, part_type: str) -> str:
    how = ("build.py is self-contained. Install CadQuery (pip install cadquery) and run: python build.py"
           if mode == "self-contained" else
           "build.py is in package mode: it imports cad_templates, so run it with the Nitocad project on the Python path.")
    return (f"Nitocad recipe for job {job_id} ({part_type})\n\n"
            "part.step   the solid as exported by Nitocad\n"
            "spec.json   the part type, material and resolved parameters (millimetres)\n"
            "build.py    rebuilds the part and writes part.step and part.stl in the current folder\n\n"
            f"{how}\n\n"
            "Change a number in spec.json's parameters (and the PARAMS line in build.py) to make a variant.\n")


# --------------------------------------------------------------------- public ---

def build_recipe(job: dict, templates: dict[str, Any] | None = None) -> dict[str, Any]:
    """The recipe for a stored job: {"spec": dict, "script": str, "mode": "self-contained"|"package", "readme": str}.
    Raises RecipeUnsupported with a message the person can read."""
    part_type = job.get("part_type")
    if part_type in UNSUPPORTED_TYPES:
        raise RecipeUnsupported("A recipe needs a part Nitocad built from parameters. An attached file has none.")
    if templates is None:
        from cad_templates import TEMPLATES as templates  # noqa: N811 - imported late so tests can pass their own
    try:
        stored = json.loads(job["parameters"]) if job.get("parameters") else {}
    except ValueError as exc:
        raise RecipeUnsupported("The stored parameters for this part could not be read.") from exc
    if not isinstance(stored, dict):
        raise RecipeUnsupported("The stored parameters for this part could not be read.")
    job_id = str(job.get("id") or "job")

    is_assembly = part_type == "assembly"
    if is_assembly:
        if stored.get("assembly"):
            raise RecipeUnsupported("Mated assemblies are solved when they are built, so they have no recipe yet. "
                                    "Assemblies made from a parts list do.")
        import assembly as assembly_mod
        try:
            data = assembly_mod.normalize_assembly_spec(stored.get("parts"))
        except ValueError as exc:
            raise RecipeUnsupported(f"The stored assembly cannot be written as a recipe: {exc}.") from exc
        missing = sorted({p["part_type"] for p in data if p["part_type"] not in templates})
        if missing:
            raise RecipeUnsupported(f"No template for {', '.join(missing)}, so this assembly has no recipe.")
        types = sorted({p["part_type"] for p in data})
        spec = {"format": FORMAT_TAG, "job_id": job_id, "part_type": "assembly", "units": "mm", "parts": data}
    else:
        if part_type not in templates:
            raise RecipeUnsupported(f"There is no recipe for {part_type!r}.")
        data = stored
        types = [part_type]
        spec = {"format": FORMAT_TAG, "job_id": job_id, "part_type": part_type, "material": job.get("material"),
                "units": "mm", "parameters": stored}

    funcs = {t: templates[t] for t in types}
    try:
        bundle = _inline_bundle(funcs)
    except (OSError, SyntaxError, TypeError):
        bundle = None
    mode = "self-contained" if bundle else "package"
    script = _script(job_id, mode, part_type, data, bundle, funcs, is_assembly)
    compile(script, "build.py", "exec")  # a script that does not parse is a bug here, not the person's problem
    return {"spec": spec, "script": script, "mode": mode, "readme": _readme(job_id, mode, part_type)}


def write_zip(out_path: Path, recipe: dict, step_path: Path) -> Path:
    """Write the recipe zip. The STEP file is copied in as part.step."""
    tmp = out_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(step_path, "part.step")
        z.writestr("spec.json", json.dumps(recipe["spec"], indent=2, sort_keys=True, default=list) + "\n")
        z.writestr("build.py", recipe["script"])
        z.writestr("README.txt", recipe["readme"])
    tmp.replace(out_path)
    return out_path

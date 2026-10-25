"""Recipe export: spec JSON, CadQuery script, STEP in one zip. No CadQuery needed: the script is only compiled."""
import ast
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

try:
    import cadquery  # noqa: F401
except Exception:  # noqa: BLE001 - the script text is checked, never run, so a stand-in is enough
    sys.modules["cadquery"] = mock.MagicMock()

import recipe  # noqa: E402
from cad_templates import TEMPLATES  # noqa: E402

BRACKET = {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 4, "hole_count": 2,
           "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.5}


def job(part_type, params, material="6061", jid="job123"):
    return {"id": jid, "part_type": part_type, "material": material, "parameters": json.dumps(params), "success": True}


def params_in_script(script, name="PARAMS"):
    tree = ast.parse(script)
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == name:
            return json.loads(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"{name} not found")


def test_a_bracket_recipe_is_self_contained_and_compiles():
    r = recipe.build_recipe(job("l_bracket", BRACKET), TEMPLATES)
    assert r["mode"] == "self-contained"
    compile(r["script"], "build.py", "exec")
    assert "from cad_templates" not in r["script"] and "from ._safe_ops" not in r["script"]
    assert "def generate_l_bracket" in r["script"] and "def safe_fillet" in r["script"]
    assert "pip install cadquery" in r["script"]


def test_parameters_are_embedded_as_data_and_round_trip_exactly():
    r = recipe.build_recipe(job("l_bracket", BRACKET), TEMPLATES)
    assert params_in_script(r["script"]) == BRACKET
    assert r["spec"]["parameters"] == BRACKET and r["spec"]["material"] == "6061" and r["spec"]["units"] == "mm"
    assert r["spec"]["format"] == recipe.FORMAT_TAG


def test_hostile_text_in_parameters_cannot_become_code():
    evil = dict(BRACKET, note="'''; import os; os.system('x') #")
    r = recipe.build_recipe(job("l_bracket", evil), TEMPLATES)
    tree = ast.parse(r["script"])
    assert not any(isinstance(n, ast.Import) and any(a.name == "os" for a in n.names) for n in ast.walk(tree))
    assert params_in_script(r["script"])["note"] == evil["note"]


def test_a_template_that_needs_other_project_modules_falls_back_to_package_mode():
    r = recipe.build_recipe(job("hex_bolt", {"size": "M8", "length_mm": 40}), TEMPLATES)
    assert r["mode"] == "package"
    compile(r["script"], "build.py", "exec")
    assert "from cad_templates import TEMPLATES" in r["script"]
    assert "Package mode" in r["script"] and "package mode" in r["readme"]


def test_operations_are_applied_by_the_script_like_the_generator_does():
    r = recipe.build_recipe(job("l_bracket", dict(BRACKET, operations=[{"type": "chamfer", "size": 1.0}])), TEMPLATES)
    assert "def apply_operations" in r["script"] and 'PARAMS.get("operations")' in r["script"]
    assert params_in_script(r["script"])["operations"] == [{"type": "chamfer", "size": 1.0}]


def test_an_assembly_from_a_parts_list_carries_the_normalized_list():
    parts = [{"name": "shaft", "part_type": "shaft", "parameters": {"diameter_mm": 8, "length_mm": 60}, "position": [0, 0, 0]},
             {"name": "gear", "part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 8.2},
              "position": [0, 0, 20]}]
    r = recipe.build_recipe(job("assembly", {"parts": parts, "layout": "custom"}), TEMPLATES)
    compile(r["script"], "build.py", "exec")
    listed = params_in_script(r["script"], "PARTS")
    assert [p["name"] for p in listed] == ["shaft", "gear"] and listed[1]["position"] == [0.0, 0.0, 20.0]
    assert listed[0]["rotation"] == [0.0, 0.0, 0.0]
    assert r["spec"]["part_type"] == "assembly" and "assy.save" in r["script"]


def test_mated_assemblies_and_attached_files_are_refused_in_plain_words():
    for j, word in ((job("assembly", {"assembly": {"parts": []}}), "Mated"), (job("imported", {"kind": "stl"}), "attached file")):
        try:
            recipe.build_recipe(j, TEMPLATES)
        except recipe.RecipeUnsupported as exc:
            assert word in str(exc)
        else:
            raise AssertionError("expected RecipeUnsupported")


def test_unknown_part_types_and_bad_stored_parameters_are_refused():
    for j in (job("teapot", {}), {"id": "x", "part_type": "l_bracket", "parameters": "{not json"}):
        try:
            recipe.build_recipe(j, TEMPLATES)
        except recipe.RecipeUnsupported:
            pass
        else:
            raise AssertionError("expected RecipeUnsupported")


def test_the_zip_holds_step_spec_script_and_readme():
    r = recipe.build_recipe(job("l_bracket", BRACKET), TEMPLATES)
    d = Path(tempfile.mkdtemp())
    step = d / "x.step"
    step.write_bytes(b"ISO-10303-21;")
    out = recipe.write_zip(d / "job123_recipe.zip", r, step)
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["README.txt", "build.py", "part.step", "spec.json"]
        assert z.read("part.step") == b"ISO-10303-21;"
        assert json.loads(z.read("spec.json"))["parameters"] == BRACKET
        compile(z.read("build.py").decode(), "build.py", "exec")
    assert not list(d.glob("*.tmp"))


def test_the_same_job_always_gives_the_same_script():
    a = recipe.build_recipe(job("l_bracket", BRACKET), TEMPLATES)["script"]
    b = recipe.build_recipe(job("l_bracket", dict(reversed(list(BRACKET.items())))), TEMPLATES)["script"]
    assert a == b


def _run(script):
    """Load the script with the stand-in CadQuery and call build(): catches names the copy forgot to bring along."""
    ns = {"__name__": "recipe_check"}
    exec(compile(script, "build.py", "exec"), ns)  # noqa: S102 - our own generated text, stand-in CadQuery
    return ns["build"]()


def test_the_generated_scripts_load_and_build_with_every_name_defined():
    _run(recipe.build_recipe(job("l_bracket", dict(BRACKET, operations=[{"type": "fillet", "radius": 0.5}])), TEMPLATES)["script"])
    _run(recipe.build_recipe(job("motor_mount", {"motor_size_mm": 50, "thickness_mm": 3}), TEMPLATES)["script"])
    parts = [{"name": "shaft", "part_type": "shaft", "parameters": {"diameter_mm": 8, "length_mm": 60}},
             {"name": "gear", "part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 8.2},
              "position": [0, 0, 20]}]
    _run(recipe.build_recipe(job("assembly", {"parts": parts}), TEMPLATES)["script"])

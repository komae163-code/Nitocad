"""Model-written CadQuery code: validator, worker logic and the generate / repair loop.

No CadQuery, no network, no real model. The worker's run_build is exercised against a tiny stand-in for the
cadquery module, so these tests prove the guard rails and the control flow. They do NOT prove the sandbox process
limits or that real geometry builds: run tests/test_codegen_kernel.py (skipped without CadQuery) for that.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codegen_check as CC
import codegen_llm as CL
import codegen_worker as CW

GOOD = '''import cadquery as cq
import math

PARAMS = {
    "length": {"default": 120, "min": 40, "max": 400, "label": "Length", "unit": "mm"},
    "wall": {"default": 3, "min": 1, "max": 10},
    "count": {"default": 4, "min": 1, "max": 12, "unit": "count", "int": True},
}

def build(p):
    body = cq.Workplane("XY").box(p["length"], 60, 40)
    return body
'''


def wrap(body: str) -> str:
    return 'import cadquery as cq\nPARAMS = {"a": {"default": 1, "min": 0, "max": 2}}\ndef build(p):\n' + "".join("    " + l + "\n" for l in body.splitlines())


# ------------------------------------------------------------------ validator
def test_good_code_passes_and_params_are_read_without_running_it():
    assert CC.validate(GOOD) == []
    params = CC.parse_params(GOOD)
    assert list(params) == ["length", "wall", "count"]
    assert params["length"] == {"default": 120, "min": 40, "max": 400, "label": "Length", "unit": "mm", "int": False}
    assert params["wall"]["label"] == "Wall" and params["count"]["int"] is True


def test_escape_attempts_are_rejected():
    attacks = {
        "import os": "import os\nos.system('x')",
        "open": "return open('/etc/passwd')",
        "__import__": "return __import__('os')",
        "dunder walk": "return ().__class__.__bases__[0].__subclasses__()",
        "getattr": "return getattr(cq, 'exporters')",
        "exporters": "cq.exporters.export(cq.Workplane('XY').box(1,1,1), 'x.step')",
        "exportStep": "cq.Workplane('XY').box(1,1,1).val().exportStep('x.step')",
        "format string": "return '{0.__class__}'.format(cq)",
        "eval": "return eval('1+1')",
        "exec": "exec('x=1')",
        "type": "return type(cq)",
        "vars": "return vars(cq)",
        "font path": "return cq.Workplane('XY').text('hi', 5, 1, fontPath='/etc/passwd')",
        "path string": "x = '/etc/passwd'",
        "traceback frame": "try:\n    1/0\nexcept Exception as e:\n    return e.__traceback__.tb_frame",
        "frame attr": "return p.gi_frame",
        "import dxf": "return cq.Workplane('XY').importDXF('a.dxf')",
        "save": "return cq.Assembly().save('a.step')",
        "rebind builtins": "__builtins__ = cq\nreturn 1",
    }
    for label, body in attacks.items():
        src = wrap(body) if not body.startswith("import") else body
        assert CC.validate(src), f"{label} was accepted"
    for extra in ("from cadquery import exporters", "from cadquery import *", "import cadquery.occ_impl", "from os import path",
                  "import cadquery as cq\nclass A: pass", "import cadquery as cq\nwith open('x') as f: pass"):
        assert CC.validate(extra + '\nPARAMS = {"a": {"default": 1, "min": 0, "max": 2}}\ndef build(p): return 1\n'), extra


def test_structure_rules():
    assert any("build" in e for e in CC.validate('import cadquery as cq\nPARAMS = {"a": {"default": 1, "min": 0, "max": 2}}\n'))
    assert any("PARAMS" in e for e in CC.validate("import cadquery as cq\ndef build(p): return 1\n"))
    assert CC.validate("def build(p:\n")[0].startswith("Syntax error")
    assert CC.validate("") and CC.validate("x = 1\n" * 20000)
    bad_params = [
        'PARAMS = {"A": {"default": 1, "min": 0, "max": 2}}',                       # not snake case
        'PARAMS = {"a": {"default": 5, "min": 0, "max": 2}}',                       # default out of range
        'PARAMS = {"a": {"default": 1, "min": 2, "max": 2}}',                       # min == max
        'PARAMS = {"a": {"default": "x", "min": 0, "max": 2}}',                     # not a number
        'W = 3\nPARAMS = {"a": {"default": W, "min": 0, "max": 9}}',                 # expression, not literal
        'PARAMS = {}',
    ]
    for p in bad_params:
        assert CC.validate(f"import cadquery as cq\n{p}\ndef build(p): return 1\n"), p


def test_legit_cadquery_idioms_are_not_blocked():
    src = wrap('''path = cq.Workplane("XZ").lineTo(10, 10)
prof = cq.Workplane("XY").circle(2).sweep(path)
holes = [cq.Workplane("XY").cylinder(5, 1) for i in range(int(p["a"]) + 2)]
r = prof.faces(">Z").workplane().hole(1).edges("|Z").fillet(0.1)
return r.union(holes[0]).cut(holes[1])''')
    assert CC.validate(src) == [], CC.validate(src)


# ------------------------------------------------------------------ worker logic with a stand-in kernel
def fake_cq():
    class Shape:
        pass

    class Workplane:
        def __init__(self, *a):
            self._v = []

        def box(self, *a):
            self._v = [Shape()]
            return self

        def vals(self):
            return self._v

    class Compound(Shape):
        @staticmethod
        def makeCompound(items):
            c = Compound()
            c.items = list(items)
            return c

    class Assembly:
        def toCompound(self):
            return Shape()

    m = types.ModuleType("cadquery")
    m.Shape, m.Workplane, m.Compound, m.Assembly = Shape, Workplane, Compound, Assembly
    m.exporters = object()
    m.importers = object()
    return m


def test_worker_builds_with_defaults_and_overrides():
    cq = fake_cq()
    shape, values = CW.run_build(GOOD, {}, cq)
    assert isinstance(shape, cq.Shape) and values == {"length": 120, "wall": 3, "count": 4}
    _, values = CW.run_build(GOOD, {"length": 200, "count": 6.4}, cq)
    assert values["length"] == 200 and values["count"] == 6                       # int params are rounded


def test_worker_rejects_bad_overrides_and_reports_code_errors_with_line_numbers():
    cq = fake_cq()
    for bad in ({"nope": 1}, {"length": 9999}, {"length": "long"}, {"length": True}, {"length": float("nan")}):
        try:
            CW.run_build(GOOD, bad, cq)
            raise AssertionError(bad)
        except CW.CodeError:
            pass
    src = wrap("x = 1\ny = 1 / 0\nreturn cq.Workplane('XY')")
    try:
        CW.run_build(src, {}, cq)
        raise AssertionError("no error")
    except CW.CodeError as e:
        assert str(e).startswith("line 5: ZeroDivisionError") and "/" not in str(e)       # the user's line, and no file paths


def test_worker_return_types():
    cq = fake_cq()
    assert isinstance(CW.to_shape([cq.Workplane().box(), cq.Workplane().box()], cq), cq.Compound)
    assert isinstance(CW.to_shape(cq.Assembly(), cq), cq.Shape)
    for bad in (3, "x", None, cq.Workplane()):
        try:
            CW.to_shape(bad, cq)
            raise AssertionError(bad)
        except CW.CodeError:
            pass


def test_runtime_sandbox_holds_even_if_the_validator_were_bypassed():
    ns = CW.namespace(fake_cq())
    for code in ("import os", "import cadquery.occ_impl", "from cadquery import exporters", "open('x')", "eval('1')", "getattr(1, 'real')"):
        try:
            exec(compile(code, "<t>", "exec"), dict(ns))
            raise AssertionError(code)
        except (ImportError, NameError, AttributeError, TypeError):
            pass
    exec("import cadquery as cq\nimport math\nx = math.sqrt(4)\nprint('hidden')", dict(ns))
    assert not hasattr(CW.safe_cadquery(fake_cq()), "exporters")


# ------------------------------------------------------------------ the generate / repair loop
def reply(code: str, summary: str = "A part.") -> str:
    return json.dumps({"summary": summary, "code": code})


def scripted(replies):
    calls = []

    def llm(messages):
        calls.append([dict(m) for m in messages])
        return replies[min(len(calls) - 1, len(replies) - 1)]
    llm.calls = calls
    return llm


async def runner_ok(code, overrides, exports, library=None):
    runner_ok.n += 1
    return {"ok": True, "stl": b"solid", "stats": {"solids": 1}, "values": {}}


runner_ok.n = 0


def test_loop_returns_code_params_and_result_on_first_try():
    llm = scripted([reply(GOOD)])
    out = asyncio.run(CL.generate("a bar", llm, runner_ok))
    assert out["ok"] and out["attempts"] == 1 and out["code"] == GOOD and out["summary"] == "A part."
    assert list(out["params"]) == ["length", "wall", "count"] and out["result"]["stl"] == b"solid"
    assert llm.calls[0][0]["role"] == "system" and llm.calls[0][1]["content"] == "a bar"


def test_loop_repairs_validation_and_build_errors_then_succeeds():
    seen = []

    async def runner(code, overrides, exports, library=None):
        seen.append(code)
        return {"ok": False, "kind": "code", "error": "line 9: ValueError: no"} if len(seen) == 1 else {"ok": True, "stl": b"x", "stats": {}, "values": {}}
    llm = scripted(["not json at all", reply("import os\n"), reply(GOOD), reply(GOOD)])
    out = asyncio.run(CL.generate("a bar", llm, runner, max_attempts=5))
    assert out["ok"] and out["attempts"] == 4
    feedback = [c[-1]["content"] for c in llm.calls[1:]]
    assert 'JSON object with a "code"' in feedback[0]
    assert "imports are allowed" in feedback[1]
    assert "line 9: ValueError: no" in feedback[2]


def test_loop_gives_up_after_max_attempts_and_reports_the_last_errors():
    async def runner(code, overrides, exports, library=None):
        return {"ok": False, "kind": "code", "error": "The result has no solids."}
    out = asyncio.run(CL.generate("a bar", scripted([reply(GOOD)]), runner, max_attempts=3))
    assert not out["ok"] and out["attempts"] == 3 and out["errors"] == ["The result has no solids."] * 3


def test_loop_infrastructure_errors_are_not_blamed_on_the_model():
    class Busy(Exception):
        pass

    async def runner(code, overrides, exports, library=None):
        raise Busy("busy")
    try:
        asyncio.run(CL.generate("a bar", scripted([reply(GOOD)]), runner))
        raise AssertionError("should propagate")
    except Busy:
        pass

    def broken(messages):
        raise RuntimeError("secret key sk-123 in the message")
    out = asyncio.run(CL.generate("a bar", broken, runner_ok))
    assert not out["ok"] and out.get("infra") and "sk-123" not in json.dumps(out)


def test_revision_sends_the_current_code_and_empty_prompts_are_refused():
    llm = scripted([reply(GOOD)])
    out = asyncio.run(CL.generate("make it longer", llm, runner_ok, previous_code=GOOD))
    assert out["ok"] and GOOD.strip() in llm.calls[0][1]["content"] and "make it longer" in llm.calls[0][1]["content"]
    assert not asyncio.run(CL.generate("   ", llm, runner_ok))["ok"]


def test_reply_parsing_accepts_json_fenced_code_and_rejects_junk():
    assert CL.parse_reply(reply(GOOD))[1] == GOOD
    assert CL.parse_reply("Sure!\n```python\n" + GOOD + "```\n")[1] == GOOD
    assert CL.parse_reply('Here you go: {"summary": "s", "code": "x=1"}')[0] == "s"
    for junk in ("", "   ", "no code here", '{"code": 5}', "{broken"):
        try:
            CL.parse_reply(junk)
            raise AssertionError(junk)
        except ValueError:
            pass


# ------------------------------------------------------------------ library parts
LIB_CODE = '''import cadquery as cq

LIBRARY = {"bearing": {"query": "608 bearing"}}
PARAMS = {"h": {"default": 10, "min": 1, "max": 50}}

def build(p, lib):
    brg = lib["bearing"]
    return brg.translate((0, 0, p["h"]))
'''


def test_library_block_is_validated_and_read_without_running_anything():
    assert CC.validate(LIB_CODE) == []
    assert CC.parse_library(LIB_CODE) == {"bearing": {"query": "608 bearing", "scale": 1.0}}
    assert CC.parse_library(GOOD) == {}
    base = LIB_CODE.replace('LIBRARY = {"bearing": {"query": "608 bearing"}}', "LIBRARY = %s")
    bad = ['{"Bearing": {"query": "608 bearing"}}', '{"b": {"query": "x"}}', '{"b": {"query": "../../etc/passwd"}}',
           '{"b": {"query": "608 bearing", "url": "https://evil"}}', '{"b": {"query": "608 bearing", "scale": 0}}',
           '{"b": {"query": "608 bearing", "scale": True}}', '{"b": "608 bearing"}', "{n: 1}"] + \
          ['{' + ", ".join(f'"p{i}": {{"query": "screw"}}' for i in range(7)) + '}']
    for b in bad:
        assert CC.validate(base % b), b
    assert CC.validate(LIB_CODE.replace("def build(p, lib):", "def build(p):"))                  # LIBRARY without lib argument
    assert CC.validate(GOOD.replace("def build(p):", "def build(p, lib):"))                         # lib argument without LIBRARY


def fake_cq_with_step():
    cq = fake_cq()

    class Vec:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class Box:
        def __init__(self):
            self.center = Vec(10.0, 20.0, 30.0)

    class Solid(cq.Shape):
        moved = None
        scaled = None

        def BoundingBox(self):
            return Box()

        def translate(self, v):
            self.moved = (v.x, v.y, v.z)
            return self

        def scale(self, k):
            self.scaled = k
            return self

        def Solids(self):
            return [self]

    class Loaded:
        def vals(self):
            return [Solid()]

    cq.Vector = Vec
    cq.importers = types.SimpleNamespace(importStep=lambda path: Loaded())
    cq.Workplane.newObject = lambda self, objs: (setattr(self, "_v", list(objs)), self)[1]
    cq.Workplane.translate = lambda self, v: self
    return cq


def test_worker_imports_library_parts_centred_and_passes_them_to_build():
    import tempfile
    cq = fake_cq_with_step()
    work = Path(tempfile.mkdtemp())
    lib = CW.load_library(work, {"bearing": {"file": "lib_bearing.step", "scale": 25.4}}, cq)
    solid = lib["bearing"].vals()[0]
    assert solid.moved == (-10.0, -20.0, -30.0) and solid.scaled == 25.4                           # centred on the origin, then scaled
    shape, values = CW.run_build(LIB_CODE, {"h": 5}, cq, lib)
    assert values == {"h": 5} and isinstance(shape, cq.Shape)
    for bad in ({"b": {"file": "../x.step"}}, {"b": {"file": "a/b.step"}}, {"B": {"file": "x.step"}}):
        try:
            CW.load_library(work, bad, cq)
            raise AssertionError(bad)
        except CW.CodeError:
            pass


def test_loop_resolves_library_parts_then_builds_with_them():
    seen = {}

    class Resolver:
        async def resolve(self, spec):
            seen["spec"] = spec
            return {"ok": True, "parts": {"bearing": {"path": "/cache/b.step", "scale": 1.0}}, "entries": [{"name": "bearing", "licence": "CC-BY-3.0"}]}

    async def runner(code, overrides, exports, library=None):
        seen["library"] = library
        return {"ok": True, "stl": b"x", "stats": {}, "values": {}}
    out = asyncio.run(CL.generate("a bearing block", scripted([reply(LIB_CODE)]), runner, resolver=Resolver()))
    assert out["ok"] and out["library"] == [{"name": "bearing", "licence": "CC-BY-3.0"}]
    assert seen["spec"] == {"bearing": {"query": "608 bearing", "scale": 1.0}} and seen["library"]["bearing"]["path"] == "/cache/b.step"


def test_loop_tells_the_model_when_a_part_is_missing_and_lets_it_model_the_part_itself():
    class Resolver:
        async def resolve(self, spec):
            return {"ok": False, "error": "No library part matches '608 bearing' (for 'bearing'). Model it yourself."}
    llm = scripted([reply(LIB_CODE), reply(GOOD)])
    out = asyncio.run(CL.generate("a bar", llm, runner_ok, resolver=Resolver()))
    assert out["ok"] and out["attempts"] == 2 and out["library"] == []
    assert "No library part matches" in llm.calls[1][-1]["content"]
    no_resolver = scripted([reply(LIB_CODE), reply(GOOD)])
    out = asyncio.run(CL.generate("a bar", no_resolver, runner_ok))
    assert out["ok"] and "not available" in no_resolver.calls[1][-1]["content"]

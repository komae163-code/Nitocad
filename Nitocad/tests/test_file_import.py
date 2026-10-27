"""Orchestration tests for server-side CAD import. A stand-in worker replaces the OpenCascade worker, so these
run without cadquery; they cover validation, limits, isolation, timeouts and clean-up."""
import asyncio
import glob
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import file_import as fi

FAKE = textwrap.dedent('''
    import json, os, sys, time
    from pathlib import Path
    work = Path(sys.argv[1]); job = json.loads((work / "job.json").read_text())
    src = next(work.glob("in.*")).read_text(errors="ignore")
    if "MODE=sleep" in src: time.sleep(60)
    if "MODE=crash" in src: raise RuntimeError("boom with /secret/path")
    if "MODE=fail" in src:
        (work / "result.json").write_text(json.dumps({"ok": False, "error": "The 'fillet' edit failed on this part."})); sys.exit(2)
    if "MODE=big" in src: (work / "out.stl").write_bytes(b"x" * 5000)
    else: (work / "out.stl").write_bytes(b"solid fake\\nendsolid\\n")
    if "step" in job["exports"]: (work / "out.step").write_bytes(b"ISO-10303-21;")
    stats = {"env_keys": sorted(os.environ), "ops": job["ops"], "cwd_is_work": os.getcwd() == str(work)}
    (work / "result.json").write_text(json.dumps({"ok": True, "stats": stats}))
''')


def setup_module(_m=None):
    d = Path(tempfile.mkdtemp())
    (d / "fake_worker.py").write_text(FAKE)
    fi.WORKER = d / "fake_worker.py"
    fi.WORKER_TIMEOUT_S = 20.0
    fi.QUEUE_WAIT_S = 20.0
    fi.MAX_CONCURRENT = 2


def run(coro):
    return asyncio.run(coro)


def step(mode=""):
    return f"ISO-10303-21;\nMODE={mode}\n".encode()


class Up:
    def __init__(self, data):
        self.data, self.pos = data, 0

    async def read(self, n):
        c = self.data[self.pos:self.pos + n]; self.pos += n; return c


def test_format_by_extension_and_unsupported():
    assert fi.format_of("a.STEP") == "step" and fi.format_of("b.igs") == "iges" and fi.format_of("c.brp") == "brep"
    for bad in ("a.stl", "a.exe", "a", ""):
        with pytest.raises(fi.UnsupportedUpload):
            fi.format_of(bad)


def test_content_sniffing_for_each_format():
    assert fi.sniff_cad("step", b"ISO-10303-21;\nHEADER;")
    assert not fi.sniff_cad("step", b"<html>")
    assert fi.sniff_cad("iges", b" " * 72 + b"S      1\n")
    assert not fi.sniff_cad("iges", b"hello")
    assert fi.sniff_cad("brep", b"DBRep_DrawableShape\nCASCADE Topology V1")
    assert not fi.sniff_cad("brep", b"PK\x03\x04")
    assert not fi.sniff_cad("stl", b"solid")


def test_safe_stem_strips_paths_and_odd_characters():
    stem = fi.safe_stem("../../etc/pass wd;rm -rf.step")
    assert stem == "etc_pass_wd_rm_-rf" and "/" not in stem and ".." not in stem
    assert fi.safe_stem("") == "model" and fi.safe_stem("....") == "model"
    assert len(fi.safe_stem("a" * 500 + ".step")) <= 60


def test_read_limited_enforces_size_and_empty():
    assert run(fi.read_limited(Up(b"abc"), 10)) == b"abc"
    with pytest.raises(fi.UploadTooLarge):
        run(fi.read_limited(Up(b"x" * 100), 10))
    with pytest.raises(fi.FileImportError):
        run(fi.read_limited(Up(b""), 10))


def test_valid_ops_are_normalised():
    ops = fi.validate_ops([{"op": "translate", "x": "5", "y": 0, "z": -2}, {"op": "rotate", "axis": "z", "deg": 90},
                           {"op": "scale", "factor": 2}, {"op": "mirror", "plane": "YZ"}, {"op": "center"},
                           {"op": "fillet", "radius": 1.5}, {"op": "chamfer", "length": 0.5}])
    assert ops[0] == {"op": "translate", "x": 5.0, "y": 0.0, "z": -2.0}
    assert ops[1]["about"] == [0.0, 0.0, 0.0]
    assert fi.validate_ops(None) == [] and fi.validate_ops("") == []


def test_invalid_ops_are_rejected():
    bad = [[{"op": "exec", "code": "import os"}], [{"op": "rotate", "axis": "w", "deg": 1}], [{"op": "rotate", "axis": "x", "deg": float("nan")}],
           [{"op": "scale", "factor": 0}], [{"op": "scale", "factor": 1e9}], [{"op": "mirror", "plane": "AB"}], [{"op": "fillet", "radius": -1}],
           [{"op": "translate", "x": float("inf")}], "not a list", [{"nope": 1}], [{"op": "center"}] * 21, [{"op": "rotate", "axis": "x", "deg": 1, "about": [1, 2]}]]
    for b in bad:
        with pytest.raises(fi.FileImportError):
            fi.validate_ops(b)


def test_exports_validation_always_includes_stl():
    assert fi.validate_exports(None) == ["stl"] and fi.validate_exports(["step"]) == ["step", "stl"]
    with pytest.raises(fi.FileImportError):
        fi.validate_exports(["exe"])


def test_normal_run_returns_geometry_and_stats():
    r = run(fi.process_cad(step(), "step", [{"op": "center"}], ["stl"]))
    assert r["stl"].startswith(b"solid") and r["step"] is None
    assert r["stats"]["ops"] == [{"op": "center"}] and r["stats"]["cwd_is_work"]


def test_requested_export_formats_are_returned():
    r = run(fi.process_cad(step(), "step", [], ["step", "stl"]))
    assert r["step"].startswith(b"ISO-10303")


def test_server_secrets_never_reach_the_worker():
    os.environ["NITOCAD_TEST_SECRET"] = "s3cr3t"
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    try:
        keys = run(fi.process_cad(step(), "step", [], ["stl"]))["stats"]["env_keys"]
    finally:
        os.environ.pop("NITOCAD_TEST_SECRET"); os.environ.pop("ANTHROPIC_API_KEY")
    assert "NITOCAD_TEST_SECRET" not in keys and "ANTHROPIC_API_KEY" not in keys
    assert "PATH" in keys and "HOME" in keys


def test_timeout_kills_the_worker_and_cleans_up():
    fi.WORKER_TIMEOUT_S = 1.5
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "cadimp_*")))
    try:
        with pytest.raises(fi.CadTimeout):
            run(fi.process_cad(step("sleep"), "step", [], ["stl"]))
    finally:
        fi.WORKER_TIMEOUT_S = 20.0
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "cadimp_*"))) == before


def test_crash_gives_a_generic_message_without_leaking_internals():
    with pytest.raises(fi.CadProcessingError) as e:
        run(fi.process_cad(step("crash"), "step", [], ["stl"]))
    assert "secret" not in str(e.value) and "boom" not in str(e.value)


def test_worker_reported_failures_reach_the_user():
    with pytest.raises(fi.CadProcessingError) as e:
        run(fi.process_cad(step("fail"), "step", [], ["stl"]))
    assert "fillet" in str(e.value)


def test_oversized_output_is_refused():
    old = fi.MAX_OUTPUT_BYTES; fi.MAX_OUTPUT_BYTES = 1000
    try:
        with pytest.raises(fi.CadProcessingError):
            run(fi.process_cad(step("big"), "step", [], ["stl"]))
    finally:
        fi.MAX_OUTPUT_BYTES = old


def test_temp_directories_are_always_removed():
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "cadimp_*")))
    run(fi.process_cad(step(), "step", [], ["stl"]))
    with pytest.raises(fi.CadProcessingError):
        run(fi.process_cad(step("crash"), "step", [], ["stl"]))
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "cadimp_*"))) == before


def test_overload_returns_503_instead_of_piling_up():
    fi.MAX_CONCURRENT, fi.QUEUE_WAIT_S, fi.WORKER_TIMEOUT_S = 1, 0.5, 3.0

    async def both():
        a = asyncio.create_task(fi.process_cad(step("sleep"), "step", [], ["stl"]))
        await asyncio.sleep(0.3)
        try:
            await fi.process_cad(step(), "step", [], ["stl"])
            second = None
        except fi.ServerBusy as e:
            second = e
        try:
            await a
        except fi.CadTimeout:
            pass
        return second
    try:
        assert isinstance(run(both()), fi.ServerBusy)
    finally:
        fi.MAX_CONCURRENT, fi.QUEUE_WAIT_S, fi.WORKER_TIMEOUT_S = 2, 20.0, 20.0


def test_status_codes_match_the_error_kinds():
    assert (fi.UnsupportedUpload.status_code, fi.UploadTooLarge.status_code, fi.CadProcessingError.status_code, fi.CadTimeout.status_code, fi.ServerBusy.status_code) == (415, 413, 422, 504, 503)


def test_routes_are_registered_and_cors_exposes_the_stats_header():
    src = Path(__file__).resolve().parents[1].joinpath("web_app.py").read_text()
    assert '@app.post("/convert")' in src and '@app.post("/api/files/cad/export")' in src
    assert '"X-NitoCAD-Stats",' in src.split("expose_headers=[")[1].split("]")[0]
    assert "File, Form" in src

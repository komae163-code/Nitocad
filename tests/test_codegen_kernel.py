"""Real-kernel checks for the code-first spike. Skipped (returns early) when CadQuery is not installed.

NOT RUN in the sandbox this was written in: there was no CadQuery. Run these on a machine with the kernel before trusting
the spike: they prove the sandbox actually builds, limits time, and that sliders change real geometry."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cadquery  # noqa: F401
    HAVE_KERNEL = True
except Exception:  # noqa: BLE001
    HAVE_KERNEL = False

import codegen_run as CR

BIN = '''import cadquery as cq

PARAMS = {
    "height":  {"default": 1000, "min": 400, "max": 1500, "label": "Height", "unit": "mm"},
    "base_w":  {"default": 480,  "min": 200, "max": 900,  "label": "Base width"},
    "top_w":   {"default": 580,  "min": 250, "max": 1100, "label": "Top width"},
    "wall":    {"default": 4,    "min": 2,   "max": 12,   "label": "Wall"},
}

def build(p):
    h, bw, tw, t = p["height"], p["base_w"], p["top_w"], p["wall"]
    slope = (tw - bw) / (2 * h)
    outer = cq.Workplane("XY").rect(bw * 1.2, bw).workplane(offset=h).rect(tw * 1.2, tw).loft(combine=True)
    iw0 = bw + 2 * slope * t - 2 * t
    iw1 = bw + 2 * slope * h - 2 * t
    inner = (cq.Workplane("XY").workplane(offset=t).rect(iw0 * 1.2, iw0)
             .workplane(offset=h - t + 10).rect(iw1 * 1.2, iw1).loft(combine=True))
    return outer.cut(inner)
'''


def test_real_build_and_sliders_change_the_geometry():
    if not HAVE_KERNEL:
        return
    a = asyncio.run(CR.run(BIN, {}, ("step",)))
    assert a["ok"], a
    assert a["stats"]["solids"] == 1 and a["stats"]["volume"] > 0 and len(a["stl"]) > 84
    assert a["step"] and b"ISO-10303" in a["step"][:200]
    b = asyncio.run(CR.run(BIN, {"height": 1400, "wall": 8}, ()))
    assert b["ok"] and b["stats"]["size"][2] > a["stats"]["size"][2] and b["stats"]["volume"] != a["stats"]["volume"]


def test_real_limits_and_errors():
    if not HAVE_KERNEL:
        return
    CR.TIMEOUT_S = 4.0
    loop = 'import cadquery as cq\nPARAMS = {"a": {"default": 1, "min": 0, "max": 2}}\ndef build(p):\n    while True:\n        pass\n'
    r = asyncio.run(CR.run(loop, {}, ()))
    assert not r["ok"] and "stopped" in r["error"]
    wires = 'import cadquery as cq\nPARAMS = {"a": {"default": 1, "min": 0, "max": 2}}\ndef build(p):\n    return cq.Workplane("XY").rect(5, 5)\n'
    r = asyncio.run(CR.run(wires, {}, ()))
    assert not r["ok"] and "solid" in r["error"].lower()
    r = asyncio.run(CR.run(BIN, {"wall": 99}, ()))
    assert not r["ok"] and "between" in r["error"]

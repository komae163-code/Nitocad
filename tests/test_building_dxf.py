"""DXF output through ezdxf (already a project dependency). Skipped where ezdxf is not installed."""
import io

import pytest

ezdxf = pytest.importorskip("ezdxf")

from building import design_building  # noqa: E402
from building.sheet import LAYERS, fit, to_dxf  # noqa: E402
from building.sheets import elevation_sheet, plan_sheet  # noqa: E402

HOUSE = {"name": "Two-storey house", "width_mm": 13000, "depth_mm": 10000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "dining"}, {"type": "kitchen"},
               {"type": "bedroom", "name": "Guest bedroom"}, {"type": "toilet"}, {"type": "store"}]},
    {"rooms": [{"type": "master_bedroom"}, {"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}]}


def _read(text):
    return ezdxf.read(io.StringIO(text))


def test_plan_dxf_reads_back_with_layers_units_and_door_arcs():
    model = design_building(HOUSE)["model"]
    sheet = plan_sheet(model, 0)
    doc = _read(to_dxf(sheet))
    assert doc.units == 4                                   # millimetres
    assert set(LAYERS) <= {layer.dxf.name for layer in doc.layers}
    msp = doc.modelspace()
    swings = sum(1 for o in model["levels"][0]["openings"] if o["kind"] == "door" and o["swing"])
    assert len(msp.query("ARC")) == swings
    assert len(msp.query("HATCH")) > 20 and len(msp.query("TEXT")) > 10


def test_dxf_is_at_full_size_and_text_height_follows_the_scale():
    model = design_building(HOUSE)["model"]
    sheet = plan_sheet(model, 0)
    doc = _read(to_dxf(sheet))
    scale = fit(sheet, None)["scale"]
    heights = {round(t.dxf.height, 3) for t in doc.modelspace().query("TEXT")}
    assert round(2.5 * scale, 3) in heights or round(3.0 * scale, 3) in heights
    xs = [e.dxf.start.x for e in doc.modelspace().query("LINE")]
    assert max(xs) > 12000                                    # model millimetres, not paper units


def test_elevation_dxf_contains_glazing_on_its_layer():
    model = design_building(HOUSE)["model"]
    doc = _read(to_dxf(elevation_sheet(model, "south")))
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert "A-GLAZ" in layers and "A-ROOF" in layers and "A-GRND" in layers

"""The template catalogue: every spec is valid as written and uses a part type the engine has."""
import re
from pathlib import Path

import pytest

import studio_templates as st
import validator

ROOT = Path(__file__).resolve().parent.parent


def engine_part_types():
    """Part types registered in cad_templates, read from the source so this runs without CadQuery."""
    src = (ROOT / "cad_templates" / "__init__.py").read_text()
    return set(re.findall(r'^\s*"([a-z_0-9]+)":\s*generate_', src, re.M))


def test_ids_are_unique_and_every_category_is_used():
    ids = [t.id for t in st.TEMPLATES]
    assert len(ids) == len(set(ids))
    assert {t.category for t in st.TEMPLATES} <= {c for c, _ in st.CATEGORIES}


def test_listing_shape_matches_what_the_picker_reads():
    data = st.listing()
    assert {c["id"] for c in data["categories"]} == {t.category for t in st.TEMPLATES}
    for t in data["templates"]:
        assert set(t) == {"id", "title", "category", "category_label", "summary", "tags", "hint", "part_type", "parts"}
        assert isinstance(t["tags"], list) and t["parts"] >= 1
    assert "spec" not in str(data)                      # the build spec stays on the server


def test_every_single_part_validates_with_no_warnings_or_corrections():
    for t in st.TEMPLATES:
        spec = st.spec_of(t)
        if spec["part_type"] == "assembly":
            continue
        params, res = validator.validate_parameters(spec["part_type"], spec["parameters"])
        assert res.is_valid, (t.id, res.errors)
        assert not res.warnings and not res.corrections, (t.id, res.warnings, res.corrections)


def test_every_assembly_member_validates_too():
    for t in st.TEMPLATES:
        spec = st.spec_of(t)
        if spec["part_type"] != "assembly":
            continue
        assert spec["parameters"]["layout"] == "custom" and t.parts >= 2
        for p in spec["parameters"]["parts"]:
            params, res = validator.validate_parameters(p["part_type"], p["parameters"])
            assert res.is_valid, (t.id, p["name"], res.errors)
            if t.category == "vehicles":
                continue                      # a vehicle may carry honest warnings (e.g. the aerospace "preliminary geometry" note)
            assert not res.warnings and not res.corrections, (t.id, p["name"], res.warnings, res.corrections)


def test_part_types_exist_in_the_engine():
    have = engine_part_types()
    assert {"bearing", "gear", "shaft", "l_bracket", "assembly"} - have == {"assembly"}     # parser sanity
    for t in st.TEMPLATES:
        spec = st.spec_of(t)
        types = [p["part_type"] for p in spec["parameters"]["parts"]] if spec["part_type"] == "assembly" else [spec["part_type"]]
        for ty in types:
            assert ty in have, (t.id, ty)


def test_assembly_parts_sit_on_the_shaft_with_clearance():
    """Shafts, gears, pulleys and bearings are all built along Z from the origin, so a part is on the
    shaft when its Z range is inside the shaft and its bore is wider than the shaft."""
    for t in st.TEMPLATES:
        spec = st.spec_of(t)
        if spec["part_type"] != "assembly" or t.category == "vehicles":
            continue                          # vehicles are laid out by their own spec, not on one shaft
        parts = spec["parameters"]["parts"]
        shaft = next(p for p in parts if p["part_type"] == "shaft")
        d, length = shaft["parameters"]["diameter_mm"], shaft["parameters"]["length_mm"]
        placed = []
        for p in parts:
            if p is shaft:
                continue
            q, z = p["parameters"], p["position"][2]
            bore = q.get("bore_diameter_mm", q.get("inner_diameter_mm"))
            height = q.get("thickness_mm", q.get("width_mm"))
            assert bore is not None and bore > d and bore - d <= 0.5, (t.id, p["name"], bore, d)
            assert 0 <= z and z + height <= length, (t.id, p["name"])
            assert p["position"][0] == p["position"][1] == 0
            placed.append((z, z + height))
        placed.sort()
        for (a0, a1), (b0, b1) in zip(placed, placed[1:]):
            assert a1 <= b0, (t.id, "parts overlap along the shaft")
        names = [p["name"] for p in parts]
        assert len(names) == len(set(names))


def test_spec_of_returns_a_copy():
    t = st.get("gear-20")
    s = st.spec_of(t)
    s["parameters"]["teeth"] = 99
    assert t.spec["parameters"]["teeth"] == 20


def test_unknown_template_and_reply_text():
    assert st.get("nope") is None
    r = st.reply_for(st.get("asm-gear-shaft"))
    assert "assembly of 2 parts" in r and "free preview" in r and "—" not in r
    assert "assembly of" not in st.reply_for(st.get("gear-20"))


def test_assemblies_build_through_the_real_parts_list_helper():
    pytest.importorskip("cadquery")
    import assembly
    for t in st.TEMPLATES:
        spec = st.spec_of(t)
        if spec["part_type"] == "assembly":
            out = assembly.assembly_spec_from_parts(spec["parameters"]["parts"], layout="custom")
            assert len(out) == t.parts


def test_vehicle_starters_are_listed_and_keep_their_vehicle_link():
    cat = st.listing()
    assert any(c["id"] == "vehicles" for c in cat["categories"])
    ids = {t.id for t in st.TEMPLATES if t.category == "vehicles"}
    assert {"veh-wheelbarrow", "veh-bicycle", "veh-rc-helicopter"} <= ids
    assert all(st.get(i) is not None for i in ids)                      # the lookup was rebuilt after they were added
    for t in st.TEMPLATES:
        if t.category == "vehicles":
            assert isinstance(t.spec["parameters"].get("vehicle"), dict)         # a chat edit changes the vehicle's own sizes

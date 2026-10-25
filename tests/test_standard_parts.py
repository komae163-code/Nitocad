"""Geometry for standard parts. Needs the CadQuery kernel (skipped where it is not installed)."""
import math

import pytest

cq = pytest.importorskip("cadquery")

from assembly import check_interference  # noqa: E402
from cad_templates import TEMPLATES  # noqa: E402
from standards_data import lookup_flange  # noqa: E402


def solid(part_type, **params):
    return TEMPLATES[part_type](params).val()


def test_registered():
    assert "fastener" in TEMPLATES and "std_bearing" in TEMPLATES


def test_socket_screw_extents():
    bb = solid("fastener", standard="ISO_4762", size="M8", length_mm=30).BoundingBox()
    assert bb.zmin == pytest.approx(-8.0, abs=0.01) and bb.zmax == pytest.approx(30.0, abs=0.01)
    assert bb.xlen == pytest.approx(13.0, abs=0.05)  # head diameter dk


def test_hex_bolt_across_flats():
    bb = solid("fastener", standard="ISO_4017", size="M10", length_mm=40).BoundingBox()
    assert min(bb.xlen, bb.ylen) == pytest.approx(16.0, abs=0.05)
    assert bb.zmin == pytest.approx(-6.4, abs=0.01)


def test_nut_and_washer_dimensions():
    nut = solid("fastener", standard="ISO_4032", size="M8").BoundingBox()
    assert nut.zlen == pytest.approx(6.8, abs=0.01)
    washer = solid("fastener", standard="ISO_7089", size="M8")
    assert washer.BoundingBox().xlen == pytest.approx(16.0, abs=0.01)
    assert washer.Volume() > 0


def test_bearing_matches_catalog():
    s = solid("std_bearing", designation="6205")
    bb = s.BoundingBox()
    assert bb.xlen == pytest.approx(52.0, abs=0.01) and bb.zlen == pytest.approx(15.0, abs=0.01)


def test_bolt_washer_nut_stack_has_no_interference():
    bolt = TEMPLATES["fastener"]({"standard": "ISO_4017", "size": "M8", "length_mm": 30}).val()
    washer = TEMPLATES["fastener"]({"standard": "ISO_7089", "size": "M8"}).val()
    nut = TEMPLATES["fastener"]({"standard": "ISO_4032", "size": "M8"}).val().moved(cq.Location(cq.Vector(0, 0, 20)))
    overlaps, failures = check_interference([("bolt", bolt), ("washer", washer), ("nut", nut)])
    assert overlaps == [] and failures == []


def test_shaft_in_bearing_bore_is_a_clean_fit():
    shaft = TEMPLATES["shaft"]({"diameter_mm": 25, "length_mm": 60}).val()
    bearing = TEMPLATES["std_bearing"]({"designation": "6205"}).val().moved(cq.Location(cq.Vector(0, 0, 20)))
    overlaps, _ = check_interference([("shaft", shaft), ("bearing", bearing)])
    assert overlaps == []


def test_key_form_a_extents_and_round_ends():
    key = solid("key", shaft_diameter_mm=25, length_mm=40)  # 8x7
    bb = key.BoundingBox()
    assert bb.xlen == pytest.approx(8.0, abs=0.01) and bb.zlen == pytest.approx(40.0, abs=0.01)
    assert bb.ymin == pytest.approx(0.0, abs=0.01) and bb.ymax == pytest.approx(7.0, abs=0.01)
    # round ends remove the corners: less volume than the square-ended key
    square = solid("key", shaft_diameter_mm=25, length_mm=40, form="B")
    assert square.Volume() == pytest.approx(8 * 7 * 40, rel=0.001)
    assert key.Volume() < square.Volume()


def test_keyed_shaft_stays_one_solid_and_loses_only_the_pocket():
    plain = TEMPLATES["shaft"]({"diameter_mm": 25, "length_mm": 60})
    keyed = TEMPLATES["shaft"]({"diameter_mm": 25, "length_mm": 60, "keyway_width_mm": 8, "keyway_depth_mm": 4.0})
    assert len(keyed.solids().vals()) == 1  # regression: the old cut sliced the shaft in two
    removed = plain.val().Volume() - keyed.val().Volume()
    assert removed == pytest.approx(8 * 4.0 * 60, rel=0.15)  # pocket, minus the curved-surface sliver


def test_key_seats_in_the_shaft_keyway_without_interference():
    shaft = TEMPLATES["shaft"]({"diameter_mm": 25, "length_mm": 60, "keyway_width_mm": 8, "keyway_depth_mm": 4.0}).val()
    key = TEMPLATES["key"]({"shaft_diameter_mm": 25, "length_mm": 40, "form": "B"}).val().moved(
        cq.Location(cq.Vector(0, 12.5 - 4.0, 10)))
    overlaps, failures = check_interference([("shaft", shaft), ("key", key)])
    assert overlaps == [] and failures == []


def test_o_ring_is_a_torus_of_the_right_size():
    ring = solid("o_ring", inner_diameter_mm=20.0, cross_section_mm=3.53)
    r, big_r = 3.53 / 2, 20.0 / 2 + 3.53 / 2
    assert ring.Volume() == pytest.approx(2 * math.pi ** 2 * big_r * r ** 2, rel=1e-3)  # Pappus
    bb = ring.BoundingBox()
    assert bb.xlen == pytest.approx(20.0 + 2 * 3.53, abs=0.01)
    assert bb.zmin == pytest.approx(0.0, abs=1e-6) and bb.zmax == pytest.approx(3.53, abs=1e-6)


def test_slip_on_flange_volume_and_extents():
    spec = lookup_flange("ASME_B16_5_150", "4", "slip_on")
    body = solid("asme_flange", standard="ASME_B16_5_150", nps="4", flange_type="slip_on")
    t, rf_h = spec["thickness"], spec["raised_face_height"]
    expected = (math.pi / 4) * (spec["od"] ** 2 * t + spec["raised_face_dia"] ** 2 * rf_h
                                - spec["bolt_count"] * spec["bolt_hole"] ** 2 * t
                                - spec["bore"] ** 2 * (t + rf_h))
    assert body.Volume() == pytest.approx(expected, rel=1e-4)
    bb = body.BoundingBox()
    assert bb.xlen == pytest.approx(spec["od"], abs=0.01) and bb.zmax == pytest.approx(t + rf_h, abs=1e-6)


def test_blind_flange_has_no_bore_and_bolt_holes_straddle_the_axes():
    blind = solid("asme_flange", nps="2", flange_type="blind")
    spec = lookup_flange("ASME_B16_5_150", "2", "blind")
    expected = (math.pi / 4) * (spec["od"] ** 2 * spec["thickness"] + spec["raised_face_dia"] ** 2 * 1.6
                                - 4 * spec["bolt_hole"] ** 2 * spec["thickness"])
    assert blind.Volume() == pytest.approx(expected, rel=1e-4)
    # no material removed on the +X axis at the bolt circle radius: no hole sits on a centerline
    probe = cq.Workplane("XY").pushPoints([(spec["bolt_circle"] / 2, 0)]).circle(0.2).extrude(spec["thickness"]).val()
    assert blind.intersect(probe).Volume() > 0.95 * probe.Volume()  # `blind` is already a Shape (solid() returns .val())

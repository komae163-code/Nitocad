"""Sizes the user typed are kept and flagged, never silently changed."""
import validator as v

# The bracket's hole is too big to fit its leg (the limit here is 10 mm: 2 x (10 - 3 mm metal - 2)), and its fillet is too big for
# 3 mm metal. Those are the two corrections these tests are about. (They used to be a 3.2 mm hole in 3 mm metal, which the
# validator no longer changes: a hole does not have to be thinner than the metal.)
BRACKET = {"part_type": "l_bracket", "width_mm": 50, "height_mm": 60, "depth_mm": 40,
           "thickness_mm": 3, "hole_count": 2, "hole_diameter_mm": 12, "fillet_radius_mm": 2.0}
TEXT = "L-bracket 50mm wide, 60mm tall, 40mm deep, 3mm thick, 2 holes per leg"


def test_typed_keys_finds_only_what_the_user_wrote():
    keys = v.typed_keys(TEXT, BRACKET)
    assert {"width_mm", "height_mm", "depth_mm", "thickness_mm", "hole_count"} <= keys
    assert "hole_diameter_mm" not in keys and "fillet_radius_mm" not in keys


def test_typed_keys_understands_other_units():
    assert "width_mm" in v.typed_keys("a plate 5cm wide", {"width_mm": 50.0})
    assert "width_mm" in v.typed_keys("a plate 2 in wide", {"width_mm": 50.8})


def test_defaults_are_still_corrected_and_reported_once():
    params, res = v.validate_parameters("l_bracket", dict(BRACKET), description=TEXT)
    assert params["hole_diameter_mm"] == 10 and params["fillet_radius_mm"] == 1.4
    assert not res.flags
    notes = v.merge_notes(res.warnings, res.corrections)
    assert len(notes) == 2


def test_typed_size_is_kept_and_flagged():
    text = TEXT + ", 12mm holes"
    params, res = v.validate_parameters("l_bracket", dict(BRACKET), description=text)
    assert params["hole_diameter_mm"] == 12
    assert "hole_diameter_mm" not in res.corrections
    assert res.flags["hole_diameter_mm"] == {"value": 12, "suggested": 10}
    assert any("Kept as you typed" in w and "10" in w for w in res.warnings)
    assert not any("Changed hole_diameter_mm" in w for w in res.warnings)


def test_without_a_description_every_correction_applies():
    params, res = v.validate_parameters("l_bracket", dict(BRACKET))
    assert params["hole_diameter_mm"] == 10 and not res.flags


def test_flag_text_does_not_claim_a_change():
    msg = v._as_flag("Hole diameter 3.2mm >= thickness 3.0mm, reducing to 2.5mm", 2.5)
    assert msg == "Hole diameter 3.2mm >= thickness 3.0mm. Kept as you typed; a safer value is 2.5."


def test_merge_notes_keeps_corrections_no_warning_explains():
    notes = v.merge_notes(["Hole diameter 3.2mm >= thickness 3.0mm, reducing to 2.5mm"],
                          {"hole_diameter_mm": {"old": 3.2, "new": 2.5}, "extra_mm": {"old": 10, "new": 8}})
    assert notes == ["Hole diameter 3.2mm >= thickness 3.0mm, reducing to 2.5mm", "extra_mm: 10 to 8"]

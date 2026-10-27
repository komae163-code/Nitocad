"""Holes have to fit their part. They do not have to be thinner than it."""
from validator import validate_flat_plate, validate_l_bracket, validate_parameters, validate_t_bracket


# ---- plates ---------------------------------------------------------------------------------

def test_a_hole_wider_than_the_plate_is_thick_is_left_alone():
    for thickness, hole in ((5.0, 6.0), (3.0, 8.0), (2.0, 10.0), (5.0, 5.0)):
        r = validate_flat_plate({"length_mm": 120, "width_mm": 80, "thickness_mm": thickness, "hole_count_x": 4, "hole_count_y": 3, "hole_diameter_mm": hole})
        assert "hole_diameter_mm" not in r.corrections, (thickness, hole)


def test_a_hole_that_leaves_less_than_2mm_at_the_edge_is_reduced():
    r = validate_flat_plate({"length_mm": 120, "width_mm": 80, "hole_count_x": 4, "hole_count_y": 3, "hole_diameter_mm": 20.0})   # a grid keeps centres 10 mm in
    assert r.corrections["hole_diameter_mm"]["new"] == 16.0
    r = validate_flat_plate({"length_mm": 120, "width_mm": 80, "hole_count_x": 1, "hole_count_y": 3, "hole_diameter_mm": 30.0})   # one column can be wider in X only
    assert r.corrections["hole_diameter_mm"]["new"] == 16.0                                    # but the three rows still have 10 mm margins in Y


def test_a_single_hole_can_be_nearly_as_wide_as_the_plate():
    ok = validate_flat_plate({"length_mm": 100, "width_mm": 80, "hole_count_x": 1, "hole_count_y": 1, "hole_diameter_mm": 60.0})
    assert "hole_diameter_mm" not in ok.corrections
    big = validate_flat_plate({"length_mm": 100, "width_mm": 80, "hole_count_x": 1, "hole_count_y": 1, "hole_diameter_mm": 79.0})
    assert big.corrections["hole_diameter_mm"]["new"] == 76.0                                  # width less 2 mm each side


def test_a_circular_pattern_follows_the_same_limit():
    r = validate_flat_plate({"hole_pattern": "circular", "hole_count_x": 6, "hole_count_y": 1, "hole_diameter_mm": 20.0})
    assert r.corrections["hole_diameter_mm"]["new"] == 16.0


def test_holes_that_would_run_into_each_other_are_cut_back_to_what_fits():
    # 60 mm long: centres span 40 mm. 10 mm holes need 12 mm between centres, so 4 fit.
    r = validate_flat_plate({"length_mm": 60, "width_mm": 80, "hole_count_x": 5, "hole_count_y": 1, "hole_diameter_mm": 10.0})
    assert r.corrections["hole_count_x"] == {"old": 5, "new": 4}
    assert "hole_count_y" not in r.corrections


def test_a_plate_with_no_holes_is_never_checked_for_them():
    r = validate_flat_plate({"length_mm": 30, "width_mm": 30, "hole_count_x": 0, "hole_count_y": 0, "hole_diameter_mm": 50.0})
    assert not r.corrections


def test_the_default_plate_is_untouched():
    assert not validate_flat_plate({}).corrections


def test_a_plate_with_no_hole_counts_has_no_holes_to_check():
    """The default is no holes, so a hole diameter with no count is left alone. (The default used to be a 4 x 3 grid.)"""
    r = validate_flat_plate({"length_mm": 60, "width_mm": 40, "hole_diameter_mm": 30.0})
    assert "hole_diameter_mm" not in r.corrections


# ---- L-brackets -----------------------------------------------------------------------------

def test_the_default_l_bracket_is_untouched():
    assert not validate_l_bracket({}).corrections


def test_too_many_holes_for_the_shorter_leg_are_cut_back():
    r = validate_l_bracket({"height_mm": 40, "depth_mm": 30, "thickness_mm": 3, "hole_count": 5, "hole_diameter_mm": 3.2})
    # the horizontal leg sticks out 27 mm, so the row of centres spans 7 mm: two 3.2 mm holes at 1.5 diameters apart
    assert r.corrections["hole_count"]["new"] == 2


def test_the_hole_limit_uses_the_corrected_thickness():
    r = validate_l_bracket({"width_mm": 50, "height_mm": 20, "depth_mm": 20, "thickness_mm": 9.0, "hole_diameter_mm": 9.0})
    assert r.corrections["thickness_mm"]["new"] < 9.0                                          # 9 mm metal on a 20 mm leg is cut to a third
    assert "hole_diameter_mm" in r.corrections and r.corrections["hole_diameter_mm"]["new"] > 0


# ---- T-brackets -----------------------------------------------------------------------------

def test_t_bracket_holes_are_limited_by_the_flat_beside_the_stem_not_the_whole_cap():
    # cap 40, stem 4 thick: each flat is 18 mm wide, so a hole can be 14 mm (2 mm of metal each side).
    ok = validate_t_bracket({"cap_width_mm": 40, "thickness_mm": 4, "hole_diameter_mm": 14.0, "hole_count": 1})
    assert "hole_diameter_mm" not in ok.corrections
    too_big = validate_t_bracket({"cap_width_mm": 40, "thickness_mm": 4, "hole_diameter_mm": 20.0, "hole_count": 1})
    assert too_big.corrections["hole_diameter_mm"]["new"] == 14.0                              # the old limit (cap - 4 = 36) let this through into the stem


def test_the_default_t_bracket_is_untouched():
    assert not validate_t_bracket({}).corrections


def test_validate_parameters_agrees():
    fixed, result = validate_parameters("flat_plate", {"length_mm": 120, "width_mm": 80, "thickness_mm": 5, "hole_diameter_mm": 6})
    assert fixed["hole_diameter_mm"] == 6 and result.is_valid


# ---- typed sizes and the newer validators' wording ------------------------------------------

def test_a_typed_hole_that_is_kept_does_not_claim_it_was_changed():
    """The newer validators word a correction as 'Changed hole_diameter_mm from 12 to 10.' If the person typed the size it
    is kept, so that sentence must be replaced, not left beside a second line saying it was kept."""
    text = "L-bracket 50mm wide, 60mm tall, 40mm deep, 3mm thick, 2 holes per leg, 12mm holes"
    base = {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 3, "hole_count": 2, "hole_diameter_mm": 12, "fillet_radius_mm": 1.0}
    params, res = validate_parameters("l_bracket", dict(base), description=text)
    assert params["hole_diameter_mm"] == 12
    assert res.flags["hole_diameter_mm"] == {"value": 12, "suggested": 10}
    notes = [w for w in res.warnings if "hole" in w.lower()]
    assert len(notes) == 1 and "Kept as you typed" in notes[0] and "Changed" not in notes[0]


def test_the_same_size_not_typed_is_corrected_and_says_so():
    base = {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 3, "hole_count": 2, "hole_diameter_mm": 12, "fillet_radius_mm": 1.0}
    params, res = validate_parameters("l_bracket", dict(base))
    assert params["hole_diameter_mm"] == 10 and not res.flags
    assert any("Changed hole_diameter_mm from 12 to 10" in w for w in res.warnings)

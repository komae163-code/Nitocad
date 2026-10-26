"""Plate holes, three-number sizes and hollow boxes in the rule-based parser, and the validator rules they rely on."""
import smart_parser as sp
from validator import validate_flat_plate, validate_gear, validate_parameters


def parse(text):
    r = sp.parse_description(text)
    return r.part_type, r.parameters


# ---- three-number sizes -----------------------------------------------------------------

def test_three_numbers_are_length_width_thickness_for_a_plate():
    t, p = parse("flat plate 200 x 100 x 6 mm")
    assert t == "flat_plate" and (p["length_mm"], p["width_mm"], p["thickness_mm"]) == (200, 100, 6)
    t, p = parse("plate 120 by 80 by 5 with 4 holes")
    assert (p["length_mm"], p["width_mm"], p["thickness_mm"]) == (120, 80, 5)


def test_three_numbers_are_length_width_height_for_a_box():
    t, p = parse("a box 200 by 150 by 100 mm")
    assert t == "simple_box" and (p["length_mm"], p["width_mm"], p["height_mm"]) == (200, 150, 100)


def test_units_apply_to_all_three_or_one_by_one():
    _, p = parse("plate 4in x 2in x 0.25in")
    assert (p["length_mm"], p["width_mm"], p["thickness_mm"]) == (101.6, 50.8, 6.35)
    _, p = parse("plate 6 x 4 x 0.25 inches")
    assert p["length_mm"] == 152.4 and p["thickness_mm"] == 6.35
    _, p = parse("box 10 x 6 x 4 cm")
    assert (p["length_mm"], p["width_mm"], p["height_mm"]) == (100, 60, 40)


def test_a_size_with_only_two_numbers_is_not_read_as_three():
    assert sp._triple_dims_mm("3 by 2 grid of holes") is None
    assert sp._triple_dims_mm("100 x 80") is None


# ---- holes ------------------------------------------------------------------------------

def test_an_explicit_grid_beats_a_bare_count_in_the_same_sentence():
    _, p = parse("plate 120 x 80 x 5 mm with 6 holes of 5 mm in a 3 by 2 grid")
    assert (p["hole_count_x"], p["hole_count_y"], p["hole_diameter_mm"]) == (3, 2, 5)


def test_a_bare_count_gives_exactly_that_many_holes():
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 16):
        _, p = parse(f"plate 150 x 100 x 6 mm with {n} holes")
        assert p["hole_count_x"] * p["hole_count_y"] == n, n


def test_the_grid_runs_longer_along_the_longer_side():
    assert sp.plate_grid_for_count(6, 150, 100) == (3, 2)
    assert sp.plate_grid_for_count(6, 100, 150) == (2, 3)
    assert sp.plate_grid_for_count(12, 200, 100) == (4, 3)
    assert sp.plate_grid_for_count(7, 200, 100) == (7, 1)          # a prime has no square-ish grid
    assert sp.plate_grid_for_count(1, 50, 50) == (1, 1)


def test_no_holes_means_no_holes():
    for text in ("flat plate 200 x 100 x 6 mm, no holes", "plate 100 x 60 x 5 without any holes", "solid plate 150 mm long, 100 mm wide and 6 mm thick, without holes",
                 "plain rectangular plate 100 by 80 by 5, no holes"):
        _, p = parse(text)
        assert (p["hole_count_x"], p["hole_count_y"]) == (0, 0), text


def test_a_plate_that_does_not_mention_holes_has_none():
    for text in ("flat plate 200 x 100 x 6 mm", "120 x 80 x 5 mm flat plate", "steel plate 150 mm long, 100 mm wide, 6 mm thick"):
        _, p = parse(text)
        assert (p["hole_count_x"], p["hole_count_y"]) == (0, 0), text


def test_a_plate_that_asks_for_holes_without_a_count_gets_the_usual_grid():
    for text in ("plate 200 x 100 x 6 mm with mounting holes", "flat plate 200 x 100 x 6 mm, drilled for bolts", "plate 100 x 80 x 5 with holes"):
        _, p = parse(text)
        assert (p["hole_count_x"], p["hole_count_y"]) == (4, 3), text


def test_a_bolt_circle_makes_a_circular_pattern():
    _, p = parse("square plate 100 x 100 x 6 mm with 6 holes of 5 mm on a bolt circle")
    assert p["hole_pattern"] == "circular" and p["hole_count_x"] == 6 and p["hole_diameter_mm"] == 5


def test_zero_holes_survive_validation_but_a_negative_or_half_zero_count_does_not():
    r = validate_flat_plate({"hole_count_x": 0, "hole_count_y": 0})
    assert "hole_count_x" not in r.corrections and "hole_count_y" not in r.corrections
    fixed, _ = validate_parameters("flat_plate", {"length_mm": 100, "width_mm": 60, "thickness_mm": 5, "hole_count_x": 0, "hole_count_y": 0})
    assert fixed["hole_count_x"] == 0 and fixed["hole_count_y"] == 0
    r = validate_flat_plate({"hole_count_x": 0, "hole_count_y": 3})
    assert r.corrections["hole_count_x"]["new"] == 1
    r = validate_flat_plate({"hole_count_x": -2, "hole_count_y": -3})
    assert r.corrections["hole_count_y"]["new"] == 1


# ---- boxes ------------------------------------------------------------------------------

def test_an_enclosure_or_a_box_with_walls_is_hollow():
    for text in ("box enclosure 100x80x50mm, 3mm walls, with lid", "open top box 100 x 80 x 50 mm with 3 mm walls", "project case 120 x 60 x 40 with 2mm walls",
                 "enclosure 150x100x60 mm 2.5 mm wall"):
        t, p = parse(text)
        assert t == "simple_box" and p["hollow"] is True and "has_lid" not in p, text


def test_a_plain_box_stays_solid():
    for text in ("a box 200 by 150 by 100 mm", "cube 50mm", "solid block 60 x 40 x 30 mm"):
        t, p = parse(text)
        assert t == "simple_box" and p["hollow"] is False, text


def test_the_wall_thickness_is_read_from_the_walls():
    assert parse("open top box 100 x 80 x 50 mm with 3 mm walls")[1]["wall_thickness_mm"] == 3
    assert parse("enclosure 150x100x60 mm 2.5 mm wall")[1]["wall_thickness_mm"] == 2.5
    assert parse("box 100 x 80 x 50 mm walls of 4 mm")[1]["wall_thickness_mm"] == 4


def test_a_cube_has_three_equal_sides():
    _, p = parse("cube 50mm")
    assert (p["length_mm"], p["width_mm"], p["height_mm"]) == (50, 50, 50)


# ---- the gear validator -----------------------------------------------------------------

def test_a_bore_that_would_cut_the_tooth_roots_is_reduced():
    # Module 1, 12 teeth: pitch diameter 12, root diameter 9.5. The old rule allows a bore up to 9.6,
    # which would leave nothing at the roots. The new one keeps 1 mm of wall: 7.5.
    r = validate_gear({"module": 1.0, "teeth": 12, "bore_diameter_mm": 8.0})
    assert r.corrections["bore_diameter_mm"] == {"old": 8.0, "new": 7.5}
    assert "bore_diameter_mm" not in validate_gear({"module": 1.0, "teeth": 12, "bore_diameter_mm": 7.0}).corrections
    assert "bore_diameter_mm" not in validate_gear({"module": 2.0, "teeth": 20, "bore_diameter_mm": 20}).corrections


def test_the_old_bore_rule_still_applies_first_and_the_root_rule_never_raises_it():
    r = validate_gear({"module": 2.0, "teeth": 20, "bore_diameter_mm": 35.0})
    assert r.corrections["bore_diameter_mm"]["new"] == 20.0                     # unchanged: 0.5 x the pitch diameter
    r = validate_gear({"module": 1.0, "teeth": 6, "bore_diameter_mm": 5.0})        # old rule says 3.0, root rule says 1.5
    assert r.corrections["bore_diameter_mm"] == {"old": 5.0, "new": 1.5}


def test_a_gear_too_small_to_carry_a_bore_is_an_error():
    assert not validate_gear({"module": 1.0, "teeth": 4, "bore_diameter_mm": 1.0}).is_valid


def test_fewer_than_three_teeth_is_an_error_and_a_few_is_a_warning():
    assert not validate_gear({"teeth": 2}).is_valid
    assert validate_gear({"teeth": 5}).is_valid


def test_the_pressure_angle_is_kept_within_the_standard_range():
    assert validate_gear({"pressure_angle": 20}).corrections.get("pressure_angle") is None
    assert validate_gear({"pressure_angle": 14.5}).corrections.get("pressure_angle") is None
    assert validate_gear({"pressure_angle": 60}).corrections["pressure_angle"]["new"] == 25.0
    assert validate_gear({"pressure_angle": 5}).corrections["pressure_angle"]["new"] == 14.5

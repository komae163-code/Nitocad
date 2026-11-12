"""A count that arrives as a float (44.0) is made a whole number for every part type, not only in vehicles."""
import pytest

from validator import is_count_key, validate_parameters, whole_number_counts


@pytest.mark.parametrize("ptype,params,key", [
    ("gear", {"module": 2, "teeth": 44.0, "thickness_mm": 10, "bore_diameter_mm": 8}, "teeth"),
    ("sprocket", {"teeth": 17.0, "chain_pitch_mm": 12.7}, "teeth"),
    ("flange", {"outer_diameter_mm": 80, "inner_diameter_mm": 20, "thickness_mm": 10, "hole_count": 6.0, "hole_diameter_mm": 8}, "hole_count"),
    ("t_bracket", {"hole_count": 3.0}, "hole_count"),
])
def test_a_whole_float_count_becomes_an_int_for_single_parts(ptype, params, key):
    fixed, _ = validate_parameters(ptype, dict(params))
    assert fixed[key] == params[key] and type(fixed[key]) is int


def test_a_fractional_count_is_rounded_and_reported():
    fixed, res = validate_parameters("gear", {"module": 2, "teeth": 44.6, "thickness_mm": 10, "bore_diameter_mm": 8})
    assert fixed["teeth"] == 45 and any("whole number" in w for w in res.warnings)


def test_a_size_that_merely_has_an_integer_default_is_left_alone():
    """angle_deg and motor_size_mm default to ints but 22.5 is a real value, so only counts are touched."""
    out, notes = whole_number_counts("t_bracket", {"angle_deg": 22.5, "hole_count": 3.0})
    assert out["angle_deg"] == 22.5 and out["hole_count"] == 3 and notes == []
    assert not is_count_key("angle_deg") and not is_count_key("motor_size_mm") and is_count_key("hole_count_x")


def test_the_callers_dict_is_not_edited_and_ints_pass_through_untouched():
    params = {"module": 2, "teeth": 44.0, "thickness_mm": 10, "bore_diameter_mm": 8}
    validate_parameters("gear", params)
    assert params["teeth"] == 44.0 and type(params["teeth"]) is float
    same, notes = whole_number_counts("gear", {"module": 2, "teeth": 44})
    assert same == {"module": 2, "teeth": 44} and notes == []


def test_unknown_part_types_and_non_counts_are_ignored():
    assert whole_number_counts("no_such_part", {"teeth": 4.0}) == ({"teeth": 4.0}, [])


def test_a_real_fraction_still_reaches_the_validator_instead_of_being_rounded_away():
    """Rounding first hid two deliberate behaviours: a sprocket rejects 12.5 teeth, and a road records a lane_count correction."""
    _, res = validate_parameters("sprocket", {"teeth": 12.5, "chain_pitch_mm": 12.7})
    assert not res.is_valid and res.errors
    fixed, res = validate_parameters("road_segment", {"lane_count": 3.6})
    assert type(fixed["lane_count"]) is int and "lane_count" in res.corrections

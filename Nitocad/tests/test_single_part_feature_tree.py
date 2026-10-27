"""Single-part AI parse path accepts and validates feature_tree responses."""
from pathlib import Path

from freeform_validation import VALIDATOR_VERSION, validate_llm_response

PLATE = {"id": "plate", "op": "extrude", "plane": "XY",
         "profile": {"type": "rect", "width": 100, "height": 60}, "distance": 8}
HOLE = {"id": "hole", "op": "extrude", "plane": "XY", "mode": "cut",
        "profile": {"type": "circle", "diameter": 6, "center": [-40, -20]}, "distance": 8}


def response(features, **extra):
    return {"part_type": "feature_tree", "parameters": {"features": features, **extra},
            "material": None, "operations": [], "confidence": 0.9}


def test_valid_feature_tree_is_accepted_and_normalized():
    data, errors = validate_llm_response(response([PLATE, HOLE]), "plate with a hole")
    assert errors == [] and data["part_type"] == "feature_tree"
    feats = data["parameters"]["features"]
    assert feats[0]["mode"] == "new" and feats[1]["mode"] == "cut" and feats[0]["offset"] == 0.0


def test_invalid_feature_tree_is_rejected_with_actionable_errors():
    data, errors = validate_llm_response(response([dict(PLATE, distance=-5)]), "x")
    assert data is None and any("distance" in e for e in errors)


def test_extra_parameters_are_rejected():
    data, errors = validate_llm_response(response([PLATE], color="red"), "x")
    assert data is None and any("only a" in e for e in errors)


def test_validator_version_was_bumped_so_stale_cache_entries_are_ignored():
    assert int(VALIDATOR_VERSION) >= 3


def test_parser_prompt_advertises_feature_tree_and_bumps_prompt_version():
    src = Path(__file__).resolve().parent.parent.joinpath("deepseek_parser.py").read_text(encoding="utf-8")
    assert '- feature_tree: features' in src
    assert "DSL_HELP" in src and 'PROMPT_VERSION = "freeform-fidelity-v3-civil1-arch5"' in src
    assert "- building:" in src and "- feature_tree:" in src and "road_segment" in src  # catalogue holds civil and mine
    assert "If the description doesn't clearly match any of these" in src  # anchor the prompt splice relies on
    assert '"freeform", "feature_tree"' in src  # fidelity-style error reporting covers both

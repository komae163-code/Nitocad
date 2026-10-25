"""The accuracy harness itself and the dataset it reads. Offline: scoring is pure, and the
dataset is checked against the real list of templates and parameter names."""
import json
import re
from pathlib import Path

import eval_parser as ev

ROOT = Path(__file__).resolve().parent.parent


def template_types():
    src = (ROOT / "cad_templates" / "__init__.py").read_text()
    return set(re.findall(r'^\s+"(\w+)":\s+generate_', src, re.M)) - {"freeform"}


def test_dataset_is_well_formed_and_big_enough():
    cases = ev.load_cases()
    assert len(cases) >= 90
    prompts = [c["prompt"].lower() for c in cases]
    assert len(prompts) == len(set(prompts)), "duplicate prompts"
    for c in cases:
        assert isinstance(c["prompt"], str) and 2 < len(c["prompt"]) < 300
        assert c["part_type"] in template_types() | {"unknown"}, c
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in c["params"].values()), c
        assert c["part_type"] != "unknown" or not c["params"]


def test_every_template_type_has_a_case():
    covered = {c["part_type"] for c in ev.load_cases()}
    assert not (template_types() - covered), template_types() - covered
    assert sum(c["part_type"] == "unknown" for c in ev.load_cases()) >= 5


def test_expected_parameter_names_exist_in_the_validators_or_templates():
    # Each key a case expects must be a real parameter name somewhere in the codebase,
    # otherwise the case can never pass whatever the parser does.
    blob = "".join(p.read_text() for p in (ROOT / "cad_templates").glob("*.py")) + (ROOT / "validator.py").read_text()
    for c in ev.load_cases():
        for key in c["params"]:
            assert f'"{key}"' in blob or f"'{key}'" in blob, (c["prompt"], key)


def test_scoring():
    case = {"prompt": "x", "part_type": "shaft", "params": {"diameter_mm": 10, "length_mm": 50}}
    assert ev.score_case(case, "shaft", {"diameter_mm": 10, "length_mm": 50.4, "extra": 1})["ok"]          # within 1%, extras ignored
    r = ev.score_case(case, "shaft", {"diameter_mm": 10})
    assert not r["ok"] and r["missing"] == ["length_mm"] and r["type_ok"]
    r = ev.score_case(case, "shaft", {"diameter_mm": 12, "length_mm": 50})
    assert r["wrong"] == {"diameter_mm": (10, 12)}
    assert not ev.score_case(case, "gear", {"diameter_mm": 10, "length_mm": 50})["type_ok"]
    assert not ev.score_case(case, "shaft", {"diameter_mm": True, "length_mm": 50})["ok"]               # a bool is not a size
    nothing = {"prompt": "hi", "part_type": "unknown", "params": {}}
    assert ev.score_case(nothing, "unknown", {})["ok"] and ev.score_case(nothing, None, None)["ok"]
    assert not ev.score_case(nothing, "shaft", {})["ok"]                                                   # inventing a part is a miss


def test_summary_and_exit_code():
    cases = ev.load_cases()[:4]
    results = [(c, ev.score_case(c, c["part_type"], dict(c["params"]))) for c in cases[:3]] + [(cases[3], ev.score_case(cases[3], "gear", {}))]
    s = ev.summarize(results)
    assert s["cases"] == 4 and s["accuracy"] == 0.75 and s["type_accuracy"] == 0.75
    assert sum(v["cases"] for v in s["by_type"].values()) == 4
    assert ev.summarize([])["accuracy"] == 0.0

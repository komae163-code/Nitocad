"""The chat planner for assemblies and for attached files being edited (conversation.py).
Pure Python: the AI and the parser are replaced by small stand-ins."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import conversation as cv  # noqa: E402

ASSEMBLY = {"part_type": "assembly", "parameters": {"layout": "row", "parts": [
    {"part_type": "flat_plate", "parameters": {"length_mm": 100}, "count": 1},
    {"part_type": "shaft", "parameters": {}, "count": 2, "name": "pin"}]}}
IMPORTED = {"part_type": "imported", "parameters": {"import_id": "a" * 16, "kind": "dxf", "filename": "plate.dxf", "thickness_mm": 3.0, "edits": [], "bbox_mm": [100, 60, 3]}}


class Parsed:
    def __init__(self, ptype, params=None):
        self.part_type, self.parameters, self.material, self.operations = ptype, params or {}, None, []


def regex_parse(text):
    if "toofew" in text:
        raise cv.PlanError("Describe at least two parts, for example: a base plate and an L bracket")
    if "plate" in text and "bracket" in text:
        return Parsed("assembly", {"parts": [{"part_type": "flat_plate", "parameters": {}}, {"part_type": "l_bracket", "parameters": {}}], "layout": "row"})
    return Parsed("gear" if "gear" in text else "flat_plate")


class TestAssemblySpecs:
    def test_a_good_parts_list_is_cleaned(self):
        c = cv._clean_spec(copy.deepcopy(ASSEMBLY))
        assert c["parameters"]["layout"] == "row" and c["parameters"]["parts"][1] == {"part_type": "shaft", "parameters": {}, "count": 2, "name": "pin"}

    def test_positions_survive_only_when_given(self):
        s = copy.deepcopy(ASSEMBLY)
        s["parameters"]["parts"][0]["position"] = [1, 2, 3]
        assert cv._clean_spec(s)["parameters"]["parts"][0]["position"] == [1.0, 2.0, 3.0]

    @pytest.mark.parametrize("params", [
        {}, {"parts": []}, {"parts": [{"part_type": "shaft"}]}, {"parts": [{"part_type": "assembly"}, {"part_type": "shaft"}]},
        {"parts": [{"part_type": "shaft", "position": [1, 2]}, {"part_type": "shaft"}]}, {"parts": [{"part_type": "shaft", "count": 10**9}, {"part_type": "shaft"}]},
        {"parts": [{"part_type": "shaft", "parameters": []}, {"part_type": "shaft"}]}, {"parts": ["x", "y"]},
        {"layout": "diagonal", "parts": [{"part_type": "shaft"}, {"part_type": "shaft"}]},
        {"parts": [{"part_type": "shaft"}] * (cv.RUNAWAY_ENTRIES + 1)},
    ])
    def test_unusable_parts_lists_are_refused(self, params):
        with pytest.raises(ValueError):
            cv._clean_spec({"part_type": "assembly", "parameters": params})

    def test_there_is_no_cap_on_parts_only_a_runaway_guard(self):
        assert cv.MAX_ASSEMBLY_ENTRIES is None and cv.MAX_PART_COUNT is None
        many = cv._clean_spec({"part_type": "assembly", "parameters": {"parts": [{"part_type": "shaft"}] * 60}})
        assert len(many["parameters"]["parts"]) == 60
        counted = cv._clean_spec({"part_type": "assembly", "parameters": {"parts": [{"part_type": "shaft", "count": 500}, {"part_type": "shaft"}]}})
        assert counted["parameters"]["parts"][0]["count"] == 500

    def test_a_cap_can_still_be_switched_on(self, monkeypatch):
        monkeypatch.setattr(cv, "MAX_ASSEMBLY_ENTRIES", 3)
        with pytest.raises(ValueError):
            cv._clean_spec({"part_type": "assembly", "parameters": {"parts": [{"part_type": "shaft"}] * 4}})

    def test_the_planner_accepts_an_assembly_from_the_ai(self):
        plan = cv.coerce_plan({"intent": "build", "reply": "ok", "spec": ASSEMBLY}, None)
        assert plan.intent == "build" and plan.spec["part_type"] == "assembly"

    def test_diff_names_added_parts_and_changed_values(self):
        old = cv._clean_spec(copy.deepcopy(ASSEMBLY))
        new = json.loads(json.dumps(old))
        new["parameters"]["parts"][0]["parameters"]["length_mm"] = 150
        new["parameters"]["parts"][1]["count"] = 3
        d = cv._diff(old, new)
        assert "flat_plate length_mm: 100 to 150" in d and "pin count: 2 to 3" in d
        new["parameters"]["parts"].append({"part_type": "gear", "parameters": {}, "count": 1})
        assert "parts: 2 to 3" in cv._diff(old, new)


class TestAssemblyRules:
    def test_a_description_with_two_parts_builds_an_assembly(self):
        plan = cv.rules_plan("a base plate and a bracket", None, regex_parse)
        assert plan.intent == "build" and plan.spec["part_type"] == "assembly"

    def test_too_few_parts_is_a_plain_reply(self):
        plan = cv.rules_plan("toofew", None, regex_parse)
        assert plan.intent == "answer" and "at least two parts" in plan.reply

    def test_an_assembly_cannot_be_edited_by_the_rules_and_says_how_to_continue(self):
        plan = cv.rules_plan("make it 150mm long", cv._clean_spec(copy.deepcopy(ASSEMBLY)), regex_parse)
        assert plan.intent == "answer" and "AI" in plan.reply

    def test_describing_a_whole_new_assembly_replaces_it(self):
        assert cv.rules_plan("plate and bracket again", cv._clean_spec(copy.deepcopy(ASSEMBLY)), regex_parse).intent == "build"


class TestImportedSpecs:
    def test_params_are_cleaned_and_the_size_note_is_dropped(self):
        assert cv._clean_spec(copy.deepcopy(IMPORTED))["parameters"] == {"import_id": "a" * 16, "kind": "dxf", "filename": "plate.dxf", "thickness_mm": 3.0, "edits": []}

    def test_an_ai_cannot_invent_a_file(self):
        bad = copy.deepcopy(IMPORTED)
        bad["parameters"]["import_id"] = "nope"
        with pytest.raises(ValueError):
            cv._clean_spec(bad)

    def test_the_ai_can_return_edits(self):
        spec = copy.deepcopy(IMPORTED)
        spec["parameters"]["edits"] = [{"op": "hole", "diameter_mm": 6, "x": 0, "y": 0}, {"op": "scale_to", "mm": 150}]
        plan = cv.coerce_plan({"intent": "edit", "reply": "Added a hole.", "spec": spec}, cv._clean_spec(copy.deepcopy(IMPORTED)))
        assert [e["op"] for e in plan.spec["parameters"]["edits"]] == ["hole", "scale_to"] and plan.changes

    def test_a_mesh_cannot_be_given_a_hole_by_the_ai(self):
        spec = {"part_type": "imported", "parameters": {"import_id": "b" * 16, "kind": "stl", "filename": "m.stl", "edits": [{"op": "hole", "diameter_mm": 6}]}}
        with pytest.raises(ValueError):
            cv._clean_spec(spec)

    def test_diff_shows_added_and_removed_edits_and_thickness(self):
        a = cv._clean_spec(copy.deepcopy(IMPORTED))
        b = copy.deepcopy(a)
        b["parameters"]["thickness_mm"] = 5.0
        b["parameters"]["edits"] = [{"op": "hole", "diameter_mm": 6.0, "x": 0.0, "y": 0.0, "depth_mm": None, "face": "top"}]
        d = cv._diff(a, b)
        assert "thickness: 3.0 to 5 mm" in d and any(x.startswith("added: 6 mm hole") for x in d)
        assert any(x.startswith("removed: 6 mm hole") for x in cv._diff(b, a))


class TestImportedRules:
    def test_edit_words_become_edits(self):
        plan = cv.rules_plan("add a 6 mm hole at the center", cv._clean_spec(copy.deepcopy(IMPORTED)), regex_parse)
        assert plan.intent == "edit" and plan.spec["parameters"]["edits"][0]["op"] == "hole" and plan.reply.startswith("Done.")

    def test_unclear_words_get_the_help_text_not_a_new_part(self):
        plan = cv.rules_plan("hello", cv._clean_spec(copy.deepcopy(IMPORTED)), regex_parse)
        assert plan.intent == "answer" and "scale it to 150 mm long" in plan.reply

    def test_naming_another_part_builds_it_instead(self):
        assert cv.rules_plan("make a gear 20 teeth", cv._clean_spec(copy.deepcopy(IMPORTED)), regex_parse).intent == "build"

    def test_the_ai_instructions_describe_both_features(self):
        assert "IMPORTED PART" in cv._INSTRUCTIONS and "ASSEMBLY" in cv._INSTRUCTIONS and "NEVER invent" in cv._INSTRUCTIONS or "never invent" in cv._INSTRUCTIONS

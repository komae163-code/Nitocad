"""Which engine builds an assembly: named / mated spec -> mate engine, plain parts list -> row layout.
Pure Python (no CAD kernel): covers routing, the planner's mated shape, its validation and the prompt."""
import copy
import unittest

import assembly
import assembly_spec
import assembly_templates
import conversation as cv


def _planner_mated():
    """The wheel corner module written the way the chat planner would return it."""
    spec = assembly_templates.corner_module()
    return {
        "layout": "mated", "root": spec["root"], "name": spec["name"],
        "parts": [{"id": p["id"], "part_type": p["part_type"], "parameters": dict(p["parameters"])} for p in spec["parts"]],
        "mates": [dict(m) for m in spec["mates"]],
    }


class RouteTests(unittest.TestCase):
    def test_plain_parts_list_is_row(self):
        self.assertEqual(assembly.assembly_route({"parts": [{"part_type": "flat_plate"}], "layout": "row"}), "row")
        self.assertEqual(assembly.assembly_route({}), "row")
        self.assertEqual(assembly.assembly_route(None), "row")
        self.assertEqual(assembly.assembly_route({"parts": [], "layout": "custom"}), "row")

    def test_mates_or_stored_spec_is_mated(self):
        self.assertEqual(assembly.assembly_route({"parts": [], "mates": [{"a": "x.y", "b": "z.w"}]}), "mated")
        self.assertEqual(assembly.assembly_route({"layout": "mated"}), "mated")
        self.assertEqual(assembly.assembly_route({"assembly": {"parts": [{"id": "a"}]}}), "mated")
        self.assertEqual(assembly.assembly_route({"assembly": {}}), "row")

    def test_mated_spec_from_planner_shape_resolves(self):
        params = cv._clean_assembly_params(_planner_mated())
        res = assembly_spec.resolve_assembly(assembly.mated_spec(params))
        self.assertTrue(res.ok, [f.message for f in res.errors])
        self.assertEqual(set(res.order), {p["id"] for p in params["parts"]})

    def test_mated_spec_returns_stored_spec_unchanged(self):
        stored = {"name": "n", "parts": [{"id": "a", "part_type": "flat_plate", "parameters": {}}], "mates": []}
        self.assertIs(assembly.mated_spec({"assembly": stored}), stored)

    def test_incompatible_mate_is_reported_not_replaced(self):
        bad = copy.deepcopy(_planner_mated())
        bad["mates"][0] = {"a": "rotor.hub_face", "b": "arm.bolt_pattern"}   # wrong kinds / unknown interface
        res = assembly_spec.resolve_assembly(assembly.mated_spec(cv._clean_assembly_params(bad)))
        self.assertFalse(res.ok)
        self.assertTrue(res.errors)


class PlannerShapeTests(unittest.TestCase):
    def test_row_assembly_is_unchanged(self):
        out = cv._clean_assembly_params({"parts": [{"part_type": "flat_plate", "count": 2}, {"part_type": "l_bracket"}]})
        self.assertEqual(out["layout"], "row")
        self.assertNotIn("mates", out)
        self.assertEqual(out["parts"][0]["count"], 2)

    def test_mates_imply_mated_layout(self):
        raw = _planner_mated(); raw["layout"] = "row"
        self.assertEqual(cv._clean_assembly_params(raw)["layout"], "mated")

    def test_rejects_bad_mated_shapes(self):
        base = _planner_mated()
        cases = {}
        c = copy.deepcopy(base); c["parts"][0].pop("id"); cases["missing id"] = c
        c = copy.deepcopy(base); c["parts"][1]["id"] = c["parts"][0]["id"]; cases["duplicate id"] = c
        c = copy.deepcopy(base); c["parts"][0]["count"] = 3; cases["count in mated"] = c
        c = copy.deepcopy(base); c["mates"][0]["a"] = "ghost.face"; cases["mate to unknown part"] = c
        c = copy.deepcopy(base); c["mates"][0]["a"] = "rotor"; cases["mate without interface"] = c
        c = copy.deepcopy(base); c["root"] = "ghost"; cases["unknown root"] = c
        c = copy.deepcopy(base); c["mates"] = []; cases["mated without mates"] = c
        for label, raw in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    cv._clean_assembly_params(raw)

    def test_llm_plan_builds_and_edit_diff_mentions_mates(self):
        raw = {"intent": "build", "reply": "Built the corner.", "spec": {"part_type": "assembly", "parameters": _planner_mated()}}
        plan = cv.coerce_plan(raw, None)
        self.assertEqual(plan.spec["parameters"]["layout"], "mated")
        edited = copy.deepcopy(raw["spec"]); edited["parameters"]["mates"] = edited["parameters"]["mates"][:-1]
        plan2 = cv.coerce_plan({"intent": "edit", "reply": "ok", "spec": edited}, plan.spec)
        self.assertTrue(any(c.startswith("mates:") for c in plan2.changes), plan2.changes)

    def test_prompt_lists_interfaces_and_mated_rule(self):
        p = cv.build_system_prompt("")
        self.assertIn("MATED ASSEMBLY", p)
        self.assertIn("INTERFACE CATALOGUE", p)
        self.assertIn("wheel_hub", p)


if __name__ == "__main__":
    unittest.main()

"""PCB boards inside CAD Studio and the CAD pipeline: chat planning, validation, the 3D solid description
and the package service. Pure Python - no CadQuery, FastAPI or network (the parser test needs pydantic and the
template-registry test needs CadQuery; each skips if its library is missing)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conversation as cv
import pcb_service
from pcb import intent
from pcb.model3d import component_height_mm, design_from_params, solid_boxes
from validator import validate_parameters

H = lambda *msgs: [{"role": "user", "content": m} for m in msgs]          # noqa: E731
HAVE = lambda mod: importlib.util.find_spec(mod) is not None               # noqa: E731


class IntentTests(unittest.TestCase):
    def test_boards_are_recognised(self):
        for text in ("design a PCB for a sensor node", "5V board with a 555 timer blinking LED",
                     "a 12V buck converter board with an LED", "circuit board with an I2C header"):
            self.assertTrue(intent.is_pcb_request(text), text)

    def test_mechanical_parts_that_mention_a_board_are_not_boards(self):
        for text in ("hex standoff for PCB mounting", "enclosure for a PCB 80mm x 60mm",
                     "L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes", "gear 20 teeth module 2"):
            self.assertFalse(intent.is_pcb_request(text), text)


class ChatPlanningTests(unittest.TestCase):
    def test_first_message_builds_a_board_from_the_rules_planner(self):
        p = cv.plan_turn(H("design a PCB with a 555 blinking LED powered from 5V"), None)
        self.assertEqual((p.intent, p.source), ("build", "rules"))
        self.assertEqual(p.spec["part_type"], "pcb_board")
        types = [b["type"] for b in p.spec["parameters"]["spec"]["blocks"]]
        self.assertIn("tlc555_blinker", types)
        self.assertIn("free preview", p.reply)

    def test_follow_up_edits_the_same_board_and_keeps_earlier_requests(self):
        first = cv.plan_turn(H("design a PCB with a 555 blinking LED powered from 5V"), None)
        p = cv.plan_turn(H("design a PCB with a 555 blinking LED powered from 5V", "add a MOSFET switch for a load"),
                         first.spec)
        self.assertEqual(p.intent, "edit")
        types = [b["type"] for b in p.spec["parameters"]["spec"]["blocks"]]
        self.assertIn("mosfet_switch", types)
        self.assertIn("tlc555_blinker", types)               # still there
        self.assertTrue(any("added" in c for c in p.changes))

    def test_later_voltage_overrides_the_first(self):
        first = cv.plan_turn(H("PCB with a status LED powered from 5V"), None)
        p = cv.plan_turn(H("x", "change it to a 12V supply"), first.spec)
        blocks = p.spec["parameters"]["spec"]["blocks"]
        self.assertEqual(next(b for b in blocks if b["type"] == "power_in_terminal")["params"]["voltage"], 12.0)
        self.assertIn("buck_lm2596", [b["type"] for b in blocks])

    def test_no_change_removal_and_unsupported_get_honest_answers(self):
        first = cv.plan_turn(H("PCB with a status LED powered from 5V"), None)
        for text, needle in (("what is a 555?", "nothing on the board was changed"), ("remove the LED", "not supported yet")):
            p = cv.plan_turn(H("x", text), first.spec)
            self.assertEqual(p.intent, "answer", text)
            self.assertIn(needle, p.reply)
        p = cv.plan_turn(H("design a PCB with a wifi module and an OLED"), None)
        self.assertEqual(p.intent, "answer")
        self.assertIn("nothing was charged", p.reply)

    def test_questions_never_change_the_board_and_go_to_the_ai_when_there_is_one(self):
        board = cv.plan_turn(H("PCB with a status LED powered from 5V"), None).spec
        for q in ("what is a 555?", "how does the regulator work", "Can you add a 555?"):
            self.assertEqual(cv.plan_turn(H("x", q), board).intent, "answer", q)
        self.assertIsNone(cv._pcb_turn("what is a 555 timer?", board, lambda m: "{}"))   # AI path answers it

    def test_a_mechanical_message_is_not_taken_over(self):
        self.assertIsNone(cv._pcb_turn("L-bracket 50 mm wide, 60 mm tall, 3 mm thick", None, None))
        board = cv.plan_turn(H("PCB with a status LED powered from 5V"), None).spec
        self.assertIsNone(cv._pcb_turn("make an L-bracket 50 mm wide", board, None))

    def test_ai_planner_is_used_when_valid_and_falls_back_to_rules_otherwise(self):
        spec = {"title": "t", "blocks": [
            {"id": "pwr", "type": "power_in_terminal", "params": {"voltage": 5}, "nets": {"VIN": "VIN5", "GND": "GND"}},
            {"id": "led", "type": "led_indicator", "nets": {"V": "VIN5", "GND": "GND"}}]}
        ok = cv.plan_turn(H("design a PCB with a status LED"), None, llm=lambda m: json.dumps(spec))
        self.assertEqual(ok.source, "llm")

        def down(_):
            raise RuntimeError("model is down")
        # the model failed, the rules planner built it: not counted as, or priced as, an AI plan
        self.assertEqual(cv.plan_turn(H("design a PCB with a status LED"), None, llm=down).source, "rules")
        self.assertEqual(cv.plan_turn(H("design a PCB with a status LED"), None, llm=lambda m: "nonsense").source, "rules")


class ChatServiceTests(unittest.TestCase):
    def test_a_board_flows_through_chat_service_as_a_free_preview(self):
        from _chat_fakes import fresh_store
        import chat_service as svc
        import chat_store as cs

        fresh_store()
        user = "0xuser"
        chat_id = cs.create_chat(user)["id"]
        made = []

        def generate(description, spec, user_id, source):
            params, result = validate_parameters(spec["part_type"], spec["parameters"])
            if not result.is_valid:
                return {"success": False, "error": "; ".join(result.errors)}
            made.append(spec)
            return {"success": True, "job_id": f"job{len(made)}", "parameters": spec,
                    "validation": {"warnings": result.warnings, "corrections": {}}}

        deps = svc.Deps(plan=lambda hist, spec: cv.plan_turn(hist, spec), generate=generate,
                        balance=lambda u: 5, job_cost=lambda j: 1)
        out = svc.run_turn(deps, user, chat_id, "design a PCB with a status LED powered from 5V")
        self.assertEqual(out["part"]["part_type"], "pcb_board")
        self.assertEqual(out["part"]["title"], "PCB board")
        self.assertEqual(out["charged"], 0)
        self.assertTrue(out["part"]["notes"])                 # the not-tested-on-hardware warning is shown
        out2 = svc.run_turn(deps, user, chat_id, "add an I2C sensor header")
        self.assertEqual(out2["part"]["version"], 2)

        bad = svc.run_turn(deps, user, chat_id, "x")          # a non-board message in a board chat
        self.assertIsNone(bad["part"])


class ValidatorTests(unittest.TestCase):
    def test_valid_board_has_the_hardware_warning(self):
        _, r = validate_parameters("pcb_board", {"description": "5V board with a status LED"})
        self.assertTrue(r.is_valid)
        self.assertTrue(any("tested on hardware" in w for w in r.warnings))

    def test_unbuildable_boards_say_why(self):
        for desc, needle in (("wifi gadget", "cannot build"), ("", "Describe the circuit board")):
            _, r = validate_parameters("pcb_board", {"description": desc})
            self.assertFalse(r.is_valid)
            self.assertIn(needle, r.errors[0])
        _, r = validate_parameters("pcb_board", {"description": "35V input with a status LED"})
        self.assertFalse(r.is_valid)
        self.assertIn("30.0 V", r.errors[0])

    def test_a_stored_spec_wins_over_the_description(self):
        spec = {"title": "t", "blocks": [
            {"id": "pwr", "type": "power_in_terminal", "params": {"voltage": 5}, "nets": {"VIN": "VIN5", "GND": "GND"}},
            {"id": "led", "type": "led_indicator", "nets": {"V": "VIN5", "GND": "GND"}}]}
        _, r = validate_parameters("pcb_board", {"description": "wifi gadget", "spec": spec})
        self.assertTrue(r.is_valid)


class SolidModelTests(unittest.TestCase):
    def test_every_part_sits_on_the_board_and_inside_its_outline(self):
        design, _ = design_from_params({"description": "5V board with a 555 blinking LED and a MOSFET switch"})
        boxes = solid_boxes(design)
        board = boxes[0]
        self.assertEqual((board["name"], board["sz"]), ("board", 1.6))
        self.assertEqual(len(boxes), len(design.components) + 1)
        for b in boxes[1:]:
            self.assertLessEqual(abs(b["cx"]) + b["sx"] / 2, board["sx"] / 2 + 0.01, b["name"])
            self.assertLessEqual(abs(b["cy"]) + b["sy"] / 2, board["sy"] / 2 + 0.01, b["name"])
            self.assertLess(b["z0"], 1.6)                      # sunk into the board so the union fuses
            self.assertGreater(b["z0"] + b["sz"], 1.6)

    def test_heights_follow_the_footprint(self):
        class C:
            def __init__(self, ref, lib): self.ref, self.lib_id = ref, lib
        self.assertEqual(component_height_mm(C("C1", "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm")), 15.0)
        self.assertGreater(component_height_mm(C("J1", "nitocad:TerminalBlock_1x02_P5.08mm")), 8)
        self.assertLess(component_height_mm(C("R1", "Resistor_SMD:R_0603_1608Metric")), 1)
        self.assertEqual(component_height_mm(C("L1", "Inductor_SMD:L_12x12mm_H8mm")), 8.0)

    def test_deterministic(self):
        a, _ = design_from_params({"description": "5V board with a status LED"})
        b, _ = design_from_params({"description": "5V board with a status LED"})
        self.assertEqual(solid_boxes(a), solid_boxes(b))


@unittest.skipUnless(HAVE("pydantic"), "pydantic not installed")
class ParserTests(unittest.TestCase):
    def test_pcb_sentences_become_pcb_boards_and_mechanical_ones_do_not(self):
        from smart_parser import parse_description
        self.assertEqual(parse_description("design a PCB for a sensor node").part_type, "pcb_board")
        self.assertEqual(parse_description("hex standoff for PCB mounting").part_type, "hex_standoff")
        self.assertEqual(parse_description("enclosure for a PCB 80mm x 60mm").part_type, "simple_box")


@unittest.skipUnless(HAVE("cadquery"), "CadQuery not installed")
class TemplateRegistryTests(unittest.TestCase):
    def test_template_builds_one_solid_with_the_board_footprint(self):
        from cad_templates import TEMPLATES
        wp = TEMPLATES["pcb_board"]({"description": "5V board with a status LED"})
        solid = wp.val()
        self.assertGreater(solid.Volume(), 0)
        self.assertEqual(len(wp.solids().vals()), 1)
        bb = solid.BoundingBox()
        design, _ = design_from_params({"description": "5V board with a status LED"})
        board = solid_boxes(design)[0]
        self.assertAlmostEqual(bb.xlen, board["sx"], delta=0.5)
        self.assertAlmostEqual(bb.ylen, board["sy"], delta=0.5)


class PackageServiceTests(unittest.TestCase):
    JOB = {"id": "job123", "parameters": json.dumps({"description": "5V board with a 555 blinking LED"})}

    def test_builds_once_then_serves_from_cache(self):
        root = Path(tempfile.mkdtemp())
        report = pcb_service.ensure_package(self.JOB, root, run_kicad=False)
        self.assertTrue(report["ok"] and report["routed"])
        self.assertFalse(report["manufacturable"])            # no KiCad: never claimed
        for kind in ("svg", "bom", "kicad", "report"):
            self.assertIsNotNone(pcb_service.file_for(self.JOB, root, kind), kind)
        self.assertIsNone(pcb_service.file_for(self.JOB, root, "gerbers"))
        self.assertIsNone(pcb_service.file_for(self.JOB, root, "../../etc/passwd"))
        self.assertEqual(pcb_service.ensure_package(self.JOB, root, run_kicad=False), report)

    def test_unbuildable_jobs_raise_with_a_reason(self):
        root = Path(tempfile.mkdtemp())
        for params in ({"description": "wifi gadget"}, {}):
            with self.assertRaises(pcb_service.PackageUnavailable):
                pcb_service.ensure_package({"id": "j", "parameters": json.dumps(params)}, root, run_kicad=False)


class TitleTests(unittest.TestCase):
    def test_title(self):
        import chat_service as svc
        self.assertEqual(svc.titleize("pcb_board"), "PCB board")
        self.assertEqual(svc.titleize("l_bracket"), "L bracket")


if __name__ == "__main__":
    unittest.main()

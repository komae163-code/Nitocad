"""Tests for the PCB domain pack. Pure python - no KiCad needed (KiCad stages are skipped)."""
import json
import tempfile
import unittest
from pathlib import Path

from pcb import footprints
from pcb.blocks import BLOCKS
from pcb.compose import SpecError, compose, validate_spec
from pcb.pipeline import FILE_KINDS, build_board, file_path_for, make_bom
from pcb.planner import PlanError, keyword_plan, llm_plan, plan

POWER = {"id": "pwr", "type": "power_in_terminal", "params": {"voltage": 5}, "nets": {"VIN": "VIN5", "GND": "GND"}}


def spec_of(*blocks, title="t"):
    return {"title": title, "blocks": [POWER, *blocks]}


class ValidationTests(unittest.TestCase):
    def test_unknown_block_type(self):
        _, errors, _ = validate_spec(spec_of({"id": "x", "type": "flux_capacitor", "nets": {}}))
        self.assertTrue(any("unknown type" in e for e in errors))

    def test_unconnected_port_and_bad_net_name(self):
        _, errors, _ = validate_spec(spec_of(
            {"id": "led", "type": "led_indicator", "nets": {"V": "lowercase", "GND": "GND"}},
            {"id": "led2", "type": "led_indicator", "nets": {"V": "VIN5"}}))
        self.assertTrue(any("UPPERCASE" in e for e in errors))
        self.assertTrue(any("not connected" in e for e in errors))

    def test_overvoltage_is_caught(self):
        spec = {"title": "t", "blocks": [
            {"id": "pwr", "type": "power_in_terminal", "params": {"voltage": 12}, "nets": {"VIN": "VIN", "GND": "GND"}},
            {"id": "reg", "type": "ldo_3v3", "nets": {"VIN": "VIN", "VOUT": "V3", "GND": "GND"}}]}
        _, errors, _ = validate_spec(spec)
        self.assertTrue(any("at most 6.0 V" in e for e in errors))

    def test_two_sources_on_one_net_is_a_short(self):
        spec = spec_of({"id": "p2", "type": "power_in_terminal", "nets": {"VIN": "VIN5", "GND": "GND"}})
        _, errors, _ = validate_spec(spec)
        self.assertTrue(any("more than one source" in e for e in errors))

    def test_unpowered_consumer(self):
        spec = {"title": "t", "blocks": [{"id": "led", "type": "led_indicator", "nets": {"V": "VCC", "GND": "GND"}}]}
        _, errors, _ = validate_spec(spec)
        self.assertTrue(any("nothing supplies it" in e for e in errors))

    def test_block_param_out_of_range_becomes_spec_error(self):
        spec = spec_of({"id": "osc", "type": "tlc555_blinker", "params": {"timing_cap_nf": 5},
                        "nets": {"V": "VIN5", "GND": "GND", "OUT": "O"}})
        with self.assertRaises(SpecError):
            compose(spec)


class FootprintTests(unittest.TestCase):
    def test_2x3_header_numbering_matches_kicad(self):
        fp = footprints._builtin("Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
        pos = {p.number: (p.x, p.y) for p in fp.pads}
        self.assertEqual(pos["1"], (0.0, 0.0))
        self.assertEqual(pos["2"], (2.54, 0.0))
        self.assertEqual(pos["5"], (0.0, 5.08))

    def test_soic8_pin1_top_left_ccw(self):
        fp = footprints._builtin("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
        pos = {p.number: (p.x, p.y) for p in fp.pads}
        self.assertLess(pos["1"][0], 0)
        self.assertLess(pos["1"][1], pos["4"][1])
        self.assertGreater(pos["5"][0], 0)
        self.assertGreater(pos["5"][1], pos["8"][1])

    def test_missing_footprint_is_an_error_not_a_guess(self):
        with self.assertRaises(footprints.FootprintError):
            footprints.resolve("Nope:Not_A_Footprint")


class PipelineTests(unittest.TestCase):
    def build(self, spec):
        with tempfile.TemporaryDirectory() as d:
            report = build_board(spec, Path(d), run_kicad=False)
            files = {k: file_path_for(Path(d), k) is not None for k in FILE_KINDS}
            texts = {k: file_path_for(Path(d), k).read_text(errors="ignore")
                     for k in ("svg", "bom", "report") if file_path_for(Path(d), k)}
            pcb_text = next(Path(d).glob("*.kicad_pcb")).read_text()
        return report, files, texts, pcb_text

    def test_blinker_board_builds_and_is_honest_about_kicad(self):
        spec = spec_of({"id": "osc", "type": "tlc555_blinker", "nets": {"V": "VIN5", "GND": "GND", "OUT": "BLINK"}},
                       {"id": "led", "type": "led_from_signal", "nets": {"SIG": "BLINK", "GND": "GND"}})
        report, files, texts, pcb_text = self.build(spec)
        self.assertTrue(report["routed"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["verification"], "not_checked_by_kicad")
        self.assertFalse(report["manufacturable"])          # no KiCad -> never claimed manufacturable
        self.assertFalse(files["gerbers"])
        self.assertTrue(files["svg"] and files["bom"] and files["kicad"] and files["report"])
        self.assertTrue(pcb_text.startswith("(kicad_pcb"))
        self.assertIn("<svg", texts["svg"])
        self.assertTrue(any("KiCad is not installed" in w for w in report["warnings"]))
        self.assertTrue(any("Hz" in n for n in report["notes"]))
        json.loads(texts["report"])

    def test_bom_groups_identical_parts(self):
        spec = spec_of({"id": "i2c", "type": "i2c_sensor_header",
                        "nets": {"V": "VIN5", "GND": "GND", "SDA": "SDA", "SCL": "SCL"}})
        design = compose(spec)
        rows = make_bom(design).strip().splitlines()
        pullups = [r for r in rows if "4.7k" in r]
        self.assertEqual(len(pullups), 1)
        self.assertIn(",2,", pullups[0])

    def test_invalid_spec_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SpecError):
                build_board({"blocks": []}, Path(d), run_kicad=False)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_file_lookup_rejects_unknown_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(file_path_for(Path(d), "../../etc/passwd"))
            self.assertIsNone(file_path_for(Path(d), "svg"))      # whitelisted but absent

    @unittest.skipIf(footprints.library_dir() is not None, "KiCad footprint library present")
    def test_mcu_without_library_fails_loudly(self):
        spec = {"title": "t", "blocks": [
            POWER,
            {"id": "mcu", "type": "mcu_atmega328p", "nets": {"V": "VIN5", "GND": "GND", "SDA": "SDA",
                                                            "SCL": "SCL", "IO1": "A", "IO2": "B", "IO3": "C"}}]}
        with self.assertRaises(SpecError) as cm:
            compose(spec)
        self.assertIn("not available", str(cm.exception))


class PlannerTests(unittest.TestCase):
    def test_keywords_blink_and_switch(self):
        r = keyword_plan("5V board with a 555 blinking LED and a MOSFET switch for a load")
        types = [b["type"] for b in r.spec["blocks"]]
        self.assertIn("tlc555_blinker", types)
        self.assertIn("mosfet_switch", types)
        self.assertIn("header_breakout", types)        # undriven gate gets exposed

    def test_oled_is_not_mistaken_for_led(self):
        with self.assertRaises(PlanError) as cm:
            keyword_plan("wifi thermostat with an OLED")
        self.assertIn("cannot build", cm.exception.errors[0])

    def test_12v_input_gets_a_buck_converter_not_an_ldo(self):
        r = keyword_plan("12V input with a 3.3V regulator and an I2C sensor header")
        types = [b["type"] for b in r.spec["blocks"]]
        self.assertIn("buck_lm2596", types)
        self.assertNotIn("ldo_3v3", types)
        buck = next(b for b in r.spec["blocks"] if b["type"] == "buck_lm2596")
        self.assertEqual(buck["params"]["vout"], 3.3)

    def test_explicit_target_voltage_is_used(self):
        r = keyword_plan("24V input stepped down to 9V with a status LED")
        buck = next(b for b in r.spec["blocks"] if b["type"] == "buck_lm2596")
        self.assertEqual(buck["params"]["vout"], 9.0)

    def test_input_above_buck_limit_is_reported(self):
        with self.assertRaises(PlanError) as cm:
            keyword_plan("35V input with a status LED")
        self.assertTrue(any("at most 30.0 V" in e for e in cm.exception.errors))

    def test_nonsense_prompt(self):
        with self.assertRaises(PlanError):
            keyword_plan("make me a sandwich")

    def test_every_keyword_plan_validates(self):
        for prompt in ["usb powered led", "5V sensor node with i2c header and led", "mosfet switch 12v load",
                       "555 blinker on 9V"]:
            r = keyword_plan(prompt)
            _, errors, _ = validate_spec(r.spec)
            self.assertEqual(errors, [], prompt)

    def test_llm_repair_loop_feeds_errors_back(self):
        bad = {"title": "t", "blocks": [{"id": "x", "type": "flux_capacitor", "nets": {}}]}
        good = spec_of({"id": "led", "type": "led_indicator", "nets": {"V": "VIN5", "GND": "GND"}})
        replies = [json.dumps(bad), "```json\n" + json.dumps(good) + "\n```"]
        seen = []

        def call(messages):
            seen.append(messages[-1]["content"])
            return replies[len(seen) - 1]

        r = llm_plan("an led", call)
        self.assertEqual(r.attempts, 2)
        self.assertEqual(r.source, "llm")
        self.assertIn("rejected", seen[1])
        self.assertIn("unknown type", seen[1])

    def test_llm_gives_up_with_reasons(self):
        bad = json.dumps({"title": "t", "blocks": [{"id": "x", "type": "nope", "nets": {}}]})
        with self.assertRaises(PlanError) as cm:
            llm_plan("x", lambda m: bad, max_repairs=1)
        self.assertTrue(any("unknown type" in e for e in cm.exception.errors))

    def test_llm_can_decline(self):
        with self.assertRaises(PlanError) as cm:
            llm_plan("wifi board", lambda m: '{"error": "no wifi block"}')
        self.assertIn("no wifi block", cm.exception.errors[0])

    def test_unreadable_reply_is_retried(self):
        good = json.dumps(spec_of({"id": "led", "type": "led_indicator", "nets": {"V": "VIN5", "GND": "GND"}}))
        replies = iter(["sorry, here you go!", good])
        self.assertEqual(llm_plan("led", lambda m: next(replies)).attempts, 2)

    def test_plan_without_call_uses_keywords(self):
        self.assertEqual(plan("5V led").source, "keywords")


def _write_fake_library(root: Path) -> None:
    """Synthetic stand-in for KiCad's footprint library (approximate geometry) so the buck block's
    routing plumbing can be tested without KiCad. NOT a check of real footprint accuracy."""
    def smd(n, x, y, w, h):
        return f'(pad "{n}" smd rect (at {x} {y}) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))'

    def thru(n, x, y):
        return f'(pad "{n}" thru_hole circle (at {x} {y}) (size 2.4 2.4) (drill 1.2) (layers "*.Cu" "*.Mask"))'

    def mod(lib, name, pads, court):
        d = root / f"{lib}.pretty"
        d.mkdir(parents=True, exist_ok=True)
        x0, y0, x1, y1 = court
        (d / f"{name}.kicad_mod").write_text(
            f'(footprint "{name}" (version 20221018) (layer "F.Cu")\n ' + "\n ".join(pads) +
            f'\n (fp_rect (start {x0} {y0}) (end {x1} {y1}) (layer "F.CrtYd")))\n')

    mod("Package_TO_SOT_SMD", "TO-263-5_TabPin3",
        [smd(i + 1, -4.2, -3.4 + 1.7 * i, 2.4, 1.0) for i in range(5)] + [smd(3, 2.2, 0, 6.5, 9.4)],
        (-5.6, -5.4, 6.0, 5.4))
    mod("Capacitor_THT", "CP_Radial_D12.5mm_P5.00mm", [thru(1, 0, 0), thru(2, 5, 0)], (-4.0, -6.6, 9.0, 6.6))
    mod("Capacitor_THT", "CP_Radial_D8.0mm_P3.50mm", [thru(1, 0, 0), thru(2, 3.5, 0)], (-3.0, -4.4, 6.5, 4.4))
    mod("Capacitor_THT", "CP_Radial_D10.0mm_P5.00mm", [thru(1, 0, 0), thru(2, 5, 0)], (-3.0, -5.4, 8.0, 5.4))
    mod("Inductor_SMD", "L_Bourns-SRR1260", [smd(1, -4.6, 0, 3.0, 3.6), smd(2, 4.6, 0, 3.0, 3.6)],
        (-6.5, -6.5, 6.5, 6.5))
    mod("Diode_SMD", "D_SMA", [smd(1, -2.0, 0, 1.8, 2.2), smd(2, 2.0, 0, 1.8, 2.2)], (-3.4, -1.8, 3.4, 1.8))


def net_voltage_of(spec, net):
    from pcb.compose import net_voltages, validate_spec
    clean, errors, _ = validate_spec(spec)
    assert not errors, errors
    return net_voltages(clean)[net]


class BuckTests(unittest.TestCase):
    SPEC = {"title": "12V to 5V", "blocks": [
        {"id": "pwr", "type": "power_in_terminal", "params": {"voltage": 12}, "nets": {"VIN": "VIN12", "GND": "GND"}},
        {"id": "buck", "type": "buck_lm2596", "params": {"vout": 5, "iout_max_a": 1.0},
         "nets": {"IN": "VIN12", "OUT": "V5", "GND": "GND"}},
        {"id": "led", "type": "led_indicator", "nets": {"V": "V5", "GND": "GND"}}]}

    def setUp(self):
        import os
        self._tmp = tempfile.TemporaryDirectory()
        _write_fake_library(Path(self._tmp.name))
        self._old = os.environ.get("PCB_FOOTPRINT_DIR")
        os.environ["PCB_FOOTPRINT_DIR"] = self._tmp.name

    def tearDown(self):
        import os
        if self._old is None:
            os.environ.pop("PCB_FOOTPRINT_DIR", None)
        else:
            os.environ["PCB_FOOTPRINT_DIR"] = self._old
        self._tmp.cleanup()

    def _spec_at(self, amps):
        return {**self.SPEC, "blocks": [self.SPEC["blocks"][0],
                {**self.SPEC["blocks"][1], "params": {"vout": 5, "iout_max_a": amps}}, self.SPEC["blocks"][2]]}

    def test_buck_board_routes_with_wide_power_tracks_and_passes_drc_lite(self):
        with tempfile.TemporaryDirectory() as d:
            report = build_board(self._spec_at(2.0), Path(d), run_kicad=False)
        self.assertTrue(report["routed"], report["stages"]["route"])
        self.assertTrue(report["stages"]["drc_lite"]["ok"], report["stages"]["drc_lite"])
        wide = report["stages"]["route"]["wide_nets"]
        self.assertGreaterEqual(wide.get("buck.SW", 0), 0.9)       # 2 A switch node
        self.assertGreaterEqual(wide.get("V5", 0), 0.9)            # 2 A output rail
        self.assertGreaterEqual(wide.get("VIN12", 0), 0.4)         # ~1 A average input at 12 V
        self.assertTrue(any("wide" in n.lower() for n in report["notes"]))
        self.assertTrue(any("LM2596" in r for r in report["review_items"]))

    def test_one_amp_board_also_builds(self):
        with tempfile.TemporaryDirectory() as d:
            report = build_board(self.SPEC, Path(d), run_kicad=False)
        self.assertTrue(report["routed"] and report["stages"]["drc_lite"]["ok"])
        self.assertGreaterEqual(report["stages"]["route"]["wide_nets"].get("buck.SW", 0), 0.4)

    @staticmethod
    def _ohms(text):
        return float(text[:-1]) * 1000 if text.endswith("k") else float(text)

    def test_calculated_values_match_the_formulas(self):
        design = compose(self.SPEC)
        ind = next(c for c in design.components if c.ref.startswith("L"))
        # 12 V (+10 %) -> 5 V at 1 A, 40 % ripple at 150 kHz needs ~52 uH -> next standard value 68 uH
        self.assertTrue(ind.value.startswith("68uH"), ind.value)
        r1, r2 = [self._ohms(c.value) for c in design.components
                  if c.block_id == "buck" and c.ref.startswith("R")]
        self.assertAlmostEqual(1.23 * (1 + r2 / r1), 5.0, delta=0.1)
        self.assertTrue(1000 <= r1 <= 5100)
        out = next(c for c in design.components if c.ref.startswith("C") and "220uF" in c.value)
        self.assertIn("25V", out.value)          # >= 1.5x a 5 V output
        # the downstream rail carries the real (quantised) output voltage, not the nominal one
        self.assertAlmostEqual(net_voltage_of(self.SPEC, "V5"), 5.0, delta=0.1)

    def test_higher_current_needs_a_smaller_inductor_value_and_bigger_rating(self):
        a = compose({**self.SPEC, "blocks": [self.SPEC["blocks"][0],
                     {**self.SPEC["blocks"][1], "params": {"vout": 5, "iout_max_a": 2.0}}, self.SPEC["blocks"][2]]})
        b = compose(self.SPEC)
        la = next(c for c in a.components if c.ref.startswith("L")).value
        lb = next(c for c in b.components if c.ref.startswith("L")).value
        self.assertLessEqual(int(la.split("uH")[0]), int(lb.split("uH")[0]))
        self.assertGreater(float(la.split(">=")[1].rstrip("A")), float(lb.split(">=")[1].rstrip("A")))

    def test_param_limits(self):
        for params in ({"vout": 25}, {"vout": 1.0}, {"iout_max_a": 3.0}):
            spec = {"title": "t", "blocks": [self.SPEC["blocks"][0],
                    {**self.SPEC["blocks"][1], "params": params}, self.SPEC["blocks"][2]]}
            with self.assertRaises(SpecError, msg=str(params)):
                compose(spec)

    def test_erc_rejects_input_outside_the_supported_range(self):
        for volts, text in ((35, "at most 30.0 V"), (5, "at least 7.0 V")):
            spec = {"title": "t", "blocks": [
                {**self.SPEC["blocks"][0], "params": {"voltage": volts}}, *self.SPEC["blocks"][1:]]}
            _, errors, _ = validate_spec(spec)
            self.assertTrue(any(text in e for e in errors), (volts, errors))

    def test_without_the_library_it_fails_loudly(self):
        import os
        os.environ["PCB_FOOTPRINT_DIR"] = "/nonexistent"
        if footprints.library_dir() is not None:
            self.skipTest("a real KiCad library is installed")
        with self.assertRaises(SpecError) as cm:
            compose(self.SPEC)
        self.assertIn("not available", str(cm.exception))


class RouterWidthTests(unittest.TestCase):
    def test_load_current_sets_track_width_and_stays_drc_clean(self):
        spec = spec_of({"id": "sw", "type": "mosfet_switch", "params": {"load_a": 2.0},
                        "nets": {"V": "VIN5", "GND": "GND", "GATE": "CTL"}},
                       {"id": "brk", "type": "header_breakout", "params": {"pins": 1},
                        "nets": {"GND": "GND", "P1": "CTL"}})
        with tempfile.TemporaryDirectory() as d:
            report = build_board(spec, Path(d), run_kicad=False)
        self.assertTrue(report["routed"])
        self.assertTrue(report["stages"]["drc_lite"]["ok"])
        self.assertGreaterEqual(report["stages"]["route"]["wide_nets"]["VIN5"], 0.9)

    def test_width_follows_ipc2221(self):
        from pcb.model import width_for_current
        self.assertEqual(width_for_current(0.3), 0.25)      # default width is enough
        self.assertGreaterEqual(width_for_current(2.0), 0.9)
        self.assertGreater(width_for_current(3.0), width_for_current(2.0))


class BlockLibraryTests(unittest.TestCase):
    def test_every_block_has_description_and_ports(self):
        for name, b in BLOCKS.items():
            self.assertTrue(b.description, name)
            self.assertTrue(b.ports, name)

    def test_review_blocks_say_why(self):
        for name, b in BLOCKS.items():
            if b.review_required:
                self.assertTrue(b.notes, name)


if __name__ == "__main__":
    unittest.main()

import unittest

import symbols
import smart_parser


class SymbolTests(unittest.TestCase):
    def test_ascii_text_is_unchanged(self):
        self.assertEqual(symbols.normalize("L-bracket 50 mm wide"), "L-bracket 50 mm wide")

    def test_electronics_units(self):
        self.assertEqual(symbols.normalize("4.7kΩ and 10µF and 2.2μH"), "4.7kohm and 10uF and 2.2uH")
        self.assertEqual(symbols.normalize("470Ω"), "470ohm")

    def test_mechanical_signs(self):
        self.assertEqual(symbols.normalize("Ø12 shaft, 45° chamfer, 50×60"), "diameter 12 shaft, 45 deg chamfer, 50x60")
        self.assertEqual(symbols.normalize("±0.1 at 25°C"), "+/-0.1 at 25 degC")

    def test_greek_letters_become_words(self):
        self.assertEqual(symbols.normalize("α = 20"), "alpha = 20")
        self.assertEqual(symbols.normalize("5α"), "5 alpha")
        self.assertEqual(symbols.normalize("θ and β"), "theta and beta")

    def test_history_only_changes_user_messages(self):
        h = [{"role": "user", "content": "gear α=20"}, {"role": "assistant", "content": "ok α"}]
        out = symbols.normalize_history(h)
        self.assertEqual(out[0]["content"], "gear alpha=20")
        self.assertEqual(out[1]["content"], "ok α")
        self.assertEqual(h[0]["content"], "gear α=20")          # the input is not modified

    def test_gear_pressure_angle_by_alpha_and_by_words(self):
        for text in ("spur gear module 2, 24 teeth, 10 mm thick, pressure angle α = 25",
                     "spur gear module 2, 24 teeth, 10 mm thick, 25 degree pressure angle"):
            r = smart_parser.parse_description(text)
            self.assertEqual(r.part_type, "gear")
            self.assertEqual(r.parameters.get("pressure_angle"), 25.0, text)

    def test_gear_without_angle_keeps_template_default(self):
        r = smart_parser.parse_description("spur gear module 2, 24 teeth, 10 mm thick")
        self.assertNotIn("pressure_angle", r.parameters)


if __name__ == "__main__":
    unittest.main()

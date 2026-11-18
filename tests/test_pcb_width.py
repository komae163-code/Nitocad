import unittest

from pcb import planner
from pcb.intent import board_width_mm


class BoardWidthTests(unittest.TestCase):
    def test_phrases(self):
        cases = {
            "pcb with max width 60 mm": 60.0,
            "maximum board width of 45mm": 45.0,
            "board width: 5 cm": 50.0,
            "pcb 70 mm wide": 70.0,
            "a pcb no wider than 55 mm wide": 55.0,
            "width of the board is 90": 90.0,
        }
        for text, want in cases.items():
            self.assertEqual(board_width_mm(text), want, text)

    def test_not_a_board_width(self):
        self.assertIsNone(board_width_mm("a pcb with a 5 mm wide trace"))
        self.assertIsNone(board_width_mm("pcb with a status led"))

    def test_clamped_to_the_range_the_placer_works_in(self):
        self.assertEqual(board_width_mm("pcb max width 500 mm"), 200.0)
        self.assertEqual(board_width_mm("pcb max width 5 mm"), 20.0)

    def test_latest_mention_wins(self):
        self.assertEqual(board_width_mm("pcb max width 60 mm | change it: max width 90 mm"), 90.0)

    def test_planner_uses_it(self):
        r = planner.keyword_plan("pcb with a 3.3v regulator and a status led, max width 60 mm")
        self.assertEqual(r.spec["board"]["max_width_mm"], 60.0)
        self.assertTrue(any("60" in n for n in r.notes))

    def test_default_is_unchanged(self):
        r = planner.keyword_plan("pcb with a 3.3v regulator and a status led")
        self.assertEqual(r.spec["board"]["max_width_mm"], 80.0)


if __name__ == "__main__":
    unittest.main()

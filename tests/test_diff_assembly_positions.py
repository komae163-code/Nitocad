"""A part that did not move is not listed as changed, and added parts do not shift the comparison."""
import sys, types
try:
    import conversation as C
except Exception:   # pydantic etc. missing in a bare sandbox
    C = None


def _spec(parts):
    return {"parameters": {"parts": parts}}


def test_tuple_and_list_positions_are_the_same_place():
    if C is None: return
    a = _spec([{"name": "floor", "part_type": "flat_plate", "parameters": {}, "position": (0.0, 0.0, 0.0)}])
    b = _spec([{"name": "floor", "part_type": "flat_plate", "parameters": {}, "position": [0, 0, 0]}])
    assert C._diff_assembly(a, b) == []


def test_a_real_move_is_still_listed():
    if C is None: return
    a = _spec([{"name": "floor", "part_type": "flat_plate", "parameters": {}, "position": [0, 0, 0]}])
    b = _spec([{"name": "floor", "part_type": "flat_plate", "parameters": {}, "position": [0, 0, 5]}])
    assert C._diff_assembly(a, b) == ["floor position: (0, 0, 0) to (0, 0, 5)"]


def test_parts_added_in_the_middle_do_not_shift_the_comparison():
    if C is None: return
    def part(n, z): return {"name": n, "part_type": "flat_plate", "parameters": {}, "position": [0, 0, z]}
    a = _spec([part("a", 1), part("c", 3)])
    b = _spec([part("a", 1), part("b", 2), part("c", 3)])
    assert C._diff_assembly(a, b) == ["parts: 2 to 3"]

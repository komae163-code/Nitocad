"""build_hints turns a refused geometry build into plain reasons taken from the request's own numbers."""
import build_hints as bh

FAILED = "Post-build geometry verification failed: STL mesh is not watertight You were not charged."

ORIGINAL = ("Pillow block bearing housing in 6061 aluminium. Base 120 × 50 × 12 mm with four 10 mm holes on a 95 × 30 mm rectangle. "
            "A 62 mm diameter boss, 40 mm tall, centred on the base, with a 52 mm bore going all the way through. "
            "Two 5 mm holes in the top of the boss, 180° apart. Must fit in 130 × 60 × 55 mm and weigh under 250 g.")

REVISED = ("Pillow block bearing housing in 6061 aluminium. Base 120 × 64 × 12 mm with four 10 mm holes on a 95 × 30 mm rectangle. "
           "A 62 mm diameter boss, 40 mm tall, centred on the base, with a 48 mm bore going all the way through. "
           "Two 4 mm holes in the top of the boss, on a 55 mm circle, 180° apart. Must fit in 130 × 64 × 55 mm and weigh under 330 g.")


def test_parses_the_stated_numbers():
    p = bh.parse_request(ORIGINAL)
    assert p["base"] == (120, 50, 12) and p["envelope"] == (130, 60, 55)
    assert p["boss_d"] == 62 and p["boss_h"] == 40 and p["bore_d"] == 52
    assert p["base_holes"] == [{"n": 4, "d": 10, "rect": (95, 30)}]
    assert p["boss_holes"] == [{"n": 2, "d": 5, "pcd": None}]
    assert p["max_g"] == 250 and p["density"] == 2.70


def test_original_request_names_every_real_conflict():
    msg = bh.explain_failure(ORIGINAL, FAILED)
    assert "you were not charged" in msg
    assert "The bore is 52 mm wide, but the base is only 50 mm wide" in msg
    assert "at least 60 mm wide" in msg
    assert "A 62 mm boss does not fit in a 60 mm wide envelope" in msg
    assert "wall only 5 mm thick" in msg and "as wide as the wall" in msg
    assert "46 mm or smaller" in msg                     # bore that would leave room for 5 mm holes
    assert "watertight" not in msg                      # no mesh jargon


def test_original_weight_is_under_its_limit_so_it_is_not_blamed():
    assert "Weight limit" not in bh.explain_failure(ORIGINAL, FAILED)


def test_revised_request_has_no_geometry_conflict_but_a_tight_weight_check():
    assert bh.find_conflicts(REVISED) == []
    est = bh._estimate_mass_g(bh.parse_request(REVISED))
    assert 305 < est < 316                               # about 310 g


def test_weight_over_the_limit_is_reported_with_its_assumption():
    msg = bh.explain_failure(REVISED.replace("330 g", "250 g"), FAILED)
    assert "Weight limit" in msg and "about 311 g" in msg and "250 g limit" in msg
    assert "boss to stand 40 mm above the base" in msg


def test_nothing_readable_gives_the_general_explanation():
    msg = bh.explain_failure("make it bigger", FAILED)
    assert msg.startswith("I could not build a clean solid") and "fit inside the part" in msg


def test_other_failures_are_left_alone():
    assert bh.explain_failure(ORIGINAL, "Width must be positive. You were not charged.") is None

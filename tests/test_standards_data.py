"""Structural sanity of the standards tables + lookups + planner/validator integration.
Pins invariants, NOT the individual values: verify values against the licensed standards."""
import pytest

import standards_data as sd
from assembly_planner import default_catalog, planner_system_prompt, validate_spec
from validator import validate_parameters


class TestTables:
    def test_every_size_has_a_pitch_and_every_table_covers_the_same_sizes(self):
        sizes = set(sd.PITCH)
        for table in (sd.ISO_4762, sd.ISO_4017, sd.ISO_4032, sd.ISO_7089):
            assert set(table) == sizes

    def test_socket_head_geometry_is_physically_consistent(self):
        for size, (dk, k, s, t) in sd.ISO_4762.items():
            d = float(size[1:])
            assert dk > d and 0 < t < k and s < dk and k <= d * 1.01

    def test_hex_head_and_nut_fit_the_thread(self):
        for size in sd.PITCH:
            d = float(size[1:])
            assert sd.ISO_4017[size][0] > d and sd.ISO_4032[size][0] > d
            assert sd.ISO_4032[size][1] > 0.5 * d  # nut thick enough to carry the thread
            assert sd.ISO_4017[size][0] == sd.ISO_4032[size][0]  # bolt and nut share wrench size

    def test_washer_clears_the_bolt_and_overhangs_the_head(self):
        for size, (d1, d2, h) in sd.ISO_7089.items():
            d = float(size[1:])
            assert d1 > d and d2 > d1 and 0 < h < d
            assert d2 >= sd.ISO_4762[size][0] * 0.9

    def test_bearings_are_well_formed_and_series_are_ordered(self):
        for des, (d, D, B) in sd.BEARINGS.items():
            assert D > d + 2 and 0 < B < D
        for series in ("60", "62", "63"):
            bores = [sd.BEARINGS[f"{series}{n:02d}"][0] for n in range(5, 11) if f"{series}{n:02d}" in sd.BEARINGS]
            assert bores == sorted(bores)
        # heavier series, same bore -> larger OD and width
        for n in range(0, 11):
            a, b, c = (sd.BEARINGS.get(f"{p}{n:02d}") for p in ("60", "62", "63"))
            assert a[1] < b[1] < c[1] and a[0] == b[0] == c[0]

    def test_standard_lengths_sorted_unique(self):
        assert sd.STANDARD_LENGTHS == sorted(set(sd.STANDARD_LENGTHS))


class TestLookups:
    def test_fastener_lookup_normalizes(self):
        spec = sd.lookup_fastener("iso 4762", " m8 ")
        assert spec["standard"] == "ISO_4762" and spec["size"] == "M8" and spec["d"] == 8.0

    def test_unknown_fastener_lists_valid_choices(self):
        with pytest.raises(ValueError) as e:
            sd.lookup_fastener("ISO_4762", "M7")
        assert "M8" in str(e.value)
        with pytest.raises(ValueError):
            sd.lookup_fastener("DIN_999", "M8")

    def test_nearest_standard_length(self):
        assert sd.nearest_standard_length(31) == 30
        assert sd.nearest_standard_length(100) == 100
        assert sd.nearest_standard_length(47) in (45, 50)

    @pytest.mark.parametrize("name", ["6205", "6205-2RS", "6205 ZZ", "6205-2RS C3", " 608 "])
    def test_bearing_suffixes_do_not_change_dimensions(self, name):
        assert sd.lookup_bearing(name)["designation"] in ("6205", "608")

    def test_unknown_bearing(self):
        with pytest.raises(ValueError):
            sd.lookup_bearing("9999")


class TestIntegration:
    def test_new_families_are_in_the_planner_catalog(self):
        cat = default_catalog()
        assert {"standard", "size", "length_mm"} <= set(cat["fastener"])
        assert "designation" in cat["std_bearing"]

    def test_prompt_carries_standard_choices(self):
        text = planner_system_prompt()
        assert "ISO_4032" in text and "ISO 15" in text

    def test_validator_snaps_length_and_normalizes_size(self):
        corrected, result = validate_parameters("fastener", {"standard": "ISO_4762", "size": "m8", "length_mm": 31})
        assert corrected["length_mm"] == 30.0 and corrected["size"] == "M8" and result.warnings

    def test_validator_rejects_unknown_size_and_designation(self):
        _, r = validate_parameters("fastener", {"standard": "ISO_4762", "size": "M7"})
        assert r.errors
        _, r = validate_parameters("std_bearing", {"designation": "1234"})
        assert r.errors

    def test_planner_rejects_a_bad_standard_part(self):
        _, errors = validate_spec({"parts": [{"name": "bolt", "part_type": "fastener",
                                              "parameters": {"standard": "ISO_4762", "size": "M7"}}]})
        assert errors
        parts, errors = validate_spec({"parts": [{"name": "bolt", "part_type": "fastener",
                                                  "parameters": {"standard": "ISO_4762", "size": "M8", "length_mm": 32}}]})
        assert errors == [] and parts[0]["parameters"]["length_mm"] == 30.0


class TestKeys:
    def test_ranges_are_contiguous_and_cover_6_to_110(self):
        rows = sd.KEY_TABLE
        assert rows[0][0] == 6 and rows[-1][1] == 110
        for prev, nxt in zip(rows, rows[1:]):
            assert prev[1] == nxt[0]

    def test_cross_sections_grow_with_the_shaft(self):
        bs = [r[2] for r in sd.KEY_TABLE]
        assert bs == sorted(bs) and len(set(bs)) == len(bs)

    def test_keyway_depths_are_consistent_with_key_height(self):
        for lo, hi, b, h, t1, t2 in sd.KEY_TABLE:
            assert h < t1 + t2 <= h + 0.5   # key sits in both keyways with a small clearance
            assert t1 > h / 2               # shaft carries the larger share
            assert b in sd.KEY_LENGTH_RANGE and h <= b * 1.2 + 0.01

    def test_length_ranges_use_standard_lengths(self):
        for b, (lo, hi) in sd.KEY_LENGTH_RANGE.items():
            assert lo in sd.KEY_LENGTHS and hi in sd.KEY_LENGTHS and lo < hi
            assert lo > b  # round-ended key needs length > width

    def test_lookup_boundaries(self):
        assert sd.lookup_key(25)["b"] == 8 and sd.lookup_key(22)["b"] == 6 and sd.lookup_key(22.1)["b"] == 8
        assert sd.lookup_key(110)["b"] == 28
        for bad in (6, 5, 110.1):
            with pytest.raises(ValueError):
                sd.lookup_key(bad)

    def test_standard_key_length_respects_range(self):
        spec = sd.lookup_key(25)  # 8x7, 18..90
        assert sd.standard_key_length(41, spec["length_min"], spec["length_max"]) == 40
        assert sd.standard_key_length(5, spec["length_min"], spec["length_max"]) == 18
        assert sd.standard_key_length(500, spec["length_min"], spec["length_max"]) == 90

    def test_validator_and_planner(self):
        corrected, r = validate_parameters("key", {"shaft_diameter_mm": 25, "length_mm": 41})
        assert corrected["length_mm"] == 40.0 and r.warnings
        _, r = validate_parameters("key", {"shaft_diameter_mm": 150})
        assert r.errors
        _, r = validate_parameters("key", {"shaft_diameter_mm": 25, "form": "C"})
        assert r.errors
        assert {"shaft_diameter_mm", "length_mm", "form"} <= set(default_catalog()["key"])
        assert "DIN 6885" in planner_system_prompt()


class TestFlangesAndORings:
    def test_tables_cover_the_same_sizes_and_grow(self):
        assert list(sd.FLANGE_ASME_150_IN) == list(sd.PIPE_OD_MM)
        ods = [r[0] for r in sd.FLANGE_ASME_150_IN.values()]
        thick = [r[1] for r in sd.FLANGE_ASME_150_IN.values()]
        pipe = list(sd.PIPE_OD_MM.values())
        assert ods == sorted(ods) and thick == sorted(thick) and pipe == sorted(pipe)

    def test_flange_geometry_is_physically_consistent(self):
        for nps, (od, t, pcd, n, hole, rf) in sd.FLANGE_ASME_150_IN.items():
            assert pcd / 2 + hole / 2 < od / 2 - 0.05          # bolt holes stay inside the rim
            assert rf / 2 < pcd / 2 - hole / 2                   # raised face clears the bolt holes
            assert n in (4, 8, 12) and 0 < t < od / 4
            assert sd.PIPE_OD_MM[nps] / 25.4 < rf                # slip-on bore sits inside the raised face

    def test_lookup_converts_inches_and_normalizes_input(self):
        spec = sd.lookup_flange("asme b16.5 150", "2 1/2", "Slip-On")
        assert spec["standard"] == "ASME_B16_5_150" and spec["nps"] == "2-1/2" and spec["flange_type"] == "slip_on"
        four = sd.lookup_flange("ASME_B16_5_150", "4")
        assert four["od"] == 228.6 and four["bolt_count"] == 8 and four["bore"] == 114.3
        assert sd.lookup_flange("ASME_B16_5_150", '4"', "blind")["bore"] == 0.0

    def test_unknown_flange_inputs_list_valid_choices(self):
        for args in (("DIN_1", "4"), ("ASME_B16_5_150", "7"), ("ASME_B16_5_150", "4", "weld_neck")):
            with pytest.raises(ValueError):
                sd.lookup_flange(*args)

    def test_o_ring_series_and_validator(self):
        assert sd.is_standard_o_ring_cross_section(3.53) and sd.is_standard_o_ring_cross_section(5.3)
        assert not sd.is_standard_o_ring_cross_section(4.0)
        _, r = validate_parameters("o_ring", {"inner_diameter_mm": 30, "cross_section_mm": 3.53})
        assert not r.errors and not r.warnings
        _, r = validate_parameters("o_ring", {"inner_diameter_mm": 30, "cross_section_mm": 4.0})
        assert r.errors
        _, r = validate_parameters("o_ring", {"inner_diameter_mm": 5, "cross_section_mm": 3.53})
        assert not r.errors and r.warnings
        _, r = validate_parameters("o_ring", {"inner_diameter_mm": -1, "cross_section_mm": 3.53})
        assert r.errors

    def test_flange_validator_and_planner(self):
        corrected, r = validate_parameters("asme_flange", {"standard": "asme b16.5 150", "nps": "2 1/2", "flange_type": "slip_on"})
        assert not r.errors and corrected["nps"] == "2-1/2" and corrected["standard"] == "ASME_B16_5_150"
        _, r = validate_parameters("asme_flange", {"nps": "7"})
        assert r.errors
        cat = default_catalog()
        assert {"standard", "nps", "flange_type"} <= set(cat["asme_flange"])
        assert {"inner_diameter_mm", "cross_section_mm"} <= set(cat["o_ring"])
        text = planner_system_prompt()
        assert "ASME B16.5" in text and "O-ring" in text

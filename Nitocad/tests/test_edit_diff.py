"""edit_diff: what an edit changed on the build, compared with the version before it."""
import edit_diff as ed


def _asm(overlaps=(), groups=0, unchecked=0, parts=2, mass=30.0, size=(60, 22, 22)):
    return {"size_mm": list(size), "mass_g": mass, "min_wall_mm": None, "material": None,
            "assembly": {"bom": {"part_count": parts},
                         "interferences": [{"a": a, "b": b, "volume_mm3": v, "severity": s} for a, b, v, s in overlaps],
                         "disconnected_groups": [["x"]] * groups, "unchecked_pairs": [{}] * unchecked}}


def test_nothing_changed_gives_no_diff():
    assert ed.build([], _asm(), _asm(), [], []) is None


def test_size_and_mass_rows():
    rows = ed.fact_changes(_asm(), _asm(size=(80, 22, 22), mass=38.5))
    labels = {r["label"]: r for r in rows}
    assert labels["Overall size"]["before"] == "60 x 22 x 22 mm" and labels["Overall size"]["after"] == "80 x 22 x 22 mm"
    assert labels["Mass"]["before"] == "30 g" and labels["Mass"]["after"] == "38.5 g"
    assert all(r["direction"] is None for r in rows)


def test_a_new_overlap_is_flagged_and_a_cleared_one_is_good_news():
    rows = ed.fact_changes(_asm(), _asm(overlaps=[("gear", "shaft", 12.5, "major")]))
    assert rows[0]["label"] == "New overlap" and rows[0]["direction"] == "worse" and "gear and shaft" in rows[0]["after"]
    rows = ed.fact_changes(_asm(overlaps=[("shaft", "gear", 5, "minor")]), _asm())
    assert rows[0]["label"] == "Overlap cleared" and rows[0]["direction"] == "better"


def test_the_same_overlap_in_either_name_order_is_not_new():
    a = _asm(overlaps=[("gear", "shaft", 5, "minor")])
    b = _asm(overlaps=[("shaft", "gear", 5, "minor")])
    assert ed.fact_changes(a, b) == []


def test_groups_and_unchecked_pairs():
    rows = ed.fact_changes(_asm(), _asm(groups=1, unchecked=2))
    by = {r["label"]: r for r in rows}
    assert by["Separate groups"]["direction"] == "worse" and by["Pairs not checked"]["after"] == 2


def test_single_part_rows():
    old = {"size_mm": [50, 60, 40], "mass_g": 20.0, "min_wall_mm": 3, "material": "6061"}
    new = {"size_mm": [50, 60, 40], "mass_g": 21.5, "min_wall_mm": 2, "material": "6061"}
    rows = {r["label"]: r for r in ed.fact_changes(old, new)}
    assert rows["Thinnest wall"]["direction"] == "worse" and rows["Mass"]["after"] == "21.5 g" and "Overall size" not in rows


def test_unknown_before_or_after_is_not_reported_as_a_change():
    assert ed.fact_changes({"size_mm": None, "mass_g": None}, {"size_mm": [1, 2, 3], "mass_g": 4}) == []
    assert ed.fact_changes(None, _asm()) == [] and ed.fact_changes(_asm(), None) == []


def test_requirement_rows():
    old = [{"id": 1, "text": "mass at most 40 g", "status": "pass"}, {"id": 2, "text": "walls", "status": "unknown"}]
    new = [{"id": 1, "text": "mass at most 40 g", "status": "fail"}, {"id": 2, "text": "walls", "status": "unknown"},
           {"id": 3, "text": "brand new", "status": "fail"}]
    rows = ed.requirement_changes(old, new)
    assert len(rows) == 1 and rows[0]["direction"] == "worse" and rows[0]["before"] == "pass"
    back = ed.requirement_changes(new[:1], old[:1])
    assert back[0]["direction"] == "better"


def test_build_and_warning_sentence():
    d = ed.build(["shaft length_mm: 60 to 80"], _asm(), _asm(overlaps=[("gear", "shaft", 12.5, "major")], mass=45),
                 [{"id": 1, "text": "mass at most 40 g", "status": "pass"}], [{"id": 1, "text": "mass at most 40 g", "status": "fail"}], 1, 2)
    assert d["worse"] and d["from_version"] == 1 and d["to_version"] == 2 and d["spec"] == ["shaft length_mm: 60 to 80"]
    s = ed.warning_sentence(d)
    assert s.startswith(" Check this:") and "new overlap between gear and shaft" in s and "mass at most 40 g" in s
    s2 = ed.warning_sentence(d, requirements=False)
    assert "overlap" in s2 and "mass at most" not in s2


def test_no_warning_when_nothing_got_worse():
    d = ed.build(["x: 1 to 2"], _asm(), _asm(size=(80, 22, 22)))
    assert d and not d["worse"] and ed.warning_sentence(d) == "" and ed.warning_sentence(None) == ""


def test_rows_are_capped():
    many = [(f"a{i}", f"b{i}", 1, "minor") for i in range(20)]
    assert len(ed.fact_changes(_asm(), _asm(overlaps=many))) <= ed.MAX_ROWS

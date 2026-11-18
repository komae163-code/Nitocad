"""Inspection and fixing for assemblies and circuit boards, and the questions card across every kind of design."""
import design_inspect as DI

U = lambda t: {"role": "user", "content": t}          # noqa: E731
A = lambda t: {"role": "assistant", "content": t}     # noqa: E731


def built(interferences=(), groups=(), unchecked=()):
    return {"success": True, "assembly_checks": {"interferences": list(interferences), "disconnected_groups": list(groups),
                                                   "unchecked_pairs": list(unchecked)}}


def hit(a, b, severity="major", share=0.3):
    return {"a": a, "b": b, "volume_mm3": 100.0, "share_of_smaller_part": share, "severity": severity}


# ------------------------------------------------------------------------------ assemblies
def test_serious_overlaps_and_loose_parts_are_faults_and_small_overlaps_are_notes():
    f = DI.assess_assembly(built([hit("shaft", "gear"), hit("pin", "plate", "minor", 0.01)], [["frame", "plate"], ["handle"]]))
    codes = {x["code"]: x for x in f}
    assert codes["overlap"]["severity"] == "fix" and codes["overlap"]["parts"] == ["gear", "shaft"]
    assert codes["small_overlap"]["severity"] == "note"
    assert codes["floating"]["severity"] == "fix" and codes["floating"]["parts"] == ["handle"]      # the biggest group is the body
    assert "handle" in codes["floating"]["instruction"]


def test_a_clean_assembly_and_a_non_assembly_have_no_findings():
    assert DI.assess_assembly(built()) == [] and DI.assess_assembly({"success": True}) == []
    assert [x["code"] for x in DI.assess_assembly(built(unchecked=[{"a": "x", "b": "y"}]))] == ["unchecked"]


def test_kinds_are_told_apart():
    assert DI.kind_of({"part_type": "pcb_board"}, {}) == "pcb"
    assert DI.kind_of({"part_type": "assembly"}, built()) == "assembly"
    assert DI.kind_of({"part_type": "bracket"}, {"success": True}) is None and DI.kind_of(None, None) is None


def run_assembly(builds, replans=None, max_passes=2):
    """builds: the results rebuild() returns in order; replans: the specs the planner returns in order."""
    calls = {"rebuild": 0, "replan": []}
    b, r = list(builds), list(replans if replans is not None else [])

    def rebuild(spec):
        calls["rebuild"] += 1
        return b.pop(0) if b else None

    def replan(text, spec):
        calls["replan"].append(text)
        return r.pop(0) if r else None
    rep = DI.refine("assembly", {"v": 0}, built([hit("a", "b")]), rebuild=rebuild, replan=replan if replans is not None else None,
                    max_passes=max_passes)
    return rep, calls


def test_a_fix_that_clears_the_overlap_is_kept_and_checked_again():
    rep, calls = run_assembly([built()], [{"v": 1}])
    assert rep["changed"] and rep["spec"] == {"v": 1} and rep["findings"] == [] and calls["rebuild"] == 1
    assert "a and b" in calls["replan"][0] and "Fixed" in rep["summary"] and "Nothing wrong" in rep["summary"]


def test_a_fix_that_does_not_improve_is_dropped_and_the_original_comes_back():
    rep, _ = run_assembly([built([hit("a", "b")])], [{"v": 1}])
    assert not rep["changed"] and rep["spec"] == {"v": 0} and "Still open" in rep["summary"]
    worse, _ = run_assembly([built([hit("a", "b"), hit("c", "d")])], [{"v": 1}])
    assert not worse["changed"]


def test_at_most_two_fixes_and_no_ai_means_no_fix_but_the_findings_are_still_reported():
    rep, calls = run_assembly([built([hit("a", "b")]), built([hit("a", "b")]), built()], [{"v": 1}, {"v": 2}, {"v": 3}])
    assert len(calls["replan"]) <= 2
    none, calls2 = run_assembly([built()], None)                          # no planner (the Engine plan)
    assert not none["changed"] and calls2["rebuild"] == 0 and none["findings"] and "Still open" in none["summary"]


def test_a_planner_that_returns_nothing_or_a_build_that_fails_keeps_the_original():
    rep, _ = run_assembly([], [None])
    assert not rep["changed"] and rep["spec"] == {"v": 0}
    rep, _ = run_assembly([], [{"v": 1}])                                  # the rebuild fails (returns None)
    assert not rep["changed"] and rep["result"] == built([hit("a", "b")])


# ------------------------------------------------------------------------------ circuit boards
def board(width=80.0):
    return {"part_type": "pcb_board", "parameters": {"description": "d", "spec": {"title": "t", "blocks": [], "board": {"max_width_mm": width}}}}


def test_a_board_is_widened_by_a_third_inside_its_limits_and_nothing_else_changes():
    w = DI.widen_board(board(80))
    assert w["parameters"]["spec"]["board"]["max_width_mm"] == 104.0 and board(80)["parameters"]["spec"]["board"]["max_width_mm"] == 80
    assert DI.widen_board(board(190))["parameters"]["spec"]["board"]["max_width_mm"] == 200.0
    assert DI.widen_board(board(200)) is None and DI.widen_board({"part_type": "bracket"}) is None
    assert DI.widen_board(board(20))["parameters"]["spec"]["board"]["max_width_mm"] == 26.0


def run_board(monkeypatch, by_width, rebuild_ok=True):
    seen = []

    def fake(spec):
        w = spec["board"]["max_width_mm"]
        seen.append(w)
        return by_width.get(w, [])
    monkeypatch.setattr(DI, "assess_pcb", fake)
    rebuilt = []

    def rebuild(spec):
        rebuilt.append(spec["parameters"]["spec"]["board"]["max_width_mm"])
        return {"success": True, "w": rebuilt[-1]} if rebuild_ok else None
    rep = DI.refine("pcb", board(80), {"success": True, "w": 80}, rebuild=rebuild)
    return rep, seen, rebuilt


BAD = [DI._finding("unrouted", "fix", [], "2 nets could not be routed: SDA, SCL", "")]


def test_a_wider_board_that_routes_is_kept_and_built_once(monkeypatch):
    rep, seen, rebuilt = run_board(monkeypatch, {80.0: BAD, 104.0: []})
    assert rep["changed"] and rep["spec"]["parameters"]["spec"]["board"]["max_width_mm"] == 104.0
    assert seen == [80.0, 104.0] and rebuilt == [104.0] and rep["result"]["w"] == 104.0       # the 3D board is built once, at the end
    assert "Fixed" in rep["summary"] and "Nothing wrong" in rep["summary"]


def test_a_wider_board_that_does_not_help_is_dropped_and_never_rebuilt(monkeypatch):
    rep, seen, rebuilt = run_board(monkeypatch, {80.0: BAD, 104.0: BAD, 135.2: BAD})
    assert not rep["changed"] and rep["spec"]["parameters"]["spec"]["board"]["max_width_mm"] == 80.0 and rebuilt == []
    assert len(seen) == 2 and "Still open" in rep["summary"]                                   # one try, then it stops


def test_a_failed_rebuild_of_the_wider_board_restores_the_original(monkeypatch):
    rep, _, _ = run_board(monkeypatch, {80.0: BAD, 104.0: []}, rebuild_ok=False)
    assert not rep["changed"] and rep["spec"]["parameters"]["spec"]["board"]["max_width_mm"] == 80.0 and rep["result"]["w"] == 80


def test_notes_alone_are_never_fixed(monkeypatch):
    note = [DI._finding("holes", "note", [], "1 holes problem", "")]
    rep, seen, rebuilt = run_board(monkeypatch, {80.0: note})
    assert not rep["changed"] and seen == [80.0] and rebuilt == [] and "Worth a look" in rep["summary"]


def test_a_board_that_cannot_be_checked_here_says_so_and_changes_nothing():
    f = DI.assess_pcb({"title": "t", "blocks": [{"nonsense": True}]})
    assert len(f) == 1 and f[0]["code"] == "not_checked" and f[0]["severity"] == "note"
    s = DI.summary("pcb", False, f, 1)
    assert "could not be checked here" in s


def test_a_real_small_board_passes_its_own_checks():
    from pcb import planner as pp
    spec = pp.keyword_plan("small LED blinker board with a 555 timer").spec
    assert DI.assess_pcb(spec) == []


def test_an_inspection_problem_never_loses_the_design():
    def boom(spec):
        raise RuntimeError("kernel fell over")
    rep = DI.refine("assembly", {"v": 0}, built([hit("a", "b")]), rebuild=boom, replan=lambda t, s: {"v": 1})
    assert not rep["changed"] and rep["spec"] == {"v": 0}


# ------------------------------------------------------------------------------ the questions card, every kind of design
def test_every_kind_of_design_gets_the_card_and_a_reply_builds():
    import build_scope as BS
    prompts = {"bracket": "I want a bracket", "plate": "I need a plate", "box": "I want a box", "gear": "I want a gear",
               "round": "I need a round bar", "pipe": "I want a pipe", "beam": "I need a beam", "hinge": "I want a hinge",
               "engine_part": "I want a piston", "wing": "I want a wing", "airframe": "I want a fuselage",
               "road": "I want a road", "culvert": "I want a culvert", "wall": "I want a concrete wall", "building": "I want a house",
               "fastener": "I want a bolt", "pcb": "I want a circuit board", "assembly": "I want an assembly"}
    asked, skipped = [], []
    for fam, text in prompts.items():
        got = BS.scope_turn([U(text)], None, has_ai=True)
        if got is None:
            skipped.append(fam)
            continue
        kind, plan = got
        assert kind == "ask" and plan.choices and all(c.get("card") for c in plan.choices), fam
        reply = BS.scope_turn([U(text), A(plan.reply), U(plan.choices[0]["question"] + " " + plan.choices[0]["options"][0])], None, has_ai=True)
        assert reply[0] == "build", fam
        asked.append(fam)
    assert skipped == [], skipped                                # every kind, no exceptions


def test_common_phrasings_get_the_card_and_words_inside_other_requests_do_not_hijack_it():
    import build_scope as BS
    for text, fam in (("I need a round bar", "round"), ("I want a piston", "engine_part"), ("a camshaft please", "engine_part"),
                      ("I want a brick wall", "wall"), ("build a garden wall", "wall"), ("I need a dowel", "round")):
        assert BS.family_of(text) == fam, text
        assert BS.scope_turn([U(text)], None, has_ai=True)[0] == "ask", text
    assert BS.family_of("a wall mount bracket") == "bracket"                  # the word "wall" alone is not a wall build
    assert BS.family_of("a box with 3 mm walls") == "box"
    assert BS.family_of("a connecting rod") == "engine_part"                  # the longer phrase still wins over "rod"


def test_an_assembly_fix_that_removes_a_part_is_dropped():
    spec = {"part_type": "assembly", "parameters": {"parts": [{"name": "tyre"}, {"name": "rim"}]}}
    rep = DI.refine("assembly", spec, built([hit("tyre", "rim")]), rebuild=lambda s: built(),
                    replan=lambda t, s: {"part_type": "assembly", "parameters": {"parts": [{"name": "rim"}]}})
    assert not rep["changed"] and rep["spec"] == spec
    rep = DI.refine("assembly", spec, built([hit("tyre", "rim")]), rebuild=lambda s: built(),
                    replan=lambda t, s: {"part_type": "assembly", "parameters": {"parts": [{"name": "tyre"}, {"name": "rim"}, {"name": "hub"}]}})
    assert rep["changed"]                                    # moving, resizing or adding is fine


def test_things_named_after_a_vehicle_that_are_not_vehicles_go_to_the_general_planner():
    import vehicle_chat as VC
    for text in ("a bike rack", "a car key holder", "a motorcycle helmet holder", "a jet engine", "a jet turbine blade"):
        assert VC.kind_of(text) is None and not VC.is_vehicle_request(text), text
    assert VC.kind_of("a bicycle") == "bicycle" and VC.kind_of("a motorbike") == "bicycle" and VC.kind_of("a tractor") == "truck"


def test_circuit_board_wording_is_recognised_and_boards_for_other_things_are_not_taken_for_a_board():
    import build_scope as BS
    for text in ("an Arduino shield", "a motor driver board", "a sensor board", "a power supply board", "a LED matrix", "a dev board"):
        assert BS.family_of(text) == "pcb", text
    assert BS.family_of("a bracket for a PCB") == "bracket" and BS.family_of("a box for a circuit board") == "box"

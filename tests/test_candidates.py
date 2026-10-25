"""Candidate designs: thin, standard and heavy versions of a bracket. No CadQuery, no network."""
import copy

from _chat_fakes import BRACKET, fresh_store

import candidates as cand
import chat_service as svc
import chat_store as cs
from conversation import Plan

U = "0xuser"
V = "0xother"

MOTOR = {"part_type": "motor_mount", "material": "6061", "operations": [],
         "parameters": {"motor_size_mm": 50, "thickness_mm": 4, "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.5}}
L_BRACKET = {"part_type": "l_bracket", "material": "6061", "operations": [],
             "parameters": {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 5,
                            "hole_count": 2, "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.5}}


def ids(out):
    return [c["id"] for c in out["candidates"]]


def thickness(out, cid):
    return next(c for c in out["candidates"] if c["id"] == cid)["spec"]["parameters"]["thickness_mm"]


# ------------------------------------------------------------- pure rules ---

def test_only_motor_mounts_and_l_brackets_have_candidates():
    assert cand.applies(MOTOR) and cand.applies(L_BRACKET)
    assert not cand.applies({"part_type": "gear", "parameters": {}})
    assert not cand.applies(None)


def test_three_versions_in_order_and_thicker_means_heavier_plate():
    out = cand.variants(L_BRACKET)
    assert ids(out) == ["thin", "standard", "heavy"]
    t = [thickness(out, i) for i in ids(out)]
    assert t[0] < t[1] < t[2] and t[1] == 5


def test_standard_is_the_spec_as_given_and_the_input_is_not_changed():
    before = copy.deepcopy(L_BRACKET)
    out = cand.variants(L_BRACKET)
    assert L_BRACKET == before
    std = next(c for c in out["candidates"] if c["id"] == "standard")["spec"]
    assert std["parameters"] == L_BRACKET["parameters"] and std["material"] == "6061"


def test_only_thickness_and_fillet_differ_between_versions():
    out = cand.variants(L_BRACKET)
    keep = {k: v for k, v in L_BRACKET["parameters"].items() if k not in ("thickness_mm", "fillet_radius_mm")}
    for c in out["candidates"]:
        p = c["spec"]["parameters"]
        assert {k: p[k] for k in keep} == keep


def test_fillet_always_stays_under_half_the_thickness():
    for spec in (MOTOR, L_BRACKET):
        for c in cand.variants(spec)["candidates"]:
            p = c["spec"]["parameters"]
            assert p["fillet_radius_mm"] < p["thickness_mm"] / 2


def test_l_bracket_thin_never_goes_below_its_hole():
    spec = copy.deepcopy(L_BRACKET)
    spec["parameters"].update(thickness_mm=4, hole_diameter_mm=3.2)
    out = cand.variants(spec)
    for c in out["candidates"]:
        assert c["spec"]["parameters"]["thickness_mm"] > 3.2  # validator would shrink the hole otherwise


def test_a_version_with_no_room_is_skipped_with_a_reason_not_faked():
    spec = copy.deepcopy(L_BRACKET)
    spec["parameters"].update(thickness_mm=3.5, hole_diameter_mm=3.2)  # no room to go thinner than 3.7
    out = cand.variants(spec)
    assert "thin" not in ids(out) and "thin" in out["skipped"]
    assert "heavy" in ids(out)


def test_heavy_respects_the_third_of_smallest_side_limit():
    spec = copy.deepcopy(L_BRACKET)
    spec["parameters"].update(width_mm=30, height_mm=60, depth_mm=40, thickness_mm=8)  # limit is 10
    out = cand.variants(spec)
    assert thickness(out, "heavy") <= 10.0


def test_missing_thickness_uses_the_template_default_and_writes_it_in():
    spec = {"part_type": "motor_mount", "parameters": {"motor_size_mm": 50}, "material": None, "operations": []}
    out = cand.variants(spec)
    assert thickness(out, "standard") == 3.0


def test_unsupported_part_raises_a_readable_error():
    try:
        cand.variants({"part_type": "gear", "parameters": {}})
    except cand.CandidateError as exc:
        assert "motor mounts" in str(exc)
    else:
        raise AssertionError("expected CandidateError")


def test_a_message_that_already_says_how_thick_gets_no_offer():
    assert cand.user_stated_thickness("a 3 mm thick motor bracket")
    assert cand.user_stated_thickness("make me a heavy duty L bracket")
    assert cand.user_stated_thickness("a thin mounting bracket")
    assert not cand.user_stated_thickness("a motor bracket for a 60 mm motor")


def test_comparison_uses_mass_when_every_version_has_it_else_volume():
    def e(t, mass, vol):
        c = {"id": "x", "label": "X", "thickness_mm": t}
        return {**c, "summary": cand.summarize(c, None, {"mass_g": mass, "volume_mm3": vol})}
    with_mass = [e(2, 10, 4000), e(3, 15, 6000)]
    assert cand.common_metric(with_mass) == "mass_g"
    mixed = [e(2, None, 4000), e(3, 15, 6000)]
    assert cand.common_metric(mixed) == "volume_cm3"
    none = [e(2, None, None), e(3, None, None)]
    assert cand.common_metric(none) is None and cand.ordering_is_sane(none)
    assert not cand.ordering_is_sane([e(3, 15, 6000), e(2, 10, 4000)])


# ------------------------------------------------------------ chat service ---

class World:
    """A fake generator whose volume grows with thickness, like a real plate."""
    def __init__(self, fail_for=None, wrong_order=False):
        self.generated, self.fail_for, self.wrong_order = [], fail_for, wrong_order

    def generate(self, desc, spec, user, source):
        self.generated.append((copy.deepcopy(spec), source))
        t = spec["parameters"].get("thickness_mm", 3)
        if self.fail_for is not None and t == self.fail_for:
            return {"success": False, "error": "boom"}
        vol = 10000.0 * t if not self.wrong_order else 90000.0 / t
        return {"success": True, "job_id": f"job{len(self.generated)}", "parameters": {
            "part_type": spec["part_type"], "parameters": copy.deepcopy(spec["parameters"]), "material": spec.get("material"),
            "operations": []}, "validation": {"warnings": [], "corrections": {}},
            "verification": {"measured": {"volume": vol, "bbox": {"xmin": 0, "xmax": 50, "ymin": 0, "ymax": 40, "zmin": 0, "zmax": t}}}}

    def deps(self, plans=(), offer=True):
        plans = list(plans)
        return svc.Deps(plan=lambda history, spec: plans.pop(0), generate=self.generate,
                        balance=lambda u: 5, offer_candidates=offer)


def setup():
    fresh_store()
    return cs.create_chat(U)["id"]


def first_build(w, spec=L_BRACKET, text="an L bracket for a shelf", offer=True):
    chat_id = setup()
    out = svc.run_turn(w.deps([Plan("build", "Built.", copy.deepcopy(spec), "llm")], offer), U, chat_id, text)
    return chat_id, out


def test_first_bracket_build_offers_three_versions_and_one_part():
    w = World()
    chat_id, out = first_build(w)
    offer = out["candidates"]
    assert [c["id"] for c in offer["candidates"]] == ["thin", "standard", "heavy"]
    assert len(w.generated) == 3 and all(src == "rules" for _, src in w.generated[1:])  # standard reused, no AI for the rest
    assert out["part"]["version"] == 1 and len(cs.get_chat(U, chat_id)["parts"]) == 1
    assert "specs" not in str(offer) and "job_id" not in str(offer)  # the browser never sees specs or job ids
    assert out["charged"] == 0


def test_cards_compare_the_same_number_for_every_version():
    _, out = first_build(World())
    lines = [c["line"] for c in out["candidates"]["candidates"]]
    assert all(l.endswith(" cm3") or " cm3" in l for l in lines) or all(" g" in l for l in lines)


def test_nothing_is_offered_when_the_feature_is_off_or_the_message_names_a_thickness():
    w = World()
    _, out = first_build(w, offer=False)
    assert out["candidates"] is None and len(w.generated) == 1
    w2 = World()
    _, out2 = first_build(w2, text="a 4 mm thick L bracket")
    assert out2["candidates"] is None and len(w2.generated) == 1


def test_gears_and_edits_get_no_offer():
    w = World()
    gear = {"part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 5},
            "material": None, "operations": []}
    _, out = first_build(w, spec=gear, text="a gear")
    assert out["candidates"] is None
    chat_id, _ = first_build(World())
    w3 = World()
    edit = Plan("edit", "", copy.deepcopy(L_BRACKET), "llm", ["thickness_mm: 5 to 6"])
    out3 = svc.run_turn(w3.deps([edit]), U, chat_id, "make it a bit bigger")
    assert out3["candidates"] is None and len(w3.generated) == 1


def test_a_failed_variant_is_left_out_and_never_loses_the_first_version():
    heavy = cand.variants(L_BRACKET)["candidates"][-1]["thickness_mm"]
    _, out = first_build(World(fail_for=heavy))
    assert out["part"] is not None
    assert [c["id"] for c in out["candidates"]["candidates"]] == ["thin", "standard"]


def test_if_every_variant_fails_the_person_just_gets_their_part():
    class Broken(World):
        def generate(self, desc, spec, user, source):
            if len(self.generated) >= 1:
                raise RuntimeError("engine down")
            return super().generate(desc, spec, user, source)
    _, out = first_build(Broken())
    assert out["part"]["version"] == 1 and out["candidates"] is None


def test_a_comparison_that_disagrees_with_itself_is_not_shown():
    _, out = first_build(World(wrong_order=True))
    assert out["candidates"] is None and out["part"] is not None


def test_picking_a_version_adds_it_as_the_next_version_once():
    w = World()
    chat_id, out = first_build(w)
    mid = out["assistant_message"]["id"]
    d = w.deps()
    picked = svc.pick_candidate(d, U, chat_id, mid, "thin")
    assert picked["part"]["version"] == 2 and picked["charged"] == 0
    assert picked["part"]["spec"]["parameters"]["thickness_mm"] < L_BRACKET["parameters"]["thickness_mm"]
    again = svc.pick_candidate(d, U, chat_id, mid, "thin")
    assert again["part"]["id"] == picked["part"]["id"] and len(cs.get_chat(U, chat_id)["parts"]) == 2
    stored = cs.get_message(U, chat_id, mid)["meta"]["candidates"]
    assert stored["picked"] == "thin"


def test_picking_standard_returns_the_existing_version_without_a_duplicate():
    w = World()
    chat_id, out = first_build(w)
    mid = out["assistant_message"]["id"]
    picked = svc.pick_candidate(w.deps(), U, chat_id, mid, "standard")
    assert picked["part"]["id"] == out["part"]["id"] and len(cs.get_chat(U, chat_id)["parts"]) == 1


def test_pick_rejects_unknown_versions_messages_and_other_accounts():
    w = World()
    chat_id, out = first_build(w)
    mid = out["assistant_message"]["id"]
    for args in ((chat_id, mid, "gigantic"), (chat_id, "nope", "thin")):
        try:
            svc.pick_candidate(w.deps(), U, *args)
        except svc.CandidateNotFound:
            pass
        else:
            raise AssertionError("expected CandidateNotFound")
    try:
        svc.pick_candidate(w.deps(), V, chat_id, mid, "thin")
    except svc.ChatNotFound:
        pass
    else:
        raise AssertionError("another account must not pick from this chat")


def test_the_spec_comes_from_the_stored_offer_not_from_the_caller():
    import inspect
    assert list(inspect.signature(svc.pick_candidate).parameters) == ["deps", "user_id", "chat_id", "message_id", "candidate_id"]


def test_asking_for_versions_of_an_existing_part():
    w = World()
    chat_id, out = first_build(w, offer=False)
    res = svc.offer_candidates(w.deps(), U, chat_id, out["part"]["id"])
    assert [c["id"] for c in res["candidates"]["candidates"]] == ["thin", "standard", "heavy"]
    assert cs.get_message(U, chat_id, res["message_id"])["meta"]["candidates"]["picked"] is None


def test_asking_for_versions_of_a_gear_is_refused_in_plain_words():
    w = World()
    gear = {"part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 5},
            "material": None, "operations": []}
    chat_id, out = first_build(w, spec=gear, text="a gear")
    try:
        svc.offer_candidates(w.deps(), U, chat_id, out["part"]["id"])
    except svc.CandidatesUnavailable as exc:
        assert "motor mounts" in str(exc)
    else:
        raise AssertionError("expected CandidatesUnavailable")


def test_motor_mount_gets_candidates_too():
    _, out = first_build(World(), spec=MOTOR, text="a motor bracket")
    assert [c["id"] for c in out["candidates"]["candidates"]] == ["thin", "standard", "heavy"]

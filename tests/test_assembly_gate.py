"""assembly_gate: a plan for an assembly is judged before it is built. No CAD, no network."""
import copy
import json

import assembly_gate as ag
import assembly_relations as ar
import conversation as conv
import studio_templates as st

ASSEMBLIES = [t for t in st.TEMPLATES if t.spec["part_type"] == "assembly" and t.category != "vehicles"]   # vehicles are not shaft assemblies


def _tpl(tid):
    return copy.deepcopy(st.spec_of(st.get(tid)))


def _part(spec, name):
    return next(p for p in spec["parameters"]["parts"] if p["name"] == name)


def _hist(text):
    return [{"role": "user", "content": text}]


# ---- the check itself ------------------------------------------------------------------

def test_every_template_assembly_passes_clean():
    for t in ASSEMBLIES:
        r = ag.check(t.spec)
        assert r.ok and not r.warnings, (t.id, r.errors, r.warnings)
        assert not r.unchecked, (t.id, r.unchecked)


def test_a_bore_too_narrow_for_the_shaft_is_an_error_with_the_fix():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 7
    r = ag.check(spec)
    assert not r.ok and len(r.errors) == 1
    assert "shaft and gear overlap" in r.errors[0] and "about 8.2 mm" in r.errors[0]


def test_a_part_with_no_bore_on_a_shaft_overlaps():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 0
    assert not ag.check(spec).ok


def test_two_parts_sharing_length_on_one_shaft_overlap():
    spec = _tpl("asm-shaft-bearings")
    _part(spec, "bearing_right")["position"] = [0.0, 0.0, 8.0]          # left bearing runs z=5 to 12
    r = ag.check(spec)
    assert not r.ok and "bearing_left and bearing_right overlap" in r.errors[0] and "z=5" in r.errors[0]


def test_touching_parts_are_fine():
    spec = _tpl("asm-shaft-bearings")
    _part(spec, "bearing_right")["position"] = [0.0, 0.0, 12.0]         # starts exactly where the left one ends
    assert ag.check(spec).ok


def test_a_part_past_the_end_of_the_shaft_is_an_error():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["position"] = [0.0, 0.0, 70.0]
    r = ag.check(spec)
    assert not r.ok and "past the end of the 60 mm shaft" in r.errors[0]


def test_a_part_hanging_a_little_over_the_end_is_a_warning_not_an_error():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["position"] = [0.0, 0.0, 53.0]                  # 53 to 63 on a 60 mm shaft
    r = ag.check(spec)
    assert r.ok and "hangs over the end of the shaft" in r.warnings[0]


def test_a_loose_gear_is_a_warning():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 14
    r = ag.check(spec)
    assert r.ok and "gear is loose on the shaft" in r.warnings[0]


def test_a_keyed_or_chamfered_shaft_does_not_make_a_fitted_gear_look_loose():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "shaft")["parameters"].update(chamfer_mm=1, keyway_width_mm=3, keyway_depth_mm=1.5)
    r = ag.check(spec)
    assert r.ok and not r.warnings


def test_meshing_gears_do_not_collide():
    spec = _tpl("asm-gear-shaft")
    spec["parameters"]["parts"] = [
        {"name": "gear_a", "part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 8.2}, "position": [0, 0, 0]},
        {"name": "gear_b", "part_type": "gear", "parameters": {"module": 2, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 8.2}, "position": [40, 0, 0]},
    ]
    assert ag.check(spec).ok                                           # centre distance equals the pitch circles touching
    spec["parameters"]["parts"][1]["position"] = [20, 0, 0]
    assert not ag.check(spec).ok                                       # half a gear apart is a real collision


def test_sideways_offset_clears_the_shaft():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["position"] = [40.0, 0.0, 20.0]
    r = ag.check(spec)
    assert r.ok and not r.warnings                                     # off the axis, so not a part riding on the shaft


def test_what_the_gate_cannot_model_is_reported_and_not_rejected():
    spec = _tpl("asm-gear-shaft")
    spec["parameters"]["parts"] += [
        {"name": "plate", "part_type": "flat_plate", "parameters": {"length_mm": 80}, "position": [0, 0, 0]},
        {"name": "tilted", "part_type": "bearing", "parameters": {}, "position": [0, 0, 0], "rotation": [0, 90, 0]},
        {"name": "many", "part_type": "spacer", "parameters": {}, "position": [0, 0, 0], "count": 4}]
    r = ag.check(spec)
    assert r.ok and set(r.unchecked) == {"plate", "tilted", "many"}


def test_only_custom_layouts_are_judged():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 1
    spec["parameters"]["layout"] = "row"
    assert ag.check(spec).ok and not ag.check(spec).checked
    spec["parameters"]["layout"] = "mated"
    assert ag.check(spec).ok


def test_junk_never_raises():
    for bad in (None, {}, {"part_type": "assembly"}, {"part_type": "assembly", "parameters": {"layout": "custom", "parts": [1, None, {"part_type": 5}]}},
                {"part_type": "assembly", "parameters": {"layout": "custom", "parts": [{"part_type": "gear", "parameters": {"teeth": "x"}, "position": "up"}]}}):
        ag.check(bad)
    r = ag.check({"part_type": "assembly", "parameters": {"layout": "custom", "parts": [{"part_type": "gear", "position": [1, 2]}]}})
    assert not r.ok and "position that is not three numbers" in r.errors[0]


def test_unnamed_and_repeated_names_still_get_distinct_labels():
    spec = _tpl("asm-shaft-bearings")
    for p in spec["parameters"]["parts"]:
        p.pop("name")
    _part_by = spec["parameters"]["parts"]
    _part_by[2]["position"] = [0.0, 0.0, 8.0]
    r = ag.check(spec)
    assert "bearing_1 and bearing_2 overlap" in r.errors[0]


def test_clearance_is_signed_and_symmetric():
    a = ag.Body("a", "shaft", 0, 0, 0, 60, 0, 4)
    b = ag.Body("b", "gear", 0, 0, 20, 30, 4.1, 15)
    assert abs(ag.clearance(a, b) - 0.1) < 1e-9 and ag.clearance(a, b) == ag.clearance(b, a)
    b2 = ag.Body("b", "gear", 0, 0, 20, 30, 3.5, 15)
    assert ag.clearance(a, b2) < 0


def test_the_gate_agrees_with_the_follow_on_relations():
    """Whatever assembly_relations produces after a shaft edit must pass the gate, or the planner would
    reject its own carried-through plan."""
    for t in ASSEMBLIES:
        for kw in ({"diameter_mm": 10}, {"diameter_mm": 12}, {"length_mm": 80}, {"length_mm": 120}, {"diameter_mm": 9, "length_mm": 70}):
            old = copy.deepcopy(t.spec)
            new = copy.deepcopy(old)
            _part(new, "shaft")["parameters"].update(kw)
            out = ar.reconcile(old, new).spec
            r = ag.check(out)
            assert r.ok and not r.warnings, (t.id, kw, r.errors, r.warnings)


# ---- in the planner ---------------------------------------------------------------------

def _llm_returning(*specs):
    replies = [json.dumps({"intent": "build", "reply": "Built it.", "spec": s}) for s in specs]
    seen = []

    def llm(msgs):
        seen.append(msgs)
        return replies[min(len(seen), len(replies)) - 1]
    llm.seen = seen
    return llm


def _bad():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 7
    return spec


def test_a_bad_plan_is_sent_back_with_the_reason_and_the_fixed_one_is_used():
    llm = _llm_returning(_bad(), _tpl("asm-gear-shaft"))
    plan = conv.plan_turn(_hist("a gear on a shaft"), None, llm=llm)
    assert plan.intent == "build" and plan.source == "llm" and len(llm.seen) == 2
    retry = llm.seen[1]
    assert retry[-2]["role"] == "assistant" and "bore_diameter_mm" in retry[-2]["content"]         # the model sees its own plan
    assert "checked before building" in retry[-1]["content"] and "about 8.2 mm" in retry[-1]["content"]
    assert _part(plan.spec, "gear")["parameters"]["bore_diameter_mm"] == 8.2


def test_a_plan_that_fails_twice_is_not_built_and_the_person_gets_the_reasons():
    llm = _llm_returning(_bad(), _bad())
    plan = conv.plan_turn(_hist("a gear on a shaft"), None, llm=llm)
    assert plan.intent == "answer" and plan.spec is None and plan.source == "llm"
    assert "I did not build that" in plan.reply and "shaft and gear overlap" in plan.reply


def test_a_failed_second_call_still_reports_the_first_plans_problems():
    state = {"n": 0}

    def llm(msgs):
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"intent": "build", "reply": "Built it.", "spec": _bad()})
        raise TimeoutError("model went away")

    plan = conv.plan_turn(_hist("a gear on a shaft"), None, llm=llm)
    assert plan.intent == "answer" and "I did not build that" in plan.reply


def test_a_shaft_only_edit_by_the_model_passes_because_relations_run_first():
    old = _tpl("asm-gear-shaft")
    new = copy.deepcopy(old)
    _part(new, "shaft")["parameters"]["diameter_mm"] = 10               # the model forgot the gear bore
    llm = _llm_returning(new)
    llm_edit = lambda msgs: json.dumps({"intent": "edit", "reply": "Shaft is 10 mm.", "spec": new})  # noqa: E731
    plan = conv.plan_turn(_hist("make the shaft 10 mm"), old, llm=llm_edit)
    assert plan.intent == "edit" and _part(plan.spec, "gear")["parameters"]["bore_diameter_mm"] == 10.2


def test_warnings_ride_along_in_the_reply():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["bore_diameter_mm"] = 14
    plan = conv.plan_turn(_hist("a gear on a shaft"), None, llm=_llm_returning(spec))
    assert plan.intent == "build" and "Heads up: gear is loose on the shaft" in plan.reply


def test_the_rules_path_is_gated_too():
    old = _tpl("asm-gear-shaft")
    plan = conv.plan_turn(_hist("make the gear 6 mm bore"), old)
    assert plan.intent == "answer" and "I did not build that" in plan.reply and "shaft and gear overlap" in plan.reply
    plan = conv.plan_turn(_hist("use 30 teeth"), old)
    assert plan.intent == "edit"


def test_the_prompt_asks_for_a_tree_of_named_parts_with_positions():
    p = conv.build_system_prompt("")
    assert "tree of named parts" in p and "[0, 0, z]" in p and "checked before it is built" in p


# ---- parameter names ---------------------------------------------------------------------

def test_known_parameter_table_matches_the_templates():
    """The gate's table is written out by hand. If a template starts reading a new key, or drops one, this fails."""
    import assembly_planner as ap
    cat = ap.default_catalog()
    for ptype, names in ag._KNOWN_PARAMS.items():
        assert set(cat[ptype]) == set(names), (ptype, set(cat[ptype]) ^ set(names))


def test_a_misspelt_parameter_is_an_error_that_suggests_the_right_name():
    spec = _tpl("asm-gear-shaft")
    p = _part(spec, "shaft")["parameters"]
    p["diameter"] = p.pop("diameter_mm")
    r = ag.check(spec)
    assert not r.ok
    assert 'shaft has a parameter "diameter"' in r.errors[0] and 'Did you mean "diameter_mm"?' in r.errors[0]
    # The ignored name leaves the shaft at its 10 mm default, so the 8.2 mm gear bore now overlaps it too.
    assert any("shaft and gear overlap" in e for e in r.errors[1:])


def test_a_name_with_no_close_match_lists_what_the_part_takes():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["colour"] = "red"
    r = ag.check(spec)
    assert "gear does not have" in r.errors[0] and "bore_diameter_mm" in r.errors[0]


def test_position_inside_parameters_gets_a_pointed_message():
    spec = _tpl("asm-gear-shaft")
    _part(spec, "gear")["parameters"]["position"] = [0, 0, 20]
    r = ag.check(spec)
    assert 'has "position" inside "parameters"' in r.errors[0] and "next to" in r.errors[0]


def test_the_name_check_applies_to_every_layout():
    for layout in ("row", "mated", "custom"):
        spec = _tpl("asm-gear-shaft")
        spec["parameters"]["layout"] = layout
        _part(spec, "gear")["parameters"]["bore"] = 8.2
        assert not ag.check(spec).ok, layout


def test_other_part_types_are_not_name_checked():
    spec = _tpl("asm-gear-shaft")
    spec["parameters"]["parts"] += [
        {"name": "bolt", "part_type": "hex_bolt", "parameters": {"size": "M8", "anything": 1}, "position": [30, 0, 0]},
        {"name": "house", "part_type": "building", "parameters": {"bedrooms": 3}, "position": [200, 0, 0]}]
    assert ag.check(spec).ok


def test_many_typos_are_capped():
    spec = _tpl("asm-gear-shaft")
    for i in range(12):
        _part(spec, "gear")["parameters"][f"typo{i}"] = i
    r = ag.check(spec)
    assert len(r.errors) == ag._MAX_LISTED + 1 and "6 more unknown parameter names" in r.errors[-1]


def test_the_model_is_sent_back_to_fix_a_misspelt_parameter():
    typo = _tpl("asm-gear-shaft")
    p = _part(typo, "shaft")["parameters"]
    p["diameter"] = p.pop("diameter_mm")
    llm = _llm_returning(typo, _tpl("asm-gear-shaft"))
    plan = conv.plan_turn(_hist("a gear on a shaft"), None, llm=llm)
    assert plan.intent == "build" and len(llm.seen) == 2
    assert 'Did you mean "diameter_mm"?' in llm.seen[1][-1]["content"]
    assert "diameter_mm" in _part(plan.spec, "shaft")["parameters"]

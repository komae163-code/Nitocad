"""The visual-fix path through the real chat code, with a scripted vision model and a scripted reviser.

Everything between the vision reply and the rebuilt plan is the real code: vehicle_chat._inspect_finished, the
inspector, matplotlib renders (boxes, because there is no CAD kernel here), the finding parser and the plan rebuild.
Only the two models are fakes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spec_library as SL
import vehicle_chat as VC

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def mini_car(glass_h=400):
    def box(i, x, y, z, l, w, h):
        return {"id": i, "type": "simple_box", "assembly": "body", "position": [x, y, z],
                "parameters": {"length_mm": l, "width_mm": w, "height_mm": h, "wall_thickness_mm": 5, "hollow": False}}
    return {"name": "mini car", "notes": "test", "params": {}, "components": [
        box("body", 0, -900, 300, 4000, 1800, 800),
        box("glass", 1000, -700, 1100, 1500, 1400, glass_h),          # sits on the body: no checklist finding
        box("wheel_fl", 600, 750, 0, 600, 200, 600), box("wheel_fr", 600, -950, 0, 600, 200, 600),
        box("wheel_rl", 2800, 750, 0, 600, 200, 600), box("wheel_rr", 2800, -950, 0, 600, 200, 600)],
        "explode": {"directions": {}}, "checks": []}


def fake_bbox(p):
    q = p["parameters"]
    return (0.0, 0.0, 0.0, float(q["length_mm"]), float(q["width_mm"]), float(q["height_mm"]))


class Run:
    """One inspection through _inspect_finished with scripted models. `sees(n, prompt, imgs)` returns the reply."""

    def __init__(self, sees=None, revise_to=None, scoped=True):
        self.vision_calls, self.revise_calls = [], []
        self.sees, self.revise_to, self.scoped = sees, revise_to, scoped

    def vision(self, prompt, imgs):
        self.vision_calls.append((prompt, imgs))
        return self.sees(len(self.vision_calls), prompt, imgs)

    def revise(self, cur, instruction, llm=None, lib=None, time_budget_s=None):
        self.revise_calls.append(instruction)
        new = self.revise_to(cur, len(self.revise_calls)) if self.revise_to else None
        return {"ok": True, "spec": new} if new else {"ok": False, "spec": None}

    def go(self, vspec=None):
        vspec = vspec or mini_car()
        old_bbox, old_rev = VC._bbox_fn, VC.VP.revise_vehicle
        VC.set_bbox_fn(fake_bbox)
        VC.VP.revise_vehicle = self.revise
        try:
            plan = VC._build_plan(vspec, None, intent="build", source="llm", changes=None, previous=None, unresolved=None)
            assert plan.spec is not None, plan.reply
            lib = SL.SpecLibrary(SL.MemoryStore())
            if self.sees is None:
                return plan, VC._inspect_finished(plan, vspec, lambda m: "", lib, None)
            with VC.vision_scope(self.vision):
                return plan, VC._inspect_finished(plan, vspec, lambda m: "", lib, None)
        finally:
            VC.set_bbox_fn(old_bbox)
            VC.VP.revise_vehicle = old_rev


ISSUE = json.dumps({"issues": [{"parts": ["glass"], "problem": "the glass is a plain box",
                                "fix": "Make the glass shorter so it reads as a windscreen."}]})
CLEAN = json.dumps({"issues": []})


def test_a_visual_finding_drives_one_fix_and_a_second_look_confirms_it():
    run = Run(sees=lambda n, p, i: ISSUE if n == 1 else CLEAN,
              revise_to=lambda cur, n: mini_car(glass_h=250))
    plan, out = run.go()
    # the vision model got four real PNGs and a prompt naming the real parts
    assert len(run.vision_calls) == 2
    prompt, imgs = run.vision_calls[0]
    assert len(imgs) == 4 and all(b.startswith(PNG_MAGIC) and len(b) > 1000 for b in imgs)
    assert "glass" in prompt and "wheel_fl" in prompt and "nothing" in prompt      # checklist was clean
    # its instruction reached the reviser unchanged, once
    assert len(run.revise_calls) == 1 and "windscreen" in run.revise_calls[0]
    # the plan was rebuilt from the revised spec and says so
    assert "fixed after inspection" in out.changes
    glass = [p for p in out.spec["parameters"]["parts"] if p["name"] == "glass"][0]
    assert glass["parameters"]["height_mm"] == 250
    assert "looked at four renders" in out.reply and "Nothing wrong found" in out.reply


def test_a_model_that_never_stops_complaining_still_gets_only_two_fixes():
    run = Run(sees=lambda n, p, i: ISSUE, revise_to=lambda cur, n: mini_car(glass_h=400 - 50 * n))
    _, out = run.go()
    assert len(run.revise_calls) == 2 and len(run.vision_calls) == 3
    assert "Still open" in out.reply


def test_a_part_the_model_invents_is_dropped_and_nothing_is_revised():
    bad = json.dumps({"issues": [{"parts": ["spoiler"], "problem": "no spoiler", "fix": "Add a spoiler."}]})
    run = Run(sees=lambda n, p, i: bad, revise_to=lambda cur, n: mini_car(glass_h=100))
    plan, out = run.go()
    assert run.revise_calls == [] and out is plan and "Nothing wrong found" in out.reply


def test_a_reply_that_is_not_json_changes_nothing():
    run = Run(sees=lambda n, p, i: "Looks like a lovely car!", revise_to=lambda cur, n: mini_car(glass_h=100))
    plan, out = run.go()
    assert run.revise_calls == [] and "looked at four renders" in out.reply


def test_a_vision_model_that_fails_leaves_the_checklist_and_the_model_intact():
    def boom(n, p, i):
        raise RuntimeError("provider down")
    run = Run(sees=boom, revise_to=lambda cur, n: mini_car(glass_h=100))
    plan, out = run.go()
    assert out is plan and run.revise_calls == [] and "looked at four renders" not in out.reply


def test_a_reviser_that_cannot_fix_it_returns_the_original_with_the_finding_listed():
    run = Run(sees=lambda n, p, i: ISSUE, revise_to=lambda cur, n: None)
    plan, out = run.go()
    assert len(run.revise_calls) == 1 and out is plan
    assert "the glass is a plain box" in out.reply and "Still open" in out.reply


def test_with_no_vision_model_nothing_is_rendered_or_asked_and_the_reply_says_nothing_about_it():
    run = Run(sees=None)
    plan, out = run.go()
    assert run.vision_calls == [] and "looked at four renders" not in out.reply


def test_a_real_checklist_fault_and_a_visual_fault_are_fixed_together():
    flying = mini_car()
    flying["components"][1]["position"] = [1000, -700, 1900]                # glass floats 500 mm above the body
    run = Run(sees=lambda n, p, i: ISSUE if n == 1 else CLEAN, revise_to=lambda cur, n: mini_car(glass_h=250))
    _, out = run.go(flying)
    ins = run.revise_calls[0]
    assert "glass" in ins and "windscreen" in ins                            # one instruction carries both
    assert len(run.revise_calls) == 1

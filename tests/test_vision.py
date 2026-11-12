"""The picture-reading side of the finished-vehicle inspection: who gets a vision model, and what is sent."""
from types import SimpleNamespace as NS

import ai_models as AI
import vehicle_chat as VC
import vehicle_inspect as VI

PNG = b"\x89PNG\r\n\x1a\nfake"


def settings(**kw):
    base = dict(AI_TIMEOUT_SECONDS=30, ANTHROPIC_API_KEY="k", ANTHROPIC_MODEL="claude-x", OPENAI_API_KEY="k",
                OPENAI_BASE_URL="http://x", OPENAI_MODEL="gpt-x", QWEN_API_KEY="k", QWEN_BASE_URL="http://q", QWEN_MODEL="qwen-plus")
    base.update(kw)
    return NS(**base)


def spec(provider):
    return NS(id=f"x:{provider}", tier="plus", provider=provider, label=provider, available=True)


def test_only_models_that_can_see_get_a_vision_function():
    s = settings()
    assert callable(AI.make_vision(spec("anthropic"), s)) and callable(AI.make_vision(spec("openai"), s))
    assert AI.make_vision(spec("deepseek"), s) is None and AI.make_vision(spec("rules"), s) is None
    assert AI.make_vision(spec("qwen"), s) is None                                   # qwen-plus is text only
    assert callable(AI.make_vision(spec("qwen"), settings(QWEN_MODEL="qwen-vl-max")))
    assert AI.make_vision(spec("anthropic"), settings(ANTHROPIC_API_KEY=None)) is None


def test_the_anthropic_payload_has_images_then_the_prompt_and_at_most_four():
    p = AI.anthropic_vision_payload("claude-x", "look", [PNG] * 6)
    blocks = p["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image"] * 4 + ["text"] and blocks[-1]["text"] == "look"
    assert blocks[0]["source"]["media_type"] == "image/png"


def test_the_openai_message_carries_data_urls():
    m = AI.openai_vision_messages("look", [PNG, PNG])
    c = m[0]["content"]
    assert c[0] == {"type": "text", "text": "look"} and c[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_the_vision_scope_is_per_request_and_restored():
    f = lambda prompt, imgs: ""                                                  # noqa: E731
    assert VC._vision_ctx.get() is None
    with VC.vision_scope(f):
        assert VC._vision_ctx.get() is f
    assert VC._vision_ctx.get() is None


def test_a_vision_reply_adds_findings_and_the_summary_says_it_looked(tmp_path):
    def bbox(p):
        return p["parameters"]["_b"]

    def part(n, lo, hi):
        return {"name": n, "part_type": "box", "parameters": {"_b": (*lo, *hi)}, "position": [0, 0, 0], "rotation": [0, 0, 0]}
    parts = [part("body", (0, -900, 300), (4000, 900, 1100)), part("glass", (1000, -400, 1100), (2000, 400, 1500))]
    seen = {}

    def see(prompt, imgs):
        seen["n"], seen["prompt"] = len(imgs), prompt
        return '{"issues":[{"parts":["glass"],"problem":"glass is a plain box","fix":"Slope the glass to follow the roof line."}]}'
    rep = VI.inspect_vehicle({"name": "car"}, expand=lambda v: (parts, []), bbox_fn=bbox, vision_fn=see, work_dir=tmp_path, kind="car")
    assert seen["n"] == 4 and "glass" in seen["prompt"] and "body" in seen["prompt"]
    assert rep["visual"] == "done" and any(f["code"] == "visual" for f in rep["findings"])
    assert "looked at four renders" in rep["summary"]

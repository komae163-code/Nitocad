"""Does the AI planner pick external parts from plain-language requests?
Needs an AI key in the environment: DEEPSEEK_API_KEY (or LLM_API_KEY), or ANTHROPIC_API_KEY.
Run:  python3 try_planner.py"""
import json
import os
import urllib.request
from pathlib import Path

import ai_models as AM
import asset_providers as AP
import spec_library as SL
import vehicle_planner as VP

PROMPTS = [
    "a small hand cart with a cardboard box sitting on the bed",
    "a go-kart frame with a ball bearing in each front wheel hub",
    "a workshop trolley with an M6 hex nut and bolt holding the handle on",
    "a simple four wheel chassis",
]


def _post(url, headers, body, timeout=90):
    hdr = {"content-type": "application/json", "user-agent": "NitoCAD-planner-test/1", **headers}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def make_llm():
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY")
    if key:
        base = (os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        model = (os.environ.get("CHAT_PLANNER_MODEL") or os.environ.get("DEEPSEEK_DEFAULT_MODEL")
                 or os.environ.get("LLM_MODEL") or "deepseek-v4-flash")

        def call(messages):
            body = {"model": model, "messages": messages, "temperature": 0.1,
                    "response_format": {"type": "json_object"}}
            out = _post(base + "/chat/completions", {"authorization": "Bearer " + key}, body)
            return out["choices"][0]["message"]["content"] or ""
        return call, "deepseek-compatible: " + model
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5"

        def call(messages):
            system, turns = AM.to_anthropic(messages)
            body = {"model": model, "max_tokens": 1200, "system": system, "messages": turns}
            out = _post("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
            return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        return call, "anthropic: " + model
    return None, ""


def run(llm=None, label="", registry=None, prompts=PROMPTS):
    if llm is None:
        llm, label = make_llm()
    if llm is None:
        print("No AI key found. Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY first.")
        return
    reg = registry or AP.default_registry()
    lib = SL.SpecLibrary(SL.MemoryStore())
    try:
        lib.seed_from_dir(Path("vehicles/examples"))
    except Exception:
        pass
    print("model:", label)
    for text in prompts:
        print("\nPROMPT:", text)
        r = VP.plan_vehicle(text, llm, lib, save_candidate=False)
        if not r.get("ok"):
            print("  planner failed:", "; ".join(map(str, r.get("errors", [])))[:200])
            continue
        comps = r["spec"].get("components", [])
        ext = [c for c in comps if c.get("source")]
        print(f"  parts: {len(comps)}, external: {len(ext)}, attempts: {r.get('attempts')}")
        for c in ext:
            src = c["source"]
            res = reg.search(src.get("query") or src.get("id") or "", limit=100)
            hits = [x for x in res["results"] if not src.get("provider") or x.provider == src["provider"]]
            first = f", first {hits[0].id}" if hits else ""
            print(f"   {c['id']}: provider={src.get('provider')!r} query={src.get('query')!r} "
                  f"-> {len(hits)} match(es){first}")


if __name__ == "__main__":
    run()

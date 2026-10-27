#!/usr/bin/env python3
"""
How accurately does NitoCAD read a description?

    python eval_parser.py                       regex parser (no key needed, runs in CI)
    python eval_parser.py --mode llm            DeepSeek parser (needs DEEPSEEK_API_KEY)
    python eval_parser.py --fail-under 0.85     exit 1 if overall accuracy is below 85%
    python eval_parser.py --verbose             print every miss

A case passes when the part type is right and every listed parameter is within 1% (or 0.01).
Parameters the case does not list are ignored, so a parser that returns extra values is not
penalised. Cases whose expected type is "unknown" check the parser does NOT invent a part.

The dataset in tests/eval/parser_cases.json is the target, not a record of what works today: a
fresh run lists what to fix. Add a case whenever a real prompt is misread.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

CASES = Path(__file__).parent / "tests" / "eval" / "parser_cases.json"


def close(expected: float, actual) -> bool:
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return math.isclose(float(expected), float(actual), rel_tol=0.01, abs_tol=0.01)


def score_case(case: dict, part_type: str | None, params: dict | None) -> dict:
    """Returns {"ok", "type_ok", "missing": [...], "wrong": {key: (expected, got)}}."""
    params = params or {}
    type_ok = (part_type or "unknown") == case["part_type"]
    missing, wrong = [], {}
    if case["part_type"] != "unknown" and type_ok:
        for key, expected in case["params"].items():
            if key not in params:
                missing.append(key)
            elif not close(expected, params[key]):
                wrong[key] = (expected, params[key])
    return {"ok": type_ok and not missing and not wrong, "type_ok": type_ok, "missing": missing, "wrong": wrong}


def summarize(results: list[tuple[dict, dict]]) -> dict:
    by_type: dict[str, list[bool]] = defaultdict(list)
    type_hits = 0
    for case, r in results:
        by_type[case["part_type"]].append(r["ok"])
        type_hits += r["type_ok"]
    n = len(results)
    return {
        "cases": n, "accuracy": round(sum(r["ok"] for _, r in results) / n, 4) if n else 0.0,
        "type_accuracy": round(type_hits / n, 4) if n else 0.0,
        "by_type": {t: {"cases": len(v), "accuracy": round(sum(v) / len(v), 3)} for t, v in sorted(by_type.items())},
    }


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text())["cases"]


def _parser(mode: str):
    if mode == "regex":
        from smart_parser import parse_description

        def run(text):
            p = parse_description(text)
            return p.part_type, dict(p.parameters)
        return run
    from deepseek_parser import parse_description as deepseek_parse_description

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("DEEPSEEK_API_KEY is not set")

    def run(text):
        p = deepseek_parse_description(text, use_deepseek=True, api_key=key)
        return p.part_type, dict(p.parameters)
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("regex", "llm"), default="regex")
    ap.add_argument("--fail-under", type=float, default=0.0)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)

    parse = _parser(args.mode)
    results = []
    for case in load_cases():
        try:
            ptype, params = parse(case["prompt"])
        except Exception as exc:  # noqa: BLE001 - a parser crash is a failed case, not a failed run
            ptype, params = None, {}
            if args.verbose:
                print(f"CRASH {case['prompt']!r}: {exc}")
        results.append((case, score_case(case, ptype, params)))
        if args.verbose and not results[-1][1]["ok"]:
            r = results[-1][1]
            print(f"MISS  {case['prompt']!r}\n      expected {case['part_type']} {case['params']}\n"
                  f"      got      type_ok={r['type_ok']} missing={r['missing']} wrong={r['wrong']}")
    summary = summarize(results)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"{args.mode} parser: {summary['accuracy']:.1%} of {summary['cases']} cases fully correct, "
              f"{summary['type_accuracy']:.1%} right part type")
        for t, s in summary["by_type"].items():
            print(f"  {t:18s} {s['accuracy']:6.1%}  ({s['cases']})")
    return 0 if summary["accuracy"] >= args.fail_under else 1


if __name__ == "__main__":
    sys.exit(main())

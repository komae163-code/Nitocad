#!/usr/bin/env python3
"""
How often does a prompt turn into the right solid, how fast, and at what cost?

    python eval_geometry.py                          rule-based parser, no key needed
    python eval_geometry.py --mode llm               DeepSeek parser (needs DEEPSEEK_API_KEY)
    python eval_geometry.py --label v17 --out bench-v17.json --markdown BENCHMARK.md
    python eval_geometry.py --baseline bench-v16.json --max-regression 0.02
    python eval_geometry.py --fail-under-completion 0.95 --fail-under-correct 0.70

Every case in tests/eval/geometry_cases.json is a prompt a person might type, with the size, volume
and hole count of the part they mean. Each prompt goes through the same path a real request takes:
parse, validate, build with CadQuery, verify, export. Then the finished solid is measured.

Three numbers, kept apart because they answer different questions:
  completion   the share of prompts that gave a valid, single, non-empty solid. A part that is the
               wrong size still counts here. This is "did it build".
  correct      the share that completed AND match the expected size, volume and (when the prompt states
               them) hole count. This is "did it build what was asked".
  speed, cost  seconds per part, and tokens and dollars per part when an AI parser ran. The rule-based
               parser costs nothing.

Rules of the road:
  * The cases are held out from the parser eval and from the template picker. Tune on
    tests/eval/parser_cases.json, score on this.
  * Needs CadQuery. Without it nothing can be built or measured, and the run stops instead of
    reporting a zero.
  * Dollar cost needs your prices: --price-in and --price-out, dollars per million tokens. They are not
    built in because they change. Without them tokens are reported and dollars are left blank.
  * Holes are counted on the STEP file as round concave faces grouped by axis. If the count cannot be
    read for a part it is left unscored for that part, and the summary says how many were scored.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CASES = Path(__file__).parent / "tests" / "eval" / "geometry_cases.json"
DEFAULT_TOLERANCES = {"size_rel": 0.005, "size_abs_mm": 0.1, "volume_rel": 0.015}


# ------------------------------------------------------------------ what one build produced
@dataclass
class Outcome:
    success: bool                          # the generator returned a part
    valid: bool = False                    # verification passed: valid, exactly one solid, volume above zero
    error: str | None = None
    size_mm: list[float] | None = None     # bounding-box edges as measured
    volume_mm3: float | None = None
    holes: int | None = None               # None when it could not be counted
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    parser: str | None = None

    @property
    def completed(self) -> bool:
        return self.success and self.valid and bool(self.volume_mm3 and self.volume_mm3 > 0)


def _close(expected: float, actual: float, rel: float, abs_tol: float) -> bool:
    return math.isclose(float(expected), float(actual), rel_tol=rel, abs_tol=abs_tol)


def score_case(case: dict, out: Outcome, tol: dict | None = None) -> dict:
    """{"completed", "size_ok", "volume_ok", "holes_ok", "correct", "why"}. A check the case or the run
    could not make is None, never True."""
    tol = {**DEFAULT_TOLERANCES, **(tol or {}), **(case.get("tolerances") or {})}      # a case may loosen its own tolerance
    exp = case["expect"]
    why: list[str] = []
    res: dict[str, Any] = {"completed": out.completed, "size_ok": None, "volume_ok": None, "holes_ok": None, "correct": False, "why": why}
    if not out.completed:
        why.append(out.error or "no valid solid")
        return res

    if out.size_mm and exp.get("size_mm"):
        got, want = sorted(out.size_mm), sorted(exp["size_mm"])
        res["size_ok"] = len(got) == len(want) and all(_close(w, g, tol["size_rel"], tol["size_abs_mm"]) for w, g in zip(want, got))
        if not res["size_ok"]:
            why.append(f"size {[round(x, 2) for x in got]} mm, expected {want}")
    if out.volume_mm3 is not None and exp.get("volume_mm3"):
        res["volume_ok"] = _close(exp["volume_mm3"], out.volume_mm3, tol["volume_rel"], 0.0)
        if not res["volume_ok"]:
            why.append(f"volume {out.volume_mm3:.4g} mm3, expected {exp['volume_mm3']:.4g} ({(out.volume_mm3 / exp['volume_mm3'] - 1):+.1%})")
    if exp.get("holes") is not None and out.holes is not None:
        res["holes_ok"] = out.holes == exp["holes"]
        if not res["holes_ok"]:
            why.append(f"{out.holes} holes, expected {exp['holes']}")
    # Correct means nothing that was checked failed, and everything the case expects of size and volume was
    # measured. A case with no size to check (a sprocket) is not marked down for it.
    wanted = [k for k, key in (("size_ok", "size_mm"), ("volume_ok", "volume_mm3")) if exp.get(key)]
    res["correct"] = all(res[k] is not False for k in ("size_ok", "volume_ok", "holes_ok")) and all(res[k] is True for k in wanted)
    return res


# ------------------------------------------------------------------ holes, counted on the solid
def count_holes(cylinders: list[tuple[float, tuple, tuple, bool]]) -> int:
    """Round holes from cylindrical faces: (radius, point on axis, axis direction, faces inward).

    Only inward-facing faces are holes. Faces with the same radius on the same axis are one hole, since
    a hole that is split by a crossing feature, or that the kernel cut in two halves, is still one hole."""
    keys = set()
    for radius, point, direction, concave in cylinders:
        if not concave:
            continue
        n = math.sqrt(sum(c * c for c in direction)) or 1.0
        d = tuple(c / n for c in direction)
        if next((c for c in d if abs(c) > 1e-9), 1.0) < 0:
            d = tuple(-c for c in d)                       # an axis has no sign
        dot = sum(p * c for p, c in zip(point, d))
        foot = tuple(round(p - dot * c, 2) + 0.0 for p, c in zip(point, d))   # where the axis passes closest to the origin
        keys.add((round(radius, 2), tuple(round(c, 4) + 0.0 for c in d), foot))
    return len(keys)


def holes_in_step(path: str) -> int | None:
    """Count the round holes of the first solid in a STEP file. None when it cannot be read."""
    try:
        import cadquery as cq
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
        from OCP.TopAbs import TopAbs_REVERSED

        shape = cq.importers.importStep(path).val()
        cyls = []
        for face in shape.Faces():
            surf = BRepAdaptor_Surface(face.wrapped)
            if surf.GetType() != GeomAbs_Cylinder:
                continue
            c = surf.Cylinder()
            loc, d = c.Axis().Location(), c.Axis().Direction()
            cyls.append((c.Radius(), (loc.X(), loc.Y(), loc.Z()), (d.X(), d.Y(), d.Z()), face.wrapped.Orientation() == TopAbs_REVERSED))
        return count_holes(cyls)
    except Exception:  # noqa: BLE001 - an unreadable file is an unscored hole count, not a failed case
        return None


# ------------------------------------------------------------------ the default builder
def make_builder(mode: str, workdir: Path, formats: list[str]) -> Callable[[str], Outcome]:
    """Build prompts through the real generator, in a scratch database and output folder."""
    os.environ.setdefault("DB_PATH", str(workdir / "bench.db"))
    os.environ.setdefault("OUTPUT_DIR", str(workdir / "out"))
    try:
        import cadquery  # noqa: F401
    except ImportError:
        sys.exit("CadQuery is not installed, so nothing can be built or measured. Run this where the app runs.")
    import cad_generator
    import deepseek_parser

    if mode == "llm" and not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY")):
        sys.exit("DEEPSEEK_API_KEY is not set")
    gen = cad_generator.CADGenerator(output_dir=str(workdir / "out"))
    usage = {"in": 0, "out": 0}

    def hook(u: Any) -> None:
        usage["in"] += int(getattr(u, "prompt_tokens", 0) or 0)
        usage["out"] += int(getattr(u, "completion_tokens", 0) or 0)

    deepseek_parser.USAGE_HOOK = hook

    def build(prompt: str) -> Outcome:
        before = dict(usage)
        t0 = time.perf_counter()
        try:
            r = gen.generate_from_text(prompt, use_deepseek=(mode == "llm"), formats=formats, user_id="bench")
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a failed run
            r = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        seconds = time.perf_counter() - t0
        out = Outcome(success=bool(r.get("success")), error=r.get("error"), seconds=seconds,
                      tokens_in=usage["in"] - before["in"], tokens_out=usage["out"] - before["out"])
        prov = r.get("provenance") or {}
        out.retries, out.parser = int(prov.get("retry_count") or 0), prov.get("parser")
        v = r.get("verification") or {}
        checks, measured = v.get("checks") or {}, v.get("measured") or {}
        out.valid = bool(v.get("passed") and checks.get("is_valid") and checks.get("exactly_one_solid") and checks.get("volume_positive"))
        out.volume_mm3 = measured.get("volume")
        bb = measured.get("bbox")
        if bb:
            out.size_mm = [bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"], bb["zmax"] - bb["zmin"]]
        if out.completed and r.get("step_file"):
            out.holes = holes_in_step(r["step_file"])
        return out

    return build


# ------------------------------------------------------------------ the numbers
def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, max(0, math.ceil(p * len(sorted_vals)) - 1))]


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def summarize(results: list[tuple[dict, Outcome, dict]], *, price_in: float | None = None, price_out: float | None = None,
              mode: str = "rules", label: str = "") -> dict:
    n = len(results)
    secs = sorted(o.seconds for _, o, _ in results)
    tin, tout = sum(o.tokens_in for _, o, _ in results), sum(o.tokens_out for _, o, _ in results)
    cost = None
    if price_in is not None and price_out is not None:
        cost = (tin * price_in + tout * price_out) / 1_000_000
    by_type: dict[str, list[tuple[Outcome, dict]]] = defaultdict(list)
    for case, o, s in results:
        by_type[case["part_type"]].append((o, s))

    def block(items: list[tuple[Outcome, dict]]) -> dict:
        m = len(items)
        return {"cases": m, "completion": _rate(sum(s["completed"] for _, s in items), m), "correct": _rate(sum(s["correct"] for _, s in items), m)}

    scored = lambda key: [s[key] for _, _, s in results if s[key] is not None]  # noqa: E731
    return {
        "label": label, "mode": mode, "cases": n,
        "completion_rate": _rate(sum(s["completed"] for _, _, s in results), n),
        "correct_rate": _rate(sum(s["correct"] for _, _, s in results), n),
        "size_rate": _rate(sum(scored("size_ok")), len(scored("size_ok"))),
        "volume_rate": _rate(sum(scored("volume_ok")), len(scored("volume_ok"))),
        "holes_rate": _rate(sum(scored("holes_ok")), len(scored("holes_ok"))),
        "holes_scored": len(scored("holes_ok")),
        "seconds": {"mean": round(sum(secs) / n, 3) if n else 0.0, "p50": round(_pct(secs, 0.5), 3), "p95": round(_pct(secs, 0.95), 3), "total": round(sum(secs), 2)},
        "tokens": {"in": tin, "out": tout, "per_part": round((tin + tout) / n, 1) if n else 0.0},
        "cost_usd": None if cost is None else round(cost, 6),
        "cost_usd_per_part": None if cost is None or not n else round(cost / n, 6),
        "retries": sum(o.retries for _, o, _ in results),
        "by_type": {t: block(v) for t, v in sorted(by_type.items())},
        "failures": [{"id": c["id"], "prompt": c["prompt"], "completed": s["completed"], "why": s["why"]} for c, _, s in results if not s["correct"]],
    }


def compare(now: dict, before: dict) -> dict:
    """Change since a saved run. Positive is better for rates and worse for seconds."""
    def d(a, b):
        return None if a is None or b is None else round(a - b, 4)
    return {"completion_rate": d(now["completion_rate"], before.get("completion_rate")),
            "correct_rate": d(now["correct_rate"], before.get("correct_rate")),
            "seconds_mean": d(now["seconds"]["mean"], (before.get("seconds") or {}).get("mean")),
            "cost_usd_per_part": d(now.get("cost_usd_per_part"), before.get("cost_usd_per_part"))}


def load_cases(path: Path = CASES) -> tuple[list[dict], dict]:
    data = json.loads(path.read_text())
    return data["cases"], {**DEFAULT_TOLERANCES, **(data.get("tolerances") or {})}


def run(cases: list[dict], build: Callable[[str], Outcome], tol: dict, *, progress: Callable[[int, int, dict, dict], None] | None = None
        ) -> list[tuple[dict, Outcome, dict]]:
    results = []
    for i, case in enumerate(cases, 1):
        try:
            out = build(case["prompt"])
        except Exception as exc:  # noqa: BLE001
            out = Outcome(success=False, error=f"{type(exc).__name__}: {exc}")
        score = score_case(case, out, tol)
        results.append((case, out, score))
        if progress:
            progress(i, len(cases), case, score)
    return results


# ------------------------------------------------------------------ output
def _pc(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def markdown(s: dict, delta: dict | None = None) -> str:
    cost = "not measured (no prices given)" if s["cost_usd_per_part"] is None else f"${s['cost_usd_per_part']:.5f} per part"
    if s["mode"] == "rules":
        cost = "$0 (no AI used)"
    lines = [f"# Geometry benchmark{' ' + s['label'] if s['label'] else ''}", "",
             f"{s['cases']} prompts, {s['mode']} parser. Every prompt is built with CadQuery and the finished solid is measured.", "",
             "| Measure | Result |", "|---|---|",
             f"| Completion (valid single solid) | {_pc(s['completion_rate'])} |",
             f"| Correct (right size, volume and holes) | {_pc(s['correct_rate'])} |",
             f"| Size right | {_pc(s['size_rate'])} |", f"| Volume right | {_pc(s['volume_rate'])} |",
             f"| Hole count right | {_pc(s['holes_rate'])} (scored on {s['holes_scored']}) |",
             f"| Seconds per part | {s['seconds']['mean']:.2f} mean, {s['seconds']['p50']:.2f} median, {s['seconds']['p95']:.2f} p95 |",
             f"| Cost | {cost} |"]
    if s["mode"] == "llm":
        lines.append(f"| Tokens per part | {s['tokens']['per_part']:.0f} |")
    if delta:
        lines += ["", "Change since the baseline run: completion " + _signed(delta["completion_rate"]) + ", correct " + _signed(delta["correct_rate"])
                  + ", seconds per part " + (_signed(delta["seconds_mean"], pct=False) or "n/a") + "."]
    lines += ["", "| Part | Prompts | Completion | Correct |", "|---|---|---|---|"]
    lines += [f"| {t} | {b['cases']} | {_pc(b['completion'])} | {_pc(b['correct'])} |" for t, b in s["by_type"].items()]
    lines += ["", "Tolerances: size within 0.5% or 0.1 mm, volume within 1.5%, hole count exact. Cases are held out from the parser tuning set."]
    return "\n".join(lines) + "\n"


def _signed(x: float | None, pct: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f} points" if pct else f"{x:+.2f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--label", default="", help="a name for this run, for example the release")
    ap.add_argument("--only", help="run only this part type")
    ap.add_argument("--limit", type=int, help="run only the first N cases")
    ap.add_argument("--formats", default="step,stl", help="export formats to build (default step,stl)")
    ap.add_argument("--price-in", type=float, help="dollars per million input tokens")
    ap.add_argument("--price-out", type=float, help="dollars per million output tokens")
    ap.add_argument("--out", help="write the full results as JSON")
    ap.add_argument("--markdown", help="write a summary table to publish")
    ap.add_argument("--baseline", help="a results JSON from an earlier run to compare with")
    ap.add_argument("--max-regression", type=float, help="exit 1 if the correct rate fell by more than this against --baseline (0.02 is two points)")
    ap.add_argument("--fail-under-completion", type=float, default=0.0)
    ap.add_argument("--fail-under-correct", type=float, default=0.0)
    ap.add_argument("--verbose", action="store_true", help="print every miss as it happens")
    args = ap.parse_args(argv)

    cases, tol = load_cases()
    if args.only:
        cases = [c for c in cases if c["part_type"] == args.only]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        sys.exit("no cases selected")

    with tempfile.TemporaryDirectory(prefix="nitocad-bench-") as tmp:
        build = make_builder(args.mode, Path(tmp), [f.strip() for f in args.formats.split(",") if f.strip()])
        build("cube 10 mm")                                    # warm the kernel so the first case is not charged for the import

        def progress(i: int, n: int, case: dict, score: dict) -> None:
            if args.verbose and not score["correct"]:
                print(f"MISS {case['id']}  {case['prompt']!r}\n     {'; '.join(score['why']) or 'did not match'}")
            elif not args.verbose and i % 25 == 0:
                print(f"  {i}/{n}", file=sys.stderr)

        results = run(cases, build, tol, progress=progress)

    summary = summarize(results, price_in=args.price_in, price_out=args.price_out, mode=args.mode, label=args.label)
    delta = compare(summary, json.loads(Path(args.baseline).read_text())["summary"]) if args.baseline else None
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "cases": [
            {"id": c["id"], "prompt": c["prompt"], "part_type": c["part_type"], "score": s,
             "measured": {"size_mm": o.size_mm, "volume_mm3": o.volume_mm3, "holes": o.holes, "seconds": round(o.seconds, 3),
                          "tokens_in": o.tokens_in, "tokens_out": o.tokens_out, "retries": o.retries, "parser": o.parser}}
            for c, o, s in results]}, indent=1))
    if args.markdown:
        Path(args.markdown).write_text(markdown(summary, delta))

    print(markdown(summary, delta))
    if args.baseline and args.max_regression is not None and delta and delta["correct_rate"] is not None and delta["correct_rate"] < -args.max_regression:
        print(f"Correct rate fell {abs(delta['correct_rate']):.1%}, more than the {args.max_regression:.1%} allowed.", file=sys.stderr)
        return 1
    if (summary["completion_rate"] or 0) < args.fail_under_completion or (summary["correct_rate"] or 0) < args.fail_under_correct:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

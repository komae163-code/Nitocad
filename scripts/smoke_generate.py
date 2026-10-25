#!/usr/bin/env python3
"""
Staging smoke test for the generator, with no HTTP, wallet or credits involved.

Calls CADGenerator.generate_from_text directly for one request per new capability and reports, per
case: success, parser used, part type, whether every exported file exists and is non-empty, any
validation warnings, and the error. Uses a throwaway database and output folder.

    DEEPSEEK_API_KEY=... python scripts/smoke_generate.py          # all cases (needs the key)
    python scripts/smoke_generate.py --offline                      # only the cases that work without an LLM

The AI-parsed cases can legitimately vary run to run; a FAIL here means "look at it", not "broken".
Exit status is non-zero if any case fails.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (label, description, needs_llm, expected_part_types)
CASES = [
    ("legacy shaft", "a steel shaft 20 mm diameter and 100 mm long", False, {"shaft"}),
    ("socket head screw", "M8 socket head cap screw, 30 mm long", True, {"fastener"}),
    ("deep groove bearing", "6205 deep groove ball bearing", True, {"std_bearing"}),
    ("pipe flange", "ASME class 150 slip-on flange for NPS 4 pipe", True, {"flange"}),
    ("o-ring", "O-ring 30 mm inside diameter with a 3.53 mm cross section", True, {"o_ring"}),
    ("parallel key", "DIN 6885 parallel key for a 25 mm shaft, 40 mm long", True, {"key"}),
    ("plate with holes (feature tree)",
     "rectangular plate 100 by 60 by 8 mm with four 6 mm through holes, each 10 mm in from the corners",
     True, {"feature_tree", "flat_plate"}),
    ("bent pipe (sweep)",
     "pipe 50 mm outside diameter, 3 mm wall, going 300 mm straight up, then 90 degrees sideways for 200 mm "
     "with a 100 mm bend radius", True, {"feature_tree"}),
    ("shaft, bearing and key assembly",
     "assembly: a 25 mm diameter 80 mm long shaft, a 6205 bearing sitting 20 mm from one end, "
     "and a parallel key", True, {"assembly"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="only run cases that need no LLM")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="nitocad_smoke_")
    os.environ["DB_PATH"] = str(Path(tmp) / "smoke.db")
    from cad_generator import CADGenerator  # imported after DB_PATH is set
    import db

    if hasattr(db, "init_db"):
        db.init_db()
    gen = CADGenerator(output_dir=str(Path(tmp) / "out"))
    have_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    if not have_key and not args.offline:
        print("DEEPSEEK_API_KEY is not set: the AI-parsed cases will fall back to the regex parser and "
              "mostly fail. Set it, or pass --offline.\n")

    failures = 0
    for label, text, needs_llm, expected in CASES:
        if needs_llm and args.offline:
            print(f"SKIP  {label}  (needs the LLM)")
            continue
        r = gen.generate_from_text(text, formats=["step", "stl"], user_id="smoke")
        ptype = (r.get("parameters") or {}).get("part_type")
        files = [r.get("step_file"), r.get("stl_file")]
        files_ok = r.get("success") and all(f and Path(f).exists() and Path(f).stat().st_size > 0 for f in files)
        warns = (r.get("validation") or {}).get("warnings") or []
        ok = bool(r.get("success") and files_ok and (ptype in expected))
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        print(f"      part_type={ptype!r} (expected one of {sorted(expected)})  parser={(r.get('provenance') or {}).get('parser')}")
        if warns:
            for w in warns:
                print(f"      warning: {w}")
        if not r.get("success"):
            print(f"      error: {r.get('error_type')}: {r.get('error')}")
        elif not files_ok:
            print("      exported files missing or empty")
    print(f"\n{len(CASES) - failures} of {len(CASES)} cases passed (skipped cases count as passed in --offline mode)")
    print(f"artifacts: {tmp}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

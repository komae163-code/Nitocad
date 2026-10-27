#!/usr/bin/env bash
# Separate regressions from failures that already existed.
#
#   bash scripts/compare_baseline.sh /path/to/original/Nitocad-main
#
# Runs the full test suite in the baseline tree and in this tree (same Python environment), then
# prints three lists: failing ONLY here (regressions), failing ONLY in the baseline (fixed), and
# failing in BOTH (pre-existing or environment problems). Use the environment you verified in
# (the venv from scripts/verify_env.sh): `. .venv/bin/activate` first.
set -u
# Environment variables leak into the suite: PORT=4242 breaks the config-defaults test, and a shared
# DB_PATH makes wallet tests see each other's credits. Re-exec once in a clean environment.
if [ -z "${NITOCAD_CLEAN_ENV:-}" ]; then
  exec env -i HOME="$HOME" PATH="$PATH" TMPDIR="${TMPDIR:-/tmp}" NITOCAD_CLEAN_ENV=1 bash "$0" "$@"
fi
BASE="${1:?usage: compare_baseline.sh /path/to/baseline-tree}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/verify_logs"; mkdir -p "$OUT"
PY=python; command -v python >/dev/null 2>&1 || PY=python3

run() { # tree, outfile
  ( cd "$1" && "$PY" -m pytest -q -rfE -p no:cacheprovider tests >"$2.full" 2>&1 )
  grep -E "^(FAILED|ERROR) " "$2.full" | sed -E 's/ - .*$//' | sort -u > "$2"
}
echo "running baseline: $BASE"; run "$BASE" "$OUT/baseline_failures.txt"
echo "running current:  $HERE"; run "$HERE" "$OUT/current_failures.txt"

echo; echo "== REGRESSIONS (fail only in the current tree) =="; comm -13 "$OUT/baseline_failures.txt" "$OUT/current_failures.txt"
echo; echo "== FIXED (fail only in the baseline) ==";            comm -23 "$OUT/baseline_failures.txt" "$OUT/current_failures.txt"
echo; echo "== PRE-EXISTING / ENVIRONMENT (fail in both) ==";    comm -12 "$OUT/baseline_failures.txt" "$OUT/current_failures.txt"
echo; echo "totals: baseline $(wc -l < "$OUT/baseline_failures.txt") failing, current $(wc -l < "$OUT/current_failures.txt") failing"

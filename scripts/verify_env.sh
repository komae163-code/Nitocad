#!/usr/bin/env bash
# NitoCAD environment verification.
#
# Runs, in order: CadQuery import, the pure-Python suites, the CadQuery geometry suites, the
# full test suite, and (if present) the phase acceptance scripts. Writes everything to
# verify_logs/ and prints a summary that separates PASSED from SKIPPED. A skipped test is
# NOT verified; the summary says so.
#
#   bash scripts/verify_env.sh              # create .venv, install, run everything
#   bash scripts/verify_env.sh --no-install # reuse the current environment
#   bash scripts/verify_env.sh --acceptance # also run scripts/run_phase*_acceptance.py
#
set -u
cd "$(dirname "$0")/.."

INSTALL=1
ACCEPT=0
for arg in "$@"; do
  case "$arg" in
    --no-install) INSTALL=0 ;;
    --acceptance) ACCEPT=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

LOG=verify_logs
rm -rf "$LOG"; mkdir -p "$LOG"
SUMMARY="$LOG/summary.txt"
: > "$SUMMARY"
say() { echo "$*" | tee -a "$SUMMARY"; }

# ---- interpreter -----------------------------------------------------------
if [ "$INSTALL" = 1 ]; then
  BASE_PY=""
  for c in python3.11 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1; then BASE_PY="$c"; break; fi
  done
  [ -n "$BASE_PY" ] || { say "FATAL: no python3 found"; exit 2; }
  ver=$("$BASE_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  case "$ver" in
    3.11|3.12) ;;
    *) say "FATAL: Python $ver found; this project needs 3.11 or 3.12 (OCP wheels)."; exit 2 ;;
  esac
  say "python: $BASE_PY ($ver)"
  "$BASE_PY" -m venv .venv || { say "FATAL: could not create venv (apt install python3-venv)"; exit 2; }
  # shellcheck disable=SC1091
  . .venv/bin/activate
  python -m pip install --upgrade pip >"$LOG/pip_upgrade.log" 2>&1
  say "installing requirements (this can take 10-30 minutes on a phone)..."
  if ! pip install -r requirements.txt >"$LOG/pip_install.log" 2>&1; then
    say "full requirements failed (see $LOG/pip_install.log); retrying with the minimum needed for the tests"
    pip install "cadquery==2.3.1" "numpy>=1.26.4,<2.0" ezdxf reportlab fastapi pydantic \
      pydantic-settings python-multipart jinja2 openai httpx slowapi boto3 pytest \
      >"$LOG/pip_install_min.log" 2>&1 || say "WARNING: minimal install also failed (see $LOG/pip_install_min.log)"
  fi
fi
PY=python
command -v python >/dev/null 2>&1 || PY=python3

FAILED=0
stage() { # name, logfile, command...
  local name="$1" file="$2"; shift 2
  "$@" >"$LOG/$file" 2>&1
  local rc=$?
  local tail_line
  tail_line=$(grep -E "passed|failed|error|skipped" "$LOG/$file" | tail -1)
  if [ $rc -eq 0 ]; then say "PASS   $name   ${tail_line}"; else say "FAIL   $name   ${tail_line}   (see $LOG/$file)"; FAILED=1; fi
  if grep -qiE "[0-9]+ skipped" "$LOG/$file"; then
    say "       note: some tests were SKIPPED in '$name'; skipped means NOT verified (reasons in $LOG/$file)"
  fi
}

say "---- stage 1: CadQuery ----"
stage "import cadquery" cq_import.log "$PY" -c "import cadquery; print('cadquery', cadquery.__version__, 'passed')"

say "---- stage 2: pure-Python suites ----"
stage "planner, standards, feature tree, mates" pure.log \
  "$PY" -m pytest -q -rs -p no:cacheprovider \
  tests/test_assembly_planner.py tests/test_standards_data.py tests/test_feature_tree.py \
  tests/test_single_part_feature_tree.py tests/test_mates.py tests/test_building.py tests/test_building_domain.py

say "---- stage 3: CadQuery geometry suites ----"
stage "feature builder, standard parts, interference, mates (real geometry)" geometry.log \
  "$PY" -m pytest -q -rs -p no:cacheprovider \
  tests/test_feature_builder.py tests/test_standard_parts.py \
  tests/test_assembly_interference.py tests/test_assembly_mates_cq.py tests/test_building_geometry.py tests/test_building_dxf.py

say "---- stage 4: full suite ----"
stage "entire tests/ directory" full.log "$PY" -m pytest -q -rs -p no:cacheprovider tests

if [ "$ACCEPT" = 1 ]; then
  say "---- stage 5: phase acceptance scripts ----"
  shopt -s nullglob
  found=0
  for s in scripts/run_phase*_acceptance.py; do
    found=1
    stage "$(basename "$s")" "$(basename "$s").log" "$PY" "$s"
  done
  [ "$found" = 0 ] && say "no scripts/run_phase*_acceptance.py in this tree (they live in the engineering-core line)"
fi

say "----"
if [ "$FAILED" = 0 ]; then say "RESULT: all stages passed (check the SKIPPED notes above)"; else say "RESULT: FAILURES; send $SUMMARY and the failing logs"; fi
exit "$FAILED"

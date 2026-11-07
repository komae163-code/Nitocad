# Merge: conversational vehicles + codegen / studio hookup / admin

Joined two branches that both grew out of the 2026-10-31 zip.

- Branch 1 (Nitocad-main-merged + admin patch): codegen spike, codegen_chat, asset ranking, step merge,
  car / wheelie bin / light airplane / mini engine examples, admin dashboard, WAL.
- Branch 2 (Nitocad-main.zip): conversational vehicle builds, tapered_tray, tube origin fix, piston engine modules.

## How it was merged
- Files only one side touched were taken from that side.
- `conversation.py`, `frontend/studio.js` and `vehicle_planner.py` were touched by both and merged line by line
  against the 2026-10-31 originals. No conflicts.
- `data/spec_library/*` from branch 2 was left out. It is runtime data and `.gitignore` already excludes it.

## Two engine families now ship
Branch 1 has `mini_engine_single` and `mini_engine_twin` (module `piston_slider`). Branch 2 has
`mini_engine_single_cylinder`, `inline_two` and `inline_four` (modules `piston_engine_1/2/4`). Both are kept.
A plain-words "two cylinder engine" now finds `mini_engine_inline_two` first. Pick one family to keep and delete the
other (files, plus its lines in `tests/test_vehicle_new_examples.py`) when you decide.

## Two test edits
- `test_an_example_of_a_different_kind_is_never_handed_back` used an airplane. An airplane now has a real example, so it
  uses a truck, which still has none.
- `test_new_examples_seed_the_library_and_plain_words_find_them` accepts either engine family for the cylinder-count queries.

## Not run
CadQuery, pydantic and FastAPI were missing, so only the pure-Python vehicle tests ran. Run `pytest` and
`node --test tests/js/*.mjs` before deploying. Branch 2's own notes still apply: the tapered tray and tube origin are
unverified on the real kernel, so generate the wheelbarrow and look at it.

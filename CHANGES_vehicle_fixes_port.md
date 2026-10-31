# Vehicle fixes ported into the merged studio build

Base: **Nitocad-merged-final**. Source of the fixes: **Nitocad-vehicle-fixes**. The two had diverged (vehicle-fixes is built on
the older `vehicle_studio.py` layer and has no picture tracing; merged-final has `vehicle_chat.py`, `vehicle_api.py`,
`vehicle_views.py` and the newer tracer), so the fixes were re-implemented against merged-final's code, not copied over it.

## Ported
| Fix | Where | Notes |
|---|---|---|
| Planning time budget | `vehicle_planner.py` (`_run_loop`, `plan_vehicle`, `revise_vehicle`), `vehicle_chat.py`, `config.py` | `timed_out` is returned; a timeout is free. |
| Package cache | `package_cache.py` (new), `vehicle_api.py`, `config.py` | Key is the plain vehicle spec (merged-final stores it that way). Adds a 40 MB per-package cap and cleans its locks on a failed build. The route now returns the zip from memory instead of a `FileResponse`. |
| Free size edits | `vehicle_chat.py` (`_edit_rules`, `apply_param_edit_rules`, `_edit`) | merged-final already had `apply_param_edit_rules` as a fallback, so this extends it instead of adding `rule_edit`: relative edits (by amount or %), "900 long" style dimension words, and a free path tried before the model when every word of the message is accounted for. |
| Library review queue | `spec_library.py`, `vehicle_chat.get_library`, `config.py` | In merged-final the library learns from Generate (`on_generated`), so Generate is what queues. The CLI reads the same folder the app uses. Queued designs are exempt from the 300-candidate prune. |
| 2D fallback in the exploded viewer | `frontend/exploded.html` | Keeps merged-final's `nitocad-exploded` message format (vehicle-fixes used a different one). |
| Check button price | `frontend/studio.js` (`checkCardHtml`) | Fixes the two failing `studio_panels` tests. |

## Not ported, and why
- **Exploded-view message listener leak.** merged-final's studio has no window message listener for the viewer. It hands the
  model over with `frame.onload = send`, which replaces itself and cannot accumulate. Nothing to fix.
- **Vehicle feedback toasts.** merged-final has no thumbs-up button or `vehicle-feedback` route. The library learns from
  Generate instead, so there are no toasts to correct.
- **`vehicle_studio.py` and `tests/test_vehicle_studio.py`, `tests/js/vehicle_tab.test.mjs`.** The older layer that
  `vehicle_chat.py` replaces. The behaviours worth keeping were moved over and have new tests in `test_vehicle_chat.py`.
- **`SqlStore` (library kept in the app database).** Not part of the list asked for, and merged-final keeps the library in
  `VEHICLE_LIBRARY_DIR`. On a host with no persistent disk the library is memory-only and the review tool has nothing to see.
- **`VEHICLE_FETCH_EXTERNAL`.** vehicle-fixes made fetching outside parts into the package opt-in (default off). merged-final
  always fetches (`fetch=True` in `vehicle_api.py`). Left as it was; it is now one constant, `_FETCH_EXTERNAL`.

## Tests
New: planner time budget (5), review queue and review tool (7), package cache (9), free and relative size edits,
time budget in chat, review on the app library (12). Python: 472 passed here, 0 new failures. JS: 134 of 134 pass
(two failed before).

## Not run here
No CadQuery, FastAPI or pydantic in this sandbox, so these did not run: the `vehicle_api.py` package route (only its cache
helper is tested), `test_vehicle_chat_service.py` and the other 32 tests that were already failing for the same reason
before any change, and the studio in a browser. The exploded viewer was run in Node against a fake DOM, with and without
three.js. Run `pytest` and `node --test tests/js/*.mjs` on a real install first.

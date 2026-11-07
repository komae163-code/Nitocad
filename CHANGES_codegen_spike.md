# Code-first CAD (prompt -> CadQuery code -> solid, sliders re-run for free)

The AI writes one small CadQuery file (a `PARAMS` literal + `def build(p)`), the server validates it, runs it in a locked-down
subprocess, and the page shows a preview with sliders. Slider moves re-run the SAME code: no AI call, no charge. A failed build goes
back to the model with the error and line number, up to 3 tries. This sits beside the template/spec/module pipeline and does not replace it.

## What changed since the first spike
1. Billing. Generate and revise reserve `ai_models.generate_credits(settings, tier)` through `db.charge_credits(..., ai_tier=tier)` BEFORE the
   AI runs, and refund through `db.refund_credits` whenever no saved model results (AI failure, build failure, busy kernel, save failure).
   402 when the person is out of allowance (same wording as the chat). Models are limited to the plan (`allowed_for_plan`); with no
   `model_id` the strongest model the plan allows is used. Rebuild, export, list, code and delete are free.
2. Storage. `codegen_store.py` keeps models in the app's SQLite database (same pattern as chat_store), so they survive restarts on your
   volume. Each revision is a new row linked to its parent. 300 models per person.
3. Preview and front end. Builds return JSON plus a short-lived `mesh_url`; the browser fetches plain binary STL (no base64). New page
   `frontend/codegen.html`: prompt, live preview (Studio's viewer look), one slider per parameter, revise-with-words, STEP / STL / zip
   downloads, saved-model list, "view the code".
4. Library parts. Code can declare `LIBRARY = {"bearing": {"query": "608 bearing"}}` and take `build(p, lib)`. The server (never the code) searches
   FreeCAD-library STEP parts via `asset_providers.py` (licence-gated, https allowlist, size cap), downloads them, pins URL + SHA-256, looks
   up each part's author credit, and hands the imported solid (centred on the origin) to the sandbox as a file. Saved models re-use the pinned
   file and refuse to rebuild if upstream changed. A `package` download adds NOTICES.txt; files are withheld while any author credit is unresolved.

## Files
New: `codegen_check.py`, `codegen_worker.py`, `codegen_run.py`, `codegen_llm.py`, `codegen_parts.py`, `codegen_store.py`, `codegen_api.py`,
`frontend/codegen.html`, `tests/test_codegen.py`, `test_codegen_store.py`, `test_codegen_parts.py`, `test_codegen_api.py`, `test_codegen_kernel.py`.
Edited: `web_app.py` (three small additions below). Nothing else existing was touched.

## Wire it in (web_app.py)
    import codegen_api            # next to `import vehicle_api`
    import codegen_store
    # in lifespan(), after chat_store.init_tables():
    codegen_store.configure(db.get_conn)
    codegen_store.init_tables()
    # next to the vehicle_api include_router line:
    app.include_router(codegen_api.build_router(limiter=limiter))
Then link `codegen.html` from the nav wherever you want it. The page is `noindex`. Add the file to `tools/seo_build.py`'s private list if that script lists pages.
Optional env: CODEGEN_TIMEOUT_S (60), CODEGEN_MEM_MB (2048), CODEGEN_PART_CACHE (temp dir), CODEGEN_PART_MAX_MB (30).

## Security model, honestly
This runs code an AI wrote from a stranger's prompt. file_import.py's old rule ("no user code is ever executed") no longer holds for these routes.
1. People cannot post code: a prompt goes to the AI, numbers go to the sliders. Only validated AI code is stored or run.
2. Static validator: only `cadquery` and `math`, no underscore attributes, no file / export / import attributes, no unknown names, no classes / with / yield.
3. In the worker: restricted builtins, an import hook that only returns cadquery and math, a `cadquery` copy without exporters / importers / occ_impl.
4. Subprocess: empty environment (no API keys), memory / CPU / file-size limits, wall-clock kill of the process group.
5. Library files are downloaded by the server, never by the code, and enter the sandbox only as bytes in its work folder.
Layers 2 and 3 are Python-level and could have gaps: treat layer 4 as the boundary. Before this is public, also deny the container outbound network
and run the worker as an unprivileged user.

## NOT verified (read this before shipping)
- Nothing has run against the real CadQuery kernel, a real model, a real browser, or the live GitHub / FreeCAD-library services. The sandbox this
  was written in had none of them (and no FastAPI). Offline tests cover the logic with fakes: validator, worker, repair loop, store (real SQLite),
  resolver (fake network), API routes (fake billing / store / model / kernel). `tests/test_codegen_api.py` and `test_codegen_kernel.py` skip themselves
  where FastAPI / CadQuery are missing: run them on your machine first.
- `frontend/codegen.html` was syntax-checked only. Try it in a browser: sign in, generate, drag a slider, download each format.
- FreeCAD-library naming is unknown to me: a query like "608 bearing" may find nothing. The model is told and models the part itself.
- Only STEP library parts are used. Parametric cq_warehouse parts need that package installed first; meshes are reference-only and skipped.
- Credits are in the ledger that `db.charge_credits` uses. I did not run the ledger; the tests only prove WHEN reserve and refund happen.
- Prompt quality is untested. Adding 3 to 5 verified code samples per object type will help more than anything else.

## First real run
1. `pytest tests/test_codegen*.py` on a machine with CadQuery and FastAPI.
2. Sign in, open /codegen.html, generate "a wheelie bin with a hinged lid", drag a slider, download the zip.
3. Watch the logs line `codegen ok=... attempts=... model=... tokens=...` to see repair-loop cost, and check that a failed build refunds.

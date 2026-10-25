# Import & edit workspace (`frontend/import.html`)

One upload button for every supported type. Each file is detected by name AND contents (a `.step` that is
really HTML is refused), size-limited per type, then opened by the matching editor. Everything except
STEP/IGES/BREP runs in the browser and is never uploaded.

## Status per type
| Type | Where it runs | Status |
|---|---|---|
| STL, OBJ | browser | **Working, browser-tested**: move, rotate, scale, mirror, centre, drop to Z=0, flip normals, undo, export STL/OBJ |
| STEP, IGES, BREP | server (sandboxed worker) | **Orchestration tested; real CAD kernel NOT run** (no cadquery in the build sandbox). Edits: move, rotate, scale, mirror, centre, fillet/chamfer all edges. Export STEP/BREP/STL |
| DXF | browser | Engine **tested** (parse, byte-exact round trip, move/rotate/scale/delete/layer/add). **Editor UI not built** |
| SVG | browser | Sanitiser **tested** (scripts, handlers, foreignObject, external links, entity bombs). **Editor UI not built** |
| KiCad (.kicad_*) | browser | Engine **tested** (parse/serialise, PCB model, move/rotate/delete, ref/value edit). **Editor UI not built** |
| Gerber, drill, zip bundle | browser | Engine **tested** (parse, macros, arcs, move/mirror/rotate rewrite, Excellon, zip). **Editor UI not built** |
| PDF | browser | **Not built** (needs pdf.js and pdf-lib from a CDN, which cannot be tested offline) |
| PNG, JPG, WEBP | browser | Image maths **tested**. **Editor UI not built** |

Opening a type whose editor is not built shows: "the X editor is not available yet in this build".

## Server routes (B-rep only)
- `POST /convert` (file, optional `ops`) -> STL. This route did not exist before: the preview page called it and got 404.
- `POST /api/files/cad/export` (file, `ops`, `format` = step|brep|stl).
- Limits: 25 MB upload, content sniffing, 20 ops, fixed operation list (no user code), 2 concurrent jobs then 503,
  90 s timeout, memory/CPU/output limits, environment variables are **not** passed to the worker.

## Running the tests
    node --test tests/js/*.test.mjs
    pytest tests/test_file_import.py tests/test_aerospace_core.py tests/test_aerospace.py
    python3 tests/ui/run_app_ui.py        # needs playwright + chromium

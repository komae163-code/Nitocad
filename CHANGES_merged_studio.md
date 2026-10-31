# Merge: Nitocad-merged-vehicle-studio + Nitocad-studio-vehicles

Base: **Nitocad-merged-vehicle-studio** (studio UI, vehicle conversation layer, `vehicle_api.py`, `vehicle_views.py`).
Ported from **Nitocad-studio-vehicles**: the newer picture tracer, DXF reader, library hardening and spec robustness.
Everything outside vehicles and pictures was already identical.

## Taken from studio-vehicles
`raster_trace.py`, `image_import.py`, `profiles.py` (splines, ellipses, blocks, mirrored extrusion), `import_ops.py`
(traced pictures are an `image` profile kind), `file_analysis.py`, `file_api.py` (`/files/analyze` options, PDF tracing,
capabilities `image_tracing` and `pdf_tracing`), `spec_library.py` (candidates capped at 300), `vehicle_spec.py` (derived
values in any order), `requirements.txt` (`opencv-python-headless`), the four example vehicle JSONs, `tools/seo_build.py`
(exploded.html is noindex), and their tests.

## Kept from merged-vehicle-studio
`vehicle_chat.py` (Q&A, plain-sentence edits without an AI, diffs), `vehicle_api.py`, `vehicle_views.py`, the studio UI
(`studio.html|js`, `exploded.html` with same-origin `postMessage`), `chat_service._keep_vehicle`, the assembly-gate skip.

## New in the merge
- `vehicle_chat.on_generated` and its call in `chat_api` (Generate): the library learns. The chat spec carries
  `parameters.library_id`, kept across edits and by `restore_vehicle`.
- `vehicle_planner.py`: AI plans are stored with no description, and free-text `notes` stay out of prompts.
- `config.VEHICLE_LIBRARY_DIR`, read by the library (falls back to the environment variable, then `data/spec_library`).
- `studio_templates.py`: Vehicles category (wheelbarrow, bicycle, two-axle cart, RC helicopter) built with
  `vehicle_chat.to_chat_spec`; the lookup table is rebuilt after they are added. The locomotive needs the CAD kernel, so
  it stays a chat starter and is not a template.
- Studio file card: real size (width, height or mm per pixel), trace mode, keep-biggest-shape, **Trace again**, and
  **Trace page 1** for PDFs (`traceControlsHtml`, `retraceFile`). The upload is kept in the tab only.
- Tests: `on_generated` (3), planner privacy (1), vehicle templates (1), trace controls (2 JS). Template-wide shaft tests
  skip vehicles, as vehicles are not shaft assemblies.

## Behaviour change to know about
A picture is now stored as an `image` profile, not `svg`. It still edits and extrudes like a DXF. With no size given it is
returned at 1 pixel = 1 mm and the card says so until the person enters a real size.

## Run state
Run with a stand-in for pytest (no network): vehicle spec/chat/planner/views/build, library, tracing, image import, DXF,
templates, conversation, mates, exploded, package: all pass. The merged tree has no new failures against the base.
Not run here (no CadQuery, FastAPI or pydantic): `test_file_api.py`, `test_vehicle_chat_service.py`, `test_file_import.py`,
CAD-kernel tests, the real HTTP routes, and the studio in a browser. Two `studio_panels` JS tests fail in both original
zips as well (the check card price and process); they were not touched. Run `pytest` and `node --test tests/js/*.mjs`.

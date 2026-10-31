# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - One project from Nitocad-merged (3.9.0 + packs) and Nitocad-merged-v8 (mechanical, building, feature tree)

Base is Nitocad-merged (master-v7 with the PCB, aerospace and automotive packs). The mechanical, building and
feature-tree work from Nitocad-merged-v8 was ported on top. Both grew from 3.6.0.

### Vehicles and picture tracing in the studio (merge of Nitocad-merged-vehicle-studio and Nitocad-studio-vehicles)
- Studio: the Assembly view starts a vehicle from five starters or free text, the chat shows the plan and edits it, the
  Specs tab shows sizes, groups and design checks, the Files tab has the Exploded view and the Vehicle package (after
  Generate). A **Vehicles** category in Templates lists the wheelbarrow, bicycle, two-axle cart and RC helicopter.
- Generate teaches the vehicle library (`vehicle_chat.on_generated`). An AI plan is marked accepted; a version the person
  changed by chatting is added as its own accepted record. The person's own words are never stored, and free-text notes
  stay out of prompts. `VEHICLE_LIBRARY_DIR` is now a real setting (empty = `data/spec_library`, memory if unwritable).
- Pictures: the newer tracer (silhouette and line-drawing modes, scale options, PDF page 1), the image import layer and the
  wider DXF reader replace the earlier simple tracer. The file card has a **Trace again** form: real width, height or
  mm per pixel, trace mode, keep-biggest-shape, and **Trace page 1** for PDFs. Free, no AI.
- Derived vehicle values resolve in any order, so a spec read back from a database (sorted keys) still validates.

### Vehicle robustness (ported from Nitocad-vehicle-fixes)
- Planning has a time budget (`VEHICLE_PLAN_TIME_BUDGET_S`, default 120 s) shared by all model retries. No retry starts with
  under 25 s left. A timeout builds nothing and charges nothing; the chat says it took too long and, for a new vehicle,
  offers the closest built-in example. One model call is still limited only by `AI_TIMEOUT_SECONDS`.
- Built vehicle packages are cached in memory (`package_cache.py`, `VEHICLE_PACKAGE_CACHE_TTL_S`, default 900 s, 0 = off).
  The payment check always runs first. Packages over 40 MB are served but not kept. Per process.
- Size edits are free and need no AI: "set tray_len to 900", "make the tray 900 long", "increase the wheel diameter by
  10%", "decrease the track by 50 mm". A message that is only sizes is answered by the rules even when an AI is selected;
  anything that also asks for parts still goes to the model. "Increase ... by N" was previously read as "set to N".
  Whole-number sizes reject fractions.
- Library review queue (`VEHICLE_LIBRARY_REVIEW`, default on): pressing Generate on a planned vehicle queues it instead of
  making it an example other people's prompts can see. `python spec_library.py pending | show <id> | approve <id> | decline <id>`.
  A queued design is never pruned to make room. Approve refuses a spec whose own design checks fail.
- Exploded view: a flat 2D side view when three.js or WebGL is unavailable, so the page is never blank.
- Studio: the manufacturability check button shows its price again ("Run check · 5 credits"). This fixes the two
  `studio_panels` JS tests that failed in every earlier build.

### Vehicles in the studio chat

- Say what you want ("I want to create a wheelbarrow") and the chat builds a free preview of a vehicle assembly, then
  changes it as you talk ("make the tray 900 mm long", "make the handles 100 mm longer"). Planning and editing stay free;
  only Generate costs credits, exactly as before. With the Engine plan (no AI) the request is matched to a shipped example
  and sizes change by plain sentences; with an AI plan the model drafts the vehicle and is checked and corrected.
- New Vehicles category in Templates and in the Assembly view: wheelbarrow, bicycle and RC helicopter (with internals).
  Vehicles are concept layouts: the chat says so, and nothing about them is load-rated or flight-tested.
- Assembly details get an Exploded view button (parts drawn as bounding boxes, free, shows no paid files).
- No limit on the number of parts or mates in an assembly (the 12-line and 20-per-line chat limits, the 40 and 50 part
  limits and the 120 mate limit are gone). A guard at 20,000 parts stops a typo from hanging the server, and a chat
  preview is limited to 500 parts for the same reason. The 50-part limit of the aerospace assessment endpoint is unchanged.
- Generating a vehicle teaches its library: a version someone pays to generate becomes an example for similar requests.
  A person's own words are never stored with it. Set VEHICLE_LIBRARY_DIR to keep what it learns across restarts.
- Back in this build: picture and drawing to CAD (raster tracing, image import, the wider DXF reader and their tests).
- New modules: vehicle_chat, vehicle_planner, vehicle_spec, vehicle_build, spec_library, asset_providers,
  assembly_package, assembly_exploded.

### Picture and drawing to CAD (raster tracing, richer DXF)
Images and scanned drawings used to go to the vision model only, which returns a description. They now also produce
real geometry that extrudes, edits and exports like any DXF.
- `raster_trace.py`: traces PNG, JPG and WEBP into a flat profile. Two modes, chosen automatically: a filled silhouette
  (photo of a part, cut-out, transparent PNG whose alpha is used as the shape) or a line drawing (scan, plot, screenshot).
  Line drawings are moved out to the middle of the line using the line width measured around each region, so thin and
  thick lines both land on the true outline. Even-odd nesting gives holes. Round shapes stay exact circles (radius from
  pixel counting, no edge bias). A sheet border is dropped, text and specks are counted and ignored, and a noisy or
  textured picture is refused. Accuracy is about half a pixel and the report says what that is in millimetres.
- Size is never guessed. It comes from `scale_mm_per_px`, `overall_width_mm` or `overall_height_mm`. With none, the shape
  is returned at 1 pixel = 1 mm and flagged, so the normal "make it 150 mm long" edit sets it. A dimension the vision
  model reads is offered as a suggestion and never applied.
- `POST /files/analyze` takes the new form fields `scale_mm_per_px`, `overall_width_mm`, `overall_height_mm`,
  `trace_mode` (auto, silhouette, outline), `largest_only` (keep the biggest shape, for title blocks and several views) and
  `trace_pdf` (trace page 1 of a PDF drawing, scaled from the page size and flagged as right only at 1:1).
  `GET /files/capabilities` adds `image_tracing` and `pdf_tracing`, and lists pictures as editable when tracing is on.
  Tracing needs no vision key. Without tracing, images behave as before (503 when no vision model is on).
- `image_import.py`: the orchestration (trace, vision, combine), kept free of the web layer so it is tested without a
  server. A failed vision call keeps the traced shape, and a failed trace falls back to the vision notes.
- The file card shows how the shape was traced, where the size came from, the accuracy, and a preview of the traced
  outline (an image data URI, so nothing in it can run). The card builder now also shows the server's rows for flat shapes.
- `profiles.py` DXF reader: splines (rational B-splines and fit-point splines), ellipses and elliptical arcs, blocks and
  `INSERT` (nested, scaled, rotated, arrayed, with block base points, bounded by a depth and size limit), and the mirrored
  extrusion flag that flips arcs, circles and polylines in many exports. Units now apply through blocks. Entities that still
  cannot be read are reported as before.
- Dependency: `opencv-python-headless>=4.8,<4.11` (pinned for `numpy<2`).
- Tests: `test_raster_trace.py`, `test_image_import.py` and `test_profiles_dxf_extended.py` (new), plus updates to
  `test_file_api.py`. Pictures in the tests are drawn with known sizes, and DXF areas are worked out by hand.
- Not covered: multi-view orthographic drawings are not reconstructed into 3D (a drawing gives one flat profile),
  perspective photos are not rectified, and dimension annotations are read by the vision model, not by the tracer.

### Frontend design update (frontend-v14-writeup)
Visual layer only: the new write-up, colours and styles across the site. The site is now dark only (black with a navy
tint and glow), so the light/dark toggle and its code were removed from the Studio, `theme.js` no longer builds one, and
`theme.css`, `hero-blueprint.js` and the homepage have the new look. Pages the new zip did not include (`import.html`,
`aerospace.html`, `import/`) keep their own content and take the new colours through `theme.css`. No backend, API call or
Studio feature was changed. The Studio's added cards (check card, template cards, requirement fields, assembly table) were
given the same navy surface as the rest.

### Added from frontend-v8-with-studio-features (Studio frontend, with the backend it needed)
The new frontend differed from the merged one in `studio.js`, `studio.css` and `studio.html` only. The merged
frontend stays the base (it is the superset: requirements panel, PCB rows, DXF/SVG/KiCad/Gerber attach, the import and
aerospace pages). The new Studio work was ported into it, and the backend endpoints it calls were built.
- Templates: the Templates button and picker. `GET /templates` (catalogue in `studio_templates.py`, 26 templates,
  each tested to validate with no corrections) and `POST /chats/{id}/templates/{template}` (a free preview, no AI,
  priced as Engine on Generate). Assembly templates place parts on a shaft with explicit positions and 0.1 mm clearance.
- Manufacturability check: `POST /chats/{id}/parts/{part}/check` with `{process}` for `cnc`, `sheet_metal`,
  `injection_molding`, `fdm` or `resin`. Costs `CHECK_CREDITS` (default 5) once per part version and process, then the
  saved report reopens free (`chat_part_checks` table, returned as `part.checks` by `GET /chats/{id}`). No AI. The report is
  worked out before credits are taken, a failed save refunds, and two taps charge once. Price is in `/ai/models` and
  `/chat-config` as `check_credits`. Single parts only (assemblies, boards, buildings and civil parts return 422).
  - `manufacturability.py` reads the dimensions through `dfm.py` and, when the STL is readable, the mesh: closed surface,
    thin spots (rays through the walls), FDM overhangs, molding draft, resin trapped cavities.
  - `dfm.py` gained injection molding and resin rules, `check_with_passes()` (what held, not just what failed) and
    `SIZE_LIMIT_MM`. `PROCESSES`, `applicable()` and `best_fit()` are unchanged, so chat answers behave as before.
- Exports: GLB, 3MF and URDF (`mesh_formats.py`, pure Python, no new dependency) through `/export/{fmt}/{job}`. GLB and 3MF
  work for assemblies (one node or object per part). URDF is one rigid link with mass and inertia from the mesh and the
  material's density (aluminium is assumed, and said so in the file, when there is no material), and is refused for
  assemblies. An attached mesh gets all three without pressing Generate, since there is nothing to unlock.
- Assemblies: the Specs section now has the per-part table (position, size, mass) next to the overlap, floating-part and
  mass findings, the parts list CSV and the requirements. `assembly_checks_report` returns `parts` for it. Versions saved
  before this fall back to the grouped parts list.
- Credits: `db.charge_credits` and `db.refund_credits` (subscription pool first, refunded to the pool that paid).

### Not verified in the build environment
CadQuery, FastAPI and pytest were not installable there, so the CadQuery paths were not run: tessellating a real solid for
GLB, 3MF and URDF, the assembly `parts` table, building the templates, and the HTTP routes. Run `pytest` and open one GLB, one
3MF and one URDF in a viewer, a slicer and a simulator before release. See the tests marked `importorskip("cadquery")`.

### Added from Nitocad-merged-v8
- Building domain (`building/`, the `building` part type, DXF and PDF drawing sets), feature tree (`feature_tree.py`,
  `feature_builder.py`), standard parts (`standards.py`, `standards_data.py`: `fastener`, `std_bearing`, `key`,
  `asme_flange`, `o_ring`, plus `hex_bolt`, `socket_cap_screw`, `hex_nut`).
- Requirements that stay attached to a chat and are re-checked on every version (`requirements.py`,
  `POST/DELETE /chats/{id}/requirements`), manufacturability answers (`dfm.py`), screening and materials.
- Typed sizes are kept and flagged (`validator.typed_keys`, `merge_notes`, `validation.flags`), and free questions are answered.
- Drawing footer with notes, tolerance table and parts list (`drawing_footer.py`), STEP export with a round-trip check.
- Bug fixes: the freeform loft builds a real solid, the shaft keyway is one pocket instead of splitting the shaft.
- Studio: assembly panel (parts list CSV, overlaps, mass) and requirements panel, next to the PCB download rows.

### Decisions
- One assembly engine: the mate and row engine from Nitocad-merged. The text-parsed assembly path from
  Nitocad-merged-v8 (`deps.generate_assembly`, its `build_assembly` and `assembly_parser.parse_assembly`) was not ported.
- Its parts list, overlap, floating-part and mass checks now run on every built assembly
  (`assembly.assembly_checks_report`, returned as `assembly_checks`, stored in the part facts, shown in Studio, and
  the parts list is printed on the DXF and PDF). Floating-part checks are skipped for the side-by-side row layout.
- `assembly_planner.py` and `mates.py` come along as unused modules, with their tests.
- Removed tests that exercised the dropped path: `test_assembly_logic`, `test_assembly_mates_cq`, `test_chat_assembly`.

### Not verified
- CadQuery, pytest and a live server were not available while merging. Every file compiles and every local import
  and attribute reference resolves. Run `pytest -q` where CadQuery is installed before deploying.

## [Unreleased] - Merge of master-v7, PCB v4, aerospace and automotive packs

Base is master-v7 (3.9.0). The other four zips were layered on top of it.

### Added
- **PCB pack** (from Nitocad-v4-PCB): `pcb/`, `pcb_api.py`, `pcb_service.py`, `pcb_board` template,
  validator, parser and chat-planner hooks, KiCad layer in the Dockerfile (image grows by about 1-2 GB).
- **Aerospace pack** (from nitocad-update): `aerospace/`, `aerospace_pack.py`, 9 aerospace templates,
  `/api/aero/*`, and sandboxed STEP/IGES/BREP import (`file_import.py`, `cad_worker.py`,
  `/convert`, `/api/files/cad/export`). Frontend pages `aerospace.html`, `import.html`, `import/`.
- **Automotive pack** (from nitocad-automotive-pack): `domain_packs/` (9 parts), mate-based
  assemblies (`assembly_spec.py`, `mating.py`, `envelope.py`, `tolerance.py`, `loads.py`,
  `assembly_analysis.py`, `assembly_parser.py`, `assembly_templates.py`), `/api/domain-packs`, `/api/assembly/*`.
- Frontend is `frontend-v8`, with the PCB download rows and the DXF/SVG/KiCad/Gerber attach types ported in.

### Decisions
- Both assembly systems use `part_type: "assembly"`; the spec decides the engine (`assembly.assembly_route`):
  1. a **named** assembly (for example "wheel corner module") or a stored spec holding `assembly` uses the mate engine;
  2. a chat-planner assembly with `mates` (`layout: "mated"`, parts with ids, optional `root`) uses the mate engine;
  3. a plain parts list uses the row layout, as before.
  The mate engine is fail-closed: interfaces that do not fit, or solids that collide, are reported to the user and
  are never replaced by a row layout. Parts without connection points can still be placed with explicit `placement`.
- Dropped as superseded by master: `assembly_store.py`, `file_formats.py` (PCB zip) and `file_edit/` (automotive zip, unwired work in progress).

### Not verified
- Nothing was run against real CadQuery/OpenCascade or a live server during the merge. See `NITOCAD_STATUS.md`
  (automotive geometry has never run on OCCT; run `python scripts/verify_automotive.py`).

## [3.9.0] - Assemblies in chat, attached files you can edit (backend only)

This release is backend only. The Studio screens for it are not built yet (see the end).

### Added
- **The chat planner builds and edits assemblies.** `part_type: "assembly"` with a parts list
  (`conversation.py`, `assembly.py`). Parts from the AI or from the description go through one
  validated layout, side by side, with names and counts. Without an AI, the description is read
  by rules. Without an AI, an existing assembly can only be replaced, not edited, and the reply says so.
- **Attached files that can be edited** (`import_store.py`, `import_ops.py`, `imported_solids.py`,
  `meshes.py`, `profiles.py`). A new `imported` part type is the stored file plus an ordered list
  of edits: scale, scale to a size, holes, fillets, chamfers, move, rotate, mirror, and for drawings
  a thickness. Formats:
  - STEP, IGES, BREP: kept as BREP.
  - DXF, SVG, KiCad boards (`.kicad_pcb`), Gerber (one file, or a zip of Gerber and drill files):
    read into flat shapes and extruded.
  - STL, OBJ: kept as a mesh. Meshes can be scaled, rotated, moved, mirrored, rested on the ground
    and centered, and export as STL only. No holes or fillets.
  - PDF, PNG, JPG, WEBP are read for sizes as before and are not edited.
- **`POST /chats/{chat_id}/imports/{import_id}/part`** opens a stored file as an editable part.
  `POST /files/analyze` now returns a `card` and an `import_id`, accepts `chat_id` to save the
  card in the chat (so it survives a reload), and accepts all the formats above.
- **Rules planner edits** for attached files: "scale it to 150 mm long", "make it 5 mm thick",
  "add a 6 mm hole at x 10 y 20", "fillet the top edges 2 mm", "rotate it 90 degrees", "undo".
- **Tests:** `test_profiles.py`, `test_meshes.py`, `test_import_ops.py`,
  `test_planner_assembly_import.py`, and updated `test_file_analysis.py`.

### Changed
- STL and OBJ are now uploaded for editing instead of staying in the browser. GLB, GLTF and 3MF
  are refused with a message.
- A mesh part has nothing to buy: its STL is free, and Generate returns without charging.
- Imported parts skip the "one watertight solid" check, and record their size with the job.

### Not done in this release
- Studio screens: the attach button still lists only the old file types, there is no "Edit this
  file" button, saved file cards are not drawn after a reload, there is no assembly view, no link to
  Civil tools and no plans screen.
- Tests that need CadQuery or the database for imported parts and the new chat route are not written.

## [3.8.0] - Assemblies are built from the description

### Fixed
- **Assemblies ignored what you asked for.** `parse_assembly_description` returned the same
  base plate and L-bracket for any description, and reported success. It now reads the
  description: it is cut into parts at commas, "and", "plus" and "with", a piece that names
  no part (for example "4 holes") stays with the part before it, and counts such as "two l
  brackets" or "3 flat plates" work. Each part is read with the single-part parser and its
  values go through the same validator. Parts are laid out in a row along X with a 10 mm
  gap, sitting on z = 0, so they never overlap. How parts mate is not guessed. Part names
  come from the description (`base_plate`, `l_bracket`, `l_bracket_2`).
- **Fewer than two parts is now an error** (422, "Describe at least two parts, for example:
  a base plate and an L bracket") instead of the fixed example. Something like "assemble
  multiple parts into one unit" used to succeed and now asks for the parts.
- **Older assembly jobs** (saved before 3.7.0, with no stored parts list) are rebuilt from
  `legacy_assembly_example()`, the exact fixed example they were built from. This replaces
  the `ASSEMBLY_PARSER_IS_DETERMINISTIC` flag from 3.7.0, which could not have reproduced
  them once the parser changed.
- **PDF drawing scale** no longer depends on `Shape.scale` existing in the installed CadQuery
  (`exporters._scale_shape` falls back to the OCCT transform).

### Added
- `tests/test_civil_api.py` and `tests/test_file_api.py`: HTTP tests for the `/civil` and
  `/files` routes (sign-in, refusals, caps, each kind of file).
- Parser and layout tests in `tests/test_assembly_reexport.py`.

### Changed
- `tests/test_exporters.py` expects the part names the parser now gives (`base_plate`,
  `l_bracket`).

### Not changed
CAD Studio still cannot create assemblies (`conversation.UNSUPPORTED_TYPES`) and has no
assembly screen. Assembly jobs come from `POST /generate`, the API and MCP.

## [3.7.0] - Re-export stored assemblies

### Added
- **Stored assemblies can be exported again.** `GET /export/{fmt}/{job_id}` used to
  refuse any job whose part type was `assembly`, because an assembly was rebuilt by
  parsing its description a second time. The parts list used for the build is now
  saved with the job (its `parameters`, as `{"parts": [...]}`), and a later export
  rebuilds from that list, so the file matches what was generated and anchored.
  STEP, IGES, STL, DXF and PDF all work. (`cad_generator.py`, `assembly.py`)
- **`assembly.normalize_assembly_spec`** checks a parts list before it is built or
  stored: a list of up to 50 parts, each with a part type, a parameters dictionary
  and finite position and rotation. Values are not changed.
- **`tests/test_assembly_reexport.py`.**

### Changed
- `CADGenerator._generate_assembly` takes the parts list (a description string is still
  accepted). Its export step is now `_export_assembly`, shared by first-time generation
  and re-export, so both write the same files.
- A failed rebuild raises `AssemblyError` (422) with a plain message instead of a 500.

### Older jobs
Assembly jobs saved before this change have no stored parts list. They are rebuilt from the exact example they were originally built from (see 3.8.0).

## [3.6.0] - Preview first, pay when you generate; AI tiers; new 3D look; embedded demo

### Added
- **Preview first, pay when you generate.** Every Studio message builds a free STL
  preview. Only the Generate button spends credits. Generate charges once per
  version, builds STEP and unlocks STEP, IGES, DXF and PDF. A failed build refunds
  exactly what was taken, and a double press never charges twice. Locked formats
  return an error from the export and download routes. STL stays free. Older, API,
  MCP and x402 jobs are never locked. (`chat_api.py`, `chat_service.py`,
  `conversation.py`, `db.py`)
- **AI tiers** (`ai_models.py`) and an AI picker above the message box. Engine is no
  AI, Basic is DeepSeek, Plus is GPT or Qwen, Advanced is Claude. Messages per
  rolling 24 hours are 40, 15 and 4 on the AI tiers, checked before the model is
  called. Engine has no limit. Generate costs 2, 10, 25 and 40 credits, priced by
  what actually planned the version, so an AI failure that falls back to the rules
  costs 2. A model only appears once its key and model name are set.
- **`frontend/viewer-look.js`**, shared by Studio and the landing viewer: metal or
  plastic finish from the material, studio lighting, smooth surfaces, CAD edge
  lines, a ground shadow and filmic tone mapping.
- **Landing page demo** now embeds the full Free Preview page (`preview.html?embed=1`).
  The "nitocad.dev/demo" bar, the pop-up preview and its scripts are removed.
- **Tests** - `tests/test_preview_then_pay.py`, `tests/test_ai_models.py`, and updates
  to `tests/test_chat_service.py` and `tests/test_conversation.py`.

### Restored
- **`exporters.py`** the STEP re-import check, topology-based hole detection for
  drawings, the written-file assertion and safe output path handling had been lost
  in an earlier merge. They are back, together with `tests/test_exporters_logic.py`
  and the six tests removed from `tests/test_exporters.py`. The large-part PDF scale
  from 3.5.0 is kept, and hole callouts now use the validated hole data at any scale.

### Merge notes
- `preview.html`, `profile.html` and `refine.html` keep the shared `config.js`
  backend address from 3.5.0, not the hard-coded address from the preview-then-pay
  build.
- The embedded demo frame gets a default height, so it is visible before the
  preview page reports its own.

## [3.5.0] - Civil engineering calculations, file upload in CAD Studio

### Added
- **`civil/`** - Screening-level civil calculations, pure Python plus numpy:
  survey point parsing (`survey.py`), a Delaunay terrain surface with height
  lookup, profiles and an STL export (`terrain.py`), horizontal and vertical
  alignments (`alignment.py`), cross-sections, earthworks and material
  quantities by average end area (`earthworks.py`), rational method, Manning
  pipes and channels and a box culvert screening check (`hydraulics.py`),
  culvert loads and Rankine earth pressure (`loads.py`), cantilever wall
  overturning, sliding and base pressure (`wall_stability.py`), and an editable
  criteria check (`criteria.py`). The criteria are limits the user sets. The
  built-in set is a generic starting point, and nothing says "complies with".
- **`civil_api.py`, `frontend/civil.html`** - `/civil/*` routes (signed in, rate
  limited, free) and a Civil tools page.
- **`file_analysis.py`, `file_api.py`** - `POST /files/analyze` reads STEP, IGES
  and BREP files (CAD kernel in a separate, time limited process), PDF text, and
  images or PDF pages through a vision model. `GET /files/capabilities` reports
  what the server can read. Free for now, capped per account per day. Nothing is
  stored.
- **`frontend/studio.html`, `studio.js`, `studio.css`** - Attach button, drag and
  drop and paste in the composer. Mesh files (STL, OBJ, GLB, 3MF) open in the
  browser without uploading. Results show as a card with sizes, a plain
  description to edit and send, and a 3D preview for CAD files.
- **`config.py`, `.env.example`** - `RATE_LIMIT_CIVIL`, `CIVIL_MAX_SURVEY_POINTS`,
  `FILE_*` limits and `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL`.
- **`requirements.txt`** - `pypdf`, `pypdfium2`, `Pillow`.
- **Tests** - `tests/test_civil_engineering.py`, `tests/test_file_analysis.py`.

### Fixed
- **Two backend addresses.** `auth.js` pointed at one Railway service and
  `preview.html`, `profile.html` and `refine.html` at another. All pages now read
  one address from `frontend/config.js` (override in the browser with
  `localStorage.nitocad_api_base`).
- **Prices typed into the HTML** drifted from `config.py`. The front page and
  `pricing.html` now read `/config/chain`, with the printed values as fallback.
- **`smart_parser.py`** ignored "metre" and "metres". Both convert to millimetres now,
  for every part type.
- **"Box culvert" was read as a plain box.** Civil keywords are checked first.
- **Civil parser regex bugs** found by the new tests: an invalid optional-unit
  pattern, and a miss on phrasing like "two 3.5 m lanes".
- **`exporters.py`** PDF drawings of large parts needed a page no viewer can open.
  Parts over 1500 mm combined size now print at a standard scale, with real sizes
  in the title block and dimensions. Smaller parts are unchanged.
- **`civil/terrain.py`** left some convex hull triangles out (the helper triangle was
  too small). The surface now covers the hull exactly.
- **`file_analysis.py`** the vision answer cleaner raised an error on a non-list
  field such as `"features": 5`. It now returns empty lists.
- **`frontend/civil.html`** read the wrong auth global (`Auth`, should be `NitoAuth`).
- **`frontend/studio.js`** the size label shows metres for parts over 3 m.

### Front page (3.3.0 follow-ups)
- "100% Exact B-Rep" replaced with "True B-rep, Solids on OpenCASCADE".
- Google sign-in placed before Connect Wallet, BOT Chain badge moved out of the header.
- Spec table and "Who it's for" section removed, headings bold.

### Not included
No structural or reinforcement design, no seismic or water loads on walls, no
outlet control or tailwater in culvert hydraulics, no built-in rainfall data, no
named design standards. Reading dimensions from an image is only as good as the
picture, so results are shown for review and nothing is built until the person
sends a message.

## [3.4.0] - Civil elements (preliminary)

### Added
- **`cad_templates/civil.py`, `cad_templates/_civil_profiles.py`** - Three civil
  part types: `road_segment` (crowned carriageway, shoulders, optional kerbs),
  `box_culvert` (1 to 4 cells) and `retaining_wall` (cantilever, tapered stem).
  Parameters are millimetres like every other template. The cross-section
  maths is plain Python in `_civil_profiles.py` so it can be tested without
  the CAD kernel.
- **`validator.py`** - Geometry checks for the three types. Every civil result
  carries a notice that it is preliminary, not checked against any design
  code and not for construction.
- **`smart_parser.py`** - Regex detection and extraction for the civil types,
  placed before the generic keywords so "box culvert" is not read as a box.
  Metres are converted to millimetres, and British spelling (metre, metres)
  now works for every part type.
- **`deepseek_parser.py`, `prompt_refine.py`** - The AI parser and the chat
  planner know the civil types and are told to convert metres to millimetres.
  `PROMPT_VERSION` bumped so cached parses are not reused.
- **`tests/test_civil.py`** - Profile maths, validator, parser, and (with
  CadQuery) solid volumes, bounding boxes and all five exports.

### Changed
- **`exporters.py`** - PDF drawings keep true 1:1 up to 1500 mm of combined
  X+Y+Z size. Larger parts are drawn at the first standard scale that fits
  (1:2 to 1:1000). The title block shows the scale and the real size, and
  the dimension text is real size. Normal parts are unaffected.
- **`frontend/studio.js`** - The viewer size label switches to metres for
  parts over 3 m.

### Not included
Civil here is geometry only. It has no terrain or survey data, alignments,
drainage design, quantities, design standards, loads or hydraulics.

## [3.3.0] - Front page redesign, shared frontend config

### Changed
- **`frontend/index.html`** - Rebuilt front page: airplane/house blueprint
  scene first, then the demo, "What works today", stats, a compact pricing
  dropdown and "Who NitoCAD serves". Removed How it works, the Agent
  Marketplace section and the 23+ templates stat.
- **`frontend/auth.js`, `studio.html`, `preview.html`, `profile.html`,
  `refine.html`** - Backend address now comes from one place,
  `frontend/config.js`. Before, `auth.js` pointed at one Railway service and
  three pages at another.

### Added
- **`frontend/config.js`** - Single `apiBase`, a `localStorage`
  override (`nitocad_api_base`) for testing, and a live price loader.
- **`frontend/pricing.html`, `frontend/exports.html`** - Own pages for pricing
  and export formats. Prices fill from `GET /config/chain`, with the printed
  values as fallback if the request fails.

## [3.2.0] — Security audit fixes, subscriptions, Google sign-in, Paystack replaces Stripe

### Fixed
- **`design_registry.py`** — `anchor_design()` was missing the PoA
  middleware `scripts/deploy_design_registry.py` itself documents as
  required for `build_transaction()` on BOT Chain's proof-of-authority
  chain. Every anchor call was likely failing silently on both testnet
  and mainnet, swallowed by the function's own by-design broad
  `except` (anchoring failures must never break a paid export). Fixed
  by injecting `ExtraDataToPOAMiddleware`/`geth_poa_middleware`, same
  as the deploy script already does. New `scripts/test_anchor_e2e.py`
  validates the fix end-to-end on testnet (two distinct jobs anchored
  back to back to actually exercise nonce handling, receipts and
  `isAnchored`/`getDesign` read back from chain state, not app logs) —
  written, **not yet run**; needs a real RPC connection this
  environment didn't have.
- **`GET /download/{fmt}/{filename}`** — all five routes were fully
  unauthenticated (path-traversal-safe via `file_safety.py`, but no
  ownership check at all), unlike `GET /export/{fmt}/{job_id}` which
  already required the requesting wallet to own the job. A leaked URL
  (browser history, a referrer header, a screen share) gave permanent,
  un-revocable access to a paid job's files with no session needed.
  Fixed via `db.find_job_owner()` (parses `job_id` out of on-demand
  export filenames; falls back to a `stl_url`/`step_url` table lookup
  for `/generate`'s inline-STL filenames, which use a different,
  non-`job_id` uuid) and `wallet_auth.get_current_wallet_optional()` —
  anonymous `/preview` output stays public on purpose (free,
  regenerable, no wallet to own it), everything else now 404s for
  anyone but the owning wallet.

### Changed
- **Pricing repriced** — BOT was trading around $9.73 when the
  original 0.05/5/50 BOT tiers were set, pricing a single generation
  at roughly $0.49. Repriced to 0.01/1/10 BOT (same credit counts,
  same bulk-rate ratio), bringing a single generation to roughly
  $0.10. `frontend/index.html`'s pricing copy was hardcoded rather
  than reading `/config/chain` live and had drifted from the real
  price even before this — fixed to read live values everywhere.
- **Subscriptions replace the single flat-rate model, and now run on
  Paystack, not Stripe.** Three tiers (`config.py`'s
  `settings.subscription_tiers`): **Starter** ($5/mo, 100 subscription
  credits/period — a pool that resets and does not roll over, also
  throttled to 10 generations/rolling-24h so the pool can't be spent
  in one sitting), **Engineer** ($10/mo, no credit pool at all, 30
  free generations/rolling-24h), **Professional** ($50/mo, same shape
  as Engineer, 150/rolling-24h, plus an `advanced_engineering`
  entitlement that is a named placeholder only — no scope defined,
  nothing checks it). `db.charge_generation` is the full per-tier
  spend policy — subscription pool/allowance first, purchased credits
  second, an explicit and easy-to-flip choice over hard-blocking a
  subscriber who exceeds their daily cap. Daily tracking is a rolling
  24 hours from each request (`db.count_generations_last_24h`, reusing
  the existing `jobs` table), not a UTC-midnight or per-wallet-local-
  timezone reset — no timezone column needed, no reset-cliff, at the
  cost of being a `COUNT` query rather than an O(1) counter compare.
  **Team ($500/5 seats) and Enterprise (custom) are explicitly not
  built** — a seat/organization model doesn't exist anywhere in this
  app, and Enterprise is normally manual invoicing, neither is a
  config tweak.
  Switched off Stripe onto **Paystack** mid-build (see `paystack_pay.py`'s
  module docstring for what was independently verified against
  Paystack's live docs before writing any of it, rather than assumed
  from Stripe's shape): no separate webhook-signing secret (Paystack
  signs with the same secret key used for API calls, HMAC-SHA512 of
  the raw body), no Checkout Session object (`POST /transaction/initialize`
  with a plan code returns an `authorization_url` directly), and a
  **required real customer email** — Paystack has no concept of a
  wallet, so `POST /subscribe/checkout` now takes an `email` field, and
  the frontend asks for it at subscribe time. The wallet stays the
  actual NitoCAD identity throughout: `subscriptions.provider`
  distinguishes which rail wrote a row, `paystack_customer_code`/
  `paystack_subscription_code` exist only for reconciliation, a webhook
  is resolved back to a wallet via the customer code, never by
  re-deriving one from an email. USD pricing on Paystack is **not**
  assumed enabled — `PAYSTACK_CURRENCY` must be explicitly set to
  `"USD"` by someone who confirmed that in the Paystack dashboard, or
  every subscription route refuses to run at all rather than silently
  billing NGN amounts equal to the USD figures. `stripe_pay.py` and its
  `STRIPE_*` settings are left in place, unused, not deleted — a cheap
  rollback path, no data loss risk.
- BOT Chain USDT payments remain **explicitly blocked** — not
  "no verified address yet," but not to be built at all right now
  regardless of address availability, per an explicit decision not to
  guess a contract address from a web search result.
- File Exports (the subscription entitlement) remains **explicitly
  undefined** — every credit type, purchased or subscription, already
  gets free STEP/IGES/DXF/PDF export after the 1-credit generation
  spend, so the perk has no content until a real definition is chosen.
  Recorded as a named-but-unimplemented entitlement on purpose.

### Added
- **Google sign-in** (`google_auth.py`, `POST /auth/google`,
  `GET /config/auth`) — a SECOND, independent identity path alongside
  wallet sign-in, not a replacement. Stripe/Paystack-only (an email
  account has no wallet to pay BOT/USDT from), 1 free credit granted
  immediately on first sign-in (not gated on a first payment, unlike
  the wallet flow's 10 — a real Google account has more inherent
  friction than a free Ethereum keypair). Implementation trick that
  kept this cheap: nothing in the existing schema actually validates
  that `wallet_address` is a real Ethereum address, it's just a `TEXT`
  primary key everywhere, so a Google account gets identifier
  `"email:{google_sub}"` and every existing credit/subscription/
  ownership/session function works on it completely unchanged.
- BOT Chain co-branding in `frontend/index.html`'s footer — logo, name,
  links to `botchain.ai` and `scan.botchain.ai`, plus a link to
  `x.com/nitocad_agent`.

## [3.1.0] — MCP agents migrated to wallet sessions, standalone free preview page, CDN fix

### Changed
- **`a2mcp_botchain/server.py`** — `generate_cad_part` and `export_format`
  now take a `session_id` instead of a per-call `tx_hash`, matching the
  web app's wallet-session + credits model from 3.0.0. This closes the
  gap that blocked migrating it earlier: it never had an
  API-key-equivalent identity to fall back on, since the payment
  transaction itself used to *be* the identity proof. A wallet session
  solves that without needing a payment at all - signing a message
  (`POST /auth/nonce` → sign → `POST /auth/verify`, all plain HTTP, no
  browser needed) is strictly simpler than sending a transaction, and
  any agent calling these tools already holds a private key capable of
  the latter. `generate_cad_part` spends 1 credit; `export_format` is
  free, same reasoning as `GET /export/{fmt}/{job_id}`.
- **`wallet_auth.py`** — session lookup factored out into
  `resolve_session()`, a plain function with no FastAPI dependency
  machinery. `get_current_wallet` (HTTP routes) now wraps it; the MCP
  tools above call it directly, since MCP tool functions take explicit
  arguments, not dependency injection.

### Added
- **`frontend/preview.html`** — a standalone, no-wallet-required version
  of the free preview: a bigger space to iterate on a description, see
  the free STL update, and tweak it as many times as wanted, separate
  from the paid demo section's conversion-focused layout. Saves the
  description to `localStorage` on each successful preview so
  `index.html#demo`'s form can offer to pick up where this page left
  off, without duplicating any server-side state (free previews stay
  anonymous and unclaimable, unchanged from 2.x).
- `tests/test_a2mcp_botchain.py` — covers the migrated MCP tools:
  invalid/expired session handling, insufficient-credits failures (and
  that they don't themselves consume anything), ownership privacy (a
  wallet that doesn't own a job gets the same "not found" a nonexistent
  job would, not a distinguishable error), and - CadQuery permitting -
  that generation spends exactly 1 credit and export doesn't spend a
  second one.

### Fixed
- **Three.js loaded from unpkg is CORS-unreliable for ES module
  imports** - a real, externally documented problem (multiple long-
  standing GitHub issues against unpkg, including one for this exact
  package), not something introduced by this release, but surfaced
  while building `preview.html` against the same pattern
  `frontend/index.html` already used. `<script type="module">` imports
  are fetched in CORS mode by the browser itself (unlike a classic
  `<script src="...">` tag, which isn't subject to CORS at all) - when
  the CDN doesn't consistently send `Access-Control-Allow-Origin`, the
  whole module fails to instantiate, silently, with no exception the
  page's own error handling can catch. Both `index.html` and
  `preview.html` now load `three` from `cdn.jsdelivr.net/npm/` instead,
  which does reliably send that header. **Not independently verified
  end-to-end against either CDN in this project's own test environment**
  - its outbound network access is blocked entirely, uniformly, for any
  external domain, which made it impossible to distinguish "unpkg
  specifically fails here" from "nothing external loads here." The
  business logic around the CDN load (the `/preview` call, response
  handling, parameter/warning rendering, the `localStorage` handoff) was
  verified directly, with the CDN import stubbed out - see this
  release's test notes. Worth a real-browser smoke test of both pages
  after deploying.

## [3.0.0] — Wallet sign-in + prepaid credits replace API keys

Breaking change to the auth and payment model, not an additive feature.
Anyone integrating against the old `X-API-Key`/per-call-payment flow
needs to migrate - there is no compatibility shim and no conversion
path for existing keys, per the project's own "going forward only"
decision.

### Added
- **`wallet_auth.py`** — Sign-In-With-Wallet: `POST /auth/nonce` issues
  a one-time challenge, the wallet signs it for free (no gas, nothing
  on-chain), `POST /auth/verify` checks the signature and issues a
  session. Sessions are opaque server-side tokens (revocable via
  `POST /auth/logout`), not JWTs.
- **`db.py`** — `nonces` and `sessions` tables backing the above, plus
  `wallet_credits`: a prepaid-generation balance per wallet.
  `consume_credit` is a single atomic `UPDATE ... WHERE balance >= ?` -
  tested under 20 real concurrent threads against a wallet holding
  exactly 1 credit in `tests/test_wallet_auth.py`, confirming exactly
  one winner and zero overdraft.
- **`POST /credits/purchase`** — three tiers: `single`
  (`BOTCHAIN_CREDIT_PRICE_BOT`, 1 credit), `pack_1000`, `pack_10000`.
  The two packs share the same per-credit rate; `single` pays a 10x
  premium for not committing to volume - see `config.py`'s comment on
  these three numbers.
- **`GET /credits/balance`** — current balance for the signed-in wallet.
- `tests/test_wallet_auth.py` — unit tests for nonce/session/credit
  storage, including the concurrency test above.

### Changed
- **`POST /generate`** now authenticates via
  `Authorization: Bearer <session_id>` (wallet session) instead of
  `X-API-Key`, and spends 1 credit (`db.consume_credit`) instead of
  requiring a fresh on-chain payment on every call.
- **`GET /export/{fmt}/{job_id}`** likewise moved to wallet-session
  auth. Still free per job (unchanged from 2.x) - this fixes nothing
  about pricing, only identity.
- **`GET /api/jobs`, `GET /api/jobs/{job_id}`** moved to wallet-session
  auth - same audit-history behavior, different identity mechanism.
- **`frontend/index.html`** and **`web_app.py`**'s embedded demo both
  rewritten: a persistent "Connect Wallet" control replaces the old
  implicit "pay 5 BOT the first time you click Generate" flow, with
  visible "Buy 1 / 1000 / 10000 credits" actions and a live balance/
  cost note shown before anything is charged.
- **`tests/conftest.py`** — `api_key` fixture replaced by
  `wallet_session` (signs a real throwaway wallet in through the actual
  HTTP endpoints) and `wallet_with_credits` (the same, pre-loaded with
  1000 credits for tests that need to call `/generate`).
- **`tests/test_api.py`** — rewritten against the new auth model, plus
  new coverage: a 402 on zero credits, a regression test pinning down
  that a rejected (422) request never spends a credit, and a regression
  test for the exact bug this release also fixes (see below).

### Fixed
- Exports were briefly double-billed after the 2.x per-call pricing
  model shipped - `GET /export/{fmt}/{job_id}` was charging its own
  `BOTCHAIN_PER_CALL_PRICE_BOT` on top of the payment already made at
  `/generate` time, for the same job. Fixed by removing the second
  charge entirely (ownership already proves the caller paid once at
  generation) rather than discounting it - the fix carried forward
  into this release's credit-based model too:
  `test_export_does_not_consume_an_additional_credit` guards it going
  forward.

### Removed
- **`security.py`** — deleted. `X-API-Key` header auth is gone.
- **`db.py`**'s `api_keys` table (no longer created on a fresh deploy;
  not dropped from any already-deployed sqlite file, since that's
  destructive and unnecessary for "stop reading it") and the
  `create_api_key`/`validate_api_key` functions.
- **`POST /api/keys/generate`** endpoint, and the `ApiKeyResponse`/
  `KeyGenerateRequest` models.
- `RATE_LIMIT_KEY_ISSUE` config setting (rate-limited the now-removed
  endpoint above).

### Not migrated (deliberately, see the project's own notes)
- **`a2mcp_botchain/server.py`**'s `generate_cad_part`/`export_format`
  MCP tools still use the old per-call BOT payment model. They have no
  API-key-equivalent identity to fall back on (a payment transaction
  *is* the identity proof there), so migrating them needs its own
  design rather than copying the web app's change - tracked as
  follow-up work, not an oversight.

## [2.3.0] — Multi-view orthographic + multi-part DXF/PDF export

`export_dxf`/`export_pdf` previously derived their entire drawing from
one horizontal section through the part (`Workplane.section()` at
Z=0) — correct for flat/plate-style parts, but blind to any geometry
that didn't intersect that one cutting plane (a stepped shaft's
diameter change along its axis, for example). Replaced with a real
multi-view orthographic drawing.

### Changed
- **`exporters.py`** — `export_dxf`/`export_pdf` now generate front,
  top, and right-side views, each via OCCT's `HLRBRep_Algo`
  hidden-line-removal algorithm (through OCP — no new dependency),
  separating visible edges (solid line) from hidden edges (dashed
  line). Views are laid out in the standard third-angle drafting
  arrangement (top above front, side beside front, shared-axis
  registered — see `_layout_three_views`), with per-view labels and a
  title block. This is the new default behavior, not an opt-in flag —
  there is no `multi_view`/`view_mode` parameter.
- DXF layers changed from the section-based `PIECE_OUTLINE` / `HOLES`
  / `CENTERLINES` set to `VISIBLE` / `HIDDEN` / `LABELS` / `BORDER`,
  since HLR classifies edges by visibility, not by section-wire
  nesting/area.
- **`export_all`** / **`export_dxf`** / **`export_pdf`** gained an
  optional `parts: list[(name, shape)]` parameter. For assemblies,
  passing `parts` draws one full front/top/side block per sub-part,
  stacked on one sheet and individually labeled, instead of a single
  block for the merged compound. step/stl/iges are unaffected.
- **`assembly.py`** — added `get_assembly_parts(assy)`, which flattens
  an `Assembly` into `(name, shape)` pairs with each part's geometry
  already moved into its assembly-world location, feeding the `parts`
  param above.
- **`cad_generator.py`** — `_generate_assembly` now calls
  `get_assembly_parts` and threads the result through to
  `exporters.export_all` for the dxf/pdf formats.
- `/generate`, the `/download/*` routes, `storage.upload_export`, and
  the frontend format checkboxes needed no changes — they all operate
  on format name, not view mode.

### Verification status
Same standing caveat as the rest of `exporters.py`: written and
syntax-checked against the cadquery 2.3.1 / OCP API surface pinned in
`requirements.txt`, but not executed against real OCCT geometry (no
network access to install cadquery's native dependencies in this
environment). View orientation in particular (which axis maps to
which side of the page) was worked out algebraically against OCCT's
`HLRAlgo_Projector` convention, not confirmed against a rendered
drawing. Run `pytest tests/test_exporters.py` for real before shipping.

---

## [2.2.1] — Multi-format export is now the default

`exporters.DEFAULT_FORMATS` was `("step", "stl")` — the 2.2.0 export
matrix (IGES/DXF/PDF) was opt-in only, via an explicit `formats: [...]`
list. That undersold the feature: a caller who didn't know the param
existed still got the old two-format behavior. Changed the default to
every supported format: `("step", "stl", "iges", "dxf", "pdf")`.

### Changed
- **`exporters.py`** — `DEFAULT_FORMATS` now lists all five supported
  formats. `validate_formats([])` and `export_all(..., formats=None)`
  both fall through to this constant, so the change is one line plus
  updated comments — no new code paths.
- **`web_app.py`** / **`cad_generator.py`** — docstrings/comments
  describing the old `["step", "stl"]` default corrected to match.
- A caller that wants the old, narrower behavior back still can, by
  passing `formats: ["step", "stl"]` explicitly — nothing about the
  opt-in mechanism changed, only what happens when it's omitted.

### Fixed
- **`tests/test_exporters.py`** — `TestValidateFormats` and
  `TestExportAll.test_export_all_default_formats` asserted the old
  two-format default; updated to expect all five, since they'd
  otherwise fail against the new behavior.

### Verification status
Same standing caveat as the rest of `exporters.py`: this is a one-line
constant change with no new logic, but still unverified against a real
CadQuery/OCP install (no network access in this environment). The
existing `tests/test_exporters.py` (updated above) covers it — run it
for real before relying on this.

---

## [2.2.0] — Tier-1 export formats

Adds the production export matrix on top of the existing STEP/STL
pipeline: exact-geometry IGES, layer-classified DXF for 2D cutting, and
1:1 scale vector PDF technical drawings.

### Added
- **`exporters.py`** — unified multi-format export module.
  - `export_step` / `export_stl` — the existing exports, moved here
    behind one interface (no behavior change).
  - `export_iges` — true boundary-representation IGES via OCP's
    `IGESControl_Writer`, called directly since cadquery 2.3.1's own
    `exporters` module doesn't expose an IGES `ExportType`. No new
    native dependency — OCP already ships with cadquery.
  - `export_dxf` — layer-classified DXF (`PIECE_OUTLINE`, `HOLES`,
    `CENTERLINES`) built from a real horizontal section through the
    solid and written via `ezdxf`, with holes auto-classified from the
    outer boundary by enclosed area.
  - `export_pdf` — 1:1 scale vector PDF with a title block (part name,
    material, scale, bounding-box size, date), built from the same
    section geometry as the DXF export, via `reportlab`.
  - `export_all` — orchestrator; validates the requested format list up
    front (`UnsupportedFormatError`, 422) before doing any geometry
    work.
  - **Scope note**: DXF/PDF are derived from a single horizontal
    section, not full multi-view orthographic projection — correct and
    sufficient for the flat/plate/bracket-style parts this catalog is
    dominated by, but a part whose relevant geometry isn't captured by
    one horizontal cut will only export that one cross-section. See the
    module docstring for the full scope discussion, including why GLTF
    isn't included in this pass.
- **`GenerateRequest.formats`** / **`GenerateResponse`** — `/generate`
  now accepts an optional `formats: ["step", "stl", "iges", "dxf",
  "pdf"]` list (defaults to `["step", "stl"]`, matching prior behavior
  exactly when omitted). Response gains `iges_file`/`dxf_file`/
  `pdf_file` and matching `_url` fields, populated only for formats that
  were actually requested.
- **`/download/iges/{filename}`**, **`/download/dxf/{filename}`**,
  **`/download/pdf/{filename}`** — new download routes, same
  path-traversal-safe resolution (`safe_output_path`) as the existing
  `/download/step` and `/download/stl` routes.
- **`storage.upload_export(local_path, fmt)`** — generic R2 upload for
  any supported format, added alongside (not replacing)
  `upload_step`/`upload_stl`.
- **`exceptions.UnsupportedFormatError`** (422) — a bad/unknown format
  name in the `formats` list fails fast, before any parse or geometry
  work, with a clear client-fixable error.
- Frontend (`web_app.py`'s inline HTML/JS) — format checkboxes next to
  the existing DeepSeek toggle; download links render dynamically for
  whichever formats come back in the response.
- **`tests/test_exporters.py`** — covers format validation, each
  exporter individually, `export_all`, and an end-to-end
  `CADGenerator.generate_from_text(..., formats=[...])` call. Marked
  `requires_cadquery` per the existing convention (`test_cad_templates.py`)
  — written and reviewed against the pinned cadquery 2.3.1/OCP API
  surface but **not executed against real OCCT geometry** in the
  environment this was authored in (no network access to install
  cadquery's native dependencies there). Run this file for real, in an
  environment with the project's actual dependencies installed (the
  existing Dockerfile/CI already has them), before relying on it.

### Changed
- `requirements.txt` / `pyproject.toml` — added `ezdxf==1.3.5` and
  `reportlab==4.2.5`.
- `cad_generator.py` — `_generate_single_part` and `_generate_assembly`
  now return a `dict[str, Path]` keyed by format instead of a fixed
  `(step_path, stl_path)` tuple. Assembly STEP export still goes through
  `Assembly.save()` (preserves named sub-parts); every other
  format — including STEP/STL/IGES/DXF/PDF for single parts — goes
  through `exporters.export_all` on the built shape.

---

## [2.1.0] — Engine components: connecting_rod, crankshaft

Two new CAD templates — the most geometrically complex in this project
so far (multi-body boolean unions, not a single extrude+cut like most
existing templates).

### Added
- **`cad_templates/connecting_rod.py`** — big-end/small-end bosses of
  different diameters, a shank between them, an optional raised center
  rib (the visual signature of a forged I-section rod), and
  representative big-end cap bolt holes. See the module's own docstring
  for exactly what's simplified (no true tapered I-beam cross-section,
  no actual two-piece rod/cap split — a single STEP solid can't
  represent a bolted joint between separate bodies).
- **`cad_templates/crankshaft.py`** — configurable number of throws,
  main journals, offset rod journals, webs with counterweight lobes,
  optional front nose and rear flywheel flange. Default even throw
  spacing (`360°/num_throws`); pass `phase_angles_deg` for a specific
  engine's real firing-order spacing instead.
- Both registered in `cad_templates/__init__.py`'s `TEMPLATES` dict,
  wired into `smart_parser.py`'s keyword detection (`infer_part_type`)
  ahead of the existing generic `shaft`/`rod` keywords they'd otherwise
  be shadowed by, and given real validators
  (`validate_connecting_rod`, `validate_crankshaft` in `validator.py`).
- Test coverage in `tests/test_cad_templates.py` and
  `tests/test_validator.py`, plus both added to `smoke_test.py`'s
  real-pipeline test case list.

### Fixed during development (not shipped bugs — caught before delivery)
- An early `connecting_rod.py` draft used
  `faces(">Z").workplane().moveTo(x, y).hole(d)` to cut the bores —
  which centers its local coordinate system on the selected face's
  center of mass, not the global origin. Harmless for a part symmetric
  about the origin (which is why the existing `flat_plate.py` template
  gets away with the same pattern), but silently wrong for this rod's
  two off-origin, different-sized bosses: the holes would land at the
  wrong location. Fixed by cutting with explicit cutter solids at known
  global coordinates instead. `TestConnectingRod::test_big_end_bore_is_actually_at_the_big_end_not_the_origin`
  regression-tests this directly.
- An early `crankshaft.py` draft built one continuous full-length
  cylinder at main-journal diameter as a "backbone" and unioned the
  throws onto it — which left main-journal-diameter material running
  straight through every throw region as well as the actual main
  bearing surfaces, producing a fat cylinder with bumps instead of an
  actual crank profile. Fixed by building each main journal as its own
  discrete segment, with the offset rod journal/webs only present in
  the gaps between them. `TestCrankshaft::test_main_journal_diameter_does_not_run_through_the_throw_region`
  regression-tests this directly.

### Verification status
Same caveat as every CadQuery-dependent change in this file: written
and syntax-checked, but **not run against a real CadQuery install** in
the environment this was authored in (no native OCCT build available).
Run `pytest -m requires_cadquery` (or the full `make test`) plus
`python smoke_test.py` on your VPS before relying on either template.

---

## [2.0.0] — Professional hardening pass

A structural pass on top of the existing generation engine (parser,
templates, validator, CadQuery pipeline) — none of that logic changed,
this is entirely infrastructure: config, error handling, observability,
security, and testing. See sections below for what moved where.

### Added
- **`config.py`** — centralized `pydantic-settings` configuration.
  Every environment variable the project reads (previously ~15 separate
  `os.getenv` calls across `db.py`, `storage.py`, `deepseek_parser.py`,
  `web_app.py`) is now declared, typed, and validated in one place. A
  typo'd env var name now fails at startup instead of silently resolving
  to "not configured."
- **`logging_config.py`** — structured logging (JSON or console format,
  `LOG_FORMAT` env var) with per-request correlation IDs, replacing
  every `print()` call in the request path. `X-Request-ID` is echoed
  back on every response and threaded through all log lines emitted
  while handling that request.
- **`exceptions.py`** — a real exception hierarchy (`ParseError`,
  `GeometryValidationError`, `GenerationError`, `UnsupportedPartTypeError`,
  `AssemblyError`, `StorageError`) instead of one bare `except Exception`
  around the whole pipeline. FastAPI exception handlers map each to an
  appropriate status code (422 for bad input, 500 for internal failures)
  and never leak a Python traceback into an HTTP response body — full
  detail goes to the logs only.
- **`file_safety.py`** — fixes a real path-traversal vulnerability in
  `/download/step/{filename}` and `/download/stl/{filename}`: the
  filename from the URL is now resolved and confirmed to stay inside
  the output directory before being served, instead of being joined
  onto the output path unchecked.
- **Rate limiting** (`slowapi`) on `/generate` (20/min per IP by
  default) and `/api/keys/generate` (5/hour per IP) — each `/generate`
  call does real OCCT geometry work, and unauthenticated key issuance
  was previously uncapped.
- **`/healthz`** (liveness) and **`/readyz`** (readiness, checks DB
  connectivity) endpoints for container/orchestrator health checks.
- **Pydantic response models** (`GenerateResponse`, `ApiKeyResponse`,
  etc.) on every route — gives accurate OpenAPI docs at `/docs` instead
  of untyped `dict` returns, and FastAPI now validates outgoing
  responses match the documented shape.
- **Test suite** (`tests/`) — previously the only test artifact was
  `smoke_test.py`, a manual script not picked up by any test runner.
  Now: unit tests for the validator and regex parser (no CadQuery
  needed, run everywhere), API integration tests via `TestClient`
  (auth, rate limiting, request validation, path-traversal protection),
  and a CadQuery-gated suite (`pytest -m requires_cadquery`) covering
  template geometry validity and the fillet-degradation / structural-beam
  regressions called out in the "Fixed" sections below. **Written but
  not run against a real CadQuery install** — no network/native-build
  access in the environment this was authored in; run `pytest` on your
  VPS to confirm, same caveat every prior DeepSeek/CadQuery change in
  this file has carried.
- **CI** (`.github/workflows/ci.yml`) — lint (ruff), type check (mypy,
  informational for now), and two test jobs (with/without CadQuery) on
  every push/PR.
- **Tooling** — `pyproject.toml` (ruff + mypy + pytest + coverage
  config), `.pre-commit-config.yaml`, `Makefile` with `make check` /
  `make test` / `make fmt` etc.
- **Non-root Docker user + HEALTHCHECK** in the `Dockerfile`.

### Changed
- `cad_generator.py` — every pipeline stage now raises a typed exception
  on failure instead of falling through to a generic `except Exception`;
  `generate_from_text()`'s external dict-return contract (used by
  `cli.py` and `a2mcp/server.py`) is unchanged, but failures now include
  a stable `error_type` field alongside `error`.
  Also added: a cap on the number of chained `operations` per request
  (`MAX_OPERATIONS_PER_REQUEST = 25`) — an unbounded `operations` list
  was a cheap way to tie up the OCCT kernel for a long time on one
  request.
- `web_app.py` — CORS origins, rate limits, and docs visibility now come
  from `config.settings` instead of hardcoded values; added a request
  logging/correlation-id middleware.
- `db.py` / `storage.py` — read configuration via `config.settings`
  instead of scattered `os.getenv` calls; `db.py` gained
  `check_connection()` for the readiness probe.
- `deepseek_parser.py` — the DeepSeek client now has an explicit request
  timeout (`DEEPSEEK_TIMEOUT_SECONDS`, default 20s). Previously a stalled
  API call blocked the request indefinitely instead of falling back to
  the regex parser the way every other DeepSeek failure mode already did.

### Fixed
- **Path traversal** on the download routes (see `file_safety.py` above)
  — the one concrete security bug found in this pass, not something
  discovered in production. Treat this as sensitive if reporting it.
- **`assembly.py`** — an assembly part with an unsupported/typo'd
  `part_type` previously vanished from the output with zero signal
  anywhere; now logged as a warning so a shrinking assembly is
  diagnosable.

### Unchanged (deliberately)
- Every CAD template (`cad_templates/`), the regex/keyword parser
  (`smart_parser.py`), the DeepSeek prompt/extraction logic, the
  validator's actual geometric rules, and the assembly/x402/MCP gateway
  logic — this pass is infrastructure only. Prior fixes documented
  further down this file (fillet degradation, i-beam/channel flange
  overlap, etc.) still apply and now have regression tests in
  `tests/test_cad_templates.py`.

---

## Prior history

See the sections below this line for the pre-2.0.0 history (DeepSeek
integration, R2/db/security hardening, the three-round fillet bug fix,
the OKX A2MCP gateway) — carried over from the previous README, not
reproduced twice.

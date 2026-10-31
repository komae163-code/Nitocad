# Vehicle-core changes (added on top of the uploaded Nitocad-main)

NOT yet run against real CadQuery or the live download services. Run `pytest` first.

## Changed existing files (part/mate caps removed)
- assembly_planner.py, assembly_spec.py, conversation.py, assembly.py: MAX_PARTS / MAX_MATES /
  MAX_ASSEMBLY_PARTS / MAX_COUNT_PER_PART are now None (unlimited). Set an int to bring a limit back.
  assembly.RUNAWAY_PARTS = 20000 stops typos from hanging the server.
- tests/test_assembly_planner.py, tests/test_assembly.py, tests/test_assembly_reexport.py: cap tests replaced.
- Left alone on purpose: aerospace/assembly.py (50 parts, separate structural endpoint),
  assembly_checks.py MAX_PAIR_CHECKS (performance guard).

## New files
- assembly_package.py       mechanical packager: folders, manifest + hashes, BOM, params, validation, NOTICES.txt
- assembly_exploded.py      exploded offsets, assembly sequence, schematic SVG, viewer JSON
- asset_providers.py        licence-gated on-demand parts (cq_warehouse, FreeCAD-library, NVIDIA SimReady);
                            Polyfork, OpenBuilds, KiCad, TraceParts are BLOCKED with reasons recorded
- vehicle_spec.py           vehicle = JSON spec: params, formulas, repeat/mirror, mates, assemblies, checks
- vehicle_build.py          build_vehicle(spec, out_dir): expand > fetch > mates > exploded > export > package
- frontend/exploded.html    3D exploded viewer (loads JSON from vehicle_build; needs internet for three.js)
- vehicles/examples/        loco_0_4_0_core.json, two_axle_cart.json
- tests/test_assembly_package.py, test_assembly_exploded.py, test_asset_providers.py,
  test_vehicle_spec.py, test_vehicle_build.py

## Known limits
- Mates solve only for quarter-turn (90 degree) rotations (limit of mates.py).
- Exploded views are drawn from bounding boxes, not true projected solids.
- Licence notes are my reading, not legal advice.

## Added later: modules, planner, learning library
- vehicle_spec.py: reusable modules (`{"id","module","params","position"}`) and `between` end-point tubes.
- vehicles/modules/: wheel_on_axle, gear_pair, bottom_bracket, rotor_head (internals shared by several vehicles).
- vehicles/examples/: wheelbarrow, bicycle, rc_helicopter (concept layouts with internals; NOT verified on CadQuery,
  NOT load-rated or flight-worthy).
- spec_library.py: stores specs; seed / candidate / accepted / rejected; retrieval + error-pattern lessons.
  "Learning" = better examples and warnings from user feedback; the model is not trained.
  FileStore needs a real disk: on Vercel/serverless use a database behind the Store interface.
- vehicle_planner.py: plan_vehicle(description, llm, library) with validate-and-retry; plan_and_build(...) end to end.
- tests: test_vehicle_modules, test_spec_library, test_vehicle_planner.

### Wiring it in
    from spec_library import SpecLibrary, FileStore
    from vehicle_planner import plan_vehicle, plan_and_build
    lib = SpecLibrary(FileStore("data/spec_library")); lib.seed_from_dir("vehicles/examples")
    def llm(messages): ...            # call your model with chat messages, return the reply text
    r = plan_vehicle("a garden wheelbarrow", llm, lib)      # r["spec"], r["candidate_id"]
    lib.feedback(r["candidate_id"], True)                    # thumbs-up promotes it to an example

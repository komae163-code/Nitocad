# Merge: multipart fixes + vehicle shells and library patch

Base: Nitocad-main-multipart-fixes.zip. Overlaid: vehicle_shells_and_library_patch.zip.

Taken from the patch (newer): vehicle_shell.py, vehicle_overlap.py, vehicle_spec.py, vehicle_decompose.py, vehicle_planner.py,
vehicle_views.py, assembly_exploded.py, spec_library.py, vehicles/examples/car.json, vehicles/modules/*, and the vehicle tests.

Kept from the base (newer or identical): exporters.py (the patch copy lacks the drawing-sheet title fix), assembly_gate.py,
validator.py, cad_templates/*, tests/test_gear_profile.py, CHANGES_vehicle_decomposition.md.

## Deploy check
GET /healthz on the Railway service must show version "2.0.0+vehicle-shells-multipart". If it shows "2.0.0", the old code is still live.
The frontend (Vercel) only shows the page. The build runs on the Railway service named in frontend/config.js.
cad_generator.py now also treats any spec with a `vehicle` or more than one part as multi-part, not only part_type "assembly".

## Other multi-solid parts
cad_generator.MULTI_SOLID_PART_TYPES = assembly, building, jet_aircraft, turbofan_engine. These skip the merged-mesh watertight test.
jet_aircraft and turbofan_engine also skip "exactly one solid" and are checked solid by solid (verify_solids).
Any other template whose built shape has more than one solid is treated the same way. Single parts keep the strict check.
Not run here (no CAD kernel): the jet_aircraft and turbofan_engine paths.

## What-changed list (conversation._diff_assembly)
A part whose position was a tuple on one side and a list on the other was listed as "(0, 0, 0) to (0, 0, 0)". Positions are now
compared as numbers, and parts are matched by name (by place only when names are missing or repeated), so a build step that adds
parts does not compare a part with a different one. tests/test_diff_assembly_positions.py.

## Pickup cab (car_body module)
A pickup built on car_body kept the sedan cabin (roof, rear window, trunk lid) because rear_bay was capped at 1200 and nothing told
the planner to set it, so the bed sat inside the cabin. rear_bay now goes up to 2200, the module has a cabin_has_length check, and the
planner prompt says to set rear_bay to the bed length for a pickup. Tests in tests/test_vehicle_car.py.

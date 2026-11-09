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

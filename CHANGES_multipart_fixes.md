# Multi-part / vehicle fixes

1. **No merged-mesh watertight test for vehicles or any assembly.** `geometry_verify.verify_geometry(multi_part=True)` skips it
   (the merged result is only recorded as information). Instead `verify_solids()` checks every solid of every part on its own
   (valid B-rep + closed surface) inside `cad_generator._export_assembly(verify_parts=True)`, and names the failing part.
   Single parts are unchanged.
2. **Exploded view uses the real parts.** New `explode_mesh.py` + `GET /chats/{id}/parts/{part}/explode-mesh`; the studio
   slider (`explodeSetupReal` in `studio.js`) uses it and falls back to the old mesh-island cut only if the server cannot.
3. **A partial build can no longer pass for the whole.** Generate on a vehicle/assembly that is still in steps returns 409
   with "step X of Y ... say finish" and charges nothing (`chat_service.StepsRemaining`; `?allow_partial=true` overrides).
   The Specs card says "Step k of n: showing 7 of 32 parts".
4. **Build order for vehicles without a stage table:** running gear, body, doors, inside, finishing. Wheels come first only on motor vehicles.
5. **Drawing sheet:** per-part title no longer starts off the page (was printing "ALE 1:5"); parts list names every part
   instead of "+1"; vehicle parts now carry their material so material and mass fill in.
6. **Code builder:** a bare "yes, write it as code" with nothing behind it now asks what to write instead of inventing an object.
   Code parts get a neutral icon instead of the L-bracket.

Tests: `tests/test_multipart_fixes.py` (numpy only). Not run here: the CAD kernel paths (tessellation, `shape.isValid()`).

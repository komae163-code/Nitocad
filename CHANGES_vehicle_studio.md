# Vehicles and picture tracing, reachable from the studio

> Superseded in part: the picture tracing described below was replaced by the newer tracer in the merge. See CHANGES_merged_studio.md.

Built on `Nitocad-merged-vehicle.zip`. Credit rules are unchanged: planning, editing, the exploded view and tracing a
picture are free; only Generate (`chat_service.unlock_part`) spends credits, and a failed file build still refunds.

## What a person can do now
- Assembly chip -> **Design a vehicle or machine**: five starters (wheelbarrow, bicycle, two-axle cart, RC helicopter,
  small locomotive) or free text. Typing "I want to create a wheelbarrow" in the chat does the same.
- The chat shows the plan (part count, groups, main sizes, how many design checks pass, notes) and builds a free preview.
- Chatting edits it: "make the wheel 400 mm", "add a second axle". With an AI model the whole spec is revised and
  re-validated; without one (Engine plan) a named size can still be set ("wheel diameter 450 mm") and anything else
  says plainly that an AI is needed. Questions ("how long is the tray?") are answered, not built.
- Specs tab: sizes (tap one to start a change), groups, design checks, honesty note. Files tab: **Exploded view** (free,
  needs the CAD kernel) and **Vehicle package** zip (unlocked by Generate).
- File card: **Edit as a part** for any attached file the server kept (this button did not exist for any file type).
- Pictures: a clear dark-on-light / light-on-dark drawing, or a transparent PNG cut-out, is traced to an editable flat
  part with no vision key and no network. The card shows the traced outline.

## Files
New: `vehicle_chat.py` (conversation layer), `vehicle_views.py`, `vehicle_api.py` (3 routes), `raster_trace.py`,
`image_import.py`, tests `test_vehicle_chat.py`, `test_vehicle_chat_service.py`, `test_vehicle_views.py`,
`test_raster_trace.py`, `tests/js/studio_vehicle.test.mjs`.
Edited: `conversation.py` (vehicle hook, caps), `chat_service.py` (keeps the vehicle spec on the saved part),
`assembly_gate.py` (skips vehicles), `vehicle_planner.py` (`revise_vehicle`, shared retry loop), `web_app.py` (mounts
the routes), `file_api.py`, `file_analysis.py` (traced pictures, `preview_svg`), `frontend/studio.html|js`,
`frontend/exploded.html` (accepts data from the studio, same origin only), `tests/test_file_api.py` (+4 tests),
`.env.example` (`VEHICLE_LIBRARY_DIR`), `.gitignore`.

## Correction to CHANGES_vehicle_core.md
`conversation.py` still capped assembly chat plans at 12 lines and 20 per line. Those are now `None` (no design
limit) with a 20,000 runaway guard. Still in place on purpose: `aerospace/assembly.py` (50 parts) and the pair guard in
`assembly_checks.py`.

## How it fits together (one decision worth knowing)
A vehicle in the chat is an ordinary custom-layout `assembly` part, so preview, Generate, refund, BOM, mass and
interference all work unchanged. Its parameters also carry `vehicle` (the spec it came from). The generator keeps
only the parts list, so `chat_service._keep_vehicle` puts the vehicle spec back before the part is saved. The overlap
gate and the shaft-bore "also updated to match" logic skip vehicles (parts on a shaft are meant to touch).

## Limits
- Not load-rated, not checked for safety. The checks are arithmetic from the spec.
- Mated parts (the locomotive) need CadQuery to be placed; without it the plan says so and builds nothing.
- Downloaded parts (bolts, bearings) are not in the preview; they are added to the package at Generate.
- A picture has no scale: the longest side is set to 100 mm and says so until the person gives a real size.
  Photos of 3D objects do not give a part outline. Curves become short straight segments; round holes become circles.
- The vehicle chat recognises whole vehicles by name (wheelbarrow, bicycle, cart, trolley, wagon, trailer, helicopter,
  locomotive, scooter, vehicle ...). Anything else goes to the normal planner; "Vehicle: <text>" from the Assembly
  panel forces it for any wording.
- While a vehicle is the current part, a message about a different single part is treated as a vehicle edit unless it
  names a new vehicle. Start a new chat for an unrelated part.

## Not run (no CadQuery, FastAPI, pydantic or pytest in the build sandbox)
`vehicle_api.py` and the new HTTP tests in `tests/test_file_api.py` were compiled but not executed; nothing ran
against real CadQuery. Run `pytest` and `node --test tests/js` in your environment first.

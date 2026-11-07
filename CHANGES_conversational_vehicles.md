# Conversational vehicle builds

Built on the Nitocad-main zip of 2026-10-31. Credit rules are unchanged: asking questions and adding steps are free,
only Generate spends credits.

## What changed
- "I want to build a wheelbarrow" no longer builds a model at once. With fewer than 3 concrete details (numbers,
  a material, a count of wheels or rotors) the chat answers with one tappable question and says where it would start.
  Each tap sends `question + option`, and the next question follows until the details are enough, every question has been
  asked, the person says "just build it", or 5 messages have passed.
- A message that already holds its sizes, material and counts, and "just build it", build the whole vehicle as before.
- After the questions the vehicle is built in steps. Step 1 shows only its first parts (wheelbarrow: tray and handles,
  then legs and crossbars, then fork and wheel). "Next step" adds one, "Finish the whole thing" adds the rest, size edits
  keep the current step. The reply and the follow-up chips say what comes next.
- Recognised as whole machines now: car, sedan, truck, lorry, pickup, bin, dustbin, airplane, aeroplane, plane, aircraft,
  jet, engine. Part words after them (door, lid, block, mount, piston ...) still mean a part.
- Without an AI, a request is only answered with a built-in example of the same kind. An airplane no longer returns the
  RC helicopter. It says plainly that an AI is needed.

## Files
`vehicle_chat.py` (`scope_turn`, `stage_plan`, `describe_stage`, `_advance`, `staged` argument), `conversation.py`
(`_vehicle_turn` takes the history), `frontend/studio.js` (follow-up chips for a build in steps), tests in
`tests/test_vehicle_chat.py` and `tests/test_vehicle_chat_service.py`.

## Limits and things to know
- Generate makes the parts shown at that moment. The exploded view and the vehicle package are built from the full
  vehicle spec, so they show every part.
- Cars, bins, airplanes and engines get questions and a plan only when an AI is selected. There are still no built-in
  examples or modules for them (piston engine, gearbox, car body), so quality depends on the model.
- Not run here: `test_vehicle_chat_service.py` (needs pydantic and FastAPI), CAD-kernel tests, the studio in a browser.
  Run `pytest` and `node --test tests/js/*.mjs` first.

## Wheelbarrow shape (step 4)
- New part type `tapered_tray` (`cad_templates/tapered_tray.py`, validator in `validator.py`): an open-top tub, smaller
  at the floor than at the rim. Its origin is the underside of the floor, so a tray placed at rail height sits on the rails.
  Vehicle specs pick it up automatically from the template registry.
- `vehicles/examples/wheelbarrow.json`: the tray is a tapered tray (floor 560 x 460, rim 800 x 560), the rails stop at the
  back of the tray, and a separate handle tube rises from there (`handle_rise`, default 300 mm, so the grips are near
  730 mm) and flares out to `grip_gap` (620 mm). New params `tray_base_len`, `tray_base_w`, `rail_gap` (now 440 so the rails sit under the floor) and
  `handle_rise`, plus four checks. It has 15 parts now, up from 13. Build steps are tray and handles (5 parts), legs
  and crossbars (4), fork and wheel (6).
- Not run here: the CadQuery code. `tapered_tray` and the tube orientation are unverified on the real kernel. Generate
  the wheelbarrow once and look at it before trusting it. The original dev note about tube orientation still applies.

## Rails that close in (step 4b)
- `between` in `vehicle_spec.py` now accepts any direction. Before, a tube could only lie in the X-Z plane or run along Y.
  `_tube_rotation` turns the part's +Z axis onto the direction with rz = 0 (a tube can roll freely), using the same
  R = Rx * Ry * Rz convention as `assembly.py` and `mating.transform_from_pose`. Checked against `mating` to 1e-15 on 2000
  random directions.
- With `mirror: "y"` the right-hand tube is now a true mirror image (rx is negated). Tubes in the X-Z plane or along Y
  are unchanged.
- Wheelbarrow: the rails run from the fork tops above the wheel (about 118 mm apart) back to the rear of the tray
  (`rail_gap`, 440 mm), so they now meet the forks. Before, the forks stood 160 mm inside the rails and touched nothing.
  Legs and crossbars sit on the slanted rail line through `rail_slope`. Handles flare to `grip_gap`. Four new checks.
- `_eval_derived` waited for a name that was defined later only when the error started with "unknown name". An unknown
  name inside `max(...)` was reported as an error instead. Fixed.
- The planner prompt (`vehicle_planner.py`) no longer tells the AI that tubes are limited to the X-Z plane.
- Still unverified on the real kernel: the origin and axis assumption for tubes, and the tapered tray. Generate the
  wheelbarrow and look at it from above.

## Tubes were placed by their middle (step 5a, a bug fix that changes every vehicle)
- The tube and shaft templates extrude from z = 0, so their origin is the START of the part. `between` used the midpoint,
  which shifted every tube by half its own length along its axis. This is very likely why the wheelbarrow in the
  screenshot showed rods sticking out of the tray. `between` now defaults to `"origin": "base"`. Use `"center"` only for
  a part whose origin is mid-length. Bicycle, cart, locomotive, helicopter and wheelbarrow examples all change.
- Source-read only. Confirm it by generating the wheelbarrow, and expect the frame to sit inside the tray's footprint now.

## Piston engines (step 5b)
- `vehicles/modules/piston_engine_{1,2,4}.json` and `vehicles/examples/mini_engine_{single_cylinder,inline_two,inline_four}.json`,
  written by `tools/make_engine_modules.py` (edit the script and rerun it, do not hand-edit the three modules).
  Parts: crankshaft, flywheel ring, and per cylinder a connecting rod, piston, pin and liner, plus one head plate
  (7, 11 and 19 parts). Built in three steps: crankshaft and flywheel, rods and pistons, cylinders and head.
- Each rod is placed from its piston pin and leaned so its big end lands on the crank pin. A test checks this for every
  rod at seven crank angles, and that the pistons stay inside their liners. `crank_deg` sets the pose (0 is cylinder 1 at the
  bottom of its stroke).
- Not modelled: crankcase, valves, camshaft, drilled pistons. Cylinders are inline only. No V engines or jet engines yet.
- A shaft shorter than its diameter is silently lengthened by `validator.validate_shaft`, so pistons are as tall as their bore.
- `assembly_planner.validate_spec` now accepts a short list of numbers for list parameters (crank `phase_angles_deg`).
  Before, any list was rejected.
- Chat: "engine" questions now offer only what can be built (single, two or four cylinder inline). A request with no AI
  returns the matching engine example.

# Questions card, finished-model inspection, readable part names

## 1. One questions card (build_scope.py, vehicle_chat.py, frontend/studio.js, studio.css)
- The first reply to a vague request shows every question at once as one card: tappable chips, an "Add details" box,
  Skip and Build. Nothing is sent until Build or Skip, and the answers go as one message.
- Any reply to the card starts the build. Questions left blank fall back to the planner's defaults.
- Choices from the AI planner (no `card` flag) behave as before.

## 2. Finished-model inspection (vehicle_inspect.py, hooked into "Finish the whole thing")
- Fixed checklist on the placed parts: floating parts, deep overlaps (exact shape models from vehicle_overlap),
  level wheels, wheels poking out, left/right symmetry, proportions for cars and trucks.
- Up to two fix passes through the existing revise path; a pass that does not improve the checklist is dropped.
- Optional vision pass (ai_models.make_vision, vehicle_chat.vision_scope): four renders are shown to the person's own
  model, which is asked only about the same fault list. Models without vision skip it and the reply says so.
- Needs the CAD kernel for part boxes; without it the finish step is unchanged.

## 3. Readable part names (part_naming.py, vehicle_spec.py, vehicles/**, planner prompt)
- The engine now names mirrored parts `_left` / `_right` (`post_3_left`), and `{side}` in a mate expands to those.
  Specs saved with the old `_l` / `_r` names keep working: explode settings and mate targets are translated on load
  (`vehicle_spec.canon_name`).
- Source ids spelled out in car.json, light_airplane.json, loco_0_4_0_core.json, modules/car_body.json and
  modules/bottom_bracket.json: fl/fr/rl/rr -> front_left ..., `_l`/`_r` -> `_left`/`_right`, instance `panels` ->
  `exterior`. 51 of the car's 97 part names changed; every example expands to identical geometry, positions,
  rotations, types and groups (checked part by part), only the names differ.
- Do NOT name an instance `body`, `cabin`, `shell`, `hull`, `chassis` or `fuselage`: vehicle_shell.BODY_NAME treats
  every part with such a word in its name as a one-piece body and hollows it out.
- Labels for people ("Front left door") are added to the parts table and both exploded views; the id stays in the tooltip.
- vehicle_views: default pull-apart directions understand `_left` / `_right`, and `bumper_right` is no longer taken for
  the rear bumper.
- tools/make_car_example.py was out of date (33 components against the shipped 45). It now refuses to overwrite car.json
  without --force.

## 4. Background builds (build_jobs.py, job_progress.py, chat_api.py, frontend/studio.js)
- `POST /chats/{id}/turns` runs the same turn as `/messages` as a saved job. An answer that is ready within
  BACKGROUND_INLINE_WAIT_S (20 s) comes straight back, exactly like `/messages`. A longer one answers 202 with the job, and
  the browser shows live progress (the steps the server has reached, time running, Cancel).
- Leaving the page does not stop a build. Reopening the chat rejoins it (`active_job` on `GET /chats/{id}`); a finished
  build is already saved in the chat. `GET /jobs/{id}` follows a job, `POST /jobs/{id}/cancel` stops it at its next step.
- Inside a job the planning limit is BACKGROUND_PLAN_BUDGET_S (900 s) instead of VEHICLE_PLAN_TIME_BUDGET_S (120 s); a job
  is stopped after BACKGROUND_JOB_MAX_S (3600 s). One model call is still limited by AI_TIMEOUT_SECONDS.
- State is in the database (table `build_jobs`), so it survives page closes. Workers are threads in the web process
  (BACKGROUND_MAX_WORKERS at once, one job per chat, three per person). A running job writes a heartbeat; if its process
  dies, a sweeper runs the job again once the heartbeat has been silent for 75 s (at most twice, the person's message
  is not saved twice, a reply already saved is not built again), then marks it failed. A job is claimed atomically, so
  it never runs twice at once, even with several processes.
- A server without `/turns` is handled: the browser falls back to `/messages`. Set BACKGROUND_BUILDS=false to turn the
  feature off on the server.
- Limits: a redeploy still interrupts a build; it resumes within about two minutes, from the start of that turn (not
  from the middle). Progress lines are coarse (planning, each AI try, checking, building, inspecting, saving).

## 5. Every kind of design, not only vehicles (design_inspect.py, conversation.py, chat_service.py)
- **Questions card:** every kind build_scope knows (bracket, plate, box, gear, round bar, pipe, beam, hinge, engine part, wing,
  fuselage, road, culvert, wall, building, fastener, circuit board, assembly) gets the one card; a reply builds. Tested for each.
- **Inspection and fixing, inside background builds:** after the preview is built,
  - assemblies (machines, aerospace and building assemblies): the real-solid overlap test and the "part touches nothing" test the
    build already ran. A serious overlap or a loose part is fixed by asking the planner for exactly that fix (needs an AI plan).
  - circuit boards: NitoCAD's own routing and design-rule checks (pure Python, about 7 s a run, no KiCad needed). Unrouted nets
    and clearance, courtyard, edge or connectivity problems are fixed by making the board wider (board.max_width_mm).
  - at most two fixes, each kept only if the checks come back better; otherwise the design that went in comes out.
  - single parts (bracket, wall, wing, ...) are not inspected: their validation and geometry checks already stop a bad build.
  - vehicles keep vehicle_inspect.py; it now also runs on a whole AI build and an AI edit, not only on "Finish the whole thing".
- **Naming rule** is now in the generic and the assembly planner prompts too. The shipped assembly templates already read well.
- **Progress lines** for the generic planner and the board planner in background builds.
- Not proven: widening a board. No failing board could be produced offline (the USB-C footprint needs KiCad's library), so the
  fix is only kept when the rebuilt checks are strictly better.

## 6. Starting from a template or an assembly (studio_templates.py, chat_service.py, frontend/studio.js)
The reported gap: pressing a template or an assembly started it, then the chat gave one "Try:" line and nothing else.
- **The hints were broken.** 11 of the 30 templates' own "Try:" hints failed with the free Engine plan ("I did not catch which
  value to change"; the two assembly hints said an AI was needed). Nine now suggest an edit that works (for example the 6205
  bearing: "use a 6206 bearing"). The key and the pipe flange have no edit Engine understands, so they are marked
  `needs_ai` and the reply says so.
- **Next steps under the reply.** The reply now says what it started, gives one example edit, says "or say it in your own
  words", and ends with "press Generate". Under it, one-tap options send exact messages. Options marked `needs_ai` are hidden
  for Engine. The chat draws `choices` with `say` (exact message) and `needs_ai` per option.
- **It remembers what you asked.** If the person asked for something before pressing a template, the reply quotes it and offers
  "Change it to match what I asked before" (needs an AI).
- **Missing starting points added:** hatchback car (97 parts), light airplane, wheelie bin, inline-four engine. The locomotive
  is left out: its natural edit phrasings are not understood yet.
- Guard: tests/test_template_followup.py fails if any template hint stops working.
- Not done: "Open an attached file" (startImport) has the same one-line ending and no next steps.

## 7. Vehicles without tyres (vehicle_decompose.py, vehicle_planner.py, vehicle_inspect.py, design_inspect.py)
Reported: vehicles come out with no tyres. What was checked: every shipped vehicle with wheels has its tyre parts in the chat
spec, every part type they use is a registered template, and none of the earlier changes touch tyres. What was found:
- **The gate never required tyres.** vehicle_decompose demanded an axle when there were wheels, but nothing demanded a tyre, so
  an AI plan with wheel or hub parts and no tyre ring passed. It now sends a wheeled road vehicle (car, truck, bus, bicycle, cart,
  wheelbarrow, bin) back with "the wheels have no tyres", with rail and tracked vehicles exempt. The planner prompt says it too.
- **The inspector reports a car or truck whose wheels lost their tyres** (a fixable fault).
- **A fix may never delete a part.** The inspection fixers (vehicles, assemblies) keep a fix only if the checklist improves, and a
  fix that "improves" it by deleting the offending part would have been accepted. They now reject any fix that removes a part.
- **A tyre could collapse to nothing.** The wheel and landing-gear modules took `tyre_dia` and `rim_dia` independently, so a rim
  as big as the tyre (an AI edit, or a hand-edited spec) made a ring with no thickness, which the kernel cannot build, and the tyre
  vanished ("could not be built and is not shown"). The tyre radius is now `max(tyre_dia/2, rim_dia/2 + 2)`, so there is always
  rubber. All 13 shipped examples expand exactly as before. Chat edits through the Engine plan already scaled the rim with the tyre.
- Not reproduced: no CAD kernel here, so a tyre that is in the spec but fails to build or tessellate in the kernel would show as
  "<name> could not be built and is not shown" in the preview notes. That note is the thing to look for.

## 8. Vehicles that are not templates: a Lamborghini, a tractor, a tuk-tuk (vehicle_chat.py, vehicle_decompose.py, vehicles/examples)
- **They were not recognised as vehicles.** `kind_of` was a short fixed word list, so "a Lamborghini", "a Lambo", "a Ferrari",
  "a Tesla Model 3", "a supercar", "a tractor", "a forklift", "a motorbike", "a tuk-tuk", "an ambulance" or "a school bus" never reached
  the vehicle builder: no wheel module with tyres, no plan checks, no step-by-step build, no inspection. They went to the generic
  part planner. Common vehicle types and makes are now recognised. Left out on purpose because they mean something else in
  CAD: "bus" alone (an I2C bus, a bus bar), "tesla" alone (the unit), "beetle"; "van der ..." is not a van.
- **A plan with no wheels at all passed.** The plan check asked for an axle and a tyre only when wheel parts existed. It now
  sends back a car, truck, bicycle, tricycle, scooter, cart, wheelbarrow, bin or locomotive with no wheels.
- **The tyre rule no longer depends on the word "car".** Any wheeled vehicle must have a tyre per wheel; rail and tracked are exempt.
- **The shipped Two-axle cart had no wheels** (a deck and two bare axles, while its template card said it had two wheels, and it
  is a proven example the AI planner copies). It now has four wheel modules: 17 parts, tyres at each corner, checks pass, no overlaps.
- Not verified: how the AI draws an unregistered vehicle's body, and what the kernel builds. Both need a real run.

## 9. Across the other disciplines (build_scope.py, vehicle_chat.py)
What carries over to every discipline and what does not:
- **Everything discipline-neutral:** background builds, the questions card for every known kind, readable names in all planner
  prompts, template hints that work (every template, guarded by a test), assembly and circuit-board inspection with the
  never-delete-a-part rule.
- **Vehicle-only:** the wheel, tyre and recognition fixes (vehicles have their own builder, modules and plan gate).
- **Fixed here:** "a bike rack", "a car key holder" and "a jet engine" were taken for vehicles; they now go to the general planner.
  Circuit-board wording (Arduino shield, motor driver board, sensor board, power supply board, LED matrix, dev board) is recognised
  as a board.
- **Still the general planner, with no question card** (it builds them, with the assembly and board inspection): springs, clutches,
  valves, pumps, impellers, levers, clamps, turbines, bridges, dams, tunnels, columns, staircases, towers, silos, tanks, drones,
  rockets, satellites, propellers, furniture, robot arms. Each would need its own questions and a builder to check against.

## Tests
New: test_vehicle_inspect.py, test_vision.py, test_part_naming.py, test_build_jobs.py, test_design_inspect.py, test_template_followup.py; updated for the new names and the single card:
test_build_scope.py, test_vehicle_chat.py, test_vehicle_car.py, test_vehicle_spec.py, test_vehicle_modules.py,
test_vehicle_new_examples.py, test_vehicle_overlap.py, test_vehicle_decompose.py, test_vehicle_views.py.

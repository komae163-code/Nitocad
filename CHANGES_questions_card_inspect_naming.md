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

## Tests
New: test_vehicle_inspect.py, test_vision.py, test_part_naming.py; updated for the new names and the single card:
test_build_scope.py, test_vehicle_chat.py, test_vehicle_car.py, test_vehicle_spec.py, test_vehicle_modules.py,
test_vehicle_new_examples.py, test_vehicle_overlap.py, test_vehicle_decompose.py, test_vehicle_views.py.

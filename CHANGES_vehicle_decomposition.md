# Vehicle decomposition, gears, explode and PDF scale

Unzip over the repo root, run `pytest`, push. Railway redeploys. vehicle_api.py is not in this zip: the version you uploaded already has the wallet fix.

## What changed
- vehicle_decompose.py (new): rejects coarse plans. Under 12 parts for a car, truck, plane or similar, fewer than 3 groups, one body solid with no panels, a solid body with no interior cut, gears asked for but missing or without a keyway, an engine with no crankshaft, wheels with no axle. Each message says how to split.
- vehicle_planner.py: runs that check on every new plan and sends the messages back to the model. The prompt now says to build in parts. If the model still fails after its retries, the plan comes back with "split into parts" in unresolved_checks. Edits to an existing vehicle skip the check.
- Gears: cad_templates/transmission.py and validator.py accept keyway_width_mm, keyway_depth_mm and backlash_mm. Backlash thins each tooth by half of it at the pitch circle (checked numerically). The validator refuses a keyway wider than the bore or deep enough to reach the roots.
- vehicle_views.py: parts with no explode direction get one from their name and side. Wheels, doors and mirrors go out sideways, roof and hood up, floor, tank and exhaust down, bumpers along the length.
- exporters.py: a multi-part PDF is no longer forced to 1:1. Each part gets the smallest standard scale that fits, its label shows it, and dimensions are multiplied back to real size. The title block says AS NOTED when scales differ.

## The car example (second patch)
- vehicles/examples/car.json is rebuilt from panels by tools/make_car_example.py. Edit the script and rerun it, do not hand-edit the JSON.
- 75 parts in 8 groups: body (floor, two side panels with arches and door openings, end panels, nose, tail, hood, deck, roof, windscreen, rear window, two side glasses), doors, interior (seats, hollow fuel tank), wheels, powertrain (inline four engine module), gearbox (two shafts, four keyed gears with backlash, four bearings, hollow case) driveline and exhaust (pipe, muffler, tail pipe).
- All 30 design checks pass, including new ones for door versus arch clearance, gear centre distance and engine and gearbox position. The splitting check finds nothing wrong with it, so the planner now shows the model an example that follows the rule.
- assembly_gate.py lists the three new gear parameters as known. Without that, a test that compares the table with the templates failed.
- Driveline: the gearbox countershaft is the output. It runs back at wheel-centre height under the flywheel to a hollow differential housing on the front axle, and a half shaft runs out to each wheel axle. To make the shafts straight I raised the engine crank to z = 430 and widened the gear spacing to 110 mm (module 5, 20/24 and 22/22 teeth).
- Not modelled: the crown wheel, pinion and CV joints, suspension, steering, lights.

## Handoff and code builder fixes (third patch)
Problem seen in the studio: a pickup chat ended with "Yes write it as code" and the builder returned a planter pot, then the part page said "The code builder could not do that (HTTP 422)".
- codegen_chat.start_prompt and chat_service.py: a bare "yes, write it as code" used to be the whole prompt. Now the person's earlier messages since the last saved part go with it (the request and the answers, not our questions). A message that names its own shape is sent as written.
- conversation.py: when the vehicle path fails or returns nothing for a vehicle chat, the reply says so and nothing is built. It no longer falls through to the generic planner, which answered "custom" and sent the car to the code builder. The swallowed exception is now logged as "vehicle turn failed".
- codegen_api.py: the rebuild and export requests drop any slider value that is not a finite number instead of failing with a 422. A failed rebuild always returns a readable reason and logs "codegen rebuild failed".
- frontend/studio.js: the part page sends only finite numbers, and shows the server's field and reason when a request is refused, instead of "HTTP 422".
- I could not confirm which of these caused your 422, because the response body was not visible. After deploying, if it still appears, the page now shows the real reason and the Railway log has the "codegen rebuild failed" line. Look for "vehicle turn failed" in the same log to see why the pickup left the vehicle path.
- Tests added: tests/test_vehicle_handoff.py and six in tests/test_codegen_chat.py. One test in test_vehicle_handoff.py needs fastapi, so it skips or runs only where your dependencies are installed.

## Not verified here
- No CAD kernel in this sandbox. The keyway cut, the scaled multi-part PDF and the exploded view on real solids have not been run. Generate a car once and open the PDF and the exploded view.
- The panel positions in car.json follow feature_tree.py by reading it (XZ extrudes toward -Y, YZ toward +X). A wrong sign would put a panel on the wrong side. Open the car in the studio first. The repo's assembly_gate checked nothing on this spec, so it did not test collisions.
- Tests: 273 pass in the vehicle, gear, library and gate suites. Two fail only because my stand-in runner has no monkeypatch fixture. Real pytest will run them.

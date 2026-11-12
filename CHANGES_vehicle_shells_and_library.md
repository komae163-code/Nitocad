# Automatic body shells, panel body, new library car, explode and mirror fixes

Unzip over the repo root, run `pytest`, push. This zip includes everything in vehicle_decomposition_patch.zip, so it replaces it.
vehicle_api.py is not in this zip: the version you uploaded already has the wallet fix.

## What changed
- vehicle_shell.py (new): the expander hollows any solid body-named part (body, hull, cabin, fuselage, bodywork, chassis). Each additive
  convex extrude gets an inward-offset cut, wall 1.5 to 3 mm (about 2.5 mm for a car). A solid car body went from about 55 t of steel to about 690 kg.
  Concave outlines, revolves, lofts and patterns of additive features are reported in `shell_problems` and left for the splitting check.
  Thin sheet panels are left alone. A vehicle can set `body_wall_mm`.
- vehicle_spec.py: calls auto_shell before the planner validator. Mirror inside a module now flips the part's own y before adding the
  module offset (it used to put wheel_fl's second bearing on the right wheel).
- vehicles/modules/car_body.json (new): the body as 23 separate panels (floor, firewall with a tunnel hole, side panels with arches and door
  openings, four doors, shoulders, roof, glass, hood, nose, trunk lid, tail, fascias, bumpers). Floor has wheel wells, firewall clears the front tyres.
- vehicles/modules/car_engine_4.json and gearbox_5speed.json (new): a four-cylinder engine and a five-speed gearbox (ten keyed gears, backlash).
- vehicles/examples/car.json: rebuilt. 97 parts in 8 groups, 12 design checks, no overlaps by the new overlap check, explode directions set.
- vehicle_overlap.py (new): kernel-free overlap check that samples real part shapes (extrudes, revolves, cylinders, gears to the root circle,
  boxes). Lists parts it cannot model instead of calling them clean. It is a plan check; the post-build interference check still decides.
- vehicle_decompose.py: snake_case names such as hatchback_car now count as a car; the engine rule also accepts parts named crank or conrod;
  messages point to the car_body module; thin panels are not called solid blocks.
- spec_library.py: a coarse design is never shown as an example, even one stored earlier; a thumbs-up cannot promote it; approve() refuses it
  unless the operator overrides. The verdict is stored with each record and computed on the fly for older records.
- vehicle_planner.py: prompt explains the automatic shell and the car_body module. Example limit raised from 9000 to 12000 characters
  (the new car is about 9.1k, so it would otherwise never reach the prompt).
- assembly_exploded.py and vehicle_views.py: parts that share a direction and sit inside or against each other get different pull distances
  (a door vs its side panel, an axle vs its tyre). Before, they travelled together and never separated.
- assembly_gate.py: its gear parameter table knows backlash and keyway.
- The earlier patch is included: gear keyway and backlash, the decomposition check, default explode directions, PDF scale per part.

- vehicle_chat.py and build_scope.py: a typed answer such as "A Lambo" now counts as answering the question. Before, only a tapped chip
  did, so the chat asked "What kind of car?" again and again (up to the 5-message cap). It now moves to the next question and builds
  when the questions run out. test_build_scope's five-message test was rewritten, because it relied on that bug.

- validator.py: whole_number_counts() runs inside validate_parameters, so EVERY part type gets it (single mechanical parts, assemblies,
  vehicles, civil): a count that arrives as 44.0 becomes 44 before the part's validator looks. A real fraction (44.6) is left to that validator to reject or correct (a sprocket rejects 12.5 teeth, a road records a lane_count correction); only a fraction it leaves alone is rounded and reported. Before, a staged bicycle build crashed at the
  chainring with "'float' object cannot be interpreted as an integer". Only real counts are touched (teeth, *_count, stages, blades, throws);
  angle_deg and motor_size_mm have integer defaults but take decimals, so they are left alone. vehicle_spec.py applies the same rule early.

## Tests
1786 pass here against 1714 on the original upload. The same 65 tests fail on both, because they need FastAPI, the database or a CAD kernel.
New: test_vehicle_shell, test_vehicle_overlap, test_vehicle_car, test_library_quality. Changed: test_vehicle_decompose, test_vehicle_new_examples.

## Not verified here (no CAD kernel in this sandbox)
- The gear keyway cut, the multi-part PDF scale and the exploded view on real solids. Generate a car once and open the PDF and exploded view.
- The auto-shell cut features are ordinary extrude cuts, but they have not been run through the feature builder.
- The overlap check ignores teeth, keyways and fillets, and can miss a thin overlap smaller than its 0.5 mm step.
- The crown wheel and pinion are spur gears drawn for layout; a real one is hypoid. Gearbox and diff cases have open tops.
- loco_0_4_0_core has 9 parts and would fail the part-count rule if someone asked for a locomotive. It is unchanged.

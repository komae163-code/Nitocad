# Vehicle decomposition, gears, explode and PDF scale

Unzip over the repo root, run `pytest`, push. Railway redeploys. vehicle_api.py is not in this zip: the version you uploaded already has the wallet fix.

## What changed
- vehicle_decompose.py (new): rejects coarse plans. Under 12 parts for a car, truck, plane or similar, fewer than 3 groups, one body solid with no panels, a solid body with no interior cut, gears asked for but missing or without a keyway, an engine with no crankshaft, wheels with no axle. Each message says how to split.
- vehicle_planner.py: runs that check on every new plan and sends the messages back to the model. The prompt now says to build in parts. If the model still fails after its retries, the plan comes back with "split into parts" in unresolved_checks. Edits to an existing vehicle skip the check.
- Gears: cad_templates/transmission.py and validator.py accept keyway_width_mm, keyway_depth_mm and backlash_mm. Backlash thins each tooth by half of it at the pitch circle (checked numerically). The validator refuses a keyway wider than the bore or deep enough to reach the roots.
- vehicle_views.py: parts with no explode direction get one from their name and side. Wheels, doors and mirrors go out sideways, roof and hood up, floor, tank and exhaust down, bumpers along the length.
- exporters.py: a multi-part PDF is no longer forced to 1:1. Each part gets the smallest standard scale that fits, its label shows it, and dimensions are multiplied back to real size. The title block says AS NOTED when scales differ.

## Not verified here
- No CAD kernel in this sandbox. The keyway cut, the scaled multi-part PDF and the exploded view on real solids have not been run. Generate a car once and open the PDF and the exploded view.
- The library still contains the old 18-part car example. The model sees it as a proven example and may copy its single body. Rebuild it from panels, or remove it, then the gate will not fight it.
- Tests: 174 pass. Two fail only because my stand-in runner has no monkeypatch fixture. Real pytest will run them.

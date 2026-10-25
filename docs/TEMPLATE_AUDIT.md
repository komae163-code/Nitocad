# Template audit

An audit of the mechanical templates, done by reading each one against what the part is called and against the validator's own description of it. It was not done by building them. The fixes below come with geometry tests in `tests/test_template_fixes.py` and `tests/test_gear_profile.py`, and the CadQuery ones run in CI.

## Fixed

| Template | What was wrong | What it does now |
|---|---|---|
| `gear` | Each tooth space was a rectangle centred on the root circle, so it never reached the tip circle and was a closed pocket, not a gap. It was also rotated about the origin after being placed, which moved it to every second tooth angle, so a 20-tooth gear got 10 pockets. There were no teeth. | A real spur gear: involute flanks, tip arc and root arc, `teeth` times around (`cad_templates/_gear_profile.py`, pure Python and tested without a kernel). |
| `sprocket` | Same rotation bug: tooth spaces landed on every second angle, so an 18-tooth sprocket got 9. | One space at every tooth angle. |
| `pulley` | The groove was cut as a solid cylinder of radius `outer/2 - groove_depth`, which removed the hub and left a hollow drum. The validator's own docstring describes a groove cut down to that radius. | The groove is the ring from that radius out past the rim, between the two flanges. The hub stays. |
| `l_bracket` | The horizontal leg was drawn centred on the vertical one, giving a T-shaped section. The hole code assumed a leg that starts at the vertical leg and runs one way. | A true L: one profile in the YZ plane, extruded along the width. The part measures width x depth x height, with the bend on the origin. |
| `angle` (structural) | The two legs were extruded along different axes, so they did not form an L-shaped section. `test_angle_is_a_single_connected_l` expects a volume of about (leg1 + leg2 - thickness) x thickness x length. | One L-shaped profile extruded along the length. |
| `hinge` | Leaf 1 was centred on the origin and spanned x = -15 to 15 at the default size, the knuckles sat at x = 30, and leaf 2 spanned 18 to 48. Leaf 1 touched nothing, so the hinge was two solids, and the generator requires exactly one. | One solid, built opened flat: a barrel along the pin axis cut into `knuckle_count` knuckles with a 10% gap between them, the pin through all of them, and a full-length leaf each side that runs into the barrel. The barrel has 1 mm of wall round the pin. |
| `t_bracket` | Two bugs. The holes were on the centre line, straight over the stem, and `hole()` goes all the way through, so each one went down the middle of the stem. And they were spaced about X = 0 while the bracket runs from X = 0 to `length`, so on a default bracket one of the two holes missed the part. | `hole_count` holes in a row down the length in the flat on each side of the stem (2 x `hole_count` in all), cut through the cap only, at explicit coordinates. |
| `shaft` chamfer | The edge selector `"|Z"` picks the seam line on the side of the cylinder, so the ends were never chamfered. | `"%CIRCLE"` picks the two end circles. |
| `flat_plate` default | With no hole counts the template drew a 4 x 3 grid, so a plate whose prompt never mentioned holes got 12. The parser, the validator, the LLM prompts and the offline fallback all repeated that default. | No counts means no holes. A lone `hole_count_x` or `hole_count_y` is a single row, so the other direction is 1. A prompt that asks for holes without a number (`with mounting holes`) gets 2 x 2, one in each corner. |
| `simple_box` flag | `has_lid` was the switch that shells the box into an open-top shape. No lid is ever built, and the name said it was. `lid_fit_tolerance_mm` was read and never used. | The switch is `hollow`. `has_lid` still works as an alias, so a saved part rebuilds the same, and `hollow` wins if both are present. `lid_fit_tolerance_mm` is gone from the prompts and the template ignores it. |

Behaviour changes that go with them:

- Hole rule: the validator no longer makes a hole thinner than the metal. A 6 mm hole in a 5 mm plate is ordinary, and the old rule shrank it to 4.5 mm in `validate_flat_plate` and `validate_l_bracket`. A hole now has to fit its part instead:
  - Plate: with more than one hole in a direction the template keeps the outer centres 10 mm from the edge, so at most 16 mm across leaves 2 mm of metal. A single hole can be as wide as the plate less 2 mm each side. Holes that would run into each other (less than 2 mm between them) are cut back to what fits in the row.
  - L-bracket: 2 mm of metal each side across the width, clear of the bend (at most 2 x (10 - thickness - 2) mm), and a count that fits along the shorter leg.
  - T-bracket: the limit is the flat beside the stem, `(cap - thickness) / 2 - 4`. It used to be the whole cap width less 4, which let a hole run into the stem.
- Typed sizes: when someone types a size and the validator would have changed it, the size is kept and flagged. For the validators written with `_fix` the note still said "Changed hole_diameter_mm from 12 to 10" next to a second line saying it was kept, because the rewrite only recognised the older wording. It now recognises both, so a kept size is described once, as kept.
- Gear bore: the validator now also keeps the bore 1 mm inside the root circle, so a bore cannot cut the tooth roots and split the gear. Pressure angle is kept between 14.5 and 25 degrees, where neighbouring teeth do not overlap. Fewer than 3 teeth is an error.
- Flat plate: `hole_count_x` and `hole_count_y` both 0 now means a plate with no holes, and the validator leaves it alone. A negative count, or zero in only one direction, is still corrected to 1.
- `assembly_gate.py` and `assembly_relations.py` now treat the gear's root circle as certain material, and the pulley's groove floor.
- Plate default: `validate_flat_plate` reads missing counts the same way as the template (none, or 1 for the missing side of a lone count), so a plate with no counts is no longer checked for hole fit. The explicit examples are unchanged: the Studio `Mounting plate` card and the legacy two-part assembly both state 4 x 3, so they still build 12 holes. A saved job that stored no hole counts and relied on the template default now rebuilds without holes. The built-in parsers always store the counts, so that only affects parts parsed by the AI with the counts left out.
- Box: the Studio `Box` card said "3 mm walls" and built a solid block, because it never set the switch. It now sets `hollow`. The AI parser prompt asks for `hollow` instead of `has_lid` and `PROMPT_VERSION` moved, so cached parses that used the old name are not reused.
- `tests/test_single_part_feature_tree.py` compared `PROMPT_VERSION` to one exact old string and had been failing since the aerospace and automotive tags were added. It now checks the prefix.

## Parser fixes (rule-based parser)

- Three sizes in a row (`200 x 100 x 6 mm`, `4in x 2in x 0.25in`, `120 by 80 by 5`) are read as length, width and thickness for a plate, and length, width and height for a box.
- An explicit grid (`3 by 2 grid`) beats a bare count (`6 holes`) in the same sentence.
- A bare count gives exactly that many holes (6 is 3 x 2, 7 is 7 x 1). It used to give `min(4, n)` by `n // 4`, so 6 became 4.
- `no holes`, `without holes`, `solid plate` and `plain plate` give a plate with no holes.
- A plate prompt that names no holes gives a plate with no holes, and one that asks for holes with no number gives 2 x 2. It used to give a 4 x 3 grid either way.
- `bolt circle` or `circular` gives a circular pattern.
- An enclosure, a box with walls, or an open-top box is hollow. The template shells the box only when `hollow` is true (called `has_lid` until v21) and builds a solid block otherwise, and the parser always passed false, so every box was a solid block. `cube` is a box. Wall thickness is read from `3 mm walls` or `walls of 3 mm`.

On the 114 cases in `tests/eval/parser_cases.json`, the rule-based parser went from 27.2% to 34.2% fully correct.

## Found, not changed

Nothing is open from the first audit. The templates listed under "Not audited" have not been read yet.

## Not audited

Aerospace, civil, automotive, building, PCB and freeform templates, and the standard-parts pack.

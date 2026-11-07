# Conversational building for everything, not only vehicles

Built on the merged tree (conversational vehicles + codegen + admin). Credit rules are unchanged: every question is
free, only Generate spends credits.

## What changed
- New `build_scope.py` gives every other kind of build the question-first chat the vehicles already had. "I want a
  bracket" no longer builds a default part at once. The chat says what it would start with and asks one tappable
  question at a time. Tap an answer, type your own sizes, or say "just build it" or "use the defaults".
- Covered: brackets and motor mounts, plates, boxes and enclosures, gears, pulleys and sprockets, round parts (shaft,
  bearing, spacer, washer, standoff), flanges and pipe, beams and tube, hinges and cams, connecting rods and
  crankshafts, wings and airframe parts, roads, culverts, retaining walls, buildings, fasteners, circuit boards and
  assemblies. Whole vehicles still go through `vehicle_chat.py`.
- The same rules as the vehicles: enough concrete details (sizes, a material, a count) build at once, a skip phrase
  builds at once, 5 messages build with what there is, a part already on the bench is edited with no questions unless
  the person says "new" or "another".
- Not questioned: questions and edits ("make it thicker"), standard parts ("6204 bearing", "M8 hex bolt"), anything
  naming two or more different things ("a gear on a shaft", "a plate with a bracket on top"), and assemblies when no AI
  is selected, since the rules planner cannot build them anyway.
- A circuit board is described by what it does, so naming any block or voltage ("PCB with a status LED powered from
  5V") counts as a full order. Only a bare "I need a PCB" gets questions.
- The planner sees one plain request. After the questions, the question chat is folded into a single user message
  ("I want a bracket Medium, 50 mm wide, 60 mm tall, 4 mm thick"), so the AI and the regex parser both read it as if
  it had been typed that way. Answer options use the wording the regex parser reads, so Engine mode works too.
- `conversation.py`: `_plan_core` calls `build_scope.scope_turn` after the vehicle check and before the board check.

## Adding a kind of build
One entry in `build_scope.FAMILIES`: its words, what it would start with, up to three questions with 2 to 5 options,
and `enough` (how many concrete details count as a full order). Domain packs (architecture, civil, aerospace,
automotive) join the same way when they reach the chat.

## Step by step for everything (build_steps.py)
After the questions, a build is shown in steps, written for a beginner. Each step says what it is and why it comes
at that point, then "Next is ... Say next to add it, say finish to add everything, or tell me what to change."
- Brackets, motor mounts, plates and flanges: the main body, then the holes, then the rounded edges.
- Buildings: the ground floor (when the rooms fit on it), the upper floors, then the roof. The footprint is pinned, so
  the building does not change size between steps.
- Assemblies of three or more parts (row or custom layout): the base parts first, then the rest in order.
- Circuit boards: the power supply, then the parts that do the work, then the connectors and headers.
- Roads and culverts: road surface then kerbs, one barrel then the extra barrels.
- A gear, shaft, plain box and anything else with nothing to split builds in one go. Mated assemblies and vehicles
  keep their own handling.
How it works: a staged spec is the step that is showing, plus `spec["stage"] = {k, of, target}`, where target is the
finished design. A change typed mid-way is planned against the finished design and the step is cut again, so "make it
5 mm thick" never gets confused by a hole that has not been added yet. A change to something that arrives later says
so. Every step is checked with the same validators the build uses (the part validator, the building resolver, the PCB
spec check), and if any step fails the build is not staged. Generate makes what is showing, the same rule as the
vehicles. `chat_service` keeps the stage on the saved part, and the Studio shows Next step and Finish chips.
Steps start when the person answered the questions. "just build it" builds everything at once, and a complete request
builds whole, unless they ask to be walked through it ("step by step", "walk me through", "I'm a beginner", "teach me").

## Not done
- A gear, shaft, beam, plain box and the aerospace and wall parts have no natural steps in their parameters, so they
  build in one go. Giving them steps needs new template features, not chat changes.
- Mated assemblies are not staged, because placing them needs the CAD kernel on every step.
- The question options and step texts are a first draft. Tune them against real use.

## Existing tests changed
`test_build_scope.py` now unpacks four values from a build result (the last says whether to show it in steps).
`test_conversation.py` (two tests) and `test_dfm_requirements.py` (one test) sent a bare "gear" or "a bracket" and
expected an immediate build. They now send a request with its details, since a bare name now gets a question.

## Not run
CadQuery, pydantic, FastAPI and pytest were missing, so only the pure-Python tests ran (`test_build_scope`,
`test_build_steps`, `test_conversation`, `test_assembly_gate`, the vehicle tests, and `test_pcb_studio` apart from two tests that need
pydantic). `test_dfm_requirements.py` needs pytest and was edited without being run. Run `pytest` before deploying.

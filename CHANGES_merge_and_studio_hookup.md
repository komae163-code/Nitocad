# Merge of the vehicle asset work and the code builder, plus the Studio chat hookup

This tree is the original upload with three sets of changes on top. They touched different files, so they merged without conflicts.

1. **The code builder** (from the other chat): `codegen_*.py`, `frontend/codegen.html`, five vehicle examples, two modules, tests, `CHANGES_codegen_spike.md`, and three additions to `web_app.py`.
2. **The vehicle asset work**: downloaded parts (FreeCAD library STEP files, NVIDIA SimReady props) are searched, ranked, cached, delivered inside the package under `External/`, and merged into the assembly STEP when CadQuery is present. See `asset_providers.py`, `assembly_package.py`, `vehicle_build.py`, `vehicle_planner.py`.
3. **The Studio chat hookup** (this change): the chat can write a part as code, open it beside the chat with sliders, check it, and download it.

## How a chat message is routed now

| Message | Goes to |
|---|---|
| Fits a template ("a 90 mm gear") | the template planner, as before |
| A vehicle request | the vehicle planner, as before |
| No template fits and it reads like a build request | the code builder (a new part, version 1) |
| "write code for ..." | the code builder, skipping the template planner |
| An edit to a code part ("make the slots wider") | the code builder revises it (a new version) |
| Only a material for a code part ("make it aluminium") | a free new version with the material set. No AI, no charge, no code rewritten |
| A question about a code part | the planner, planned as if no part were open |

A saved code part is an ordinary chat part with `part_type = "codegen"` and no job. Its spec points at the saved model: `parameters.codegen = {id, values}`. Versions, requirements and the manufacturability check keep working on it.

The LLM planner has a new intent, `custom`, for "no catalogue part can make this shape". The rules planner marks its "could not tell which part" reply the same way (`Plan.no_match`).

## Files changed or added by the hookup

- `codegen_chat.py` (new): routing rules, the part shape, facts, the sync bridge to the code builder.
- `conversation.py`: `custom` intent and `Plan.no_match`.
- `chat_service.py`: three optional `Deps` callables, the two hooks in `run_turn`, `_codegen_turn`, `_codegen_material_turn`, the mesh source for the manufacturability check, and a fix so "can this be 3D printed?" no longer answers "no problems" for a shape with no dimension rules (this also fixed imported parts).
- `chat_api.py`: builds the bridge, hands it the server's event loop, keeps code parts marked as paid.
- `codegen_api.py`: exposes an in-process `get_service()`.
- `codegen_parts.py`: uses the shared cache name and cache folder (`NITOCAD_ASSET_CACHE`).
- `frontend/studio.js`: code-part card, viewer, sliders, downloads.
- Tests: `tests/test_codegen_chat.py`, `tests/studio_codegen_part_check.mjs` (run with `node`).

## What the Studio's checks do on a code part

- **Requirements** (mass, size): work. Size comes from the code builder's own measurement. Mass needs a material, so until one is set it reads "unknown" and the chat says how to set one.
- **Wall thickness requirements**: stay "unknown". There are no template rules for a free shape. The manufacturability check measures walls on the mesh.
- **Manufacturability check**: works on the mesh of the rebuilt part (closed surface, thin walls, overhangs, draft, size, material fit). It is refused, and never charged, if the mesh cannot be rebuilt.
- **"Can this be 3D printed?"** (the free rule-of-thumb answer): now points to the check instead of claiming "no problems".

## Settings

- `NITOCAD_ASSET_CACHE`: where downloaded parts and the library listing are kept. Set it to a folder that survives restarts.
- `NITOCAD_GITHUB_TOKEN`: optional. Raises the GitHub limit for the FreeCAD library from 60 to 5000 requests an hour.
- `CODEGEN_PART_CACHE`: optional override for the code builder's part cache only.

## Not verified

None of this has run against a real CadQuery, a real model, FastAPI, or a browser. The tests use stand-ins, and the front-end functions were run in Node with stand-ins for the page.

## Before opening it to the public

The code builder runs code an AI wrote from a stranger's prompt. Deny its container outbound network and run the worker as an unprivileged user first (see `CHANGES_codegen_spike.md`). The chat hookup makes this reachable by every chat user.

## Known gaps

- Slider changes on a code part are live only. They are not saved as a version. Ask in the chat to keep one.
- The small 3D card in the chat thread shows the saved shape, not the slider values.
- Code parts have no on-chain anchor.
- The code builder's own hourly limit applies to its page, not to the chat path. The chat's own credits and daily message limits apply there.
- A chat message that writes code holds that chat busy until it finishes.

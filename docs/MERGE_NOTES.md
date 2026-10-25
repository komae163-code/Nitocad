# Merge of v19, v21 and v23

Three lines of work had grown apart. This project is all three together.

| Source | What it contributed |
|---|---|
| v21 | The template audit: gear, sprocket, pulley, L-bracket, angle, hinge, T-bracket and shaft fixes, the hole rule, the plate with no holes by default, the `hollow` box flag, the benchmark job in CI. See `docs/TEMPLATE_AUDIT.md`. |
| v19 | Candidate designs (`candidates.py`), the build recipe (`recipe.py`), matching a part against a reference file (`reference_match.py`), and the backend and Studio changes that go with them: `chat_api.py`, `chat_service.py`, `chat_store.py`, `cad_generator.py`, `requirements.py`, `web_app.py`, `frontend/studio.js`, `frontend/studio.css`, and four new test files. |
| v23 | The newest front end: the new landing page (`frontend/index.html`, `frontend/hero-blueprint.js`, `frontend/adam-look.css`). |

How conflicts were settled:

- No file was changed by more than one source except the landing page. v19 and v23 both matched the common base on every file the audit touched, so v21's version won there.
- `frontend/index.html`: v23's page in full. v19 had only removed the Airplane and House scene tabs and narrowed the "What you can build" copy. v23 already has no such tabs and has its own "Limit" line for each discipline, so both v19 edits were superseded.
- `frontend/hero-blueprint.js`: v23's file, plus v19's one-line guard that stops the hero cycling to a scene with no tab.
- `frontend/studio.js` and `studio.css`: v19's. v23 left them unchanged, so the newer Studio features (candidate cards, reference match chip) come from v19.
- `adam-look.css` is scoped to `body.adam`, so the Studio and the other pages keep their look.

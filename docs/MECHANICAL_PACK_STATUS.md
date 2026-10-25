# Mechanical domain pack: what exists and how far it is proven

Three levels, kept apart on purpose:

* **Tested here**: logic exercised by automated tests that ran without a CAD kernel.
* **Needs CAD run**: written and unit-tested around the edges, but the CadQuery/OCCT part has not been
  executed yet. Run `pytest -m requires_cadquery` before relying on it.
* **Not built**: listed so nothing is assumed.

| Area | Module | Level |
|---|---|---|
| Part validators, all 24 part types | `validator.py` | Tested here (rules). Corrected values building real solids: needs CAD run (`test_template_geometry.py`) |
| Standards: ISO threads, fasteners, washers, bearings, pipe, IPE/UPN | `standards.py` | Tested here against published values |
| Fastener templates (bolt, socket screw, nut) | `cad_templates/fasteners.py` | Needs CAD run. Thread is cosmetic (plain shank) |
| Materials table | `materials.py` | Tested here. Values are typical, confirm with the supplier |
| Screening: beam bending/deflection, bolt tension/shear, mass and centre of mass | `screening.py` | Tested here against hand calculations. NOT FEA, no stress concentrations, fatigue or buckling |
| Process checks: CNC, turning, FDM, sheet metal | `dfm.py` | Tested here. Parameter-driven rules of thumb. Hole positions and overhangs are not in the templates, so those rules are absent |
| Requirements that persist and re-check each version | `requirements.py`, `chat_store.py`, `chat_service.py` | Tested here |
| Assemblies: mates, patterns, interference, floating parts, parts list, mass | `assembly*.py` | Logic tested here. Interference and centroids: needs CAD run |
| Assemblies in the chat (planner, service, studio panel), re-export of stored assemblies | `conversation.py`, `chat_service.py`, `cad_generator.py`, `frontend/studio.js` | Chat flow and panel tested here with a fake generator. Real build and re-export: needs CAD run (`test_assembly_geometry.py`) |
| Drawings: hole call-outs with counts, centre marks, notes, ISO 2768 table, parts list | `drawing_footer.py`, `exporters.py` | PDF tested here (rendered and viewed). DXF: needs a run with ezdxf |
| Export checks: file written, STEP re-import | `exporters.py` | Logic tested here. Real STEP round trip: needs CAD run |
| Parser accuracy set (111 prompts) | `eval_parser.py`, `tests/eval/` | Harness tested here. Baseline score: run `python eval_parser.py` |

## Not built

* Section views, per-feature tolerances and GD&T on drawings
* Real FEA, fatigue, buckling, thermal
* Helical thread geometry
* Hole-to-edge and internal-corner-radius process rules (needs hole positions in the part spec)
* Studio screens for the screening calculations and the process-check results (they answer in chat text only)

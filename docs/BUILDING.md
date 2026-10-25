# Building design (architecture domain)

Describe a building in words, or give a spec; NitoCAD lays it out, checks it, and writes a deliverable folder:
plans, 4 elevations and 2 sections (SVG, PDF, DXF) at a true standard scale, door/window/room/furniture
schedules, quantities, a compliance report, an optional preliminary foundation estimate, 3D parts (feature
trees, STEP when CadQuery is installed) and a manifest with file hashes.

The AI only proposes a spec. Deterministic code lays out and checks everything, and refuses with a reason when
something does not fit.

## Try it
```bash
python scripts/demo_building.py --text "two-storey house with three bedrooms, living room, dining room, kitchen, two bathrooms"
python scripts/demo_building.py my_house.json --out out/my_house --soil 150     # soil bearing from a geotechnical report
python scripts/demo_building.py my_house.json --ruleset my_jurisdiction.json     # your authoritative rules
```
From text you get a PROPOSAL: every inferred item is printed as "assumed:", open points as "question:". Nothing is
confirmed until you confirm it (`building.intake.confirm`).

## What it does
- **Intake** (`intake.py`): rule parser (offline) or any LLM, including a vision model for drawings; never trusts
  dimensions read from an image; searches for the smallest footprint whose layout builds. Room areas are minimums: rooms that come out larger than requested (the floor with the most rooms sets the footprint) are reported as notes, not refused. When nothing builds, the message names the rooms that block it.
- **Layout** (`layout.py`): rectangular footprint, 1-4 levels, central corridor, two room bands balanced by area,
  stair hall with straight stair, doors and windows sized per room, entrance into the living room.
- **Drawings** (`sheet.py`, `sheets.py`): one neutral sheet model rendered to SVG, PDF (reportlab) and DXF (ezdxf).
  Walls are poche with real gaps at openings; layers use AIA-style names; title block with scale and status.
- **Furniture** (`furniture.py`): beds, wardrobes, sofa set, dining set, kitchen units, bathroom fixtures, desk,
  shelving, placed by rules, never inside a door swing or overlapping; schedule and plumbing-fixture count; optional 3D.
- **Compliance** (`compliance.py`): PASS / FAIL / UNKNOWN / NOT_APPLICABLE per rule. Ships only rules of thumb
  (authoritative = false). Load a jurisdiction's rules as JSON with citations; an uncited rule is UNKNOWN. The overall
  result is never PASS: structure, fire, egress, services and approvals are listed as unassessed.
- **Foundation** (`structure.py`): UNKNOWN unless you supply an allowable soil bearing capacity. With it, a PRELIMINARY
  strip-footing estimate with every assumption listed, flagged "engineer review required".

## Limits that remain
- Footprint is rectangular (width >= depth) with one corridor typology; no L/U/T/curved plans, courtyards or split levels.
- Straight stair only; gable or flat roof only (no hip, dormers, balconies, verandas, garages).
- Image intake needs a vision-capable model wired in (the default text model cannot read images); untested with real images.
- Furniture is heuristic; if a piece does not fit you get a warning, not a bad placement. Room labels can overlap furniture.
- Not engineering: no structural design, no services design, no code approval. Drawings are marked CONCEPT. A registered
  architect and engineer must design and approve a real building, and local authority approval is required.
- Defaults (225/150 mm block walls, 3000 mm floor height, door/window sizes, room minimums) are assumptions and rules
  of thumb, not code values; review them for your market.

# NitoCAD update - what was fixed, what was not

Everything below refers to this zip unzipped over your original repo.

## FIXED (verified here unless marked)

**Wallet anchoring ("On-chain anchoring is not enabled")**
- Root cause: `config.py` had no `DESIGN_REGISTRY_ADDRESS` field, so the Railway variable was silently dropped. Added.
- Registry address is whitespace-trimmed everywhere; `/api/anchor/status` shows why anchoring is off; real reason is logged.
- `index.html` / `profile.html`: chain + account checks before signing, add-chain fallback, clear rejection message, optional "Anchor" button on the profile page. (JS syntax-checked; not run in a wallet.)

**Automotive domain pack (`domain_packs/`)** - 9 parts: brake rotor, wheel hub, piston, flywheel, control arm,
steering knuckle, ball joint, subframe, battery bracket. One spec per part drives template, validator and LLM prompt.

**Interface-driven assemblies** (replaces the old "always a plate + bracket" behaviour)
- Mating model, 8 compatibility rules, rigid-mate solver, dependency graph, what-if impact analysis.
- Wheel corner module: subframe -> control arm -> ball joint -> knuckle -> hub -> rotor.
- Generation is gated on interface errors and real-solid overlap; stores the resolved spec; on-demand export works.
- Measured clearances (analytic envelopes), ISO 286/2768 fits with worst-case checking, tolerance stack-up,
  jounce/rebound sweep, fastener load screening. Default corner: closest non-mating approach measured at 2.0 mm.
- API: /api/assembly/catalog, /analyze, /impact, /deep-analysis; /api/domain-packs.
- Tests that ran here and passed: 331 pure-Python (82 + 67 + 122 + 33 + 27), including your existing validator and parser suites; ~35,000 random valid parts and 4,000 random assemblies, no crashes.

## NOT FIXED / NOT VERIFIED

1. **Real CadQuery/OCCT geometry has never been run.** No CadQuery in my sandbox. Run on your VPS:
   `python scripts/verify_automotive.py` and the `requires_cadquery` tests. Highest-risk spots: ring-gear tooth cut,
   the arc bend in the battery bracket, vane unions in vented rotors, the ball-joint/knuckle cones.
2. **Envelope model vs real solids** is only checked by `verify_automotive.py` / `TestEnvelopeAgainstOCCT` (unrun).
3. **ISO tables are hand-transcribed** (checked against textbook examples): verify against your copy of ISO 286-1.
4. **Kinematics** is a single swing arm (no upper arm, spring, damper, steering). **Load screening** cannot derive
   ball-joint/bushing reactions; you must supply them. It is not structural validation.
5. **Open interfaces**: right-hand clevises and the 4 cradle body mounts (no mirrored arm / body part yet).
6. **Chat / Studio assembly integration: NOT DONE.** The `conversation.py` (`UNSUPPORTED_TYPES`), chat planner and
   Studio "Specs" tab you described are not in the repository you gave me. Send those files and I will do the three
   changes. Also not built: a mass column in the assembly parts list.
7. **Browser/wallet flows** (anchoring, UI) were not executed.

## UPLOAD / EDIT FEATURE - INCOMPLETE (work in progress, NOT wired into the app)

`file_edit/` is a standalone package; nothing imports it yet, so it cannot affect the running app.

Done and exercised in my sandbox (ad-hoc scripts, not yet saved as test files):
- format detection with magic-byte validation; strict operation schema
- STL/OBJ (analysis, transforms, repair, export), SVG (sanitising edits), PNG/JPG/WEBP (edits, EXIF/GPS stripping,
  trace to SVG/DXF), PDF (page ops, stamps, sanitising, rendering), Gerber (parse/edit/preview), KiCad
  (.kicad_pcb/.sch/.mod/.sym edit + preview)

Written but UNTESTED (libraries unavailable here): DXF handler (needs ezdxf), STEP/IGES/BREP handler (needs CadQuery);
raster -> STEP/STL extrusion.

NOT built: upload/edit/download API endpoints, frontend upload button and edit panel, file storage with quotas/expiry,
isolated worker processes (needed before parsing untrusted files in production), AI instruction parser, credit
charging, test files, `requirements.txt` additions (pillow, lxml, pypdf, pdfplumber, reportlab, opencv-python-headless,
numpy, scipy are used by `file_edit`).
Deliberately NOT offered: footprint rotation, moving schematic symbols, PDF text replacement/true redaction,
DXF mirror, Excellon drill files.

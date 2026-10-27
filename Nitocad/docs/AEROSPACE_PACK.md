# Aerospace domain pack

Status: **substantially implemented at conceptual and evidence-workflow level**.
NitoCAD computes, checks and records. It does **not** certify, approve or
declare anything airworthy or flight-ready, and no code path produces such a
statement. The strongest status the software computes by itself is
`ready_for_engineering_review`; anything above it is a *recorded human decision*
(see "Beyond ready for engineering review").

## Implemented
**Geometry.** `airfoil_wing`, `wing_rib`, `fuselage_frame`, `wing_spar`, `attachment_lug`,
and concept (outer-mold-line) `fuselage`, `tail_surface`, `turbofan_engine` (optional stage
rows), `jet_aircraft`. CadQuery solid tests must run in CI; they could not run in the
authoring sandbox. Concept geometry has no structure or engine internals: its mass is
declared (or derived from a declared thrust-to-weight), never computed from volume.

**Structures (closed-form screening).** Wing root bending, spar bending and web buckling,
lug net tension / bearing / shear-out, thin-ring frame, fastener bearing, mass. Assemblies
and interfaces (edge distance, clearance, pitch, bearing on both parts, fastener shear,
galvanic compatibility), with joint rules that you supply with a cited source.

**Propulsion.** Turbofan/turbojet design point, off-design throttle/altitude/Mach sweeps
(fixed-geometry model matched to the design point, corrected-flow limit), mean-line sizing of
fan/compressor/turbine stages with a check against the CAD fan diameter.

**Aerodynamics and flight.** Drag build-up, lift-curve slope, high-lift (flap and slat
increments from type, area, chord ratio, deflection; clean airfoil cl_max, gear drag and
engine-out drag are inputs), level flight, thrust available vs required, Breguet range, climb,
service ceiling, takeoff (all engines, one-engine-out second segment), **balanced field length
with an engine failure at V1** (dry and wet, hydroplaning speed), landing with braking
(dry and wet), mass and CG roll-up.

**Stability and control.** Longitudinal static stability and trim; **lateral-directional
stability and dynamic modes** (short period, phugoid, Dutch roll, roll, spiral from linearised
equations; derivatives estimated or overridden with declared values; handling criteria you
declare); **elevator sizing** (effectiveness, trim deflection per loading case, stabiliser
incidence for zero elevator, rotation pitch acceleration).

**Evidence and allowables.**
- *FEA evidence verifier.* NitoCAD does not run FEA. It decides whether FEA you ran elsewhere
  can count: mesh convergence (observed order, grid convergence index), reaction equilibrium,
  material card match, element quality, margin computed from the extrapolated stress, and a
  cross-check against the closed-form screening stress, against acceptance limits you declare.
- *Structured test evidence.* Measurements are compared with your criteria by NitoCAD; it
  needs lab, article conformity, calibration references and a report hash.
- *Manual evidence* (anything else) must name the tool or lab, its version, a report
  reference and a reviewer.
- *A/B-basis allowables from your own coupon data*, normal distribution with exact tolerance
  factors and an Anderson-Darling normality check. Basis is only as valid as your sampling plan.

**Records.** Revision identity, append-only evidence, stale detection, change-impact report,
release gate with explicit blockers, compliance matrix, hash-chained approvals.

## Beyond ready for engineering review
A ladder of human sign-offs, bound to the exact design revision and assessment digest, in an
append-only hash chain (a forked chain is rejected by the database):
`engineering_review_accepted` (an authorised reviewer who is not the design owner)
-> `submitted_for_certification` (a different authorised person; compliance matrix complete)
-> `authority_acceptance_recorded` (an authorised liaison records the authority's reference and
the SHA-256 of its document). NitoCAD does not verify the authority's decision. Any change to
the design or to the assessment makes earlier records stale and the stage drops back.

## NOT implemented (do not claim)
- Licensed allowable data (MMPDS, CMH-17 values). You supply your own with a source, or derive
  them from your own coupon tests. The built-in properties are typical values only.
- A 3D FEA solver or hosting for one. Only the verifier for results produced elsewhere.
- Certification itself, compliance with any specific regulation, authority acceptance.
- Blade aerodynamics and real blading, component maps, surge margin, spool speeds, transient
  engine models, engine certification.
- Contaminated (snow/slush) runways, wind and slope, brake energy limits, noise procedures.
- Stability augmentation and flight control laws, flutter and aeroelasticity, structural loads,
  fatigue and damage tolerance, crash and thermal analysis.
- Statistical weight estimates: masses are declared with a source.
- Wind-tunnel/CFD validation of the aerodynamic and high-lift estimates (typically +-20..30%).
- Reading assembly features from CAD; bolt groups with moment loads; joint fatigue.

## API (wallet-authenticated unless noted)
- `GET  /api/aero/materials` (public)
- `POST /api/aero/assess` design spec -> assessment (includes certification stage when a `certification` block exists)
- `POST /api/aero/impact`, `POST /api/aero/assembly/assess`, `POST /api/aero/assembly/impact`
- `GET  /api/aero/designs/{design_id}` latest spec plus evidence with `current` flags
- `POST /api/aero/allowables/basis` A/B-basis from coupon values
- `POST /api/aero/approvals` record a sign-off (`owner`, `design_id`, `stage`, `statement`, ...);
  only people listed in the design's `certification.authorized_signers` may sign (others get 404)
- `GET  /api/aero/approvals/{owner}/{design_id}` chain, chain validity and current stage

## Rules the code enforces
1. A missing load, factor of safety, allowable or declared input is never defaulted: the analysis
   is `not_evaluable` and a gap is listed.
2. Inputs in `draft` or `assumed` state block release; every input block needs a source.
3. Typical material properties block release.
4. Hand calculations never satisfy substantiation: parts need current, accepted `fea` evidence
   (engines and aircraft also `test`) for the current revision.
5. Evidence is append-only; a changed revision makes older evidence stale unless the inputs it
   declared are unchanged.
6. Approvals need an authorised signer, separation of duties, and an unbroken hash chain.

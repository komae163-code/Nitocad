"""
Main CAD generation pipeline with validation and assembly support.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import cadquery as cq

import db
import exporters
import storage
import import_ops
import import_store
import assembly as assembly_mod
import assembly_parser
import assembly_spec
import assembly_templates
from assembly import (
    assembly_route,
    mated_spec,
    assembly_spec_from_parts,
    generate_assembly,
    get_assembly_parts,
    legacy_assembly_example,
    normalize_assembly_spec,
    parse_assembly_description,
)
from cad_templates import TEMPLATES
from cad_templates._safe_ops import safe_chamfer, safe_fillet
from config import settings
from deepseek_parser import parse_description as deepseek_parse_description
from exceptions import (
    AssemblyError,
    GenerationError,
    GeometryValidationError,
    NitocadError,
    ParseError,
    StorageError,
    UnsupportedFormatError,
    UnsupportedPartTypeError,
)
from logging_config import get_logger, log_duration
from smart_parser import ParsedParameters
from validator import validate_parameters

logger = get_logger(__name__)

# Bounds on the *number* of extra operations a single request can chain -
# unrelated to any one operation's own parameter validation (radius,
# thickness, etc, which validator.py / safe_fillet/safe_chamfer already
# bound). Without this, a pathological `operations` list (thousands of
# entries) is a cheap way to make one request tie up the OCCT kernel for
# a long time - each fillet/chamfer/shell call is real geometry work, not
# a no-op.
MAX_OPERATIONS_PER_REQUEST = 25


class CADGenerator:
    """Main CAD generation engine."""

    def __init__(self, output_dir: str | None = None):
        self.output_dir = Path(output_dir or settings.OUTPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_from_text(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """See _generate_from_text. This wrapper says whose attached files a part may read."""
        with import_store.owner(kwargs.get("user_id") or "default"):
            return self._generate_from_text(*args, **kwargs)

    def _generate_from_text(
        self,
        description: str,
        use_deepseek: bool | None = None,
        api_key: str | None = None,
        model: str | None = None,
        user_id: str = "default",
        formats: list[str] | None = None,
        endpoint: str = "paid",
        parse_policy: Any = None,
        preparsed: Any = None,
        draft: bool = False,
        ai_tier: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate CAD from natural language description.

        use_deepseek: None (default) = auto - uses DeepSeek if a key
        resolves from anywhere (caller-supplied api_key, or this server's
        own DEEPSEEK_API_KEY env var), otherwise regex. True/False force
        one or the other explicitly. See deepseek_parser.parse_description
        for the full resolution logic - this is what lets the frontend
        not need a DeepSeek toggle in the UI at all; the server decides.

        formats: which export formats to produce. Defaults to every
        supported format (exporters.DEFAULT_FORMATS = "step", "stl",
        "iges", "dxf", "pdf") when omitted - pass an explicit list to
        narrow it. Invalid entries raise UnsupportedFormatError (422)
        before any geometry work happens.

        Returns dict with:
        - step_file / stl_file / iges_file / dxf_file / pdf_file: local
          paths for each requested format (only formats that were
          requested are populated; the rest are None)
        - step_url / stl_url / iges_url / dxf_url / pdf_url: matching
          download URLs (R2 if configured, else local /download/{fmt}/..)
        - parameters: Parsed parameters used
        - validation: Validation results
        - success: Boolean
        - error / error_type: populated on failure

        preparsed: an already-built ParsedParameters (from the chat planner,
        chat_service.py). When given, the parse step is skipped entirely and
        everything after it - validation, geometry, verification, the job
        record, provenance - runs exactly as for a parsed description. Paid
        and preview callers never pass it.
        """
        # Defined before the try block so the except handler below always
        # has a value, even in the unlikely case parsing itself throws
        # before the resolution logic runs.
        resolved_key = api_key or settings.DEEPSEEK_API_KEY
        resolved_model = model or settings.DEEPSEEK_DEFAULT_MODEL
        used_deepseek = bool(resolved_key) and use_deepseek is not False
        parser_name = None
        parser_model = resolved_model if resolved_key else None
        prompt_version = None
        retry_count = 0
        parse_hash = None

        try:
            if not description or not description.strip():
                raise ParseError("description must not be empty")
            if len(description) > 4000:
                raise ParseError(
                    f"description is {len(description)} characters; the parser isn't "
                    "designed for anything beyond a short part spec. Keep it under 4000."
                )

            # Fail fast on a bad format list before doing any parse/geometry
            # work - no point spending an OCCT build only to reject the
            # export format at the very end.
            resolved_formats = exporters.validate_formats(list(formats) if formats else list(exporters.DEFAULT_FORMATS))

            # Interface-driven assemblies: a NAMED assembly ("wheel corner module", see
            # assembly_templates.py) is built from its template, deterministically and without an
            # AI key; parts are placed by solving their mates, and generation is gated on interface
            # fit and real-solid overlap (assembly_spec.py, mating.py). Every other assembly
            # description goes through the chat-planner path below (parts laid out in a row).
            assembly_report: dict | None = None
            assembly_checks_data: dict | None = None
            if preparsed is not None:
                params = preparsed
            elif assembly_templates.match_named_assembly(description):
                with log_duration(logger, "assembly_parse"):
                    asm = assembly_parser.parse_assembly_request(
                        description,
                        api_key=resolved_key if use_deepseek is not False else None,
                        model=resolved_model,
                    )
                params = ParsedParameters(
                    part_type="assembly",
                    parameters={"assembly": asm.spec},
                    parser=asm.parser, model=asm.model, prompt_version=asm.prompt_version,
                    retry_count=asm.retry_count, parse_hash=asm.parse_hash, warnings=list(asm.warnings),
                    confidence=1.0,
                )
            else:
                with log_duration(logger, "parse", part_type=None):
                    params = deepseek_parse_description(
                        description, use_deepseek=use_deepseek, api_key=api_key, model=resolved_model
                    )

            # Record what actually happened, rather than inferring it only
            # from whether an API key existed. A cache hit is a validated
            # DeepSeek-origin parse; regex fallback is not.
            parser_name = params.parser
            parser_model = params.model or resolved_model
            prompt_version = params.prompt_version
            retry_count = params.retry_count
            parse_hash = params.parse_hash
            used_deepseek = parser_name in {"deepseek", "cache"}

            if params.part_type == "unknown":
                raise ParseError("Could not determine part type from description")

            logger.info(
                "parsed description",
                extra={"part_type": params.part_type, "used_deepseek": used_deepseek},
            )

            # Step 2: Validate and auto-correct parameters
            with log_duration(logger, "validate", part_type=params.part_type):
                corrected_params, validation_result = validate_parameters(
                    params.part_type, params.parameters, description=description
                )

            if not validation_result.is_valid:
                raise GeometryValidationError(
                    "Parameters are geometrically invalid: " + "; ".join(validation_result.errors),
                    details={"errors": validation_result.errors},
                )

            if validation_result.warnings:
                logger.warning(
                    "validation warnings: %s",
                    validation_result.warnings,
                    extra={"part_type": params.part_type},
                )
            if validation_result.corrections:
                logger.info(
                    "auto-corrections applied: %s",
                    validation_result.corrections,
                    extra={"part_type": params.part_type},
                )
                params.parameters = corrected_params

            # Step 3: Generate CAD
            imported_mesh = False
            if params.part_type == "imported":
                try:
                    params.parameters = import_ops.clean_import_params(params.parameters or {})
                except ValueError as exc:
                    raise GeometryValidationError(f"The attached file's edits cannot be used: {exc}") from exc
                imported_mesh = params.parameters["kind"] in import_ops.MESH_KINDS
            if imported_mesh:
                export_paths, mesh_stats = self._generate_imported_mesh(params.parameters)
                params.parameters = {**params.parameters, "bbox_mm": [round(v, 3) for v in mesh_stats["size_mm"]]}
                workplane_for_verify = None
            elif params.part_type == "assembly" and assembly_route(params.parameters) == "mated":
                # Fail-closed: interfaces that do not fit, or solids that collide, raise instead of
                # quietly becoming a different assembly.
                export_paths, assembly_report, resolved_spec, assembly_checks_data = self._generate_resolved_assembly(
                    mated_spec(params.parameters), resolved_formats
                )
                # Store the RESOLVED spec (auto-corrections applied) with the job, so the exact
                # assembly can be rebuilt on demand.
                params.parameters = {"assembly": resolved_spec}
                params.warnings = list(params.warnings) + [
                    f["message"] for f in assembly_report["findings"] if f["severity"] == "warning"
                ]
                workplane_for_verify = None
            elif params.part_type == "assembly":
                # The parts list is kept with the job (as its parameters) so a later
                # export rebuilds exactly this assembly instead of parsing the text again.
                try:
                    given = (params.parameters or {}).get("parts")
                    if given:   # the chat planner named the parts
                        layout = (params.parameters or {}).get("layout") or "row"
                        parts_spec = assembly_spec_from_parts(given, layout=layout)
                    else:       # read them out of the description
                        layout = "row"
                        parts_spec = normalize_assembly_spec(parse_assembly_description(description))
                except Exception as exc:  # noqa: BLE001
                    raise AssemblyError(f"Assembly construction failed: {exc}") from exc
                params.parameters = {"parts": parts_spec, "layout": layout}
                export_paths, assembly_checks_data = self._generate_assembly(parts_spec, resolved_formats)
                workplane_for_verify = None
            else:
                try:
                    export_paths = self._generate_single_part(
                        params, validation_result, resolved_formats
                    )
                except GenerationError as exc:
                    # A typed size was kept, and the part could not be built with it.
                    if validation_result.flags and str(exc).startswith("Failed to build"):
                        listed = "; ".join(
                            f"{k} = {f['value']} (safer: {f['suggested']})" for k, f in validation_result.flags.items()
                        )
                        raise GeometryValidationError(
                            f"The part could not be built with the sizes you typed: {listed}. "
                            "Change them, or ask me to use the safer values.",
                            details={"flags": validation_result.flags},
                        ) from exc
                    raise
                # Rebuild briefly for verification when CQ is available;
                # solid is optional so tests without CQ still pass.
                workplane_for_verify = None
                try:
                    # A building is several solids by design (slabs, walls, stair, roof): each one is
                    # checked (valid, single solid) when it is built, and the STL is still checked
                    # for watertightness below, so the one-solid rule is not applied to the compound.
                    if params.part_type != "building":
                        workplane_for_verify = self._build_workplane(
                            params.part_type, params.parameters, warnings=[]
                        )
                except Exception:  # noqa: BLE001
                    logger.debug("could not rebuild workplane for verification", exc_info=True)

            # Step 3b: Post-build verification (fail-closed)
            from geometry_verify import verify_geometry

            stl_path = export_paths.get("stl")
            if params.part_type == "imported":
                # A file from somewhere else is not held to "one watertight solid". Its size is
                # recorded with the job so the chat can show and edit it.
                if workplane_for_verify is not None:
                    bb = workplane_for_verify.val().BoundingBox()
                    params.parameters = {**params.parameters, "bbox_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)]}
                from geometry_verify import VerificationReport
                vreport = VerificationReport(
                    passed=True,
                    warnings=["Attached files are checked when they are read, not for a single watertight solid"],
                )
            else:
                vreport = verify_geometry(
                    solid=workplane_for_verify,
                    stl_path=stl_path,
                    expected=None,  # expected volume/bbox filled when compiler provides them
                )
            if not vreport.passed:
                raise GeometryValidationError(
                    "Post-build geometry verification failed: " + "; ".join(vreport.errors),
                    details={"verification": vreport.to_dict()},
                )

            # Upload each produced format to R2 if configured, otherwise
            # fall back to serving the local file via /download/{fmt}/{filename}
            # - see storage.py and the download routes in web_app.py.
            try:
                urls = {
                    fmt: (
                        storage.upload_export(str(p), fmt)
                        or f"/download/{fmt}/{p.name}"
                    )
                    for fmt, p in export_paths.items()
                }
            except Exception as exc:  # noqa: BLE001 - genuinely any storage backend error
                raise StorageError(f"Generated locally but failed to upload: {exc}") from exc

            step_url = urls.get("step")
            stl_url = urls.get("stl")

            job_id = db.record_job(
                description=description,
                user_id=user_id,
                success=True,
                part_type=params.part_type,
                parameters=params.parameters,
                material=params.material,
                used_deepseek=used_deepseek,
                step_url=step_url,
                stl_url=stl_url,
                warnings=list(params.warnings) + list(validation_result.warnings) + list(vreport.warnings),
                corrections=validation_result.corrections,
                parser=parser_name,
                parser_model=parser_model,
                prompt_version=prompt_version,
                retry_count=retry_count,
                parse_hash=parse_hash,
                draft=draft,
                ai_tier=ai_tier,
                state="settled",
                verification_report=vreport.to_dict(),
            )

            result = {
                "success": True,
                "job_id": job_id,
                "parameters": params.dict(),
                "validation": {
                    "warnings": list(params.warnings) + list(validation_result.warnings) + list(vreport.warnings),
                    "errors": validation_result.errors,
                    "corrections": validation_result.corrections,
                    "flags": validation_result.flags,
                },
                "verification": vreport.to_dict(),
                "assembly": assembly_report,
                "assembly_checks": assembly_checks_data,
                "error": None,
                "provenance": self._provenance(
                    parser_name, parser_model, prompt_version, retry_count, parse_hash
                ),
            }
            # step_file/stl_file/... and step_url/stl_url/... - populated
            # only for formats that were actually requested, None otherwise,
            # so old callers that only ever look at step_file/stl_file keep
            # working unchanged.
            for fmt in exporters.SUPPORTED_FORMATS:
                result[f"{fmt}_file"] = str(export_paths[fmt]) if fmt in export_paths else None
                result[f"{fmt}_url"] = urls.get(fmt)
            return result

        except NitocadError as exc:
            logger.warning("generation failed (%s): %s", type(exc).__name__, exc.message)
            # A ParseError raised inside the parser carries what actually ran.
            d = getattr(exc, "details", None) or {}
            parser_name = parser_name or d.get("parser")
            parser_model = d.get("model") or parser_model
            prompt_version = prompt_version or d.get("prompt_version")
            retry_count = retry_count or d.get("retry_count", 0)
            vrep = d.get("verification") if isinstance(d, dict) else None
            job_id = db.record_job(
                description=description, user_id=user_id, success=False,
                used_deepseek=used_deepseek, error=exc.message,
                parser=parser_name, parser_model=parser_model,
                prompt_version=prompt_version, retry_count=retry_count,
                parse_hash=parse_hash,
                state="failed",
                verification_report=vrep,
            )
            return {
                "success": False,
                "job_id": job_id,
                "error": exc.message,
                "error_type": type(exc).__name__,
                "parameters": None,
                "provenance": self._provenance(
                    parser_name, parser_model, prompt_version, retry_count, parse_hash
                ),
            }
        except Exception as exc:  # noqa: BLE001 - last-resort boundary; see exceptions.py
            logger.exception("unexpected generation failure")
            db.record_job(
                description=description, user_id=user_id, success=False,
                used_deepseek=used_deepseek, error=str(exc),
                parser=parser_name, parser_model=parser_model,
                prompt_version=prompt_version, retry_count=retry_count,
                parse_hash=parse_hash,
            )
            return {
                "success": False,
                "error": f"Generation failed: {exc}",
                "error_type": "UnexpectedError",
                "parameters": None,
            }

    # -- internal helpers ------------------------------------------------

    @staticmethod
    def _provenance(parser, model, prompt_version, retry_count, parse_hash) -> dict | None:
        """What actually parsed this request - shown on the job/result cards."""
        if parser is None:
            return None
        return {
            "parser": parser,
            "model": model,
            "prompt_version": prompt_version,
            "retry_count": retry_count,
            "parse_hash": parse_hash,
        }

    def _generate_imported_mesh(self, parameters: dict) -> tuple[dict[str, Path], dict]:
        """An attached STL or OBJ after its edits, as an STL file. A mesh can only be exported
        as STL: it has no faces to write as STEP, IGES, DXF or PDF."""
        from imported_solids import build_imported_mesh
        with log_duration(logger, "imported_mesh"):
            stl_bytes, stats = build_imported_mesh(parameters)
        path = self.output_dir / f"imported_{uuid.uuid4().hex}.stl"
        path.write_bytes(stl_bytes)
        return {"stl": path}, stats

    def _generate_assembly(
        self, parts_spec: "list[dict] | str", formats: list[str]
    ) -> tuple[dict[str, Path], dict]:
        """Build an assembly and export `formats`. Pass the parts list. A plain
        description string is still accepted and parsed, for older callers.
        Returns (export_paths, checks): the checks hold the parts list, overlaps and mass."""
        try:
            with log_duration(logger, "assembly_build"):
                if isinstance(parts_spec, str):
                    parts_spec = normalize_assembly_spec(parse_assembly_description(parts_spec))
                assembly = generate_assembly(parts_spec)
            with log_duration(logger, "assembly_checks"):
                checks = self._assembly_checks(assembly, parts_spec, check_floating=False)
        except Exception as exc:  # noqa: BLE001
            raise AssemblyError(f"Assembly construction failed: {exc}") from exc
        paths = self._export_assembly(assembly, f"assembly_{uuid.uuid4().hex}", formats,
                                      bom=(checks or {}).get("bom"))
        return paths, checks

    @staticmethod
    def _assembly_checks(assembly, parts: list[dict], *, check_floating: bool) -> dict | None:
        """Parts list, overlap, floating-part and mass checks. Advisory: a failure here is logged
        and the assembly is still built, never turned into a different assembly."""
        try:
            return assembly_mod.assembly_checks_report(assembly, parts, check_floating=check_floating)
        except Exception as exc:  # noqa: BLE001
            logger.warning("assembly checks failed: %s", exc)
            return None

    def _resolve_and_build_assembly(self, spec: dict):
        """Resolve -> gate on interface errors -> build parts -> gate on real
        solid overlap -> assemble. Returns (assembly, resolved, report).
        Fail-closed: an assembly whose interfaces do not fit, or whose solids
        collide, is never exported."""
        try:
            resolved = assembly_spec.resolve_assembly(spec)
        except assembly_spec.AssemblySpecError as exc:
            raise AssemblyError(f"Invalid assembly: {exc}") from exc
        if not resolved.ok:
            raise GeometryValidationError(
                "Assembly interfaces do not fit: " + "; ".join(f.message for f in resolved.errors[:5]),
                details={"findings": [f.to_dict() for f in resolved.errors]},
            )
        try:
            with log_duration(logger, "assembly_build", parts=len(resolved.order)):
                built = assembly_mod.build_parts(resolved)
                fit = assembly_mod.check_interference(resolved, built)
        except NitocadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AssemblyError(f"Assembly construction failed: {exc}") from exc
        collisions = [f for f in fit if f.severity == "error"]
        if collisions:
            raise GeometryValidationError(
                "Assembly parts collide: " + "; ".join(f.message for f in collisions[:5]),
                details={"findings": [f.to_dict() for f in collisions]},
            )
        report = resolved.to_dict()
        report["findings"] = report["findings"] + [f.to_dict() for f in fit]
        report["interference_checked"] = True
        # Measured clearances from the analytic envelopes. ADVISORY: OCCT's
        # boolean check above is the gate, so an envelope-only "collision" is
        # downgraded to a warning (it would mean the envelope model and the
        # real solid disagree, which is itself worth surfacing).
        try:
            import assembly_analysis
            meas = assembly_analysis.measure_clearances(resolved)
            report["analytic_clearances"] = {
                "pairs": meas["pairs"], "min_non_mating_clearance_mm": meas["min_non_mating_clearance_mm"]}
            for f in meas["findings"]:
                d = f.to_dict()
                if d["severity"] == "error":
                    d["severity"] = "warning"
                    d["message"] = "[envelope vs solid disagree] " + d["message"]
                if d["severity"] != "info":
                    report["findings"].append(d)
        except Exception as exc:  # noqa: BLE001 - advisory only
            logger.warning("analytic clearance measurement failed: %s", exc)
        try:
            assembly = assembly_mod.assemble_resolved(resolved, built)
        except Exception as exc:  # noqa: BLE001
            raise AssemblyError(f"Assembly construction failed: {exc}") from exc
        return assembly, resolved, report

    def _generate_resolved_assembly(self, spec: dict, formats: list[str]):
        """Interface-driven assembly: resolve, gate, build, export. Returns
        (export_paths, report, resolved_spec)."""
        assembly, resolved, report = self._resolve_and_build_assembly(spec)
        placed = [{"name": p["id"], "part_type": p["part_type"], "parameters": p["parameters"]}
                  for p in resolved.resolved_spec()["parts"]]
        checks = self._assembly_checks(assembly, placed, check_floating=True)
        export_paths = self._export_assembly(assembly, f"assembly_{uuid.uuid4().hex}", formats,
                                             bom=(checks or {}).get("bom"))
        return export_paths, report, resolved.resolved_spec(), checks

    def _export_assembly(
        self, assembly: "cq.Assembly", base_name: str, formats: list[str], bom: dict | None = None
    ) -> dict[str, Path]:
        """Write `formats` for a built assembly as `<base_name>.<ext>`. Shared by
        first-time generation and by on-demand re-export of a stored job."""
        compound = assembly.toCompound()
        # Per-part shapes (name, world-located shape) for the multi-part
        # DXF/PDF drawing path - see assembly.get_assembly_parts and the
        # `parts` param on exporters.export_all/export_dxf/export_pdf.
        # step/stl/iges still use the flattened `compound` below; only
        # dxf/pdf draw one block per part instead of one for the compound.
        try:
            with log_duration(logger, "assembly_parts"):
                parts = get_assembly_parts(assembly)
        except Exception as exc:  # noqa: BLE001
            raise AssemblyError(f"Failed to enumerate assembly parts: {exc}") from exc

        try:
            with log_duration(logger, "assembly_export", formats=",".join(formats)):
                export_paths: dict[str, Path] = {}
                # STEP for an assembly goes through Assembly.save(), not
                # exporters.export_step(), because that's what preserves
                # named sub-parts in the STEP file rather than flattening
                # to one solid - every other format works on the compound.
                if "step" in formats:
                    step_path = self.output_dir / f"{base_name}.step"
                    # export_step runs Assembly.save() (names kept) and then
                    # checks the file is non-empty and re-imports to the same shape.
                    exporters.export_step(assembly, step_path)
                    export_paths["step"] = step_path
                remaining = [f for f in formats if f != "step"]
                if remaining:
                    export_paths.update(
                        exporters.export_all(
                            compound,
                            self.output_dir,
                            base_name,
                            formats=remaining,
                            part_name="assembly",
                            parts=parts,
                            bom=bom,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(f"Assembly export failed: {exc}") from exc

        return export_paths

    def _stored_assembly_spec(self, job: dict) -> list[dict]:
        """The parts list for a stored assembly job. New jobs carry it in their
        parameters. A job saved before the parts list was stored was always built from
        the fixed example (the description was not read then), so that example is
        what rebuilds it."""
        import json as _json
        stored = None
        try:
            raw = _json.loads(job["parameters"]) if job.get("parameters") else {}
            stored = raw.get("parts") if isinstance(raw, dict) else None
        except ValueError:
            stored = None
        try:
            if stored:
                return normalize_assembly_spec(stored)
            logger.warning("assembly job %s has no stored parts, rebuilding the original fixed example", job.get("id"))
            return normalize_assembly_spec(legacy_assembly_example())
        except ValueError as exc:
            raise AssemblyError(f"The stored assembly cannot be rebuilt: {exc}") from exc

    def _build_workplane(
        self, part_type: str, parameters: dict, warnings: list | None = None
    ) -> cq.Workplane:
        """Build the CadQuery solid for one part from resolved (already-
        validated) parameters. Split out of _generate_single_part so
        export_format_for_job (below) can rebuild the exact same
        geometry from a stored job's params, for the on-demand export
        path - same part_type + same parameters always produces the
        same solid, which is also the property the on-chain design-
        provenance anchor (see easycad's decision log) depends on."""
        if part_type == "imported":
            from imported_solids import build_imported_workplane
            return build_imported_workplane(parameters)
        if part_type not in TEMPLATES:
            raise UnsupportedPartTypeError(f"Unsupported part type: {part_type}")
        template_func = TEMPLATES[part_type]
        try:
            with log_duration(logger, "template_build", part_type=part_type):
                workplane = template_func(parameters)
                if "operations" in parameters:
                    operations = parameters["operations"]
                    if len(operations) > MAX_OPERATIONS_PER_REQUEST:
                        raise GenerationError(
                            f"Too many operations requested ({len(operations)}); "
                            f"max is {MAX_OPERATIONS_PER_REQUEST}."
                        )
                    with log_duration(logger, "apply_operations", part_type=part_type):
                        workplane = self._apply_operations(
                            workplane, operations, warnings=warnings
                        )
        except NitocadError:
            raise
        except Exception as exc:  # noqa: BLE001 - CadQuery/OCCT raises many exception types
            raise GenerationError(f"Failed to build {part_type}: {exc}") from exc
        return workplane

    def export_format_for_job(
        self, job_id: str, fmt: str, anchor: bool = True
    ) -> tuple[Path, str]:
        """See _export_format_for_job. This wrapper says whose attached files the rebuild may read."""
        job = db.get_job(job_id)
        with import_store.owner((job or {}).get("user_id")):
            return self._export_format_for_job(job_id, fmt, anchor)

    def _export_format_for_job(
        self, job_id: str, fmt: str, anchor: bool = True
    ) -> tuple[Path, str]:
        """On-demand export: rebuild geometry from a stored job's params
        and export exactly one format. This is the deferred half of what
        /generate used to do inline for every format on every call - see
        web_app.py's generate_cad, which now only requests ["stl"].

        Assemblies are rebuilt from the parts list stored with the job
        (see _stored_assembly_spec), not from the description text, so
        they come back as the same assembly that was generated.
        """
        job = db.get_job(job_id)
        if job is None:
            raise GenerationError(f"No such job: {job_id}")
        if not job["success"]:
            raise GenerationError(f"Job {job_id} did not complete successfully; nothing to export")

        resolved_formats = exporters.validate_formats([fmt])
        resolved_fmt = resolved_formats[0]
        part_type = job["part_type"]
        if part_type == "assembly" and resolved_fmt == "urdf":
            raise UnsupportedFormatError(
                "A URDF describes one rigid link, so it is not offered for assemblies. "
                "Use STEP, GLB or 3MF for the whole assembly."
            )

        base_name = f"{job_id}_{resolved_fmt}"
        out_path = self.output_dir / f"{base_name}.{exporters._EXTENSION_BY_FORMAT[resolved_fmt]}"
        if out_path.exists():
            # Cheap same-instance cache. Railway's filesystem is ephemeral
            # across redeploys (see storage.py), so this only helps within
            # one running process, not a durability guarantee.
            return out_path, part_type

        if part_type == "imported":
            import json as _json
            stored = _json.loads(job["parameters"]) if job["parameters"] else {}
            if stored.get("kind") in import_ops.MESH_KINDS:
                if resolved_fmt not in ("stl", "glb", "3mf", "urdf"):
                    raise UnsupportedFormatError(
                        "A mesh can only be exported as STL, GLB, 3MF or URDF. STEP, IGES, DXF and PDF need a solid with faces"
                    )
                from imported_solids import build_imported_mesh
                stl_bytes, _stats = build_imported_mesh(stored)
                if resolved_fmt == "stl":
                    out_path.write_bytes(stl_bytes)
                    return out_path, part_type
                import mesh_formats
                try:
                    verts, tris = mesh_formats.read_stl(stl_bytes)
                    label = str(stored.get("filename") or "imported")
                    if resolved_fmt == "glb":
                        mesh_formats.write_glb(out_path, [(label, verts, tris)], flat=True)
                    elif resolved_fmt == "3mf":
                        mesh_formats.write_3mf(out_path, [(label, verts, tris)], title=label)
                    else:
                        mesh_formats.write_urdf_zip(out_path, label.rsplit(".", 1)[0], verts, tris,
                                                    density_g_cm3=None, material=None)
                except mesh_formats.MeshError as exc:
                    raise GenerationError(f"{resolved_fmt.upper()} export failed: {exc}") from exc
                return out_path, part_type

        if part_type == "assembly":
            # Rebuilt from the parts list stored with the job, never from the
            # text again, so the file matches what was generated and anchored.
            import json as _json_a
            stored_params = _json_a.loads(job["parameters"]) if job.get("parameters") else {}
            if isinstance(stored_params, dict) and stored_params.get("assembly"):
                # An interface-driven assembly: re-resolved from its stored, resolved spec.
                assembly, _resolved, _report = self._resolve_and_build_assembly(stored_params["assembly"])
                bom = None
                if resolved_fmt in ("dxf", "pdf"):   # only drawings carry the parts list
                    placed = [{"name": q["id"], "part_type": q["part_type"], "parameters": q["parameters"]}
                              for q in _resolved.resolved_spec()["parts"]]
                    bom = ((self._assembly_checks(assembly, placed, check_floating=True)) or {}).get("bom")
                paths = self._export_assembly(assembly, base_name, [resolved_fmt], bom=bom)
                return paths[resolved_fmt], part_type
            spec = self._stored_assembly_spec(job)
            try:
                assembly = generate_assembly(spec)
            except Exception as exc:  # noqa: BLE001
                raise AssemblyError(f"Assembly construction failed: {exc}") from exc
            bom = None
            if resolved_fmt in ("dxf", "pdf"):
                bom = ((self._assembly_checks(assembly, spec, check_floating=False)) or {}).get("bom")
            paths = self._export_assembly(assembly, base_name, [resolved_fmt], bom=bom)
            return paths[resolved_fmt], part_type

        import json as _json
        parameters = _json.loads(job["parameters"]) if job["parameters"] else {}
        warnings: list[str] = []
        workplane = self._build_workplane(part_type, parameters, warnings=warnings)

        try:
            exporters.export_all(
                workplane, self.output_dir, base_name, formats=[resolved_fmt],
                part_name=part_type, material=job["material"], part_type=part_type,
                drawing_override=self._drawing_override(part_type, parameters),
            )
        except NitocadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(f"{resolved_fmt.upper()} export failed: {exc}") from exc

        # No server-side anchoring here any more: each customer's own
        # wallet signs its anchor tx (see design_registry.py and the
        # /api/jobs/{job_id}/anchor/* routes in web_app.py). The `anchor`
        # argument is kept only so existing callers don't break.

        return out_path, part_type

    def _generate_single_part(
        self, params: Any, validation_result: Any, formats: list[str]
    ) -> dict[str, Path]:
        workplane = self._build_workplane(
            params.part_type, params.parameters, warnings=validation_result.warnings
        )
        base_name = f"{params.part_type}_{uuid.uuid4().hex}"

        try:
            with log_duration(logger, "export_all", part_type=params.part_type, formats=",".join(formats)):
                export_paths = exporters.export_all(
                    workplane,
                    self.output_dir,
                    base_name,
                    formats=formats,
                    part_name=params.part_type,
                    material=getattr(params, "material", None),
                    part_type=params.part_type,
                    drawing_override=self._drawing_override(params.part_type, params.parameters),
                )
        except NitocadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(f"Export failed for {params.part_type}: {exc}") from exc

        return export_paths

    @staticmethod
    def _drawing_override(part_type: str, parameters: dict) -> dict | None:
        """A building's DXF and PDF are its own drawing set (plans, elevations, sections), not generic views."""
        if part_type != "building":
            return None
        from building.drawing_set import write_building_dxf, write_building_pdf

        return {"dxf": lambda path: write_building_dxf(path, parameters),
                "pdf": lambda path: write_building_pdf(path, parameters)}

    def _apply_operations(
        self, workplane: cq.Workplane, operations: list, warnings: list | None = None
    ) -> cq.Workplane:
        """Apply additional CAD operations to workplane."""
        for op in operations:
            op_type = op.get("type")

            if op_type == "fillet":
                radius = op.get("radius", 1.0)
                edges = op.get("edges", "all")
                selector = None if edges == "all" else edges
                workplane = safe_fillet(workplane, radius, selector, warnings=warnings)

            elif op_type == "chamfer":
                size = op.get("size", 1.0)
                edges = op.get("edges", "all")
                selector = None if edges == "all" else edges
                workplane = safe_chamfer(workplane, size, selector, warnings=warnings)

            elif op_type == "shell":
                thickness = op.get("thickness", 2.0)
                open_top = op.get("open_top", True)
                if open_top:
                    workplane = workplane.faces(">Z").shell(-thickness)
                else:
                    workplane = workplane.shell(-thickness)
            else:
                logger.warning("unknown operation type %r ignored", op_type)

        return workplane


# Global instance
generator = CADGenerator()

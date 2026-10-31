"""
Mechanical assembly packager: one call, one deliverable folder.

Vehicle-agnostic. Give it the parts, the parameters and whatever files you have already generated
(STEP, STL, PDF, DXF ...) and it lays them out as

    CAD/  Meshes/  Drawings/  Assembly/  BOM/  Parameters/  Validation/   + manifest.json

Design rules (taken from building/package.py, tightened):
  * It never invents a file. The manifest lists only files that exist on disk, with size and sha256.
  * Every deliverable that was requested but is missing is reported as "missing", not hidden.
  * Cheap, real checks run on what exists (STEP/STL header sanity, duplicate names, empty files).
    Anything it cannot check is "not_run", never "pass".
  * It does not need CadQuery. Producing the geometry is somebody else's job; this only packages it.

Nothing here has been run against real CadQuery output yet. The checks are structural only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
from pathlib import Path
from typing import Any, Iterable

from assembly_checks import bill_of_materials, bom_to_csv

GENERATOR = "NitoCAD assembly packager v1"
FOLDERS = ("CAD", "Meshes", "Drawings", "Assembly", "BOM", "Parameters", "Validation")

_CATEGORY_BY_EXT = {
    ".step": "CAD", ".stp": "CAD", ".iges": "CAD", ".igs": "CAD",
    ".stl": "Meshes", ".3mf": "Meshes", ".glb": "Meshes", ".obj": "Meshes",
    ".pdf": "Drawings", ".dxf": "Drawings", ".svg": "Drawings",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(name: str, status: str, detail: str = "") -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}   # status: pass | fail | not_run


def _stl_ok(path: Path) -> tuple[bool, str]:
    data = path.read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:2000]:
        return (b"endsolid" in data[-200:], "ascii STL" if b"endsolid" in data[-200:] else "ascii STL missing endsolid")
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if len(data) == 84 + 50 * n and n > 0:
            return True, f"binary STL, {n} triangles"
        return False, f"binary STL size does not match its triangle count ({n})"
    return False, "too small to be an STL"


def _step_ok(path: Path) -> tuple[bool, str]:
    head = path.read_bytes()[:64]
    tail = path.read_bytes()[-200:]
    if not head.lstrip().startswith(b"ISO-10303-21"):
        return False, "missing ISO-10303-21 header"
    return (b"END-ISO-10303-21" in tail, "STEP header and trailer present" if b"END-ISO-10303-21" in tail
            else "STEP trailer missing (truncated?)")


def _validate_file(path: Path) -> dict[str, str]:
    rel = path.name
    if path.stat().st_size == 0:
        return _check(f"file:{rel}", "fail", "file is empty")
    ext = path.suffix.lower()
    if ext in (".stl",):
        ok, why = _stl_ok(path)
        return _check(f"file:{rel}", "pass" if ok else "fail", why)
    if ext in (".step", ".stp"):
        ok, why = _step_ok(path)
        return _check(f"file:{rel}", "pass" if ok else "fail", why)
    if ext == ".pdf":
        ok = path.read_bytes()[:5] == b"%PDF-"
        return _check(f"file:{rel}", "pass" if ok else "fail", "PDF header ok" if ok else "not a PDF")
    return _check(f"file:{rel}", "not_run", "no structural check for this format")


def package_assembly(
    name: str,
    parts: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    params: dict[str, Any] | None = None,
    artifacts: Iterable[str | Path | tuple[str, str | Path]] = (),
    requested: Iterable[str] = (),
    volumes: dict[str, float] | None = None,
    extra_checks: Iterable[dict[str, str]] = (),
    limits: Iterable[str] = (),
    sources: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Write the package and return the manifest.

    parts      [{"name", "part_type", "parameters", "material"}] (same shape as the rest of NitoCAD)
    artifacts  files that already exist. A bare path is filed by extension; (folder, path) forces a folder.
    requested  file names the caller expects to deliver. Any that are not in `artifacts` are reported missing.
    extra_checks  results from other validators, e.g. interference, as {"name","status","detail"}.
    limits     plain-text statements of what this package does NOT cover.
    sources    fetched external assets (asset_providers.Registry.fetch results). Their licences and credit
               lines are written to NOTICES.txt and Assembly/sources.json, and reference-only meshes are
               reported as not validated.
    """
    out = Path(out_dir)
    for f in FOLDERS:
        (out / f).mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, str]] = []
    written: list[Path] = []

    # --- parts sanity -------------------------------------------------------------------------
    names = [p.get("name") for p in parts]
    dupes = sorted({n for n in names if names.count(n) > 1})
    checks.append(_check("unique_part_names", "fail" if dupes else "pass",
                         f"duplicate names: {', '.join(map(str, dupes))}" if dupes else f"{len(names)} parts"))
    unnamed = [i for i, n in enumerate(names) if not n]
    if unnamed:
        checks.append(_check("all_parts_named", "fail", f"parts without a name at index {unnamed}"))

    # --- copy artifacts into the folder tree --------------------------------------------------
    delivered_names: set[str] = set()
    for item in artifacts:
        folder, src = (item if isinstance(item, tuple) else (None, item))
        src = Path(src)
        if not src.is_file():
            checks.append(_check(f"artifact:{src.name}", "fail", "listed but the file does not exist"))
            continue
        folder = folder or _CATEGORY_BY_EXT.get(src.suffix.lower(), "Assembly")
        if folder not in FOLDERS:
            checks.append(_check(f"artifact:{src.name}", "fail", f"unknown folder {folder!r}"))
            continue
        dest = out / folder / src.name
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            checks.append(_check(f"artifact:{src.name}", "fail", f"a different file named {src.name} is already in {folder}/"))
            continue
        shutil.copyfile(src, dest)
        written.append(dest)
        delivered_names.add(src.name)
        checks.append(_validate_file(dest))

    missing = sorted(set(requested) - delivered_names)
    for m in missing:
        checks.append(_check(f"requested:{m}", "fail", "requested but not delivered"))

    # --- parameters, BOM, assembly list -------------------------------------------------------
    p_path = out / "Parameters" / "params.json"
    p_path.write_text(json.dumps(params or {}, indent=2, sort_keys=True, default=str), encoding="utf-8")
    written.append(p_path)
    if not params:
        checks.append(_check("parameters_present", "fail", "no parameters supplied; params.json is empty"))

    bom = bill_of_materials(parts, volumes)
    b_path = out / "BOM" / "bom.csv"
    b_path.write_text(bom_to_csv(bom), encoding="utf-8")
    written.append(b_path)
    if not bom["mass_is_complete"]:
        checks.append(_check("mass", "not_run", "volume or material unknown for some parts; total mass is partial"))

    a_path = out / "Assembly" / "parts.json"
    a_path.write_text(json.dumps(parts, indent=1, default=str), encoding="utf-8")
    written.append(a_path)

    sources = list(sources)
    if sources:
        from asset_providers import notices
        s_path = out / "Assembly" / "sources.json"
        s_path.write_text(json.dumps([{**s["record"], "file": s.get("file")} for s in sources], indent=2, default=str),
                          encoding="utf-8")
        n_path = out / "NOTICES.txt"
        n_path.write_text(notices(sources), encoding="utf-8")
        written += [s_path, n_path]
        meshes = [s["record"]["name"] for s in sources if s["record"]["kind"] == "mesh"]
        if meshes:
            checks.append(_check("external_reference_meshes", "not_run",
                                 f"{len(meshes)} reference-only mesh(es) included ({', '.join(meshes)}): "
                                 "not editable, not measured, not in mass or fit checks"))
        unresolved = [s["record"]["name"] for s in sources
                      if str(s["record"].get("attribution_text", "")).startswith("UNRESOLVED")]
        if unresolved:
            checks.append(_check("external_attribution", "fail",
                                 f"credit line unresolved for: {', '.join(unresolved)}; look up the author(s) first"))
        blocked = [s["record"]["name"] for s in sources if s["record"]["licence"] == "UNCONFIRMED"]
        if blocked:
            checks.append(_check("external_licences", "fail", f"unconfirmed licence: {', '.join(blocked)}"))

    checks.extend(extra_checks)

    # --- validation report ---------------------------------------------------------------------
    failed = [c for c in checks if c["status"] == "fail"]
    not_run = [c for c in checks if c["status"] == "not_run"]
    report = {
        "assembly": name,
        "overall": "FAIL" if failed else ("INCOMPLETE" if not_run else "PASS"),
        "counts": {"pass": sum(c["status"] == "pass" for c in checks), "fail": len(failed), "not_run": len(not_run)},
        "checks": checks,
        "limits": list(limits) + ["Structural checks only: geometry validity is not verified here.",
                                  "No motion or clearance simulation is performed by this packager."],
    }
    v_path = out / "Validation" / "validation_report.json"
    v_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    written.append(v_path)

    manifest = {
        "generator": GENERATOR,
        "assembly": name,
        "validation": report["overall"],
        "part_count": len(parts),
        "files": [{"path": str(f.relative_to(out)).replace("\\", "/"), "bytes": f.stat().st_size, "sha256": _sha(f)}
                  for f in sorted(written)],
        "missing": missing,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

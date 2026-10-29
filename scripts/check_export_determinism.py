"""Do identical inputs export to identical bytes?

Run from the repo root in a shell that has CadQuery, ezdxf and reportlab (a Railway shell):

    python scripts/check_export_determinism.py

For STEP, DXF and PDF it exports one plate three ways and compares SHA-256:
  same path, a few seconds later   isolates clocks, dates and random IDs
  a different file name            isolates a file name written into the file
Where two files differ it prints the first lines that changed, which names the cause.
Exit code 1 if anything differs.

The DesignRegistry anchors a hash of the STEP file, so STEP is the one that matters most for provenance.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cadquery as cq  # noqa: E402

import exporters  # noqa: E402

PAUSE_S = 2.1          # long enough that any seconds-resolution timestamp must differ
SHOW_BLOCKS = 6


def build_part() -> cq.Workplane:
    return (
        cq.Workplane("XY").box(60, 40, 8)
        .faces(">Z").workplane()
        .pushPoints([(-20, -10), (20, -10), (-20, 10), (20, 10)])
        .hole(5)
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def first_differences(a: bytes, b: bytes) -> list[str]:
    la = a.decode("latin-1").splitlines()
    lb = b.decode("latin-1").splitlines()
    out: list[str] = []
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<missing>"
        y = lb[i] if i < len(lb) else "<missing>"
        if x != y:
            out.append(f"    line {i + 1}\n      first : {x[:140]}\n      second: {y[:140]}")
            if len(out) >= SHOW_BLOCKS:
                break
    return out


def export_bytes(fmt: str, path: Path, fixed_date: str | None) -> bytes:
    """Export one format to `path`, read it back, and remove it."""
    part = build_part()
    kwargs = {"part_name": "PLATE", "material": "AL6061", "part_type": "flat_plate"}
    if fixed_date is not None:
        kwargs["drawing_date"] = fixed_date
    if fmt == "step":
        exporters.export_step(part, path)
    elif fmt == "dxf":
        exporters.export_dxf(part, path, **kwargs)
    else:
        exporters.export_pdf(part, path, **kwargs)
    data = path.read_bytes()
    path.unlink()
    return data


def compare(label: str, a: bytes, b: bytes) -> bool:
    same = a == b
    print(f"  {label:34}{'IDENTICAL' if same else 'DIFFERENT'}  {sha(a)[:12]}  {sha(b)[:12]}")
    if not same:
        for block in first_differences(a, b):
            print(block)
    return same


def main() -> int:
    code = exporters.export_pdf.__code__
    supports_date = "drawing_date" in code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    fixed = "2026-01-01" if supports_date else None
    print(f"drawing_date parameter present: {'yes' if supports_date else 'no'}")
    ok = True
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for fmt in ("step", "dxf", "pdf"):
            print(fmt.upper())
            first = export_bytes(fmt, work / f"plate.{fmt}", fixed)
            time.sleep(PAUSE_S)
            later = export_bytes(fmt, work / f"plate.{fmt}", fixed)
            renamed = export_bytes(fmt, work / f"other_name.{fmt}", fixed)
            ok &= compare("same path, later", first, later)
            ok &= compare("different file name", first, renamed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

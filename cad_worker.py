"""Runs inside a restricted subprocess (see file_import.py). Usage: cad_worker.py <workdir>
Reads <workdir>/job.json and <workdir>/in.<fmt>, writes out.stl (always), out.step / out.brep (if requested)
and result.json. Error messages written to result.json are shown to users, so they never contain paths."""
import json
import math
import sys
from pathlib import Path


def done(work, ok, **kw):
    (work / "result.json").write_text(json.dumps({"ok": ok, **kw}))
    sys.exit(0 if ok else 2)


def main():
    work = Path(sys.argv[1])
    job = json.loads((work / "job.json").read_text())
    fmt, ops, exports = job["fmt"], job["ops"], job["exports"]
    try:
        import cadquery as cq
    except Exception:
        done(work, False, error="The CAD kernel is not available on this server.")

    src = str(work / f"in.{fmt}")
    try:
        if fmt == "step":
            objs = cq.importers.importStep(src).vals()
            shape = objs[0] if len(objs) == 1 else cq.Compound.makeCompound(objs)
        elif fmt == "iges":
            from OCP.IGESControl import IGESControl_Reader
            from OCP.IFSelect import IFSelect_RetDone
            r = IGESControl_Reader()
            if r.ReadFile(src) != IFSelect_RetDone:
                done(work, False, error="This IGES file could not be read.")
            r.TransferRoots()
            shape = cq.Shape.cast(r.OneShape())
        else:
            from OCP.BRep import BRep_Builder
            from OCP.BRepTools import BRepTools
            from OCP.TopoDS import TopoDS_Shape
            s = TopoDS_Shape()
            if not BRepTools.Read_s(s, src, BRep_Builder()):
                done(work, False, error="This BREP file could not be read.")
            shape = cq.Shape.cast(s)
    except SystemExit:
        raise
    except Exception:
        done(work, False, error=f"This {fmt.upper()} file could not be read. It may be damaged or use unsupported content.")
    if shape is None or shape.isNull() or not shape.Solids() and not shape.Faces():
        done(work, False, error="The file contains no usable geometry.")

    for op in ops:
        try:
            k = op["op"]
            if k == "translate":
                shape = shape.translate(cq.Vector(op["x"], op["y"], op["z"]))
            elif k == "rotate":
                a = cq.Vector(*op["about"])
                d = {"x": cq.Vector(1, 0, 0), "y": cq.Vector(0, 1, 0), "z": cq.Vector(0, 0, 1)}[op["axis"]]
                shape = shape.rotate(a, a + d, op["deg"])
            elif k == "scale":
                shape = shape.scale(op["factor"])
            elif k == "mirror":
                shape = shape.mirror(op["plane"], (0, 0, 0))
            elif k == "center":
                c = shape.BoundingBox().center
                shape = shape.translate(cq.Vector(-c.x, -c.y, -c.z))
            elif k in ("fillet", "chamfer"):
                bb = shape.BoundingBox()
                size = op.get("radius") or op.get("length")
                if size > 0.25 * min(bb.xlen, bb.ylen, bb.zlen or 1e9):
                    done(work, False, error=f"The {k} size is too large for this part (limit: 25% of its smallest dimension).")
                solids = shape.Solids()
                if not solids:
                    done(work, False, error=f"{k.capitalize()} needs a solid; this file only has surfaces.")
                res = [(s.fillet(size, s.Edges()) if k == "fillet" else s.chamfer(size, None, s.Edges())) for s in solids]
                shape = res[0] if len(res) == 1 else cq.Compound.makeCompound(res)
        except SystemExit:
            raise
        except Exception:
            done(work, False, error=f"The '{op.get('op', '?')}' edit failed on this part. Try a smaller value or a different edit.")

    bb = shape.BoundingBox()
    diag = math.sqrt(bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2)
    stats = {"size": [round(bb.xlen, 4), round(bb.ylen, 4), round(bb.zlen, 4)], "min": [round(bb.xmin, 4), round(bb.ymin, 4), round(bb.zmin, 4)],
             "solids": len(shape.Solids()), "faces": len(shape.Faces()), "volume": round(sum(s.Volume() for s in shape.Solids()), 4),
             "area": round(shape.Area(), 4), "valid": bool(shape.isValid())}
    tol = min(0.5, max(0.01, diag * 0.0005))
    wp = cq.Workplane("XY").newObject([shape])
    try:
        cq.exporters.export(wp, str(work / "out.stl"), exportType="STL", tolerance=tol, angularTolerance=0.2)
        if "step" in exports:
            cq.exporters.export(wp, str(work / "out.step"), exportType="STEP")
        if "brep" in exports:
            shape.exportBrep(str(work / "out.brep"))
    except Exception:
        done(work, False, error="The result could not be exported.")
    done(work, True, stats=stats)


if __name__ == "__main__":
    main()

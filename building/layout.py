"""
Deterministic building layout: spec in, rooms / walls / openings / stair out.

Typology (v1): a central corridor along the building width with one band of rooms on each
side. Public rooms (living, dining, kitchen, study) go in the south (front) band on the ground
floor, private rooms (bedrooms, bathrooms, stores) in the north band. With more than one level,
a stair hall sits at the east end of the north band and carries a straight stair.

Everything is computed, nothing is estimated by an LLM. When the program does not fit, the
result is an error that says what to change, never a silently distorted plan.

Coordinates: mm, x east, y north, origin at the outer south-west corner. Walls are described by
centerlines; rooms by their NET (clear) rectangles.
"""
from __future__ import annotations

import math
from typing import Any

from .spec import DOOR_HEIGHT, ENTRANCE_DOOR, ROOMS, normalize_building_spec

TREAD = 250.0
TARGET_RISER = 175.0
MAX_RISER = 190.0
STAIR_WIDTH = 1000.0
BOTTOM_CLEAR = 900.0       # clear floor in front of the first riser
TOP_LANDING = 1000.0       # landing beyond the last riser
EDGE_MARGIN = 150.0        # opening to wall-end clearance
MIN_OPENING_GAP = 100.0
HALL_OPENING = 1200.0
MIN_HALL_DEPTH = STAIR_WIDTH + 100.0 + 1200.0


def stair_geometry(floor_height: float) -> dict[str, float]:
    n = max(2, int(round(floor_height / TARGET_RISER)))
    return {"risers": n, "riser": floor_height / n, "tread": TREAD, "treads": n - 1,
            "run": (n - 1) * TREAD, "width": STAIR_WIDTH}


def _room_id(k: int, n: int) -> str:
    return f"L{k}_R{n}"


def _assign(rooms: list[dict[str, Any]], k: int, hall_area: float) -> tuple[list[dict], list[dict]]:
    """Split a level's rooms between the south and north bands."""
    south: list[dict] = []
    north: list[dict] = []
    if k == 0:
        for r in rooms:
            (south if ROOMS[r["type"]]["band"] == "public" else north).append(r)
        order = {"living": 0, "dining": 1, "kitchen": 2, "study": 3}
        south.sort(key=lambda r: order.get(r["type"], 9))
        # balance the two bands by area: move rooms (never the living room, which holds the entrance, and never
        # the stair hall) from the heavier band to the lighter one while that evens them out
        for _ in range(12):
            area_s = sum(r["area_m2"] for r in south)
            area_n = sum(r["area_m2"] for r in north) + hall_area
            heavy, light = (south, north) if area_s > area_n else (north, south)
            best = None
            for r in heavy:
                if r["type"] == "living":
                    continue
                shift = r["area_m2"] if heavy is south else -r["area_m2"]
                gain = abs(area_n - area_s) - abs((area_n + shift) - (area_s - shift))
                if gain > 1.0 and (best is None or gain > best[0]):
                    best = (gain, r)
            if not best:
                break
            heavy.remove(best[1])
            light.append(best[1])
        order = {"living": 0, "dining": 1, "kitchen": 2, "study": 3}
        south.sort(key=lambda r: order.get(r["type"], 9) if ROOMS[r["type"]]["band"] == "public" else 8)
    else:
        area_s, area_n = 0.0, hall_area
        index = {id(r): n for n, r in enumerate(rooms)}
        for r in sorted(rooms, key=lambda r: -r["area_m2"]):
            if area_s <= area_n:
                south.append(r)
                area_s += r["area_m2"]
            else:
                north.append(r)
                area_n += r["area_m2"]
        south.sort(key=lambda r: index[id(r)])
        north.sort(key=lambda r: index[id(r)])
    return south, north


def design_building(raw: Any) -> dict[str, Any]:
    spec, errors, assumptions = normalize_building_spec(raw)
    if errors or spec is None:
        return {"ok": False, "errors": errors, "warnings": [], "assumptions": assumptions, "model": None}

    errors = []
    warnings: list[str] = []
    W, D = spec["width_mm"], spec["depth_mm"]
    e, i = spec["ext_wall_mm"], spec["int_wall_mm"]
    fh, slab, cw = spec["floor_height_mm"], spec["slab_mm"], spec["corridor_width_mm"]
    n_levels = len(spec["levels"])
    h_w = fh - slab

    X0, X1, Y0, Y1 = e / 2, W - e / 2, e / 2, D - e / 2
    band_cl = ((Y1 - Y0) - cw - i) / 2.0          # centerline depth of each band
    yc0 = Y0 + band_cl                             # corridor south wall centerline
    yc1 = yc0 + cw + i                             # corridor north wall centerline
    clear_depth = band_cl - e / 2 - i / 2
    if clear_depth < 2200:
        errors.append(f"The depth {D:g} mm leaves rooms only {clear_depth:.0f} mm deep; "
                      f"increase the depth or reduce the corridor (needs at least ~"
                      f"{2 * (2200 + e / 2 + i / 2) + cw + i + e:.0f} mm).")

    stair = stair_geometry(fh)
    if stair["riser"] > MAX_RISER:
        errors.append(f"Riser {stair['riser']:.0f} mm exceeds {MAX_RISER:.0f} mm for floor height {fh:g} mm.")
    hall_clear_x = stair["run"] + BOTTOM_CLEAR + TOP_LANDING
    xh = X1 - e / 2 - i / 2 - hall_clear_x       # hall west partition centerline
    hall_net_x0, hall_net_x1 = xh + i / 2, X1 - e / 2
    stair_yb = (Y1 - e / 2) - 100.0
    stair_ya = stair_yb - STAIR_WIDTH
    if n_levels > 1 and clear_depth < MIN_HALL_DEPTH:
        errors.append(f"The stair hall needs {MIN_HALL_DEPTH:.0f} mm of clear depth; only {clear_depth:.0f} mm available.")

    g = {"W": W, "D": D, "e": e, "i": i, "fh": fh, "slab": slab, "h_w": h_w, "cw": cw,
         "X0": X0, "X1": X1, "Y0": Y0, "Y1": Y1, "yc0": yc0, "yc1": yc1, "clear_depth": clear_depth,
         "xh": xh}
    levels: list[dict[str, Any]] = []

    for k, lv in enumerate(spec["levels"]):
        if errors:
            break
        rooms_in = [dict(r) for r in lv["rooms"]]
        hall_area = hall_clear_x * clear_depth / 1e6 if n_levels > 1 else 0.0
        south, north = _assign(rooms_in, k, hall_area)
        placed: list[dict[str, Any]] = []
        walls: list[dict[str, Any]] = [
            {"id": "ext_s", "kind": "ext", "orient": "x", "a": (0.0, Y0), "b": (W, Y0), "t": e},
            {"id": "ext_n", "kind": "ext", "orient": "x", "a": (0.0, Y1), "b": (W, Y1), "t": e},
            {"id": "ext_w", "kind": "ext", "orient": "y", "a": (X0, e), "b": (X0, D - e), "t": e},
            {"id": "ext_e", "kind": "ext", "orient": "y", "a": (X1, e), "b": (X1, D - e), "t": e},
            {"id": "cor_s", "kind": "int", "orient": "x", "a": (e, yc0), "b": (W - e, yc0), "t": i},
            {"id": "cor_n", "kind": "int", "orient": "x", "a": (e, yc1), "b": (W - e, yc1), "t": i},
        ]
        wall_by_id = {w["id"]: w for w in walls}
        for w in walls:
            w["openings"] = []
        openings: list[dict[str, Any]] = []
        counter = {"room": 0, "open": 0}

        def add_opening(wall_id: str, kind: str, room_id: str, center: float, width: float, height: float,
                        sill: float, into: tuple[float, float] | None, hinge: str = "start") -> None:
            wall = wall_by_id[wall_id]
            origin = wall["a"][0] if wall["orient"] == "x" else wall["a"][1]
            counter["open"] += 1
            op = {"id": f"L{k}_O{counter['open']}", "level": k, "wall": wall_id, "kind": kind, "room": room_id,
                  "width": width, "height": height, "sill": sill,
                  "offset": center - width / 2 - origin, "center_along": center,
                  "swing": {"into": into, "hinge": hinge} if into else None}
            wall["openings"].append(op)
            openings.append(op)

        def band(rooms: list[dict], which: str) -> None:
            has_hall = which == "north" and n_levels > 1
            if which == "south":
                ylo, yhi, y0n, y1n = Y0, yc0, Y0 + e / 2, yc0 - i / 2
            else:
                ylo, yhi, y0n, y1n = yc1, Y1, yc1 + i / 2, Y1 - e / 2
            span_end = xh if has_hall else X1
            avail = span_end - X0
            if not rooms:
                if avail > 1 and not has_hall:
                    errors.append(f"Level {k}: the {which} band has no rooms to fill {avail / 1000:.1f} m; add rooms.")
                return
            cd = y1n - y0n
            # width from the requested area, but never narrower than the room's minimum dimension or
            # a 1:4 proportion against the band depth (a 2.5 m2 toilet is not a 0.6 m x 4 m slot)
            desired = [max(r["area_m2"] * 1e6 / cd, ROOMS[r["type"]]["min_dim"], cd / 4.0) + i for r in rooms]
            s = avail / sum(desired)
            if s > 1.5:
                why = ", because the floor with the most rooms sets the footprint size" if n_levels > 1 else ""
                warnings.append(f"Level {k}: the {which} rooms come out {100 * (s - 1):.0f}% larger than requested "
                                f"(the footprint is generous for this program{why}).")
            bounds = [X0]
            for d in desired:
                bounds.append(bounds[-1] + d * s)
            bounds[-1] = span_end
            for j, r in enumerate(rooms):
                bl, br = bounds[j], bounds[j + 1]
                tl = e if j == 0 else i
                tr = i if (j < len(rooms) - 1 or has_hall) else e
                x0n, x1n = bl + tl / 2, br - tr / 2
                spec_r = ROOMS[r["type"]]
                w_net, d_net = x1n - x0n, y1n - y0n
                area = w_net * d_net / 1e6
                if min(w_net, d_net) < spec_r["min_dim"] or area < spec_r["min_area"]:
                    errors.append(f"Level {k}: {r['name']} would be {w_net / 1000:.2f} x {d_net / 1000:.2f} m "
                                  f"({area:.1f} m2); minimum {spec_r['min_dim'] / 1000:.1f} m wide and "
                                  f"{spec_r['min_area']:g} m2. Remove a room or enlarge the footprint.")
                elif area < 0.85 * r["area_m2"]:
                    warnings.append(f"Level {k}: {r['name']} is {area:.1f} m2, {100 * (1 - area / r['area_m2']):.0f}% "
                                    f"under the requested {r['area_m2']:g} m2.")
                counter["room"] += 1
                rid = _room_id(k, counter["room"])
                placed.append({"id": rid, "name": r["name"], "type": r["type"], "band": which, "net": [x0n, y0n, x1n, y1n],
                               "area_m2": round(area, 2), "requested_m2": r["area_m2"], "level": k})
                if j > 0:
                    walls.append({"id": f"part_{which[0]}_{j}", "kind": "int", "orient": "y", "a": (bl, ylo), "b": (bl, yhi),
                                  "t": i, "openings": []})
                # door to the corridor
                cor_wall = "cor_s" if which == "south" else "cor_n"
                dw = spec_r["door"]
                lo, hi = x0n + EDGE_MARGIN + dw / 2, x1n - EDGE_MARGIN - dw / 2
                if lo > hi:
                    errors.append(f"Level {k}: {r['name']} is too narrow for a {dw} mm door.")
                else:
                    c = min(max((x0n + x1n) / 2, lo), hi)
                    into = (0.0, -1.0) if which == "south" else (0.0, 1.0)
                    add_opening(cor_wall, "door", rid, c, dw, DOOR_HEIGHT, 0.0, into)
                # windows
                ww, wh, ws = spec_r["win"]
                ext_wall = "ext_s" if which == "south" else "ext_n"
                ww_fit = min(ww, w_net - 2 * (EDGE_MARGIN + 100))
                if ww_fit >= 600:
                    add_opening(ext_wall, "window", rid, (x0n + x1n) / 2, ww_fit, wh, ws, None)
                else:
                    warnings.append(f"Level {k}: {r['name']} has no room for a window on its front wall.")
                if spec_r["habitable"] or r["type"] in ("kitchen",):
                    pass
                if spec_r["habitable"]:
                    if j == 0:
                        yc = (y0n + y1n) / 2
                        gw = min(ww, d_net - 2 * (EDGE_MARGIN + 100))
                        if gw >= 600:
                            add_opening("ext_w", "window", rid, yc, gw, wh, ws, None)
                    if j == len(rooms) - 1 and not has_hall:
                        yc = (y0n + y1n) / 2
                        gw = min(ww, d_net - 2 * (EDGE_MARGIN + 100))
                        if gw >= 600:
                            add_opening("ext_e", "window", rid, yc, gw, wh, ws, None)
                if k == 0 and which == "south" and r["type"] == "living" and not any(
                        o["kind"] == "door" and o["wall"] == "ext_s" for o in openings):
                    ew, eh = ENTRANCE_DOOR
                    # entrance door beside the window, in the part of the front wall left clear
                    wins = [o for o in openings if o["wall"] == "ext_s" and o["room"] == rid]
                    lo_e, hi_e = x0n + EDGE_MARGIN + ew / 2, x1n - EDGE_MARGIN - ew / 2
                    if wins:
                        wmin = wins[0]["center_along"] - wins[0]["width"] / 2
                        wmax = wins[0]["center_along"] + wins[0]["width"] / 2
                        if wmin - (x0n + EDGE_MARGIN) >= ew + MIN_OPENING_GAP:
                            c_e = (x0n + EDGE_MARGIN + wmin - MIN_OPENING_GAP) / 2
                        elif (x1n - EDGE_MARGIN) - wmax >= ew + MIN_OPENING_GAP:
                            c_e = (wmax + MIN_OPENING_GAP + x1n - EDGE_MARGIN) / 2
                        else:
                            # shrink the window to make room for the entrance
                            wins[0]["width"] = max(600.0, wins[0]["width"] - (ew + MIN_OPENING_GAP))
                            wins[0]["center_along"] = x1n - EDGE_MARGIN - wins[0]["width"] / 2 - 100
                            wins[0]["offset"] = wins[0]["center_along"] - wins[0]["width"] / 2 - wall_by_id["ext_s"]["a"][0]
                            c_e = x0n + EDGE_MARGIN + ew / 2 + 100
                    else:
                        c_e = min(max((x0n + x1n) / 2, lo_e), hi_e)
                    add_opening("ext_s", "door", rid, c_e, ew, eh, 0.0, (0.0, 1.0))

        band(south, "south")
        band(north, "north")

        stair_info = None
        if n_levels > 1:
            counter["room"] += 1
            hid = _room_id(k, counter["room"])
            hall = {"id": hid, "name": "Stair hall", "type": "stair_hall", "band": "north",
                    "net": [hall_net_x0, yc1 + i / 2, hall_net_x1, Y1 - e / 2],
                    "area_m2": round((hall_net_x1 - hall_net_x0) * clear_depth / 1e6, 2),
                    "requested_m2": hall_area, "level": k}
            placed.append(hall)
            walls.append({"id": "part_n_hall", "kind": "int", "orient": "y", "a": (xh, yc1), "b": (xh, Y1), "t": i,
                          "openings": []})
            add_opening("cor_n", "opening", hid, hall_net_x0 + 100 + HALL_OPENING / 2, HALL_OPENING, DOOR_HEIGHT, 0.0, None)
            gw = min(900.0, (stair_ya - 100) - (yc1 + i / 2) - 2 * EDGE_MARGIN)
            if gw >= 600:
                add_opening("ext_e", "window", hid, ((yc1 + i / 2) + (stair_ya - 100)) / 2, gw, 1200.0, 900.0, None)
            if k < n_levels - 1:
                stair_info = {"level": k, "x0": hall_net_x0 + BOTTOM_CLEAR,
                              "x1": hall_net_x0 + BOTTOM_CLEAR + stair["run"], "y0": stair_ya, "y1": stair_yb,
                              "risers": stair["risers"], "riser": stair["riser"], "tread": stair["tread"],
                              "width": stair["width"], "z0": k * fh, "z1": (k + 1) * fh}

        # openings must not touch or overlap on the same wall
        for w in walls:
            ops = sorted(w.get("openings", []), key=lambda o: o["offset"])
            for a, b in zip(ops, ops[1:]):
                if b["offset"] - (a["offset"] + a["width"]) < MIN_OPENING_GAP:
                    errors.append(f"Level {k}: openings on wall {w['id']} collide ({a['id']} and {b['id']}); "
                                  f"enlarge the rooms.")
        # daylight rule of thumb (NOT a code check)
        for r in placed:
            if ROOMS.get(r["type"], {}).get("habitable"):
                glaz = sum(o["width"] * o["height"] for o in openings if o["room"] == r["id"] and o["kind"] == "window") / 1e6
                if glaz < 0.10 * r["area_m2"]:
                    warnings.append(f"Level {k}: {r['name']} glazing is {100 * glaz / r['area_m2']:.0f}% of floor area "
                                    f"(rule of thumb: at least 10%; not a code check).")
        levels.append({"index": k, "elevation": k * fh, "rooms": placed, "walls": walls, "openings": openings,
                       "stair": stair_info})

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "assumptions": assumptions, "model": None}

    model = {"spec": spec, "geometry": g, "stair": stair, "levels": levels, "roof": spec["roof"],
             "compliance": {"building_code": "UNKNOWN",
                            "note": "No building-code data is loaded, so compliance is not assessed. "
                                    "Concept design only: structure, services and approvals need qualified professionals."}}
    return {"ok": True, "errors": [], "warnings": warnings, "assumptions": assumptions, "model": model}

"""
Furniture and fixtures: parametric library plus deterministic placement.

Items are placed against walls by rules (bed head on the far wall, wardrobe on a side wall, kitchen run
along the longest free wall, WC and basin in wet rooms, ...), never inside a door swing, never
overlapping each other, always inside the room's net rectangle. A piece that cannot be placed produces a
warning instead of a silently overlapping symbol.
"""
from __future__ import annotations

from typing import Any

# kind: (width_along_wall, depth, height, display name, fixture?)
LIB: dict[str, tuple[float, float, float, str, bool]] = {
    "bed_king": (1800, 2000, 450, "King bed", False), "bed_double": (1500, 2000, 450, "Double bed", False),
    "bed_single": (1000, 2000, 450, "Single bed", False), "wardrobe": (1800, 600, 2000, "Wardrobe", False),
    "bedside": (450, 400, 500, "Bedside table", False), "sofa": (2100, 900, 800, "Sofa", False),
    "armchair": (850, 850, 800, "Armchair", False), "coffee_table": (1100, 600, 420, "Coffee table", False),
    "tv_unit": (1600, 400, 500, "TV unit", False), "dining_table": (1800, 900, 750, "Dining table", False),
    "dining_chair": (450, 450, 450, "Dining chair", False), "kitchen_run": (3000, 600, 900, "Kitchen units", True),
    "fridge": (700, 700, 1800, "Refrigerator", False), "wc": (400, 700, 400, "WC", True),
    "basin": (500, 400, 850, "Wash basin", True), "shower": (900, 900, 60, "Shower tray", True),
    "desk": (1400, 700, 750, "Desk", False), "chair": (500, 500, 450, "Chair", False),
    "bookshelf": (900, 300, 1800, "Bookshelf", False), "shelf": (1800, 400, 1800, "Storage shelving", False),
}
Rect = tuple[float, float, float, float]


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1


def _inside(a: Rect, room: Rect) -> bool:
    return a[0] >= room[0] - 1 and a[1] >= room[1] - 1 and a[2] <= room[2] + 1 and a[3] <= room[3] + 1


class _Room:
    def __init__(self, level: int, room: dict[str, Any], door_zones: list[Rect], door_wall: str):
        self.level, self.room, self.rect = level, room, tuple(room["net"])
        self.obstacles: list[Rect] = list(door_zones)
        self.door_wall = door_wall
        self.far_wall = {"N": "S", "S": "N", "E": "W", "W": "E"}[door_wall]
        self.items: list[dict[str, Any]] = []

    @property
    def w(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def d(self) -> float:
        return self.rect[3] - self.rect[1]

    def wall_len(self, wall: str) -> float:
        return self.w if wall in "NS" else self.d

    def rect_for(self, wall: str, pos: float, w: float, d: float) -> Rect:
        x0, y0, x1, y1 = self.rect
        if wall == "S":
            return (pos, y0, pos + w, y0 + d)
        if wall == "N":
            return (pos, y1 - d, pos + w, y1)
        if wall == "W":
            return (x0, pos, x0 + d, pos + w)
        return (x1 - d, pos, x1, pos + w)

    def positions(self, wall: str, w: float, mode: str) -> list[float]:
        lo = self.rect[0] if wall in "NS" else self.rect[1]
        hi = (self.rect[2] if wall in "NS" else self.rect[3]) - w
        if hi < lo - 1:
            return []
        mid = (lo + hi) / 2
        scan = [lo + 100 * i for i in range(int((hi - lo) // 100) + 1)] + [hi]
        order = {"center": [mid] + scan, "start": scan, "end": scan[::-1]}[mode]
        return order

    def free(self, r: Rect) -> bool:
        return _inside(r, self.rect) and not any(_overlap(r, o) for o in self.obstacles)

    def add(self, kind: str, r: Rect, head: str | None = None) -> dict[str, Any]:
        w, d, h, name, fix = LIB[kind]
        it = {"level": self.level, "room_id": self.room["id"], "room": self.room["name"], "kind": kind, "name": name,
              "x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3], "height": h, "fixture": fix, "head": head, "symbol": []}
        if head and kind.startswith("bed"):
            if head in "NS":
                y = r[1] + 350 if head == "S" else r[3] - 350
                it["symbol"] = [((r[0], y), (r[2], y))]
            else:
                x = r[0] + 350 if head == "W" else r[2] - 350
                it["symbol"] = [((x, r[1]), (x, r[3]))]
        if kind == "sofa" and head:
            if head in "NS":
                y = r[1] + 250 if head == "S" else r[3] - 250
                it["symbol"] = [((r[0], y), (r[2], y))]
            else:
                x = r[0] + 250 if head == "W" else r[2] - 250
                it["symbol"] = [((x, r[1]), (x, r[3]))]
        self.items.append(it)
        self.obstacles.append(r)
        return it

    def place(self, kind: str, walls: list[str], w: float | None = None, d: float | None = None,
              modes: tuple[str, ...] = ("center", "start", "end"), head: bool = False) -> dict[str, Any] | None:
        lw, ld = LIB[kind][0], LIB[kind][1]
        w, d = w or lw, d or ld
        for wall in walls:
            for mode in modes:
                for pos in self.positions(wall, w, mode):
                    r = self.rect_for(wall, pos, w, d)
                    if self.free(r):
                        return self.add(kind, r, wall if head else None)
        return None


def _door_wall_of(room: dict[str, Any]) -> str:
    return "N" if room["band"] == "south" else "S"


def _door_zones(model: dict[str, Any], level: int, room: dict[str, Any]) -> list[Rect]:
    lv = model["levels"][level]
    walls = {w["id"]: w for w in lv["walls"]}
    zones: list[Rect] = []
    for o in lv["openings"]:
        if o["room"] != room["id"] or o["kind"] not in ("door", "opening") or not o["swing"]:
            continue
        w = walls[o["wall"]]
        d0 = w["a"][0] + o["offset"]
        yw = w["a"][1]
        into = o["swing"]["into"][1]
        y0, y1 = (yw, yw + o["width"]) if into > 0 else (yw - o["width"], yw)
        zones.append((d0 - 20, y0, d0 + o["width"] + 20, y1))
    return zones


def _bedsides(r: _Room, bed: dict[str, Any], n: int) -> None:
    head = bed["head"]
    if head is None:
        return
    w, d = LIB["bedside"][0], LIB["bedside"][1]
    bx0, by0, bx1, by1 = bed["x0"], bed["y0"], bed["x1"], bed["y1"]
    if head == "S":
        cands = [(bx0 - w, by0, bx0, by0 + d), (bx1, by0, bx1 + w, by0 + d)]
    elif head == "N":
        cands = [(bx0 - w, by1 - d, bx0, by1), (bx1, by1 - d, bx1 + w, by1)]
    elif head == "W":
        cands = [(bx0, by0 - w, bx0 + d, by0), (bx0, by1, bx0 + d, by1 + w)]
    else:
        cands = [(bx1 - d, by0 - w, bx1, by0), (bx1 - d, by1, bx1, by1 + w)]
    placed = 0
    for c in cands:
        if placed < n and r.free(c):
            r.add("bedside", c)
            placed += 1


def _furnish_room(r: _Room, warnings: list[str]) -> None:
    t = r.room["type"]
    walls_side = ["E", "W"]
    far = r.far_wall

    def need(kind: str, it: dict[str, Any] | None) -> None:
        if it is None:
            warnings.append(f"Level {r.level}: {LIB[kind][3]} does not fit in {r.room['name']}.")

    if t in ("master_bedroom", "bedroom"):
        bed_kind = "bed_king" if t == "master_bedroom" else ("bed_double" if min(r.w, r.d) >= 2900 else "bed_single")
        bed = r.place(bed_kind, [far] + walls_side, head=True)
        if bed is None:
            w, d = LIB[bed_kind][1], LIB[bed_kind][0]       # lengthwise along the wall
            bed = r.place(bed_kind, [far, "E", "W"], w=w, d=d)
        need(bed_kind, bed)
        sizes = [2400, 1800, 1500, 1200, 900] if t == "master_bedroom" else [1800, 1500, 1200, 900]
        wd = None
        for s in sizes:
            wd = r.place("wardrobe", walls_side + [far], w=s)
            if wd:
                break
        need("wardrobe", wd)
        if bed:
            _bedsides(r, bed, 2 if t == "master_bedroom" else 1)
    elif t == "living":
        sofa = r.place("sofa", [far] + walls_side, head=True)
        need("sofa", sofa)
        tv = r.place("tv_unit", [r.door_wall] + walls_side)
        if sofa and sofa["head"] in ("N", "S"):
            cx = (sofa["x0"] + sofa["x1"]) / 2
            front = sofa["y1"] + 400 if sofa["head"] == "S" else sofa["y0"] - 400 - 600
            ct = (cx - 550, front, cx + 550, front + 600)
            if r.free(ct):
                r.add("coffee_table", ct)
        for wall in ("W", "E"):
            r.place("armchair", [wall], modes=("center",))
    elif t == "dining":
        tw, td = (1800, 900) if r.room["area_m2"] >= 11 else (1400, 800)
        placed = None
        shifts = sorted(((sx, sy) for sx in range(-900, 901, 150) for sy in range(-900, 901, 150)),
                        key=lambda t: (abs(t[0]) + abs(t[1]), t))
        for sx, sy in shifts:
            cx, cy = (r.rect[0] + r.rect[2]) / 2 + sx, (r.rect[1] + r.rect[3]) / 2 + sy
            tb = (cx - tw / 2, cy - td / 2, cx + tw / 2, cy + td / 2)
            ring = (tb[0] - 30, tb[1] - 500, tb[2] + 30, tb[3] + 500)
            if r.free(tb) and _inside(ring, r.rect) and not any(_overlap(ring, o) for o in r.obstacles):
                placed = tb
                break
        if placed is None:
            warnings.append(f"Level {r.level}: the dining table and chairs do not fit in {r.room['name']}.")
        else:
            tb = r.add("dining_table", placed)
            per_side = 3 if tw >= 1800 else 2
            step = tw / per_side
            for i in range(per_side):
                x0 = placed[0] + i * step + (step - 450) / 2
                r.add("dining_chair", (x0, placed[1] - 500, x0 + 450, placed[1] - 50))
                r.add("dining_chair", (x0, placed[3] + 50, x0 + 450, placed[3] + 500))
    elif t == "kitchen":
        order = sorted(["S", "N", "E", "W"], key=lambda w: (w == r.door_wall, -r.wall_len(w)))
        run = None
        for wall in order:
            length = min(r.wall_len(wall) - 800, 3600)
            length = int(length // 100 * 100)
            if length >= 1800:
                run = r.place("kitchen_run", [wall], w=float(length), modes=("start", "end"))
                if run:
                    fr = (run["x1"], run["y0"], run["x1"] + 700, run["y0"] + 700) if wall in "NS" else None
                    if wall in "NS":
                        yy = run["y0"] if wall == "S" else run["y1"] - 700
                        fr = (run["x1"], yy, run["x1"] + 700, yy + 700)
                        if not r.free(fr):
                            fr = (run["x0"] - 700, yy, run["x0"], yy + 700)
                    else:
                        xx = run["x0"] if wall == "W" else run["x1"] - 700
                        fr = (xx, run["y1"], xx + 700, run["y1"] + 700)
                        if not r.free(fr):
                            fr = (xx, run["y0"] - 700, xx + 700, run["y0"])
                    if r.free(fr):
                        r.add("fridge", fr)
                    else:
                        warnings.append(f"Level {r.level}: the refrigerator does not fit in {r.room['name']}.")
                    break
        need("kitchen_run", run)
    elif t == "bathroom":
        sh = r.place("shower", [far], modes=("start", "end"))
        need("shower", sh)
        need("wc", r.place("wc", [far] + walls_side, modes=("center", "start", "end")))
        need("basin", r.place("basin", walls_side + [r.door_wall, far], modes=("center", "start", "end")))
    elif t == "toilet":
        need("wc", r.place("wc", [far] + walls_side, modes=("center", "start", "end")))
        need("basin", r.place("basin", walls_side + [r.door_wall, far], modes=("center", "start", "end")))
    elif t == "study":
        desk = r.place("desk", [far] + walls_side)
        need("desk", desk)
        if desk and far in "NS":
            ch = (desk["x0"] + 450, desk["y1"] + 100, desk["x0"] + 950, desk["y1"] + 600) if far == "S" else \
                 (desk["x0"] + 450, desk["y0"] - 600, desk["x0"] + 950, desk["y0"] - 100)
            if r.free(ch):
                r.add("chair", ch)
        r.place("bookshelf", walls_side + [r.door_wall])
    elif t == "store":
        for wall in sorted(["E", "W", "N", "S"], key=lambda w: (w == r.door_wall, -r.wall_len(w)))[:2]:
            length = float(int(min(r.wall_len(wall) - 200, 1800) // 100 * 100))
            if length >= 600:
                r.place("shelf", [wall], w=length, modes=("start", "end"))


def place_furniture(model: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for lv in model["levels"]:
        for room in lv["rooms"]:
            if room["type"] == "stair_hall":
                continue
            ctx = _Room(lv["index"], room, _door_zones(model, lv["index"], room), _door_wall_of(room))
            _furnish_room(ctx, warnings)
            items.extend(ctx.items)
    return items, warnings


def furniture_schedule(items: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    fixtures: dict[str, int] = {}
    by_room: dict[str, dict[str, int]] = {}
    for it in items:
        totals[it["name"]] = totals.get(it["name"], 0) + 1
        by_room.setdefault(f"L{it['level']} {it['room']}", {})
        by_room[f"L{it['level']} {it['room']}"][it["name"]] = by_room[f"L{it['level']} {it['room']}"].get(it["name"], 0) + 1
        if it["fixture"]:
            fixtures[it["name"]] = fixtures.get(it["name"], 0) + 1
    return {"totals": dict(sorted(totals.items())), "plumbing_fixtures": dict(sorted(fixtures.items())), "by_room": by_room}


def furniture_parts(items: list[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    """One feature-tree part per item (each is its own connected solid)."""
    fh = model["geometry"]["fh"]
    parts = []
    for n, it in enumerate(items):
        w, d = it["x1"] - it["x0"], it["y1"] - it["y0"]
        feat = {"id": "item", "op": "extrude", "plane": "XY", "offset": it["level"] * fh,
                "profile": {"type": "rect", "width": w, "height": d, "center": [it["x0"] + w / 2, it["y0"] + d / 2]},
                "distance": it["height"]}
        parts.append({"name": f"furn_l{it['level']}_{n}", "part_type": "feature_tree",
                      "parameters": {"features": [feat]}, "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "mate": None})
    return parts

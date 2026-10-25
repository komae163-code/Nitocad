"""
Two-layer grid router (A*), pure Python.

Not a general-purpose autorouter - it is sized for the boards our pre-designed
blocks produce (tens of parts). Guarantees we rely on:
  * a net is only reported routed if every pad ended up connected;
  * every other-net pad/track/via is kept >= clearance away (checked again
    independently by drc_lite.py, and finally by KiCad's own DRC);
  * GND is handled as a bottom-layer copper pour when plane mode is on:
    SMD ground pads just get a via down to the pour.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict

from .model import RULES, Design, Track, Via, abs_pads

PITCH = 0.1
MARGIN = 0.02            # extra clearance beyond the rule, absorbs rounding
VIA_COST = 8.0
TURN_COST = 0.4
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))   # 0,1 horizontal; 2,3 vertical
LAYERS = ("F.Cu", "B.Cu")
NECK_MM = 1.6           # wide nets drop to the default width this close to their own pads


def _box_any(a, r):
    """True wherever any True lies within Chebyshev distance r (separable box dilation)."""
    import numpy as np
    out = a
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(r, r) if ax == axis else (0, 0) for ax in (0, 1)]
        cs = np.insert(np.cumsum(np.pad(out.astype(np.int32), pad), axis=axis), 0, 0, axis=axis)
        hi = np.take(cs, np.arange(2 * r + 1, n + 2 * r + 1), axis=axis)
        lo = np.take(cs, np.arange(0, n), axis=axis)
        out = (hi - lo) > 0
    return out


class Router:
    def __init__(self, design: Design, plane: bool, pitch: float = PITCH):
        self.d = design
        self.P = pitch
        self.plane = plane
        self.hw = RULES["track_width"] / 2
        self.infl = RULES["clearance"] + self.hw + MARGIN
        x0, y0, x1, y1 = design.board
        self.ox, self.oy = x0, y0
        self.nx = int((x1 - x0) / pitch) + 1
        self.ny = int((y1 - y0) / pitch) + 1
        ek = RULES["edge_clearance"] + self.hw + MARGIN
        self.ex0, self.ey0, self.ex1, self.ey1 = x0 + ek, y0 + ek, x1 - ek, y1 - ek
        self.blocked = [defaultdict(set), defaultdict(set)]
        self.net_id = {n: i for i, n in enumerate(design.nets)}
        self.tree = defaultdict(set)
        self.tracks: list[Track] = []
        self.vias: list[Via] = []
        self.pads_by_net = defaultdict(list)
        self.net_name = {i: n for n, i in self.net_id.items()}
        self.widths: dict[int, float] = {}
        self._wide: dict[int, tuple] = {}
        for net, w in (getattr(design, "net_width", None) or {}).items():
            if w > RULES["track_width"] + 1e-9 and net in self.net_id and not (net == "GND" and plane):
                self.widths[self.net_id[net]] = w
        self._register_pads()

    # ---------------------------------------------------------- geometry
    def xy(self, ix, iy):
        return self.ox + ix * self.P, self.oy + iy * self.P

    def _cell_range(self, lo, hi, origin):
        return (math.ceil((lo - origin) / self.P - 1e-9), math.floor((hi - origin) / self.P + 1e-9))

    def _mark(self, layer, x0, y0, x1, y1, owner, infl):
        a0, a1 = self._cell_range(x0 - infl, x1 + infl, self.ox)
        b0, b1 = self._cell_range(y0 - infl, y1 + infl, self.oy)
        blk = self.blocked[layer]
        for ix in range(max(a0, 0), min(a1, self.nx - 1) + 1):
            for iy in range(max(b0, 0), min(b1, self.ny - 1) + 1):
                blk[(ix, iy)].add(owner)

    def _register_pads(self):
        uniq = 10_000
        for c in self.d.components:
            for pad, cx, cy, w, h in abs_pads(c):
                net = c.pin_nets.get(pad.number)
                if pad.kind == "np":
                    for li in (0, 1):
                        self._mark(li, cx, cy, cx, cy, -1, pad.drill / 2 + 0.25 + self.hw)
                    continue
                if net is None:
                    uniq += 1
                    owner = uniq
                else:
                    owner = self.net_id[net]
                    self.pads_by_net[net].append((c, pad, cx, cy, w, h))
                layers = (0, 1) if pad.kind == "thru" else (0 if "F.Cu" in pad.layers else 1,)
                for li in layers:
                    self._mark(li, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, owner, self.infl)

    def _blocked_for(self, nid, li, ix, iy):
        s = self.blocked[li].get((ix, iy))
        if not s:
            return False
        if -1 in s:
            return True
        return len(s) > 1 or nid not in s

    def _free(self, nid, li, ix, iy):
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            return False
        x, y = self.xy(ix, iy)
        if x < self.ex0 or x > self.ex1 or y < self.ey0 or y > self.ey1:
            return False
        return not self._blocked_for(nid, li, ix, iy)

    def _via_ok(self, nid, ix, iy):
        r = math.ceil((RULES["via_dia"] / 2 - self.hw) / self.P)
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for li in (0, 1):
                    if not self._free(nid, li, ix + dx, iy + dy):
                        return False
        return True

    # ---------------------------------------------------------- wide nets
    def _wide_maps(self, nid):
        """Per-net keep-out for a wide net's CENTRE-LINE: the base keep-out already includes
        default-half-width + clearance, so dilate other nets' cells by the extra half-width.
        Cells near the net's own pads are exempt (the track necks down to the default width
        there - see _commit), so fine-pitch pins stay reachable."""
        if nid in self._wide:
            return self._wide[nid]
        import numpy as np
        w = self.widths[nid]
        extra = (w - RULES["track_width"]) / 2
        ext = int(math.ceil((extra + 1e-9) / self.P))
        occ = []
        for li in (0, 1):
            a = np.zeros((self.nx, self.ny), dtype=bool)
            for (ix, iy), owners in self.blocked[li].items():
                if (-1 in owners) or len(owners) > 1 or nid not in owners:
                    a[ix, iy] = True
            a = _box_any(a, ext)
            xs = self.ox + np.arange(self.nx) * self.P
            ys = self.oy + np.arange(self.ny) * self.P
            a |= ((xs < self.ex0 + extra) | (xs > self.ex1 - extra))[:, None]
            a |= ((ys < self.ey0 + extra) | (ys > self.ey1 - extra))[None, :]
            occ.append(a)
        neck = np.zeros((self.nx, self.ny), dtype=bool)
        for (_c, _pad, cx, cy, pw, ph) in self.pads_by_net.get(self.net_name[nid], []):
            a0, a1 = self._cell_range(cx - pw / 2 - NECK_MM, cx + pw / 2 + NECK_MM, self.ox)
            b0, b1 = self._cell_range(cy - ph / 2 - NECK_MM, cy + ph / 2 + NECK_MM, self.oy)
            neck[max(a0, 0):max(min(a1, self.nx - 1) + 1, 0), max(b0, 0):max(min(b1, self.ny - 1) + 1, 0)] = True
        self._wide[nid] = (occ, neck)
        return self._wide[nid]

    def _free_n(self, nid, li, ix, iy):
        if not self._free(nid, li, ix, iy):
            return False
        wm = self._wide.get(nid)
        if wm is None:
            return True
        occ, neck = wm
        return bool(neck[ix, iy]) or not occ[li][ix, iy]

    # ---------------------------------------------------------- pad cells
    def _pad_cells(self, nid, entry):
        c, pad, cx, cy, w, h = entry
        layers = (0, 1) if pad.kind == "thru" else (0 if "F.Cu" in pad.layers else 1,)
        a0, a1 = self._cell_range(cx - w / 2 + self.hw, cx + w / 2 - self.hw, self.ox)
        b0, b1 = self._cell_range(cy - h / 2 + self.hw, cy + h / 2 - self.hw, self.oy)
        cells = []
        for li in layers:
            for ix in range(a0, a1 + 1):
                for iy in range(b0, b1 + 1):
                    if self._free(nid, li, ix, iy):
                        cells.append((li, ix, iy))
        return cells

    # ---------------------------------------------------------- A*
    def _astar(self, nid, starts, goal_fn, heur_fn):
        heap, best, parent, cnt = [], {}, {}, 0
        wocc = self._wide[nid][0] if nid in self._wide and nid in self.widths else None
        for s in starts:
            st = (s[0], s[1], s[2], 4)
            best[st] = 0.0
            heapq.heappush(heap, (heur_fn(s), 0.0, cnt, st))
            cnt += 1
        while heap:
            _, g, _, st = heapq.heappop(heap)
            if g > best.get(st, 1e18) + 1e-12:
                continue
            li, ix, iy, d = st
            if goal_fn(li, ix, iy):
                path = []
                while st is not None:
                    path.append(st[:3])
                    st = parent.get(st)
                return path[::-1]
            for nd, (dx, dy) in enumerate(DIRS):
                nix, niy = ix + dx, iy + dy
                if not self._free_n(nid, li, nix, niy):
                    continue
                horiz = nd < 2
                pref = (li == 0) == horiz
                cost = 1.0 + (0.0 if pref else 0.3)
                if wocc is not None and wocc[li][nix, niy]:
                    cost += 1.5          # tight spot: only the thin neck-down fits here
                if d != 4 and d != nd:
                    cost += TURN_COST
                if li == 1:
                    cost += (3.0 if self.plane else 0.5) + (6.0 if (self.plane and nid in self.widths) else 0.0)
                ns = (li, nix, niy, nd)
                ng = g + cost
                if ng < best.get(ns, 1e18):
                    best[ns] = ng
                    parent[ns] = st
                    heapq.heappush(heap, (ng + heur_fn((li, nix, niy)), ng, cnt, ns))
                    cnt += 1
            if self._via_ok(nid, ix, iy):
                ns = (1 - li, ix, iy, 4)
                ng = g + VIA_COST * (4 if nid in self.widths else 1)
                if ng < best.get(ns, 1e18):
                    best[ns] = ng
                    parent[ns] = st
                    heapq.heappush(heap, (ng + heur_fn((1 - li, ix, iy)), ng, cnt, ns))
                    cnt += 1
        return None

    # ---------------------------------------------------------- committing
    def _commit(self, net, nid, path, pad_entry):
        """Turn a cell path into segments/vias and register their copper."""
        w0 = RULES["track_width"]
        wnet = self.widths.get(nid)
        occ = self._wide[nid][0] if wnet else None
        c, pad, cx, cy, pw, ph = pad_entry
        segs: list[list] = []   # [layer, (x1,y1), (x2,y2), axis, width]
        for i, (li, ix, iy) in enumerate(path):
            self.tree[nid].add((li, ix, iy))
            if i == 0:
                continue
            pl, pix, piy = path[i - 1]
            if pl != li:
                x, y = self.xy(ix, iy)
                self.vias.append(Via(round(x, 4), round(y, 4), RULES["via_dia"], RULES["via_drill"], net))
                r = RULES["via_dia"] / 2
                for lj in (0, 1):
                    self._mark(lj, x - r, y - r, x + r, y + r, nid, self.infl)
                    self.tree[nid].add((lj, ix, iy))
                continue
            axis = "h" if iy == piy else "v"
            # wide wherever a wide track fits on this layer; default width only in the tight spots
            # (the neck zone around the net's own pads) where it does not
            sw = wnet if (wnet and not occ[li][pix, piy] and not occ[li][ix, iy]) else w0
            if (segs and segs[-1][0] == LAYERS[li] and segs[-1][3] == axis and segs[-1][4] == sw
                    and segs[-1][2] == self.xy(pix, piy)):
                segs[-1][2] = self.xy(ix, iy)
            else:
                segs.append([LAYERS[li], self.xy(pix, piy), self.xy(ix, iy), axis, sw])
        out = []
        for layer, a, b, _, sw in segs:
            out.append(Track(round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4), layer, sw, net))
        # stub from the exact pad centre to the first grid node (stays inside the pad)
        li0, ix0, iy0 = path[0]
        sx, sy = self.xy(ix0, iy0)
        if abs(sx - cx) > 1e-3 or abs(sy - cy) > 1e-3:
            out.insert(0, Track(round(cx, 4), round(cy, 4), round(sx, 4), round(sy, 4), LAYERS[li0], w0, net))
        for t in out:
            self.tracks.append(t)
            hw = t.width / 2
            self._mark(0 if t.layer == "F.Cu" else 1, min(t.x1, t.x2) - hw, min(t.y1, t.y2) - hw,
                       max(t.x1, t.x2) + hw, max(t.y1, t.y2) + hw, nid, self.infl)

    # ---------------------------------------------------------- nets
    def route_net(self, net: str) -> bool:
        nid = self.net_id[net]
        pads = list(self.pads_by_net.get(net, []))
        if nid in self.widths:
            try:
                self._wide_maps(nid)
            except ImportError:            # numpy missing: fall back to the default width
                del self.widths[nid]
        if net == "GND" and self.plane:
            return self._route_ground_plane(net, nid, pads)
        if len(pads) < 2:
            return True
        remaining = pads[:]
        first = remaining.pop(0)
        cells = self._pad_cells(nid, first)
        if not cells:
            return False
        self.tree[nid].update(cells)
        while remaining:
            tx = [self.xy(ix, iy) for (_, ix, iy) in self.tree[nid]]
            cxm = sum(p[0] for p in tx) / len(tx)
            cym = sum(p[1] for p in tx) / len(tx)
            remaining.sort(key=lambda e: abs(e[2] - cxm) + abs(e[3] - cym))
            entry = remaining.pop(0)
            starts = self._pad_cells(nid, entry)
            if not starts:
                return False
            tree = self.tree[nid]
            bx0 = min(c[1] for c in tree); bx1 = max(c[1] for c in tree)
            by0 = min(c[2] for c in tree); by1 = max(c[2] for c in tree)

            def heur(s, bx0=bx0, bx1=bx1, by0=by0, by1=by1):
                return max(bx0 - s[1], 0, s[1] - bx1) + max(by0 - s[2], 0, s[2] - by1)

            def goal(li, ix, iy, tree=tree):
                return (li, ix, iy) in tree

            path = self._astar(nid, starts, goal, heur)
            if path is None:
                return False
            self._commit(net, nid, path, entry)
            self.tree[nid].update(starts)
        return True

    def _route_ground_plane(self, net, nid, pads) -> bool:
        ok = True
        for entry in pads:
            c, pad, cx, cy, w, h = entry
            if pad.kind == "thru":
                continue                 # plated hole touches the B.Cu pour directly
            starts = self._pad_cells(nid, entry)
            if not starts:
                ok = False
                continue
            path = self._astar(nid, starts, lambda li, ix, iy: li == 1, lambda s: 0.0)
            if path is None:
                ok = False
                continue
            self._commit(net, nid, path, entry)
        return ok


def route_design(design: Design, plane: bool = True, attempts: int = 8) -> Design:
    """Route every net; on failure retry with the failing nets first."""
    nets = [n for n in design.nets]

    # estimated length per net from pad centres
    probe = Router(design, plane)
    length = {}
    for n in nets:
        ps = probe.pads_by_net.get(n, [])
        if len(ps) < 2:
            length[n] = 0
        else:
            xs = [p[2] for p in ps]
            ys = [p[3] for p in ps]
            length[n] = (max(xs) - min(xs)) + (max(ys) - min(ys))
    wide = set(getattr(design, "net_width", None) or {})
    order = sorted(nets, key=lambda n: (n == "GND", n not in wide, length[n]))
    best = None
    priority: list[str] = []
    for _ in range(attempts):
        r = Router(design, plane)
        seq = priority + [n for n in order if n not in priority]
        failed = [n for n in seq if not r.route_net(n)]
        if best is None or len(failed) < len(best[2]):
            best = (r.tracks, r.vias, failed)
        if not failed:
            break
        for f in failed:
            if f in priority:
                priority.remove(f)
            priority.insert(0, f)
    design.tracks, design.vias, design.unrouted = best
    design.ground_mode = "plane" if plane else "traces"
    return design

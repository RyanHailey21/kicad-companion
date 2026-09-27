"""Post-route finishing: re-link to schematic, copper pours, zone fill."""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kicad_python import run_pcbnew, sibling
from .net_sync import sync_pcb_nets_from_schematic
from .supervisor import guard_headless_write

_POUR_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
net = None
for cand in ARGS["net_candidates"]:
    net = board.FindNet(cand)
    if net is not None:
        break
if net is None:
    raise SystemExit("pour net not found: %s" % ARGS["net_candidates"])
layers = {board.GetLayerName(l): l for l in board.GetEnabledLayers().Seq()}
ol = board_outline(board)
existing = {}
for z in board.Zones():
    if z.GetNetname() == net.GetNetname() and not z.GetIsRuleArea():
        existing[board.GetLayerName(z.GetLayer())] = z
made = []
for lname in ARGS["layers"]:
    z = existing.get(lname)
    if z is None:
        z = pcbnew.ZONE(board)
        z.SetLayer(layers[lname])
        z.SetNet(net)
        o = z.Outline()
        o.NewOutline()
        inset = ARGS["edge_inset"]
        for x, y in ((ol[0] + inset, ol[1] + inset), (ol[2] - inset, ol[1] + inset),
                     (ol[2] - inset, ol[3] - inset), (ol[0] + inset, ol[3] - inset)):
            o.Append(mm(x), mm(y))
        board.Add(z)
        made.append(lname)
    z.SetLocalClearance(mm(ARGS["clearance"]))
    z.SetMinThickness(mm(ARGS["min_width"]))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if ARGS["thermal"] else pcbnew.ZONE_CONNECTION_FULL)
    z.SetThermalReliefGap(mm(ARGS["thermal_gap"]))
    z.SetThermalReliefSpokeWidth(mm(ARGS["spoke_width"]))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
pcbnew.ZONE_FILLER(board).Fill(board.Zones())

# Stitching vias: drop a GND via wherever the net's fill covers the via footprint on every
# copper layer that has a GND zone, clear of pads. Ties split pour islands to each other and
# to inner planes, and never lands near another net (the fill already keeps clearance).
stitched = 0
if ARGS["stitch_pitch"] > 0 or ARGS["island_vias"]:
    gz = [z for z in board.Zones() if z.GetNetname() == net.GetNetname() and not z.GetIsRuleArea()]
    zlayers = [(z, z.GetLayer()) for z in gz]
    vd, vdr = mm(ARGS["via_diameter"]), mm(ARGS["via_drill"])
    R = vd / 2 + mm(0.05)
    # copper pads only (0201 paste-only pads carry no copper), checked by true pad shape
    pads = [p for f in board.GetFootprints() for p in f.Pads() if p.IsOnCopperLayer()]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    others = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetCode() != net.GetNetCode()]
    keep = vd // 2 + mm(ARGS["track_clearance"])
    mincl = max(board.GetDesignSettings().m_MinClearance, mm(0.1)) + mm(0.01)   # board rule + margin
    import math
    ring = [(0, 0)] + [(R * math.cos(a * math.pi / 4), R * math.sin(a * math.pi / 4)) for a in range(8)]
    plane_layers = {l for _, l in zlayers if board.GetLayerType(l) == pcbnew.LT_POWER}
    edge = vd // 2 + mm(0.3)
    def fits(c, need_layers=None, strict=True):
        """Via at c lies inside the pour fill on need_layers (default: every pour layer) and is
        clear of other-net pads, tracks and vias on all layers. strict=False only needs the via
        centre in the fill (for slivers narrower than a via); copper clearance is still checked."""
        if not (mm(ol[0]) + edge < c.x < mm(ol[2]) - edge and mm(ol[1]) + edge < c.y < mm(ol[3]) - edge):
            return False
        for layer in (need_layers if need_layers is not None else {l for _, l in zlayers}):
            for dx, dy in (ring if strict else ring[:1]):
                q = pcbnew.VECTOR2I(int(c.x + dx), int(c.y + dy))
                if not any(z.HitTestFilledArea(l, q) for z, l in zlayers if l == layer):
                    return False
        for p in pads:
            if p.GetNetCode() == net.GetNetCode():
                if p.HitTest(c, int(vd // 2)):   # next to own-net pads is fine, not in them
                    return False
            elif p.HitTest(c, int(vd // 2 + (R if strict else mincl))):
                return False
        for t in others:
            if t.HitTest(c, int((keep if strict else vd // 2 + mincl) + t.GetWidth() // 2)):
                return False
        for v in vias:
            if (v.GetPosition() - c).EuclideanNorm() < vd + mm(0.3):
                return False
        return True

    def add_via(c):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(c)
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        try:
            v.SetWidth(vd)
        except TypeError:
            v.SetWidth(pcbnew.F_Cu, vd)
        v.SetDrill(vdr)
        v.SetNet(net)
        board.Add(v)
        vias.append(v)

    pitch = mm(ARGS["stitch_pitch"])
    x = mm(ol[0]) + pitch // 2 if pitch > 0 else mm(ol[2])   # pitch 0: islands only, no grid
    while x < mm(ol[2]):
        y = mm(ol[1]) + pitch // 2
        while y < mm(ol[3]):
            c = pcbnew.VECTOR2I(int(x), int(y))
            if fits(c):
                add_via(c)
                stitched += 1
            y += pitch
        x += pitch
    # Islands the grid missed: fine-scan each unconnected fill fragment for a spot that fits.
    def touching(z, L, polys, i, bb):
        # pour-net pads this fragment actually touches (its thermal spokes reach within the gap)
        reach = z.GetThermalReliefGap() + mm(0.05)
        out = []
        for p in pads:
            if p.GetNetCode() != net.GetNetCode() or not p.IsOnLayer(L):
                continue
            pb = p.GetBoundingBox()
            pb.Inflate(reach)
            if not pb.Intersects(bb):
                continue
            px0, py0, px1, py1 = pb.GetLeft(), pb.GetTop(), pb.GetRight(), pb.GetBottom()
            ring_pts = [(px0 + (px1 - px0) * k / 8, py) for k in range(9) for py in (py0, py1)] +                        [(px, py0 + (py1 - py0) * k / 8) for k in range(9) for px in (px0, px1)]
            if any(polys.Contains(pcbnew.VECTOR2I(int(x_), int(y_)), i) for x_, y_ in ring_pts):
                out.append(p)
        return out
    # pour-net pads already tied to a via through tracks (so a fragment touching one is connected)
    own_tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetCode() == net.GetNetCode()]
    own_pads = [p for p in pads if p.GetNetCode() == net.GetNetCode()]
    frontier = [v.GetPosition() for v in vias if v.GetNetCode() == net.GetNetCode()]
    seen_t, linked = set(), set()
    while frontier:
        q = frontier.pop()
        for p in own_pads:
            if id(p) not in linked and p.HitTest(q):
                linked.add(id(p))
                frontier += [t.GetStart() for t in own_tracks if p.HitTest(t.GetEnd())] +                             [t.GetEnd() for t in own_tracks if p.HitTest(t.GetStart())]
        for k, t in enumerate(own_tracks):
            if k in seen_t:
                continue
            if (t.GetStart() - q).EuclideanNorm() < mm(0.01) or (t.GetEnd() - q).EuclideanNorm() < mm(0.01):
                seen_t.add(k); frontier += [t.GetStart(), t.GetEnd()]
    islands_fixed, islands_left = 0, []
    fine = mm(0.1)
    for z, L in zlayers:
        polys = z.GetFilledPolysList(L)
        for i in range(polys.OutlineCount()):
            if any(polys.Contains(v.GetPosition(), i) for v in vias):
                continue
            bb = polys.Outline(i).BBox()
            touch = touching(z, L, polys, i, bb)
            if any(id(p) in linked for p in touch):
                continue
            done = False
            yy = bb.GetTop()
            while yy <= bb.GetBottom() and not done:
                xx = bb.GetLeft()
                while xx <= bb.GetRight():
                    c = pcbnew.VECTOR2I(int(xx), int(yy))
                    if polys.Contains(c, i) and (fits(c, {L} | plane_layers) or fits(c, {L} | plane_layers, strict=False)):
                        add_via(c); stitched += 1; islands_fixed += 1; done = True
                        break
                    xx += fine
                yy += fine
            if not done:
                islands_left.append({"layer": board.GetLayerName(L), "at_mm": [to_mm(bb.GetX()), to_mm(bb.GetY())],
                                     "size_mm": [to_mm(bb.GetWidth()), to_mm(bb.GetHeight())], "_pads": touch})
    # Islands no via fits in: bridge a GND pad inside the island to the nearest GND via with a
    # short straight track on the island's layer, if it clears every other-net item.
    bridged = 0
    if islands_left and ARGS["bridge_islands"]:
        bw = mm(ARGS["bridge_width"])
        def clear_path(a, b, L):
            n = max(2, int((b - a).EuclideanNorm() / mm(0.05)))
            items = [p for p in pads if p.GetNetCode() != net.GetNetCode() and p.IsOnLayer(L)] +                     [t for t in board.GetTracks() if t.GetNetCode() != net.GetNetCode() and
                     (t.GetClass() == "PCB_VIA" or t.GetLayer() == L)]
            for k in range(n + 1):
                q = pcbnew.VECTOR2I(int(a.x + (b.x - a.x) * k / n), int(a.y + (b.y - a.y) * k / n))
                if not (mm(ol[0]) + bw < q.x < mm(ol[2]) - bw and mm(ol[1]) + bw < q.y < mm(ol[3]) - bw):
                    return False
                for it in items:
                    if it.HitTest(q, int(bw // 2 + mincl)):
                        return False
            return True
        def maze_to_via(p, L, radius=4.0, cell=0.05):
            # BFS over a grid centred on the pad; cells must clear other-net copper on L by the
            # track half-width + clearance; goal = a cell where a via fits (plane, pads, tracks, vias).
            import collections
            pc = p.GetPosition()
            n = int(radius / cell)
            step_ = mm(cell)
            near_items = [it for it in ([q for q in pads if q.GetNetCode() != net.GetNetCode() and q.IsOnLayer(L)] +
                          [t for t in board.GetTracks() if t.GetNetCode() != net.GetNetCode() and
                           (t.GetClass() == "PCB_VIA" or t.GetLayer() == L)])
                          if it.GetBoundingBox().Distance(pc) < mm(radius + 1.0)] if hasattr(pcbnew.BOX2I, "Distance") else                          [q for q in pads if q.GetNetCode() != net.GetNetCode() and q.IsOnLayer(L)] +                          [t for t in board.GetTracks() if t.GetNetCode() != net.GetNetCode() and
                          (t.GetClass() == "PCB_VIA" or t.GetLayer() == L)]
            acc = int(bw // 2 + mincl)
            def at(i, j):
                return pcbnew.VECTOR2I(int(pc.x + i * step_), int(pc.y + j * step_))
            free_c = {}
            def ok_cell(i, j):
                k = (i, j)
                if k not in free_c:
                    q = at(i, j)
                    free_c[k] = (mm(ol[0]) + bw < q.x < mm(ol[2]) - bw and mm(ol[1]) + bw < q.y < mm(ol[3]) - bw and
                                 not any(it.HitTest(q, acc) for it in near_items))
                return free_c[k]
            # Dijkstra with a turn penalty: straight 45/90-degree runs instead of staircases
            import heapq
            moves = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
            start = (0, 0, None)
            dist = {start: 0.0}
            prev = {start: None}
            pq = [(0.0, start)]
            goal = None
            while pq:
                dcur, st = heapq.heappop(pq)
                if dcur > dist.get(st, 1e18):
                    continue
                i, j, dirn = st
                if (i * i + j * j) * cell * cell >= 0.36 and i % 2 == 0 and j % 2 == 0 and                         fits(at(i, j), plane_layers, strict=False):
                    goal = st; break
                for mv in moves:
                    k = (i + mv[0], j + mv[1])
                    if abs(k[0]) > n or abs(k[1]) > n:
                        continue
                    if not (p.HitTest(at(*k)) or ok_cell(*k)):
                        continue
                    nd = dcur + (1.4142 if mv[0] and mv[1] else 1.0) + (0.0 if dirn in (None, mv) else 6.0)
                    ns = (k[0], k[1], mv)
                    if nd < dist.get(ns, 1e18):
                        dist[ns] = nd; prev[ns] = st; heapq.heappush(pq, (nd, ns))
            if goal is None:
                return None
            cells = []
            k = goal
            while k is not None:
                cells.append(k[:2]); k = prev[k]
            cells.reverse()
            # keep only direction changes
            pts = [cells[0]]
            for a, b_, c_ in zip(cells, cells[1:], cells[2:]):
                if (b_[0] - a[0], b_[1] - a[1]) != (c_[0] - b_[0], c_[1] - b_[1]):
                    pts.append(b_)
            pts.append(cells[-1])
            return [at(*q) for q in pts]
        still = []
        for isl in islands_left:
            L = board.GetLayerID(isl["layer"])
            own = isl["_pads"]
            # already bridged on an earlier run: a pad track that ends on a pour-net via
            vpos = {(v.GetPosition().x, v.GetPosition().y) for v in board.GetTracks()
                    if v.GetClass() == "PCB_VIA" and v.GetNetCode() == net.GetNetCode()}
            if any(t.GetClass() == "PCB_TRACK" and t.GetNetCode() == net.GetNetCode() and t.GetLayer() == L and
                   ((p.HitTest(t.GetStart()) and (t.GetEnd().x, t.GetEnd().y) in vpos) or
                    (p.HitTest(t.GetEnd()) and (t.GetStart().x, t.GetStart().y) in vpos))
                   for p in own for t in board.GetTracks()):
                continue
            gv = [v for v in board.GetTracks() if v.GetClass() == "PCB_VIA" and v.GetNetCode() == net.GetNetCode()]
            opts = sorted(((v.GetPosition() - p.GetPosition()).EuclideanNorm(), p, v) for p in own for v in gv
                          if (v.GetPosition() - p.GetPosition()).EuclideanNorm() <= mm(ARGS["bridge_max"]))
            ok = False
            for d, p, v in opts:
                if clear_path(p.GetPosition(), v.GetPosition(), L):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(p.GetPosition()); t.SetEnd(v.GetPosition())
                    t.SetWidth(bw); t.SetLayer(L); t.SetNet(net)
                    board.Add(t); bridged += 1; ok = True
                    break
            if not ok:
                # fanout: a new via right beside one of the island's pads, joined by a short track
                import math
                for p in own:
                    pc = p.GetPosition()
                    spots = []
                    for ri in range(4, 21):
                        rr = mm(ri * 0.1)
                        for ai in range(24):
                            a = ai * math.pi / 12
                            spots.append((rr, pcbnew.VECTOR2I(int(pc.x + rr * math.cos(a)), int(pc.y + rr * math.sin(a)))))
                    for rr, c in spots:
                        if fits(c, plane_layers, strict=False) and clear_path(pc, c, L):
                            add_via(c)
                            t = pcbnew.PCB_TRACK(board)
                            t.SetStart(pc); t.SetEnd(c); t.SetWidth(bw); t.SetLayer(L); t.SetNet(net)
                            board.Add(t); bridged += 1; stitched += 1; ok = True
                            break
                    if ok:
                        break
            if not ok:
                # maze: bent path on a fine grid from an island pad around obstacles to a via spot
                for p in own:
                    path = maze_to_via(p, L)
                    if path:
                        for a, b_ in zip(path, path[1:]):
                            t = pcbnew.PCB_TRACK(board)
                            t.SetStart(a); t.SetEnd(b_); t.SetWidth(bw); t.SetLayer(L); t.SetNet(net)
                            board.Add(t)
                        add_via(path[-1]); bridged += 1; stitched += 1; ok = True
                        break
            if not ok:
                still.append(isl)
        islands_left = still
    if stitched or bridged:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(ARGS["pcb"])
emit({"net": net.GetNetname(), "zones_created": made, "zones_total": len(list(board.Zones())),
      "stitching_vias": stitched, "islands_fixed": globals().get("islands_fixed", 0),
      "islands_bridged": globals().get("bridged", 0),
      "islands_without_via": [dict({k: v for k, v in d.items() if k != "_pads"},
                                   pads=["%s.%s" % (p.GetParentFootprint().GetReference(), p.GetNumber())
                                         for p in d.get("_pads", [])])
                              for d in globals().get("islands_left", [])]})
'''


def finalize_pcb(
    pcb_path: str,
    sch_path: Optional[str] = None,
    pour_net: Optional[str] = "GND",
    pour_layers: Optional[List[str]] = None,
    clearance_mm: float = 0.4,
    min_width_mm: float = 0.3,
    thermal_reliefs: bool = True,
    thermal_gap_mm: float = 0.3,
    spoke_width_mm: float = 0.4,
    edge_inset_mm: float = 0.5,
    stitch_pitch_mm: float = 0.0,
    island_vias: bool = True,
    bridge_islands: bool = True,
    bridge_width_mm: float = 0.2,
    bridge_max_mm: float = 4.0,
    via_diameter_mm: float = 0.5,
    via_drill_mm: float = 0.3,
    relink: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Finish a routed board: re-sync links/fields from the schematic, add/refresh copper pours, fill zones.

    Args:
        pcb_path: Routed board.
        sch_path: Schematic (defaults to sibling).
        pour_net: Net to pour ("GND" also matches "/GND"); None skips pours.
        pour_layers: Copper layers to pour (default F.Cu and B.Cu).
        clearance_mm / min_width_mm / thermal_*: zone settings. Existing pours on the net are updated, not duplicated.
        stitch_pitch_mm: Grid pitch for pour stitching vias (0 = none). A via is placed wherever the
            pour net's fill fully covers it on every layer carrying that net's zones, clear of pads
            and other vias, which ties isolated pour islands and inner planes together.
        island_vias: Even with stitch_pitch_mm = 0, give every pour fragment that is not already tied
            to the net (no via inside, no touching pad with a track to a via) one via, bridge or
            maze track, so pours never float while the board stays free of a via grid.
        via_diameter_mm / via_drill_mm: Stitching via size.
        bridge_islands / bridge_width_mm / bridge_max_mm: For pour islands no via fits in, run a short
            straight track from a pour-net pad inside the island to the nearest pour-net via (within
            bridge_max_mm), only where it clears every other-net pad, track and via.
        relink: Run sync_pcb_nets first so symbol links, values, fields and NC nets match the schematic.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    out: Dict[str, Any] = {"status": "success", "pcb_file": str(p_path)}
    if relink and sibling(p_path, ".kicad_sch", sch_path).is_file():
        sync = sync_pcb_nets_from_schematic(str(p_path), sch_path, configure_classes=False,
                                            allow_while_open=allow_while_open)
        out["relink"] = {k: sync[k] for k in ("pads_changed", "missing_on_board", "extra_on_board")}
    if pour_net:
        cands = [pour_net] if pour_net.startswith("/") else [pour_net, "/" + pour_net]
        out["pours"] = run_pcbnew(_POUR_JOB, {
            "pcb": str(p_path), "net_candidates": cands, "layers": pour_layers or ["F.Cu", "B.Cu"],
            "clearance": clearance_mm, "min_width": min_width_mm, "thermal": thermal_reliefs,
            "thermal_gap": thermal_gap_mm, "spoke_width": spoke_width_mm, "edge_inset": edge_inset_mm,
            "stitch_pitch": stitch_pitch_mm, "island_vias": island_vias, "bridge_islands": bridge_islands,
            "bridge_width": bridge_width_mm, "bridge_max": bridge_max_mm, "via_diameter": via_diameter_mm, "via_drill": via_drill_mm,
            "track_clearance": max(clearance_mm, 0.15),
        })
    return out

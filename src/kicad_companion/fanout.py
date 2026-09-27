"""Pre-route plane fanout: a via + short dogbone stub for every SMD pad on a plane net.

Autorouters treat plane connections as ordinary work items. On a dense board some of them
lose the race for space, the pad ends up boxed in by signal tracks, and the outer-layer pour
around it becomes an isolated island that no via can reach any more. Fanning every plane pad
out *before* routing reserves that via; the stubs and vias are locked so Freerouting
routes around them instead of ripping them up.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kicad_python import run_pcbnew
from .supervisor import guard_headless_write

_FANOUT_JOB = r'''
import math
board = pcbnew.LoadBoard(ARGS["pcb"])
ds = board.GetDesignSettings()
ol = board_outline(board)
if ARGS["nets"]:
    want = set()
    for n in ARGS["nets"]:
        for cand in (n, "/" + n.lstrip("/")):
            if board.FindNet(cand) is not None:
                want.add(board.FindNet(cand).GetNetname())
else:
    # default: every net that owns a zone on a power (plane) layer
    want = {z.GetNetname() for z in board.Zones()
            if not z.GetIsRuleArea() and z.GetNetname() and board.GetLayerType(z.GetLayer()) == pcbnew.LT_POWER}
cl = mm(ARGS["clearance"]) if ARGS["clearance"] else max(ds.m_MinClearance, mm(0.1)) + mm(0.01)
vd, vdr = mm(ARGS["via_diameter"]), mm(ARGS["via_drill"])
sw = mm(ARGS["stub_width"])
r = vd // 2
edge_keep = r + max(ds.m_CopperEdgeClearance, mm(0.2))
pads = [p for f in board.GetFootprints() for p in f.Pads() if p.IsOnCopperLayer()]
tracks = list(board.GetTracks())
vias = [t for t in tracks if t.GetClass() == "PCB_VIA"]
keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
planes = {}
for z in board.Zones():
    if not z.GetIsRuleArea() and board.GetLayerType(z.GetLayer()) == pcbnew.LT_POWER:
        planes.setdefault(z.GetNetname(), []).append(z)
if ARGS["check_plane_fill"]:
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

def via_fits(c, netcode, netname):
    if ol and not (mm(ol[0]) + edge_keep < c.x < mm(ol[2]) - edge_keep and
                   mm(ol[1]) + edge_keep < c.y < mm(ol[3]) - edge_keep):
        return False
    for z in keepouts:
        if z.Outline().Contains(c):
            return False
    for p in pads:
        # never in any pad (no via-in-pad: solder wicks down an open via), clearance to others
        if p.HitTest(c, int(r + (mm(0.05) if p.GetNetCode() == netcode else cl))):
            return False
    for t in tracks:
        if t.GetNetCode() == netcode:
            continue
        if t.GetClass() == "PCB_VIA":
            if (t.GetPosition() - c).EuclideanNorm() < r + t.GetWidth(pcbnew.F_Cu) // 2 + cl:
                return False
        elif t.HitTest(c, int(r + cl + t.GetWidth() // 2)):
            return False
    for v in vias:
        if v.GetNetCode() == netcode and (v.GetPosition() - c).EuclideanNorm() < vd + mm(0.2):
            return False
    if ARGS["check_plane_fill"] and planes.get(netname):
        # the via must land in the plane's fill (not in another net's antipad)
        if not any(z.HitTestFilledArea(z.GetLayer(), c) for z in planes[netname]):
            return False
    return True

def stub_clear(a, b, layer, netcode, pad):
    n = max(2, int((b - a).EuclideanNorm() / mm(0.05)))
    acc = int(sw // 2 + cl)
    for k in range(n + 1):
        q = pcbnew.VECTOR2I(int(a.x + (b.x - a.x) * k / n), int(a.y + (b.y - a.y) * k / n))
        for p in pads:
            if p is pad or not p.IsOnLayer(layer):
                continue
            if p.GetNetCode() != netcode and p.HitTest(q, acc):
                return False
        for t in tracks:
            if t.GetNetCode() == netcode or t.GetClass() == "PCB_VIA":
                continue
            if t.GetLayer() == layer and t.HitTest(q, int(acc + t.GetWidth() // 2)):
                return False
        for v in vias:
            if v.GetNetCode() != netcode and v.HitTest(q, acc):
                return False
    return True

def routed(p):
    # already has copper leaving it (a track end or a via inside the pad): leave it alone
    for t in tracks:
        if t.GetNetCode() != p.GetNetCode():
            continue
        if t.GetClass() == "PCB_VIA":
            if p.HitTest(t.GetPosition()):
                return True
        elif p.IsOnLayer(t.GetLayer()) and (p.HitTest(t.GetStart()) or p.HitTest(t.GetEnd())):
            return True
    return False

placed, shared, skipped, failed = [], [], [], []
fan_vias = []   # (via, netcode)
for f in board.GetFootprints():
    fc = f.GetPosition()
    bb = f.GetBoundingBox(False)
    fc = pcbnew.VECTOR2I((bb.GetLeft() + bb.GetRight()) // 2, (bb.GetTop() + bb.GetBottom()) // 2)
    for p in f.Pads():
        if p.GetNetname() not in want or not p.GetNumber() or not p.IsOnCopperLayer():
            continue
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            continue                      # through-hole pads already reach every layer
        name = "%s.%s" % (f.GetReference(), p.GetNumber())
        if routed(p):
            skipped.append(name); continue
        layer = pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
        pc = p.GetPosition()
        nc = p.GetNetCode()
        # 1. share a fanout via already placed for a neighbouring pad of the same net
        done = False
        for v, vn in fan_vias:
            if vn == nc and (v.GetPosition() - pc).EuclideanNorm() <= mm(ARGS["share_mm"]) and \
                    stub_clear(pc, v.GetPosition(), layer, nc, p):
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pc); t.SetEnd(v.GetPosition()); t.SetWidth(sw); t.SetLayer(layer); t.SetNet(p.GetNet())
                t.SetLocked(ARGS["lock"]); board.Add(t); tracks.append(t)
                shared.append(name); done = True
                break
        if done:
            continue
        # 2. new via: outward from the part first (classic dogbone), then sweep all around
        ox, oy = pc.x - fc.x, pc.y - fc.y
        base = math.atan2(oy, ox) if (ox or oy) else 0.0
        order = sorted(range(24), key=lambda k: min(k, 24 - k))
        half = max(p.GetBoundingBox().GetWidth(), p.GetBoundingBox().GetHeight()) / 2
        best = None
        for k in order:
            a = base + k * math.pi / 12
            d = half + r + mm(0.1)
            while d <= mm(ARGS["max_mm"]) + half:
                c = pcbnew.VECTOR2I(int(pc.x + d * math.cos(a)), int(pc.y + d * math.sin(a)))
                if via_fits(c, nc, p.GetNetname()) and stub_clear(pc, c, layer, nc, p):
                    best = c
                    break
                d += mm(0.05)
            if best is not None:
                break
        if best is None:
            failed.append({"pad": name, "net": p.GetNetname(), "at_mm": [to_mm(pc.x), to_mm(pc.y)]})
            continue
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(best); v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        try:
            v.SetWidth(vd)
        except TypeError:
            v.SetWidth(pcbnew.F_Cu, vd)
        v.SetDrill(vdr); v.SetNet(p.GetNet()); v.SetLocked(ARGS["lock"])
        board.Add(v); vias.append(v); tracks.append(v); fan_vias.append((v, nc))
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pc); t.SetEnd(best); t.SetWidth(sw); t.SetLayer(layer); t.SetNet(p.GetNet())
        t.SetLocked(ARGS["lock"]); board.Add(t); tracks.append(t)
        placed.append(name)
board.Save(ARGS["pcb"])
emit({"nets": sorted(want), "vias_added": len(placed), "pads_fanned": len(placed) + len(shared),
      "pads_sharing_a_via": len(shared), "pads_already_routed": len(skipped), "failed": failed})
'''


def fanout_vias(
    pcb_path: str,
    nets: Optional[List[str]] = None,
    via_diameter_mm: float = 0.5,
    via_drill_mm: float = 0.3,
    stub_width_mm: float = 0.2,
    max_distance_mm: float = 1.5,
    share_mm: float = 1.2,
    clearance_mm: Optional[float] = None,
    lock: bool = True,
    check_plane_fill: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Fan out SMD pads on plane nets before autorouting: one via + dogbone stub per pad.

    Args:
        pcb_path: Placed (usually unrouted) board.
        nets: Nets to fan out (bare or "/"-prefixed names). Default: every net that owns a
            zone on a power-type (plane) layer, e.g. {"In1.Cu": "GND"} from build_pcb_from_schematic.
        via_diameter_mm / via_drill_mm / stub_width_mm: Via and stub size.
        max_distance_mm: How far past the pad edge the via may go.
        share_mm: A pad within this distance of another same-net fanout via reuses it
            (a stub to it) instead of adding one more via.
        clearance_mm: Copper clearance to respect (default: board minimum + 0.01 mm).
        lock: Lock vias and stubs so the autorouter keeps them.
        check_plane_fill: Only accept spots where the plane's fill actually reaches.

    Vias never land in a pad (no via-in-pad), stay clear of other nets on every layer,
    respect via keep-out rule areas and the board edge. Pads that already have copper
    leaving them are skipped, so it is safe to re-run.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    res = run_pcbnew(_FANOUT_JOB, {
        "pcb": str(p_path), "nets": nets or [], "via_diameter": via_diameter_mm, "via_drill": via_drill_mm,
        "stub_width": stub_width_mm, "max_mm": max_distance_mm, "share_mm": share_mm,
        "clearance": clearance_mm, "lock": lock, "check_plane_fill": check_plane_fill,
    })
    res["status"] = "success" if not res["failed"] else "partial"
    if res["failed"]:
        res["hint"] = ("No legal via spot near these pads: give them room in placement (routing halo, "
                       "near rules) or raise max_distance_mm.")
    return res

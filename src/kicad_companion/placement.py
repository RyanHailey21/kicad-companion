"""Placement analysis and automation on exact footprint courtyards (via pcbnew).

Footprint origins are frequently pin 1 (every THT part), so geometry is always taken
from the rendered courtyard polygons, never from the origin.
"""
import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import find_kicad_cli, get_render_cache_dir
from .kicad_python import run_pcbnew
from .supervisor import guard_headless_write

Rect = Tuple[float, float, float, float]

_READ_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
fps = []
for fp in board.GetFootprints():
    p = fp.GetPosition()
    fps.append({"ref": fp.GetReference(), "lib": str(fp.GetFPID().GetLibNickname()),
                "name": str(fp.GetFPID().GetLibItemName()), "x": to_mm(p.x), "y": to_mm(p.y),
                "rot": fp.GetOrientationDegrees(), "bbox": courtyard_bbox(fp),
                "back": fp.IsFlipped(), "pads": len(fp.Pads())})
emit({"outline": board_outline(board), "footprints": fps})
'''

_MOVE_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
for ref, (dx, dy) in ARGS["moves"].items():
    fp = by_ref.get(ref)
    if fp is not None:
        fp.Move(pcbnew.VECTOR2I(mm(dx), mm(dy)))
board.Save(ARGS["pcb"])
emit({"moved": len(ARGS["moves"])})
'''


def _read(pcb: Path) -> Dict[str, Any]:
    return run_pcbnew(_READ_JOB, {"pcb": str(pcb)})


def _is_connector(fp: Dict[str, Any]) -> bool:
    return fp["ref"].upper().startswith(("J", "P", "CN", "X")) or "Connector" in fp["lib"]


def _is_mechanical(fp: Dict[str, Any]) -> bool:
    return fp["ref"].upper().startswith(("H", "MH")) or fp["lib"].startswith("MountingHole")


def _overlap(a: Rect, b: Rect, gap: float) -> Tuple[float, float]:
    ox = min(a[2], b[2]) - max(a[0], b[0]) + gap
    oy = min(a[3], b[3]) - max(a[1], b[1]) + gap
    return ox, oy


def _drc_courtyard_overlaps(pcb: Path) -> Optional[int]:
    out = get_render_cache_dir() / f"{pcb.stem}_crt_{int(time.time() * 1000)}.json"
    subprocess.run([find_kicad_cli(), "pcb", "drc", "--format", "json", "--severity-all", "-o", str(out), str(pcb)],
                   capture_output=True, text=True, check=False)
    if not out.is_file():
        return None
    data = json.loads(out.read_text(encoding="utf-8"))
    return sum(1 for v in data.get("violations", []) if v.get("type") in ("courtyards_overlap", "npth_inside_courtyard", "pth_inside_courtyard"))


def check_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.25,
    connector_edge_inset_mm: float = 2.0,
    part_edge_inset_mm: float = 0.5,
) -> Dict[str, Any]:
    """Courtyard overlaps, board-edge insets and mounting-hole keepouts from exact courtyards."""
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")
    data = _read(p_path)
    fps, outline = data["footprints"], data["outline"]

    overlaps, edge_violations, hole_violations = [], [], []
    for i, a in enumerate(fps):
        if outline:
            inset = connector_edge_inset_mm if _is_connector(a) else (0.0 if _is_mechanical(a) else part_edge_inset_mm)
            b = a["bbox"]
            worst = min(b[0] - outline[0], b[1] - outline[1], outline[2] - b[2], outline[3] - b[3])
            if worst < inset - 1e-6:
                edge_violations.append({"ref": a["ref"], "bbox": b, "inset_mm": round(worst, 3), "required_inset_mm": inset})
        for b in fps[i + 1:]:
            if a["back"] != b["back"] and not (_is_mechanical(a) or _is_mechanical(b)):
                continue  # opposite sides only collide through holes; DRC covers that
            ox, oy = _overlap(a["bbox"], b["bbox"], min_clearance_mm)
            if ox > 0 and oy > 0:
                entry = {"comp1": a["ref"], "comp2": b["ref"], "overlap_mm": round(min(ox, oy), 3)}
                (hole_violations if (_is_mechanical(a) or _is_mechanical(b)) else overlaps).append(entry)

    drc_overlaps = _drc_courtyard_overlaps(p_path)
    score = max(0, 100 - 10 * len(overlaps) - 15 * len(edge_violations) - 10 * len(hole_violations))
    return {
        "status": "success",
        "board": str(p_path),
        "board_outline_mm": outline,
        "placement_score": score,
        "is_pass": score == 100 and not drc_overlaps,
        "total_components": len(fps),
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "edge_violation_count": len(edge_violations),
        "edge_violations": edge_violations,
        "hole_violation_count": len(hole_violations),
        "hole_violations": hole_violations,
        "drc_courtyard_violations": drc_overlaps,
        "method": "exact courtyard bounding boxes (pcbnew) + KiCad DRC polygon check",
    }


def resolve_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.5,
    grid_step_mm: float = 0.5,
    fixed_refs: Optional[List[str]] = None,
    max_iterations: int = 50,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Separate overlapping footprints by minimum-penetration relaxation on exact courtyards.

    Connectors and mounting holes are fixed by default, plus anything in fixed_refs.
    Movable parts are clamped inside the board outline and their origins snapped to grid.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    data = _read(p_path)
    fps, outline = data["footprints"], data["outline"]
    fixed = set(fixed_refs or []) | {f["ref"] for f in fps if _is_connector(f) or _is_mechanical(f)}

    box = {f["ref"]: list(f["bbox"]) for f in fps}
    orig = {f["ref"]: (f["x"], f["y"]) for f in fps}
    shift = {f["ref"]: [0.0, 0.0] for f in fps}

    def move(ref: str, dx: float, dy: float) -> None:
        b = box[ref]
        if outline:
            dx = max(outline[0] + 0.5 - b[0], min(outline[2] - 0.5 - b[2], dx))
            dy = max(outline[1] + 0.5 - b[1], min(outline[3] - 0.5 - b[3], dy))
        b[0] += dx; b[2] += dx; b[1] += dy; b[3] += dy
        shift[ref][0] += dx; shift[ref][1] += dy

    iteration = 0
    for iteration in range(max_iterations):
        moved = False
        for i, a in enumerate(fps):
            for b in fps[i + 1:]:
                ra, rb = a["ref"], b["ref"]
                if ra in fixed and rb in fixed:
                    continue
                ox, oy = _overlap(box[ra], box[rb], min_clearance_mm)
                if ox <= 0 or oy <= 0:
                    continue
                moved = True
                ca = ((box[ra][0] + box[ra][2]) / 2, (box[ra][1] + box[ra][3]) / 2)
                cb = ((box[rb][0] + box[rb][2]) / 2, (box[rb][1] + box[rb][3]) / 2)
                if ox < oy:
                    s = 1.0 if cb[0] >= ca[0] else -1.0
                    d = (ox * s, 0.0)
                else:
                    s = 1.0 if cb[1] >= ca[1] else -1.0
                    d = (0.0, oy * s)
                if ra not in fixed and rb not in fixed:
                    move(ra, -d[0] / 2, -d[1] / 2)
                    move(rb, d[0] / 2, d[1] / 2)
                elif ra in fixed:
                    move(rb, *d)
                else:
                    move(ra, -d[0], -d[1])
        if not moved:
            break

    # Snap origins to grid, choosing the floor/ceil combination that keeps clearance
    moves, relocated = {}, {}
    for ref, (dx, dy) in shift.items():
        if ref in fixed or (abs(dx) < 1e-3 and abs(dy) < 1e-3):
            continue
        ox, oy = orig[ref]
        rx, ry = ox + dx, oy + dy
        g = grid_step_mm
        cands = sorted({(round(fx(rx / g) * g, 4), round(fy(ry / g) * g, 4))
                        for fx in (math.floor, math.ceil, round) for fy in (math.floor, math.ceil, round)},
                       key=lambda c: abs(c[0] - rx) + abs(c[1] - ry))
        chosen = cands[0]
        for nx, ny in cands:
            b = box[ref]
            test = (b[0] + nx - rx, b[1] + ny - ry, b[2] + nx - rx, b[3] + ny - ry)
            if all(not all(v > 0 for v in _overlap(test, box[o], min_clearance_mm)) for o in box if o != ref):
                chosen = (nx, ny)
                break
        nx, ny = chosen
        b = box[ref]
        box[ref] = [b[0] + nx - rx, b[1] + ny - ry, b[2] + nx - rx, b[3] + ny - ry]
        moves[ref] = (nx - ox, ny - oy)
        relocated[ref] = {"from": (ox, oy), "to": (nx, ny)}
    if moves:
        run_pcbnew(_MOVE_JOB, {"pcb": str(p_path), "moves": moves})

    final = check_placement_overlaps(str(p_path), min_clearance_mm=min(min_clearance_mm, 0.25))
    return {
        "status": "success",
        "board": str(p_path),
        "iterations": iteration + 1,
        "components_relocated_count": len(relocated),
        "relocated": relocated,
        "fixed_refs": sorted(fixed),
        "remaining_overlaps": final["overlap_count"],
        "placement_score": final["placement_score"],
        "drc_courtyard_violations": final["drc_courtyard_violations"],
    }


# ------------------------------------------------------------------ plan-driven placement
_PLACE_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
ol = board_outline(board)
ox, oy = (ol[0], ol[1]) if (ol and ARGS["relative"]) else (0.0, 0.0)
gap = ARGS["gap"]
def is_through(fp):
    return any(p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH) for p in fp.Pads())
def side_of(fp):
    return "both" if is_through(fp) else ("B" if fp.IsFlipped() else "F")
def set_side(fp, side):
    if (side == "B") != fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
def place_center(fp, cx, cy, rot):
    fp.SetOrientationDegrees(rot)
    b = courtyard_bbox(fp)
    fp.Move(pcbnew.VECTOR2I(mm(cx - (b[0] + b[2]) / 2), mm(cy - (b[1] + b[3]) / 2)))
def rel(r):
    return [v + (ox if i % 2 == 0 else oy) for i, v in enumerate(r)]
rects, done, overflow, unknown = [], set(), [], []   # rect = (x0, y0, x1, y1, side, halo)
# functional groups: each member is placed by connectivity but kept inside its group's rect
# and on its group's sides; group_of[ref] = (x0, y0, x1, y1, sides, target)
group_of, group_spill = {}, []
for g in ARGS.get("groups", []):
    gr = rel(g["rect"])
    gt = rel(g["target"]) if g.get("target") else [(gr[0] + gr[2]) / 2, (gr[1] + gr[3]) / 2]
    gtuple = (gr[0], gr[1], gr[2], gr[3], g.get("sides") or [g.get("side", "F")], gt)   # shared: identity = group
    for ref in g["refs"]:
        group_of.setdefault(ref, gtuple)
for k in ARGS["keepouts"]:
    rects.append(tuple(rel(k["rect"])) + (k.get("side", "both"), 0.0))
for ref, spec in ARGS["fixed"].items():
    fp = by_ref.get(ref)
    if fp is None:
        unknown.append(ref); continue
    set_side(fp, spec[3] if len(spec) > 3 else "F")
    place_center(fp, spec[0] + ox, spec[1] + oy, spec[2] if len(spec) > 2 else fp.GetOrientationDegrees())
    rects.append(courtyard_bbox(fp) + (side_of(fp), 0.0)); done.add(ref)
for ref, fp in by_ref.items():
    if ref not in done and ref in ARGS["keep"]:
        rects.append(courtyard_bbox(fp) + (side_of(fp), 0.0)); done.add(ref)
HALO = {"cur": 0.0}   # routing halo of the part currently being placed
def free(a, side):
    h = HALO["cur"]
    for b in rects:
        if not (side == "both" or b[4] == "both" or b[4] == side):
            continue
        g = gap + (h if HALO.get("tight") else max(h, b[5]))
        if not (a[2] + g <= b[0] or b[2] + g <= a[0] or a[3] + g <= b[1] or b[3] + g <= a[1]):
            return False
    return True
def pack(fp, x0, y0, x1, y1, rot, step, side):
    set_side(fp, side)
    fp.SetOrientationDegrees(rot)
    b = courtyard_bbox(fp)
    w, h = b[2] - b[0], b[3] - b[1]
    occ = side_of(fp)
    y = y0
    while y + h <= y1 + 1e-6:
        x = x0
        while x + w <= x1 + 1e-6:
            if free((x, y, x + w, y + h), occ):
                place_center(fp, x + w / 2, y + h / 2, rot)
                rects.append((x, y, x + w, y + h, occ, HALO["cur"]))
                return True
            x += step
        y += step
    return False
for reg in ARGS["regions"]:
    x0, y0, x1, y1 = rel(reg["rect"])
    sides = reg.get("sides") or [reg.get("side", "F")]
    for ref in reg["refs"]:
        fp = by_ref.get(ref)
        if fp is None:
            unknown.append(ref); continue
        if ref in done:
            continue
        rot = reg.get("rotations", {}).get(ref, reg.get("rotation", 0))
        if any(pack(fp, x0, y0, x1, y1, rot, ARGS["step"], sd) for sd in sides):
            done.add(ref)
        else:
            overflow.append(ref)
def area(f):
    b = courtyard_bbox(f)
    return (b[2] - b[0]) * (b[3] - b[1])
if ARGS["auto_rest"] and ol and ARGS["strategy"] == "pack":
    rest = [fp for r, fp in by_ref.items() if r not in done]
    rest.sort(key=lambda f: (-area(f), f.GetReference()))
    m = ARGS["margin"]
    for fp in rest:
        rots = [0, 90] if ARGS["try_rotations"] else [fp.GetOrientationDegrees()]
        ok = any(pack(fp, ol[0] + m, ol[1] + m, ol[2] - m, ol[3] - m, rot, ARGS["step"], sd)
                 for sd in ARGS["auto_sides"] for rot in rots)
        (done.add(fp.GetReference()) if ok else overflow.append(fp.GetReference()))
elif ARGS["auto_rest"] and ol:
    # Connectivity-driven: place each part as close as possible to the parts it shares
    # signal nets with (supply/ground and other high-fanout nets are ignored), which keeps
    # functional blocks together and makes the board far easier to route.
    pad_count = {}
    for f in by_ref.values():
        for p in f.Pads():
            n = p.GetNetname()
            if n:
                pad_count[n] = pad_count.get(n, 0) + 1
    def nets_of(f):
        return {p.GetNetname() for p in f.Pads()
                if p.GetNetname() and 1 < pad_count[p.GetNetname()] <= ARGS["max_net_pads"]
                and not p.GetNetname().startswith("unconnected-")}
    nets = {r: nets_of(f) for r, f in by_ref.items()}
    def center(f):
        b = courtyard_bbox(f)
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    placed_c = {r: center(by_ref[r]) for r in done}
    def pad_xy(p):
        q = p.GetPosition()
        return (pcbnew.ToMM(q.x), pcbnew.ToMM(q.y))
    def anchor_point(r, ic):
        # the IC pad on the cap's lowest-fanout shared net (the supply pin, not ground)
        shared = {p.GetNetname() for p in by_ref[r].Pads() if p.GetNetname()} &                  {p.GetNetname() for p in by_ref[ic].Pads() if p.GetNetname()}
        if not shared:
            return placed_c[ic]
        n = min(shared, key=lambda k: pad_count.get(k, 0))
        pts = [pad_xy(p) for p in by_ref[ic].Pads() if p.GetNetname() == n]
        return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
    def net_points(r):
        # pads of already-placed parts on each signal net this part uses (one mean point per net)
        out = []
        for n in nets[r]:
            pts = [pad_xy(p) for q in placed_c for p in by_ref[q].Pads() if p.GetNetname() == n]
            if pts:
                out.append(((sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)), 1))
        return out
    m, step = ARGS["margin"], ARGS["step"]
    todo = set(by_ref) - done
    # Adaptive routing halo: spread parts so they use ~target_util of the usable area
    # instead of clumping, scaling each part's halo with its pin count (fanout room).
    def npins(f):
        return len({p.GetNumber() for p in f.Pads() if p.GetNumber()})
    def weight(r):
        return min(2.0, max(0.5, (npins(by_ref[r]) / 4.0) ** 0.5))
    halo_of = {}
    if ARGS["halo"] == "auto" or isinstance(ARGS["halo"], (int, float)):
        dims = {}
        for r in todo:
            b = courtyard_bbox(by_ref[r])
            dims[r] = (b[2] - b[0], b[3] - b[1])
        def solve_halo(members, area_rect, sides, util):
            # halo that makes these members fill ~target_util of the free area of area_rect
            x0, y0, x1, y1 = area_rect
            blocked = 0.0
            for q in rects:
                if q[4] != "both" and q[4] not in sides:
                    continue
                ox_ = min(x1, q[2]) - max(x0, q[0])
                oy_ = min(y1, q[3]) - max(y0, q[1])
                if ox_ > 0 and oy_ > 0:
                    blocked += ox_ * oy_ * (len(sides) if q[4] == "both" else 1)
            avail = max(1.0, (x1 - x0) * (y1 - y0) * len(sides) - blocked) * util
            lo, hi = 0.0, 3.0
            for _ in range(40):
                d = (lo + hi) / 2
                need = sum((dims[r][0] + 2 * d * weight(r) + gap) * (dims[r][1] + 2 * d * weight(r) + gap) for r in members)
                lo, hi = (d, hi) if need < avail else (lo, d)
            return lo
        if ARGS["halo"] == "auto":
            # grouped parts share their group's rect; everything else shares the board
            by_area = {}
            for r in todo:
                g = group_of.get(r)
                key = (g[0], g[1], g[2], g[3], tuple(g[4])) if g else None
                by_area.setdefault(key, []).append(r)
            board_rect = (ol[0] + m, ol[1] + m, ol[2] - m, ol[3] - m)
            for key, members in by_area.items():
                if key is None:
                    base = solve_halo(members, board_rect, ARGS["auto_sides"], ARGS["target_util"])
                else:
                    # members of every group sharing this rect compete for it
                    # a small rect packs less efficiently than the open board
                    base = solve_halo(members, key[:4], list(key[4]), ARGS["target_util"] * 0.75)
                for r in members:
                    halo_of[r] = base * weight(r)
        else:
            halo_of = {r: float(ARGS["halo"]) * weight(r) for r in todo}
    # Decoupling-style parts connect only to high-fanout (supply) nets. Assign each one to an
    # IC on the same supply, round-robin, and place it right after the ICs.
    def supply_nets(f):
        return {p.GetNetname() for p in f.Pads() if p.GetNetname() and pad_count[p.GetNetname()] > ARGS["max_net_pads"]}
    def pins(f):
        return {p.GetNumber() for p in f.Pads() if p.GetNumber()}
    ics = [r for r, f in by_ref.items() if len(pins(f)) >= 4]
    ic_supplies = {r: supply_nets(by_ref[r]) for r in ics}
    load = {r: 0 for r in ics}
    anchor = {}
    for r in sorted(todo):
        f = by_ref[r]
        if nets[r] or len(pins(f)) < 2 or r in ics:
            continue
        sup = supply_nets(f)
        cands = [ic for ic in ics if ic_supplies[ic] & sup and len(ic_supplies[ic] & sup) >= min(2, len(sup))]
        if cands:
            same = [c for c in cands if r in group_of and group_of.get(c) is group_of[r]]
            ic = min(same or cands, key=lambda c: (load[c], c))
            load[ic] += 1
            anchor[r] = ic
    # explicit proximity rules (feedback networks, timing parts, reference caps): same treatment
    # as decoupling, aimed at the target's pad on the lowest-fanout net they share
    supply_decap = set(anchor)   # supply decoupling gets first pick of the spots at its IC
    for r, t in ARGS.get("near", {}).items():
        if r in todo and t in by_ref:
            anchor[r] = t
    while todo:
        def score(r):
            # large parts (ICs, connectors) first, then decoupling parts next to their IC,
            # then everything else by how many signal links it has to placed parts
            links = sum(len(nets[r] & nets[q]) for q in placed_c)
            dec = r in anchor and anchor[r] in placed_c
            return (area(by_ref[r]) >= ARGS["big_area"], dec, dec and r in supply_decap, links, area(by_ref[r]))
        r = max(sorted(todo), key=score)
        todo.discard(r)
        fp = by_ref[r]
        HALO["cur"] = halo_of.get(r, 0.0)
        decap = r in anchor and anchor[r] in placed_c
        HALO["tight"] = decap
        if decap:
            HALO["cur"] = 0.0   # decoupling goes hard against its IC's supply pins, inside its halo
        peers = net_points(r)
        if decap:
            peers = [(anchor_point(r, anchor[r]), 1)]
        if peers:
            wsum = sum(w for _, w in peers)
            tx = sum(c[0] * w for c, w in peers) / wsum
            ty = sum(c[1] * w for c, w in peers) / wsum
        else:
            tx, ty = (ol[0] + ol[2]) / 2, (ol[1] + ol[3]) / 2
        grp = group_of.get(r)
        if grp:
            gw = ARGS["group_pull"] if peers and not (r in anchor and anchor[r] in placed_c) else 1.0
            if r in anchor and anchor[r] in placed_c:
                gw = 0.0
            tx, ty = tx * (1 - gw) + grp[5][0] * gw, ty * (1 - gw) + grp[5][1] * gw
        sides_try = [sd for sd in (grp[4] if grp else ARGS["auto_sides"])]
        best = None
        rots = [0, 90] if ARGS["try_rotations"] else [fp.GetOrientationDegrees()]
        for si, sd in enumerate(sides_try):
            for rot in rots:
                set_side(fp, sd)
                fp.SetOrientationDegrees(rot)
                b = courtyard_bbox(fp)
                w, h = b[2] - b[0], b[3] - b[1]
                occ = side_of(fp)
                # contact-point candidates: flush with the outline or with an existing
                # courtyard (plus a coarse grid near the target), so free space stays compact
                lo_x, hi_x = ol[0] + m, ol[2] - m - w
                lo_y, hi_y = ol[1] + m, ol[3] - m - h
                if grp:
                    lo_x, hi_x = max(lo_x, grp[0]), min(hi_x, grp[2] - w)
                    lo_y, hi_y = max(lo_y, grp[1]), min(hi_y, grp[3] - h)
                    if lo_x > hi_x + 1e-6 or lo_y > hi_y + 1e-6:
                        continue
                xs, ys = {lo_x, hi_x}, {lo_y, hi_y}
                for q in rects:
                    if not (occ == "both" or q[4] == "both" or q[4] == occ):
                        continue
                    if abs((q[0] + q[2]) / 2 - tx) > ARGS["search_radius"] or abs((q[1] + q[3]) / 2 - ty) > ARGS["search_radius"]:
                        continue
                    xs.update((q[2] + gap, q[0] - gap - w, q[0], q[2] - w))
                    ys.update((q[3] + gap, q[1] - gap - h, q[1], q[3] - h))
                for i in range(-8, 9):
                    xs.add(round((tx - w / 2 + i * step * 4) / step) * step)
                    ys.add(round((ty - h / 2 + i * step * 4) / step) * step)
                if ARGS["spread_weight"] > 0:
                    # coarse whole-board grid so empty regions are reachable, not only
                    # spots touching already-placed parts
                    cg = ARGS["spread_grid"]
                    xs.update(lo_x + i * cg for i in range(int((hi_x - lo_x) / cg) + 1))
                    ys.update(lo_y + j * cg for j in range(int((hi_y - lo_y) / cg) + 1))
                xs = [x for x in xs if lo_x - 1e-6 <= x <= hi_x + 1e-6]
                ys = [y for y in ys if lo_y - 1e-6 <= y <= hi_y + 1e-6]
                cands = sorted(((x + w / 2 - tx) ** 2 + (y + h / 2 - ty) ** 2, x, y) for x in xs for y in ys)
                if not cands:
                    continue
                # gather the nearest free spots, then trade distance against local crowding
                found = []
                for d2, x, y in cands:
                    base = d2 ** 0.5 + si * ARGS["side_penalty"]
                    if best is not None and base >= best[0]:
                        break
                    if free((x, y, x + w, y + h), occ):
                        found.append((base, x, y))
                        if len(found) >= ARGS["spread_candidates"] or ARGS["spread_weight"] <= 0 or decap:
                            break
                R = ARGS["spread_radius"]
                for base, x, y in found:
                    cost = base
                    if ARGS["spread_weight"] > 0 and not decap:
                        wx0, wy0, wx1, wy1 = x - R, y - R, x + w + R, y + h + R
                        win = (wx1 - wx0) * (wy1 - wy0)
                        occd = 0.0
                        for q in rects:
                            if not (occ == "both" or q[4] == "both" or q[4] == occ):
                                continue
                            ox_ = min(wx1, q[2]) - max(wx0, q[0])
                            oy_ = min(wy1, q[3]) - max(wy0, q[1])
                            if ox_ > 0 and oy_ > 0:
                                occd += ox_ * oy_
                        cost += ARGS["spread_weight"] * occd / win
                    if best is None or cost < best[0]:
                        best = (cost, sd, rot, x, y, w, h, occ)
        for scope in ((grp, None) if grp else (None,)):
          if best is not None:
              break
          if grp and scope is None and HALO["cur"] > 0:
              break   # retry without halo inside the group before spilling out of it
          if grp and scope is None:
              group_spill.append(r)
          # fallback: exhaustive grid scan, nearest free spot to the target
          for si, sd in enumerate(sides_try if scope else ARGS["auto_sides"]):
                for rot in rots:
                    set_side(fp, sd)
                    fp.SetOrientationDegrees(rot)
                    b = courtyard_bbox(fp)
                    w, h = b[2] - b[0], b[3] - b[1]
                    occ = side_of(fp)
                    bx0, by0, bx1, by1 = (max(ol[0] + m, scope[0]), max(ol[1] + m, scope[1]),
                                          min(ol[2] - m, scope[2]), min(ol[3] - m, scope[3])) if scope else                                          (ol[0] + m, ol[1] + m, ol[2] - m, ol[3] - m)
                    gx = [bx0 + i * step for i in range(int((bx1 - bx0 - w) / step) + 1)]
                    gy = [by0 + j * step for j in range(int((by1 - by0 - h) / step) + 1)]
                    for d2, x, y in sorted(((x + w / 2 - tx) ** 2 + (y + h / 2 - ty) ** 2, x, y) for x in gx for y in gy):
                        cost = d2 ** 0.5 + si * ARGS["side_penalty"]
                        if best is not None and cost >= best[0]:
                            break
                        if free((x, y, x + w, y + h), occ):
                            best = (cost, sd, rot, x, y, w, h, occ)
                            break
        if best is None and HALO["cur"] > 0:
            HALO["cur"] = 0.0
            todo.add(r)
            halo_of[r] = 0.0
            continue
        if best is None:
            overflow.append(r)
            continue
        _, sd, rot, x, y, w, h, occ = best
        set_side(fp, sd)
        place_center(fp, x + w / 2, y + h / 2, rot)
        rects.append((x, y, x + w, y + h, occ, HALO["cur"]))
        placed_c[r] = (x + w / 2, y + h / 2)
        done.add(r)
board.Save(ARGS["pcb"])
emit({"placed": len(done), "overflow": overflow, "unknown_refs": unknown,
      "decoupling_anchors": len(globals().get("anchor", {})),
      "group_spill": sorted(set(group_spill)),
      "routing_halo_mm": round(max(globals().get("halo_of", {0: 0}).values() or [0]), 3),
      "unplaced": sorted(r for r in by_ref if r not in done),
      "back_side": sorted(r for r, f in by_ref.items() if f.IsFlipped())})
'''


def place_footprints(
    pcb_path: str,
    fixed: Optional[Dict[str, List[float]]] = None,
    regions: Optional[List[Dict[str, Any]]] = None,
    keep_refs: Optional[List[str]] = None,
    auto_place_rest: bool = True,
    gap_mm: float = 0.8,
    step_mm: float = 0.25,
    margin_mm: float = 1.0,
    relative_to_outline: bool = True,
    keepouts: Optional[List[Dict[str, Any]]] = None,
    auto_sides: Optional[List[str]] = None,
    try_rotations: bool = True,
    strategy: str = "connectivity",
    max_net_pads: int = 12,
    side_penalty_mm: float = 2.0,
    routing_halo: Any = "auto",
    target_utilization: float = 0.8,
    spread_weight_mm: float = 6.0,
    groups: Optional[List[Dict[str, Any]]] = None,
    group_pull: float = 0.35,
    near: Optional[Dict[str, str]] = None,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Deterministic placement from a plan, using exact courtyards, on one or both board sides.

    Args:
        pcb_path: Board to modify.
        fixed: {ref: [cx, cy, rot, side]} courtyard-centre positions (mm, board-relative by default);
            side is "F" (default) or "B".
        regions: [{"rect": [x0, y0, x1, y1], "refs": [...], "rotation": 0, "rotations": {ref: deg},
            "side": "F" | "B", "sides": ["B", "F"]}]; refs are first-fit packed in the given order.
        keepouts: [{"rect": [x0, y0, x1, y1], "side": "F" | "B" | "both"}] areas nothing is packed into
            (e.g. a lens holder base). Through-hole parts block both sides automatically.
        auto_sides: Sides tried, in order, for auto-placed parts (default ["F"]).
        try_rotations: Also try 90 degrees for auto-placed parts.
        strategy: "connectivity" (default) places each remaining part as close as possible to the
            parts it shares signal nets with; "pack" first-fit packs largest-first.
        max_net_pads: Nets with more pads than this (supplies, ground, references) are ignored
            when clustering. side_penalty_mm: extra cost per step down the auto_sides list.
        routing_halo: Extra clearance reserved around each auto-placed part for routing, in mm,
            scaled by sqrt(pins/4) (0.5x-2x). "auto" sizes it so the parts spread over about
            target_utilization of the free area, so a bigger board actually gets used. 0 = dense.
        spread_weight_mm: Crowding penalty. Each candidate spot costs its distance to the part's
            connected neighbours plus spread_weight_mm x (occupied fraction of a 2.5 mm window
            around it), so parts drift into empty board regions instead of piling up next to the
            fixed parts. 0 disables it (pure nearest-fit clustering).
        groups: Functional groups for connectivity placement: [{"refs": [...], "rect": [x0, y0, x1, y1],
            "sides": ["B"], "target": [x, y]}]. Members are still placed next to the parts they share
            signal nets with, but only inside their rect and on their sides; decoupling parts stick to
            an IC of their own group. A member that cannot fit spills to the whole board and is listed
            in "group_spill".
        near: {ref: target_ref} proximity rules, e.g. TIA feedback R/C -> op-amp, one-shot timing R/C
            -> its IC, reference caps -> the reference. Placed right after the target, as close as
            possible to the target's pad on the net they share (like decoupling caps, which are
            matched to an IC of their own group automatically).
        group_pull: 0-1, how strongly members are pulled toward their group target versus their
            connected neighbours.
        keep_refs: Footprints left where they are but treated as obstacles.
        auto_place_rest: First-fit pack every remaining footprint inside the outline (largest first).
        gap_mm: Courtyard-to-courtyard gap. step_mm: search grid for packing.
        margin_mm: Keep-in margin from the outline for auto-placed parts.
        relative_to_outline: Coordinates are relative to the outline's top-left corner.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    res = run_pcbnew(_PLACE_JOB, {
        "pcb": str(p_path), "fixed": fixed or {}, "regions": regions or [], "keep": keep_refs or [],
        "auto_rest": auto_place_rest, "gap": gap_mm, "step": step_mm, "margin": margin_mm,
        "relative": relative_to_outline, "keepouts": keepouts or [], "auto_sides": auto_sides or ["F"],
        "try_rotations": try_rotations, "strategy": strategy, "max_net_pads": max_net_pads,
        "side_penalty": side_penalty_mm, "search_radius": 1e9, "big_area": 6.0,
        "halo": routing_halo, "target_util": target_utilization,
        "groups": groups or [], "group_pull": group_pull, "near": near or {},
        "spread_weight": spread_weight_mm, "spread_radius": 2.5, "spread_candidates": 40, "spread_grid": 1.0,
    })
    check = check_placement_overlaps(str(p_path), min_clearance_mm=min(gap_mm, 0.25))
    return {"status": "success" if not res["overflow"] else "partial", **res,
            "placement_score": check["placement_score"], "is_pass": check["is_pass"],
            "edge_violations": check["edge_violations"], "overlaps": check["overlaps"],
            "drc_courtyard_violations": check["drc_courtyard_violations"],
            "hint": "Enlarge regions or the board for overflowing refs." if res["overflow"] else ""}


# ------------------------------------------------------------------ silkscreen
_SILK_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
modes = ARGS["modes"]
size, thick = mm(ARGS["size"]), mm(ARGS["thickness"])
out = {}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    field = fp.Reference()
    name = str(fp.GetFPID().GetLibItemName())
    lib = str(fp.GetFPID().GetLibNickname())
    if ref in ARGS["hide"] or (ARGS["hide_small_smd"] and any(s in name for s in ("0201", "0402", "0603"))):
        field.SetVisible(False); out[ref] = "hidden"; continue
    if lib.startswith("MountingHole") or ref.startswith(("H", "MH")):
        field.SetVisible(False); out[ref] = "hidden"; continue
    mode = modes.get(ref)
    if mode is None:
        mode = "top" if (ref.startswith(("J", "TP", "P")) or "Connector" in lib or "TestPoint" in lib) else "center"
    if mode == "hidden":
        field.SetVisible(False); out[ref] = "hidden"; continue
    field.SetVisible(True)
    field.SetLayer(pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS)
    field.SetTextSize(pcbnew.VECTOR2I(size, size))
    field.SetTextThickness(thick)
    field.SetTextAngleDegrees(0)
    field.SetKeepUpright(True)
    l, t, r, b = courtyard_bbox(fp)
    cx, cy, h = (l + r) / 2, (t + b) / 2, ARGS["size"]
    pos = {"center": (cx, cy), "top": (cx, t - h * 0.8), "bottom": (cx, b + h * 0.8),
           "left": (l - h * 0.6 - len(ref) * h * 0.35, cy), "right": (r + h * 0.6 + len(ref) * h * 0.35, cy)}[mode]
    field.SetPosition(pcbnew.VECTOR2I(mm(pos[0]), mm(pos[1])))
    out[ref] = mode
board.Save(ARGS["pcb"])
emit(out)
'''

_SILK_TYPES = ("silk_overlap", "silk_over_copper", "silk_edge_clearance", "text_height", "text_thickness")
_ORDER = ["center", "top", "bottom", "left", "right", "hidden"]


def _silk_offenders(pcb: Path) -> Dict[str, int]:
    out = get_render_cache_dir() / f"{pcb.stem}_silk_{int(time.time() * 1000)}.json"
    subprocess.run([find_kicad_cli(), "pcb", "drc", "--format", "json", "--severity-all", "-o", str(out), str(pcb)],
                   capture_output=True, text=True, check=False)
    data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    bad: Dict[str, int] = {}
    for v in data.get("violations", []):
        if v.get("type") not in _SILK_TYPES:
            continue
        for it in v.get("items", []):
            m = re.match(r"Reference field of (\S+)", it.get("description", ""))
            if m:
                bad[m.group(1)] = bad.get(m.group(1), 0) + 1
    return bad


def sanitize_silkscreen(
    pcb_path: str,
    hide_passives: bool = True,
    min_pad_clearance_mm: float = 0.50,
    text_size_mm: float = 0.8,
    text_thickness_mm: float = 0.15,
    hide_refs: Optional[List[str]] = None,
    max_passes: int = 5,
    hide_unresolved: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Arrange reference designators so KiCad DRC reports no silkscreen problems.

    Refs start centred on the part body (connectors/test points above), then any ref that
    DRC flags (silk overlap, silk over copper, edge clearance, text size) is tried at
    top/bottom/left/right, and hidden as a last resort (F.Fab keeps it for assembly).

    Args:
        pcb_path: Board to modify.
        hide_passives: Hide refs of tiny SMD passives (0201/0402/0603) outright.
        min_pad_clearance_mm: Reported only; DRC's own silk clearance rule is authoritative.
        text_size_mm / text_thickness_mm: Reference text size (DRC minimum is usually 0.8/0.15).
        hide_refs: References to hide unconditionally.
        max_passes: DRC/move iterations.
        hide_unresolved: Hide refs still flagged after max_passes.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    modes: Dict[str, str] = {}
    args = {"pcb": str(p_path), "size": text_size_mm, "thickness": text_thickness_mm,
            "hide": hide_refs or [], "hide_small_smd": hide_passives}
    placed = run_pcbnew(_SILK_JOB, {**args, "modes": modes})
    history = []
    for _ in range(max_passes):
        bad = _silk_offenders(p_path)
        history.append(len(bad))
        if not bad:
            break
        for ref in bad:
            cur = modes.get(ref, placed.get(ref, "center"))
            nxt = _ORDER[min(_ORDER.index(cur) + 1, len(_ORDER) - 1)] if cur in _ORDER else "top"
            if nxt == "hidden" and not hide_unresolved:
                nxt = cur
            modes[ref] = nxt
        placed = run_pcbnew(_SILK_JOB, {**args, "modes": modes})
    remaining = _silk_offenders(p_path)
    hidden = sorted(r for r, m in placed.items() if m == "hidden")
    return {
        "status": "success" if not remaining else "partial",
        "board": str(p_path),
        "hidden_references_count": len(hidden),
        "hidden_references": hidden,
        "moved_references": {r: m for r, m in modes.items() if m != "hidden"},
        "offenders_per_pass": history,
        "remaining_silk_offenders": remaining,
        "min_pad_clearance_mm": min_pad_clearance_mm,
    }

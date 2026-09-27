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
def place_center(fp, cx, cy, rot):
    fp.SetOrientationDegrees(rot)
    b = courtyard_bbox(fp)
    fp.Move(pcbnew.VECTOR2I(mm(cx - (b[0] + b[2]) / 2), mm(cy - (b[1] + b[3]) / 2)))
rects, done, overflow, unknown = [], set(), [], []
for ref, spec in ARGS["fixed"].items():
    fp = by_ref.get(ref)
    if fp is None:
        unknown.append(ref); continue
    x, y = spec[0] + ox, spec[1] + oy
    place_center(fp, x, y, spec[2] if len(spec) > 2 else fp.GetOrientationDegrees())
    rects.append(courtyard_bbox(fp)); done.add(ref)
for ref, fp in by_ref.items():
    if ref not in done and ref in ARGS["keep"]:
        rects.append(courtyard_bbox(fp)); done.add(ref)
def free(a):
    return all(a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1] for b in rects)
def pack(fp, x0, y0, x1, y1, rot, step):
    fp.SetOrientationDegrees(rot)
    b = courtyard_bbox(fp)
    w, h = b[2] - b[0], b[3] - b[1]
    y = y0
    while y + h <= y1 + 1e-6:
        x = x0
        while x + w <= x1 + 1e-6:
            if free((x, y, x + w, y + h)):
                place_center(fp, x + w / 2, y + h / 2, rot)
                rects.append((x, y, x + w, y + h))
                return True
            x += step
        y += step
    return False
for reg in ARGS["regions"]:
    x0, y0, x1, y1 = [v + (ox if i % 2 == 0 else oy) for i, v in enumerate(reg["rect"])]
    for ref in reg["refs"]:
        fp = by_ref.get(ref)
        if fp is None:
            unknown.append(ref); continue
        if ref in done:
            continue
        rot = reg.get("rotations", {}).get(ref, reg.get("rotation", 0))
        (done.add(ref) if pack(fp, x0, y0, x1, y1, rot, ARGS["step"]) else overflow.append(ref))
if ARGS["auto_rest"] and ol:
    rest = [fp for r, fp in by_ref.items() if r not in done]
    rest.sort(key=lambda f: (-(lambda b: (b[2] - b[0]) * (b[3] - b[1]))(courtyard_bbox(f)), f.GetReference()))
    m = ARGS["margin"]
    for fp in rest:
        (done.add(fp.GetReference()) if pack(fp, ol[0] + m, ol[1] + m, ol[2] - m, ol[3] - m,
                                            fp.GetOrientationDegrees(), ARGS["step"]) else overflow.append(fp.GetReference()))
board.Save(ARGS["pcb"])
emit({"placed": len(done), "overflow": overflow, "unknown_refs": unknown,
      "unplaced": sorted(r for r in by_ref if r not in done)})
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
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Deterministic placement from a plan, using exact courtyards.

    Args:
        pcb_path: Board to modify.
        fixed: {ref: [cx, cy, rot]} courtyard-centre positions (mm, board-relative by default).
        regions: [{"rect": [x0, y0, x1, y1], "refs": [...], "rotation": 0, "rotations": {ref: deg}}];
            refs are first-fit packed top-left to bottom-right in the given order.
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
        "relative": relative_to_outline,
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

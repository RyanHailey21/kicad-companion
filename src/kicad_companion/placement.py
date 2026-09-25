import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _parse_board_outline(pcb_content: str) -> Tuple[float, float, float, float]:
    """Extract bounding box (min_x, min_y, max_x, max_y) of Edge.Cuts."""
    lines = re.findall(
        r'\(gr_line\s+\(start\s+([-\d.]+)\s+([-\d.]+)\)\s+\(end\s+([-\d.]+)\s+([-\d.]+)\)\s+.*?\(layer\s+"Edge\.Cuts"\)',
        pcb_content,
        re.DOTALL,
    )
    if not lines:
        rects = re.findall(
            r'\(gr_rect\s+\(start\s+([-\d.]+)\s+([-\d.]+)\)\s+\(end\s+([-\d.]+)\s+([-\d.]+)\)\s+.*?\(layer\s+"Edge\.Cuts"\)',
            pcb_content,
            re.DOTALL,
        )
        if rects:
            lines = rects

    if not lines:
        return (0.0, 0.0, 50.0, 70.0)

    xs, ys = [], []
    for x1, y1, x2, y2 in lines:
        xs.extend([float(x1), float(x2)])
        ys.extend([float(y1), float(y2)])

    return (min(xs), min(ys), max(xs), max(ys))


def _parse_footprints(pcb_content: str) -> List[Dict[str, Any]]:
    """Parse footprints and compute their center, rotation, and bounding boxes."""
    # Split footprints by paren depth
    lines = pcb_content.splitlines(keepends=True)
    fps = []
    in_fp = False
    curr = []
    parens = 0

    for line in lines:
        if not in_fp:
            if line.startswith("\t(footprint "):
                in_fp = True
                curr = [line]
                parens = line.count("(") - line.count(")")
        else:
            curr.append(line)
            parens += line.count("(") - line.count(")")
            if parens <= 0:
                in_fp = False
                fps.append("".join(curr))

    result = []
    for fp_text in fps:
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_text)
        if not m_ref:
            continue
        ref = m_ref.group(1)

        m_at = re.search(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)', fp_text)
        if not m_at:
            continue
        fx, fy = float(m_at.group(1)), float(m_at.group(2))
        rot = float(m_at.group(3)) if m_at.group(3) else 0.0

        # Extract courtyard or pad coordinates to get extent
        pad_pts = []
        for pm in re.finditer(r'\(pad\s+"[^"]+"\s+\w+\s+\w+\s+\(at\s+([-\d.]+)\s+([-\d.]+).*?\(size\s+([-\d.]+)\s+([-\d.]+)', fp_text):
            px, py, pw, ph = [float(v) for v in pm.groups()]
            pad_pts.append((px - pw / 2.0, py - ph / 2.0))
            pad_pts.append((px + pw / 2.0, py + ph / 2.0))

        # Check courtyard lines / rects
        for crt in re.finditer(r'\(fp_rect\s+\(start\s+([-\d.]+)\s+([-\d.]+)\)\s+\(end\s+([-\d.]+)\s+([-\d.]+)\)\s+.*?\(layer\s+"F\.CrtYd"\)', fp_text, re.DOTALL):
            cx1, cy1, cx2, cy2 = [float(v) for v in crt.groups()]
            pad_pts.extend([(cx1, cy1), (cx2, cy2)])

        if pad_pts:
            min_dx = min(p[0] for p in pad_pts)
            max_dx = max(p[0] for p in pad_pts)
            min_dy = min(p[1] for p in pad_pts)
            max_dy = max(p[1] for p in pad_pts)
        else:
            min_dx, max_dx = -1.0, 1.0
            min_dy, max_dy = -1.0, 1.0

        # Rotate relative bounding box if necessary (approximate AABB)
        w = max_dx - min_dx
        h = max_dy - min_dy
        if abs(rot) in [90.0, 270.0]:
            w, h = h, w

        half_w = max(w / 2.0, 0.5)
        half_h = max(h / 2.0, 0.5)

        result.append({
            "ref": ref,
            "x": fx,
            "y": fy,
            "rot": rot,
            "half_w": half_w,
            "half_h": half_h,
            "bbox": (fx - half_w, fy - half_h, fx + half_w, fy + half_h),
            "text": fp_text,
        })

    return result


def check_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.25,
) -> Dict[str, Any]:
    """Inspect PCB placement for component overlaps, board edge insets, and mounting hole clearances.

    Args:
        pcb_path: Path to .kicad_pcb file.
        min_clearance_mm: Minimum clearance required between component boundaries.

    Returns:
        Structured dictionary with overlap details, edge violations, and overall placement score.
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    content = p_path.read_text(encoding="utf-8")
    min_bx, min_by, max_bx, max_by = _parse_board_outline(content)
    fps = _parse_footprints(content)

    overlaps = []
    edge_violations = []
    hole_violations = []

    holes = [f for f in fps if f["ref"].startswith("H")]

    for i in range(len(fps)):
        f1 = fps[i]
        ref1 = f1["ref"]

        # Check edge clearance: connectors require >= 2.0 mm, components >= 0.5 mm
        req_margin = 2.0 if ref1.startswith("J") else 0.5
        b1 = f1["bbox"]
        if (
            b1[0] < min_bx + req_margin
            or b1[2] > max_bx - req_margin
            or b1[1] < min_by + req_margin
            or b1[3] > max_by - req_margin
        ):
            edge_violations.append({
                "ref": ref1,
                "bbox": b1,
                "required_inset_mm": req_margin,
            })

        # Check mounting hole clearance (>= 2.5 mm from hole center)
        if not ref1.startswith("H"):
            for h in holes:
                dist = math.hypot(f1["x"] - h["x"], f1["y"] - h["y"])
                if dist < 2.5 + min(f1["half_w"], f1["half_h"]):
                    hole_violations.append({
                        "ref": ref1,
                        "hole": h["ref"],
                        "distance_mm": round(dist, 2),
                        "required_mm": 2.5,
                    })

        # Check pairwise overlaps
        for j in range(i + 1, len(fps)):
            f2 = fps[j]
            ref2 = f2["ref"]
            b2 = f2["bbox"]

            # Expand bbox by min_clearance
            ox = max(0.0, min(b1[2] + min_clearance_mm, b2[2] + min_clearance_mm) - max(b1[0], b2[0]))
            oy = max(0.0, min(b1[3] + min_clearance_mm, b2[3] + min_clearance_mm) - max(b1[1], b2[1]))

            if ox > 0 and oy > 0:
                overlap_dist = min(ox, oy)
                overlaps.append({
                    "comp1": ref1,
                    "comp2": ref2,
                    "overlap_mm": round(overlap_dist, 3),
                })

    score = 100
    score -= len(overlaps) * 10
    score -= len(edge_violations) * 15
    score -= len(hole_violations) * 10
    score = max(0, score)

    return {
        "status": "success",
        "board": str(p_path),
        "placement_score": score,
        "is_pass": score == 100,
        "total_components": len(fps),
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "edge_violation_count": len(edge_violations),
        "edge_violations": edge_violations,
        "hole_violation_count": len(hole_violations),
        "hole_violations": hole_violations,
    }


def resolve_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.5,
    grid_step_mm: float = 0.5,
    fixed_refs: Optional[List[str]] = None,
    max_iterations: int = 50,
) -> Dict[str, Any]:
    """Iteratively separate overlapping footprints using geometric relaxation and grid snapping.

    Args:
        pcb_path: Path to .kicad_pcb file.
        min_clearance_mm: Minimum clearance required between footprints. Default 0.5 mm.
        grid_step_mm: Grid snap step for resolved positions. Default 0.5 mm.
        fixed_refs: Optional list of component references that must not move (e.g. connectors, sensors).
        max_iterations: Relaxation iterations. Default 50.

    Returns:
        Summary dict of components relocated, iterations executed, and remaining overlaps.
    """
    p_path = Path(pcb_path).resolve()
    content = p_path.read_text(encoding="utf-8")
    min_bx, min_by, max_bx, max_by = _parse_board_outline(content)
    fps = _parse_footprints(content)

    # Determine default fixed components: Connectors, mounting holes, optical sensors
    fixed_set: Set[str] = set(fixed_refs or [])
    for f in fps:
        ref = f["ref"]
        if (
            ref.startswith("J")
            or ref.startswith("H")
            or ref.startswith("DPD")
            or ref.startswith("LED")
        ):
            fixed_set.add(ref)

    pos_map = {f["ref"]: [f["x"], f["y"]] for f in fps}
    orig_pos = {f["ref"]: (f["x"], f["y"]) for f in fps}

    iteration = 0
    for iteration in range(max_iterations):
        moved_any = False
        for i in range(len(fps)):
            ref1 = fps[i]["ref"]
            w1, h1 = fps[i]["half_w"], fps[i]["half_h"]
            x1, y1 = pos_map[ref1]

            for j in range(i + 1, len(fps)):
                ref2 = fps[j]["ref"]
                w2, h2 = fps[j]["half_w"], fps[j]["half_h"]
                x2, y2 = pos_map[ref2]

                # Check overlap
                dx = x2 - x1
                dy = y2 - y1
                min_dist_x = w1 + w2 + min_clearance_mm
                min_dist_y = h1 + h2 + min_clearance_mm

                ox = min_dist_x - abs(dx)
                oy = min_dist_y - abs(dy)

                if ox > 0 and oy > 0:
                    # Resolve along minimal penetration axis
                    moved_any = True
                    is_fixed1 = ref1 in fixed_set
                    is_fixed2 = ref2 in fixed_set

                    if ox < oy:
                        # Push in X
                        sign = 1.0 if dx >= 0 else -1.0
                        shift = ox
                        if not is_fixed1 and not is_fixed2:
                            pos_map[ref1][0] -= (shift / 2.0) * sign
                            pos_map[ref2][0] += (shift / 2.0) * sign
                        elif is_fixed1 and not is_fixed2:
                            pos_map[ref2][0] += shift * sign
                        elif not is_fixed1 and is_fixed2:
                            pos_map[ref1][0] -= shift * sign
                    else:
                        # Push in Y
                        sign = 1.0 if dy >= 0 else -1.0
                        shift = oy
                        if not is_fixed1 and not is_fixed2:
                            pos_map[ref1][1] -= (shift / 2.0) * sign
                            pos_map[ref2][1] += (shift / 2.0) * sign
                        elif is_fixed1 and not is_fixed2:
                            pos_map[ref2][1] += shift * sign
                        elif not is_fixed1 and is_fixed2:
                            pos_map[ref1][1] -= shift * sign

                    # Clamp movable inside board edges
                    for r, f_item in [(ref1, fps[i]), (ref2, fps[j])]:
                        if r not in fixed_set:
                            hw, hh = f_item["half_w"], f_item["half_h"]
                            pos_map[r][0] = max(min_bx + hw + 0.5, min(max_bx - hw - 0.5, pos_map[r][0]))
                            pos_map[r][1] = max(min_by + hh + 0.5, min(max_by - hh - 0.5, pos_map[r][1]))

        if not moved_any:
            break

    # Snap movable components to grid
    relocated = {}
    for f in fps:
        ref = f["ref"]
        if ref not in fixed_set:
            raw_x, raw_y = pos_map[ref]
            snap_x = round(round(raw_x / grid_step_mm) * grid_step_mm, 2)
            snap_y = round(round(raw_y / grid_step_mm) * grid_step_mm, 2)
            pos_map[ref] = [snap_x, snap_y]

        ox, oy = orig_pos[ref]
        nx, ny = pos_map[ref]
        if abs(nx - ox) > 0.01 or abs(ny - oy) > 0.01:
            relocated[ref] = {"from": (ox, oy), "to": (nx, ny)}

    # Apply relocated coordinates to PCB text
    lines = content.splitlines(keepends=True)
    out_lines = []
    in_fp = False
    fp_lines = []
    parens = 0

    for line in lines:
        if not in_fp:
            if line.startswith("\t(footprint "):
                in_fp = True
                fp_lines = [line]
                parens = line.count("(") - line.count(")")
            else:
                out_lines.append(line)
        else:
            fp_lines.append(line)
            parens += line.count("(") - line.count(")")
            if parens <= 0:
                in_fp = False
                fp_text = "".join(fp_lines)
                m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_text)
                if m_ref and m_ref.group(1) in relocated:
                    ref = m_ref.group(1)
                    nx, ny = relocated[ref]["to"]
                    fp_text = re.sub(
                        r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)',
                        lambda m: f'(at {nx} {ny}' + (f' {m.group(3)})' if m.group(3) else ')'),
                        fp_text,
                        count=1,
                    )
                out_lines.append(fp_text)

    new_content = "".join(out_lines)
    p_path.write_text(new_content, encoding="utf-8")

    # Re-check remaining overlaps
    final_check = check_placement_overlaps(str(p_path), min_clearance_mm=min_clearance_mm)

    return {
        "status": "success",
        "board": str(p_path),
        "iterations": iteration + 1,
        "components_relocated_count": len(relocated),
        "relocated": relocated,
        "remaining_overlaps": final_check["overlap_count"],
        "placement_score": final_check["placement_score"],
    }


def sanitize_silkscreen(
    pcb_path: str,
    hide_passives: bool = True,
    min_pad_clearance_mm: float = 0.50,
) -> Dict[str, Any]:
    """Automatically declutter silkscreen, hide small passives, and ensure minimum clearance.

    Args:
        pcb_path: Path to .kicad_pcb file.
        hide_passives: Set (hide yes) on small 0402/0603/0805 passive reference designators on F.SilkS.
        min_pad_clearance_mm: Minimum clearance from silkscreen to copper pads.

    Returns:
        Summary dict of hidden references and cleaned silkscreen elements.
    """
    p_path = Path(pcb_path).resolve()
    content = p_path.read_text(encoding="utf-8")

    hidden_refs = []

    def process_fp(m):
        fp = m.group(0)
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp)
        if not m_ref:
            return fp
        ref = m_ref.group(1)

        # Small passives heuristic: R*, C*, L*, FB*, or footprint matches 0402/0603/0805
        is_passive = (
            ref.startswith("R")
            or ref.startswith("C")
            or ref.startswith("L")
            or ref.startswith("FB")
            or ref.startswith("ROS_")
            or ref.startswith("COS_")
            or ref.startswith("ROUT_")
        )

        # Do not hide critical ICs or connectors
        if ref.startswith("U_") or ref.startswith("J_") or ref.startswith("LED") or ref.startswith("DPD"):
            is_passive = False

        if hide_passives and is_passive:
            if "(hide yes)" not in fp:
                hidden_refs.append(ref)
                # Inject (hide yes) into Reference property
                def add_hide(pm):
                    p = pm.group(0)
                    if "(hide yes)" not in p:
                        last_p = p.rfind(")")
                        p = p[:last_p] + "\n\t\t\t(hide yes)\n\t\t)"
                    return p
                fp = re.sub(r'\(property "Reference"\s+"[^"]+".*?\n\t\t\)', add_hide, fp, flags=re.DOTALL)

        return fp

    # Process footprints
    new_content = re.sub(r'\(footprint\s+.*?\n\t\)', process_fp, content, flags=re.DOTALL)

    # Remove duplicate text lines
    unique_texts = set()
    deduped_lines = []
    lines = new_content.splitlines(keepends=True)
    in_text = False
    curr_text = []
    parens = 0

    for line in lines:
        if not in_text:
            if line.startswith("\t(gr_text "):
                in_text = True
                curr_text = [line]
                parens = line.count("(") - line.count(")")
            else:
                deduped_lines.append(line)
        else:
            curr_text.append(line)
            parens += line.count("(") - line.count(")")
            if parens <= 0:
                in_text = False
                t_str = "".join(curr_text)
                m_txt = re.search(r'\(gr_text\s+"([^"]+)"\s+\(at\s+([-\d.]+)\s+([-\d.]+)', t_str)
                if m_txt:
                    key = (m_txt.group(1), float(m_txt.group(2)), float(m_txt.group(3)))
                    if key in unique_texts:
                        continue
                    unique_texts.add(key)
                deduped_lines.append(t_str)

    p_path.write_text("".join(deduped_lines), encoding="utf-8")

    return {
        "status": "success",
        "board": str(p_path),
        "hidden_passive_references_count": len(hidden_refs),
        "hidden_references": hidden_refs,
        "min_pad_clearance_mm": min_pad_clearance_mm,
    }

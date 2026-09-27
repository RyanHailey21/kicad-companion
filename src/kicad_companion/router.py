import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import find_freerouting_jar, find_kicad_python
from .drc_triage import triage_pcb_drc
from .supervisor import guard_headless_write


def export_specctra_dsn(
    pcb_path: str,
    output_dsn: Optional[str] = None,
) -> str:
    """Export a KiCad PCB to Specctra DSN format using KiCad's bundled pcbnew engine.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        output_dsn: Optional output path for the .dsn file.

    Returns:
        Absolute path to the exported and sanitized .dsn file.
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    dsn_path = Path(output_dsn).resolve() if output_dsn else p_path.with_suffix(".dsn")
    py_exe = find_kicad_python()

    script = f"""
import pcbnew
board = pcbnew.LoadBoard(r"{p_path}")
ok = pcbnew.ExportSpecctraDSN(board, r"{dsn_path}")
if not ok:
    raise RuntimeError("pcbnew.ExportSpecctraDSN returned False")
"""
    proc = subprocess.run([py_exe, "-c", script], capture_output=True, text=True, check=False)
    if not dsn_path.is_file():
        raise RuntimeError(f"DSN export failed: {proc.stderr or proc.stdout}")

    # Sanitize DSN: strip Greek letters and non-ASCII that trip up Specctra parsers
    raw_text = dsn_path.read_text(encoding="utf-8", errors="replace")
    clean_text = re.sub(r"[ΩµΦ°]", "", raw_text)
    dsn_path.write_text(clean_text, encoding="utf-8")

    return str(dsn_path)


def run_freerouting(
    dsn_path: str,
    output_ses: Optional[str] = None,
    passes: int = 15,
    single_threaded: bool = True,
) -> str:
    """Run Freerouting in headless batch mode.

    Args:
        dsn_path: Path to the input .dsn file.
        output_ses: Optional path for the output .ses file.
        passes: Maximum autorouting passes. Default 15.
        single_threaded: Force '-mt 1' to avoid multi-threaded route optimizer bugs. Default True.

    Returns:
        Absolute path to the resulting .ses file.
    """
    d_path = Path(dsn_path).resolve()
    if not d_path.is_file():
        raise FileNotFoundError(f"DSN file not found: {dsn_path}")

    ses_path = Path(output_ses).resolve() if output_ses else d_path.with_suffix(".ses")
    jar_path = find_freerouting_jar()

    cmd = [
        "java",
        "-jar",
        jar_path,
        "-de",
        str(d_path),
        "-do",
        str(ses_path),
        "-mp",
        str(passes),
    ]

    # Freerouting v2.4.1 multi-threaded optimizer is broken and generates clearance violations
    if single_threaded:
        cmd.extend(["-mt", "1"])

    log_path = d_path.with_name(f"{d_path.stem}.freerouting.log")
    print(f"Executing Freerouting: {' '.join(cmd)}", file=sys.stderr)
    print(f"Streaming live progress to console and {log_path} ...", file=sys.stderr, flush=True)

    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            print(f"[Freerouting] {line.strip()}", file=sys.stderr, flush=True)
            log_file.write(line)
            log_file.flush()
        proc.wait()

    if not ses_path.is_file() or proc.returncode != 0:
        tail_lines = ""
        if log_path.is_file():
            tail_lines = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        err_msg = tail_lines or "Freerouting exited without producing SES"
        raise RuntimeError(f"Freerouting failed (code {proc.returncode}):\n{err_msg}")

    return str(ses_path)


def import_specctra_ses(
    pcb_path: str,
    ses_path: str,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Parse a Specctra SES file and inject routed tracks and vias into .kicad_pcb headlessly.

    Args:
        pcb_path: Path to target .kicad_pcb file.
        ses_path: Path to the Freerouting .ses file.

    Returns:
        Dict with track count, via count, and routed net count.
    """
    p_path = Path(pcb_path).resolve()
    s_path = Path(ses_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")
    if not s_path.is_file():
        raise FileNotFoundError(f"SES file not found: {ses_path}")
    guard_headless_write(str(p_path), allow_while_open)

    ses_content = s_path.read_text(encoding="utf-8")
    pcb_content = p_path.read_text(encoding="utf-8")

    # KiCad <= 9 boards reference nets by code from a top-level net table;
    # KiCad 10 boards have no table and name the net on every copper item.
    net_map = {}
    for code_str, name in re.findall(r'^\t\(net\s+(\d+)\s+"([^"]*)"\)', pcb_content, re.MULTILINE):
        net_map[name] = int(code_str)
    named_nets = not net_map

    def net_ref(name: str) -> str:
        if named_nets:
            return '(net "%s")' % name.replace('"', '\\"')
        return f"(net {net_map.get(name, 0)})"

    # Parse padstacks from SES
    via_dims = {} # name -> (size_mm, drill_mm)
    for name in re.findall(r'\(padstack\s+"([^"]+)"', ses_content):
        m = re.search(r'_(\d+):(\d+)_um', name)
        if m:
            via_dims[name] = (float(m.group(1)) / 1000.0, float(m.group(2)) / 1000.0)
        else:
            via_dims[name] = (0.6, 0.3)

    # Specctra resolution: 1 unit = 0.1 um = 0.0001 mm
    # Y is negated relative to KiCad coordinates
    scale = 0.0001

    # Extract all routed nets from network_out
    idx = ses_content.find("(network_out")
    if idx == -1:
        net_chunks = []
    else:
        net_chunks = re.split(r"\n\s{4,8}\(net\s+", ses_content[idx:])

    track_lines = []
    via_lines = []
    routed_nets = set()

    for chunk in net_chunks[1:]:
        m_name = re.match(r'"((?:[^"\\]|\\.)*)"|(\S+)', chunk)
        if not m_name:
            continue
        net_name = m_name.group(1) if m_name.group(1) is not None else m_name.group(2)
        routed_nets.add(net_name)

        # 1. Wires: (wire (path LAYER WIDTH X1 Y1 X2 Y2 ...))
        wire_matches = re.findall(r"\(wire\s+\(path\s+(\S+)\s+(\d+)\s+([-\d\s]+?)\)", chunk, re.DOTALL)
        for layer, width_units, pts_str in wire_matches:
            w_mm = max(round(float(width_units) * scale, 4), 0.15)
            pts = [float(x) for x in pts_str.split()]
            coords = []
            for i in range(0, len(pts), 2):
                x_mm = round(pts[i] * scale, 4)
                y_mm = round(-pts[i + 1] * scale, 4) # Specctra Y is inverted
                coords.append((x_mm, y_mm))

            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                u = str(uuid.uuid4())
                track_lines.append(
                    f'\t(segment (start {x1} {y1}) (end {x2} {y2}) (width {w_mm}) (layer "{layer}") {net_ref(net_name)} (uuid "{u}"))'
                )

        # 2. Vias: (via "PADSTACK" X Y)
        via_matches = re.findall(r'\(via\s+"([^"]+)"\s+([-\d.]+)\s+([-\d.]+)', chunk)
        for padstack, vx_str, vy_str in via_matches:
            vx_mm = round(float(vx_str) * scale, 4)
            vy_mm = round(-float(vy_str) * scale, 4)
            size_mm, drill_mm = via_dims.get(padstack, (0.6, 0.3))
            u = str(uuid.uuid4())
            via_lines.append(
                f'\t(via (at {vx_mm} {vy_mm}) (size {size_mm}) (drill {drill_mm}) (layers "F.Cu" "B.Cu") {net_ref(net_name)} (uuid "{u}"))'
            )

    # Remove existing tracks and vias from pcb_content using paren counting
    lines = pcb_content.splitlines(keepends=True)
    clean_lines = []
    skip = False
    parens = 0
    for line in lines:
        if not skip:
            if line.startswith("\t(segment") or line.startswith("\t(via"):
                skip = True
                parens = line.count("(") - line.count(")")
                if parens <= 0:
                    skip = False
            else:
                clean_lines.append(line)
        else:
            parens += line.count("(") - line.count(")")
            if parens <= 0:
                skip = False
    pcb_content = "".join(clean_lines)

    # Insert new tracks and vias before the final closing ')'
    elements = track_lines + via_lines
    last_p = pcb_content.rfind(')')
    updated_pcb = pcb_content[:last_p] + "\n" + "\n".join(elements) + "\n)"
    p_path.write_text(updated_pcb, encoding="utf-8")

    return {
        "status": "success",
        "pcb_file": str(p_path),
        "total_segments": len(track_lines),
        "total_vias": len(via_lines),
        "routed_nets_count": len(routed_nets),
    }


def autoroute_board(
    pcb_path: str,
    passes: int = 15,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Execute end-to-end autorouting: export DSN -> run Freerouting -> import SES -> run DRC.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        passes: Maximum autorouting passes. Default 15.

    Returns:
        Dict with routing metrics, track/via counts, and DRC triage summary.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    dsn_path = export_specctra_dsn(str(p_path))
    ses_path = run_freerouting(dsn_path, passes=passes, single_threaded=True)
    import_res = import_specctra_ses(str(p_path), ses_path, allow_while_open=allow_while_open)
    drc_res = triage_pcb_drc(str(p_path), refill_zones=True)
    for tmp in (dsn_path, ses_path):
        Path(tmp).unlink(missing_ok=True)

    return {
        "status": "success",
        "routing": import_res,
        "drc": drc_res,
    }

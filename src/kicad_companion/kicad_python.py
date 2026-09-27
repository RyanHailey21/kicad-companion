"""Run small pcbnew programs in KiCad's bundled Python and exchange JSON with them.

Editing boards through pcbnew keeps UUIDs, net tables and file-format details correct
across KiCad versions, which regex surgery on .kicad_pcb text cannot guarantee.
"""
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .config import find_kicad_cli, find_kicad_python
from .sexp import read_netlist

_MARKER = "__KICAD_COMPANION_RESULT__"

_PRELUDE = r'''
import json, sys
import pcbnew
ARGS = json.load(open(sys.argv[1], encoding="utf-8"))
mm = pcbnew.FromMM
def to_mm(v): return round(pcbnew.ToMM(v), 4)
def emit(obj):
    sys.stdout.write("\n__KICAD_COMPANION_RESULT__" + json.dumps(obj) + "\n")
    sys.stdout.flush()
def courtyard_bbox(fp):
    """Exact courtyard bounding box in mm (falls back to pads/body)."""
    fp.BuildCourtyardCaches()
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        poly = fp.GetCourtyard(layer)
        if poly.OutlineCount():
            bb = poly.BBox()
            break
    else:
        bb = fp.GetBoundingBox(False)
    return (to_mm(bb.GetLeft()), to_mm(bb.GetTop()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))
def board_outline(board):
    bb = board.GetBoardEdgesBoundingBox()
    if bb.GetWidth() <= 0:
        return None
    return (to_mm(bb.GetLeft()), to_mm(bb.GetTop()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))
'''


def run_pcbnew(body: str, args: Dict[str, Any], timeout: int = 600) -> Dict[str, Any]:
    """Execute ``body`` (which calls ``emit(result)``) under KiCad's Python with ``ARGS`` bound."""
    py = find_kicad_python()
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "job.py"
        argf = Path(td) / "args.json"
        script.write_text(_PRELUDE + "\n" + body, encoding="utf-8")
        argf.write_text(json.dumps(args), encoding="utf-8")
        proc = subprocess.run([py, str(script), str(argf)], capture_output=True, text=True,
                              timeout=timeout, check=False)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_MARKER):
            return json.loads(line[len(_MARKER):])
    noise = "swig/python detected a memory leak"
    err = "\n".join(l for l in (proc.stderr + "\n" + proc.stdout).splitlines() if noise not in l).strip()
    raise RuntimeError(f"pcbnew job failed (exit {proc.returncode}):\n{err[-3000:]}")


def export_netlist(sch_path: Path) -> Dict[str, Any]:
    """Export the schematic netlist via kicad-cli and parse it."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "netlist.net"
        proc = subprocess.run([find_kicad_cli(), "sch", "export", "netlist", "--format", "kicadsexpr",
                               "-o", str(out), str(sch_path)], capture_output=True, text=True, check=False)
        if not out.is_file():
            raise RuntimeError(f"Netlist export failed: {proc.stderr or proc.stdout}")
        return read_netlist(out.read_text(encoding="utf-8"))


def sibling(path: Path, suffix: str, given: Optional[str] = None) -> Path:
    return Path(given).resolve() if given else path.with_suffix(suffix)

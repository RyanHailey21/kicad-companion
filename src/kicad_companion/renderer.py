import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import find_kicad_cli, get_render_cache_dir


def render_pcb_3d(
    pcb_path: str,
    side: str = "top",
    width: int = 1600,
    height: int = 900,
    transparent: bool = True,
    quality: str = "basic",
    zoom: float = 1.0,
    rotate: Optional[str] = None,
) -> Dict[str, Any]:
    """Render a 3D view of the PCB to PNG using kicad-cli."""
    kicad_cli = find_kicad_cli()
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    cache_dir = get_render_cache_dir()
    timestamp = int(time.time() * 1000)
    out_file = cache_dir / f"{p_path.stem}_3d_{side}_{timestamp}.png"

    cmd = [
        kicad_cli,
        "pcb",
        "render",
        "--side",
        side,
        "--width",
        str(width),
        "--height",
        str(height),
        "--quality",
        quality,
        "--zoom",
        str(zoom),
        "-o",
        str(out_file),
    ]

    if transparent:
        cmd.extend(["--background", "transparent"])
    if rotate:
        cmd.extend(["--rotate", rotate])

    cmd.append(str(p_path))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0 or not out_file.exists():
        err_msg = proc.stderr.strip() or proc.stdout.strip() or "Unknown render error"
        raise RuntimeError(f"3D render failed (exit code {proc.returncode}): {err_msg}")

    return {
        "status": "success",
        "file_path": str(out_file),
        "format": "png",
        "side": side,
        "width": width,
        "height": height,
        "size_bytes": out_file.stat().st_size,
    }


def render_pcb_2d(
    pcb_path: str,
    layers: str = "F.Cu,B.Cu,F.Silkscreen,Edge.Cuts",
    theme: Optional[str] = None,
) -> Dict[str, Any]:
    """Export a 2D composite SVG of the selected PCB layers."""
    kicad_cli = find_kicad_cli()
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    cache_dir = get_render_cache_dir()
    timestamp = int(time.time() * 1000)
    out_file = cache_dir / f"{p_path.stem}_2d_{timestamp}.svg"

    cmd = [
        kicad_cli,
        "pcb",
        "export",
        "svg",
        "--mode-single",
        "--layers",
        layers,
        "--fit-page-to-board",
        "--exclude-drawing-sheet",
        "-o",
        str(out_file),
    ]

    if theme:
        cmd.extend(["--theme", theme])

    cmd.append(str(p_path))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0 or not out_file.exists():
        err_msg = proc.stderr.strip() or proc.stdout.strip() or "Unknown 2D render error"
        raise RuntimeError(f"2D render failed (exit code {proc.returncode}): {err_msg}")

    return {
        "status": "success",
        "file_path": str(out_file),
        "format": "svg",
        "layers": layers,
        "size_bytes": out_file.stat().st_size,
    }


def render_schematic(
    sch_path: str,
    page: Optional[int] = None,
) -> Dict[str, Any]:
    """Export a schematic sheet to SVG."""
    kicad_cli = find_kicad_cli()
    s_path = Path(sch_path).resolve()
    if not s_path.is_file():
        raise FileNotFoundError(f"Schematic file not found: {sch_path}")

    cache_dir = get_render_cache_dir()
    timestamp = int(time.time() * 1000)
    out_dir = cache_dir / f"{s_path.stem}_sch_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        kicad_cli,
        "sch",
        "export",
        "svg",
        "--exclude-drawing-sheet",
        "-o",
        str(out_dir),
    ]

    if page is not None:
        cmd.extend(["--pages", str(page)])

    cmd.append(str(s_path))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or "Unknown schematic export error"
        raise RuntimeError(f"Schematic render failed (exit code {proc.returncode}): {err_msg}")

    svg_files = list(out_dir.glob("*.svg"))
    if not svg_files:
        raise RuntimeError("No SVG files were produced by schematic export.")

    return {
        "status": "success",
        "directory": str(out_dir),
        "svg_files": [str(f) for f in svg_files],
        "primary_file": str(svg_files[0]),
    }

import sys
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer, Image

from .renderer import render_pcb_3d as _render_pcb_3d
from .renderer import render_pcb_2d as _render_pcb_2d
from .renderer import render_schematic as _render_schematic
from .drc_triage import triage_pcb_drc as _triage_pcb_drc
from .project_context import get_project_context as _get_project_context
from .project_context import execute_project_script as _execute_project_script
from .supervisor import ensure_kicad_running as _ensure_kicad_running
from .macro_compiler import list_circuit_macros as _list_circuit_macros
from .macro_compiler import compile_circuit_macro as _compile_circuit_macro
from .net_sync import sync_pcb_nets_from_schematic as _sync_pcb_nets_from_schematic
from .router import (
    autoroute_board as _autoroute_board,
    export_specctra_dsn as _export_specctra_dsn,
    import_specctra_ses as _import_specctra_ses,
    run_freerouting as _run_freerouting,
)

server = MCPServer("kicad-companion")


@server.tool()
def render_pcb_3d(
    pcb_path: str,
    side: str = "top",
    width: int = 1600,
    height: int = 900,
    transparent: bool = True,
    quality: str = "basic",
    zoom: float = 1.0,
    rotate: Optional[str] = None,
) -> list[Any]:
    """Render a 3D photorealistic view of the PCB to PNG.

    Args:
        pcb_path: Absolute or relative path to the .kicad_pcb file.
        side: Camera side: 'top', 'bottom', 'left', 'right', 'front', 'back'. Default 'top'.
        width: Image width in pixels. Default 1600.
        height: Image height in pixels. Default 900.
        transparent: Whether background should be transparent PNG. Default True.
        quality: Render quality: 'basic' or 'high'. Default 'basic'.
        zoom: Camera zoom factor. Default 1.0.
        rotate: Rotation angles in degrees 'X,Y,Z' (e.g. '-45,0,45' for isometric view).
    """
    res = _render_pcb_3d(
        pcb_path=pcb_path,
        side=side,
        width=width,
        height=height,
        transparent=transparent,
        quality=quality,
        zoom=zoom,
        rotate=rotate,
    )
    msg = f"Successfully rendered 3D {side} view to {res['file_path']} ({res['width']}x{res['height']})."
    return msg


@server.tool()
def render_pcb_2d(
    pcb_path: str,
    layers: str = "F.Cu,B.Cu,F.Silkscreen,Edge.Cuts",
    theme: Optional[str] = None,
) -> str:
    """Export a 2D composite vector SVG of specified PCB layers.

    Args:
        pcb_path: Absolute or relative path to the .kicad_pcb file.
        layers: Comma-separated list of layer names (e.g. 'F.Cu,B.Cu,F.Silkscreen,Edge.Cuts').
        theme: Color theme to use (defaults to KiCad PCB editor settings).
    """
    res = _render_pcb_2d(pcb_path=pcb_path, layers=layers, theme=theme)
    return f"2D SVG generated at: {res['file_path']} (Layers: {res['layers']})"


@server.tool()
def render_schematic(
    sch_path: str,
    page: Optional[int] = None,
) -> str:
    """Export a schematic sheet to vector SVG.

    Args:
        sch_path: Absolute or relative path to the .kicad_sch file.
        page: Optional page number to export.
    """
    res = _render_schematic(sch_path=sch_path, page=page)
    return f"Schematic exported to SVG at: {res['primary_file']} (Directory: {res['directory']})"


@server.tool()
def triage_pcb_drc(
    pcb_path: str,
    max_items_per_group: int = 10,
) -> str:
    """Run headless DRC on a PCB board and return an actionable categorized summary.

    Categorizes violations into Critical Errors (shorts, clearance violations),
    Fab Hazards, Cosmetic Warnings, and Unconnected Pins with exact mm coordinates.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        max_items_per_group: Max items to report per category.
    """
    res = _triage_pcb_drc(pcb_path=pcb_path, max_items_per_group=max_items_per_group)
    return res["summary_markdown"]


@server.tool()
def list_circuit_macros(
    project_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List available parametric circuit macros (voltage dividers, I2C pullups, LEDs, decoupling banks, USB-C, crystals).

    Args:
        project_path: Optional project directory to discover project-specific macros.
    """
    return _list_circuit_macros(project_path=project_path)


@server.tool()
def compile_circuit_macro(
    macro_name: str,
    params: Dict[str, Any],
    anchor: List[float],
    project_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compile a high-level circuit macro (DSL) into calculated components, grid coordinates, and Konnect batch recipes.

    Calculates standard E24 component values, sets proper 1.27mm grid spacing, and outputs
    ready-to-run Konnect batch commands.

    Args:
        macro_name: Name of macro ('voltage_divider', 'i2c_pullups', 'status_led', 'decoupling_bank', 'crystal_circuit', 'usb_c_pd_input').
        params: Macro parameters (e.g. {'vin': 5.0, 'vout': 3.3, 'r_target_kohm': 10.0, 'package': '0603'}).
        anchor: [x, y] coordinates in mm on the schematic where the subcircuit should be placed.
        project_path: Optional project directory.
    """
    return _compile_circuit_macro(
        macro_name=macro_name,
        params=params,
        anchor=anchor,
        project_path=project_path,
    )


@server.tool()
def get_project_context(
    target_path: str,
) -> Dict[str, Any]:
    """Retrieve project configuration, target manufacturer rules, preferred parts, and available custom scripts.

    Looks for .companion/ or .kicad-companion/ directory in the project root.

    Args:
        target_path: Any path inside the project repository (or the project root).
    """
    return _get_project_context(target_path=target_path)


@server.tool()
def execute_project_script(
    target_path: str,
    script_name: str,
    args: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a project-specific helper script from .companion/scripts/.

    Args:
        target_path: Path inside the project repository.
        script_name: Name of the script (e.g. 'calc_impedance.py').
        args: Optional list of CLI arguments for the script.
    """
    return _execute_project_script(target_path=target_path, script_name=script_name, args=args)


@server.tool()
def ensure_kicad_running(
    project_or_pcb_path: str,
) -> Dict[str, Any]:
    """Verify if KiCad 10 is running. If not, launch KiCad in the background with the project loaded.

    Use this before attempting live PCB editing via Konnect NNG/Protobuf IPC to avoid connection failures.

    Args:
        project_or_pcb_path: Path to the .kicad_pro or .kicad_pcb file.
    """
    return _ensure_kicad_running(project_or_pcb_path=project_or_pcb_path)


@server.tool()
def sync_pcb_nets(
    pcb_path: str,
    sch_path: Optional[str] = None,
    pro_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronize schematic netlist into PCB pad net assignments and project netclasses.

    Exports the netlist from the schematic headlessly via kicad-cli, updates every footprint
    pad with explicit (net <code> "<name>") attributes, defines top-level board nets, and
    configures standard design rules / netclasses (Power, Analog_Sensitive, Digital_Events, Default).

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file.
        pro_path: Optional path to the .kicad_pro file.
    """
    return _sync_pcb_nets_from_schematic(pcb_path=pcb_path, sch_path=sch_path, pro_path=pro_path)


@server.tool()
def export_specctra_dsn(
    pcb_path: str,
    output_dsn: Optional[str] = None,
) -> str:
    """Export a KiCad PCB to Specctra DSN format using KiCad's bundled pcbnew engine.

    Sanitizes non-ASCII / Greek characters to ensure full compatibility with Freerouting.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        output_dsn: Optional output path for the .dsn file.
    """
    return _export_specctra_dsn(pcb_path=pcb_path, output_dsn=output_dsn)


@server.tool()
def autoroute_board(
    pcb_path: str,
    passes: int = 15,
) -> Dict[str, Any]:
    """Execute end-to-end headless autorouting with Freerouting: DSN export -> route -> SES import -> DRC triage.

    Automatically uses single-threaded route optimization (-mt 1) to eliminate Freerouting clearance bugs.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        passes: Maximum autorouting passes. Default 15.
    """
    return _autoroute_board(pcb_path=pcb_path, passes=passes)


@server.tool()
def import_specctra_ses(
    pcb_path: str,
    ses_path: str,
) -> Dict[str, Any]:
    """Import a Freerouting Specctra SES file and inject routed tracks and vias into .kicad_pcb headlessly.

    Args:
        pcb_path: Path to target .kicad_pcb file.
        ses_path: Path to the Freerouting .ses file.
    """
    return _import_specctra_ses(pcb_path=pcb_path, ses_path=ses_path)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer, Image

from .renderer import render_pcb_3d as _render_pcb_3d
from .renderer import render_pcb_2d as _render_pcb_2d
from .renderer import render_schematic as _render_schematic
from .drc_triage import triage_pcb_drc as _triage_pcb_drc
from .project_context import get_project_context as _get_project_context
from .project_context import execute_project_script as _execute_project_script
from .supervisor import ensure_kicad_running as _ensure_kicad_running
from .supervisor import project_open_status as _project_open_status
from .macro_compiler import list_circuit_macros as _list_circuit_macros
from .macro_compiler import compile_circuit_macro as _compile_circuit_macro
from .net_sync import sync_pcb_nets_from_schematic as _sync_pcb_nets_from_schematic
from .netclasses import configure_netclasses as _configure_netclasses
from .schematic_builder import generate_schematic as _generate_schematic
from .schematic_builder import run_schematic_erc as _run_schematic_erc
from .pcb_builder import build_pcb_from_schematic as _build_pcb_from_schematic
from .finalize import finalize_pcb as _finalize_pcb
from .fanout import fanout_vias as _fanout_vias
from .symbols import create_derived_symbol as _create_derived_symbol
from .router import (
    autoroute_board as _autoroute_board,
    export_specctra_dsn as _export_specctra_dsn,
    import_specctra_ses as _import_specctra_ses,
)
from .placement import (
    check_placement_overlaps as _check_placement_overlaps,
    place_footprints as _place_footprints,
    resolve_placement_overlaps as _resolve_placement_overlaps,
    sanitize_silkscreen as _sanitize_silkscreen,
)
from .intent_context import ensure_project_context as _ensure_project_context
from .spice_verifier import (
    audit_spice_models as _audit_spice_models,
    run_circuit_simulation as _run_circuit_simulation,
)
from .pinout_audit import audit_component_pinouts as _audit_component_pinouts
from .production_pipeline import build_production_package as _build_production_package
from .jlcpcb_assembly import (
    generate_jlcpcb_assembly as _generate_jlcpcb_assembly,
    generate_pcbway_assembly as _generate_pcbway_assembly,
)

server = MCPServer("kicad-companion")

_OPEN_NOTE = ("Refuses to write while KiCad has the project open (it would overwrite the change on exit); "
              "pass allow_while_open=True to override.")


# ============================================================ rendering
@server.tool()
def render_pcb_3d(
    pcb_path: str,
    side: str = "top",
    width: int = 1600,
    height: int = 900,
    transparent: bool = False,
    quality: str = "basic",
    zoom: float = 1.0,
    rotate: Optional[str] = None,
) -> list[Any]:
    """Render a 3D view of the PCB and return the PNG image inline (plus its file path).

    Args:
        pcb_path: Absolute or relative path to the .kicad_pcb file.
        side: Camera side: 'top', 'bottom', 'left', 'right', 'front', 'back'. Default 'top'.
        width: Image width in pixels. Default 1600.
        height: Image height in pixels. Default 900.
        transparent: Transparent PNG background. Default False (opaque reads better inline).
        quality: Render quality: 'basic' or 'high'. Default 'basic'.
        zoom: Camera zoom factor. Default 1.0.
        rotate: Rotation angles in degrees 'X,Y,Z' (e.g. '-45,0,45' for isometric view).
    """
    res = _render_pcb_3d(pcb_path=pcb_path, side=side, width=width, height=height,
                         transparent=transparent, quality=quality, zoom=zoom, rotate=rotate)
    return [f"3D {side} view ({res['width']}x{res['height']}) saved to {res['file_path']}",
            Image(path=res["file_path"])]


@server.tool()
def render_pcb_2d(
    pcb_path: str,
    layers: str = "F.Cu,B.Cu,F.Silkscreen,Edge.Cuts",
    theme: Optional[str] = None,
    fmt: str = "svg",
) -> str:
    """Export a 2D plot of PCB layers as 'svg' or 'pdf' (PDF can be read visually by agents).

    Args:
        pcb_path: Absolute or relative path to the .kicad_pcb file.
        layers: Comma-separated list of layer names (e.g. 'F.Cu,B.Cu,F.Silkscreen,Edge.Cuts').
        theme: Color theme to use (defaults to KiCad PCB editor settings).
        fmt: 'svg' (default) or 'pdf'.
    """
    res = _render_pcb_2d(pcb_path=pcb_path, layers=layers, theme=theme, fmt=fmt)
    return f"2D {res['format'].upper()} generated at: {res['file_path']} (Layers: {res['layers']})"


@server.tool()
def render_schematic(
    sch_path: str,
    page: Optional[int] = None,
    fmt: str = "svg",
) -> str:
    """Export schematic sheets as 'svg' (one per sheet) or a single 'pdf' (readable visually by agents).

    Args:
        sch_path: Absolute or relative path to the .kicad_sch file.
        page: Optional page number to export.
        fmt: 'svg' (default) or 'pdf'.
    """
    res = _render_schematic(sch_path=sch_path, page=page, fmt=fmt)
    return f"Schematic exported to {res['format'].upper()} at: {res['primary_file']} (Directory: {res['directory']})"


# ============================================================ headless design pipeline
@server.tool()
def generate_schematic(
    spec: Any,
    sch_path: str,
    overwrite: bool = False,
    run_erc: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Generate a complete KiCad schematic from a declarative parts/nets spec, then run ERC.

    Works without the KiCad GUI or IPC. Symbols come from the official libraries (or the
    project's sym-lib-table). Multi-unit parts get every unit placed, including the power
    unit; each listed pin is wired to its net with a stub and a local label, and a pin
    mapped to null gets a no-connect flag. PWR_FLAGs are added automatically where ERC
    needs them. Re-running keeps symbol UUIDs stable, so an existing PCB stays linked.

    Spec: {"title", "revision", "notes", "power_flags": "auto",
           "parts": [{"ref", "lib_id", "value", "footprint", "pins": {num: net|null},
                      "fields": {...}, "description", "dnp", "group"}]}

    Args:
        spec: Spec dict, JSON string, or path to a JSON file.
        sch_path: Output .kicad_sch path.
        overwrite: Replace a schematic that already has symbols.
        run_erc: Run ERC and include a summary.
    """
    return _generate_schematic(spec=spec, sch_path=sch_path, overwrite=overwrite, run_erc=run_erc,
                               allow_while_open=allow_while_open)


@server.tool()
def create_derived_symbol(
    project_path: str,
    base_lib_id: str,
    new_name: str,
    add_pins: List[Dict[str, Any]],
    library: Optional[str] = None,
    properties: Optional[Dict[str, str]] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy a library symbol into the project library under a new name with extra pins.

    Use it when a footprint has a pad the stock symbol lacks (e.g. an exposed thermal pad that
    the datasheet says must connect to V-). The library is created and registered in the
    project sym-lib-table if needed, and generate_schematic resolves it automatically.

    Args:
        project_path: Any path in the project.
        base_lib_id: e.g. "Amplifier_Operational:MCP6022".
        new_name: e.g. "OPA2381_DRB".
        add_pins: [{"number": "9", "name": "EP", "type": "passive", "like": "4", "dx": 2.54}].
        library: Project library nickname (default: project name).
        properties: Property overrides such as {"Footprint": "..."}.
        overwrite: Replace an existing symbol with the same name.
    """
    return _create_derived_symbol(project_path=project_path, base_lib_id=base_lib_id, new_name=new_name,
                                  add_pins=add_pins, library=library, properties=properties, overwrite=overwrite)


@server.tool()
def run_erc(sch_path: str, max_items: int = 15) -> Dict[str, Any]:
    """Run KiCad ERC headlessly and summarise violations by type.

    Args:
        sch_path: Root .kicad_sch.
        max_items: Max individual violations listed.
    """
    return _run_schematic_erc(sch_path, max_items=max_items)


@server.tool()
def build_pcb_from_schematic(
    sch_path: str,
    pcb_path: Optional[str] = None,
    board_width_mm: float = 100.0,
    board_height_mm: float = 100.0,
    origin_mm: Optional[List[float]] = None,
    fixed: Optional[Dict[str, List[float]]] = None,
    regions: Optional[List[Dict[str, Any]]] = None,
    auto_place_rest: bool = True,
    gap_mm: float = 0.8,
    copper_layers: int = 2,
    planes: Optional[Dict[str, str]] = None,
    keepouts: Optional[List[Dict[str, Any]]] = None,
    graphics: Optional[List[Dict[str, Any]]] = None,
    auto_sides: Optional[List[str]] = None,
    margin_mm: float = 1.0,
    routing_halo: Any = "auto",
    target_utilization: float = 0.8,
    spread_weight_mm: float = 6.0,
    groups: Optional[List[Dict[str, Any]]] = None,
    near: Optional[Dict[str, str]] = None,
    overwrite: bool = False,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Create a placed, unrouted PCB from the schematic (headless 'Update PCB from Schematic').

    Loads each footprint from its library (stock + global/project fp-lib-table), assigns
    nets with KiCad's exact names, links footprints to symbols (clean DRC schematic parity),
    draws a rectangular outline and places parts on exact courtyards.

    Args:
        sch_path: Root schematic.
        pcb_path: Output board (default: sibling .kicad_pcb).
        board_width_mm / board_height_mm: Outline size; origin_mm is its top-left (default [100, 100]).
        fixed: {ref: [cx, cy, rot, side]} courtyard-centre positions, board-relative mm; side "F"/"B".
        regions: [{"rect": [x0, y0, x1, y1], "refs": [...], "rotation": 0, "side": "B"}], packed in order.
        auto_place_rest: Pack all remaining parts into free board area.
        gap_mm: Courtyard gap.
        copper_layers: 2, 4, 6...
        planes: Inner planes, e.g. {"In1.Cu": "GND"} (power layer + zone; autorouter vias into it).
        keepouts: [{"rect": [...], "side": "F"|"B"|"both", "rule_area": false}] kept free of packed parts.
        graphics: Silk/Fab/User shapes and text, e.g. a lens-holder outline (board-relative mm).
        auto_sides: Sides tried for auto-placed parts, e.g. ["B", "F"] for double-sided assembly.
        margin_mm: Keep-in margin from the outline for auto-placed parts.
        routing_halo: Routing clearance around auto-placed parts (mm, pin-count scaled); "auto" spreads
            parts to target_utilization of the free area so extra board area becomes routing space.
        spread_weight_mm: Crowding penalty that pulls parts into empty board regions (0 = off).
        groups: Functional groups [{"refs": [...], "rect": [x0, y0, x1, y1], "sides": ["B"], "target": [x, y]}]:
            members are placed by connectivity but confined to their rect/sides; decoupling caps stay
            with an IC of their own group.
        near: {ref: target_ref} proximity rules (feedback/timing parts next to the pin they serve).
        overwrite: Rebuild a board that already has footprints.
    """
    return _build_pcb_from_schematic(sch_path=sch_path, pcb_path=pcb_path, board_width_mm=board_width_mm,
                                     board_height_mm=board_height_mm, origin_mm=origin_mm, fixed=fixed,
                                     regions=regions, auto_place_rest=auto_place_rest, gap_mm=gap_mm,
                                     copper_layers=copper_layers, planes=planes, keepouts=keepouts,
                                     graphics=graphics, auto_sides=auto_sides, margin_mm=margin_mm,
                                     routing_halo=routing_halo, target_utilization=target_utilization,
                                     spread_weight_mm=spread_weight_mm, groups=groups, near=near, overwrite=overwrite,
                                     allow_while_open=allow_while_open)


@server.tool()
def place_footprints(
    pcb_path: str,
    fixed: Optional[Dict[str, List[float]]] = None,
    regions: Optional[List[Dict[str, Any]]] = None,
    keep_refs: Optional[List[str]] = None,
    auto_place_rest: bool = False,
    gap_mm: float = 0.8,
    relative_to_outline: bool = True,
    keepouts: Optional[List[Dict[str, Any]]] = None,
    auto_sides: Optional[List[str]] = None,
    groups: Optional[List[Dict[str, Any]]] = None,
    near: Optional[Dict[str, str]] = None,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Place footprints from a plan using exact courtyards (fixed positions + packing regions), either side.

    Args:
        pcb_path: Board to modify.
        fixed: {ref: [cx, cy, rot, side]} courtyard-centre positions (mm); side "F" (default) or "B".
        regions: [{"rect": [x0, y0, x1, y1], "refs": [...], "rotation": 0, "rotations": {ref: deg}, "side": "F"}].
        keep_refs: Parts to leave in place but treat as obstacles.
        auto_place_rest: Also pack every part not mentioned into free board area.
        gap_mm: Courtyard-to-courtyard gap.
        relative_to_outline: Coordinates relative to the outline's top-left corner.
        keepouts: [{"rect": [x0, y0, x1, y1], "side": "F"|"B"|"both"}] areas left empty.
        auto_sides: Sides tried for auto-placed parts, in order (default ["F"]).
        groups: Functional groups for auto placement: [{"refs": [...], "rect": [...], "sides": ["B"],
            "target": [x, y]}] (members confined to their rect/sides, placed by connectivity).
        near: {ref: target_ref} proximity rules (feedback/timing parts next to the pin they serve).
    """
    return _place_footprints(pcb_path=pcb_path, fixed=fixed, regions=regions, keep_refs=keep_refs,
                             auto_place_rest=auto_place_rest, gap_mm=gap_mm,
                             relative_to_outline=relative_to_outline, keepouts=keepouts,
                             auto_sides=auto_sides, groups=groups, near=near, allow_while_open=allow_while_open)


@server.tool()
def configure_netclasses(
    project_path: str,
    classes: Optional[Dict[str, Dict[str, float]]] = None,
    assignments: Optional[Dict[str, List[str]]] = None,
    auto_power: bool = True,
    overwrite_classes: bool = False,
    replace_patterns: bool = False,
    board_rules: Optional[Dict[str, float]] = None,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Define net classes and assign nets in the .kicad_pro, matching KiCad's real net names.

    Local-label nets are named with their sheet path ("/GND"), so each pattern is written as
    both "<pat>" and "*/<pat>". Existing classes are kept unless overwrite_classes is set.

    Args:
        project_path: Any path in the project.
        classes: {name: {track_width, clearance, via_diameter, via_drill}} (mm).
        assignments: {class: [net names or wildcards, e.g. "VPHOTO_*"]}.
        auto_power: Add a Power class (0.5 mm) and assign supply/ground nets found in the schematic.
        overwrite_classes: Update dimensions of existing classes too.
        replace_patterns: Drop existing patterns instead of merging.
        board_rules: e.g. {"min_clearance": 0.2, "min_track_width": 0.2, "min_resolved_spokes": 1}.
    """
    return _configure_netclasses(project_path=project_path, classes=classes, assignments=assignments,
                                 auto_power=auto_power, overwrite_classes=overwrite_classes,
                                 replace_patterns=replace_patterns, board_rules=board_rules,
                                 allow_while_open=allow_while_open)


@server.tool()
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
    stitch_pitch_mm: float = 0.0,
    island_vias: bool = True,
    bridge_islands: bool = True,
    via_diameter_mm: float = 0.5,
    via_drill_mm: float = 0.3,
    relink: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Finish a routed board: re-link to the schematic, add/refresh copper pours, fill zones.

    Args:
        pcb_path: Routed board.
        sch_path: Schematic (default sibling).
        pour_net: Pour net ('GND' also matches '/GND'); null to skip pours.
        pour_layers: Default ['F.Cu', 'B.Cu'].
        clearance_mm / min_width_mm: Zone clearance and minimum fill width (fine-pitch boards:
            about 0.15 / 0.15 so the pour reaches between pads).
        thermal_reliefs / thermal_gap_mm / spoke_width_mm: Thermal spokes vs solid connection.
        stitch_pitch_mm: Optional stitching-via grid (0 = off, the default; a grid adds hundreds
            of vias the design rarely needs).
        island_vias: Tie every pour fragment that is not already connected (no via inside, no
            touching pad routed to a via) with one via, else a bridge track to a nearby via, else a
            short maze-routed track to a new via. Islands that still fail are returned in
            pours.islands_without_via with the stranded pads: fix those by hand (move a wall track
            ~0.3 mm and drop a via beside the pad) or run fanout_vias before routing next time.
        bridge_islands: Allow the bridge/maze tracks above.
        via_diameter_mm / via_drill_mm: Size of vias added for islands.
        relink: Sync symbol links, values, fields and NC nets from the schematic first.
    """
    return _finalize_pcb(pcb_path=pcb_path, sch_path=sch_path, pour_net=pour_net, pour_layers=pour_layers,
                         clearance_mm=clearance_mm, min_width_mm=min_width_mm, thermal_reliefs=thermal_reliefs,
                         thermal_gap_mm=thermal_gap_mm, spoke_width_mm=spoke_width_mm, relink=relink,
                         stitch_pitch_mm=stitch_pitch_mm, island_vias=island_vias, bridge_islands=bridge_islands,
                         via_diameter_mm=via_diameter_mm, via_drill_mm=via_drill_mm,
                         allow_while_open=allow_while_open)


# ============================================================ verification
@server.tool()
def triage_pcb_drc(
    pcb_path: str,
    max_items_per_group: int = 10,
    refill_zones: bool = False,
    schematic_parity: bool = False,
    detailed: bool = False,
) -> Any:
    """Run headless DRC on a PCB and return an actionable categorized summary.

    Categorizes violations into Critical Errors (shorts, clearance violations), Fab Hazards,
    Cosmetic Warnings and Unconnected Pins with exact mm coordinates. Footprint-internal pad
    spacing problems are called out separately (a routing change cannot fix them).

    Args:
        pcb_path: Path to the .kicad_pcb file.
        max_items_per_group: Max items to report per category.
        refill_zones: Refill zones before checking.
        schematic_parity: Also check board-vs-schematic consistency.
        detailed: Return the full structured result instead of the markdown summary.
    """
    res = _triage_pcb_drc(pcb_path=pcb_path, max_items_per_group=max_items_per_group,
                          refill_zones=refill_zones, schematic_parity=schematic_parity)
    return res if detailed else res["summary_markdown"]


@server.tool()
def check_project_open(path: str) -> Dict[str, Any]:
    """Report whether KiCad has this project open (lock files + running process).

    Headless edits to an open project are lost when KiCad saves or exits, and the
    .kicad_pro (net classes, rules) is rewritten from memory on exit.

    Args:
        path: Any file or directory in the project.
    """
    return _project_open_status(path)


# ============================================================ macros & project
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
    return _compile_circuit_macro(macro_name=macro_name, params=params, anchor=anchor, project_path=project_path)


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
    use_kicad_python: bool = False,
) -> Dict[str, Any]:
    """Execute a project-specific helper script from .companion/scripts/.

    Args:
        target_path: Path inside the project repository.
        script_name: Name of the script (e.g. 'calc_impedance.py').
        args: Optional list of CLI arguments for the script.
        use_kicad_python: Run under KiCad's bundled Python so the script can `import pcbnew`.
    """
    return _execute_project_script(target_path=target_path, script_name=script_name, args=args,
                                   use_kicad_python=use_kicad_python)


@server.tool()
def ensure_kicad_running(
    project_or_pcb_path: str,
) -> Dict[str, Any]:
    """Verify if KiCad is running. If not, launch KiCad in the background with the project loaded.

    Use this before attempting live PCB editing via Konnect NNG/Protobuf IPC to avoid connection failures.
    Close KiCad again before headless file edits (see check_project_open).

    Args:
        project_or_pcb_path: Path to the .kicad_pro or .kicad_pcb file.
    """
    return _ensure_kicad_running(project_or_pcb_path=project_or_pcb_path)


@server.tool()
def sync_pcb_nets(
    pcb_path: str,
    sch_path: Optional[str] = None,
    sync_fields: bool = True,
    configure_classes: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Push schematic nets, symbol links and fields onto existing PCB footprints (via pcbnew).

    Net names are kept exactly as KiCad names them ("/GND"), so DRC schematic parity is clean.
    Does not add/remove footprints (use build_pcb_from_schematic). Optionally merges an
    automatic Power net class without touching existing classes.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file.
        sync_fields: Also copy Description/custom fields and DNP/BOM flags.
        configure_classes: Merge an automatic Power net class.
    """
    return _sync_pcb_nets_from_schematic(pcb_path=pcb_path, sch_path=sch_path, sync_fields=sync_fields,
                                         configure_classes=configure_classes, allow_while_open=allow_while_open)


# ============================================================ routing
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
    rounds: int = 1,
    neckdown: bool = False,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Headless autorouting with Freerouting: DSN export -> route -> SES import -> DRC triage.

    Uses single-threaded optimization (-mt 1) to avoid Freerouting clearance bugs. Track widths
    and clearances come from the net classes, so run configure_netclasses first. On boards with
    inner planes, run fanout_vias first so plane pads keep a via.

    Judge the result by the returned KiCad DRC counts (drc.total_unconnected), never by
    Freerouting's log: its "unrouted items" counts net fragments and restarts high on a partly
    routed board. Extra rounds rarely help; if a region stays congested, fix the placement.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        passes: Maximum autorouting passes per round. Default 15.
        rounds: Re-route from the partly routed board while nets remain unrouted (best kept).
        neckdown: Let Freerouting narrow tracks below class width at fine-pitch pads (off by default).
    """
    return _autoroute_board(pcb_path=pcb_path, passes=passes, rounds=rounds, neckdown=neckdown,
                            allow_while_open=allow_while_open)


@server.tool()
def fanout_vias(
    pcb_path: str,
    nets: Optional[List[str]] = None,
    via_diameter_mm: float = 0.5,
    via_drill_mm: float = 0.3,
    stub_width_mm: float = 0.2,
    max_distance_mm: float = 1.5,
    share_mm: float = 1.2,
    lock: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Pre-route fanout: give every SMD pad on a plane net its own via + short dogbone stub.

    Run between placement and autoroute_board on any board with inner planes. Autorouters treat
    plane connections as ordinary work items; the ones that lose the race get boxed in by signal
    tracks and their pour becomes an unreachable island. Reserving the via first prevents that.

    Args:
        pcb_path: Placed board.
        nets: Nets to fan out; default = nets owning a zone on a power (plane) layer.
        via_diameter_mm / via_drill_mm / stub_width_mm: Via and stub size.
        max_distance_mm: Max via distance past the pad edge.
        share_mm: Same-net pads this close reuse one via.
        lock: Lock vias/stubs so the router keeps them.

    Never via-in-pad; clears other nets on every layer, via keep-outs and the board edge; skips
    pads that already have copper, so it is safe to re-run. Returns failed pads with positions.
    """
    return _fanout_vias(pcb_path=pcb_path, nets=nets, via_diameter_mm=via_diameter_mm, via_drill_mm=via_drill_mm,
                        stub_width_mm=stub_width_mm, max_distance_mm=max_distance_mm, share_mm=share_mm,
                        lock=lock, allow_while_open=allow_while_open)


@server.tool()
def import_specctra_ses(
    pcb_path: str,
    ses_path: str,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Import a Freerouting Specctra SES file and inject routed tracks and vias into .kicad_pcb headlessly.

    Replaces all unlocked tracks and vias with the session's; locked items (e.g. from fanout_vias)
    are kept exactly as they were.

    Args:
        pcb_path: Path to target .kicad_pcb file.
        ses_path: Path to the Freerouting .ses file.
    """
    return _import_specctra_ses(pcb_path=pcb_path, ses_path=ses_path, allow_while_open=allow_while_open)


# ============================================================ placement
@server.tool()
def check_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.25,
) -> Dict[str, Any]:
    """Check courtyard overlaps, board-edge insets and mounting-hole keepouts on exact courtyards.

    Also reports KiCad DRC's polygon-accurate courtyard violations.

    Args:
        pcb_path: Path to .kicad_pcb file.
        min_clearance_mm: Minimum clearance required between courtyards. Default 0.25 mm.
    """
    return _check_placement_overlaps(pcb_path=pcb_path, min_clearance_mm=min_clearance_mm)


@server.tool()
def resolve_placement_overlaps(
    pcb_path: str,
    min_clearance_mm: float = 0.5,
    grid_step_mm: float = 0.5,
    fixed_refs: Optional[List[str]] = None,
    max_iterations: int = 50,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Separate overlapping footprints by relaxation on exact courtyards, snapping origins to grid.

    Connectors (J*/P*/connector libraries) and mounting holes stay fixed, plus fixed_refs.

    Args:
        pcb_path: Path to .kicad_pcb file.
        min_clearance_mm: Minimum clearance required between footprints. Default 0.5 mm.
        grid_step_mm: Grid snap step for resolved positions. Default 0.5 mm.
        fixed_refs: Optional list of component references that must not move.
        max_iterations: Relaxation iterations. Default 50.
    """
    return _resolve_placement_overlaps(pcb_path=pcb_path, min_clearance_mm=min_clearance_mm,
                                       grid_step_mm=grid_step_mm, fixed_refs=fixed_refs,
                                       max_iterations=max_iterations, allow_while_open=allow_while_open)


@server.tool()
def sanitize_silkscreen(
    pcb_path: str,
    hide_passives: bool = True,
    min_pad_clearance_mm: float = 0.50,
    text_size_mm: float = 0.8,
    hide_refs: Optional[List[str]] = None,
    hide_unresolved: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Arrange reference designators until KiCad DRC reports no silkscreen problems.

    Refs start centred on part bodies (connectors above), flagged refs are tried above/below/
    left/right, then hidden as a last resort (F.Fab keeps them).

    Args:
        pcb_path: Path to .kicad_pcb file.
        hide_passives: Hide refs of tiny SMD passives (0201/0402/0603).
        min_pad_clearance_mm: Informational; KiCad's silk clearance rule is authoritative.
        text_size_mm: Reference text height (DRC minimum is usually 0.8 mm).
        hide_refs: References to hide unconditionally.
        hide_unresolved: Hide refs that still conflict after all positions were tried.
    """
    return _sanitize_silkscreen(pcb_path=pcb_path, hide_passives=hide_passives,
                                min_pad_clearance_mm=min_pad_clearance_mm, text_size_mm=text_size_mm,
                                hide_refs=hide_refs, hide_unresolved=hide_unresolved,
                                allow_while_open=allow_while_open)


# ============================================================ governance & verification
@server.tool()
def ensure_project_context(
    target_path: str,
    title: Optional[str] = None,
    purpose: Optional[str] = None,
    target_fab: str = "jlcpcb",
) -> Dict[str, Any]:
    """Ensure a structured PROJECT_CONTEXT.md exists at the root of the KiCad project.

    Creates it from the design files if missing. If it exists, only the identity lines
    (when title/purpose are given) and measured facts (outline, layers, part count) are
    refreshed; everything else the user wrote is left alone.

    Args:
        target_path: Path to any file or directory in the project repo.
        title: Optional title/name for the hardware project.
        purpose: Concise description of project goal and circuit intent.
        target_fab: Target fabrication house ('jlcpcb', 'pcbway', etc.).
    """
    return _ensure_project_context(target_path=target_path, title=title, purpose=purpose, target_fab=target_fab)


@server.tool()
def audit_spice_models(
    target_path: str,
) -> Dict[str, Any]:
    """Audit schematic components against real manufacturer SPICE macromodels.

    Verifies that all operational amplifiers, comparators, transistors, diodes, and
    voltage references have legitimate SPICE macromodels (.lib, .sub, .cir, .model)
    before board layout or fabrication. If missing, requests user to provide them.

    Args:
        target_path: Path to project root, schematic, or PCB file.
    """
    return _audit_spice_models(target_path=target_path)


@server.tool()
def run_circuit_simulation(
    target_path: str,
    sim_type: str = "both",
) -> Dict[str, Any]:
    """Execute headless SPICE simulation and return concise quantitative electrical metrics.

    Runs native SPICE netlists or project simulation testbenches via KiCad's bundled
    NGSPICE solver without polluting agent context with raw waveform dumps.

    Args:
        target_path: Path to project root or simulation netlist (.cir).
        sim_type: 'transient', 'ac', or 'both'.
    """
    return _run_circuit_simulation(target_path=target_path, sim_type=sim_type)


@server.tool()
def audit_component_pinouts(
    pcb_path: str,
    sch_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit schematic and PCB pinouts against known component traps and datasheet conventions.

    Checks:
    1. LED Polarity: Pad 1 (Cathode/square) vs Pad 2 (Anode/round) wiring.
    2. Diode Polarity: Pin 1 (Cathode) vs Pin 2 (Anode).
    3. SOT-23 MOSFET gate/source/drain mapping (only parts identified as MOSFETs; BJTs are skipped).
    4. Floating pads (pins the schematic marks no-connect are not reported).
    5. Annotation Syntax: References without trailing digits (e.g. RLED, U_REF) that block KiCad GUI F8.
    6. Multi-Unit Coverage: Multi-part ICs missing power units or auxiliary gates.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to .kicad_sch.
    """
    return _audit_component_pinouts(pcb_path=pcb_path, sch_path=sch_path)


# ============================================================ manufacturing
@server.tool()
def build_production_package(
    pcb_path: str,
    output_dir: Optional[str] = None,
    revision: str = "revA",
    fab_house: str = "jlcpcb",
    generate_3d_step: bool = True,
    generate_doc_svgs: bool = True,
) -> Dict[str, Any]:
    """Execute end-to-end manufacturing export pipeline in one single headless call.

    Abstracts redundant tool calling. Performs:
    1. DRC Pre-Flight Gate (halts if errors exist).
    2. Protel Gerbers & Drill Export (.gtl, .gbl, .gts, .gbs, .gto, .gbo, .gtp, .gbp, .gm1, .drl).
    3. Production ZIP Archive ready for instant upload to JLCPCB/PCBWay.
    4. SMT Assembly Artifacts (CPL pick-and-place & BOM).
    5. Mechanical CAD 3D Model (.step).
    6. Documentation Vector Renders (2D PCB layout & schematic SVGs).

    Args:
        pcb_path: Path to .kicad_pcb file.
        output_dir: Optional custom output directory.
        revision: Board revision label (e.g. 'revA').
        fab_house: Target manufacturer ('jlcpcb', 'pcbway', etc.).
        generate_3d_step: Whether to export full 3D STEP file. Default True.
        generate_doc_svgs: Whether to refresh vector SVGs for documentation. Default True.
    """
    return _build_production_package(pcb_path=pcb_path, output_dir=output_dir, revision=revision,
                                     fab_house=fab_house, generate_3d_step=generate_3d_step,
                                     generate_doc_svgs=generate_doc_svgs)


@server.tool()
def export_jlcpcb_assembly(
    pcb_path: str,
    sch_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    preferred_parts_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate JLCPCB-compliant BOM and CPL (pick-and-place) files directly from KiCad design files.

    Produces:
    - <stem>-cpl-jlcpcb.csv: Designator, Mid X, Mid Y, Layer, Rotation
    - <stem>-bom-jlcpcb.csv: Comment, Designator, Footprint, LCSC Part #

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file.
        output_dir: Optional destination directory (defaults to <project>/production/).
        preferred_parts_path: Optional path to preferred_parts.json.
    """
    return _generate_jlcpcb_assembly(pcb_path=pcb_path, sch_path=sch_path, output_dir=output_dir,
                                     preferred_parts_path=preferred_parts_path)


@server.tool()
def export_pcbway_assembly(
    pcb_path: str,
    sch_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate PCBWay-compliant BOM and CPL (pick-and-place) files directly from KiCad design files.

    Produces:
    - <stem>-cpl-pcbway.csv: Designator, Mid X, Mid Y, Layer, Rotation
    - <stem>-bom-pcbway.csv: Item, Designator, Qty, Value, Footprint, Manufacturer, MPN

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file.
        output_dir: Optional destination directory (defaults to <project>/production/).
    """
    return _generate_pcbway_assembly(pcb_path=pcb_path, sch_path=sch_path, output_dir=output_dir)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

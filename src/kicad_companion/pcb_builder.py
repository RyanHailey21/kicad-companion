"""Create a PCB from the schematic headlessly (the 'F8 Update PCB' step without the GUI)."""
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import find_footprint_dir, KICAD_VERSIONS
from .kicad_python import export_netlist, run_pcbnew, sibling
from .placement import place_footprints
from .sexp import find, parse, value
from .supervisor import guard_headless_write

_BUILD_JOB = r'''
import os
pcb = ARGS["pcb"]
if os.path.exists(pcb):
    os.remove(pcb)
board = pcbnew.NewBoard(pcb)
board.SetCopperLayerCount(ARGS["copper_layers"])
ds = board.GetDesignSettings()
for k, v in ARGS["rules"].items():
    setattr(ds, k, mm(v))
ds.m_AllowSoldermaskBridgesInFPs = ARGS["mask_bridges_in_fps"]
nets = {}
def net(name):
    if name not in nets:
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        nets[name] = ni
    return nets[name]
X0, Y0, W, H = ARGS["origin"][0], ARGS["origin"][1], ARGS["width"], ARGS["height"]
missing = []
col = 0
for ref, c in sorted(ARGS["components"].items()):
    fp = pcbnew.FootprintLoad(c["lib_dir"], c["fp_name"]) if c["lib_dir"] else None
    if fp is None:
        missing.append({"ref": ref, "footprint": c["footprint"]}); continue
    fp.SetReference(ref)
    fp.SetValue(c["value"])
    fp.SetFPIDAsString(c["footprint"])
    if c["tstamp"]:
        fp.SetPath(pcbnew.KIID_PATH("/" + c["tstamp"]))
    fp.SetField("Description", c["description"])
    for k, v in c["fields"].items():
        if k not in ("Footprint", "Datasheet", "Reference", "Value", "Description"):
            fp.SetField(k, v)
            fp.GetField(k).SetVisible(False)
    fp.SetDNP("dnp" in c["properties"])
    if "exclude_from_bom" in c["properties"]:
        fp.SetExcludedFromBOM(True)
    for pad in fp.Pads():
        n = c["pins"].get(pad.GetNumber())
        if n:
            pad.SetNet(net(n))
    # park beside the board until placement runs
    fp.SetPosition(pcbnew.VECTOR2I(mm(X0 + W + 10 + (col % 10) * 12), mm(Y0 + (col // 10) * 12)))
    col += 1
    board.Add(fp)
rect = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
rect.SetStart(pcbnew.VECTOR2I(mm(X0), mm(Y0)))
rect.SetEnd(pcbnew.VECTOR2I(mm(X0 + W), mm(Y0 + H)))
rect.SetLayer(pcbnew.Edge_Cuts)
rect.SetWidth(mm(0.1))
board.Add(rect)
def outline_zone(z, pts):
    o = z.Outline()
    o.NewOutline()
    for x, y in pts:
        o.Append(mm(x), mm(y))
# inner planes: power-type layer + full-board zone, so the autorouter treats it as a plane
for lname, netname in ARGS["planes"].items():
    lid = board.GetLayerID(lname)
    board.SetLayerType(lid, pcbnew.LT_POWER)
    z = pcbnew.ZONE(board)
    z.SetLayer(lid)
    z.SetNet(net(netname))
    z.SetLocalClearance(mm(0.3))
    z.SetMinThickness(mm(0.25))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    outline_zone(z, [(X0 + 0.4, Y0 + 0.4), (X0 + W - 0.4, Y0 + 0.4), (X0 + W - 0.4, Y0 + H - 0.4), (X0 + 0.4, Y0 + H - 0.4)])
    board.Add(z)
layer_ids = {board.GetLayerName(l): l for l in board.GetEnabledLayers().Seq()}
for k in ARGS["keepouts"]:
    if not k.get("rule_area"):
        continue
    x0, y0, x1, y1 = k["rect"]
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetLayer(pcbnew.F_Cu if k.get("side", "F") != "B" else pcbnew.B_Cu)
    z.SetDoNotAllowFootprints(True)
    z.SetDoNotAllowTracks(False); z.SetDoNotAllowVias(False); z.SetDoNotAllowPads(False); z.SetDoNotAllowZoneFills(False)
    outline_zone(z, [(X0 + x0, Y0 + y0), (X0 + x1, Y0 + y0), (X0 + x1, Y0 + y1), (X0 + x0, Y0 + y1)])
    board.Add(z)
for g in ARGS["graphics"]:
    layer = layer_ids.get(g.get("layer", "F.SilkS"), pcbnew.F_SilkS)
    if "rect" in g:
        sh = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
        x0, y0, x1, y1 = g["rect"]
        sh.SetStart(pcbnew.VECTOR2I(mm(X0 + x0), mm(Y0 + y0))); sh.SetEnd(pcbnew.VECTOR2I(mm(X0 + x1), mm(Y0 + y1)))
    elif "circle" in g:
        sh = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_CIRCLE)
        cx, cy, r = g["circle"]
        sh.SetCenter(pcbnew.VECTOR2I(mm(X0 + cx), mm(Y0 + cy))); sh.SetEnd(pcbnew.VECTOR2I(mm(X0 + cx + r), mm(Y0 + cy)))
    elif "line" in g:
        sh = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        x0, y0, x1, y1 = g["line"]
        sh.SetStart(pcbnew.VECTOR2I(mm(X0 + x0), mm(Y0 + y0))); sh.SetEnd(pcbnew.VECTOR2I(mm(X0 + x1), mm(Y0 + y1)))
    elif "text" in g:
        sh = pcbnew.PCB_TEXT(board)
        sh.SetText(g["text"]); x, y = g["at"]
        sh.SetPosition(pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + y)))
        sz = mm(g.get("size", 0.8)); sh.SetTextSize(pcbnew.VECTOR2I(sz, sz)); sh.SetTextThickness(mm(0.15))
        if layer in (pcbnew.B_SilkS, pcbnew.B_Fab):
            sh.SetMirrored(True)
    else:
        continue
    sh.SetLayer(layer)
    if hasattr(sh, "SetWidth") and "text" not in g:
        sh.SetWidth(mm(g.get("width", 0.12)))
    board.Add(sh)
board.Save(pcb)
emit({"footprints": col, "nets": len(nets), "missing_footprints": missing})
'''


def _expand(uri: str, project_dir: Path) -> str:
    uri = uri.replace("${KIPRJMOD}", str(project_dir))
    stock = None
    try:
        stock = str(find_footprint_dir())
    except FileNotFoundError:
        pass

    def env(m: "re.Match[str]") -> str:
        name = m.group(1)
        if re.fullmatch(r"KICAD\d*_FOOTPRINT_DIR", name) and stock:
            return os.environ.get(name, stock)
        return os.environ.get(name, m.group(0))

    return re.sub(r"\$\{([^}]+)\}", env, uri)


def _footprint_libs(project_dir: Path) -> Dict[str, str]:
    """Nickname -> .pretty directory: stock libs, then global and project fp-lib-tables."""
    libs: Dict[str, str] = {}
    try:
        for d in find_footprint_dir().glob("*.pretty"):
            libs[d.stem] = str(d)
    except FileNotFoundError:
        pass
    tables = []
    appdata = os.environ.get("APPDATA")
    for ver in KICAD_VERSIONS:
        for base in filter(None, [appdata and Path(appdata) / "kicad" / ver,
                                  Path.home() / ".config" / "kicad" / ver,
                                  Path.home() / "Library" / "Preferences" / "kicad" / ver]):
            tables.append(base / "fp-lib-table")
    tables.append(project_dir / "fp-lib-table")
    for t in tables:
        if not t.is_file():
            continue
        try:
            for lib in find(parse(t.read_text(encoding="utf-8")), "lib"):
                path = _expand(str(value(lib, "uri", "")), project_dir)
                if Path(path).is_dir():
                    libs[str(value(lib, "name"))] = path
        except Exception:
            continue
    return libs


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
    step_mm: float = 0.25,
    margin_mm: float = 1.0,
    allow_soldermask_bridges_in_footprints: bool = True,
    routing_halo: Any = "auto",
    target_utilization: float = 0.8,
    spread_weight_mm: float = 6.0,
    overwrite: bool = False,
    groups: Optional[List[Dict[str, Any]]] = None,
    group_pull: float = 0.35,
    near: Optional[Dict[str, str]] = None,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Build a fresh, placed (unrouted) board from the schematic netlist.

    Loads every footprint from its library (stock KiCad libs plus global/project fp-lib-table),
    assigns nets exactly as KiCad names them, links footprints to their symbols (so DRC
    schematic-parity and later F8 updates are clean), draws a rectangular outline and places
    parts with place_footprints (fixed positions, packing regions, then automatic fill).

    Args:
        sch_path: Root .kicad_sch.
        pcb_path: Output .kicad_pcb (defaults to the sibling of the schematic).
        board_width_mm / board_height_mm: Outline size. origin_mm: outline top-left (default [100, 100]).
        fixed / regions / auto_place_rest / gap_mm / keepouts / auto_sides: see place_footprints
            (board-relative mm; fixed entries may carry a 4th element "B" for the back side).
        margin_mm: Keep-in margin from the outline for auto-placed parts.
        allow_soldermask_bridges_in_footprints: Fine-pitch parts (0.5 mm QFN/VSSOP, 0201) normally
            have merged mask openings between their own pads; don't flag those.
        routing_halo / target_utilization: see place_footprints; "auto" spreads parts over the
            board so routing channels scale with board size.
        copper_layers: 2, 4, 6 ...
        planes: Inner plane layers, e.g. {"In1.Cu": "GND"}; the layer is made a power layer with a
            full-board zone so the autorouter drops vias into it instead of routing the net.
        keepouts: Also accept "rule_area": true to add a KiCad no-footprints rule area DRC enforces.
        graphics: [{"layer": "F.SilkS", "rect"|"circle"|"line": [...], "width": 0.12} |
            {"layer": ..., "text": "...", "at": [x, y], "size": 0.8}] in board-relative mm.
        overwrite: Replace a board that already has footprints.
    """
    s_path = Path(sch_path).resolve()
    if not s_path.is_file():
        raise FileNotFoundError(f"Schematic not found: {sch_path}")
    p_path = sibling(s_path, ".kicad_pcb", pcb_path)
    guard_headless_write(str(p_path), allow_while_open)
    if p_path.is_file() and not overwrite and "(footprint " in p_path.read_text(encoding="utf-8", errors="ignore"):
        raise FileExistsError(f"{p_path.name} already has footprints; pass overwrite=True to rebuild it.")

    netlist = export_netlist(s_path)
    libs = _footprint_libs(s_path.parent)
    comps, no_fp = {}, []
    for ref, c in netlist["components"].items():
        if ref.startswith("#"):
            continue
        if ":" not in c["footprint"]:
            no_fp.append(ref)
            continue
        lib, name = c["footprint"].split(":", 1)
        comps[ref] = {**c, "lib_dir": libs.get(lib), "fp_name": name}

    rules = {}
    rules_file = s_path.parent / ".companion" / "rules.json"
    if rules_file.is_file():
        import json
        r = json.loads(rules_file.read_text(encoding="utf-8"))
        rules = {k: v for k, v in {"m_TrackMinWidth": r.get("min_trace_width_mm"),
                                   "m_MinClearance": r.get("min_clearance_mm"),
                                   "m_ViasMinSize": r.get("min_via_diameter_mm"),
                                   "m_MinThroughDrill": r.get("min_via_drill_mm")}.items() if v}
    origin = origin_mm or [100.0, 100.0]
    # plane nets must exist under their exact schematic names
    names = {n for n in netlist["nets"]}
    plane_nets = {}
    for layer, n in (planes or {}).items():
        plane_nets[layer] = n if n in names else ("/" + n if "/" + n in names else n)
    built = run_pcbnew(_BUILD_JOB, {"pcb": str(p_path), "components": comps, "origin": origin,
                                    "width": board_width_mm, "height": board_height_mm, "rules": rules,
                                    "copper_layers": copper_layers, "planes": plane_nets,
                                    "keepouts": keepouts or [], "graphics": graphics or [],
                                    "mask_bridges_in_fps": allow_soldermask_bridges_in_footprints})
    placed = place_footprints(str(p_path), fixed=fixed, regions=regions, auto_place_rest=auto_place_rest,
                              gap_mm=gap_mm, step_mm=step_mm, margin_mm=margin_mm, keepouts=keepouts, auto_sides=auto_sides,
                              routing_halo=routing_halo, target_utilization=target_utilization,
                              spread_weight_mm=spread_weight_mm, groups=groups, group_pull=group_pull, near=near,
                              allow_while_open=allow_while_open)
    return {
        "status": "success" if not (built["missing_footprints"] or no_fp or placed["overflow"]) else "partial",
        "pcb_file": str(p_path),
        "footprints": built["footprints"],
        "nets": built["nets"],
        "missing_footprints": built["missing_footprints"],
        "symbols_without_footprint": no_fp,
        "copper_layers": copper_layers,
        "placement": {k: placed[k] for k in ("placed", "overflow", "unplaced", "back_side", "placement_score", "is_pass",
                                              "edge_violations", "overlaps", "drc_courtyard_violations")},
        "next_steps": "configure_netclasses -> autoroute_board -> finalize_pcb -> sanitize_silkscreen -> triage_pcb_drc(schematic_parity=True)",
    }

"""Post-route finishing: re-link to schematic, copper pours, zone fill."""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kicad_python import run_pcbnew, sibling
from .net_sync import sync_pcb_nets_from_schematic
from .supervisor import guard_headless_write

_POUR_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
net = None
for cand in ARGS["net_candidates"]:
    net = board.FindNet(cand)
    if net is not None:
        break
if net is None:
    raise SystemExit("pour net not found: %s" % ARGS["net_candidates"])
layers = {board.GetLayerName(l): l for l in board.GetEnabledLayers().Seq()}
ol = board_outline(board)
existing = {}
for z in board.Zones():
    if z.GetNetname() == net.GetNetname() and not z.GetIsRuleArea():
        existing[board.GetLayerName(z.GetLayer())] = z
made = []
for lname in ARGS["layers"]:
    z = existing.get(lname)
    if z is None:
        z = pcbnew.ZONE(board)
        z.SetLayer(layers[lname])
        z.SetNet(net)
        o = z.Outline()
        o.NewOutline()
        inset = ARGS["edge_inset"]
        for x, y in ((ol[0] + inset, ol[1] + inset), (ol[2] - inset, ol[1] + inset),
                     (ol[2] - inset, ol[3] - inset), (ol[0] + inset, ol[3] - inset)):
            o.Append(mm(x), mm(y))
        board.Add(z)
        made.append(lname)
    z.SetLocalClearance(mm(ARGS["clearance"]))
    z.SetMinThickness(mm(ARGS["min_width"]))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if ARGS["thermal"] else pcbnew.ZONE_CONNECTION_FULL)
    z.SetThermalReliefGap(mm(ARGS["thermal_gap"]))
    z.SetThermalReliefSpokeWidth(mm(ARGS["spoke_width"]))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(ARGS["pcb"])
emit({"net": net.GetNetname(), "zones_created": made, "zones_total": len(list(board.Zones()))})
'''


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
    edge_inset_mm: float = 0.5,
    relink: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Finish a routed board: re-sync links/fields from the schematic, add/refresh copper pours, fill zones.

    Args:
        pcb_path: Routed board.
        sch_path: Schematic (defaults to sibling).
        pour_net: Net to pour ("GND" also matches "/GND"); None skips pours.
        pour_layers: Copper layers to pour (default F.Cu and B.Cu).
        clearance_mm / min_width_mm / thermal_*: zone settings. Existing pours on the net are updated, not duplicated.
        relink: Run sync_pcb_nets first so symbol links, values, fields and NC nets match the schematic.
    """
    p_path = Path(pcb_path).resolve()
    guard_headless_write(str(p_path), allow_while_open)
    out: Dict[str, Any] = {"status": "success", "pcb_file": str(p_path)}
    if relink and sibling(p_path, ".kicad_sch", sch_path).is_file():
        sync = sync_pcb_nets_from_schematic(str(p_path), sch_path, configure_classes=False,
                                            allow_while_open=allow_while_open)
        out["relink"] = {k: sync[k] for k in ("pads_changed", "missing_on_board", "extra_on_board")}
    if pour_net:
        cands = [pour_net] if pour_net.startswith("/") else [pour_net, "/" + pour_net]
        out["pours"] = run_pcbnew(_POUR_JOB, {
            "pcb": str(p_path), "net_candidates": cands, "layers": pour_layers or ["F.Cu", "B.Cu"],
            "clearance": clearance_mm, "min_width": min_width_mm, "thermal": thermal_reliefs,
            "thermal_gap": thermal_gap_mm, "spoke_width": spoke_width_mm, "edge_inset": edge_inset_mm,
        })
    return out

"""Headless 'Update PCB from Schematic' for nets, links and fields (no footprint add/remove)."""
from pathlib import Path
from typing import Any, Dict, Optional

from .kicad_python import export_netlist, run_pcbnew, sibling
from .supervisor import guard_headless_write

_SYNC_JOB = r'''
board = pcbnew.LoadBoard(ARGS["pcb"])
comps = ARGS["components"]
nets = {}
def get_net(name):
    if name not in nets:
        ni = board.FindNet(name)
        if ni is None:
            ni = pcbnew.NETINFO_ITEM(board, name)
            board.Add(ni)
        nets[name] = ni
    return nets[name]
changed_pads, missing_on_board, seen = 0, [], set()
for fp in board.GetFootprints():
    ref = fp.GetReference()
    c = comps.get(ref)
    if c is None:
        continue
    seen.add(ref)
    if c["tstamp"]:
        fp.SetPath(pcbnew.KIID_PATH("/" + c["tstamp"]))
    fp.SetValue(c["value"])
    if ARGS["sync_fields"]:
        fp.SetField("Description", c["description"])
        for k, v in c["fields"].items():
            if k in ("Footprint", "Datasheet", "Reference", "Value", "Description"):
                continue
            fp.SetField(k, v)
            fp.GetField(k).SetVisible(False)
        fp.SetDNP("dnp" in c["properties"])
        fp.SetExcludedFromBOM("exclude_from_bom" in c["properties"])
    for pad in fp.Pads():
        want = c["pins"].get(pad.GetNumber())
        if want is None:
            continue
        if pad.GetNetname() != want:
            pad.SetNet(get_net(want))
            changed_pads += 1
missing_on_board = sorted(set(comps) - seen)
extra_on_board = sorted(fp.GetReference() for fp in board.GetFootprints()
                        if fp.GetReference() not in comps and not fp.GetReference().startswith(("H", "MH", "FID", "LOGO", "G")))
board.Save(ARGS["pcb"])
emit({"changed_pads": changed_pads, "missing_on_board": missing_on_board, "extra_on_board": extra_on_board,
      "board_nets": board.GetNetCount()})
'''


def sync_pcb_nets_from_schematic(
    pcb_path: str,
    sch_path: Optional[str] = None,
    pro_path: Optional[str] = None,
    sync_fields: bool = True,
    configure_classes: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Push schematic connectivity, symbol links and fields onto existing PCB footprints.

    Net names are kept exactly as KiCad names them (e.g. "/GND"), so a later GUI
    "Update PCB from Schematic" and DRC schematic-parity see no differences.
    Footprints are not added or removed; use build_pcb_from_schematic for that.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file (defaults to sibling with same stem).
        pro_path: Unused, kept for backwards compatibility (the .kicad_pro is found automatically).
        sync_fields: Also copy Description/custom fields and DNP/BOM flags.
        configure_classes: Merge an automatic Power net class (non-destructive).
        allow_while_open: Write even if KiCad has the project open.
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")
    s_path = sibling(p_path, ".kicad_sch", sch_path)
    if not s_path.is_file():
        raise FileNotFoundError(f"Schematic file not found: {s_path}")
    guard_headless_write(str(p_path), allow_while_open)

    netlist = export_netlist(s_path)
    comps = {r: c for r, c in netlist["components"].items() if not r.startswith("#")}
    res = run_pcbnew(_SYNC_JOB, {"pcb": str(p_path), "components": comps, "sync_fields": sync_fields})

    classes = None
    if configure_classes:
        from .netclasses import configure_netclasses
        classes = configure_netclasses(str(p_path), auto_power=True, allow_while_open=allow_while_open)

    nets = sorted(n for n in netlist["nets"] if not n.startswith("unconnected-"))
    return {
        "status": "success" if not res["missing_on_board"] else "partial",
        "pcb_file": str(p_path),
        "schematic_file": str(s_path),
        "total_nets": len(nets),
        "total_pins_mapped": sum(len(c["pins"]) for c in comps.values()),
        "pads_changed": res["changed_pads"],
        "missing_on_board": res["missing_on_board"],
        "extra_on_board": res["extra_on_board"],
        "netclasses": classes,
        "nets": nets,
        "hint": ("Footprints missing on board: run build_pcb_from_schematic or place them first."
                 if res["missing_on_board"] else ""),
    }

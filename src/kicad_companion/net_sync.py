import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .config import find_kicad_cli


def sync_pcb_nets_from_schematic(
    pcb_path: str,
    sch_path: Optional[str] = None,
    pro_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract nets from schematic netlist and inject them into PCB pads and project netclasses.

    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file (defaults to sibling with same stem).
        pro_path: Optional path to the .kicad_pro file (defaults to sibling with same stem).

    Returns:
        Dict with status, net count, pins mapped, and assigned netclasses.
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    s_path = Path(sch_path).resolve() if sch_path else p_path.with_suffix(".kicad_sch")
    if not s_path.is_file():
        raise FileNotFoundError(f"Schematic file not found: {s_path}")

    pr_path = Path(pro_path).resolve() if pro_path else p_path.with_suffix(".kicad_pro")

    kicad_cli = find_kicad_cli()
    netlist_file = p_path.parent / f"{p_path.stem}_temp.net"

    # 1. Export netlist via kicad-cli
    cmd = [
        kicad_cli,
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadsexpr",
        "-o",
        str(netlist_file),
        str(s_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if not netlist_file.is_file():
        raise RuntimeError(f"Failed to export netlist from schematic: {proc.stderr or proc.stdout}")

    try:
        net_content = netlist_file.read_text(encoding="utf-8")
    finally:
        netlist_file.unlink(missing_ok=True)

    # 2. Parse (nets ...)
    nets_pos = net_content.find("(nets")
    if nets_pos == -1:
        raise ValueError("No (nets section found in exported netlist")
    nets_text = net_content[nets_pos:]

    pattern = re.compile(
        r'\(net\s+\(code\s+"(\d+)"\)\s+\(name\s+"([^"]+)"\)(.*?)(?=\n\t\t\(net|\n\t\))',
        re.DOTALL,
    )
    node_pattern = re.compile(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)')

    pin_to_net = {}
    unique_nets = set()

    for code, name, body in pattern.findall(nets_text):
        # Strip leading slash if present e.g. /3V3 -> 3V3
        clean_name = name.lstrip("/")
        if clean_name.startswith("unconnected-"):
            continue
        unique_nets.add(clean_name)
        nodes = node_pattern.findall(body)
        for ref, pin in nodes:
            pin_to_net[(ref, pin)] = clean_name

    # Order nets: GND, 3V3, 3V3_ANALOG, VREF first, then sorted
    priority_nets = ["GND", "3V3", "3V3_ANALOG", "VREF"]
    ordered_nets = [n for n in priority_nets if n in unique_nets]
    remaining_nets = sorted(list(unique_nets - set(ordered_nets)))
    all_nets = ordered_nets + remaining_nets
    net_to_code = {name: idx + 1 for idx, name in enumerate(all_nets)}

    # 3. Update PCB file
    pcb_content = p_path.read_text(encoding="utf-8")

    # Remove existing top-level (net ...) declarations
    pcb_content = re.sub(r'\n\t\(net\s+\d+\s+"[^"]*"\)', '', pcb_content)

    # Build new (net ...) declarations
    net_decl_lines = ['\t(net 0 "")']
    for name in all_nets:
        code = net_to_code[name]
        net_decl_lines.append(f'\t(net {code} "{name}")')
    net_decls = "\n" + "\n".join(net_decl_lines)

    # Insert net declarations after (setup ...) block
    setup_idx = pcb_content.find("(setup")
    if setup_idx != -1:
        depth = 0
        setup_end = -1
        for i in range(setup_idx, len(pcb_content)):
            if pcb_content[i] == '(':
                depth += 1
            elif pcb_content[i] == ')':
                depth -= 1
                if depth == 0:
                    setup_end = i + 1
                    break
        if setup_end != -1:
            pcb_content = pcb_content[:setup_end] + "\n" + net_decls + pcb_content[setup_end:]

    # Assign nets to pads inside footprints
    def update_footprint(m):
        fp_text = m.group(0)
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_text)
        if not ref_m:
            return fp_text
        ref = ref_m.group(1)

        def update_pad(pm):
            pad_text = pm.group(0)
            pad_num = pm.group(1)
            # Remove any existing (net ...)
            pad_text = re.sub(r'\s*\(net\s+\d+\s+"[^"]*"\)', '', pad_text)
            net_name = pin_to_net.get((ref, pad_num))
            if net_name and net_name in net_to_code:
                code = net_to_code[net_name]
                net_expr = f'\n\t\t\t(net {code} "{net_name}")'
                last_p = pad_text.rfind(')')
                pad_text = pad_text[:last_p] + net_expr + pad_text[last_p:]
            return pad_text

        return re.sub(
            r'\(pad\s+"([^"]+)"\s+(?:smd|thru_hole|connect|np_thru_hole).*?\n\t\t\)',
            update_pad,
            fp_text,
            flags=re.DOTALL,
        )

    pcb_content = re.sub(r'\(footprint\s+.*?\n\t\)', update_footprint, pcb_content, flags=re.DOTALL)
    p_path.write_text(pcb_content, encoding="utf-8")

    # 4. Synchronize netclasses in .kicad_pro if present
    if pr_path.is_file():
        try:
            pro = json.loads(pr_path.read_text(encoding="utf-8"))
            net_settings = pro.get("net_settings", {})
            classes = {c["name"]: c for c in net_settings.get("classes", [])}

            default_classes = [
                {
                    "name": "Default",
                    "clearance": 0.15,
                    "track_width": 0.20,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "priority": 2147483647,
                },
                {
                    "name": "Power",
                    "clearance": 0.15,
                    "track_width": 0.40,
                    "via_diameter": 0.8,
                    "via_drill": 0.4,
                    "priority": 1,
                },
                {
                    "name": "Analog_Sensitive",
                    "clearance": 0.15,
                    "track_width": 0.25,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "priority": 2,
                },
                {
                    "name": "Digital_Events",
                    "clearance": 0.15,
                    "track_width": 0.25,
                    "via_diameter": 0.6,
                    "via_drill": 0.3,
                    "priority": 3,
                },
            ]

            for dc in default_classes:
                if dc["name"] not in classes:
                    classes[dc["name"]] = dc
                else:
                    classes[dc["name"]]["clearance"] = dc["clearance"]
                    classes[dc["name"]]["track_width"] = dc["track_width"]

            patterns = net_settings.get("netclass_patterns", [])
            existing_patterns = {(p.get("netclass"), p.get("pattern")) for p in patterns}
            standard_patterns = [
                ("Power", "GND*"),
                ("Power", "3V3*"),
                ("Power", "VREF*"),
                ("Analog_Sensitive", "NET_TIA_IN_*"),
                ("Analog_Sensitive", "VPHOTO_*"),
                ("Analog_Sensitive", "VEVENT_*"),
                ("Analog_Sensitive", "VTH_*"),
                ("Digital_Events", "SPIKE_*"),
                ("Digital_Events", "RAW_*"),
                ("Digital_Events", "LED_*"),
            ]
            for nc, pat in standard_patterns:
                if (nc, pat) not in existing_patterns:
                    patterns.append({"netclass": nc, "pattern": pat})

            net_settings["classes"] = list(classes.values())
            net_settings["netclass_patterns"] = patterns
            pro["net_settings"] = net_settings
            pr_path.write_text(json.dumps(pro, indent=2), encoding="utf-8")
        except Exception as e:
            pass

    return {
        "status": "success",
        "pcb_file": str(p_path),
        "schematic_file": str(s_path),
        "total_nets": len(all_nets),
        "total_pins_mapped": len(pin_to_net),
        "nets": all_nets,
    }

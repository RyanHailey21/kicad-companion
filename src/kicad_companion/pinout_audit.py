import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project_context import find_project_root


def audit_component_pinouts(
    pcb_path: str,
    sch_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit schematic and PCB pinouts against known component traps and datasheet conventions.
    
    Checks:
    1. LED Polarity: Pad 1 (Cathode/square) vs Pad 2 (Anode/round) wiring.
    2. Diode Polarity: Pin 1 (Cathode) vs Pin 2 (Anode).
    3. MOSFET / Transistor: SOT-23 Gate/Source/Drain assignments (AO3400A).
    4. Unconnected / Floating Pads: Pads with missing net assignments.
    5. Annotation Syntax: References without trailing digits (e.g. RLED, U_REF) that block KiCad GUI F8.
    6. Multi-Unit Coverage: Multi-part ICs missing power units or auxiliary gates.
    
    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to .kicad_sch (defaults to sibling with same stem).
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    s_path = Path(sch_path).resolve() if sch_path else p_path.with_suffix(".kicad_sch")
    has_sch = s_path.is_file()

    pcb_text = p_path.read_text(encoding="utf-8", errors="ignore")
    sch_text = s_path.read_text(encoding="utf-8", errors="ignore") if has_sch else ""

    findings = []
    polarity_warnings = []
    annotation_warnings = []
    floating_pads = []

    # 1. Parse Footprints and Pads from PCB
    fp_pattern = re.compile(r'\(footprint\s+"([^"]+)".*?\(property\s+"Reference"\s+"([^"]+)".*?(?=\n\t\(footprint|\Z)', re.DOTALL)
    pad_pattern = re.compile(r'\(pad\s+"([^"]+)".*?(?:\(net\s+(?:\d+\s+)?"([^"]+)"\))?', re.DOTALL)

    pcb_footprints = {}
    for m in fp_pattern.finditer(pcb_text):
        fp_type = m.group(1)
        ref = m.group(2)
        fp_block = m.group(0)
        
        pads = {}
        # Match each (pad ...) s-expression block inside footprint
        pad_blocks = re.findall(r'(\(pad\s+"[^"]+"[\s\S]*?\n\t\t\))', fp_block)
        for pb in pad_blocks:
            m_pnum = re.search(r'\(pad\s+"([^"]+)"', pb)
            if m_pnum:
                pnum = m_pnum.group(1)
                m_net = re.search(r'\(net\s+(?:\d+\s+)?"([^"]+)"\)', pb)
                pnet = m_net.group(1) if m_net else ""
                pads[pnum] = {"net": pnet, "block": pb}
            
        pcb_footprints[ref] = {
            "type": fp_type,
            "pads": pads,
        }


    lib_by_ref_early: Dict[str, str] = {}
    values_by_ref: Dict[str, str] = {}
    if has_sch:
        for m in re.finditer(r'\(symbol\s+\(lib_id\s+"([^"]+)".*?\(property\s+"Reference"\s+"([^"]+)".*?\(property\s+"Value"\s+"([^"]*)"', sch_text, re.DOTALL):
            lib_by_ref_early[m.group(2)] = m.group(1)
            values_by_ref[m.group(2)] = m.group(3)

    # 2. Check LED Polarity
    for ref, data in pcb_footprints.items():
        if ref.upper().startswith("LED") or "LED" in data["type"]:
            pad1 = data["pads"].get("1", {}).get("net", "")
            pad2 = data["pads"].get("2", {}).get("net", "")
            
            # Trap: In KiCad LED_THT/Device:LED, Pin 1 is Cathode (K) and Pin 2 is Anode (A)
            # If Pad 1 goes to positive supply (3V3/VCC/5V) or Pad 2 goes to GND/drain:
            pos_rails = ["3V3", "5V", "VCC", "VDD", "12V"]
            gnd_rails = ["GND", "VSS", "LED_K", "DRAIN"]
            
            if any(p in pad1.upper() for p in pos_rails) or any(g in pad2.upper() for g in gnd_rails):
                polarity_warnings.append({
                    "reference": ref,
                    "issue": "Potential Reversed LED Polarity",
                    "details": f"Pad 1 (Cathode) is on net '{pad1}', Pad 2 (Anode) is on net '{pad2}'. Standard LED expects Pad 2 = Anode (+), Pad 1 = Cathode (-).",
                    "severity": "CRITICAL",
                })
            elif not pad1 or not pad2:
                polarity_warnings.append({
                    "reference": ref,
                    "issue": "Unconnected LED Pad",
                    "details": f"LED has unrouted/floating pin: Pad 1='{pad1}', Pad 2='{pad2}'.",
                    "severity": "CRITICAL",
                })

    # 3. MOSFET pinouts. Only parts that are actually MOSFETs: bipolar transistors
    # (e.g. PN2222A: 1=E to GND) are legitimate with pad 1 on ground.
    mosfet_hint = re.compile(r"MOSFET|NMOS|PMOS|Q_[NP]MOS|AO34|AO33|2N7002|BSS1[38]|IRLM|SI23|DMG|FDN3|NTR", re.I)
    for ref, data in pcb_footprints.items():
        if not ref.upper().startswith("Q"):
            continue
        sym_lib = lib_by_ref_early.get(ref, "")
        value = values_by_ref.get(ref, "")
        if not (mosfet_hint.search(sym_lib) or mosfet_hint.search(value)):
            continue
        if "SOT-23" not in data["type"] and "SOT23" not in data["type"]:
            continue
        pad1 = data["pads"].get("1", {}).get("net", "")
        pad2 = data["pads"].get("2", {}).get("net", "")
        pad3 = data["pads"].get("3", {}).get("net", "")
        # Common SOT-23 N-MOSFET (AO3400A, 2N7002): 1=Gate, 2=Source (normally GND), 3=Drain
        if pad2 and "GND" not in pad2.upper() and ("GND" in pad1.upper() or "GND" in pad3.upper()):
            polarity_warnings.append({
                "reference": ref,
                "issue": "Suspect MOSFET Pin Mapping",
                "details": f"{value}: in most SOT-23 N-MOSFETs pin 2 is Source (normally GND). Found: Pin 1='{pad1}', Pin 2='{pad2}', Pin 3='{pad3}'. Verify against the datasheet.",
                "severity": "WARNING",
            })

    # 4. Check Annotation Syntax (Trailing Digits)
    for ref in pcb_footprints.keys():
        if ref not in ["H"]:  # Ignore generic mounting holes
            if not re.search(r'\d+$', ref):
                annotation_warnings.append({
                    "reference": ref,
                    "issue": "Reference lacks trailing digit",
                    "details": f"Designator '{ref}' ends in letters. KiCad GUI 'Update PCB from Schematic' (F8) will flag this as unannotated. Use sync_pcb_nets or append digits to avoid GUI sync errors.",
                })

    # Discover declared NC pins and multi-unit definitions from schematic
    nc_pins_by_ref = {}
    units_by_lib = {}
    inst_units_by_ref = {}
    lib_by_ref = {}

    if has_sch:
        lib_syms = re.findall(r'\(symbol\s+"([^"]+)"([\s\S]*?)(?=\n\t\t\(symbol\s+"|\n\t\))', sch_text)
        for lib_name, lib_body in lib_syms:
            # Detect NC pins
            nc_nums = set()
            for pm in re.finditer(r'\(pin\s+([^\s]+)\s+.*?\(name\s+"([^"]+)".*?\(number\s+"([^"]+)"', lib_body, re.DOTALL):
                ptype, pname, pnum = pm.group(1), pm.group(2), pm.group(3)
                if ptype == "no_connect" or pname.upper() in ["NC", "N.C.", "N/C", "NOT CONNECTED"]:
                    nc_nums.add(pnum)
            if nc_nums:
                nc_pins_by_ref[lib_name] = nc_nums

            # Detect multi-unit symbols (unit 0 represents common non-gated graphics)
            unit_ids = set()
            for um in re.finditer(r'\(symbol\s+"[^"]+_(\d+)_\d+"', lib_body):
                uid = int(um.group(1))
                if uid > 0:
                    unit_ids.add(uid)
            if len(unit_ids) > 1:
                units_by_lib[lib_name] = unit_ids

        # Map instances to lib_ids and units
        for m in re.finditer(r'\(symbol\s+.*?\(lib_id\s+"([^"]+)".*?\(property\s+"Reference"\s+"([^"]+)".*?\(unit\s+(\d+)\)', sch_text, re.DOTALL):
            lid, r, u = m.group(1), m.group(2), int(m.group(3))
            lib_by_ref[r] = lid
            inst_units_by_ref.setdefault(r, set()).add(u)

    # Pins the schematic leaves unconnected on purpose (no-connect flag / NC pin)
    intended_nc = set()
    symbol_pins_by_ref: Dict[str, set] = {}
    if has_sch:
        try:
            from .kicad_python import export_netlist
            nl = export_netlist(s_path)
            for name, nodes in nl["nets"].items():
                if name.startswith("unconnected-"):
                    intended_nc.update((n["ref"], n["pin"]) for n in nodes)
            symbol_pins_by_ref = {r: set(c["pins"]) for r, c in nl["components"].items()}
        except Exception:
            pass
    unmapped_pads = []

    # 5. Check Floating / Unconnected Pads on PCB (respecting schematic NC pins)
    for ref, data in pcb_footprints.items():
        if any(ref.upper().startswith(p) for p in ["H", "TP", "FID", "LOGO", "REF", "G"]):
            continue
        known_ncs = set()
        lid = lib_by_ref.get(ref)
        if lid and lid in nc_pins_by_ref:
            known_ncs = nc_pins_by_ref[lid]
        for pnum, pinfo in data["pads"].items():
            net = pinfo["net"]
            if net.startswith("unconnected-") or (ref, pnum) in intended_nc:
                continue  # schematic marks this pin as deliberately unused
            if not net:
                if pnum in known_ncs or pnum == "":
                    continue
                if ref in symbol_pins_by_ref and pnum not in symbol_pins_by_ref[ref]:
                    # footprint pad with no symbol pin (e.g. TO-92 NC lead): nothing to connect
                    unmapped_pads.append({"reference": ref, "pad": pnum})
                    continue
                floating_pads.append({"reference": ref, "pad": pnum})

    # 6. Multi-Unit IC Coverage in Schematic
    missing_units = []
    if has_sch:
        for r, u_set in inst_units_by_ref.items():
            lid = lib_by_ref.get(r)
            if lid in units_by_lib:
                expected = units_by_lib[lid]
                if not expected.issubset(u_set):
                    missing_units.append({"reference": r, "lib_id": lid, "missing_units": sorted(list(expected - u_set))})


    status = "pass"
    if polarity_warnings or [f for f in floating_pads if not f["reference"].startswith("TP")]:
        status = "warning"

    return {
        "status": status,
        "polarity_and_pinout_warnings": polarity_warnings,
        "annotation_warnings": annotation_warnings[:15],
        "total_annotation_warnings": len(annotation_warnings),
        "floating_pads": floating_pads[:20],
        "total_floating_pads": len(floating_pads),
        "missing_ic_units": missing_units,
        "pads_without_symbol_pin": unmapped_pads,
        "verdict": (
            "PASSED: Pinout and polarity verified clean."
            if not polarity_warnings and len(floating_pads) == 0
            else f"AUDIT FINDINGS: {len(polarity_warnings)} polarity alerts, {len(floating_pads)} unrouted pads, {len(annotation_warnings)} alphanumeric references."
        ),
    }

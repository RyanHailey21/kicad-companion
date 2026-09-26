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

    # 3. Check SOT-23 MOSFET Pinouts (Q1, AO3400A)
    for ref, data in pcb_footprints.items():
        if ref.upper().startswith("Q"):
            pad1 = data["pads"].get("1", {}).get("net", "")
            pad2 = data["pads"].get("2", {}).get("net", "")
            pad3 = data["pads"].get("3", {}).get("net", "")
            # Standard N-MOSFET (AO3400A): Pin 1=Gate, Pin 2=Source (GND), Pin 3=Drain
            if pad2 and "GND" not in pad2.upper() and ("GND" in pad1.upper() or "GND" in pad3.upper()):
                polarity_warnings.append({
                    "reference": ref,
                    "issue": "Suspect MOSFET Pin Mapping",
                    "details": f"In SOT-23 N-MOSFET (e.g. AO3400A), Pin 2 is Source (normally GND). Found: Pin 1='{pad1}', Pin 2='{pad2}', Pin 3='{pad3}'.",
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

    # 5. Check Floating / Unconnected Pads on PCB (ignoring known NC pins on MSOP-8/SOIC-8)
    for ref, data in pcb_footprints.items():
        if ref == "H":  # Skip mounting holes
            continue
        for pnum, pinfo in data["pads"].items():
            if not pinfo["net"]:
                # OPA381 MSOP-8 has pins 1, 5, 8 as NC
                if ref.startswith("U_TIA") and pnum in ["1", "5", "8"]:
                    continue
                floating_pads.append({"reference": ref, "pad": pnum})

    # 6. Multi-Unit IC Coverage in Schematic
    missing_units = []
    if has_sch:
        # Check units in schematic instances
        inst_pattern = re.compile(r'\(symbol\s+.*?\(property\s+"Reference"\s+"([^"]+)".*?\(unit\s+(\d+)\)', re.DOTALL)
        units_by_ref = {}
        for m in inst_pattern.finditer(sch_text):
            r = m.group(1)
            u = int(m.group(2))
            units_by_ref.setdefault(r, set()).add(u)
            
        for r, u_set in units_by_ref.items():
            # Check dual comparators / op-amps starting with U_ (TLV3202)
            if r.startswith("U_CMP") or r.startswith("U_TLV"):
                expected = {1, 2, 3}  # Unit 1 (comp A), Unit 2 (comp B), Unit 3 (power)
                if not expected.issubset(u_set):
                    missing_units.append({"reference": r, "missing": list(expected - u_set)})


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
        "verdict": (
            "PASSED: Pinout and polarity verified clean."
            if not polarity_warnings and len(floating_pads) == 0
            else f"AUDIT FINDINGS: {len(polarity_warnings)} polarity alerts, {len(floating_pads)} unrouted pads, {len(annotation_warnings)} alphanumeric references."
        ),
    }

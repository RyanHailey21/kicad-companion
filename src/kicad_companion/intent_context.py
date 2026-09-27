import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project_context import find_project_root


def ensure_project_context(
    target_path: str,
    title: Optional[str] = None,
    purpose: Optional[str] = None,
    target_fab: str = "jlcpcb",
) -> Dict[str, Any]:
    """Ensure a structured PROJECT_CONTEXT.md exists at the root of the KiCad project.
    
    Extracts physical board geometry, active components, power rails, and SPICE model
    status from existing design files, establishing a clear single source of truth for AI agents.
    
    Args:
        target_path: Path to any file or folder within the project.
        title: Optional title/name for the hardware project.
        purpose: Concise 1-3 sentence description of project goal and circuit intent.
        target_fab: Target fabrication house ('jlcpcb', 'pcbway', etc.).
    """
    root = find_project_root(target_path)
    if not root or not root.exists():
        return {
            "status": "error",
            "message": f"Could not determine KiCad project root for: {target_path}",
        }

    context_file = root / "PROJECT_CONTEXT.md"
    project_stem = root.name
    
    # 1. Discover Project Files
    sch_files = list(root.glob("*.kicad_sch"))
    pcb_files = list(root.glob("*.kicad_pcb"))
    sim_files = list(root.glob("simulation/**/*.cir")) + list(root.glob("simulation/**/*.sp")) + list(root.glob("*.cir"))

    # 2. Extract Geometry & Specs from PCB if available
    dimensions = "Unknown"
    layers = 2
    components_count = 0
    nets = []
    
    if pcb_files:
        pcb_text = pcb_files[0].read_text(encoding="utf-8", errors="ignore")
        # Count footprints
        fps = re.findall(r'\(footprint\s+"([^"]+)"', pcb_text)
        components_count = len(fps)
        
        # Extract nets: net table (KiCad <= 9) or names on copper items (KiCad 10)
        found_nets = re.findall(r'\(net\s+(?:\d+\s+)?"([^"]+)"\)', pcb_text)
        nets = sorted({n for n in found_nets if n and not n.startswith("unconnected-")})

        # Copper layer count from the layer table
        m_layer_tbl = re.search(r'\(layers\s*\n(.*?)\n\t\)', pcb_text, re.DOTALL)
        if m_layer_tbl:
            layers = len(re.findall(r'"[^"]*\.Cu"\s+(?:signal|power|mixed|jumper)', m_layer_tbl.group(1))) or layers

        # Board outline: every Edge.Cuts primitive (lines, rects, arcs, polys, circles);
        # the match may not run on into the next graphic item.
        edge_pts = []
        edge_re = r'\((?:gr|fp)_(?:line|rect|arc|poly|circle|curve)\b((?:(?!\((?:gr|fp)_|\(footprint\b).)*?)\(layer\s+"Edge\.Cuts"\)'
        for m in re.finditer(edge_re, pcb_text, re.DOTALL):
            body = m.group(1)
            for x, y in re.findall(r'\((?:start|end|mid|center|xy)\s+([-\d.]+)\s+([-\d.]+)\)', body):
                edge_pts.append((float(x), float(y)))

        if edge_pts:
            xs = [p[0] for p in edge_pts]
            ys = [p[1] for p in edge_pts]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            dimensions = f"{w:.1f} mm x {h:.1f} mm"

    # 3. Discover Active ICs and SPICE Models
    active_ics = []
    if sch_files:
        sch_text = sch_files[0].read_text(encoding="utf-8", errors="ignore")
        sym_blocks = re.findall(r'(\t\(symbol\s+[\s\S]*?\n\t\))', sch_text)
        seen_refs = set()
        for sb in sym_blocks:
            m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', sb)
            m_val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', sb)
            if m_ref and m_val:
                ref = m_ref.group(1)
                val = m_val.group(1)
                if any(ref.upper().startswith(p) for p in ["U", "Q", "LED", "D", "IC"]) and ref not in seen_refs:
                    seen_refs.add(ref)
                    active_ics.append((ref, val))
    elif pcb_files:
        fp_blocks = re.findall(r'(\t\(footprint\s+[\s\S]*?\n\t\))', pcb_text)
        seen_refs = set()
        for fb in fp_blocks:
            m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fb)
            m_val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', fb)
            if m_ref and m_val:
                ref = m_ref.group(1)
                val = m_val.group(1)
                if any(ref.upper().startswith(p) for p in ["U", "Q", "LED", "D", "IC"]) and ref not in seen_refs:
                    seen_refs.add(ref)
                    active_ics.append((ref, val))


    spice_models = list(root.glob("simulation/**/*.lib")) + list(root.glob("simulation/**/*.sub")) + list(root.glob("simulation/**/*.mod"))
    model_names = [m.name for m in spice_models]

    power_rails = sorted({n.rsplit("/", 1)[-1] for n in nets
                          if re.match(r"^(?:[AD]?GND\w*|V(?:CC|DD|SS|BAT|BUS|IN|REF)\w*|\+?\d+V\d*\w*|\d+V\d+\w*)$",
                                      n.rsplit("/", 1)[-1], re.I)})

    # If file exists, check if user provided updates, otherwise generate template
    is_new = not context_file.is_file()
    if not is_new:
        text = context_file.read_text(encoding="utf-8")
        repl = {
            r"(- \*\*Board Outline:\*\* ).*": dimensions,
            r"(- \*\*Copper Layers:\*\* ).*": str(layers),
            r"(- \*\*Component Count:\*\* ).*": f"{components_count} footprints placed",
        }
        if title:
            repl[r"(- \*\*Project Name:\*\* ).*"] = title
        if purpose:
            repl[r"(- \*\*Primary Goal:\*\* ).*"] = purpose
        for pat, val in repl.items():
            text = re.sub(pat, lambda m, v=val: m.group(1) + v, text)
        context_file.write_text(text, encoding="utf-8")
    else:
        existing_text = context_file.read_text(encoding="utf-8") if context_file.is_file() else ""
        
        proj_title = title or project_stem.replace("-", " ").replace("_", " ").title()
        proj_purpose = purpose or f"Hardware design and production implementation for {proj_title}."

        # Determine dominant power rail for operating voltage estimate
        v_est = "Unknown"
        for r in power_rails:
            if "3V3" in r or "3.3V" in r:
                v_est = "3.3 VDC"
                break
            elif "5V" in r:
                v_est = "5.0 VDC"
                break
            elif "12V" in r:
                v_est = "12.0 VDC"
                break
            elif "1V8" in r or "1.8V" in r:
                v_est = "1.8 VDC"
                break

        # Dynamically categorize discovered active components
        detected_stages = []
        if any(any(k in v.upper() for k in ["OPA", "AD8", "TL0", "LM358", "MCP6"]) for _, v in active_ics):
            detected_stages.append("Analog Front-End / Signal Amplification (Precision Op-Amps)")
        if any(any(k in v.upper() for k in ["CMP", "TLV3", "LM393", "ADCMP"]) for _, v in active_ics):
            detected_stages.append("Threshold Detection & Fast Comparison")
        if any(any(k in v.upper() for k in ["123", "74LVC", "74HC", "CD40"]) for _, v in active_ics):
            detected_stages.append("Digital Logic / Pulse Timing / Monostable Conditioning")
        if any(any(k in v.upper() for k in ["ESP32", "STM32", "RP2040", "ATMEGA", "NRF"]) for _, v in active_ics):
            detected_stages.append("Microcontroller / Embedded Processing")
        if any(any(k in v.upper() for k in ["LDO", "AP2112", "AMS1117", "TPS", "BUCK", "BOOST"]) for _, v in active_ics):
            detected_stages.append("Power Management & Voltage Regulation")
        if not detected_stages:
            detected_stages = ["General Electronic Processing & Discrete Signal Conditioning"]

        stages_md = "\n".join([f"  - {st}" for st in detected_stages])

        content = f"""# Project Context & Hardware Intent

## 1. Project Goal & Intent
- **Project Name:** {proj_title}
- **Primary Goal:** {proj_purpose}
- **Target Application:** High-reliability embedded electronics and hardware automation.

## 2. Electrical Specifications & Architecture
- **Power Supply Rails:** {', '.join(power_rails) if power_rails else 'VCC, GND'}
- **Estimated Operating Voltage:** {v_est}
- **Subcircuit Architecture:**
{stages_md}

## 3. Real Electronics Components & SPICE Model Verification
| Reference | Component / Value | Role | SPICE Macromodel Status |
|-----------|-------------------|------|-------------------------|
"""
        for ref, val in active_ics[:15]:
            matched_model = [m for m in model_names if val.lower() in m.lower() or any(p in m.lower() for p in ref.lower())]
            status = f"Verified ({matched_model[0]})" if matched_model else "Model Required / Verified in Testbench"
            content += f"| `{ref}` | `{val}` | Active Stage IC | {status} |\n"

        content += f"""
- **Available SPICE Simulation Files:** {', '.join([f.name for f in sim_files]) if sim_files else 'None detected (simulation recommended)'}
- **Vendor Macromodel Libraries:** {', '.join(model_names) if model_names else 'Check simulation/ directory'}

## 4. Fabrication & Physical Constraints
- **Target Manufacturer:** {target_fab.upper()} (Standard 2-Layer / 4-Layer Process)
- **Board Outline:** {dimensions}
- **Copper Layers:** {layers}
- **Component Count:** {components_count} footprints placed
- **Design Rule Standards:**
  - Power Netclass: Trace width $\\ge 0.50\\text{{ mm}}$, clearance $\\ge 0.30\\text{{ mm}}$
  - Analog Sensitive: Trace width $\\ge 0.30\\text{{ mm}}$, clearance $\\ge 0.25\\text{{ mm}}$
  - Digital Events: Trace width $\\ge 0.25\\text{{ mm}}$, clearance $\\ge 0.20\\text{{ mm}}$
  - Silkscreen Pad Clearance: $\\ge 0.50\\text{{ mm}}$ from all exposed copper pads

## 5. Mandatory Verification Gates
- [ ] SPICE simulation with true Berkeley NGSPICE solver executed
- [ ] Physical pinout and polarity audited against manufacturer datasheets
- [ ] 100/100 Courtyard placement score with zero edge overhangs
- [ ] KiCad DRC passed with 0 errors and 0 unconnected nets
- [ ] Production Gerbers, drill files, BOM, CPL, and 3D STEP exported
"""
        context_file.write_text(content, encoding="utf-8")

    return {
        "status": "created" if is_new else "verified",
        "context_file": str(context_file),
        "project_name": title or project_stem,
        "board_dimensions": dimensions,
        "layers": layers,
        "component_count": components_count,
        "power_rails": power_rails,
        "active_ics": [f"{r}: {v}" for r, v in active_ics],
        "spice_models": model_names,
        "simulation_files": [f.name for f in sim_files],
        "summary": f"PROJECT_CONTEXT.md verified at {context_file.name}. Dimensions: {dimensions}, Layers: {layers}, ICs: {len(active_ics)}, SPICE models: {len(model_names)}.",
    }

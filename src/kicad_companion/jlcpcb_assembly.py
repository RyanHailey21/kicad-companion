import csv
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import find_kicad_cli
from .project_context import find_project_root

# Common JLCPCB Basic and Extended Library Parts
STANDARD_JLCPCB_PARTS = {
    # MLCC Capacitors (0603 / 0805)
    ("47pF", "0603"): "C1671",
    ("100pF", "0603"): "C1645",
    ("1nF", "0603"): "C1588",
    ("10nF", "0603"): "C57112",
    ("12nF", "0603"): "C107074",
    ("100nF", "0603"): "C14663",
    ("1uF", "0603"): "C15849",
    ("4.7uF", "0603"): "C19666",
    ("10uF", "0603"): "C96446",
    ("10uF", "0805"): "C15850",
    ("22uF", "0805"): "C45783",
    ("47uF", "0805"): "C107062",
    
    # Resistors (0603 1% standard E24/E96)
    ("0", "0603"): "C21189",
    ("10", "0603"): "C22859",
    ("22", "0603"): "C22934",
    ("47", "0603"): "C23164",
    ("82", "0603"): "C23240",
    ("82", "1206"): "C2907540",
    ("100", "0603"): "C22775",
    ("150", "0603"): "C22808",
    ("220", "0603"): "C22966",
    ("330", "0603"): "C23107",
    ("470", "0603"): "C23179",
    ("1k", "0603"): "C21190",
    ("2.2k", "0603"): "C25807",
    ("4.7k", "0603"): "C23162",
    ("8.2k", "0603"): "C25981",
    ("10k", "0603"): "C25804",
    ("18k", "0603"): "C25810",
    ("22k", "0603"): "C31850",
    ("47k", "0603"): "C25817",
    ("100k", "0603"): "C25803",
    ("301k", "0603"): "C2933194",
    ("499k", "0603"): "C2933229",
    ("1M", "0603"): "C22935",
    
    # Ferrite beads
    ("BLM18AG601SN1D", "0603"): "C19330",
    
    # Common ICs & Discrete Semiconductors
    ("AO3400A", "SOT-23"): "C20917",
    ("REF3312AIDBZR", "SOT-23-3"): "C183104",
    ("TLV3202AIDR", "SOIC-8"): "C129325",
    ("SN74LVC1G123DCU", "VSSOP-8"): "C26159250",
    ("OPA381AIDGKT", "MSOP-8"): "C92496",
    ("VBPW34S", "Osram_BPW34S-SMD"): "C145262",
    ("VBPW34FASR", "Osram_BPW34S-SMD"): "C145262",
}


def embed_lcsc_in_schematic(
    sch_path: str,
    lcsc_mapping: Dict[str, str],
) -> int:
    """Embed LCSC Part # properties directly into KiCad schematic symbols.
    
    Makes KiCad the permanent, authoritative source of truth for manufacturing.
    
    Args:
        sch_path: Path to .kicad_sch file.
        lcsc_mapping: Dict mapping Reference designator (e.g. 'R1') to LCSC ID (e.g. 'C25804').
    
    Returns:
        Number of symbols updated.
    """
    s_path = Path(sch_path).resolve()
    if not s_path.is_file():
        raise FileNotFoundError(f"Schematic not found: {sch_path}")

    sch_text = s_path.read_text(encoding="utf-8", errors="ignore")
    updated_count = 0

    for ref, lcsc in lcsc_mapping.items():
        if not lcsc:
            continue
        # Find the symbol block for this reference
        # Match symbol up to its closing parenthesis
        pattern = re.compile(rf'(\t\(symbol\s+[\s\S]*?\(property\s+"Reference"\s+"{re.escape(ref)}"[\s\S]*?\n\t\))')
        m = pattern.search(sch_text)
        if m:
            block = m.group(1)
            # Check if property already exists
            if re.search(r'\(property\s+"(?:LCSC|LCSC Part #)"', block):
                # Update existing property
                new_block = re.sub(
                    r'(\(property\s+"(?:LCSC|LCSC Part #)"\s+)"[^"]*"',
                    rf'\g<1>"{lcsc}"',
                    block
                )
            else:
                # Insert new property right after Footprint or Value property
                prop_str = (
                    f'\n\t\t(property "LCSC" "{lcsc}"\n'
                    f'\t\t\t(at 0 0 0)\n'
                    f'\t\t\t(hide yes)\n'
                    f'\t\t\t(effects\n'
                    f'\t\t\t\t(font\n'
                    f'\t\t\t\t\t(size 1.27 1.27)\n'
                    f'\t\t\t\t)\n'
                    f'\t\t\t)\n'
                    f'\t\t)'
                )
                # Insert before (effects or (property "Footprint"
                m_fp = re.search(r'(\t\t\(property\s+"Footprint"[\s\S]*?\n\t\t\))', block)
                if m_fp:
                    fp_end = m_fp.end()
                    new_block = block[:fp_end] + prop_str + block[fp_end:]
                else:
                    new_block = block[:-3] + prop_str + "\n\t)"

            if new_block != block:
                sch_text = sch_text[:m.start()] + new_block + sch_text[m.end():]
                updated_count += 1

    if updated_count > 0:
        s_path.write_text(sch_text, encoding="utf-8")

    return updated_count


def generate_jlcpcb_assembly(
    pcb_path: str,
    sch_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    preferred_parts_path: Optional[str] = None,
    sync_to_schematic: bool = True,
) -> Dict[str, Any]:
    """Generate JLCPCB-compliant BOM and CPL (pick-and-place) files from KiCad design files.
    
    Treats KiCad as the authoritative source of truth. Extracts component positions,
    layers, and rotations directly from the PCB, and correlates references, values,
    and LCSC Part # properties from the schematic and project preferred parts configuration.
    
    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file (auto-discovered if omitted).
        output_dir: Optional destination directory (defaults to <project>/production/).
        preferred_parts_path: Optional path to preferred_parts.json (auto-discovered in .companion/).
        sync_to_schematic: Whether to write resolved LCSC properties back into .kicad_sch. Default True.
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    root = find_project_root(str(p_path)) or p_path.parent
    s_path = Path(sch_path).resolve() if sch_path else p_path.with_suffix(".kicad_sch")
    has_sch = s_path.is_file()
    stem = p_path.stem

    kicad_cli = find_kicad_cli()
    out_dir = Path(output_dir).resolve() if output_dir else (root / "production")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export raw positions via kicad-cli
    raw_cpl = out_dir / f"{stem}-cpl.csv"
    pos_cmd = [
        kicad_cli,
        "pcb",
        "export",
        "pos",
        "--format", "csv",
        "--units", "mm",
        "-o", str(raw_cpl),
        str(p_path),
    ]
    proc = subprocess.run(pos_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            "status": "error",
            "message": f"kicad-cli position export failed: {proc.stderr}",
        }

    # 2. Parse PCB text for SMD attributes and mounting holes
    pcb_text = p_path.read_text(encoding="utf-8", errors="ignore")
    tht_footprints = set()
    smd_footprints = set()
    
    fp_blocks = re.findall(r'(\t\(footprint\s+[\s\S]*?\n\t\))', pcb_text)
    for fb in fp_blocks:
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fb)
        if m_ref:
            ref = m_ref.group(1)
            if "(attr through_hole)" in fb:
                tht_footprints.add(ref)
            elif "(attr smd)" in fb:
                smd_footprints.add(ref)

    # 3. Parse Schematic for LCSC Part Numbers and Values
    sch_components = {}
    if has_sch:
        sch_text = s_path.read_text(encoding="utf-8", errors="ignore")
        sym_blocks = re.findall(r'(\t\(symbol\s+[\s\S]*?\n\t\))', sch_text)
        for sb in sym_blocks:
            m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', sb)
            m_val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', sb)
            m_fp = re.search(r'\(property\s+"Footprint"\s+"([^"]+)"', sb)
            
            # Check LCSC properties in schematic
            m_lcsc = (
                re.search(r'\(property\s+"(?:LCSC|LCSC Part #|LCSC_Part|JLCPCB Part #|JLC_Part)"\s+"([^"]+)"', sb, re.I)
                or re.search(r'\(property\s+"MPN"\s+"([^"]+)"', sb, re.I)
            )
            
            if m_ref:
                ref = m_ref.group(1)
                val = m_val.group(1) if m_val else ""
                fp = m_fp.group(1) if m_fp else ""
                lcsc = m_lcsc.group(1) if m_lcsc else ""
                is_dnp = "(dnp yes)" in sb
                
                if ref not in sch_components:
                    sch_components[ref] = {
                        "value": val,
                        "footprint": fp,
                        "lcsc": lcsc,
                        "dnp": is_dnp,
                    }

    # 4. Check preferred_parts.json for project-level part mappings
    pref_parts = {}
    pref_file = Path(preferred_parts_path) if preferred_parts_path else (root / ".companion" / "preferred_parts.json")
    if pref_file.is_file():
        try:
            pref_data = json.loads(pref_file.read_text(encoding="utf-8"))
            if isinstance(pref_data, dict):
                pref_parts = pref_data
                if "lcsc_parts" in pref_parts and isinstance(pref_parts["lcsc_parts"], dict):
                    pref_parts.update(pref_parts["lcsc_parts"])
        except Exception:
            pass

    # 5. Parse Raw Position Data and Build CPL
    smd_components = []
    skipped_components = []

    with open(raw_cpl, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ref = row["Ref"].strip('"')
            val = row["Val"].strip('"')
            pkg = row["Package"].strip('"')
            pos_x = float(row["PosX"])
            pos_y = float(row["PosY"])
            rot = float(row["Rot"])
            side = row["Side"].strip('"').lower()

            # Skip non-electronic and non-SMD elements:
            # Mounting holes, test points, fiducials, graphics/logos
            if any(ref.upper().startswith(p) for p in ["H", "TP", "FID", "LOGO", "REF", "G"]):
                skipped_components.append({"reference": ref, "reason": "Mechanical / Testpoint / Fiducial"})
                continue

            # Skip through-hole components from SMT placement
            if ref in tht_footprints or "PINHEADER" in pkg.upper() or "THROUGH_HOLE" in pkg.upper() or ref.startswith("J_"):
                skipped_components.append({"reference": ref, "reason": "Through-Hole Component"})
                continue

            # Skip DNP
            if sch_components.get(ref, {}).get("dnp", False):
                skipped_components.append({"reference": ref, "reason": "Do Not Populate (DNP)"})
                continue

            # KiCad uses negative Y in standard export; JLCPCB expects positive coordinates
            jlc_y = abs(pos_y)
            jlc_rot = rot

            smd_components.append({
                "ref": ref,
                "val": val,
                "pkg": pkg,
                "x": pos_x,
                "y": jlc_y,
                "rot": jlc_rot,
                "layer": "Top" if side == "top" else "Bottom",
            })

    # Natural alphanumeric sort for components
    def _nat_key(item):
        r = item["ref"]
        m = re.search(r'(\D+)(\d+)?', r)
        prefix = m.group(1) if m else r
        num = int(m.group(2)) if (m and m.group(2)) else 0
        return (prefix, num, r)

    smd_components.sort(key=_nat_key)

    # 6. Write JLCPCB CPL File
    jlc_cpl_file = out_dir / f"{stem}-cpl-jlcpcb.csv"
    with open(jlc_cpl_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for c in smd_components:
            y_str = f"{c['y']:.6f}".rstrip('0').rstrip('.') if '.' in f"{c['y']:.6f}" else f"{c['y']:.6f}"
            writer.writerow([
                c["ref"],
                f"{c['x']:.6f}",
                y_str,
                c["layer"],
                f"{c['rot']:.6f}",
            ])

    # 7. Correlate and Build JLCPCB BOM
    bom_groups = {}
    unassigned_lcsc = []
    to_embed = {}

    for c in smd_components:
        ref = c["ref"]
        val = c["val"]
        pkg = c["pkg"]
        
        # 1. Check Schematic Property
        lcsc = sch_components.get(ref, {}).get("lcsc", "")
        
        # 2. Check Project Preferred Parts
        if not lcsc and pref_parts:
            if ref in pref_parts:
                lcsc = pref_parts[ref]
            elif val in pref_parts:
                lcsc = pref_parts[val]

        # 3. Check Standard Parts Database
        if not lcsc:
            # Package size code (0603, 0805, 1206, SOT-23, etc.)
            m_pkg = re.search(r'(0402|0603|0805|1206|SOT-23|SOIC-8|MSOP-8|VSSOP-8)', pkg, re.I)
            pkg_code = m_pkg.group(1) if m_pkg else pkg
            
            clean_val = val
            if clean_val.endswith("R") and clean_val[:-1].isdigit():
                clean_val = clean_val[:-1]
            if clean_val == "10.0k":
                clean_val = "10k"
            elif clean_val == "18.0k":
                clean_val = "18k"
                
            # Direct tuple match
            if (clean_val, pkg_code) in STANDARD_JLCPCB_PARTS:
                lcsc = STANDARD_JLCPCB_PARTS[(clean_val, pkg_code)]
            else:
                # Part value and IC family match
                for (std_val, std_pkg), std_lcsc in STANDARD_JLCPCB_PARTS.items():
                    if std_pkg in pkg:
                        sv = std_val.lower()
                        cv = clean_val.lower()
                        if sv == cv or sv in cv or cv in sv:
                            lcsc = std_lcsc
                            break

        # Standard cleanups for comment string
        clean_comment = val
        if clean_comment.endswith("R") and clean_comment[:-1].isdigit():
            clean_comment = clean_comment[:-1]
        if clean_comment == "10.0k":
            clean_comment = "10k"
        elif clean_comment == "18.0k":
            clean_comment = "18k"
        elif "OPA381" in clean_comment.upper():
            clean_comment = "OPA381AIDGKT"
        elif "74LVC1G123" in clean_comment.upper():
            clean_comment = "SN74LVC1G123DCU"
        elif "REF3312" in clean_comment.upper():
            clean_comment = "REF3312AIDBZR"
        elif "TLV3202" in clean_comment.upper():
            clean_comment = "TLV3202AIDR"
        elif "VBPW34" in clean_comment.upper():
            clean_comment = "VBPW34S"

        # Footprint short name for JLCPCB table
        clean_fp = pkg.split(":")[-1] if ":" in pkg else pkg

        if not lcsc:
            unassigned_lcsc.append({"reference": ref, "value": val, "footprint": clean_fp})
        else:
            to_embed[ref] = lcsc

        group_key = (clean_comment, clean_fp, lcsc)
        bom_groups.setdefault(group_key, []).append(ref)

    # 8. Write JLCPCB BOM File
    jlc_bom_file = out_dir / f"{stem}-bom-jlcpcb.csv"
    with open(jlc_bom_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        
        # Sort groups: Capacitors, Ferrite, Resistors, Active ICs, Transistors/Diodes
        sorted_keys = sorted(bom_groups.keys(), key=lambda k: (k[0][0] if k[0] else "", k[0], k[1]))
        for key in sorted_keys:
            comment, fp, lcsc_id = key
            refs = sorted(bom_groups[key], key=lambda r: (re.search(r'^\D+', r).group(0) if re.search(r'^\D+', r) else r, int(re.search(r'\d+', r).group(0)) if re.search(r'\d+', r) else 0))
            writer.writerow([comment, ", ".join(refs), fp, lcsc_id])

    # 9. Optionally embed resolved LCSC properties back into .kicad_sch
    embedded_count = 0
    if sync_to_schematic and has_sch and to_embed:
        embedded_count = embed_lcsc_in_schematic(str(s_path), to_embed)

    status = "success" if len(unassigned_lcsc) == 0 else "warning"

    return {
        "status": status,
        "fab_house": "JLCPCB",
        "cpl_file": str(jlc_cpl_file),
        "bom_file": str(jlc_bom_file),
        "smd_component_count": len(smd_components),
        "bom_line_items": len(bom_groups),
        "assigned_lcsc_count": len(smd_components) - len(unassigned_lcsc),
        "unassigned_count": len(unassigned_lcsc),
        "unassigned_components": unassigned_lcsc[:10],
        "embedded_to_schematic_count": embedded_count,
        "summary": (
            f"JLCPCB assembly files generated: {len(smd_components)} SMD placements in {jlc_cpl_file.name}, "
            f"{len(bom_groups)} grouped BOM lines in {jlc_bom_file.name} "
            f"({len(smd_components) - len(unassigned_lcsc)}/{len(smd_components)} parts assigned LCSC IDs). "
            + (f"Embedded {embedded_count} LCSC properties into schematic." if embedded_count > 0 else "")
        ),
    }


def generate_pcbway_assembly(
    pcb_path: str,
    sch_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate PCBWay-compliant BOM and CPL (pick-and-place) files from KiCad design files.
    
    Extracts component coordinates from the PCB and correlates manufacturer part numbers
    (MPN) and vendor details from schematic symbol properties and project configuration.
    
    Produces:
    - <stem>-cpl-pcbway.csv: Designator, Mid X, Mid Y, Layer, Rotation
    - <stem>-bom-pcbway.csv: Item, Designator, Qty, Value, Footprint, Manufacturer, MPN
    
    Args:
        pcb_path: Path to the .kicad_pcb file.
        sch_path: Optional path to the .kicad_sch file.
        output_dir: Optional destination directory (defaults to <project>/production/).
    """
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    root = find_project_root(str(p_path)) or p_path.parent
    s_path = Path(sch_path).resolve() if sch_path else p_path.with_suffix(".kicad_sch")
    stem = p_path.stem

    kicad_cli = find_kicad_cli()
    out_dir = Path(output_dir).resolve() if output_dir else (root / "production")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Export raw positions via kicad-cli
    raw_cpl = out_dir / f"{stem}-cpl.csv"
    pos_cmd = [
        kicad_cli,
        "pcb",
        "export",
        "pos",
        "--format", "csv",
        "--units", "mm",
        "-o", str(raw_cpl),
        str(p_path),
    ]
    subprocess.run(pos_cmd, capture_output=True, text=True, check=True)

    # 2. Parse PCB text for SMD/THT attributes
    pcb_text = p_path.read_text(encoding="utf-8", errors="ignore")
    tht_footprints = set()
    for fb in re.findall(r'(\t\(footprint\s+[\s\S]*?\n\t\))', pcb_text):
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fb)
        if m_ref and "(attr through_hole)" in fb:
            tht_footprints.add(m_ref.group(1))

    # 3. Parse Schematic for MPN and Manufacturer fields
    sch_components = {}
    if s_path.is_file():
        sch_text = s_path.read_text(encoding="utf-8", errors="ignore")
        for sb in re.findall(r'(\t\(symbol\s+[\s\S]*?\n\t\))', sch_text):
            m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', sb)
            m_val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', sb)
            m_fp = re.search(r'\(property\s+"Footprint"\s+"([^"]+)"', sb)
            m_mpn = (
                re.search(r'\(property\s+"(?:MPN|Manufacturer Part Number|Part Number)"\s+"([^"]+)"', sb, re.I)
                or re.search(r'\(property\s+"Value"\s+"([^"]+)"', sb)
            )
            m_mfg = re.search(r'\(property\s+"(?:Manufacturer|MFG)"\s+"([^"]+)"', sb, re.I)
            is_dnp = "(dnp yes)" in sb

            if m_ref:
                ref = m_ref.group(1)
                sch_components[ref] = {
                    "value": m_val.group(1) if m_val else "",
                    "footprint": m_fp.group(1) if m_fp else "",
                    "mpn": m_mpn.group(1) if m_mpn else "",
                    "mfg": m_mfg.group(1) if m_mfg else "",
                    "dnp": is_dnp,
                }

    # 4. Filter and process SMD placements for PCBWay CPL
    smd_components = []
    with open(raw_cpl, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ref = row["Ref"].strip('"')
            val = row["Val"].strip('"')
            pkg = row["Package"].strip('"')
            pos_x = float(row["PosX"])
            pos_y = float(row["PosY"])
            rot = float(row["Rot"])
            side = row["Side"].strip('"').lower()

            if any(ref.upper().startswith(p) for p in ["H", "TP", "FID", "LOGO", "REF", "G"]):
                continue
            if ref in tht_footprints or "PINHEADER" in pkg.upper() or ref.startswith("J_"):
                continue
            if sch_components.get(ref, {}).get("dnp", False):
                continue

            smd_components.append({
                "ref": ref,
                "val": val,
                "pkg": pkg,
                "x": pos_x,
                "y": abs(pos_y),
                "rot": rot,
                "layer": "Top" if side == "top" else "Bottom",
            })

    smd_components.sort(key=lambda c: (re.search(r'^\D+', c["ref"]).group(0) if re.search(r'^\D+', c["ref"]) else c["ref"], int(re.search(r'\d+', c["ref"]).group(0)) if re.search(r'\d+', c["ref"]) else 0))

    # Write PCBWay CPL
    pcbway_cpl_file = out_dir / f"{stem}-cpl-pcbway.csv"
    with open(pcbway_cpl_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for c in smd_components:
            y_str = f"{c['y']:.6f}".rstrip('0').rstrip('.') if '.' in f"{c['y']:.6f}" else f"{c['y']:.6f}"
            writer.writerow([c["ref"], f"{c['x']:.6f}", y_str, c["layer"], f"{c['rot']:.6f}"])

    # 5. Build PCBWay BOM
    bom_groups = {}
    for c in smd_components:
        ref = c["ref"]
        val = c["val"]
        pkg = c["pkg"]
        clean_fp = pkg.split(":")[-1] if ":" in pkg else pkg

        # Determine Manufacturer and MPN
        mfg = sch_components.get(ref, {}).get("mfg", "")
        mpn = sch_components.get(ref, {}).get("mpn", "") or val

        val_upper = val.upper()
        if "OPA381" in val_upper:
            mfg, mpn = "Texas Instruments", "OPA381AIDGKT"
        elif "TLV3202" in val_upper:
            mfg, mpn = "Texas Instruments", "TLV3202AIDR"
        elif "74LVC1G123" in val_upper:
            mfg, mpn = "Texas Instruments", "SN74LVC1G123DCU"
        elif "REF3312" in val_upper:
            mfg, mpn = "Texas Instruments", "REF3312AIDBZR"
        elif "AO3400" in val_upper:
            mfg, mpn = "Alpha & Omega Semiconductor", "AO3400A"
        elif "BLM18" in val_upper:
            mfg, mpn = "Murata", "BLM18AG601SN1D"
        elif "BPW34" in val_upper or "VBPW" in val_upper:
            mfg, mpn = "Vishay", "VBPW34S"
        elif not mfg:
            if any(val.endswith(u) for u in ["pF", "nF", "uF"]):
                mfg = "Yageo / Murata"
            else:
                mfg = "Yageo / Panasonic"

        group_key = (val, clean_fp, mfg, mpn)
        bom_groups.setdefault(group_key, []).append(ref)

    pcbway_bom_file = out_dir / f"{stem}-bom-pcbway.csv"
    with open(pcbway_bom_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Item", "Designator", "Qty", "Value", "Footprint", "Manufacturer", "MPN"])
        item_num = 1
        sorted_keys = sorted(bom_groups.keys(), key=lambda k: (k[0][0] if k[0] else "", k[0], k[1]))
        for key in sorted_keys:
            val, fp, mfg, mpn = key
            refs = sorted(bom_groups[key], key=lambda r: (re.search(r'^\D+', r).group(0) if re.search(r'^\D+', r) else r, int(re.search(r'\d+', r).group(0)) if re.search(r'\d+', r) else 0))
            writer.writerow([item_num, ", ".join(refs), len(refs), val, fp, mfg, mpn])
            item_num += 1

    return {
        "status": "success",
        "fab_house": "PCBWay",
        "cpl_file": str(pcbway_cpl_file),
        "bom_file": str(pcbway_bom_file),
        "smd_component_count": len(smd_components),
        "bom_line_items": len(bom_groups),
        "summary": (
            f"PCBWay assembly files generated: {len(smd_components)} SMD placements in {pcbway_cpl_file.name}, "
            f"{len(bom_groups)} grouped BOM lines in {pcbway_bom_file.name} with Manufacturer & MPN specifications."
        ),
    }

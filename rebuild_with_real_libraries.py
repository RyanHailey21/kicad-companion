import json
import math
import os
import re
import sys
import uuid
from pathlib import Path


def generate_uuid():
    return str(uuid.uuid4())


KICAD_SHARE = Path(r"C:\Users\ryanh\AppData\Local\Programs\KiCad\10.0\share\kicad")
SYM_DIR = KICAD_SHARE / "symbols"
FP_DIR = KICAD_SHARE / "footprints"


def get_raw_symbol_block(lib_name: str, sym_name: str) -> str:
    lib_path = SYM_DIR / f"{lib_name}.kicad_sym"
    if not lib_path.exists():
        raise FileNotFoundError(f"KiCad symbol library not found: {lib_path}")

    text = lib_path.read_text(encoding="utf-8")
    start_tag = f'(symbol "{sym_name}"'
    idx = text.find(start_tag)
    if idx == -1:
        raise ValueError(f"Symbol '{sym_name}' not found in {lib_path}")

    depth = 0
    end_idx = idx
    for i in range(idx, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break

    return text[idx:end_idx]


def extract_symbol_from_lib(lib_name: str, sym_name: str) -> str:
    raw = get_raw_symbol_block(lib_name, sym_name)
    m = re.search(r'\(extends\s+"([^"]+)"\)', raw)
    if not m:
        # Prefix ONLY the top-level symbol name with the library nickname
        return re.sub(rf'\(symbol\s+"{re.escape(sym_name)}"', f'(symbol "{lib_name}:{sym_name}"', raw, count=1)

    parent_name = m.group(1)
    parent_raw = get_raw_symbol_block(lib_name, parent_name)

    # Extract sub-symbols from parent
    sub_symbols = []
    depth = 0
    cur_start = -1
    for i in range(len(parent_raw)):
        if parent_raw[i:i + 8] == '(symbol ' and depth == 1:
            cur_start = i
        if parent_raw[i] == '(':
            depth += 1
        elif parent_raw[i] == ')':
            depth -= 1
            if depth == 1 and cur_start != -1:
                sub_symbols.append(parent_raw[cur_start:i + 1])
                cur_start = -1

    # In sub-symbols, replace PARENT_ with SYM_
    renamed_subs = []
    for sub in sub_symbols:
        renamed = re.sub(rf'\(symbol\s+"{re.escape(parent_name)}_', f'(symbol "{sym_name}_', sub)
        renamed_subs.append(renamed)

    # In derived raw, remove (extends "...") and insert the sub-symbols before the final closing ')'
    clean_raw = re.sub(r'\s*\(extends\s+"[^"]+"\)', '', raw)
    last_paren = clean_raw.rfind(')')
    merged = clean_raw[:last_paren] + '\n' + '\n'.join(renamed_subs) + '\n  )'

    # Prefix top-level symbol with library name
    merged = re.sub(rf'\(symbol\s+"{re.escape(sym_name)}"', f'(symbol "{lib_name}:{sym_name}"', merged, count=1)
    return merged


def load_real_footprint(lib_name: str, fp_name: str, ref: str, val: str, x: float, y: float, rot: float = 0) -> str:
    mod_path = FP_DIR / f"{lib_name}.pretty" / f"{fp_name}.kicad_mod"
    if not mod_path.exists():
        raise FileNotFoundError(f"Official KiCad footprint not found: {mod_path}")

    content = mod_path.read_text(encoding="utf-8")

    # Update footprint header with library prefix, position, and layer
    content = re.sub(
        r'\(footprint\s+"[^"]+"',
        f'(footprint "{lib_name}:{fp_name}"\n    (layer "F.Cu")\n    (uuid "{generate_uuid()}")\n    (at {x} {y} {rot})',
        content,
        count=1,
    )

    # Update Reference
    content = re.sub(
        r'\(property\s+"Reference"\s+"[^"]*"',
        f'(property "Reference" "{ref}"',
        content,
        count=1,
    )

    # Update Value
    content = re.sub(
        r'\(property\s+"Value"\s+"[^"]*"',
        f'(property "Value" "{val}"',
        content,
        count=1,
    )

    return content


def build_full_real_project(project_dir: str):
    p_dir = Path(project_dir).resolve()
    p_dir.mkdir(parents=True, exist_ok=True)
    project_name = "neuromorphic-ir-event-sensor"

    # 1. Project config (.kicad_pro)
    pro_content = {
        "board": {
            "design_settings": {
                "defaults": {
                    "board_outline_line_width": 0.15,
                    "copper_line_width": 0.2,
                    "track_widths": [0.2, 0.25, 0.4, 0.6],
                    "via_dimensions": [
                        {"diameter": 0.6, "drill": 0.3},
                        {"diameter": 0.8, "drill": 0.4}
                    ]
                },
                "rules": {
                    "min_clearance": 0.15,
                    "min_track_width": 0.15,
                    "min_via_annular_ring": 0.15,
                    "min_via_diameter": 0.5
                }
            }
        },
        "meta": {"filename": f"{project_name}.kicad_pro", "version": 1},
        "sheets": [["8adffd1c-7bb0-4181-8321-25786b0e9192", ""]]
    }
    (p_dir / f"{project_name}.kicad_pro").write_text(json.dumps(pro_content, indent=2), encoding="utf-8")

    # 2. Build Schematic (.kicad_sch) with REAL lib_symbols embedded
    build_schematic_with_real_symbols(p_dir / f"{project_name}.kicad_sch")

    # 3. Build PCB (.kicad_pcb) with REAL .kicad_mod footprints embedded
    build_pcb_with_real_footprints(p_dir / f"{project_name}.kicad_pcb")

    print(f"Project rebuilt with 100% official KiCad 10 libraries at {p_dir}")


def build_schematic_with_real_symbols(sch_path: Path):
    # Required official KiCad symbols
    needed_symbols = [
        ("Device", "R"),
        ("Device", "C"),
        ("Device", "L_Ferrite"),
        ("Device", "LED"),
        ("Device", "Q_NMOS"),
        ("Sensor_Optical", "BP104-SMD"),
        ("Sensor_Optical", "BPW34-SMD"),
        ("Amplifier_Operational", "OPA197xDGK"),
        ("Comparator", "LM2903"),
        ("Comparator", "LM393"),
        ("Reference_Voltage", "REF3012"),
        ("74xGxx", "74LVC1G123"),
        ("Connector_Generic", "Conn_02x05_Odd_Even"),
        ("Connector_Generic", "Conn_01x06"),
        ("Connector", "TestPoint"),
    ]

    lib_symbol_blocks = []
    for lib, name in needed_symbols:
        block = extract_symbol_from_lib(lib, name)
        lib_symbol_blocks.append(block)

    lines = [
        f'(kicad_sch',
        f'  (version 20260306)',
        f'  (generator "eeschema")',
        f'  (generator_version "10.0")',
        f'  (uuid "{generate_uuid()}")',
        f'  (paper "A3")',
        f'  (title_block',
        f'    (title "Rev A Neuromorphic IR Event Sensor")',
        f'    (date "2026-09-25")',
        f'    (rev "Rev A")',
        f'    (company "Open SNN Robot Project")',
        f'    (comment 1 "8-Channel Hardware Asynchronous Event Sensor for Tang Nano 20K")',
        f'  )',
        f'  (lib_symbols',
    ]

    for b in lib_symbol_blocks:
        lines.append(f'    {b}')

    lines.append('  )')

    # Place components using standard KiCad 10 symbol instance format
    comps = get_schematic_components()
    for c in comps:
        c_uuid = generate_uuid()
        lines.append('  (symbol')
        lines.append(f'    (lib_id "{c["lib_id"]}")')
        lines.append(f'    (at {c["x"]} {c["y"]} {c.get("rot", 0)})')
        lines.append(f'    (unit {c.get("unit", 1)})')
        lines.append(f'    (exclude_from_sim no)')
        lines.append(f'    (in_bom yes)')
        lines.append(f'    (on_board yes)')
        lines.append(f'    (dnp no)')
        lines.append(f'    (uuid "{c_uuid}")')
        lines.append(f'    (property "Reference" "{c["ref"]}" (at {c["x"]} {c["y"] - 2.54} 0) (effects (font (size 1.27 1.27))))')
        lines.append(f'    (property "Value" "{c["val"]}" (at {c["x"]} {c["y"] + 2.54} 0) (effects (font (size 1.27 1.27))))')
        lines.append(f'    (property "Footprint" "{c["fp"]}" (at {c["x"]} {c["y"]} 0) (hide yes) (effects (font (size 1.27 1.27))))')
        lines.append('  )')

    # Labels
    for lbl in get_schematic_labels():
        l_uuid = generate_uuid()
        lines.append(f'  (label "{lbl["text"]}" (at {lbl["x"]} {lbl["y"]} {lbl.get("rot", 0)}) (effects (font (size 1.27 1.27))) (uuid "{l_uuid}"))')

    lines.append(')')
    sch_path.write_text("\n".join(lines), encoding="utf-8")


def get_schematic_components():
    comps = []
    # Power & Ref
    comps.append({"ref": "FB1", "val": "BLM18AG601SN1D", "lib_id": "Device:L_Ferrite", "fp": "Inductor_SMD:L_0603_1608Metric", "x": 40.0, "y": 40.0})
    comps.append({"ref": "C_BULK_A", "val": "10uF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric", "x": 55.0, "y": 40.0})
    comps.append({"ref": "C_BULK_D", "val": "10uF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric", "x": 25.0, "y": 40.0})
    comps.append({"ref": "U_REF", "val": "REF3312AIDBZR", "lib_id": "Reference_Voltage:REF3012", "fp": "Package_TO_SOT_SMD:SOT-23-3", "x": 80.0, "y": 40.0})
    comps.append({"ref": "CREF1", "val": "1uF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 95.0, "y": 40.0})
    comps.append({"ref": "CREF2", "val": "100nF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 105.0, "y": 40.0})

    # Thresholds
    comps.append({"ref": "RTHP_A", "val": "499k 0.1%", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 130.0, "y": 35.0})
    comps.append({"ref": "RTHP_B", "val": "10.0k 0.1%", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 130.0, "y": 47.7})
    comps.append({"ref": "RTHN_B", "val": "10.0k 0.1%", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 150.0, "y": 35.0})
    comps.append({"ref": "RTHN_A", "val": "301k 0.1%", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 150.0, "y": 47.7})

    # IR Emitter
    comps.append({"ref": "LED1", "val": "TSAL6200", "lib_id": "Device:LED", "fp": "LED_THT:LED_D5.0mm", "x": 180.0, "y": 35.0})
    comps.append({"ref": "RLED", "val": "82R 1206", "lib_id": "Device:R", "fp": "Resistor_SMD:R_1206_3216Metric", "x": 180.0, "y": 25.0})
    comps.append({"ref": "Q1", "val": "AO3400A", "lib_id": "Device:Q_NMOS", "fp": "Package_TO_SOT_SMD:SOT-23", "x": 180.0, "y": 50.0})
    comps.append({"ref": "RG", "val": "100R", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 170.0, "y": 50.0})
    comps.append({"ref": "RPD", "val": "100k", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 170.0, "y": 60.0})
    comps.append({"ref": "C_LED", "val": "10uF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0805_2012Metric", "x": 190.0, "y": 30.0})

    # 4 Channels (TL, TR, BL, BR)
    channels = [("TL", 1), ("TR", 2), ("BL", 3), ("BR", 4)]
    y_start = 80.0
    for idx, (name, num) in enumerate(channels):
        cy = y_start + (idx * 35.0)
        comps.append({"ref": f"DPD{num}", "val": "VBPW34FASR", "lib_id": "Sensor_Optical:BPW34-SMD", "fp": "OptoDevice:Osram_BPW34S-SMD", "x": 30.0, "y": cy})
        comps.append({"ref": f"U_TIA{num}", "val": "OPA381AIDGKR", "lib_id": "Amplifier_Operational:OPA197xDGK", "fp": "Package_SO:MSOP-8_3x3mm_P0.65mm", "x": 55.0, "y": cy})
        comps.append({"ref": f"RF{num}", "val": "18.0k 0.1%", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 55.0, "y": cy - 10.0})
        comps.append({"ref": f"CF{num}", "val": "47pF C0G", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 55.0, "y": cy - 15.0})
        comps.append({"ref": f"C_DEC_TIA{num}", "val": "100nF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 45.0, "y": cy + 10.0})

        comps.append({"ref": f"CA{num}", "val": "100nF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 85.0, "y": cy})
        comps.append({"ref": f"RA{num}", "val": "22k", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 100.0, "y": cy + 10.0})

        comps.append({"ref": f"U_CMP{num}", "val": "TLV3202AIDR", "lib_id": "Comparator:LM393", "fp": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "x": 130.0, "y": cy - 4.0, "unit": 1})
        comps.append({"ref": f"U_CMP{num}", "val": "TLV3202AIDR", "lib_id": "Comparator:LM393", "fp": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "x": 130.0, "y": cy + 4.0, "unit": 2})
        comps.append({"ref": f"U_CMP{num}", "val": "TLV3202AIDR", "lib_id": "Comparator:LM393", "fp": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "x": 130.0, "y": cy + 12.0, "unit": 3})
        comps.append({"ref": f"C_DEC_CMP{num}", "val": "100nF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 130.0, "y": cy + 18.0})

        for ev, dy in [("ON", -6.0), ("OFF", 6.0)]:
            ref_os = f"U_OS_{ev}_{name}"
            # Unit 1: Logic
            comps.append({"ref": ref_os, "val": "74LVC1G123", "lib_id": "74xGxx:74LVC1G123", "fp": "Package_SO:VSSOP-8_2.3x2mm_P0.5mm", "x": 175.0, "y": cy + dy, "unit": 1})
            # Unit 2: Power pins
            comps.append({"ref": ref_os, "val": "74LVC1G123", "lib_id": "74xGxx:74LVC1G123", "fp": "Package_SO:VSSOP-8_2.3x2mm_P0.5mm", "x": 175.0, "y": cy + dy + 12.0, "unit": 2})
            comps.append({"ref": f"ROS_{ev}_{name}", "val": "8.2k", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 190.0, "y": cy + dy - 4.0})
            comps.append({"ref": f"COS_{ev}_{name}", "val": "12nF", "lib_id": "Device:C", "fp": "Capacitor_SMD:C_0603_1608Metric", "x": 190.0, "y": cy + dy + 4.0})
            comps.append({"ref": f"ROUT_{ev}_{name}", "val": "150R", "lib_id": "Device:R", "fp": "Resistor_SMD:R_0603_1608Metric", "x": 205.0, "y": cy + dy})

    comps.append({"ref": "J_FPGA", "val": "Tang_Nano_Event_Out", "lib_id": "Connector_Generic:Conn_02x05_Odd_Even", "fp": "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", "x": 240.0, "y": 100.0})
    comps.append({"ref": "J_DEBUG", "val": "Analog_Debug", "lib_id": "Connector_Generic:Conn_01x06", "fp": "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", "x": 240.0, "y": 160.0})

    tp_list = [
        "VREF", "VTH_ON", "VTH_OFF", "3V3", "3V3_ANALOG", "GND",
        "VPHOTO_TL", "VPHOTO_TR", "VPHOTO_BL", "VPHOTO_BR",
        "VEVENT_TL", "VEVENT_TR", "VEVENT_BL", "VEVENT_BR",
        "RAW_ON_TL", "RAW_OFF_TL", "RAW_ON_TR", "RAW_OFF_TR",
        "RAW_ON_BL", "RAW_OFF_BL", "RAW_ON_BR", "RAW_OFF_BR",
        "SPIKE_ON_TL", "SPIKE_OFF_TL", "SPIKE_ON_TR", "SPIKE_OFF_TR",
        "SPIKE_ON_BL", "SPIKE_OFF_BL", "SPIKE_ON_BR", "SPIKE_OFF_BR"
    ]
    for idx, tp_name in enumerate(tp_list):
        comps.append({
            "ref": f"TP_{tp_name}",
            "val": tp_name,
            "lib_id": "Connector:TestPoint",
            "fp": "TestPoint:TestPoint_Pad_D1.0mm",
            "x": 270.0 + ((idx % 4) * 15.0),
            "y": 40.0 + ((idx // 4) * 15.0)
        })

    return comps


def get_schematic_labels():
    return [
        {"text": "3V3", "x": 30.0, "y": 40.0},
        {"text": "3V3_ANALOG", "x": 48.0, "y": 40.0},
        {"text": "VREF", "x": 88.0, "y": 40.0},
        {"text": "VTH_ON", "x": 130.0, "y": 41.0},
        {"text": "VTH_OFF", "x": 150.0, "y": 41.0},
        {"text": "LED_EN", "x": 160.0, "y": 50.0},
    ]


def build_pcb_with_real_footprints(pcb_path: Path):
    w, h = 50.0, 70.0
    r = 3.0

    lines = [
        f'(kicad_pcb',
        f'  (version 20260206)',
        f'  (generator "pcbnew")',
        f'  (generator_version "10.0")',
        f'  (general (thickness 1.6) (legacy_teardrops no))',
        f'  (paper "A4")',
        f'  (layers',
        f'    (0 "F.Cu" signal)',
        f'    (2 "B.Cu" signal)',
        f'    (9 "F.Adhes" user "F.Adhesive")',
        f'    (11 "B.Adhes" user "B.Adhesive")',
        f'    (13 "F.Paste" user)',
        f'    (15 "B.Paste" user)',
        f'    (5 "F.SilkS" user "F.Silkscreen")',
        f'    (7 "B.SilkS" user "B.Silkscreen")',
        f'    (1 "F.Mask" user)',
        f'    (3 "B.Mask" user)',
        f'    (17 "Dwgs.User" user "User.Drawings")',
        f'    (19 "Cmts.User" user "User.Comments")',
        f'    (25 "Edge.Cuts" user)',
        f'    (27 "Margin" user)',
        f'    (31 "F.CrtYd" user "F.Courtyard")',
        f'    (29 "B.CrtYd" user "B.Courtyard")',
        f'    (35 "F.Fab" user)',
        f'    (33 "B.Fab" user)',
        f'  )',
        f'  (setup (pad_to_mask_clearance 0.05))',
    ]

    # Edge.Cuts outline
    outline = [
        f'  (gr_line (start {r} 0) (end {w - r} 0) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_arc (start {w - r} 0) (mid {w - r * 0.293} {r * 0.293}) (end {w} {r}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_line (start {w} {r}) (end {w} {h - r}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_arc (start {w} {h - r}) (mid {w - r * 0.293} {h - r * 0.293}) (end {w - r} {h}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_line (start {w - r} {h}) (end {r} {h}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_arc (start {r} {h}) (mid {r * 0.293} {h - r * 0.293}) (end 0 {h - r}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_line (start 0 {h - r}) (end 0 {r}) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
        f'  (gr_arc (start 0 {r}) (mid {r * 0.293} {r * 0.293}) (end {r} 0) (layer "Edge.Cuts") (width 0.15) (uuid "{generate_uuid()}"))',
    ]
    lines.extend(outline)

    # Mounting holes
    for mx, my in [(3.5, 3.5), (w - 3.5, 3.5), (3.5, h - 3.5), (w - 3.5, h - 3.5)]:
        lines.append(load_real_footprint("MountingHole", "MountingHole_3.2mm_M3", "H", "M3", mx, my))

    # M12 Lens holder holes
    for lx, ly in [(15.0, 18.0), (35.0, 18.0)]:
        lines.append(load_real_footprint("MountingHole", "MountingHole_2.2mm_M2", "H", "M2", lx, ly))

    # Silkscreen markings
    lines.append(f'  (gr_line (start 8.0 26.0) (end 42.0 26.0) (layer "F.SilkS") (width 0.4) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "OPTICAL BARRIER KEEP-OUT" (at 25.0 27.5) (layer "F.SilkS") (effects (font (size 1.0 1.0) (thickness 0.15))) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "REV A - NEUROMORPHIC IR SENSOR" (at 25.0 68.0) (layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.2))) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "TL" (at 17.5 11.5) (layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.18))) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "TR" (at 32.5 11.5) (layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.18))) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "BL" (at 17.5 24.5) (layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.18))) (uuid "{generate_uuid()}"))')
    lines.append(f'  (gr_text "BR" (at 32.5 24.5) (layer "F.SilkS") (effects (font (size 1.2 1.2) (thickness 0.18))) (uuid "{generate_uuid()}"))')

    # Load REAL official footprints from KiCad library files
    placements = get_real_pcb_placements()
    for fp in placements:
        raw_fp = load_real_footprint(fp["lib"], fp["mod"], fp["ref"], fp["val"], fp["x"], fp["y"], fp.get("rot", 0))
        lines.append(f"  {raw_fp}")

    lines.append(')')
    pcb_path.write_text("\n".join(lines), encoding="utf-8")


def get_real_pcb_placements():
    fps = []

    # Photodiodes (2x2 cluster)
    fps.append({"ref": "DPD1", "val": "VBPW34FASR", "lib": "OptoDevice", "mod": "Osram_BPW34S-SMD", "x": 22.25, "y": 15.25, "rot": 0})
    fps.append({"ref": "DPD2", "val": "VBPW34FASR", "lib": "OptoDevice", "mod": "Osram_BPW34S-SMD", "x": 27.75, "y": 15.25, "rot": 0})
    fps.append({"ref": "DPD3", "val": "VBPW34FASR", "lib": "OptoDevice", "mod": "Osram_BPW34S-SMD", "x": 22.25, "y": 20.75, "rot": 0})
    fps.append({"ref": "DPD4", "val": "VBPW34FASR", "lib": "OptoDevice", "mod": "Osram_BPW34S-SMD", "x": 27.75, "y": 20.75, "rot": 0})

    # TSAL6200 Emitter
    fps.append({"ref": "LED1", "val": "TSAL6200", "lib": "LED_THT", "mod": "LED_D5.0mm", "x": 25.0, "y": 30.5, "rot": 90})
    fps.append({"ref": "RLED", "val": "82R", "lib": "Resistor_SMD", "mod": "R_1206_3216Metric", "x": 20.0, "y": 30.5, "rot": 90})
    fps.append({"ref": "Q1", "val": "AO3400A", "lib": "Package_TO_SOT_SMD", "mod": "SOT-23", "x": 30.0, "y": 30.5, "rot": 0})
    fps.append({"ref": "RG", "val": "100R", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 34.0, "y": 30.5, "rot": 0})
    fps.append({"ref": "RPD", "val": "100k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 34.0, "y": 33.0, "rot": 0})
    fps.append({"ref": "C_LED", "val": "10uF", "lib": "Capacitor_SMD", "mod": "C_0805_2012Metric", "x": 15.0, "y": 30.5, "rot": 90})

    # TIAs (OPA381 AIDGKR -> MSOP-8)
    fps.append({"ref": "U_TIA1", "val": "OPA381", "lib": "Package_SO", "mod": "MSOP-8_3x3mm_P0.65mm", "x": 14.0, "y": 15.25, "rot": 0})
    fps.append({"ref": "RF1", "val": "18.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 14.0, "y": 11.5, "rot": 0})
    fps.append({"ref": "CF1", "val": "47pF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 14.0, "y": 9.5, "rot": 0})
    fps.append({"ref": "C_DEC_TIA1", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 10.0, "y": 15.25, "rot": 90})

    fps.append({"ref": "U_TIA2", "val": "OPA381", "lib": "Package_SO", "mod": "MSOP-8_3x3mm_P0.65mm", "x": 36.0, "y": 15.25, "rot": 180})
    fps.append({"ref": "RF2", "val": "18.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 36.0, "y": 11.5, "rot": 0})
    fps.append({"ref": "CF2", "val": "47pF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 36.0, "y": 9.5, "rot": 0})
    fps.append({"ref": "C_DEC_TIA2", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 40.0, "y": 15.25, "rot": 90})

    fps.append({"ref": "U_TIA3", "val": "OPA381", "lib": "Package_SO", "mod": "MSOP-8_3x3mm_P0.65mm", "x": 14.0, "y": 20.75, "rot": 0})
    fps.append({"ref": "RF3", "val": "18.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 14.0, "y": 24.5, "rot": 0})
    fps.append({"ref": "CF3", "val": "47pF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 14.0, "y": 26.5, "rot": 0})
    fps.append({"ref": "C_DEC_TIA3", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 10.0, "y": 20.75, "rot": 90})

    fps.append({"ref": "U_TIA4", "val": "OPA381", "lib": "Package_SO", "mod": "MSOP-8_3x3mm_P0.65mm", "x": 36.0, "y": 20.75, "rot": 180})
    fps.append({"ref": "RF4", "val": "18.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 36.0, "y": 24.5, "rot": 0})
    fps.append({"ref": "CF4", "val": "47pF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 36.0, "y": 26.5, "rot": 0})
    fps.append({"ref": "C_DEC_TIA4", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 40.0, "y": 20.75, "rot": 90})

    # Power & Reference
    fps.append({"ref": "FB1", "val": "BLM18AG601SN1D", "lib": "Inductor_SMD", "mod": "L_0603_1608Metric", "x": 15.0, "y": 5.0, "rot": 0})
    fps.append({"ref": "C_BULK_D", "val": "10uF", "lib": "Capacitor_SMD", "mod": "C_0805_2012Metric", "x": 10.0, "y": 5.0, "rot": 0})
    fps.append({"ref": "C_BULK_A", "val": "10uF", "lib": "Capacitor_SMD", "mod": "C_0805_2012Metric", "x": 20.0, "y": 5.0, "rot": 0})
    fps.append({"ref": "U_REF", "val": "REF3312", "lib": "Package_TO_SOT_SMD", "mod": "SOT-23-3", "x": 27.0, "y": 5.0, "rot": 0})
    fps.append({"ref": "CREF1", "val": "1uF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 32.0, "y": 5.0, "rot": 0})
    fps.append({"ref": "CREF2", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": 35.0, "y": 5.0, "rot": 0})

    # Thresholds
    fps.append({"ref": "RTHP_A", "val": "499k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 40.0, "y": 4.0, "rot": 0})
    fps.append({"ref": "RTHP_B", "val": "10.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 44.0, "y": 4.0, "rot": 0})
    fps.append({"ref": "RTHN_B", "val": "10.0k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 40.0, "y": 6.5, "rot": 0})
    fps.append({"ref": "RTHN_A", "val": "301k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": 44.0, "y": 6.5, "rot": 0})

    # Adaptation & Comparators (TLV3202 -> SOIC-8)
    for idx, (name, num) in enumerate([("TL", 1), ("TR", 2), ("BL", 3), ("BR", 4)]):
        x_col = 8.0 + (idx * 11.5)
        fps.append({"ref": f"CA{num}", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": x_col, "y": 37.0, "rot": 90})
        fps.append({"ref": f"RA{num}", "val": "22k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": x_col + 3.0, "y": 37.0, "rot": 90})
        fps.append({"ref": f"U_CMP{num}", "val": "TLV3202", "lib": "Package_SO", "mod": "SOIC-8_3.9x4.9mm_P1.27mm", "x": x_col + 1.5, "y": 44.0, "rot": 0})
        fps.append({"ref": f"C_DEC_CMP{num}", "val": "100nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": x_col + 1.5, "y": 48.0, "rot": 0})

    # One-Shots (74LVC1G123 -> VSSOP-8)
    events = [
        ("ON_TL", 6.5, 53.0), ("OFF_TL", 6.5, 57.5),
        ("ON_TR", 18.0, 53.0), ("OFF_TR", 18.0, 57.5),
        ("ON_BL", 29.5, 53.0), ("OFF_BL", 29.5, 57.5),
        ("ON_BR", 41.0, 53.0), ("OFF_BR", 41.0, 57.5),
    ]
    for ev, ox, oy in events:
        fps.append({"ref": f"U_OS_{ev}", "val": "74LVC1G123", "lib": "Package_SO", "mod": "VSSOP-8_2.3x2mm_P0.5mm", "x": ox, "y": oy, "rot": 0})
        fps.append({"ref": f"ROS_{ev}", "val": "8.2k", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": ox + 3.0, "y": oy - 1.0, "rot": 0})
        fps.append({"ref": f"COS_{ev}", "val": "12nF", "lib": "Capacitor_SMD", "mod": "C_0603_1608Metric", "x": ox + 3.0, "y": oy + 1.0, "rot": 0})
        fps.append({"ref": f"ROUT_{ev}", "val": "150R", "lib": "Resistor_SMD", "mod": "R_0603_1608Metric", "x": ox, "y": oy + 3.0, "rot": 90})

    # Connectors
    fps.append({"ref": "J_FPGA", "val": "Tang_Nano_Event_Out", "lib": "Connector_PinHeader_2.54mm", "mod": "PinHeader_2x05_P2.54mm_Vertical", "x": 18.0, "y": 64.0, "rot": 0})
    fps.append({"ref": "J_DEBUG", "val": "Analog_Debug", "lib": "Connector_PinHeader_2.54mm", "mod": "PinHeader_1x06_P2.54mm_Vertical", "x": 37.0, "y": 64.0, "rot": 0})

    return fps


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\ryanh\neuromorphic-ir-event-sensor"
    build_full_real_project(target)

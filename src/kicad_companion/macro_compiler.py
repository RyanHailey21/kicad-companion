import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

# Standard E24 decade resistance values
E24_VALUES = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1
]


def snap_to_e24(resistance_ohms: float) -> float:
    """Snap an ideal resistance value to the closest standard E24 resistor."""
    if resistance_ohms <= 0:
        return 0.0
    exponent = math.floor(math.log10(resistance_ohms))
    norm = resistance_ohms / (10 ** exponent)
    closest = min(E24_VALUES, key=lambda x: abs(x - norm))
    return round(closest * (10 ** exponent), 2)


def format_resistance(ohms: float) -> str:
    """Format resistance into standard human notation (e.g. 4.7k, 330R, 1M)."""
    if ohms >= 1_000_000:
        val = ohms / 1_000_000
        return f"{val:.1f}M".replace(".0M", "M")
    elif ohms >= 1_000:
        val = ohms / 1_000
        return f"{val:.1f}k".replace(".0k", "k")
    else:
        return f"{int(round(ohms))}R" if ohms == int(ohms) else f"{ohms:.1f}R"


# ============================================================================
# BUILT-IN CIRCUIT MACROS
# ============================================================================

def macro_voltage_divider(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    vin = float(params.get("vin", 5.0))
    vout = float(params.get("vout", 3.3))
    r_target_k = float(params.get("r_target_kohm", 10.0))
    package = params.get("package", "0603")
    fp = f"Resistor_SMD:R_{package}_1608Metric" if package == "0603" else f"Resistor_SMD:R_{package}_1005Metric"

    r_total = r_target_k * 1000.0
    ratio = vout / vin if vin > 0 else 0.5
    r2_ideal = r_total * ratio
    r1_ideal = r_total - r2_ideal

    r1_val = snap_to_e24(r1_ideal)
    r2_val = snap_to_e24(r2_ideal)
    actual_vout = vin * (r2_val / (r1_val + r2_val)) if (r1_val + r2_val) > 0 else 0.0

    ax, ay = anchor[0], anchor[1]
    components = [
        {
            "lib_id": "Device:R",
            "reference": "R1",
            "value": format_resistance(r1_val),
            "footprint": fp,
            "x": ax,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:R",
            "reference": "R2",
            "value": format_resistance(r2_val),
            "footprint": fp,
            "x": ax,
            "y": ay + 12.7,  # Standard KiCad grid spacing
            "rotation": 0,
            "unit": 1,
        },
    ]

    nets = [
        {"net": params.get("vin_net", "VIN"), "pins": [{"reference": "R1", "pin": "1"}]},
        {
            "net": params.get("vout_net", "VOUT_DIV"),
            "pins": [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "1"}],
        },
        {"net": params.get("gnd_net", "GND"), "pins": [{"reference": "R2", "pin": "2"}]},
    ]

    return {
        "macro": "voltage_divider",
        "description": f"Voltage divider {vin}V -> {actual_vout:.2f}V ({r1_val}Ω / {r2_val}Ω)",
        "calculations": {
            "vin": vin,
            "target_vout": vout,
            "actual_vout": round(actual_vout, 3),
            "error_pct": round(abs(actual_vout - vout) / vout * 100.0, 2) if vout else 0.0,
            "r1": r1_val,
            "r2": r2_val,
        },
        "components": components,
        "nets": nets,
    }


def macro_i2c_pullups(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    speed = params.get("bus_speed", "standard").lower()
    rail = params.get("rail", "+3V3")
    package = params.get("package", "0603")
    fp = f"Resistor_SMD:R_{package}_1608Metric" if package == "0603" else f"Resistor_SMD:R_{package}_1005Metric"

    # Speeds: standard (100kHz) -> 4.7k, fast (400kHz) -> 2.2k, fast_plus (1MHz) -> 1k
    val_map = {"standard": 4700.0, "fast": 2200.0, "fast_plus": 1000.0}
    res_val = val_map.get(speed, 4700.0)

    ax, ay = anchor[0], anchor[1]
    components = [
        {
            "lib_id": "Device:R",
            "reference": "R_SDA",
            "value": format_resistance(res_val),
            "footprint": fp,
            "x": ax,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:R",
            "reference": "R_SCL",
            "value": format_resistance(res_val),
            "footprint": fp,
            "x": ax + 10.16,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
    ]

    nets = [
        {"net": rail, "pins": [{"reference": "R_SDA", "pin": "1"}, {"reference": "R_SCL", "pin": "1"}]},
        {"net": params.get("sda_net", "SDA"), "pins": [{"reference": "R_SDA", "pin": "2"}]},
        {"net": params.get("scl_net", "SCL"), "pins": [{"reference": "R_SCL", "pin": "2"}]},
    ]

    return {
        "macro": "i2c_pullups",
        "description": f"I2C Pull-Up pair for {speed} mode ({format_resistance(res_val)}) on {rail}",
        "calculations": {"bus_speed": speed, "pullup_ohms": res_val, "rail": rail},
        "components": components,
        "nets": nets,
    }


def macro_status_led(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    rail_v = float(params.get("rail_volts", 3.3))
    color = params.get("color", "green").lower()
    i_ma = float(params.get("current_ma", 5.0))
    package = params.get("package", "0603")
    r_fp = f"Resistor_SMD:R_{package}_1608Metric" if package == "0603" else f"Resistor_SMD:R_{package}_1005Metric"
    led_fp = f"LED_SMD:LED_{package}_1608Metric" if package == "0603" else f"LED_SMD:LED_{package}_1005Metric"

    # Forward voltages by color
    vf_map = {"red": 1.8, "green": 2.1, "yellow": 2.0, "blue": 3.0, "white": 3.0}
    vf = vf_map.get(color, 2.0)

    v_drop = max(0.1, rail_v - vf)
    r_ideal = v_drop / (i_ma / 1000.0)
    r_val = snap_to_e24(r_ideal)
    actual_i_ma = (v_drop / r_val) * 1000.0 if r_val > 0 else 0.0

    ax, ay = anchor[0], anchor[1]
    components = [
        {
            "lib_id": "Device:LED",
            "reference": "D1",
            "value": color.capitalize(),
            "footprint": led_fp,
            "x": ax,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:R",
            "reference": "R_LED",
            "value": format_resistance(r_val),
            "footprint": r_fp,
            "x": ax,
            "y": ay + 12.7,
            "rotation": 0,
            "unit": 1,
        },
    ]

    nets = [
        {"net": params.get("signal_net", "STATUS_LED"), "pins": [{"reference": "D1", "pin": "2"}]},  # Anode
        {"net": "NET_D1_CATHODE", "pins": [{"reference": "D1", "pin": "1"}, {"reference": "R_LED", "pin": "1"}]},
        {"net": params.get("gnd_net", "GND"), "pins": [{"reference": "R_LED", "pin": "2"}]},
    ]

    return {
        "macro": "status_led",
        "description": f"{color.capitalize()} LED with {format_resistance(r_val)} current-limiting resistor ({actual_i_ma:.1f}mA)",
        "calculations": {
            "rail_v": rail_v,
            "vf": vf,
            "target_i_ma": i_ma,
            "actual_i_ma": round(actual_i_ma, 2),
            "resistor_ohms": r_val,
        },
        "components": components,
        "nets": nets,
    }


def macro_decoupling_bank(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    rail = params.get("rail", "+3V3")
    values = params.get("caps", ["10uF", "100nF", "100nF"])
    package = params.get("package", "0603")
    c_fp = f"Capacitor_SMD:C_{package}_1608Metric"

    ax, ay = anchor[0], anchor[1]
    components = []
    nets_rail_pins = []
    nets_gnd_pins = []

    for i, val in enumerate(values):
        ref = f"C{i+1}"
        cx = ax + (i * 7.62)
        components.append(
            {
                "lib_id": "Device:C",
                "reference": ref,
                "value": val,
                "footprint": c_fp,
                "x": cx,
                "y": ay,
                "rotation": 0,
                "unit": 1,
            }
        )
        nets_rail_pins.append({"reference": ref, "pin": "1"})
        nets_gnd_pins.append({"reference": ref, "pin": "2"})

    nets = [
        {"net": rail, "pins": nets_rail_pins},
        {"net": params.get("gnd_net", "GND"), "pins": nets_gnd_pins},
    ]

    return {
        "macro": "decoupling_bank",
        "description": f"Decoupling capacitor bank for {rail} ({', '.join(values)})",
        "components": components,
        "nets": nets,
    }


def macro_crystal_circuit(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    freq = float(params.get("frequency_mhz", 16.0))
    cl = float(params.get("cl_pf", 12.0))
    c_stray = float(params.get("c_stray_pf", 4.0))

    # C_load = 2 * (CL - C_stray)
    c_load = max(5.0, round(2.0 * (cl - c_stray)))

    ax, ay = anchor[0], anchor[1]
    components = [
        {
            "lib_id": "Device:Crystal",
            "reference": "Y1",
            "value": f"{freq:.1f}MHz",
            "footprint": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
            "x": ax,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:C",
            "reference": "C_XTAL1",
            "value": f"{int(c_load)}pF",
            "footprint": "Capacitor_SMD:C_0603_1608Metric",
            "x": ax - 7.62,
            "y": ay + 12.7,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:C",
            "reference": "C_XTAL2",
            "value": f"{int(c_load)}pF",
            "footprint": "Capacitor_SMD:C_0603_1608Metric",
            "x": ax + 7.62,
            "y": ay + 12.7,
            "rotation": 0,
            "unit": 1,
        },
    ]

    nets = [
        {"net": params.get("xin_net", "XTAL_IN"), "pins": [{"reference": "Y1", "pin": "1"}, {"reference": "C_XTAL1", "pin": "1"}]},
        {"net": params.get("xout_net", "XTAL_OUT"), "pins": [{"reference": "Y1", "pin": "3"}, {"reference": "C_XTAL2", "pin": "1"}]},
        {"net": params.get("gnd_net", "GND"), "pins": [{"reference": "C_XTAL1", "pin": "2"}, {"reference": "C_XTAL2", "pin": "2"}]},
    ]

    return {
        "macro": "crystal_circuit",
        "description": f"{freq}MHz Crystal oscillator with calculated {c_load}pF load capacitors",
        "calculations": {"frequency_mhz": freq, "cl_pf": cl, "c_stray_pf": c_stray, "load_capacitors_pf": c_load},
        "components": components,
        "nets": nets,
    }


def macro_usb_c_pd_input(params: Dict[str, Any], anchor: List[float]) -> Dict[str, Any]:
    package = params.get("package", "0603")
    r_fp = f"Resistor_SMD:R_{package}_1608Metric"
    c_fp = f"Capacitor_SMD:C_0805_2012Metric"

    ax, ay = anchor[0], anchor[1]
    components = [
        {
            "lib_id": "Connector:USB_C_Receptacle_USB2.0",
            "reference": "J_USB",
            "value": "USB_C_2.0",
            "footprint": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
            "x": ax,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:R",
            "reference": "R_CC1",
            "value": "5.1k",
            "footprint": r_fp,
            "x": ax + 15.24,
            "y": ay - 5.08,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:R",
            "reference": "R_CC2",
            "value": "5.1k",
            "footprint": r_fp,
            "x": ax + 15.24,
            "y": ay + 5.08,
            "rotation": 0,
            "unit": 1,
        },
        {
            "lib_id": "Device:C",
            "reference": "C_VBUS",
            "value": "10uF",
            "footprint": c_fp,
            "x": ax + 22.86,
            "y": ay,
            "rotation": 0,
            "unit": 1,
        },
    ]

    nets = [
        {"net": "VBUS_5V", "pins": [{"reference": "J_USB", "pin": "A4"}, {"reference": "J_USB", "pin": "B9"}, {"reference": "C_VBUS", "pin": "1"}]},
        {"net": "GND", "pins": [{"reference": "R_CC1", "pin": "2"}, {"reference": "R_CC2", "pin": "2"}, {"reference": "C_VBUS", "pin": "2"}, {"reference": "J_USB", "pin": "A1"}]},
        {"net": "CC1", "pins": [{"reference": "J_USB", "pin": "A5"}, {"reference": "R_CC1", "pin": "1"}]},
        {"net": "CC2", "pins": [{"reference": "J_USB", "pin": "B5"}, {"reference": "R_CC2", "pin": "1"}]},
        {"net": "USB_D_P", "pins": [{"reference": "J_USB", "pin": "A6"}, {"reference": "J_USB", "pin": "B6"}]},
        {"net": "USB_D_N", "pins": [{"reference": "J_USB", "pin": "A7"}, {"reference": "J_USB", "pin": "B7"}]},
    ]

    return {
        "macro": "usb_c_pd_input",
        "description": "USB-C 2.0 Sink Port with dual 5.1kΩ CC pull-downs (5V/3A) & VBUS decoupling",
        "components": components,
        "nets": nets,
    }


MACRO_REGISTRY = {
    "voltage_divider": macro_voltage_divider,
    "i2c_pullups": macro_i2c_pullups,
    "status_led": macro_status_led,
    "decoupling_bank": macro_decoupling_bank,
    "crystal_circuit": macro_crystal_circuit,
    "usb_c_pd_input": macro_usb_c_pd_input,
}


def list_circuit_macros(project_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all available circuit macros (built-in and project-local)."""
    macros = [
        {
            "name": "voltage_divider",
            "description": "Calculates & places an E24 standard resistor divider given Vin & Vout.",
            "parameters": {"vin": "Input voltage (e.g. 5.0)", "vout": "Target output voltage (e.g. 3.3)", "r_target_kohm": "Target total R in kΩ (default: 10.0)", "package": "0603 or 0402"},
        },
        {
            "name": "i2c_pullups",
            "description": "Standard pull-up pair for I2C bus (standard 4.7k, fast 2.2k, fast_plus 1k).",
            "parameters": {"bus_speed": "'standard', 'fast', or 'fast_plus'", "rail": "Power rail (default: +3V3)", "package": "0603 or 0402"},
        },
        {
            "name": "status_led",
            "description": "LED with calculated E24 current-limiting resistor based on rail & color.",
            "parameters": {"rail_volts": "Voltage rail (default: 3.3)", "color": "'green', 'red', 'blue', 'yellow'", "current_ma": "Target current in mA (default: 5.0)"},
        },
        {
            "name": "decoupling_bank",
            "description": "Decoupling capacitor array neatly arranged on the grid.",
            "parameters": {"rail": "Power rail (default: +3V3)", "caps": "List of capacitance values (default: ['10uF', '100nF', '100nF'])", "package": "0603 or 0402"},
        },
        {
            "name": "crystal_circuit",
            "description": "Crystal oscillator with load capacitors calculated from CL specification.",
            "parameters": {"frequency_mhz": "Crystal frequency (default: 16.0)", "cl_pf": "Specified load capacitance CL in pF (default: 12.0)"},
        },
        {
            "name": "usb_c_pd_input",
            "description": "USB-C Sink Port with 5.1k CC pull-down resistors for 5V input power & ESD.",
            "parameters": {"package": "0603 or 0402"},
        },
    ]

    # Look for project-specific custom macros in .companion/macros/
    if project_path:
        p = Path(project_path).resolve()
        if p.is_file():
            p = p.parent
        custom_dir = p / ".companion" / "macros"
        if custom_dir.is_dir():
            for f in custom_dir.glob("*.json"):
                macros.append({
                    "name": f.stem,
                    "description": f"Custom project macro defined in {f.name}",
                    "source": str(f),
                })

    return macros


def compile_circuit_macro(
    macro_name: str,
    params: Dict[str, Any],
    anchor: List[float],
    project_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compile a high-level circuit macro into verified components, coordinates, and Konnect batch recipes."""
    if macro_name not in MACRO_REGISTRY:
        available = ", ".join(MACRO_REGISTRY.keys())
        raise ValueError(f"Unknown macro '{macro_name}'. Available macros: {available}")

    fn = MACRO_REGISTRY[macro_name]
    result = fn(params, anchor)

    # Format Konnect batch recipe for instant execution via Konnect MCP tools
    batch_components = [
        {
            "lib_id": c["lib_id"],
            "reference": c["reference"],
            "value": c["value"],
            "x": c["x"],
            "y": c["y"],
            "rotation": c.get("rotation", 0),
            "unit": c.get("unit", 1),
        }
        for c in result["components"]
    ]

    batch_wiring = [
        {
            "net": n["net"],
            "pins": [f"{p['reference']}.{p['pin']}" for p in n["pins"]],
        }
        for n in result["nets"]
    ]

    result["konnect_recipe"] = {
        "step_1_load_toolsets": ["sch_components", "sch_wiring", "sch_batch"],
        "step_2_batch_place_components": {
            "components": batch_components,
        },
        "step_3_wiring_instructions": batch_wiring,
    }

    return result

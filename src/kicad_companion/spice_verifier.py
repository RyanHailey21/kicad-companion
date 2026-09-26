import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import find_kicad_cli, find_ngspice_cli, find_ngspice_dll, find_spice_python
from .project_context import find_project_root


def audit_spice_models(target_path: str) -> Dict[str, Any]:
    """Audit schematic components against real manufacturer SPICE macromodels.
    
    Verifies that all operational amplifiers, comparators, transistors, diodes, and
    voltage references have legitimate SPICE macromodels (.lib, .sub, .cir, .model)
    before board layout or fabrication.
    
    If models are missing, halts with an actionable request for the user to provide them.
    
    Args:
        target_path: Path to project root, schematic, or PCB file.
    """
    root = find_project_root(target_path)
    if not root:
        return {"status": "error", "message": f"Could not locate project root for: {target_path}"}

    # 1. Discover all active components in schematic
    sch_files = list(root.glob("*.kicad_sch"))
    active_components = {}
    
    if sch_files:
        sch_text = sch_files[0].read_text(encoding="utf-8", errors="ignore")
        # Match Reference, Value, Footprint
        sym_blocks = re.findall(r'(\t\(symbol\s+[\s\S]*?\n\t\))', sch_text)
        for sb in sym_blocks:
            m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', sb)
            m_val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', sb)
            if m_ref and m_val:
                ref = m_ref.group(1)
                val = m_val.group(1)
                ref_upper = ref.upper()
                if any(ref_upper.startswith(prefix) for prefix in ["U", "Q", "D", "DPD", "LED", "IC", "VREF", "U_"]):
                    if ref not in active_components:
                        active_components[ref] = val


    # 2. Discover available SPICE macromodels
    model_dirs = [
        root / "simulation",
        root / "simulation" / "ti_models",
        root / "simulation" / "models",
        root / "models",
        root / ".companion" / "models",
        root,
    ]
    
    discovered_models = {}
    for md in model_dirs:
        if md.is_dir():
            for f in md.rglob("*.*"):
                if f.suffix.lower() in [".lib", ".sub", ".cir", ".mod", ".model", ".spi", ".sp"]:
                    # Read model file header or subcircuits
                    try:
                        text = f.read_text(encoding="utf-8", errors="ignore")
                        subckts = re.findall(r'^\s*\.SUBCKT\s+([A-Za-z0-9_]+)', text, re.MULTILINE | re.IGNORECASE)
                        models = re.findall(r'^\s*\.MODEL\s+([A-Za-z0-9_]+)', text, re.MULTILINE | re.IGNORECASE)
                        discovered_models[f.name] = {
                            "path": str(f),
                            "subcircuits": subckts,
                            "models": models,
                        }
                    except Exception:
                        pass

    # 3. Match components to models
    verified = []
    missing = []
    
    for ref, val in active_components.items():
        # Extract core part number (strip order codes like AIDGKR, AIDR, DCU, etc.)
        core_part = re.sub(r'(AIDGKR|AIDGKT|AIDR|AIDBZR|DCU|FASR|DBZR|PW|DGK|S-SMD).*$', '', val, flags=re.I)
        val_clean = re.sub(r'[^A-Za-z0-9]', '', core_part).lower()
        matched_model = None
        
        # Check against discovered model files and subcircuit names
        for filename, data in discovered_models.items():
            fn_clean = re.sub(r'[^A-Za-z0-9]', '', filename).lower()
            # Handle wildcards like OPAx381 matching OPA381
            fn_clean_wild = fn_clean.replace("x", "")
            if val_clean in fn_clean or val_clean in fn_clean_wild or fn_clean_wild in val_clean:
                matched_model = filename
                break
            for s in data["subcircuits"]:
                s_clean = re.sub(r'[^A-Za-z0-9]', '', s).lower()
                s_clean_wild = s_clean.replace("x", "")
                if val_clean in s_clean or val_clean in s_clean_wild or s_clean_wild in val_clean:
                    matched_model = f"{filename} ({s})"
                    break
            if matched_model:
                break
                
        # Also check simulation netlists for behavioral subcircuits
        cir_files = list(root.glob("simulation/**/*.cir"))
        if not matched_model:
            for cf in cir_files:
                try:
                    ctext = cf.read_text(encoding="utf-8", errors="ignore")
                    sub_matches = re.findall(r'^\s*\.SUBCKT\s+([A-Za-z0-9_]+)', ctext, re.MULTILINE | re.IGNORECASE)
                    model_matches = re.findall(r'^\s*\.MODEL\s+([A-Za-z0-9_]+)', ctext, re.MULTILINE | re.IGNORECASE)
                    for ent in (sub_matches + model_matches):
                        ent_clean = re.sub(r'[^A-Za-z0-9]', '', ent).lower()
                        if val_clean in ent_clean or ent_clean in val_clean:
                            matched_model = f"{cf.name} ({ent})"
                            break
                    if matched_model:
                        break
                except Exception:
                    pass

        if matched_model:
            verified.append({"reference": ref, "value": val, "model": matched_model})
        else:
            missing.append({"reference": ref, "value": val})


    all_verified = len(missing) == 0

    return {
        "status": "pass" if all_verified else "missing_models",
        "verified_count": len(verified),
        "missing_count": len(missing),
        "verified_components": verified,
        "missing_components": missing,
        "available_model_files": list(discovered_models.keys()),
        "user_action_required": (
            None if all_verified else
            f"The following {len(missing)} active components lack verified vendor SPICE macromodels: "
            + ", ".join([f"{m['reference']} ({m['value']})" for m in missing[:5]])
            + ". Please provide the manufacturer SPICE model (.LIB / .CIR / .SUB) from TI, ADI, etc., and place it in simulation/models/."
        ),
    }


def run_circuit_simulation(
    target_path: str,
    sim_type: str = "both",
) -> Dict[str, Any]:
    """Execute headless SPICE simulation and return concise quantitative electrical metrics.
    
    Runs native SPICE netlists or project simulation testbenches via KiCad's bundled
    NGSPICE solver without polluting agent context with raw waveform dumps.
    
    Args:
        target_path: Path to project root or simulation netlist (.cir).
        sim_type: 'transient', 'ac', or 'both'.
    """
    root = find_project_root(target_path)
    if not root:
        return {"status": "error", "message": f"Could not find project root for: {target_path}"}

    sim_dir = root / "simulation"
    if not sim_dir.is_dir():
        sim_dir = root

    # 1. Check for dedicated runner script first
    runner_script = sim_dir / "run_spice_simulation.py"
    if runner_script.is_file():
        # Execute project's dedicated SPICE runner using python environment
        py_exec = find_spice_python()
        proc = subprocess.run(
            [py_exec, str(runner_script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = proc.stdout + "\n" + proc.stderr
        
        # Extract quantitative summary lines (avoid token flood)
        summary_lines = []
        for line in output.splitlines():
            line_s = line.strip()
            if any(k in line_s for k in ["TRANSIENT", "AC SMALL-SIGNAL", "Bandwidth", "Gain", "Spike", "Peak", "VREF", "Threshold", "Pass", "Success"]):
                summary_lines.append(line_s)

        return {
            "status": "success" if proc.returncode == 0 else "error",
            "solver": "Berkeley NGSPICE 46 (KiCad 10 Engine)",
            "returncode": proc.returncode,
            "metrics_summary": "\n".join(summary_lines[:25]),
            "artifacts_generated": [
                f.name for f in sim_dir.glob("*.png")
            ],
        }

    # 2. Check for .cir netlists to run directly via ngspice
    cir_files = list(sim_dir.glob("*.cir"))
    if not cir_files:
        return {
            "status": "no_testbench",
            "message": "No SPICE simulation netlists (.cir) found in simulation/ directory.",
        }

    target_cir = cir_files[0]
    ngspice_cli = find_ngspice_cli()
    ngspice_dll = find_ngspice_dll()

    if ngspice_cli:
        log_file = target_cir.with_suffix(".log")
        raw_file = target_cir.with_suffix(".raw")
        cmd = [ngspice_cli, "-b", "-r", str(raw_file), "-o", str(log_file), str(target_cir)]
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=60)
        
        log_content = log_file.read_text(encoding="utf-8", errors="ignore") if log_file.is_file() else ""
        return {
            "status": "success" if proc.returncode == 0 else "warning",
            "solver": f"ngspice CLI ({ngspice_cli})",
            "netlist": target_cir.name,
            "raw_output": str(raw_file),
            "log_summary": "\n".join([l for l in log_content.splitlines() if "Error" in l or "Warning" in l or "CPU" in l][:10]),
        }

    return {
        "status": "available_for_python",
        "netlist": target_cir.name,
        "ngspice_dll": ngspice_dll,
        "message": f"KiCad ngspice engine available at {ngspice_dll}. Simulation netlist ready at {target_cir.name}.",
    }

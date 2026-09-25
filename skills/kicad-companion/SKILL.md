---
name: kicad-companion
description: "Universal KiCad 10 visual inspection, DRC triage, parametric circuit macro compiler, and project intelligence assistant. Use alongside Konnect to render 3D/2D views, triage DRC violations, compile circuit macros into batch recipes, inspect project .companion rules, and ensure KiCad is running before live IPC."
---

# KiCad Companion — Visual Feedback & Intelligence Workflow

This skill guides AI agents (Antigravity, Claude, Codex) on using the **`kicad-companion`** MCP server in conjunction with **`konnect`**.

## Division of Labor

- **Konnect MCP:** Handles all low-level atomic modifications (adding/moving footprints, net edits, routing, live NNG/Protobuf IPC).
- **KiCad Companion MCP:** Handles perception, headless rendering, DRC intelligence, circuit macro compiling, process supervision, and project-local script execution.

---

## Core Workflows

### 1. High-Level Circuit Macro Compiler (Declarative Subcircuits)
Instead of placing and wiring 10 individual components one by one, use the macro compiler to calculate E24 standard component values, compute grid coordinates, and generate a complete Konnect batch recipe:

```python
# 1. Check available macros
macros = list_circuit_macros()

# 2. Compile a subcircuit (e.g. voltage divider, I2C pullups, LED, decoupling, crystal, USB-C)
recipe = compile_circuit_macro(
    macro_name="voltage_divider",
    params={"vin": 5.0, "vout": 3.3, "r_target_kohm": 10.0, "package": "0603"},
    anchor=[100.0, 100.0]
)
```

The tool performs electrical calculations (e.g. standard resistor values, voltage drop, load capacitance) and returns a `konnect_recipe` ready for instant execution with Konnect's `batch_place_components` and `batch_connect_to_net`.

Supported Built-In Macros:
- `voltage_divider`: Computes optimal E24 resistors for any Vin/Vout ratio.
- `i2c_pullups`: Dual pull-up resistors sized for bus speed (`standard`, `fast`, `fast_plus`).
- `status_led`: LED with calculated current-limiting resistor based on color & target current.
- `decoupling_bank`: Neatly spaced bypass capacitor array across power rails.
- `crystal_circuit`: Crystal oscillator with load caps calculated from $C_L$ and stray capacitance.
- `usb_c_pd_input`: USB-C 2.0 port with 5.1k CC pull-down resistors for 5V input power & ESD.

Custom project macros defined in `<project>/.companion/macros/*.json` are auto-discovered!

---

### 2. Process Supervision (Before Live PCB Edits)
Before executing live PCB operations via Konnect, ensure KiCad 10 is running with the target project:
```python
ensure_kicad_running(project_or_pcb_path="path/to/board.kicad_pcb")
```
If KiCad was closed, this launches KiCad in the background and avoids IPC connection rejections.

---

### 3. Visual Inspection (After Modifying Placement or Routing)
Whenever you place components, move footprints, or route traces, generate a visual inspection artifact:
- **3D Photorealistic Render:**
  ```python
  render_pcb_3d(pcb_path="path/to/board.kicad_pcb", side="top")
  # Or isometric view:
  render_pcb_3d(pcb_path="path/to/board.kicad_pcb", rotate="-45,0,45")
  ```
  *Returns an inline image artifact for immediate visual verification of courtyard boundaries, label overlaps, and trace flow.*
- **2D Vector Layer Plot:**
  ```python
  render_pcb_2d(pcb_path="path/to/board.kicad_pcb", layers="F.Cu,B.Cu,F.Silkscreen,Edge.Cuts")
  ```
- **Schematic Sheet Export:**
  ```python
  render_schematic(sch_path="path/to/sheet.kicad_sch")
  ```

---

### 4. Smart DRC Triage
Instead of raw text DRC dumps, run:
```python
triage_pcb_drc(pcb_path="path/to/board.kicad_pcb")
```
This categorizes all violations into:
1. **Critical Errors:** Short circuits, copper clearances, unrouted tracks.
2. **Top Unconnected Nets:** Pins grouped by net name with exact `(x, y)` coordinates.
3. **Fab Hazards:** Annular rings, minimum drill sizes, solder mask bridges.
4. **Cosmetic Warnings:** Silkscreen over pad, courtyard overlaps.
5. **Actionable Recommendations:** Ranked next steps to clear errors.

---

### 5. Project-Level Intelligence & Custom Scripts
Every project repository can contain a `.companion/` folder with custom rules:
- Query project-specific stackup, target fab house, and preferred parts:
  ```python
  get_project_context(target_path="path/to/project_dir")
  ```
- Execute custom project automation scripts:
  ```python
  execute_project_script(target_path="path/to/project_dir", script_name="calc_impedance.py")
  ```

---

## 6. Overarching Hardware Standards (Mandatory for ALL Agents)

Every agent on this system (Antigravity, Claude Code, Claude Desktop, Codex) must strictly adhere to these 5 design rules:

### Rule 1: Strict Footprint Provenance (Zero Synthetic Footprints)
- **NEVER** synthesize or invent custom `.kicad_mod` footprint pad geometries from scratch.
- All footprints must be sourced directly from:
  1. KiCad 10 official libraries (`C:\Users\ryanh\AppData\Local\Programs\KiCad\10.0\share\kicad\footprints\`).
  2. Verified vendor downloads (UltraLibrarian, SnapEDA, official manufacturer package files).
- If an official or vendor footprint cannot be found, the agent **MUST STOP and ask the user to provide the footprint**. Do not guess pad dimensions or spacing.

### Rule 2: Physical Connector Extent & Zero Edge Overhang
- Connectors (through-hole headers, shrouded headers, USB-C receptacles, barrel jacks) have physical bodies and pin arrays extending far beyond Pin 1 origin.
- When placing connectors:
  1. Calculate total bounding box ($X_{min}, X_{max}, Y_{min}, Y_{max}$) accounting for pin count, pitch, and orientation angle.
  2. Ensure connector pins and bodies remain at least **2.0 mm inside the board outline (`Edge.Cuts`)**.
  3. Ensure at least **2.5 mm clearance from all mounting hole screw heads**.

### Rule 3: Mandatory Quantitative Placement Quality Gate (`score_placement`)
- Do not guess coordinates or proceed directly from placement to routing.
- After placing or moving components, call `konnect:score_placement`.
- **Target Gate:** The layout must achieve:
  - **Score:** `100 / 100`
  - **Verdict:** `pass`
  - **Hard Failures:** `[]` (Zero courtyard collisions)
  - **Outside Outline:** `[]` (Zero components outside board outline)
- Routing must not begin until placement achieves a 100% clean passing verdict.

### Rule 4: Symbol Inheritance (`extends`) & Multi-Unit Completeness
- In KiCad 10, library symbols frequently inherit from base models (`extends`). Embedded `(lib_symbols ...)` blocks in `.kicad_sch` must contain fully resolved graphical primitives and pins, otherwise KiCad displays empty bounding boxes with `??`.
- Multi-unit symbols (e.g. ICs with separate logic gates and power units, dual comparators, dual monostables) must have **both logic units and power pin units explicitly instantiated**. Omitting power units triggers ERC `missing_unit` and `missing_power_pin`.

### Rule 5: Mandatory Multi-Angle Visual Verification Before Completion
- An agent must never report a board as complete without generating visual artifacts and reviewing them:
  1. `render_pcb_3d(pcb_path=..., side="top")`
  2. `render_pcb_3d(pcb_path=..., rotate="-45,0,45")` (Isometric)
  3. `render_schematic(sch_path=...)`
  4. `triage_pcb_drc(pcb_path=...)`
- Visually inspect component body boundaries, silkscreen readability, and connector pin margins against the physical board edges before concluding.

---
name: kicad-companion
description: "Universal KiCad 10 hardware accelerator: visual inspection, DRC triage, SPICE circuit simulation, pinout/polarity auditing, project intent governance, one-shot manufacturing export, parametric circuit macros, and headless Specctra autorouting. Abstract redundant multi-turn workflows away to minimize LLM token usage and enforce zero-defect hardware design rules."
---

# KiCad Companion — Visual Feedback & Intelligence Workflow

This skill guides AI agents (Antigravity, Claude, Codex) on using the **`kicad-companion`** MCP server in conjunction with **`konnect`**.

## Division of Labor

- **Konnect MCP:** Handles atomic live modifications (adding/moving footprints, manual net edits, interactive routing, live NNG/Protobuf IPC).
- **KiCad Companion MCP:** Handles high-level workflow abstractions, project intent governance, headless SPICE simulation, pinout/polarity auditing, one-shot production packaging, perception/rendering, DRC intelligence, circuit macro compiling, process supervision, headless netlist-to-PCB pad synchronization, and headless Specctra/Freerouting autorouting pipelines.

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

### 5. Headless Netlist Synchronization & Netclass Provisioning
When initializing a PCB or updating from schematic changes, ensure all footprint pads have explicit nets assigned and project netclasses are configured:
```python
sync_pcb_nets(pcb_path="path/to/board.kicad_pcb")
```
What this performs:
1. Executes `kicad-cli sch export netlist` from the project schematic.
2. Parses net connections and updates every footprint pad with `(net <code> "<name>")`.
3. Defines top-level `(net ...)` declarations in `.kicad_pcb`.
4. Configures standard design rules and netclasses in `.kicad_pro`:
   - `Default`: 0.25mm track, 0.20mm clearance.
   - `Power`: 0.50mm track, 0.30mm clearance (assigned to VCC, 3V3, 5V, GND, PWR rails).
   - `Analog_Sensitive`: 0.30mm track, 0.25mm clearance (for high-impedance/sensor nodes).
   - `Digital_Events`: 0.25mm track, 0.20mm clearance (for fast digital pulses/clocks).

---

### 6. Specctra / Freerouting Headless Autorouting Pipeline
For dense boards requiring autorouting:
```python
# Complete end-to-end pipeline:
autoroute_board(pcb_path="path/to/board.kicad_pcb", passes=15)
```
Or use the modular sub-tools:
1. **Export Specctra DSN:**
   ```python
   export_specctra_dsn(pcb_path="path/to/board.kicad_pcb")
   ```
   *Uses KiCad's bundled `pcbnew.ExportSpecctraDSN` with automatic non-ASCII / Greek glyph sanitization.*
2. **Run Freerouting (Single-Threaded Mandatory):**
   *Runs Freerouting JAR headlessly with `-mt 1` (avoids multi-threading clearance optimizer bugs).*
3. **Import Specctra SES:**
   ```python
   import_specctra_ses(pcb_path="path/to/board.kicad_pcb", ses_path="path/to/board.ses")
   ```
   *Pure-Python S-expression parser that injects wire segments and vias directly into `.kicad_pcb` without wxWidgets GUI event-loop deadlocks.*

---

### 7. Automated Placement Overlap Detection & Relaxation Solving
Never engage in repetitive manual trial-and-error coordinate guessing to resolve component overlaps or edge collisions. Use the automated placement engine:
1. **Check Overlaps & Edge Violations:**
   ```python
   check_placement_overlaps(pcb_path="path/to/board.kicad_pcb", min_clearance_mm=0.25)
   ```
   *Computes exact footprint courtyard extents, verifies connector inset $\ge 2.0\text{ mm}$ from board edge, checks mounting hole clearance $\ge 2.5\text{ mm}$, and reports all pairwise overlapping bounding boxes.*
2. **Automated Relaxation & Grid Snap:**
   ```python
   resolve_placement_overlaps(
       pcb_path="path/to/board.kicad_pcb",
       min_clearance_mm=0.5,
       grid_step_mm=0.5,
       fixed_refs=["J1", "H1", "H2", "H3", "H4", "DPD1", "LED1"],
   )
   ```
   *Applies geometric relaxation along the minimum penetration axis to push overlapping components apart, clamps movable footprints within board margins, snaps final coordinates to a clean grid (0.5mm/1.0mm), and preserves critical fixed anchors.*

---

### 8. Automated Silkscreen Sanitization & Decluttering
Dense layouts often suffer from 0402/0603 passive reference text clipping IC pads or overlapping adjacent components:
```python
sanitize_silkscreen(
    pcb_path="path/to/board.kicad_pcb",
    hide_passives=True,
    min_pad_clearance_mm=0.50,
)
```
*Automatically hides reference designators for small passives on `F.SilkS` (while preserving them on `F.Fab` for assembly), deduplicates overlaid text lines, and verifies $\ge 0.50\text{ mm}$ clearance from silkscreen to copper pads.*

---

### 9. Project-Level Intelligence & Custom Scripts
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

### 10. Project Intent Governance (`PROJECT_CONTEXT.md`)
Before initiating, modifying, or manufacturing any PCB project, the agent must ensure a structured `PROJECT_CONTEXT.md` file exists at the root of the PCB directory:
```python
ensure_project_context(
    target_path="path/to/project_dir",
    title="Neuromorphic IR Event Sensor",
    purpose="Sub-millisecond optical transient event detector with differential photodiode transimpedance amplification",
    target_fab="JLCPCB 2-Layer Standard"
)
```
**Why this matters:**
- Automatically parses `.kicad_pcb` and `.kicad_sch` to extract physical board outline dimensions, layer count, component footprint count, power rail nets, active ICs, and SPICE models.
- Establishes persistent hardware requirements, power budgets, stackup constraints, and a pre-fab verification checklist.
- Keeps any subsequent agent (or user) fully aligned on the functional purpose, constraints, and progress of the board.

---

### 11. Headless SPICE Simulation & Vendor Macromodel Verification
Never synthesize or finalize analog, sensor, or power circuits without quantitative SPICE verification using real vendor models:
1. **Audit Vendor SPICE Models:**
   ```python
   audit_spice_models(target_path="path/to/project_dir")
   ```
   *Scans all active ICs (`U*`, `Q*`, `DPD*`, `LED*`), normalizes manufacturer part numbers (e.g. `OPA381AIDGKR` -> `OPA381`, `74LVC1G123DCU` -> `74LVC1G123`), and verifies matching `.lib`/`.cir` files exist. If a model is missing, the tool instructs the agent to pause and ask the user to provide it.*
2. **Execute Headless SPICE Simulation:**
   ```python
   run_circuit_simulation(
       target_path="path/to/project_dir",
       sim_type="transient",
       stop_time_ms=10.0,
       step_time_us=1.0
   )
   ```
   *Runs Berkeley NGSPICE headlessly via KiCad's official `ngspice.dll` or CLI. Returns a compact ~50 token summary of critical electrical figures of merit (transient peaks, DC operating bias, rise/fall times, -3dB bandwidth) rather than dumping thousands of raw waveform points.*

---

### 12. Pre-Flight Component Pinout, Polarity & Reference Annotation Audit
Prevent costly board spins and fabrication failures from footprint pin mismatches, inverted diodes, or unannotated KiCad GUI blockers:
```python
audit_component_pinouts(
    pcb_path="path/to/board.kicad_pcb",
    sch_path="path/to/board.kicad_sch" # optional, auto-detected if omitted
)
```
**Checks performed:**
- **LED Polarity:** Validates SMD LED Pad 1 Cathode vs Pad 2 Anode conventions against schematic net connections.
- **Diode Polarity:** Verifies Cathode/Anode pad alignment against circuit net direction.
- **Transistor/MOSFET Pinout:** Verifies Gate/Drain/Source pin ordering on SOT-23/SOT-23-3 packages.
- **Unrouted / Floating Pads:** Detects pads missing copper track or plane connections.
- **Reference Designator Annotations:** Flags any reference designator lacking trailing digits (e.g. `RLED`, `U_REF`, `ROS_ON_TL`), which cause KiCad GUI's F8 "Schematic is not fully annotated" update blockers.

---

### 13. One-Shot Manufacturing Package Pipeline
Rather than executing 10 separate tools (DRC, export drill, export gerbers, zip, export pos, export 3D step, export 2D SVGs), execute the entire fabrication package in one atomic step:
```python
build_production_package(
    pcb_path="path/to/board.kicad_pcb",
    output_dir="path/to/production", # optional
    revision="revA",
    fab_house="JLCPCB"
)
```
**Atomic Pipeline Steps:**
1. **DRC Pre-Flight:** Headless DRC run; stops immediately if critical copper clearance or unrouted errors exist.
2. **Gerber & Drill Generation:** Exports Protel-standard layers (`.GTL`, `.GBL`, `.GTS`, `.GBS`, `.GTO`, `.GBO`, `.GKO`, `.DRL`).
3. **Automated ZIP Archive:** Packages gerbers into `<revision>-gerber.zip` ready for immediate JLCPCB/PCBWay upload.
4. **Centroid / CPL & BOM Export:** Automatically formats JLCPCB-compliant CPL (`<stem>-cpl-jlcpcb.csv`) and BOM (`<stem>-bom-jlcpcb.csv`) with LCSC part number resolution.
5. **High-Fidelity 3D STEP Solid Model:** Exports `<revision>.step` for mechanical CAD clearance verification.
6. **Vector Documentation SVGs:** Updates top/bottom copper, silkscreen, and schematic sheet SVGs for rapid review.

---

### 14. Automated JLCPCB Assembly Export (`export_jlcpcb_assembly`)
Directly generates JLCPCB-compliant BOM and CPL (pick-and-place) files using KiCad as the absolute source of truth:
```python
export_jlcpcb_assembly(
    pcb_path="path/to/board.kicad_pcb",
    sch_path="path/to/board.kicad_sch", # optional, auto-discovered if omitted
    output_dir="path/to/production" # optional
)
```
**Zero-Error Guarantees:**
- **Authoritative Geometry:** Extracts footprint coordinates directly from `.kicad_pcb` via `kicad-cli`, converting KiCad's inverted Y-axis to JLCPCB positive coordinates and setting layer to `Top` / `Bottom`.
- **Intelligent SMT Filtering:** Automatically excludes mechanical elements (mounting holes `H*`, test points `TP*`, fiducials `FID*`, graphics `LOGO*`) and through-hole headers/LEDs from automated SMT pick-and-place.
- **LCSC Resolution Hierarchy:** Correlates references against schematic symbol properties (`LCSC`, `LCSC Part #`), project `.companion/preferred_parts.json`, and outputs structured BOM and CPL files ready for drag-and-drop upload.
- **Validation Reporting:** Returns `assigned_lcsc_count` and flags any unassigned components so the agent or user can assign missing part numbers before fabrication.

---

## 15. Overarching Hardware Standards (Mandatory for ALL Agents)

Every agent on this system (Antigravity, Claude Code, Claude Desktop, Codex) must strictly adhere to these 14 design rules:

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

### Rule 6: Silkscreen Clearance & Typography Constraints
- **Silkscreen-to-Pad Clearance:** All graphic lines, polygons, keep-out boundaries, and text on `F.SilkS` or `B.SilkS` must maintain at least **0.50 mm (20 mils) clearance** from any exposed SMD or through-hole copper pad.
- **DRC Zero Silk-Over-Copper:** Silkscreen crossing exposed copper (`silk_over_copper`) compromises solderability and causes fab defects; it is a critical gate failure.
- **Minimum Text Sizing:** Silkscreen text height must be $\ge 0.80\text{ mm}$ (thickness $\ge 0.15\text{ mm}$) to satisfy KiCad DRC and fabrication legibility rules.

### Rule 7: Freerouting / Specctra Autorouting Protocol
- **Single-Thread Optimization Mandatory (`-mt 1`):** Freerouting v2.4+ has a known multi-threaded route optimizer bug that introduces trace-to-trace clearance violations. Always execute Freerouting with `-mt 1`.
- **Pre-Routing Gate:** Routing must never be attempted unless `score_placement` passes with 100/100 and pad nets/netclasses are synchronized.
- **Native Headless SES Import:** Do not invoke C++ wxWidgets SES imports in headless Python scripts to avoid UI event-loop deadlocks. Use `kicad-companion:import_specctra_ses`.
- **Post-Route DRC Gate:** After SES import, fill ground zones (`refill_zones`) and run `triage_pcb_drc`. The board is not done until unrouted net count is 0 and copper clearances are 0.

### Rule 8: Stale Lockfile Detection & Reconnection Hygiene
- KiCad creates lock files (`~<filename>.kicad_pcb.lck`, `~<filename>.kicad_sch.lck`) when opened in the GUI.
- If an operation fails due to file locks or KiCad IPC drops, verify whether a live KiCad process owns the lock. Do not stomp or corrupt files; use `ensure_kicad_running` to manage the process lifecycle.

### Rule 9: Real-Time Routing Observability & Streaming Logs
- Never execute Freerouting or batch autorouters silently with buffered output.
- All routing runs must stream output line-by-line in real-time (`bufsize=1`, unbuffered stdout) so progress, fanout passes, ripup costs, and unrouted net counts are observable while running.
- In addition to stdout, every autoroute run must write a persistent log to `<dsn_path>.freerouting.log` for immediate inspection and diagnostic auditing.

### Rule 10: Algorithmic Placement Overlap Resolution Over Manual Trial-and-Error
- Agents must NOT engage in repetitive, manual coordinate guessing or tedious multi-step nudging to resolve component collisions, edge margins, or silkscreen clutter.
- Always use the automated placement tools:
  1. `check_placement_overlaps`: Automatically identify pairwise courtyard collisions, board edge violations, and mounting hole clearances.
  2. `resolve_placement_overlaps`: Automatically apply geometric relaxation and grid snapping to separate overlapping components while preserving fixed connectors and sensors.
  3. `sanitize_silkscreen`: Automatically declutter passive reference texts and guarantee $\ge 0.50\text{ mm}$ clearance to copper pads.

### Rule 11: Mandatory Root `PROJECT_CONTEXT.md` Intent File
- Context is king: Before touching any schematic or PCB layout, create or verify `PROJECT_CONTEXT.md` using `ensure_project_context(target_path=...)`.
- The file documents the project purpose, architecture, key components, power budget, board physical constraints, fab rules, and signoff checklist.
- Constrains the agent to maintain clear intent throughout the hardware design cycle.

### Rule 12: Mandatory SPICE Simulation Gate (Real Manufacturer Models)
- Before finalizing analog, sensor, or power circuits, verify electronic functionality using true manufacturer SPICE models via `audit_spice_models(target_path=...)` and `run_circuit_simulation(target_path=..., sim_type=...)`.
- Never skip simulation or fake operational amplifier / sensor behavior.
- If a real manufacturer model is missing, **STOP and ask the user to provide it**.
- Output compact key figures of merit (gain, bandwidth, transient peak, rise/fall times) instead of dumping thousands of raw waveform points to preserve agent tokens.

### Rule 13: Mandatory Pinout, Polarity, and Annotation Audit Gate
- Before generating production packages or exporting gerbers, call `audit_component_pinouts(pcb_path=..., sch_path=...)`.
- Specifically checks:
  - Diode and LED polarity traps (Pad 1 Cathode vs Pad 2 Anode matching physical footprints).
  - Transistor/MOSFET pinout alignment (Drain/Source/Gate vs Pin 1/2/3).
  - Unrouted or floating pads.
  - Reference designator annotation compliance: all references must end in trailing digits, avoiding KiCad GUI F8 "Schematic is not fully annotated" blockers like `RLED`, `U_REF`.

### Rule 14: Token Conservation & Atomic Pipeline Abstraction
- Avoid executing 10-15 granular MCP/shell tool calls (export drill, export gerbers, zip files, export pos, render 3D, render 2D, export schematics, run DRC).
- Use `build_production_package(pcb_path=..., output_dir=..., revision=..., fab_house=...)` to generate a 100% complete, verified manufacturing release in a single atomic tool call.
- Ensure all tool outputs return compact, structured JSON summaries instead of verbose unparsed logs.

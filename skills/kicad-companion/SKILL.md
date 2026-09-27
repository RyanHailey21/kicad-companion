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

### 0. Headless Design Pipeline (no GUI, no IPC)
Use this whenever Konnect's schematic/PCB editing tools are unavailable (for example, a client that doesn't pick up tools loaded mid-session) or when a design should be reproducible from a spec. **Close KiCad first** (Rule 15).

```python
# 1. Schematic from a declarative spec: official-library symbols, every unit placed
#    (incl. power units), stub+label wiring, automatic PWR_FLAGs, ERC summary returned.
generate_schematic(spec="design/spec.json", sch_path="board.kicad_sch")
#    spec = {"title": ..., "power_flags": "auto", "parts": [
#      {"ref": "U2", "lib_id": "Amplifier_Operational:MCP6022", "value": "MCP6022",
#       "footprint": "Package_DIP:DIP-8_W7.62mm_Socket",
#       "pins": {"1": "VPHOTO_TL", "2": "TIA_IN_TL", "3": "VREF", "4": "GND", "8": "3V3A"},
#       "fields": {"MPN": "MCP6022-I/P"}, "group": "TIA"}]}
#    A pin mapped to null gets a no-connect flag. Re-running keeps symbol UUIDs stable,
#    so an existing PCB stays linked.

# 2. Board from the schematic: footprints, exact net names, symbol links, outline, floorplanned placement
build_pcb_from_schematic(sch_path="board.kicad_sch", board_width_mm=40, board_height_mm=40,
    copper_layers=4, planes={"In1.Cu": "GND"},                 # solid inner GND plane (never route on it)
    auto_sides=["B", "F"],                                      # double-sided SMT when space is tight
    fixed={"J1": [20, 33.6, 90, "B"], "H1": [10, 13, 0]},       # courtyard centres, board-relative, side
    keepouts=[{"rect": [8.5, 4.5, 31.5, 21.5], "side": "F"}],   # e.g. a lens holder / shield can base
    groups=[                                                    # one functional block per area and side
        {"refs": ["U2", "C8", "R5", "C10"], "rect": [11, 5, 29, 21], "sides": ["B"], "target": [20, 13]},
        {"refs": ["U8", "C22", "C23", "R17"], "rect": [1, 29, 16, 39], "sides": ["B"]}],
    near={"R5": "U2", "C10": "U2", "C23": "U8", "R17": "U8"})   # feedback/timing parts at their pins

# 3. Net classes that match KiCad's real net names ("/GND" and "*/VREF" patterns)
configure_netclasses(project_path=".", classes={"Analog": {"track_width": 0.3, "clearance": 0.3}},
                     assignments={"Analog": ["VREF", "TIA_IN_*"]}, board_rules={"min_resolved_spokes": 1})

# 4. Reserve plane vias, route, finish, verify
fanout_vias(pcb_path="board.kicad_pcb")                   # via + dogbone for every SMD pad on a plane net
autoroute_board(pcb_path="board.kicad_pcb", passes=30)    # judge by drc.total_unconnected, not the log
finalize_pcb(pcb_path="board.kicad_pcb", pour_net="GND")  # re-link + pours + a via only where an island needs one
sanitize_silkscreen(pcb_path="board.kicad_pcb")           # DRC-driven reference placement
triage_pcb_drc(pcb_path="board.kicad_pcb", refill_zones=True, schematic_parity=True)
```

**Placement engine (connectivity strategy, the default).** Big parts first, then decoupling caps
(anchored to an IC of their own group, aimed at its *supply pin*, allowed inside the IC's routing
halo), then `near` parts, then everything else next to the *pads* it connects to. `groups` confine
members to a rect and sides; the routing halo is solved per group so a confined group never spills
(a member that still cannot fit is reported in `group_spill`). A crowding penalty
(`spread_weight_mm`) spreads parts into empty board area instead of piling up. Measure the result:
decoupling and feedback parts should end up within ~1.5-2.5 mm (pad to pad) of the pin they serve.

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
What this performs (through pcbnew, never text surgery):
1. Exports the netlist with `kicad-cli sch export netlist`.
2. Sets every pad's net using KiCad's exact names (local-label nets keep their sheet prefix, e.g. `/GND`), including `unconnected-(...)` nets on no-connect pins.
3. Re-links footprints to their symbols and copies value, description, custom fields and DNP/BOM flags, so DRC schematic parity is clean.
4. Reports footprints missing on the board / extra on the board (it never adds or removes footprints; use `build_pcb_from_schematic`).
5. Optionally merges an automatic `Power` net class for supply/ground nets without touching existing classes.

For project-specific classes use `configure_netclasses(classes=..., assignments=...)`: plain patterns such as `VPHOTO_*` are written as both `VPHOTO_*` and `*/VPHOTO_*`, so they match sheet-prefixed names.

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
   *Reads exact courtyard geometry through pcbnew (footprint origins are often pin 1, so origin-centred boxes are wrong), checks connector inset $\ge 2.0\text{ mm}$ from the outline and mounting-hole keepouts, and also reports KiCad DRC's polygon-accurate courtyard violations.*
2. **Automated Relaxation & Grid Snap:**
   ```python
   resolve_placement_overlaps(
       pcb_path="path/to/board.kicad_pcb",
       min_clearance_mm=0.5,
       grid_step_mm=0.5,
       fixed_refs=["D1", "LED1"],   # connectors and mounting holes are fixed automatically
   )
   ```
   *Minimum-penetration relaxation on exact courtyards, clamped to the outline, applied through pcbnew; the grid snap picks a floor/ceil combination that keeps clearance.*
3. **Plan-Driven Placement:**
   ```python
   place_footprints(pcb_path=..., fixed={"U2": [35, 18.6, 180]}, auto_place_rest=True,
                    groups=[{"refs": ["U4", "C18", "R13"], "rect": [1, 1, 12, 14], "sides": ["B"]}],
                    near={"R13": "U4"})
   ```
   *`groups` = functional blocks placed by connectivity inside their own area (preferred);
   `regions` = plain first-fit packing in the given order (for rows of identical parts).*

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
*Centres references on part bodies (connectors/test points above), runs KiCad DRC, moves each flagged reference through top/bottom/left/right, and hides it only as a last resort (F.Fab keeps it for assembly). Iterates until DRC reports no silkscreen problems. `hide_passives` hides 0201/0402/0603 refs outright.*

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

### 14. Automated Assembly Export (`export_jlcpcb_assembly` & `export_pcbway_assembly`)
Directly generates fab-compliant BOM and CPL (pick-and-place) files using KiCad as the absolute source of truth:
```python
# For JLCPCB (requires LCSC part numbers):
export_jlcpcb_assembly(
    pcb_path="path/to/board.kicad_pcb",
    sch_path="path/to/board.kicad_sch", # optional, auto-discovered if omitted
    output_dir="path/to/production" # optional
)

# For PCBWay (requires Manufacturer and MPN columns):
export_pcbway_assembly(
    pcb_path="path/to/board.kicad_pcb",
    sch_path="path/to/board.kicad_sch", # optional, auto-discovered if omitted
    output_dir="path/to/production" # optional
)
```
**Zero-Error Guarantees:**
- **Authoritative Geometry:** Extracts footprint coordinates directly from `.kicad_pcb` via `kicad-cli`, converting KiCad's inverted Y-axis to fab positive coordinates and setting layer to `Top` / `Bottom`.
- **SMT Filtering:** Excludes mechanical elements (mounting holes `H*`, test points `TP*`, fiducials `FID*`, graphics `LOGO*`) and DNP parts. Through-hole parts are decided by the footprint's real `through_hole` attribute (an SMD pin header *is* placed); they get no CPL row but are listed in the PCBWay BOM with a `(THT)` footprint suffix so the assembler solders them. The result lists `tht_components` and `dnp_components` - check both.
- **LCSC Resolution Hierarchy (JLCPCB):** Correlates references against schematic symbol properties (`LCSC`, `LCSC Part #`), project `.companion/preferred_parts.json`, and outputs structured BOM and CPL files ready for drag-and-drop upload.
- **Manufacturer & MPN Mapping (PCBWay):** 7-column format (`Item, Designator, Qty, Value, Footprint, Manufacturer, MPN`). The schematic's `MPN`/`Manufacturer` fields are authoritative; a small built-in table is only a fallback for parts without an MPN field. **Before ordering, read the BOM and check every IC's MPN suffix matches its footprint package** (e.g. TI `...DGKR` = VSSOP, `...DR` = SOIC, `...DCKR` = SC-70).
- **Validation Reporting:** Returns `assigned_lcsc_count` or line item count and flags unassigned components so missing parts can be caught prior to ordering.

---

## 15. Overarching Hardware Standards (Mandatory for ALL Agents)

Every agent on this system (Antigravity, Claude Code, Claude Desktop, Codex) must strictly adhere to these design rules:

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

### Rule 3: Mandatory Quantitative Placement Quality Gate
- Do not guess coordinates or proceed directly from placement to routing.
- After placing or moving components, call `check_placement_overlaps` (exact courtyards + DRC courtyard check); `konnect:score_placement` is an equivalent gate when Konnect's PCB tools are loaded.
- **Target Gate:** The layout must achieve the following (a lower score is acceptable only for a deliberate, documented exception such as a sensor array whose pitch is set by optics, and only if KiCad's own courtyard DRC is clean):
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
- **Pre-Routing Gate:** Routing must never be attempted unless the placement gate (Rule 3) passes with 100/100 and pad nets/netclasses are synchronized. Confirm net classes resolve on the real net names (Rule 16) or every track is routed at the Default width.
- **Native Headless SES Import:** Do not invoke C++ wxWidgets SES imports in headless Python scripts to avoid UI event-loop deadlocks. Use `kicad-companion:import_specctra_ses`.
- **Post-Route DRC Gate:** After SES import, fill ground zones (`refill_zones`) and run `triage_pcb_drc`. The board is not done until unrouted net count is 0 and copper clearances are 0.
- **Plane fanout first:** on any board with an inner plane, run `fanout_vias` before `autoroute_board`. Pads the router fails to fan out get walled in by signal tracks, and no automatic fix can reach them afterwards. The fanout vias/stubs are locked and `import_specctra_ses` keeps locked items (lock included) across routing. On the 40x40 reference board this took the result from 23 unconnected (4 stranded GND pads after finalize) to 1 unconnected with zero pour islands.
- **Neckdown:** Freerouting may still narrow tracks (to ~50-75% of class width) to squeeze between fine-pitch pins even with `neckdown=False`. `triage_pcb_drc` lists them as `track_width` criticals with the nets; reroute those short segments by hand or give the IC more room.
- **Trust KiCad's count, not Freerouting's:** Freerouting's "unrouted items" counts net *fragments* and restarts high when it is handed a partly routed board. Use `drc.total_unconnected`. Extra `rounds` and rip-up-and-reroute of a congested corner rarely help and can make it worse - loosen the placement (bigger halo, spread, a larger board) instead.

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

### Rule 15: Never Edit Files Under an Open KiCad Session
- KiCad holds the board and project settings in memory and writes them back on save or exit, silently discarding headless edits. The `.kicad_pro` (net classes, rules) is rewritten on exit even without a save.
- Call `check_project_open(path)` before headless work. Every writing tool refuses by default while KiCad has the project open; `allow_while_open=True` overrides (then use File > Revert in KiCad).
- Workflow: ask the user to close KiCad **without saving**, run the headless tools, then reopen.

### Rule 16: Net Names Carry Their Sheet Path
- Nets from local labels are named `/NET` (or `/sheet/NET`); only global power symbols give bare names like `GND`.
- A net-class pattern `GND*` does not match `/GND`. Use `configure_netclasses`, which writes both `<pat>` and `*/<pat>`, and never strip the prefix from pad nets (it breaks schematic parity and GUI updates).
- Verify with `triage_pcb_drc(schematic_parity=True)`: 0 parity issues means the board matches the schematic exactly.

### Rule 17: Ground Strategy on Multilayer Boards
- Make one inner layer a solid ground plane (`planes={"In1.Cu": "GND"}`) and never route signals on it: every outer-layer signal keeps an unbroken return path and every decoupling cap reaches ground through one short via.
- Do not add a stitching-via grid by default (`stitch_pitch_mm=0`). `finalize_pcb` adds a via only where a pour fragment is actually disconnected; report the stranded pads it cannot fix to the user with coordinates.
- Pour islands appear in DRC as zone-to-zone "unconnected" items whose position is just the zone's corner; `finalize_pcb`'s `islands_without_via` gives the real location and pads.

### Rule 18: No Via-in-Pad on Assembled SMD Parts
- An open via inside an SMD pad wicks solder away during reflow (starved joints, tombstoned 0201/0402s), and a via larger than the pad distorts the land. Put the via just off the pad end with a short stub (dogbone). Only use via-in-pad when the fab's filled-and-capped option is ordered.

### Rule 19: Footprint Quirks Agents Trip Over
- KiCad's 0201 footprints contain two extra unnumbered, netless pads on the paste layer only (stencil apertures). Count only numbered pads when classifying parts and ignore paste-only pads in copper checks. In the GUI, clicking a 0201 pad may select the paste pad ("Pad [<no net>] on B.Paste"); the copper pad underneath is on the right net.
- Bottom-side renders are mirrored left-to-right; account for it when checking floorplans.

### Rule 20: Assembly Release Review
- After `build_production_package` / `export_pcbway_assembly`, read the BOM: every line has a real Manufacturer + MPN (generic text such as "2x5 1.27mm header" must be replaced with an orderable part), IC MPN suffixes match the footprint package, DNP parts are intended, and through-hole parts are listed.
- Confirm the gerber zip contains every copper layer (inner layers `In*_Cu`), both masks, paste layers, the outline and PTH/NPTH drill files, and state double-sided assembly when parts are on both sides.

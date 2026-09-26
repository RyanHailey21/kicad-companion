# KiCad Companion (Universal AI Hardware Accelerator)

A cross-agent Model Context Protocol (MCP) companion server and skills suite designed to work alongside [Konnect](https://github.com/mixelpixx/Konnect) and KiCad (8, 9, and 10).

Compatible with **Antigravity**, **Claude (Desktop & Code)**, and **OpenAI Codex**.

---

## Capabilities

1. **Project Intent Governance (`ensure_project_context`)**:
   - Parses `.kicad_pcb` and `.kicad_sch` to establish and maintain a persistent `PROJECT_CONTEXT.md` at the root of the PCB directory.
   - Constrains AI agents to clear intent, physical constraints, layer counts, power rails, SPICE model tracking, and pre-fab checklist verification.
2. **Headless SPICE Simulation & Auditing (`audit_spice_models`, `run_circuit_simulation`)**:
   - Scans active ICs, normalizes part numbers, and verifies manufacturer `.lib`/`.cir` macromodels (instructs agent to prompt user if missing).
   - Headlessly drives Berkeley NGSPICE (via KiCad 10's official `ngspice.dll` or CLI) and distills waveforms into compact ~50 token figures of merit (transient peaks, DC bias, -3dB bandwidth) to eliminate context bloat.
3. **Pre-Flight Pinout, Polarity & Annotation Auditor (`audit_component_pinouts`)**:
   - Detects SMD LED Pad 1 Cathode vs Pad 2 Anode polarity inversions against schematic nets.
   - Verifies diode orientation, SOT-23 MOSFET gate/source/drain mapping, and unrouted pads.
   - Blocks KiCad GUI F8 "Schematic is not fully annotated" errors by catching alphanumeric reference designators lacking trailing digits.
4. **One-Shot Manufacturing Package Pipeline (`build_production_package`)**:
   - Consolidates 10+ granular tool calls into 1 atomic operation: DRC pre-flight -> Protel Gerbers & drill generation -> zip packaging -> CPL centroid `.csv` export -> 3D STEP model -> 2D PCB & schematic documentation SVGs.
5. **Headless Visual Renders (`render_pcb_3d`, `render_pcb_2d`, `render_schematic`)**:
   - Generates high-resolution 3D board renders (top, bottom, isometric) via `kicad-cli pcb render`.
   - Generates 2D vector layer plots (F.Cu, B.Cu, Silkscreen, Edge.Cuts) via `kicad-cli pcb export svg`.
   - Generates vector schematic sheet renders via `kicad-cli sch export svg`.
6. **Intelligent DRC/ERC Triage (`triage_pcb_drc`)**:
   - Runs `kicad-cli pcb drc --format json` headlessly.
   - Categorizes violations into **Critical Blockers** (shorts, open tracks), **Fab Hazards** (clearance, annular ring), and **Cosmetic Warnings** with exact mm coordinates and suggested fixes.
7. **Automated Placement & Silkscreen Sanitizer (`check_placement_overlaps`, `resolve_placement_overlaps`, `sanitize_silkscreen`)**:
   - Evaluates bounding-box and courtyard clearances between components and board outlines.
   - Automatically clusters related passives around ICs and resolves spatial collisions to achieve 100/100 placement scores.
   - Moves silkscreen designators outside pads and components with standardized offsets.
8. **Autonomous Routing Pipeline (`export_specctra_dsn`, `autoroute_board`, `import_specctra_ses`, `sync_pcb_nets`)**:
   - Headlessly synchronizes schematic netlists and netclasses directly into `.kicad_pcb` files.
   - Translates KiCad boards into Specctra DSN format with netclass routing constraints and keepouts.
   - Executes Freerouting headlessly with real-time pass monitoring and imports SES route solutions directly back into KiCad copper layers.
9. **Project-Local Intelligence & Macros (`list_circuit_macros`, `compile_circuit_macro`, `get_project_context`, `execute_project_script`)**:
   - Parametric subcircuit macro compiler (voltage dividers, I2C pullups, status LEDs, bypass capacitor banks) producing instant batch recipes.
   - Automatically detects and inherits `.companion/rules.json` (stackups, manufacturer design rules, preferred parts).
   - Dynamically loads and runs project-specific automation scripts from `.companion/scripts/`.

---

## Setup Across Computers

### 1. Prerequisites (Host Machine)
1. **KiCad 8+**: Ensure `kicad-cli` is installed and added to your system `PATH`.
   - Windows: typically `C:\Program Files\KiCad\8.0\bin` or `C:\Program Files\KiCad\9.0\bin`
   - Linux: `sudo apt install kicad`
   - macOS: `/Applications/KiCad/KiCad.app/Contents/MacOS`
2. **Python Package Manager (`uv`)**:
   - Windows:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   - Linux / macOS:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
3. **Java (Optional, for Freerouting autorouter)**:
   - Java 17+ JRE/JDK if using the autonomous routing pipeline.

---

### 2. One-Click Installation

Clone the repository to any directory on the new machine:
```bash
git clone https://github.com/RyanHailey21/kicad-companion.git
cd kicad-companion
```

#### On Windows:
Run the PowerShell installer (configures Antigravity, Claude, and Codex):
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\install.ps1
```

#### On Linux / macOS:
Make executable and run the bash installer:
```bash
chmod +x ./install.sh
./install.sh
```

---

### 3. Manual Agent Configuration (If needed)

#### Antigravity / Gemini CLI (`~/.gemini/config/mcp_config.json`):
```json
{
  "mcpServers": {
    "kicad-companion": {
      "command": "uv",
      "args": [
        "--directory",
        "<PATH_TO_KICAD_COMPANION>",
        "run",
        "kicad-companion"
      ]
    }
  }
}
```

#### Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json` or `~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kicad-companion": {
      "command": "uv",
      "args": [
        "--directory",
        "<PATH_TO_KICAD_COMPANION>",
        "run",
        "kicad-companion"
      ]
    }
  }
}
```

#### Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.kicad_companion]
command = "uv"
args = ["--directory", "<PATH_TO_KICAD_COMPANION>", "run", "kicad-companion"]
enabled = true
```

---

### 4. Portable Project Context (`.companion/`)

Whenever you create or clone a hardware project repository (e.g. `neuromorphic-ir-event-sensor`), keep the `.companion/` folder committed to git:
- `.companion/rules.json`: Fab clearance constraints, copper stackup, trace rules.
- `.companion/preferred_parts.json`: Stock JLCPCB/LCSC/DigiKey parts and component mappings.
- `.companion/scripts/`: Any custom project generators or DRC hooks.

Any agent on any computer with `kicad-companion` installed will immediately inherit all project rules and automation when opening that workspace.

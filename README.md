# KiCad Companion (Universal AI Hardware Accelerator)

A cross-agent Model Context Protocol (MCP) companion server and skills suite designed to work alongside [Konnect](https://github.com/mixelpixx/Konnect) and KiCad 10.

Compatible with **Antigravity**, **Claude (Desktop & Code)**, and **OpenAI Codex**.

## Capabilities

1. **Instant Headless Visual Renders (`render_pcb_3d`, `render_pcb_2d`, `render_schematic`)**:
   - Generates high-resolution 3D board renders (top, bottom, isometric) via `kicad-cli pcb render`.
   - Generates 2D vector layer plots (F.Cu, B.Cu, Silkscreen, Edge.Cuts) via `kicad-cli pcb export svg`.
   - Generates vector schematic sheet renders via `kicad-cli sch export svg`.
2. **Intelligent DRC/ERC Triage (`triage_pcb_drc`)**:
   - Runs `kicad-cli pcb drc --format json` headlessly.
   - Categorizes violations into **Critical Blockers** (shorts, open tracks), **Fab Hazards** (clearance, annular ring), and **Cosmetic Warnings** with exact mm coordinates and suggested fixes.
3. **Project-Local Intelligence (`get_project_context`, `execute_project_script`)**:
   - Automatically detects and inherits `.companion/rules.json` (stackups, manufacturer design rules, preferred parts).
   - Dynamically loads and runs project-specific automation scripts from `.companion/scripts/`.
4. **KiCad IPC Health & Supervisor (`ensure_kicad_running`)**:
   - Checks if KiCad 10 is running with the target project before live IPC commands are issued, preventing tool failures.

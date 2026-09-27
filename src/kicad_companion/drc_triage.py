import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from .config import find_kicad_cli, get_render_cache_dir


def _same_footprint(items: List[Dict[str, Any]]) -> bool:
    """True when every item is a pad of one footprint, e.g. 'PTH pad 2 [/Q_B] of Q1'."""
    owners = set()
    for it in items:
        d = it.get("description", "")
        if " pad " not in d or " of " not in d:
            return False
        owners.add(d.rsplit(" of ", 1)[-1])
    return len(owners) == 1 and len(items) > 1


def triage_pcb_drc(
    pcb_path: str,
    max_items_per_group: int = 10,
    refill_zones: bool = False,
    schematic_parity: bool = False,
) -> Dict[str, Any]:
    """Run DRC in JSON mode and triage violations into categorized, actionable insights.

    Args:
        refill_zones: Refill copper zones before checking (catches stale pours).
        schematic_parity: Also compare the board against the schematic (missing/extra
            footprints, net and field mismatches), like the GUI's parity check.
    """
    kicad_cli = find_kicad_cli()
    p_path = Path(pcb_path).resolve()
    if not p_path.is_file():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    cache_dir = get_render_cache_dir()
    timestamp = int(time.time() * 1000)
    report_file = cache_dir / f"{p_path.stem}_drc_{timestamp}.json"

    cmd = [
        kicad_cli,
        "pcb",
        "drc",
        "--format",
        "json",
        "--units",
        "mm",
        "--severity-all",
        "-o",
        str(report_file),
    ]
    if refill_zones:
        cmd.append("--refill-zones")
    if schematic_parity:
        cmd.append("--schematic-parity")
    cmd.append(str(p_path))

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if not report_file.exists():
        err_msg = proc.stderr.strip() or proc.stdout.strip() or "Failed to generate DRC report"
        raise RuntimeError(f"DRC run failed: {err_msg}")

    try:
        with open(report_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"Failed to parse DRC JSON report: {e}")

    violations = data.get("violations", [])
    unconnected_items = data.get("unconnected_items", [])
    parity_items = data.get("schematic_parity", [])
    footprint_internal: List[str] = []

    critical_errors: List[Dict[str, Any]] = []
    fab_hazards: List[Dict[str, Any]] = []
    cosmetics: List[Dict[str, Any]] = []
    library_mismatches: List[Dict[str, Any]] = []

    for v in violations:
        v_type = v.get("type", "")
        severity = v.get("severity", "warning").lower()
        desc = v.get("description", "")
        items = v.get("items", [])

        entry = {
            "type": v_type,
            "severity": severity,
            "description": desc,
            "items": [
                {
                    "description": item.get("description", ""),
                    "pos": item.get("pos"),
                    "uuid": item.get("uuid"),
                }
                for item in items
            ],
        }

        if "clearance" in v_type and _same_footprint(items):
            owner = items[0].get("description", "").rsplit(" of ", 1)[-1]
            entry["hint"] = (f"Pads of {owner} are closer together than the net-class clearance. "
                             "This is the footprint's own pad spacing: pick a wider footprint variant "
                             "or lower that net class clearance; rerouting cannot fix it.")
            footprint_internal.append(owner)

        if severity == "error" and "silk" not in v_type:
            critical_errors.append(entry)
        elif any(k in v_type for k in ["short", "copper_edge_clearance", "hole_clearance"]) or (
            "clearance" in v_type and "silk" not in v_type
        ):
            critical_errors.append(entry)
        elif "silk" in v_type:
            if "silk_over_copper" in v_type:
                fab_hazards.append(entry)
            else:
                cosmetics.append(entry)
        elif any(k in v_type for k in ["text", "courtyard"]):
            cosmetics.append(entry)
        elif "lib" in v_type:
            library_mismatches.append(entry)
        else:
            fab_hazards.append(entry)

    # Group unconnected items by Net if available in descriptions
    net_unconnected: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    pour_islands = 0   # zone-to-zone pairs: KiCad reports the zone's first corner, not the island
    for u in unconnected_items:
        items = u.get("items", [])
        if items and all(it.get("description", "").startswith("Zone ") for it in items):
            pour_islands += 1
        desc = u.get("description", "")
        for it in items:
            it_desc = it.get("description", "")
            # Pad X [NetName] of Ref
            net_name = "UNSPECIFIED"
            if "[" in it_desc and "]" in it_desc:
                net_name = it_desc[it_desc.find("[") + 1 : it_desc.find("]")]
            net_unconnected[net_name].append(
                {
                    "item": it_desc,
                    "pos": it.get("pos"),
                    "uuid": it.get("uuid"),
                }
            )

    # Produce actionable remediation advice
    remediations: List[str] = []
    if critical_errors:
        remediations.append(f"Resolve {len(critical_errors)} clearance/short violations before routing further.")
    if net_unconnected:
        top_nets = sorted(net_unconnected.keys(), key=lambda k: len(net_unconnected[k]), reverse=True)[:5]
        top_summary = ", ".join([f"{n} ({len(net_unconnected[n])} pins)" for n in top_nets])
        remediations.append(f"Route major unconnected nets: {top_summary}.")
    if pour_islands:
        remediations.append(
            f"{pour_islands} unconnected item(s) are copper-pour islands (zone-to-zone; the reported "
            "position is only the zone's corner). Run finalize_pcb (island_vias=True): it ties them with "
            "a via or short track and lists any it cannot fix with their stranded pads. Next time, run "
            "fanout_vias before autorouting.")
    narrow = sorted({it.get("description", "").split("[")[1].split("]")[0]
                     for e in critical_errors if e.get("type") == "track_width"
                     for it in e.get("items", []) if "[" in it.get("description", "")})
    if narrow:
        remediations.append(
            f"Tracks below the minimum width on {', '.join(narrow)}: Freerouting necks tracks down to squeeze "
            "between fine-pitch pins (it can ignore the neckdown setting). Reroute those short segments around "
            "the pin row by hand, give the IC more room, or lower min_track_width only if the fab supports it.")
    if footprint_internal:
        remediations.append(f"Footprint-internal pad spacing violates clearance on: {', '.join(sorted(set(footprint_internal)))}.")
    if parity_items:
        remediations.append(f"{len(parity_items)} schematic/board mismatches: run sync_pcb_nets (or finalize_pcb) to re-link.")
    if library_mismatches:
        remediations.append(f"{len(library_mismatches)} footprints differ from library copies. Consider 'Update Footprints from Library'.")
    if not critical_errors and not net_unconnected and not parity_items:
        remediations.append("Board is clean of electrical and connectivity errors. Ready for final fabrication checks.")

    # Format a concise markdown executive summary
    summary_lines = [
        f"### DRC Triage Summary for `{p_path.name}`",
        f"- **Total Violations:** {len(violations)}",
        f"- **Unconnected Items:** {len(unconnected_items)}",
        f"- **Critical Errors:** {len(critical_errors)}",
        f"- **Fab/Clearance Hazards:** {len(fab_hazards)}",
        f"- **Cosmetic Warnings:** {len(cosmetics)}",
        f"- **Library Mismatches:** {len(library_mismatches)}",
    ] + ([f"- **Schematic Parity Issues:** {len(parity_items)}"] if schematic_parity else []) + [
        "",
        "#### Recommendations:",
    ]
    for r in remediations:
        summary_lines.append(f"1. {r}")

    return {
        "status": "success",
        "board": str(p_path),
        "total_violations": len(violations),
        "total_unconnected": len(unconnected_items),
        "pour_island_items": pour_islands,
        "summary_markdown": "\n".join(summary_lines),
        "critical_errors": critical_errors[:max_items_per_group],
        "fab_hazards": fab_hazards[:max_items_per_group],
        "cosmetics": cosmetics[:max_items_per_group],
        "library_mismatches": library_mismatches[:max_items_per_group],
        "top_unconnected_nets": {
            net: items[:5]
            for net, items in sorted(net_unconnected.items(), key=lambda x: len(x[1]), reverse=True)[:max_items_per_group]
        },
        "schematic_parity": [
            {"type": v.get("type"), "description": v.get("description"),
             "items": [i.get("description") for i in v.get("items", [])]}
            for v in parity_items[:max_items_per_group]
        ],
        "total_parity_issues": len(parity_items),
        "remediations": remediations,
        "raw_report_path": str(report_file),
    }

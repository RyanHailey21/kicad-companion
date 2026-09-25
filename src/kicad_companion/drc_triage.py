import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from .config import find_kicad_cli, get_render_cache_dir


def triage_pcb_drc(
    pcb_path: str,
    max_items_per_group: int = 10,
) -> Dict[str, Any]:
    """Run DRC in JSON mode and triage violations into categorized, actionable insights."""
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
        str(p_path),
    ]

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

        if severity == "error" or any(k in v_type for k in ["short", "clearance", "copper"]):
            critical_errors.append(entry)
        elif any(k in v_type for k in ["silkscreen", "text", "courtyard"]):
            cosmetics.append(entry)
        elif "lib" in v_type:
            library_mismatches.append(entry)
        else:
            fab_hazards.append(entry)

    # Group unconnected items by Net if available in descriptions
    net_unconnected: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for u in unconnected_items:
        items = u.get("items", [])
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
    if library_mismatches:
        remediations.append(f"{len(library_mismatches)} footprints differ from library copies. Consider 'Update Footprints from Library'.")
    if not critical_errors and not net_unconnected:
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
        "summary_markdown": "\n".join(summary_lines),
        "critical_errors": critical_errors[:max_items_per_group],
        "fab_hazards": fab_hazards[:max_items_per_group],
        "cosmetics": cosmetics[:max_items_per_group],
        "library_mismatches": library_mismatches[:max_items_per_group],
        "top_unconnected_nets": {
            net: items[:5]
            for net, items in sorted(net_unconnected.items(), key=lambda x: len(x[1]), reverse=True)[:max_items_per_group]
        },
        "remediations": remediations,
        "raw_report_path": str(report_file),
    }

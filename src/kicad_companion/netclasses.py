"""Net-class configuration in the .kicad_pro that actually matches KiCad's net names.

Nets named by local labels carry their sheet path ("/GND", "/sub/VREF"), while global
power symbols produce bare names ("GND"). A pattern like "GND*" silently matches only
the latter, so every user pattern is expanded to both "<pat>" and "*/<pat>".
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .kicad_python import export_netlist
from .project_context import find_project_root
from .supervisor import guard_headless_write

# leaf-name heuristics for the automatic Power class
_POWER_RE = re.compile(
    r"^(?:[AD]?GND\w*|\w*GND|V(?:CC|DD|SS|EE|BAT|BUS|IN|SYS|MOT)\w*|\+?\d+V\d*\w*|\d+V\d+\w*|\+\w+|PWR\w*|VPP\w*)$",
    re.IGNORECASE,
)

DEFAULT_CLASSES: Dict[str, Dict[str, float]] = {
    "Default": {"track_width": 0.25, "clearance": 0.2, "via_diameter": 0.6, "via_drill": 0.3},
    "Power": {"track_width": 0.5, "clearance": 0.2, "via_diameter": 0.8, "via_drill": 0.4},
}


def expand_pattern(pat: str) -> List[str]:
    """'VREF' -> ['VREF', '*/VREF'];  '/VREF' or '*x' are kept as given."""
    if pat.startswith("/") or pat.startswith("*"):
        return [pat]
    return [pat, "*/" + pat]


def leaf(net: str) -> str:
    return net.rsplit("/", 1)[-1]


def _fab_minimums(root: Path) -> Dict[str, float]:
    rules = root / ".companion" / "rules.json"
    if rules.is_file():
        try:
            d = json.loads(rules.read_text(encoding="utf-8"))
            return {"track": float(d.get("min_trace_width_mm", 0)), "clear": float(d.get("min_clearance_mm", 0))}
        except Exception:
            pass
    return {"track": 0.0, "clear": 0.0}


def configure_netclasses(
    project_path: str,
    classes: Optional[Dict[str, Dict[str, float]]] = None,
    assignments: Optional[Dict[str, List[str]]] = None,
    auto_power: bool = True,
    overwrite_classes: bool = False,
    replace_patterns: bool = False,
    board_rules: Optional[Dict[str, float]] = None,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Create/update net classes and name patterns in the project's .kicad_pro.

    Args:
        project_path: Any path inside the project (the .kicad_pro is located automatically).
        classes: {name: {track_width, clearance, via_diameter, via_drill}} in mm.
        assignments: {class_name: [net names or wildcard patterns]}; sheet prefixes are handled.
        auto_power: Add a Power class and assign supply/ground nets found in the schematic.
        overwrite_classes: Replace dimensions of classes that already exist (default keeps them).
        replace_patterns: Drop existing netclass_patterns instead of merging.
        board_rules: Board minimums, e.g. {"min_clearance": 0.2, "min_track_width": 0.2}.
        allow_while_open: Write even if KiCad has the project open.
    """
    root = find_project_root(project_path)
    pros = sorted(root.glob("*.kicad_pro")) if root else []
    if not pros:
        raise FileNotFoundError(f"No .kicad_pro found for {project_path}")
    pro_path = pros[0]
    guard_headless_write(str(pro_path), allow_while_open)

    pro = json.loads(pro_path.read_text(encoding="utf-8"))
    ns = pro.setdefault("net_settings", {})
    existing = {c["name"]: c for c in ns.get("classes", []) if "name" in c}
    template = dict(existing.get("Default") or next(iter(existing.values()), {}))
    mins = _fab_minimums(root)

    wanted: Dict[str, Dict[str, float]] = {}
    if auto_power:
        wanted.update({k: dict(v) for k, v in DEFAULT_CLASSES.items()})
    for name, dims in (classes or {}).items():
        wanted.setdefault(name, {}).update(dims)

    created, updated, kept = [], [], []
    for name, dims in wanted.items():
        dims = dict(dims)
        if mins["track"] and "track_width" in dims:
            dims["track_width"] = max(dims["track_width"], mins["track"])
        if mins["clear"] and "clearance" in dims:
            dims["clearance"] = max(dims["clearance"], mins["clear"])
        if name in existing:
            if overwrite_classes or name in (classes or {}):
                existing[name].update(dims)
                updated.append(name)
            else:
                kept.append(name)
        else:
            c = dict(template)
            c.update(name=name, **dims)
            existing[name] = c
            created.append(name)
    order = ["Default"] + [n for n in existing if n != "Default"]
    for i, name in enumerate(order):
        if name in existing:
            existing[name]["priority"] = 2147483647 if name == "Default" else i
    ns["classes"] = [existing[n] for n in order if n in existing]

    patterns: List[Dict[str, str]] = [] if replace_patterns else list(ns.get("netclass_patterns") or [])
    seen = {(p.get("netclass"), p.get("pattern")) for p in patterns}

    def add(cls: str, pat: str) -> None:
        for p in expand_pattern(pat):
            if (cls, p) not in seen:
                seen.add((cls, p))
                patterns.append({"netclass": cls, "pattern": p})

    power_nets: List[str] = []
    if auto_power:
        sch = sorted(root.glob("*.kicad_sch"))
        if sch:
            nets = export_netlist(sch[0])["nets"]
            power_nets = sorted({leaf(n) for n in nets if not n.startswith("unconnected-") and _POWER_RE.match(leaf(n))})
            for n in power_nets:
                add("Power", n)
    for cls, pats in (assignments or {}).items():
        if cls not in existing:
            raise ValueError(f"Net class '{cls}' is not defined; pass it in `classes`.")
        for pat in pats:
            add(cls, pat)
    ns["netclass_patterns"] = patterns
    ns["netclass_assignments"] = None

    if board_rules:
        pro.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {}).update(board_rules)

    pro_path.write_text(json.dumps(pro, indent=2), encoding="utf-8")
    return {
        "status": "success",
        "project_file": str(pro_path),
        "classes": {c["name"]: {k: c.get(k) for k in ("track_width", "clearance", "via_diameter", "via_drill")}
                    for c in ns["classes"]},
        "created": created, "updated": updated, "kept_existing": kept,
        "auto_power_nets": power_nets,
        "pattern_count": len(patterns),
        "note": "Close KiCad before running; it rewrites the .kicad_pro from memory on exit.",
    }

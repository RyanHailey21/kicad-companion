"""Generate a KiCad schematic from a declarative parts/nets spec (headless, no GUI/IPC needed).

Spec (JSON file or dict):
{
  "title": "My board", "revision": "A",
  "parts": [
    {"ref": "R1", "lib_id": "Device:R", "value": "10k",
     "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
     "pins": {"1": "VCC", "2": "OUT"},          # pin number -> net; null = no-connect flag
     "fields": {"MPN": "..."}, "description": "Pull-up", "dnp": false, "group": "Power"},
    {"ref": "U1", "lib_id": "Amplifier_Operational:MCP6022", ...,
     "pins": {"1": "OUT_A", "2": "IN_A-", "3": "VREF", "4": "GND", "8": "VCC", ...}}
  ],
  "power_flags": "auto"                          # or a list of net names, or []
}
Multi-unit symbols are split automatically: every unit (including the power unit) is
placed, and each listed pin is wired on the unit that owns it. Every connection is a
short wire stub plus a local net label, so the result nets up exactly as specified.
"""
import json
import math
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import find_kicad_cli
from .sexp import Sym, dump, find1, load_symbol, parse, symbol_pins, is_power_symbol
from .supervisor import guard_headless_write

GRID = 1.27
STUB = 2.54
CHAR_W = 1.05
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]
MARGIN = 15.0


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _eff(size: float = 1.27, justify: Optional[str] = None, hide: bool = False) -> list:
    e: list = [Sym("effects"), [Sym("font"), [Sym("size"), size, size]]]
    if justify:
        e.append([Sym("justify")] + [Sym(j) for j in justify.split()])
    if hide:
        e.append([Sym("hide"), Sym("yes")])
    return e


def _outward(angle: float) -> Tuple[int, int, float]:
    a = math.radians((angle + 180) % 360)
    return round(math.cos(a)), -round(math.sin(a)), (angle + 180) % 360


def _load_spec(spec: Any) -> Dict[str, Any]:
    if isinstance(spec, dict):
        return spec
    p = Path(str(spec))
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(str(spec))


def _layout(units: List[Dict[str, Any]], width: float) -> Tuple[List[Tuple[str, float, float]], float]:
    groups: List[str] = []
    for u in units:
        if u["group"] not in groups:
            groups.append(u["group"])
    texts = []
    cx, cy, row_h = MARGIN, MARGIN + 10, 0.0
    for g in groups:
        if cx > MARGIN:
            cx, cy, row_h = MARGIN, cy + row_h + 9, 0.0
        texts.append((g, cx, cy - 4))
        for u in (u for u in units if u["group"] == g):
            x0, y0, x1, y1 = u["box"]
            w, h = x1 - x0, y1 - y0
            if cx + w > width - MARGIN and cx > MARGIN:
                cx, cy, row_h = MARGIN, cy + row_h + 4, 0.0
            u["at"] = (_snap(cx - x0), _snap(cy - y0))
            cx += w + 4
            row_h = max(row_h, h)
    return texts, cy + row_h


def generate_schematic(
    spec: Any,
    sch_path: str,
    overwrite: bool = False,
    run_erc: bool = True,
    allow_while_open: bool = False,
) -> Dict[str, Any]:
    """Write a complete .kicad_sch from a parts/nets spec, then run ERC.

    Args:
        spec: Spec dict, JSON string, or path to a JSON file (see module docstring).
        sch_path: Output schematic path (a sibling .kicad_pro gives the project name).
        overwrite: Replace an existing schematic that already contains symbols.
        run_erc: Run kicad-cli ERC afterwards and summarise violations.
    """
    s = _load_spec(spec)
    out = Path(sch_path).resolve()
    guard_headless_write(str(out), allow_while_open)
    root_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, str(out).lower()))
    if out.is_file():
        tree = parse(out.read_text(encoding="utf-8"))
        if find1(tree, "symbol") is not None and not overwrite:
            raise FileExistsError(f"{out.name} already has symbols; pass overwrite=True to regenerate it.")
        if find1(tree, "uuid"):
            root_uuid = str(find1(tree, "uuid")[1])
    pros = sorted(out.parent.glob("*.kicad_pro"))
    project = pros[0].stem if pros else out.stem

    libsyms: Dict[str, list] = {}
    errors, warnings = [], []
    parts = list(s.get("parts", []))
    refs = [p.get("ref") for p in parts]
    dupes = sorted({r for r in refs if refs.count(r) > 1})
    if dupes:
        raise ValueError(f"Duplicate references in spec: {dupes}")

    # ---------------- resolve symbols, split pins across units
    units: List[Dict[str, Any]] = []
    net_pintypes: Dict[str, set] = {}
    for p in parts:
        lib_id = p["lib_id"]
        if lib_id not in libsyms:
            try:
                libsyms[lib_id] = load_symbol(lib_id, out.parent)
            except (FileNotFoundError, KeyError) as e:
                errors.append(f"{p['ref']}: {e}")
                continue
        sym = libsyms[lib_id]
        pins = symbol_pins(sym)
        by_num: Dict[str, List[Dict[str, Any]]] = {}
        for q in pins:
            by_num.setdefault(q["number"], []).append(q)
        unit_ids = sorted({q["unit"] for q in pins if q["unit"] > 0}) or [1]
        pinmap: Dict[str, Optional[str]] = {str(k): v for k, v in (p.get("pins") or {}).items()}
        for unit_key, sub in (p.get("units") or {}).items():
            pinmap.update({str(k): v for k, v in sub.items()})
        for num in pinmap:
            if num not in by_num:
                errors.append(f"{p['ref']}: pin '{num}' does not exist on {lib_id} (valid: {sorted(by_num)})")
        unassigned = sorted(n for n, qs in by_num.items()
                            if n not in pinmap and qs[0]["type"] != "no_connect")
        if unassigned and not is_power_symbol(sym):
            warnings.append(f"{p['ref']}: pins without a net or NC flag: {unassigned}")
        for n, net in pinmap.items():
            if net and n in by_num:
                net_pintypes.setdefault(net, set()).add(by_num[n][0]["type"])
        for un in unit_ids:
            ups = [q for q in pins if q["unit"] in (0, un)]
            units.append(dict(part=p, unit=un, pins=ups, netmap=pinmap, group=p.get("group", "Circuit")))
    if errors:
        return {"status": "error", "errors": errors, "warnings": warnings}

    # ---------------- automatic PWR_FLAGs
    pf = s.get("power_flags", "auto")
    flag_nets = sorted(n for n, t in net_pintypes.items() if "power_in" in t and "power_out" not in t) if pf == "auto" else list(pf or [])
    if flag_nets:
        libsyms.setdefault("power:PWR_FLAG", load_symbol("power:PWR_FLAG", out.parent))
        flag_pins = symbol_pins(libsyms["power:PWR_FLAG"])
        for i, net in enumerate(flag_nets):
            fp_part = {"ref": "#FLG%02d" % (i + 1), "lib_id": "power:PWR_FLAG", "value": "PWR_FLAG",
                       "footprint": "", "group": "Power flags", "pins": {"1": net}}
            units.append(dict(part=fp_part, unit=1, pins=flag_pins, netmap={"1": net}, group="Power flags"))

    # ---------------- extents
    for u in units:
        p, ups = u["part"], u["pins"]
        xs = [q["x"] for q in ups] or [0.0]
        ys = [-q["y"] for q in ups] or [0.0]
        x0, x1, y0, y1 = min(xs) - 2, max(xs) + 2, min(ys) - 2, max(ys) + 2
        vertical = any(q["angle"] in (90.0, 270.0) for q in ups)
        for q in ups:
            net = u["netmap"].get(q["number"])
            if not net:
                continue
            dx, dy, _ = _outward(q["angle"])
            L = STUB + len(net) * CHAR_W + 1
            ex, ey = q["x"] + dx * L, -q["y"] + dy * L
            x0, x1, y0, y1 = min(x0, ex), max(x1, ex), min(y0, ey), max(y1, ey)
        if vertical:
            x1 += max(len(p["ref"]), len(str(p.get("value", "")))) * CHAR_W + 3
        else:
            y0 -= 3
            y1 += 3
        u["box"], u["vertical"] = (x0, y0, x1, y1), vertical

    paper = None
    for name, w, h in PAPERS:
        if s.get("paper") and s["paper"] != name:
            continue
        texts, bottom = _layout(units, w)
        if bottom <= h - MARGIN - 20:
            paper = (name, w, h)
            break
    if paper is None:
        raise ValueError("Design does not fit on A0; split it into hierarchical sheets.")

    # ---------------- emit
    items: list = []
    labels = 0
    for u in units:
        p, (X, Y) = u["part"], u["at"]
        ref = p["ref"]
        sym = libsyms[p["lib_id"]]
        pwr = is_power_symbol(sym)
        mech = p["lib_id"].startswith("Mechanical:")
        if u["vertical"]:
            xr = X + max(q["x"] for q in u["pins"]) + 2.54 if u["pins"] else X + 2.54
            rpos, vpos, just = (xr, Y - 1.27), (xr, Y + 1.27), "left"
        else:
            top = min((-q["y"] for q in u["pins"]), default=0.0)
            bot = max((-q["y"] for q in u["pins"]), default=0.0)
            rpos, vpos, just = (X, Y + top - 3.81), (X, Y + bot + 3.81), None
        props = [
            [Sym("property"), "Reference", ref, [Sym("at"), rpos[0], rpos[1], 0], _eff(justify=just)],
            [Sym("property"), "Value", str(p.get("value", "")), [Sym("at"), vpos[0], vpos[1], 0], _eff(justify=just)],
            [Sym("property"), "Footprint", p.get("footprint", ""), [Sym("at"), X, Y, 0], _eff(hide=True)],
            [Sym("property"), "Datasheet", p.get("datasheet", ""), [Sym("at"), X, Y, 0], _eff(hide=True)],
            [Sym("property"), "Description", p.get("description", ""), [Sym("at"), X, Y, 0], _eff(hide=True)],
        ]
        for k, v in (p.get("fields") or {}).items():
            props.append([Sym("property"), k, str(v), [Sym("at"), X, Y, 0], _eff(hide=True)])
        node = [Sym("symbol"), [Sym("lib_id"), p["lib_id"]], [Sym("at"), X, Y, 0], [Sym("unit"), u["unit"]],
                [Sym("exclude_from_sim"), Sym("no")],
                [Sym("in_bom"), Sym("no" if (pwr or mech or p.get("exclude_from_bom")) else "yes")],
                [Sym("on_board"), Sym("no" if pwr else "yes")],
                [Sym("dnp"), Sym("yes" if p.get("dnp") else "no")],
                [Sym("uuid"), str(uuid.uuid5(uuid.UUID(root_uuid), "%s/%d" % (ref, u["unit"])))]] + props
        for q in u["pins"]:
            node.append([Sym("pin"), q["number"], [Sym("uuid"), str(uuid.uuid4())]])
        node.append([Sym("instances"), [Sym("project"), project,
                     [Sym("path"), "/" + root_uuid, [Sym("reference"), ref], [Sym("unit"), u["unit"]]]]])
        items.append(node)
        for q in u["pins"]:
            if q["number"] not in u["netmap"]:
                continue
            px, py = _snap(X + q["x"]), _snap(Y - q["y"])
            net = u["netmap"][q["number"]]
            if net is None:
                items.append([Sym("no_connect"), [Sym("at"), px, py], [Sym("uuid"), str(uuid.uuid4())]])
                continue
            dx, dy, ang = _outward(q["angle"])
            lx, ly = px + dx * STUB, py + dy * STUB
            items.append([Sym("wire"), [Sym("pts"), [Sym("xy"), px, py], [Sym("xy"), lx, ly]],
                          [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]],
                          [Sym("uuid"), str(uuid.uuid4())]])
            items.append([Sym("label"), net, [Sym("at"), lx, ly, ang],
                          _eff(justify="left bottom" if ang in (0, 90) else "right bottom"),
                          [Sym("uuid"), str(uuid.uuid4())]])
            labels += 1
    for t, x, y in texts:
        items.append([Sym("text"), t, [Sym("exclude_from_sim"), Sym("no")], [Sym("at"), _snap(x), _snap(y), 0],
                      _eff(size=2.5, justify="left bottom"), [Sym("uuid"), str(uuid.uuid4())]])
    if s.get("notes"):
        items.append([Sym("text"), s["notes"], [Sym("exclude_from_sim"), Sym("no")],
                      [Sym("at"), _snap(paper[1] * 0.6), _snap(paper[2] - 80), 0],
                      _eff(size=1.8, justify="left bottom"), [Sym("uuid"), str(uuid.uuid4())]])

    tb = [Sym("title_block"), [Sym("title"), s.get("title", out.stem)]]
    if s.get("revision"):
        tb.append([Sym("rev"), s["revision"]])
    sch = [Sym("kicad_sch"), [Sym("version"), 20250610], [Sym("generator"), "eeschema"],
           [Sym("generator_version"), "10.0"], [Sym("uuid"), root_uuid], [Sym("paper"), paper[0]], tb,
           [Sym("lib_symbols")] + list(libsyms.values())] + items + \
          [[Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]], [Sym("embedded_fonts"), Sym("no")]]
    out.write_text(dump(sch) + "\n", encoding="utf-8", newline="\n")

    result: Dict[str, Any] = {
        "status": "success", "schematic": str(out), "paper": paper[0], "parts": len(parts),
        "units_placed": len(units), "labels": labels, "power_flags": flag_nets, "warnings": warnings,
    }
    if run_erc:
        result["erc"] = run_schematic_erc(str(out))
        if result["erc"]["violations"]:
            result["status"] = "erc_violations"
    return result


def run_schematic_erc(sch_path: str, max_items: int = 15) -> Dict[str, Any]:
    """Run kicad-cli ERC and summarise violations by type."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rep = Path(td) / "erc.json"
        proc = subprocess.run([find_kicad_cli(), "sch", "erc", "--format", "json", "--severity-all",
                               "-o", str(rep), sch_path], capture_output=True, text=True, check=False)
        if not rep.is_file():
            return {"violations": -1, "error": (proc.stderr or proc.stdout).strip()}
        data = json.loads(rep.read_text(encoding="utf-8"))
    vs = [v for sh in data.get("sheets", []) for v in sh.get("violations", [])]
    by_type: Dict[str, int] = {}
    for v in vs:
        by_type[v.get("type", "?")] = by_type.get(v.get("type", "?"), 0) + 1
    return {"violations": len(vs), "by_type": by_type,
            "items": [{"type": v.get("type"), "severity": v.get("severity"),
                       "items": [i.get("description") for i in v.get("items", [])]} for v in vs[:max_items]]}

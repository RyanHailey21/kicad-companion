"""Minimal, dependency-free KiCad S-expression reader/writer plus symbol-library helpers.

Used for read-only analysis of netlists/libraries and for emitting brand-new
schematics. Existing boards are modified through pcbnew (see kicad_python.py),
never by string surgery.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import find_symbol_dir

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


class Sym(str):
    """An unquoted atom (keyword, number or bare identifier)."""


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def parse(text: str) -> Any:
    """Parse S-expression text into nested lists of Sym / str."""
    stack: List[list] = []
    cur: list = []
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if not text[pos:].strip():
                break
            raise ValueError(f"S-expression parse error at offset {pos}")
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(_unescape(m.group(3)))
        elif m.group(4) is not None:
            cur.append(Sym(m.group(4)))
    return cur[0] if len(cur) == 1 else cur


def dump(e: Any, indent: int = 0) -> str:
    """Serialize nested lists back to KiCad-style S-expression text."""
    if isinstance(e, list):
        if not e:
            return "()"
        if all(not isinstance(x, list) for x in e):
            return "(" + " ".join(dump(x) for x in e) + ")"
        out = "(" + dump(e[0])
        for x in e[1:]:
            out += ("\n" + "\t" * (indent + 1) + dump(x, indent + 1)) if isinstance(x, list) else " " + dump(x)
        return out + "\n" + "\t" * indent + ")"
    if isinstance(e, Sym):
        return str(e)
    if isinstance(e, bool):
        return "yes" if e else "no"
    if isinstance(e, (int, float)):
        s = ("%.4f" % e).rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return '"' + str(e).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def find(e: list, key: str) -> List[list]:
    return [x for x in e if isinstance(x, list) and x and x[0] == key]


def find1(e: Optional[list], key: str) -> Optional[list]:
    if not e:
        return None
    r = find(e, key)
    return r[0] if r else None


def value(e: Optional[list], key: str, default: Any = None) -> Any:
    """Return the first atom of child `key`, e.g. value(comp, "ref")."""
    node = find1(e, key)
    return node[1] if node and len(node) > 1 else default


# ------------------------------------------------------------------ symbol libraries
_lib_cache: Dict[str, list] = {}


def _lib_paths(project_dir: Optional[Path]) -> Dict[str, Path]:
    """Map library nickname -> .kicad_sym path (project sym-lib-table overrides stock libs)."""
    paths: Dict[str, Path] = {}
    try:
        for f in find_symbol_dir().glob("*.kicad_sym"):
            paths[f.stem] = f
    except FileNotFoundError:
        pass
    if project_dir and (project_dir / "sym-lib-table").is_file():
        table = parse((project_dir / "sym-lib-table").read_text(encoding="utf-8"))
        for lib in find(table, "lib"):
            uri = str(value(lib, "uri", "")).replace("${KIPRJMOD}", str(project_dir))
            if uri.endswith(".kicad_sym"):
                paths[str(value(lib, "name"))] = Path(uri)
    return paths


def load_symbol(lib_id: str, project_dir: Optional[Path] = None) -> list:
    """Return a fully resolved symbol definition (``extends`` flattened) named ``lib_id``."""
    lib, name = lib_id.split(":", 1)
    paths = _lib_paths(project_dir)
    if lib not in paths:
        raise FileNotFoundError(f"Symbol library '{lib}' not found (lib_id {lib_id})")
    key = str(paths[lib])
    if key not in _lib_cache:
        _lib_cache[key] = parse(paths[lib].read_text(encoding="utf-8"))
    syms = {s[1]: s for s in find(_lib_cache[key], "symbol")}
    if name not in syms:
        raise KeyError(f"Symbol '{name}' not found in library '{lib}'")
    s = syms[name]
    ext = find1(s, "extends")
    if ext:
        base = syms[ext[1]]
        props = {p[1]: p for p in find(s, "property")}
        new: list = [Sym("symbol"), name]
        for item in base[2:]:
            if isinstance(item, list) and item[0] == "property" and item[1] in props:
                new.append(props.pop(item[1]))
            elif isinstance(item, list) and item[0] == "symbol":
                sub = list(item)
                sub[1] = sub[1].replace(ext[1], name, 1)
                new.append(sub)
            else:
                new.append(item)
        new.extend(props.values())
        for item in s[2:]:
            if isinstance(item, list) and item[0] not in ("property", "extends", "symbol") and not find1(new, item[0]):
                new.append(item)
        s = new
    out = list(s)
    out[1] = lib_id
    return out


def symbol_pins(sym: list) -> List[Dict[str, Any]]:
    """Pins of a resolved symbol in symbol coordinates (y up). unit 0 = common to all units."""
    pins = []
    for sub in find(sym, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", sub[1])
        unit = int(m.group(1)) if m else 0
        for p in find(sub, "pin"):
            at = find1(p, "at")
            pins.append(dict(
                number=str(value(p, "number")), name=str(value(p, "name", "")),
                x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0,
                length=float(value(p, "length", 0)), unit=unit, type=str(p[1]),
            ))
    return pins


def symbol_property(sym: list, name: str, default: str = "") -> str:
    for p in find(sym, "property"):
        if p[1] == name:
            return str(p[2])
    return default


def is_power_symbol(sym: list) -> bool:
    return find1(sym, "power") is not None


# ------------------------------------------------------------------ netlists
def read_netlist(text: str) -> Dict[str, Any]:
    """Parse a kicad-cli (kicadsexpr) netlist into components and nets."""
    tree = parse(text)
    comps: Dict[str, Dict[str, Any]] = {}
    for c in find(find1(tree, "components") or [], "comp"):
        fields = {}
        for f in find(find1(c, "fields") or [], "field"):
            fields[str(value(f, "name"))] = str(f[2]) if len(f) > 2 else ""
        props = {str(value(p, "name")): str(value(p, "value", "")) for p in find(c, "property")}
        comps[str(value(c, "ref"))] = dict(
            value=str(value(c, "value", "")), footprint=str(value(c, "footprint", "")),
            description=str(value(c, "description", "")), tstamp=str(value(c, "tstamps", "")),
            lib=value(find1(c, "libsource"), "lib", ""), part=value(find1(c, "libsource"), "part", ""),
            fields=fields, properties=props, pins={},
        )
    nets: Dict[str, List[Dict[str, str]]] = {}
    for net in find(find1(tree, "nets") or [], "net"):
        name = str(value(net, "name"))
        nodes = []
        for nd in find(net, "node"):
            ref, pin = str(value(nd, "ref")), str(value(nd, "pin"))
            nodes.append(dict(ref=ref, pin=pin, pintype=str(value(nd, "pintype", ""))))
            if ref in comps:
                comps[ref]["pins"][pin] = name
        nets[name] = nodes
    return {"components": comps, "nets": nets}

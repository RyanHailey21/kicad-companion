"""Project symbol library helpers (derive a symbol with extra pins, e.g. an exposed pad)."""
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import sexp
from .project_context import find_project_root
from .sexp import Sym, dump, find, find1, load_symbol, parse, value


def _ensure_lib_table(root: Path, nickname: str, lib_file: Path) -> bool:
    table_path = root / "sym-lib-table"
    table = parse(table_path.read_text(encoding="utf-8")) if table_path.is_file() else [Sym("sym_lib_table"), [Sym("version"), 7]]
    if any(value(lib, "name") == nickname for lib in find(table, "lib")):
        return False
    table.append([Sym("lib"), [Sym("name"), nickname], [Sym("type"), "KiCad"],
                  [Sym("uri"), "${KIPRJMOD}/" + lib_file.name], [Sym("options"), ""], [Sym("descr"), "Project symbols"]])
    table_path.write_text(dump(table) + "\n", encoding="utf-8")
    return True


def create_derived_symbol(
    project_path: str,
    base_lib_id: str,
    new_name: str,
    add_pins: List[Dict[str, Any]],
    library: Optional[str] = None,
    properties: Optional[Dict[str, str]] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy a library symbol into the project library under a new name, adding pins.

    Typical use: an IC whose footprint has an exposed/thermal pad that must be tied to a net
    but whose stock symbol has no pin for it.

    Args:
        project_path: Any path in the project.
        base_lib_id: e.g. "Amplifier_Operational:MCP6022".
        new_name: Name of the new symbol, e.g. "OPA2381_DRB".
        add_pins: [{"number": "9", "name": "EP", "type": "passive", "like": "4", "dx": 2.54, "dy": 0}];
            each new pin is cloned from pin `like` (same unit/orientation), offset by dx/dy mm.
        library: Project library nickname (default: project name). Created and registered in
            sym-lib-table if missing.
        properties: Property overrides, e.g. {"Footprint": "Package_DFN_QFN:Texas_DRB0008A"}.
        overwrite: Replace an existing symbol of the same name.
    """
    root = find_project_root(project_path)
    pros = sorted(root.glob("*.kicad_pro")) if root else []
    if not pros:
        raise FileNotFoundError(f"No .kicad_pro found for {project_path}")
    nickname = library or pros[0].stem
    lib_file = root / f"{nickname}.kicad_sym"

    base = load_symbol(base_lib_id, root)
    base_short = base_lib_id.split(":", 1)[1]
    sym = copy.deepcopy(base)
    sym[1] = new_name
    for sub in find(sym, "symbol"):
        sub[1] = sub[1].replace(base_short, new_name, 1)
    for k, v in (properties or {}).items():
        for p in find(sym, "property"):
            if p[1] == k:
                p[2] = v
                break
        else:
            sym.append([Sym("property"), k, v, [Sym("at"), 0, 0, 0],
                        [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]], [Sym("hide"), Sym("yes")]]])

    added = []
    for spec in add_pins:
        src = None
        for sub in find(sym, "symbol"):
            for p in find(sub, "pin"):
                if str(value(p, "number")) == str(spec["like"]):
                    src, owner = p, sub
        if src is None:
            raise KeyError(f"Pin '{spec['like']}' not found on {base_lib_id}")
        pin = copy.deepcopy(src)
        pin[1] = Sym(spec.get("type", str(src[1])))
        at = find1(pin, "at")
        at[1] = float(at[1]) + float(spec.get("dx", 2.54))
        at[2] = float(at[2]) + float(spec.get("dy", 0.0))
        find1(pin, "number")[1] = str(spec["number"])
        find1(pin, "name")[1] = spec.get("name", str(spec["number"]))
        owner.append(pin)
        added.append(spec["number"])

    lib = parse(lib_file.read_text(encoding="utf-8")) if lib_file.is_file() else \
        [Sym("kicad_symbol_lib"), [Sym("version"), 20241209], [Sym("generator"), "kicad_companion"]]
    existing = [s for s in find(lib, "symbol") if s[1] == new_name]
    if existing and not overwrite:
        raise FileExistsError(f"{new_name} already exists in {lib_file.name}; pass overwrite=True.")
    for s in existing:
        lib.remove(s)
    lib.append(sym)
    lib_file.write_text(dump(lib) + "\n", encoding="utf-8")
    registered = _ensure_lib_table(root, nickname, lib_file)
    sexp._lib_cache.pop(str(lib_file), None)
    return {"status": "success", "lib_id": f"{nickname}:{new_name}", "library_file": str(lib_file),
            "added_pins": added, "registered_in_sym_lib_table": registered}

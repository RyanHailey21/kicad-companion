import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def find_project_root(target_path: str) -> Optional[Path]:
    """Find the root directory of a KiCad project by looking for .kicad_pro."""
    curr = Path(target_path).resolve()
    if curr.is_file():
        curr = curr.parent

    for p in [curr, *curr.parents]:
        if list(p.glob("*.kicad_pro")):
            return p
    return curr if curr.is_dir() else None


def get_project_context(target_path: str) -> Dict[str, Any]:
    """Inspect and return project-level configuration, rules, and custom scripts."""
    root = find_project_root(target_path)
    if not root or not root.exists():
        return {"status": "error", "message": f"Could not find project directory for: {target_path}"}

    companion_dir = root / ".companion"
    if not companion_dir.exists():
        companion_dir = root / ".kicad-companion"

    pro_files = [f.name for f in root.glob("*.kicad_pro")]
    sch_files = [f.name for f in root.glob("*.kicad_sch")]
    pcb_files = [f.name for f in root.glob("*.kicad_pcb")]

    rules: Dict[str, Any] = {}
    preferred_parts: Dict[str, Any] = {}
    scripts: List[str] = []

    if companion_dir.exists() and companion_dir.is_dir():
        # Load rules / config
        for r_name in ["rules.json", "config.json"]:
            r_path = companion_dir / r_name
            if r_path.is_file():
                try:
                    with open(r_path, "r", encoding="utf-8") as f:
                        rules.update(json.load(f))
                except Exception:
                    pass

        # Load preferred parts
        parts_path = companion_dir / "preferred_parts.json"
        if parts_path.is_file():
            try:
                with open(parts_path, "r", encoding="utf-8") as f:
                    preferred_parts = json.load(f)
            except Exception:
                pass

        # Discover scripts
        scripts_dir = companion_dir / "scripts"
        if scripts_dir.is_dir():
            scripts = [s.name for s in scripts_dir.glob("*.py")]

    return {
        "status": "success",
        "project_root": str(root),
        "has_companion_dir": companion_dir.exists(),
        "project_files": {
            "pro": pro_files,
            "schematics": sch_files,
            "pcbs": pcb_files,
        },
        "rules": rules,
        "preferred_parts": preferred_parts,
        "available_scripts": scripts,
    }


def execute_project_script(
    target_path: str,
    script_name: str,
    args: Optional[List[str]] = None,
    use_kicad_python: bool = False,
    timeout_s: int = 900,
) -> Dict[str, Any]:
    """Execute a project-specific Python script from the .companion/scripts/ directory.

    use_kicad_python runs it under KiCad's bundled interpreter so it can `import pcbnew`.
    """
    root = find_project_root(target_path)
    if not root:
        raise FileNotFoundError(f"Could not locate project root for: {target_path}")

    companion_dir = root / ".companion"
    if not companion_dir.exists():
        companion_dir = root / ".kicad-companion"

    script_path = companion_dir / "scripts" / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"Project script not found: {script_path}")

    if use_kicad_python:
        from .config import find_kicad_python
        interpreter = find_kicad_python()
    else:
        interpreter = sys.executable
    cmd = [interpreter, str(script_path)]
    if args:
        cmd.extend(args)

    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
    )
    noise = "swig/python detected a memory leak"
    stderr = "\n".join(l for l in proc.stderr.splitlines() if noise not in l)

    return {
        "status": "success" if proc.returncode == 0 else "error",
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": stderr.strip(),
        "script": str(script_path),
        "interpreter": interpreter,
    }

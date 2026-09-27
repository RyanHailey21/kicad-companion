import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from .config import find_kicad_app

_GUI_PROCS = ("kicad", "pcbnew", "eeschema")


def check_kicad_running() -> Dict[str, Any]:
    """Check if any KiCad GUI process is currently running on the system."""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(["tasklist", "/FO", "CSV", "/NH"], text=True, stderr=subprocess.DEVNULL)
            names = [line.split('","')[0].strip('"').lower() for line in out.splitlines() if line.strip()]
            procs = sorted({n for n in names if n.removesuffix(".exe") in _GUI_PROCS})
        else:
            out = subprocess.check_output(["ps", "-eo", "comm"], text=True, stderr=subprocess.DEVNULL)
            procs = sorted({Path(l.strip()).name for l in out.splitlines() if Path(l.strip()).name.lower() in _GUI_PROCS})
        return {"status": "success", "is_running": bool(procs), "processes": procs}
    except Exception as e:
        return {"status": "error", "is_running": False, "processes": [], "error": str(e)}


def project_open_status(path: str) -> Dict[str, Any]:
    """Report whether KiCad appears to have this project's files open (lock files + live process)."""
    p = Path(path).resolve()
    root = p if p.is_dir() else p.parent
    locks: List[str] = sorted(f.name for f in root.glob("~*.lck"))
    running = check_kicad_running().get("is_running", False)
    return {
        "kicad_running": running,
        "lock_files": locks,
        "open_in_kicad": running and bool(locks),
        "stale_locks": bool(locks) and not running,
    }


def guard_headless_write(path: str, allow_while_open: bool = False) -> Dict[str, Any]:
    """Refuse headless file edits while KiCad has the project open.

    KiCad keeps the board and project settings in memory and writes them back on save
    or exit, silently discarding edits made to the files underneath it (net classes in
    the .kicad_pro are the usual casualty).
    """
    st = project_open_status(path)
    if st["open_in_kicad"] and not allow_while_open:
        raise RuntimeError(
            "KiCad has this project open (lock files: %s). Headless edits would be overwritten when "
            "KiCad saves or exits. Close KiCad without saving first, or pass allow_while_open=True "
            "and then use File > Revert in KiCad." % ", ".join(st["lock_files"])
        )
    return st


def ensure_kicad_running(
    project_or_pcb_path: str,
) -> Dict[str, Any]:
    """Check if KiCad is running. If not, launch KiCad with the specified project or PCB file."""
    status = check_kicad_running()
    if status.get("is_running"):
        return {
            "status": "success",
            "action": "none",
            "message": "KiCad is already running.",
            "running_processes": status.get("processes", []),
        }

    kicad_app = find_kicad_app()
    target_path = Path(project_or_pcb_path).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target path does not exist: {project_or_pcb_path}")

    kwargs: Dict[str, Any] = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([kicad_app, str(target_path)], **kwargs)

    return {
        "status": "success",
        "action": "launched",
        "pid": proc.pid,
        "launched_app": kicad_app,
        "target": str(target_path),
        "message": f"Launched KiCad with {target_path.name} (PID: {proc.pid}). Allow 2-3 seconds for IPC server to initialize.",
    }

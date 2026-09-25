import subprocess
from pathlib import Path
from typing import Any, Dict

from .config import find_kicad_app


def check_kicad_running() -> Dict[str, Any]:
    """Check if any KiCad GUI process is currently running on the system."""
    try:
        # Use tasklist on Windows to quickly check running processes
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq kicad*", "/FO", "CSV"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        running = "kicad.exe" in out.lower() or "pcbnew.exe" in out.lower()
        return {
            "status": "success",
            "is_running": running,
            "processes": [line.strip('"').split('","')[0] for line in out.strip().splitlines()[1:] if "kicad" in line.lower()],
        }
    except Exception as e:
        return {"status": "error", "is_running": False, "error": str(e)}


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

    # Launch KiCad in background (detached process)
    # Creation flags 0x00000008 = DETACHED_PROCESS on Windows
    proc = subprocess.Popen(
        [kicad_app, str(target_path)],
        creationflags=0x00000008,
        close_fds=True,
    )

    return {
        "status": "success",
        "action": "launched",
        "pid": proc.pid,
        "launched_app": kicad_app,
        "target": str(target_path),
        "message": f"Launched KiCad with {target_path.name} (PID: {proc.pid}). Allow 2-3 seconds for IPC server to initialize.",
    }

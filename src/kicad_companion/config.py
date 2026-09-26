import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def find_kicad_cli() -> str:
    """Locate the KiCad 10 CLI binary."""
    # 1. User local AppData KiCad 10
    user_appdata = os.environ.get("LOCALAPPDATA", "")
    if user_appdata:
        p = Path(user_appdata) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
        if p.is_file():
            return str(p)

    # 2. Program Files KiCad 10
    prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    p = Path(prog_files) / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
    if p.is_file():
        return str(p)

    # 3. System PATH
    found = shutil.which("kicad-cli")
    if found:
        return found

    # Fallback default
    return str(Path(user_appdata) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad-cli.exe")


def find_kicad_app() -> str:
    """Locate the main KiCad 10 GUI executable."""
    user_appdata = os.environ.get("LOCALAPPDATA", "")
    if user_appdata:
        p = Path(user_appdata) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad.exe"
        if p.is_file():
            return str(p)

    prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    p = Path(prog_files) / "KiCad" / "10.0" / "bin" / "kicad.exe"
    if p.is_file():
        return str(p)

    found = shutil.which("kicad")
    if found:
        return found

    return str(Path(user_appdata) / "Programs" / "KiCad" / "10.0" / "bin" / "kicad.exe")


def get_render_cache_dir() -> Path:
    """Return the temporary rendering directory and ensure it exists."""
    temp_dir = Path(os.environ.get("TEMP", "C:\\Temp")) / "kicad_companion_renders"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def find_ngspice_dll() -> Optional[str]:
    """Locate KiCad's bundled ngspice.dll library."""
    user_appdata = os.environ.get("LOCALAPPDATA", "")
    if user_appdata:
        p = Path(user_appdata) / "Programs" / "KiCad" / "10.0" / "bin" / "ngspice.dll"
        if p.is_file():
            return str(p)

    prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    for ver in ["10.0", "9.0", "8.0"]:
        p = Path(prog_files) / "KiCad" / ver / "bin" / "ngspice.dll"
        if p.is_file():
            return str(p)

    return None


def find_ngspice_cli() -> Optional[str]:
    """Locate standalone ngspice executable if available."""
    found = shutil.which("ngspice")
    if found:
        return found

    prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    for ver in ["10.0", "9.0", "8.0"]:
        p = Path(prog_files) / "KiCad" / ver / "bin" / "ngspice.exe"
        if p.is_file():
            return str(p)

    return None


def find_spice_python() -> str:
    """Find a Python interpreter equipped with SPICE simulation packages (numpy, PySpice)."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "python.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Python311" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)

    which_py = shutil.which("python")
    if which_py:
        return which_py

    return sys.executable



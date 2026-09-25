import os
import shutil
from pathlib import Path

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

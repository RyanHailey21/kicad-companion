import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

KICAD_VERSIONS = ["10.0", "9.0", "8.0"]


def _candidate_bin_dirs() -> List[Path]:
    """KiCad bin directories in priority order (env override first)."""
    dirs: List[Path] = []
    env = os.environ.get("KICAD_BIN_DIR")
    if env:
        dirs.append(Path(env))
    local = os.environ.get("LOCALAPPDATA", "")
    progs = os.environ.get("ProgramFiles", r"C:\Program Files")
    for ver in KICAD_VERSIONS:
        if local:
            dirs.append(Path(local) / "Programs" / "KiCad" / ver / "bin")
        dirs.append(Path(progs) / "KiCad" / ver / "bin")
    dirs.append(Path("/Applications/KiCad/KiCad.app/Contents/MacOS"))
    dirs.append(Path("/usr/bin"))
    dirs.append(Path("/usr/local/bin"))
    return dirs


def _exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def find_kicad_bin_dir() -> Optional[Path]:
    """Directory containing kicad-cli (and on Windows, KiCad's bundled python)."""
    for d in _candidate_bin_dirs():
        if (d / _exe("kicad-cli")).is_file():
            return d
    found = shutil.which("kicad-cli")
    return Path(found).parent if found else None


def find_kicad_cli() -> str:
    """Locate the KiCad CLI binary."""
    d = find_kicad_bin_dir()
    if d:
        return str(d / _exe("kicad-cli"))
    raise FileNotFoundError(
        "kicad-cli not found. Install KiCad 8+ or set KICAD_BIN_DIR to its bin directory."
    )


def find_kicad_app() -> str:
    """Locate the main KiCad GUI executable."""
    d = find_kicad_bin_dir()
    if d and (d / _exe("kicad")).is_file():
        return str(d / _exe("kicad"))
    found = shutil.which("kicad")
    if found:
        return found
    raise FileNotFoundError("KiCad GUI executable not found. Set KICAD_BIN_DIR.")


def find_kicad_python() -> str:
    """Locate a Python interpreter that can `import pcbnew`."""
    env = os.environ.get("KICAD_PYTHON")
    if env and Path(env).is_file():
        return env
    d = find_kicad_bin_dir()
    if d:
        for cand in (d / "python.exe",
                     d.parent / "Frameworks" / "Python.framework" / "Versions" / "Current" / "bin" / "python3"):
            if cand.is_file():
                return str(cand)
    # Linux distro packages install pcbnew into the system interpreter
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("KiCad's Python (with pcbnew) not found. Set KICAD_PYTHON.")


def find_kicad_share() -> Optional[Path]:
    """KiCad shared data directory (symbols/, footprints/, 3dmodels/)."""
    env = os.environ.get("KICAD_SHARE_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    d = find_kicad_bin_dir()
    cands = []
    if d:
        cands += [d.parent / "share" / "kicad", d.parent / "SharedSupport"]
    cands += [Path("/usr/share/kicad"), Path("/usr/local/share/kicad")]
    for c in cands:
        if (c / "symbols").is_dir() or (c / "footprints").is_dir():
            return c
    return None


def find_symbol_dir() -> Path:
    for var in ("KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return Path(os.environ[var])
    share = find_kicad_share()
    if share and (share / "symbols").is_dir():
        return share / "symbols"
    raise FileNotFoundError("KiCad symbol library directory not found. Set KICAD10_SYMBOL_DIR.")


def find_footprint_dir() -> Path:
    for var in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR"):
        if os.environ.get(var) and Path(os.environ[var]).is_dir():
            return Path(os.environ[var])
    share = find_kicad_share()
    if share and (share / "footprints").is_dir():
        return share / "footprints"
    raise FileNotFoundError("KiCad footprint library directory not found. Set KICAD10_FOOTPRINT_DIR.")


def _documents_dirs() -> List[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return [home / "Documents", home / "OneDrive" / "Documents", home]


def find_freerouting_jar() -> str:
    """Locate a Freerouting JAR (env override, KiCad PCM plugin, Konnect bundle, AppData)."""
    env = os.environ.get("FREEROUTING_JAR")
    if env and Path(env).is_file():
        return env
    cands: List[Path] = []
    for docs in _documents_dirs():
        for ver in KICAD_VERSIONS:
            jar_dir = docs / "KiCad" / ver / "3rdparty" / "plugins" / "app_freerouting_kicad-plugin" / "jar"
            if jar_dir.is_dir():
                cands += sorted(jar_dir.glob("freerouting*.jar"), reverse=True)
    konnect = Path.home() / ".konnect"
    if konnect.is_dir():
        cands += sorted(konnect.glob("freerouting*.jar"), reverse=True)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        cands.append(Path(local) / "freerouting" / "freerouting.jar")
    for c in cands:
        if c.is_file():
            return str(c)
    raise FileNotFoundError(
        "Freerouting JAR not found (KiCad PCM plugin, ~/.konnect, or %LOCALAPPDATA%/freerouting). "
        "Set FREEROUTING_JAR."
    )


def get_render_cache_dir() -> Path:
    """Return the temporary rendering directory and ensure it exists."""
    base = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
    temp_dir = Path(base) / "kicad_companion_renders"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def find_ngspice_dll() -> Optional[str]:
    """Locate KiCad's bundled ngspice shared library."""
    d = find_kicad_bin_dir()
    if d:
        for name in ("ngspice.dll", "libngspice.dylib", "libngspice.so"):
            if (d / name).is_file():
                return str(d / name)
    return None


def find_ngspice_cli() -> Optional[str]:
    """Locate standalone ngspice executable if available."""
    found = shutil.which("ngspice")
    if found:
        return found
    d = find_kicad_bin_dir()
    if d and (d / _exe("ngspice")).is_file():
        return str(d / _exe("ngspice"))
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

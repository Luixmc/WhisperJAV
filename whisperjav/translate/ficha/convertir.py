"""Turn .ts videos into .mp4 without re-encoding (the same as convertir_ts.bat).

``ffmpeg -c copy`` only changes the container: the picture and the sound stay
as they are, the broken timestamps of the .ts are rebuilt (``+genpts``) and the
.mp4 gets an index at the front (``+faststart``), so seeking is instant. The
.mp4 is written beside the .ts with the same name; the .ts is never deleted.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


def _ts(rutas: List[Path]) -> List[Path]:
    """The .ts files given, and those inside the folders given (subfolders too)."""
    found: List[Path] = []
    for ruta in rutas:
        files = sorted(ruta.rglob("*")) if ruta.is_dir() else [ruta]
        for f in files:
            if f.suffix.lower() == ".ts" and f.is_file() and f not in found:
                found.append(f)
    return found


def convertir_uno(ts: Path) -> Optional[Path]:
    """The .mp4 for ``ts`` (made now, or already there), or None if ffmpeg failed."""
    mp4 = ts.with_suffix(".mp4")
    if mp4.exists():
        print(f"ya convertido: {mp4.name}", file=sys.stderr, flush=True)
        return mp4
    print(f"convirtiendo {ts.name} a .mp4...", file=sys.stderr, flush=True)
    inicio = time.monotonic()
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-fflags", "+genpts",
        "-i", str(ts), "-map", "0:v", "-map", "0:a?", "-c", "copy",
        "-movflags", "+faststart", "-n", str(mp4),
    ]
    if subprocess.run(command).returncode != 0 or not mp4.exists():
        if mp4.exists():
            mp4.unlink()
        print(f"error: no se pudo convertir {ts.name}", file=sys.stderr, flush=True)
        return None
    print(f"listo: {mp4.name} ({time.monotonic() - inicio:.0f} s)", file=sys.stderr, flush=True)
    return mp4


def convertir(rutas: List[Path]) -> int:
    """Convert every .ts in ``rutas``; the number that failed."""
    if not shutil.which("ffmpeg"):
        print("error: no encuentro ffmpeg en el PATH", file=sys.stderr)
        return 1
    archivos = _ts(rutas)
    if not archivos:
        print("no hay archivos .ts en lo que se indicó", file=sys.stderr)
        return 0
    fallos = sum(1 for ts in archivos if convertir_uno(ts) is None)
    print(f"\n{len(archivos) - fallos} de {len(archivos)} .ts convertidos", file=sys.stderr, flush=True)
    return fallos


def con_mp4(rutas: List[Path]) -> List[Path]:
    """``rutas`` with every .ts file given replaced by its .mp4 once converted
    (folders stay: the .mp4 sorts before the .ts and wins over it)."""
    return [r.with_suffix(".mp4") if r.suffix.lower() == ".ts" and r.with_suffix(".mp4").exists() else r
            for r in rutas]

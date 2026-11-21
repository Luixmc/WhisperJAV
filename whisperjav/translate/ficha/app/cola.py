"""The app's queue: one work at a time, each one a ``ficha`` command line.

Every video is its own job, so each card shows where its work is (convert,
transcribe, notes, translate) from the lines the command prints. A finished
translation is moved into the library. The queue only talks through
``eventos`` (a thread-safe queue), so the window reads it at its own pace.
"""

import itertools
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from whisperjav.translate.ficha.__main__ import VIDEOS
from whisperjav.translate.ficha.app import biblioteca

ACCIONES = {"ambos": "Convertir y traducir", "traducir": "Traducir", "convertir": "Convertir"}
ETAPAS = ("convertir", "transcribir", "ficha", "traducir")
NOMBRE_ETAPA = {"convertir": "Convertir", "transcribir": "Transcribir", "ficha": "Ficha", "traducir": "Traducir"}
_ids = itertools.count(1)


def etapa_de(linea: str) -> Optional[str]:
    """The stage a line of ``ficha traducir`` announces, if any."""
    l = linea.strip()
    if l.startswith("convirtiendo "):
        return "convertir"
    if l.startswith("transcribiendo "):
        return "transcribir"
    if l.startswith(("r18.dev ", "JavDB ")) or "estudi" in l.lower()[:40]:
        return "ficha"
    if "WHISPERJAV TRANSLATE" in l:
        return "traducir"
    return None


def videos_en(ruta: Path) -> List[Path]:
    """The videos (and Japanese .srt) a dropped path stands for, one job each."""
    if ruta.is_dir():
        return sorted(f for f in ruta.iterdir() if f.is_file() and f.suffix.lower() in VIDEOS)
    if ruta.suffix.lower() in VIDEOS or ruta.suffix.lower() == ".srt":
        return [ruta]
    return []


def video_final(ruta: Path) -> Optional[Path]:
    """The video a job leaves behind: the .mp4 of a .ts, the video of an .srt."""
    from whisperjav.translate.ficha.obra import nombre_video

    if ruta.suffix.lower() == ".srt":
        nombre = nombre_video(ruta)
        for ext in (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v", ".webm", ".flv"):
            v = ruta.with_name(nombre + ext)
            if v.is_file():
                return v
        return None
    if ruta.suffix.lower() == ".ts":
        mp4 = ruta.with_suffix(".mp4")
        return mp4 if mp4.is_file() else None
    return ruta if ruta.is_file() else None


@dataclass
class Trabajo:
    ruta: Path
    accion: str
    opciones: Dict
    id: int = field(default_factory=lambda: next(_ids))
    estado: str = "espera"  # espera, trabajando, listo, error
    etapa: Optional[str] = None
    hechas: List[str] = field(default_factory=list)
    registro: List[str] = field(default_factory=list)
    destino: Optional[Path] = None
    mensaje: str = ""

    @property
    def nombre(self) -> str:
        return biblioteca.codigo_de(self.ruta.name) or self.ruta.stem

    @property
    def etapas(self) -> List[str]:
        if self.accion == "convertir":
            return ["convertir"]
        lista = list(ETAPAS)
        if self.accion == "traducir" or self.ruta.suffix.lower() != ".ts":
            lista.remove("convertir")
        if self.ruta.suffix.lower() == ".srt":
            lista.remove("transcribir")
        return lista


def _python() -> str:
    console = Path(sys.executable).with_name("python.exe")
    return str(console) if console.exists() else sys.executable


class Cola:
    def __init__(self, raiz: Path = biblioteca.RAIZ):
        self.raiz = raiz
        self.pendientes: List[Trabajo] = []
        self.actual: Optional[Trabajo] = None
        self.pausada = False
        self._proceso: Optional[subprocess.Popen] = None
        self._detenido = False
        self._lock = threading.Lock()
        self.eventos: "queue.Queue[tuple]" = queue.Queue()

    # -- managing -----------------------------------------------------------

    def agregar(self, trabajos: List[Trabajo]) -> None:
        with self._lock:
            self.pendientes.extend(trabajos)
        for t in trabajos:
            self.eventos.put(("cambio", t))
        self._seguir()

    def quitar(self, trabajo_id: int) -> None:
        with self._lock:
            self.pendientes = [t for t in self.pendientes if t.id != trabajo_id]
        self.eventos.put(("quitado", trabajo_id))

    def detener(self) -> None:
        """Stop the job in course and pause; Reanudar starts it again."""
        self.pausada = True
        proceso = self._proceso
        if not proceso:
            return
        self._detenido = True
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proceso.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proceso.terminate()

    def reanudar(self) -> None:
        self.pausada = False
        self._seguir()

    @property
    def ocupada(self) -> bool:
        return self.actual is not None

    # -- running ------------------------------------------------------------

    def _seguir(self) -> None:
        with self._lock:
            if self.actual or self.pausada or not self.pendientes:
                return
            self.actual = self.pendientes.pop(0)
        threading.Thread(target=self._correr, args=(self.actual,), daemon=True).start()

    def comando(self, t: Trabajo) -> List[str]:
        base = [_python(), "-m", "whisperjav.translate.ficha"]
        if t.accion == "convertir":
            return base + ["convertir", str(t.ruta)]
        o = t.opciones
        cmd = base + ["traducir", str(t.ruta), "--sin-mover", "--tono", o["tono"], "--lote", str(o["lote"])]
        if t.accion == "ambos":
            cmd.append("--convertir")
        if o.get("modelo"):
            cmd += ["--modelo", o["modelo"]]
        if not o.get("estudiar", True):
            cmd.append("--sin-estudio")
        if o.get("rehacer"):
            cmd.append("--rehacer")
        # Keep the model loaded while another translation waits.
        otra = any(p.accion != "convertir" for p in self.pendientes)
        if otra or not o.get("descargar", True):
            cmd.append("--mantener-modelo")
        if not o.get("javdb", True):
            cmd.append("--sin-javdb")
        return cmd

    def _linea(self, t: Trabajo, linea: str) -> None:
        t.registro.append(linea)
        self.eventos.put(("linea", t, linea))
        etapa = etapa_de(linea)
        if etapa and etapa in t.etapas and etapa != t.etapa:
            if t.etapa and t.etapa not in t.hechas:
                t.hechas.append(t.etapa)
            # Stages passed over (already transcribed, notes already there) count as done.
            for e in t.etapas[: t.etapas.index(etapa)]:
                if e not in t.hechas:
                    t.hechas.append(e)
            t.etapa = etapa
            self.eventos.put(("cambio", t))

    def _correr(self, t: Trabajo) -> None:
        t.estado, t.etapa, t.hechas, t.mensaje = "trabajando", None, [], ""
        self._detenido = False
        self.eventos.put(("cambio", t))
        cmd = self.comando(t)
        self._linea(t, "> " + " ".join(cmd[2:]) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONUTF8="1")
        try:
            self._proceso = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for linea in self._proceso.stdout:
                self._linea(t, linea)
            codigo = self._proceso.wait()
        except OSError as error:
            self._linea(t, f"error: {error}\n")
            codigo = 1
        self._proceso = None

        if self._detenido:
            t.estado, t.etapa, t.mensaje = "espera", None, "Detenido; se repite al reanudar"
            with self._lock:
                self.pendientes.insert(0, t)
        elif codigo != 0:
            t.estado = "error"
            errores = [l.strip() for l in t.registro if l.strip().lower().startswith("error")]
            t.mensaje = errores[-1] if errores else f"terminó con código {codigo}"
        else:
            self._terminar(t)
        with self._lock:
            self.actual = None
        self.eventos.put(("cambio", t))
        self._seguir()
        if not self.actual and not self.pendientes:
            self.eventos.put(("fin", self._detenido))

    def _terminar(self, t: Trabajo) -> None:
        t.hechas = list(t.etapas)
        t.etapa = None
        if t.accion == "convertir":
            t.estado, t.mensaje = "listo", "Convertido"
            return
        video = video_final(t.ruta)
        if not video or not biblioteca.subtitulo_traducido(video):
            t.estado = "error"
            t.mensaje = "No encuentro el video traducido para pasarlo a la biblioteca"
            return
        try:
            self._linea(t, "pasando a la biblioteca y buscando la carátula...\n")
            t.destino = biblioteca.ingresar(video, self.raiz)
        except OSError as error:
            t.estado, t.mensaje = "error", f"Traducido, pero no se pudo mover: {error}"
            return
        t.estado, t.mensaje = "listo", f"En la biblioteca: {t.destino}"
        self.eventos.put(("biblioteca", t))

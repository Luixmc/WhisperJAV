"""The library of translated works: ``D:/J/<PREFIJO>/<CÓDIGO>/``.

Each work's folder holds the video named after its code, its subtitles, its
``.subtrans`` and ``.ficha`` folder, a ``folder.jpg`` cover and an
``obra.json`` with what r18.dev (or JavDB) says about it. Only works with a
Spanish translation enter: the app moves a work here once its translation
is done. Nothing here knows about windows, so another front end (a view for
the phone, later) can read the same library.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from whisperjav.translate.ficha.__main__ import VIDEOS

RAIZ = Path(os.environ.get("WHISPERJAV_BIBLIOTECA", r"D:\J"))
SIN_CODIGO = "Por identificar"
META = "obra.json"
PORTADA = "folder.jpg"
# A DMM cover is back + spine + front; the front is the right part, this wide
# for each pixel of height.
FRENTE = 0.703
TRADUCIDOS = ("spanish", "es", "español", "espanol")
_KANA = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def codigo_de(nombre: str) -> Optional[str]:
    from whisperjav.translate.ficha import javdb

    return javdb.codigo(nombre)


def carpeta_de(raiz: Path, codigo: Optional[str], nombre: str) -> Path:
    """Where a work goes: ``raiz/PIYO/PIYO-240`` or ``raiz/Por identificar/<nombre>``."""
    if not codigo:
        return raiz / SIN_CODIGO / nombre
    return raiz / codigo.split("-")[0] / codigo


def es_japones(srt: Path) -> bool:
    """True when the subtitle's text is mostly Japanese."""
    try:
        texto = srt.read_text(encoding="utf-8-sig", errors="replace")[:4000]
    except OSError:
        return False
    letras = [c for c in texto if c.isalpha()]
    return bool(letras) and len(_KANA.findall(texto)) > len(letras) * 0.3


def subtitulo_traducido(video: Path) -> Optional[Path]:
    """The Spanish subtitle next to ``video``, if there is one."""
    for sufijo in TRADUCIDOS:
        srt = video.with_name(f"{video.stem}.{sufijo}.srt")
        if srt.is_file():
            return srt
    srt = video.with_suffix(".srt")
    if srt.is_file() and not es_japones(srt):
        return srt
    return None


def _video_en(carpeta: Path) -> Optional[Path]:
    videos = sorted(f for f in carpeta.iterdir() if f.is_file() and f.suffix.lower() in VIDEOS
                    and f.suffix.lower() != ".ts")
    if not videos:
        return None
    # The video named after the folder first, else the largest.
    for v in videos:
        if v.stem == carpeta.name:
            return v
    return max(videos, key=lambda v: v.stat().st_size)


@dataclass
class Entrada:
    carpeta: Path
    video: Path
    subtitulo: Path
    meta: Dict = field(default_factory=dict)

    @property
    def codigo(self) -> str:
        return self.meta.get("codigo") or self.carpeta.name

    @property
    def titulo(self) -> str:
        return self.meta.get("titulo") or self.meta.get("titulo_en") or ""

    @property
    def portada(self) -> Optional[Path]:
        p = self.carpeta / PORTADA
        return p if p.is_file() else None

    def texto_busqueda(self) -> str:
        m = self.meta
        partes = [self.codigo, self.titulo, m.get("titulo_en", ""), m.get("serie", ""),
                  m.get("estudio", ""), " ".join(m.get("actrices", [])), " ".join(m.get("etiquetas", []))]
        return " ".join(partes).lower()


def leer_meta(carpeta: Path) -> Dict:
    try:
        return json.loads((carpeta / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def listar(raiz: Path = RAIZ) -> List[Entrada]:
    """Every translated work in the library, newest first."""
    entradas: List[Entrada] = []
    if not raiz.is_dir():
        return entradas
    for prefijo in raiz.iterdir():
        if not prefijo.is_dir():
            continue
        for carpeta in prefijo.iterdir():
            if not carpeta.is_dir() or carpeta.name.endswith(".ficha"):
                continue
            video = _video_en(carpeta)
            sub = subtitulo_traducido(video) if video else None
            if video and sub:
                entradas.append(Entrada(carpeta, video, sub, leer_meta(carpeta)))
    entradas.sort(key=lambda e: e.subtitulo.stat().st_mtime, reverse=True)
    return entradas


# -- entering a work -----------------------------------------------------------


def _renombrado(nombre: str, viejo: str, nuevo: str) -> str:
    return nuevo + nombre[len(viejo):] if viejo != nuevo and nombre.startswith(viejo + ".") else nombre


def _mover(src: Path, dst: Path) -> None:
    if src.is_dir() and dst.is_dir():
        for hijo in list(src.iterdir()):
            _mover(hijo, dst / hijo.name)
        src.rmdir()
        return
    if dst.is_file():
        dst.unlink()
    shutil.move(str(src), str(dst))


def ingresar(video: Path, raiz: Path = RAIZ, datos: bool = True) -> Path:
    """Move ``video`` and everything named after it (subtitles, ``.subtrans``,
    ``.ficha``; the .ts it came from is left where it is) into the library,
    renamed after its code; then fetch its data and cover. The work's folder.
    """
    codigo = codigo_de(video.name)
    nombre = codigo or video.stem
    destino = carpeta_de(raiz, codigo, video.stem)
    destino.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    for item in list(video.parent.iterdir()):
        es_el_video = item == video
        suyo = item.name.startswith(f"{stem}.") and item.suffix.lower() not in VIDEOS
        nuevo = destino / _renombrado(item.name, stem, nombre)
        # A work translated again in the library is already in place.
        if (es_el_video or suyo) and nuevo.resolve() != item.resolve():
            _mover(item, nuevo)
    if datos:
        completar(destino)
    return destino


def completar(carpeta: Path, forzar: bool = False) -> Dict:
    """Write ``obra.json`` and ``folder.jpg`` for a work that lacks them.
    Failures are printed, never raised: a work without data still plays."""
    meta = leer_meta(carpeta)
    codigo = codigo_de(carpeta.name)
    if codigo and (forzar or not meta):
        encontrada = _consultar(codigo)
        if encontrada:
            meta = {
                "codigo": codigo, "titulo": encontrada.titulo, "titulo_en": encontrada.titulo_en,
                "actrices": encontrada.actrices, "serie": encontrada.serie, "estudio": encontrada.estudio,
                "fecha": encontrada.fecha, "etiquetas": encontrada.etiquetas, "fuente": encontrada.fuente,
                "portada_url": encontrada.portada,
            }
            (carpeta / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if forzar or not (carpeta / PORTADA).is_file():
        hecha = bool(meta.get("portada_url")) and portada_de_url(meta["portada_url"], carpeta / PORTADA)
        if not hecha:
            video = _video_en(carpeta)
            if video:
                fotograma(video, carpeta / PORTADA)
    return meta


def _consultar(codigo: str):
    from whisperjav.translate.ficha import javdb, r18

    for nombre, sitio in (("r18.dev", r18), ("JavDB", javdb)):
        try:
            obra = sitio.consultar(codigo)
        except javdb.JavDBError as error:
            print(f"aviso: {nombre}: {error}", file=sys.stderr)
            continue
        if obra:
            return obra
    return None


def recortar_frente(imagen):
    """The front of a DMM cover (back + spine + front), or the picture as it is
    when it is already upright."""
    ancho, alto = imagen.size
    frente = int(alto * FRENTE)
    if ancho <= frente * 1.2:
        return imagen
    return imagen.crop((ancho - frente, 0, ancho, alto))


def portada_de_url(url: str, destino: Path) -> bool:
    import requests
    from PIL import Image

    from whisperjav.translate.ficha.javdb import AGENTE

    try:
        r = requests.get(url, headers={"User-Agent": AGENTE}, timeout=30)
        if r.status_code != 200:
            return False
        imagen = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as error:  # network or a page that is not a picture
        print(f"aviso: portada: {error}", file=sys.stderr)
        return False
    if imagen.size[1] < 300:  # DMM answers a tiny "now printing" for missing ones
        return False
    recortar_frente(imagen).save(destino, quality=90)
    return True


def fotograma(video: Path, destino: Path) -> bool:
    """A frame from a fifth into the video, as the cover of a work without one."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=60).stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        dur = 0
    with tempfile.TemporaryDirectory() as tmp:
        salida = Path(tmp) / "f.jpg"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(int(dur / 5)), "-i", str(video), "-frames:v", "1",
             "-vf", "scale=-2:540", str(salida)],
            capture_output=True, timeout=120)
        if not salida.is_file():
            return False
        shutil.move(str(salida), str(destino))
    return True

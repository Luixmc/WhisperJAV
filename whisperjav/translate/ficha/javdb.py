"""A work's tags and title from JavDB, to start its notes from.

Like Koharu's ``galeria.rs`` does with e-hentai: the video's code comes from
its file name (``SDMF-018.mp4`` -> ``SDMF-018``), JavDB's search gives the
work's page, and the page gives the title, series, maker, actresses and tags.
The tags are asked for in English (``locale=en``), which is what the aliases in
``etiquetas.py`` know ("Creampie", "Incest", "Married Woman"...); the title
stays Japanese, which is what the study reads.

What is found goes to the work's ``usuario.json`` only when the user has not
written one, so their own tags and description always win. The tags then go
through ``etiquetas.comprobar`` like any others: minors stop the work.

JavDB is not an official API: it may change its pages or put a Cloudflare
check in front of them. Every failure here is a warning, never a stop.
"""

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import requests

BASE = "https://javdb.com"
AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

_CODIGO = re.compile(r"(?<![A-Za-z])([A-Za-z]{2,6})[-_ ]?(\d{2,5})(?!\d)")


class JavDBError(RuntimeError):
    """JavDB could not be read."""


@dataclass
class Obra:
    codigo: str
    url: str
    titulo: str = ""
    fecha: str = ""
    estudio: str = ""
    serie: str = ""
    director: str = ""
    actrices: List[str] = field(default_factory=list)
    etiquetas: List[str] = field(default_factory=list)
    # From r18.dev: the English title (translated by hand) and synopsis.
    titulo_en: str = ""
    sinopsis: str = ""
    fuente: str = "JavDB"

    def descripcion(self) -> str:
        """A short description for ``usuario.json``, in the study's language."""
        parts = [f"Título: {self.titulo}" if self.titulo else ""]
        if self.titulo_en:
            parts.append(f"Título en inglés: {self.titulo_en}")
        if self.sinopsis:
            parts.append(f"Sinopsis: {self.sinopsis}")
        if self.serie:
            parts.append(f"Serie: {self.serie}")
        if self.estudio:
            parts.append(f"Estudio: {self.estudio}")
        if self.actrices:
            parts.append(f"Actrices: {', '.join(self.actrices)}")
        return ". ".join(p for p in parts if p) + (f". ({self.fuente} {self.codigo})" if parts else "")


def codigo(nombre: str) -> Optional[str]:
    """The work's code in a file name: ``SDMF-018.ja.whisperjav.srt`` -> ``SDMF-018``."""
    m = _CODIGO.search(Path(nombre).name)
    return f"{m.group(1).upper()}-{m.group(2)}" if m else None


def _get(path: str, timeout: float) -> str:
    try:
        response = requests.get(
            f"{BASE}{path}", headers={"User-Agent": AGENTE, "Accept-Language": "en"}, timeout=timeout
        )
    except requests.RequestException as error:
        raise JavDBError(f"no se pudo conectar con JavDB: {error}") from error
    if response.status_code != 200:
        raise JavDBError(f"JavDB respondió {response.status_code}")
    if "cf-challenge" in response.text or "Just a moment" in response.text[:2000]:
        raise JavDBError("JavDB pidió una verificación de Cloudflare")
    response.encoding = "utf-8"
    return response.text


def _limpio(fragmento: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragmento)).split())


def buscar(codigo_obra: str, timeout: float = 20) -> Optional[str]:
    """The path of the work's page (``/v/QbGm9``), or None if JavDB lists none."""
    page = _get(f"/search?q={codigo_obra}&f=all&locale=en", timeout)
    for href, titulo in re.findall(
        r'<a href="(/v/[A-Za-z0-9]+)"[^>]*>.*?<div class="video-title"><strong>(.*?)</strong>', page, re.S
    ):
        if _limpio(titulo).upper() == codigo_obra.upper():
            return href
    return None


def leer(pagina: str, codigo_obra: str, url: str) -> Obra:
    """The fields of a work's page (as served with ``locale=en``)."""
    obra = Obra(codigo=codigo_obra, url=url)
    titulo = re.search(r'<strong class="current-title">(.*?)</strong>', pagina, re.S)
    obra.titulo = _limpio(titulo.group(1)) if titulo else ""
    for bloque in re.findall(r'<div class="panel-block[^"]*">(.*?)</div>', pagina, re.S):
        etiqueta = re.search(r"<strong>(.*?)</strong>", bloque, re.S)
        if not etiqueta:
            continue
        nombre = _limpio(etiqueta.group(1)).rstrip(":").lower()
        enlaces = [_limpio(a) for a in re.findall(r"<a [^>]*>(.*?)</a>", bloque, re.S)]
        valor = _limpio(bloque.split("</strong>", 1)[-1])
        if nombre in ("released date", "release date"):
            obra.fecha = valor
        elif nombre == "maker":
            obra.estudio = enlaces[0] if enlaces else valor
        elif nombre == "series":
            obra.serie = enlaces[0] if enlaces else valor
        elif nombre == "director":
            obra.director = enlaces[0] if enlaces else valor
        elif nombre == "tags":
            obra.etiquetas = [e for e in enlaces if e]
        elif nombre in ("actor(s)", "actors", "actress(es)"):
            # Actresses carry class="actor-female"; male actors are plain links.
            obra.actrices = [
                _limpio(a) for a in re.findall(r'<a class="actor-female"[^>]*>(.*?)</a>', bloque, re.S)
            ]
    return obra


def consultar(codigo_obra: str, timeout: float = 20) -> Optional[Obra]:
    """The work on JavDB, or None if it is not listed."""
    ruta = buscar(codigo_obra, timeout)
    if not ruta:
        return None
    return leer(_get(f"{ruta}?locale=en", timeout), codigo_obra, f"{BASE}{ruta}")

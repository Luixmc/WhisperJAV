"""Terminology the translator must honour and slang it must understand.

Ported from Koharu's ``koharu-translator/src/glossary.rs`` and
``koharu-batch/src/revision.rs``. A general-purpose model translates every
batch as if it had never seen the video: it does not know which of several
valid renderings the viewer already read ten minutes ago, nor what a vulgar
word of the original means. These files fix those choices once, and only the
entries that occur in the lines about to be translated reach the model, so a
long list never crowds out the subtitles themselves.

All of them live next to the translate settings (``carpeta()``), are
tab-separated, and ``#`` starts a comment:

- ``glosario.tsv``: ``original<TAB>traducción[<TAB>nota]``, fixed renderings
  for every work. A work's own ``glosario.tsv`` overrides it.
- ``jerga-origen/*.tsv``: ``término<TAB>significado[<TAB>marcas]``, what the
  sexual and vulgar words of the original mean. Not a rendering: the
  translator picks the Spanish word that fits the work's register.
- ``jerga.tsv``: ``palabra<TAB>tipo<TAB>significado``, Spanish words for the
  reviewer: ``latina`` (correct, do not touch), ``españa`` and ``regional``
  (replace by the third column), ``uso`` (a usage rule) and ``error`` (a
  known misspelling, always fixed).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from whisperjav.translate.ficha.etiquetas import carpeta

# At most this many slang entries reach the model per batch.
JERGA_POR_LOTE = 30


@dataclass
class Entrada:
    original: str
    traduccion: str
    nota: str = ""


@dataclass
class Glosario:
    entradas: List[Entrada] = field(default_factory=list)

    @classmethod
    def parse(cls, texto: str) -> "Glosario":
        entradas = []
        for line in texto.splitlines():
            line = line.lstrip("﻿")
            if line.lstrip().startswith("#"):
                continue
            fields = [part.strip() for part in line.split("\t")]
            if len(fields) < 2 or not fields[0] or not fields[1]:
                continue
            entradas.append(Entrada(fields[0], fields[1], fields[2] if len(fields) > 2 else ""))
        return cls(entradas)

    @classmethod
    def cargar(cls, path: Path) -> "Glosario":
        """A missing file is an empty glossary: every work starts without one."""
        try:
            return cls.parse(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return cls()

    def a_tsv(self) -> str:
        return "".join(
            f"{e.original}\t{e.traduccion}\t{e.nota}\n" if e.nota else f"{e.original}\t{e.traduccion}\n"
            for e in self.entradas
        )

    def unir(self, otro: "Glosario") -> None:
        """Adds ``otro``; on a repeated term the later one wins."""
        for entrada in otro.entradas:
            key = entrada.original.lower()
            self.entradas = [e for e in self.entradas if e.original.lower() != key]
            self.entradas.append(entrada)

    def contiene(self, original: str) -> bool:
        key = original.strip().lower()
        return any(e.original.lower() == key for e in self.entradas)

    def relevantes(self, textos: Iterable[str]) -> List[Entrada]:
        """The entries whose term occurs in any of ``textos``."""
        lowered = [texto.lower() for texto in textos]
        return [
            e for e in self.entradas
            if any(ocurre(texto, e.original.lower()) for texto in lowered)
        ]


def _empieza_palabra(texto: str) -> bool:
    return bool(texto) and texto[0].isalnum()


def ocurre(texto: str, termino: str) -> bool:
    """Whether ``termino`` appears in ``texto`` as a word of its own.

    Latin terms must not match inside longer words ("ass" in "class"), but may
    carry an English plural ending ("cocks"). Japanese has no spaces to tell
    words apart, so any occurrence counts.
    """
    if not termino:
        return False
    espaciado = any("a" <= c <= "z" or "A" <= c <= "Z" for c in termino)
    start = texto.find(termino)
    while start != -1:
        if not espaciado:
            return True
        end = start + len(termino)
        rest = texto[end:]
        for plural in ("es", "s"):
            if rest.startswith(plural) and not _empieza_palabra(rest[len(plural):]):
                rest = rest[len(plural):]
                break
        before = texto[start - 1] if start else ""
        if not before.isalnum() and not _empieza_palabra(rest):
            return True
        start = texto.find(termino, start + 1)
    return False


def kana_corto(termino: str) -> bool:
    """One or two kana ("いい", "する") are ordinary words on nearly every line,
    and without spaces their slang readings would mislead more than help."""
    return len(termino) <= 2 and all("぀" <= c <= "ヿ" for c in termino)


def glosario_global() -> Glosario:
    return Glosario.cargar(carpeta() / "glosario.tsv")


def jerga_origen(sin: Optional[Glosario] = None, directorio: Optional[Path] = None) -> Glosario:
    """Every ``jerga-origen/*.tsv``, without the terms ``sin`` already fixes."""
    directorio = directorio or carpeta() / "jerga-origen"
    jerga = Glosario()
    for path in sorted(directorio.glob("*.tsv")) if directorio.is_dir() else []:
        jerga.entradas.extend(
            e for e in Glosario.cargar(path).entradas
            if not kana_corto(e.original) and not (sin and sin.contiene(e.original))
        )
    return jerga


@dataclass
class Palabra:
    """One line of ``jerga.tsv``."""

    palabra: str
    tipo: str
    significado: str


def jerga_destino(path: Optional[Path] = None) -> List[Palabra]:
    path = path or carpeta() / "jerga.tsv"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []
    palabras = []
    for line in lines:
        if line.lstrip().startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0].strip():
            continue
        palabras.append(Palabra(
            fields[0].strip().lower(), fields[1].strip().lower(),
            fields[2].strip() if len(fields) > 2 else "",
        ))
    return palabras


def tiene_palabra(texto: str, palabra: str) -> bool:
    """Whether ``palabra`` appears in ``texto`` as a whole word or phrase."""
    start = texto.find(palabra)
    while start != -1:
        before = texto[start - 1] if start else ""
        after = texto[start + len(palabra):start + len(palabra) + 1]
        if not before.isalnum() and not after.isalnum():
            return True
        start = texto.find(palabra, start + 1)
    return False


# -- blocks for the translator's context ---------------------------------------

def bloque_ficha(ficha: Optional[str]) -> str:
    ficha = (ficha or "").strip()
    if not ficha:
        return ""
    return (
        "FICHA DE LA OBRA\n"
        "Describe los personajes, sus relaciones y el tono. Úsala para saber quién "
        "habla, a quién se dirige y con qué registro; no la traduzcas.\n\n" + ficha
    )


def bloque_glosario(glosario: Glosario, textos: List[str]) -> str:
    terminos = glosario.relevantes(textos)
    if not terminos:
        return ""
    lines = [
        "GLOSARIO OBLIGATORIO",
        "Estos términos aparecen en el texto. Tradúcelos SIEMPRE exactamente así, "
        "ajustando solo género y número:",
    ]
    for e in terminos:
        lines.append(f'- "{e.original}" -> "{e.traduccion}"' + (f" ({e.nota})" if e.nota else ""))
    return "\n".join(lines)


def bloque_jerga(jerga: Glosario, textos: List[str]) -> str:
    terminos = jerga.relevantes(textos)
    if not terminos:
        return ""
    # Longer terms say more ("中出し" over "出し"); keep those when trimming.
    terminos.sort(key=lambda e: len(e.original), reverse=True)
    lines = [
        "JERGA DEL ORIGINAL",
        "Significado de palabras sexuales o vulgares que aparecen en el texto. No es una "
        "traducción obligatoria: úsalo para entender el sentido y elige la palabra según "
        "el registro de la ficha. Si una palabra aparece solo como parte de otra, ignórala.",
    ]
    for e in terminos[:JERGA_POR_LOTE]:
        lines.append(f'- "{e.original}": {e.traduccion}' + (f" ({e.nota})" if e.nota else ""))
    return "\n".join(lines)

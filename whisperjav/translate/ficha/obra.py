"""What is known about one video, kept in a folder beside its subtitles.

Ported from Koharu's ``koharu-batch/src/obra.rs``. Everything about
``SONE-853.mp4`` lives in ``SONE-853.ficha/``, whichever subtitle names it:
``SONE-853.ja.whisperjav.srt`` (the transcription), ``SONE-853.japanese.srt``,
``SONE-853.spanish.srt`` or ``SONE-853.srt`` (the translation):

- ``usuario.json``: tags and a short description the user writes before
  studying (``{"etiquetas": [...], "descripcion": "..."}``).
- ``ficha.md``: characters, relationships and tone, written by ``estudiar``
  and meant to be edited by hand. The translator reads it as it is.
- ``glosario.tsv``: renderings approved for this work; overrides the global one.
- ``propuestas.tsv``: renderings awaiting approval.
- ``rechazados.tsv``: renderings turned down, never proposed again.
- ``correcciones.tsv``: corrections proposed by ``revisar``.

Nothing the machine proposes is applied on its own: the first column of
``propuestas.tsv`` and ``correcciones.tsv`` is the user's decision (``s`` to
approve, ``n`` to reject, empty to decide later) and ``aplicar`` acts on it.
"""

import json
from pathlib import Path
from typing import List, Optional

from whisperjav.translate.ficha import diccionarios, etiquetas
from whisperjav.translate.ficha.diccionarios import Entrada, Glosario
from whisperjav.translate.output_naming import strip_language_suffix

CABECERA_PROPUESTAS = (
    "# Términos propuestos: decisión<TAB>original<TAB>traducción<TAB>nota\n"
    "# Pon s (aprobar) o n (rechazar) en la primera columna; puedes corregir la\n"
    "# traducción antes de aprobar. Luego ejecuta: aplicar\n"
)


def nombre_video(srt: Path) -> str:
    """The video's name from one of its subtitles: ``test.ja.whisperjav.srt``,
    ``test.japanese.srt`` and ``test.srt`` all belong to ``test.mp4``."""
    stem = Path(srt).stem
    if stem.lower().endswith(".whisperjav"):
        stem = stem[: -len(".whisperjav")]
    return strip_language_suffix(stem)


class Obra:
    def __init__(self, srt: Path):
        srt = Path(srt)
        self.nombre = nombre_video(srt)
        self.dir = srt.parent / f"{self.nombre}.ficha"

    def existe(self) -> bool:
        return self.dir.is_dir()

    @property
    def ficha_path(self) -> Path:
        return self.dir / "ficha.md"

    @property
    def glosario_path(self) -> Path:
        return self.dir / "glosario.tsv"

    @property
    def propuestas_path(self) -> Path:
        return self.dir / "propuestas.tsv"

    @property
    def rechazados_path(self) -> Path:
        return self.dir / "rechazados.tsv"

    @property
    def usuario_path(self) -> Path:
        return self.dir / "usuario.json"

    @property
    def correcciones_path(self) -> Path:
        return self.dir / "correcciones.tsv"

    @property
    def correcciones_rechazadas_path(self) -> Path:
        return self.dir / "correcciones-rechazadas.tsv"

    # -- notes ---------------------------------------------------------------

    def ficha(self) -> Optional[str]:
        try:
            text = self.ficha_path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        return text if text.strip() else None

    def escribir_ficha(self, text: str) -> Optional[Path]:
        """Writes the notes; the previous ones are kept as ``ficha.anterior.md``."""
        self.dir.mkdir(parents=True, exist_ok=True)
        backup = None
        previous = self.ficha()
        if previous:
            backup = self.dir / "ficha.anterior.md"
            backup.write_text(previous, encoding="utf-8")
        self.ficha_path.write_text(text, encoding="utf-8")
        return backup

    # -- what the user said --------------------------------------------------

    def usuario(self) -> dict:
        try:
            data = json.loads(self.usuario_path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, ValueError):
            return {}
        tags = data.get("etiquetas") or []
        if isinstance(tags, str):
            tags = etiquetas.partir(tags)
        return {"etiquetas": list(tags), "descripcion": str(data.get("descripcion") or "")}

    def escribir_usuario(self, tags: List[str], descripcion: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.usuario_path.write_text(
            json.dumps({"etiquetas": tags, "descripcion": descripcion}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def lectura(self) -> etiquetas.Lectura:
        return etiquetas.leer(self.usuario().get("etiquetas", []))

    # -- glossary ------------------------------------------------------------

    def glosario(self) -> Glosario:
        """The global glossary overlaid with this work's own."""
        glosario = diccionarios.glosario_global()
        glosario.unir(Glosario.cargar(self.glosario_path))
        return glosario

    def jerga_origen(self) -> Glosario:
        return diccionarios.jerga_origen(sin=self.glosario())

    def propuestas(self) -> List[List[str]]:
        """Pending proposals as [decision, original, traduccion, nota]."""
        return _filas(self.propuestas_path, 4)

    def proponer(self, candidatos: List[Entrada]) -> int:
        """Adds proposals not already approved, pending or rejected."""
        aprobados = self.glosario()
        rechazados = Glosario.cargar(self.rechazados_path)
        filas = self.propuestas()
        pendientes = {fila[1].lower() for fila in filas}
        added = 0
        for e in candidatos:
            key = e.original.lower()
            if aprobados.contiene(e.original) or rechazados.contiene(e.original) or key in pendientes:
                continue
            filas.append(["", e.original, e.traduccion, e.nota])
            pendientes.add(key)
            added += 1
        if added:
            self.escribir_propuestas(filas)
        return added

    def escribir_propuestas(self, filas: List[List[str]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        body = "".join("\t".join(fila) + "\n" for fila in filas)
        self.propuestas_path.write_text(CABECERA_PROPUESTAS + body, encoding="utf-8")

    def aplicar_propuestas(self) -> tuple:
        """Moves ``s`` proposals to the glossary and ``n`` ones to the rejected
        list; returns (approved, rejected)."""
        filas = self.propuestas()
        aprobadas = [f for f in filas if f[0].strip().lower() == "s"]
        rechazadas = [f for f in filas if f[0].strip().lower() == "n"]
        if not aprobadas and not rechazadas:
            return 0, 0
        glosario = Glosario.cargar(self.glosario_path)
        glosario.unir(Glosario([Entrada(f[1], f[2], f[3]) for f in aprobadas if f[1] and f[2]]))
        if aprobadas:
            self.glosario_path.write_text(
                "# Glosario de esta obra: original<TAB>traducción<TAB>nota\n" + glosario.a_tsv(),
                encoding="utf-8",
            )
        if rechazadas:
            with self.rechazados_path.open("a", encoding="utf-8") as out:
                for f in rechazadas:
                    out.write(f"{f[1]}\t{f[2]}\t{f[3]}\n")
        self.escribir_propuestas([f for f in filas if f[0].strip().lower() not in ("s", "n")])
        return len(aprobadas), len(rechazadas)


def limpiar(texto: str) -> str:
    """One TSV field: no tabs, and line breaks shown as `` / ``."""
    return texto.replace("\t", " ").replace("\r", "").replace("\n", " / ").strip()


def _filas(path: Path, width: int) -> List[List[str]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []
    filas = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        filas.append((fields + [""] * width)[:width])
    return filas

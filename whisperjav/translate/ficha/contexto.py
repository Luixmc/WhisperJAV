"""The work's notes and dictionaries, handed to PySubtrans batch by batch.

PySubtrans builds each batch's ``<context>`` from a dict that
``SubtitleTranslator.PreprocessBatch`` returns, and renders its
``description`` and ``terminology`` keys (``TranslationPrompt.default_context_tags``).
``enganchar`` wraps that method on one translator so every batch carries the
notes in ``description`` and, in ``terminology``, only the glossary terms and
slang that occur in that batch's lines.

When translating into Spanish, ``description`` also opens with the style
rules for Latin American Spanish (``defaults/estilo-es.txt``; an
``estilo-es.txt`` next to the translate settings replaces them, and an empty
one turns them off).

Nothing happens for a video without notes and without dictionaries when the
target is not Spanish. The glossary, the notes and the style are Spanish, so
they are only used when translating into Spanish; the slang meanings help any
target.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from whisperjav.translate.ficha import diccionarios, etiquetas
from whisperjav.translate.ficha.diccionarios import Glosario
from whisperjav.translate.ficha.obra import Obra

# cap_batch_size_for_context budgets this many tokens per subtitle line.
TOKENS_POR_LINEA = 500
# Spanish prose runs about three characters per token; count it high.
CARACTERES_POR_TOKEN = 3
# Room for the glossary terms and slang of one batch (up to JERGA_POR_LOTE
# slang lines plus the glossary terms that occur).
TOKENS_TERMINOLOGIA = 1000
# Never shrink a batch below this: fewer lines leave the model without context.
LOTE_MINIMO = 3


def estilo() -> str:
    """The style rules for Spanish: the user's file, else the bundled one."""
    try:
        return (etiquetas.carpeta() / "estilo-es.txt").read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        pass
    try:
        from importlib import resources
        return resources.files("whisperjav.translate.defaults").joinpath(
            "estilo-es.txt").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError):
        return ""


@dataclass
class Contexto:
    ficha: str
    glosario: Glosario
    jerga: Glosario
    estilo: str = ""

    def descripcion(self) -> str:
        blocks = [self.estilo, diccionarios.bloque_ficha(self.ficha)]
        return "\n\n".join(block for block in blocks if block)

    def terminologia(self, textos) -> str:
        blocks = [
            diccionarios.bloque_glosario(self.glosario, textos),
            diccionarios.bloque_jerga(self.jerga, textos),
        ]
        return "\n\n".join(block for block in blocks if block)

    def tokens_reservados(self) -> int:
        """Tokens the notes and terms add to every batch, counted high."""
        return len(self.descripcion()) // CARACTERES_POR_TOKEN + TOKENS_TERMINOLOGIA

    def ajustar_lote(self, max_batch_size: int) -> int:
        """``max_batch_size`` minus the lines the notes and terms displace."""
        lineas = -(-self.tokens_reservados() // TOKENS_POR_LINEA)
        return max(min(LOTE_MINIMO, max_batch_size), max_batch_size - lineas)

    def enganchar(self, translator) -> None:
        original = translator.PreprocessBatch
        descripcion = self.descripcion()

        def preprocess(batch, context=None):
            originals, context = original(batch, context)
            if descripcion:
                context["description"] = descripcion
            terms = self.terminologia([line.text for line in originals if line.text])
            if terms:
                previous = context.get("terminology")
                context["terminology"] = f"{previous}\n\n{terms}" if previous else terms
            return originals, context

        translator.PreprocessBatch = preprocess


def cargar(input_path, target_lang: str) -> Optional[Contexto]:
    """The context for translating ``input_path``, or None when there is none.

    Raises ``etiquetas.EtiquetasQueDetienen`` when the work's tags stop it.
    """
    obra = Obra(Path(input_path))
    etiquetas.comprobar(obra.lectura())
    spanish = (target_lang or "").strip().lower() in ("spanish", "español", "espanol", "es")
    glosario = obra.glosario() if spanish else Glosario()
    ficha = (obra.ficha() or "") if spanish else ""
    reglas = estilo() if spanish else ""
    jerga = diccionarios.jerga_origen(sin=glosario)
    if not (ficha or reglas or glosario.entradas or jerga.entradas):
        return None
    print(
        f"[TRANSLATE]   Ficha de la obra: {obra.ficha_path if ficha else '(ninguna)'}; "
        f"estilo: {'sí' if reglas else 'no'}; glosario: {len(glosario.entradas)} término(s); "
        f"jerga del original: {len(jerga.entradas)}",
        file=sys.stderr,
    )
    if ficha:
        print(f"[TRANSLATE]   La ficha ocupa {len(ficha)} caracteres en cada lote", file=sys.stderr)
    return Contexto(ficha=ficha, glosario=glosario, jerga=jerga, estilo=reglas)

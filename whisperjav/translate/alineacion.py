"""Catching a batch whose translations landed on the wrong lines.

A local model sometimes spreads the translation of one long line over the
next lines' slots: the line count still matches, so PySubtrans' validator is
satisfied, but from there on every line of the batch carries the text of an
earlier one (in a test video, 「会社に行かないと」 came out as "Gracias").

Nothing reads the meaning here, only lengths. Spanish runs about twice as long
as the Japanese it translates (1.0x to 3.8x for 90% of the lines of that
video). Two signs of a shift:

- from some line on, original lengths line up better with the translations a
  few lines further down than with their own (``desplazada``): on that video's
  real translation, split into batches of 20, it caught 46 of 52 simulated
  shifts and raised 1 false alarm in 14 good batches;
- two lines far outside the usual ratio, a long line translated by one word or
  a short one by a paragraph (``lineas_sospechosas``), which also catches a
  shift in the last lines of a batch.

The batch then gets a validation error, and PySubtrans' own recovery
(``retry_on_error``) asks for it again with the retry instructions; a false
alarm costs one request. The retry goes through the same check.
"""

import math
import sys
from typing import Iterable, List, Optional, Tuple

from PySubtrans.SubtitleError import TranslationValidationError

# A line with at least this many Japanese characters...
LARGA = 12
# ...translated into less than this fraction of its length is suspect.
MUY_CORTA = 0.5
# A translation this many times longer than its original is suspect...
MUY_LARGA = 9
# ...when it is at least this long.
LARGA_ES = 30
# Suspect lines in one batch that make it a shift.
SOSPECHOSAS = 2


class DesfaseError(TranslationValidationError):
    """Translations do not match the lines they are on."""


def _largo_ja(texto: str) -> int:
    return len("".join((texto or "").split()))


def _largo_es(texto: str) -> int:
    return len(" ".join((texto or "").split()))


def sospechosa(original: str, traduccion: str) -> bool:
    ja, es = _largo_ja(original), _largo_es(traduccion)
    if not ja or not es:
        return False
    if ja >= LARGA and es < ja * MUY_CORTA:
        return True
    return es >= LARGA_ES and es > ja * MUY_LARGA


def lineas_sospechosas(pares: Iterable[Tuple[int, str, str]]) -> List[int]:
    """Numbers of the (number, original, translation) lines that look shifted."""
    return [number for number, original, traduccion in pares if sospechosa(original, traduccion)]


# Offsets tried when looking for a shift, and how many lines a tail needs.
DESPLAZAMIENTOS = range(1, 6)
COLA_MINIMA = 4
# The shifted pairing must fit this much better than the one on the page.
MEJORA = 0.6


def _desajuste(ja: List[int], es: List[int], centro: float) -> float:
    """Mean distance of each pair's log length ratio from the file's usual one."""
    return sum(abs(math.log(e / j) - centro) for j, e in zip(ja, es)) / len(ja)


def desplazada(pares: List[Tuple[int, str, str]]) -> Optional[int]:
    """The first line from which the translations fit the originals better
    shifted down by a few lines than where they are, or None.

    When a translation drifts, the text on line i + d belongs to line i, so
    from the drift on, original lengths line up with translation lengths d
    lines further down.
    """
    ja = [max(1, _largo_ja(original)) for _, original, _ in pares]
    es = [max(1, _largo_es(traduccion)) for _, _, traduccion in pares]
    if len(pares) < COLA_MINIMA + 1:
        return None
    ratios = sorted(math.log(e / j) for j, e in zip(ja, es))
    centro = ratios[len(ratios) // 2]
    for inicio in range(0, len(pares) - COLA_MINIMA):
        for d in DESPLAZAMIENTOS:
            n = len(pares) - inicio - d
            if n < COLA_MINIMA:
                break
            en_su_sitio = _desajuste(ja[inicio:inicio + n], es[inicio:inicio + n], centro)
            corrida = _desajuste(ja[inicio:inicio + n], es[inicio + d:inicio + d + n], centro)
            if corrida < en_su_sitio * MEJORA:
                return pares[inicio][0]
    return None


def vigilar(translator) -> None:
    """Flag shifted batches so PySubtrans retranslates them."""
    original = translator.ProcessBatchTranslation

    def process(batch, translation, line_numbers):
        original(batch, translation, line_numbers)
        traducidas = {line.number: line.text for line in batch.translated or []}
        pares = [
            (line.number, line.text, traducidas[line.number])
            for line in batch.originals if line.number in traducidas
        ]
        desde = desplazada(pares)
        sospechosas = lineas_sospechosas(pares)
        if desde is None and len(sospechosas) < SOSPECHOSAS:
            return
        donde = f"from line {desde} on" if desde is not None else f"lines {', '.join(map(str, sospechosas))}"
        print(
            f"[TRANSLATE]   Scene {batch.scene} batch {batch.number}: translations look shifted "
            f"({donde}); asking again",
            file=sys.stderr,
        )
        batch.errors.append(DesfaseError(
            f"The translations do not match their lines ({donde}): text that belongs to one line "
            "was put on another. Translate each line on its own number; never move text to another line."
        ))

    translator.ProcessBatchTranslation = process

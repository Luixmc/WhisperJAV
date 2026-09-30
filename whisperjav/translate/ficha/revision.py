"""Proposing corrections instead of applying them.

Ported from Koharu's ``koharu-batch/src/revision.rs``. ``revisar`` shows a
local model the subtitles block by block, original and translation side by
side with the work's notes, glossary and slang, and asks only for real errors:
grammar, the wrong person or subject, a meaning the original does not carry,
an ignored glossary term, words left untranslated, Spanish from Spain. Nothing
is written to the subtitles: the proposals land in ``correcciones.tsv`` for the
user to approve or reject, because in Koharu the rewriting corrector this
replaces cost more good lines than it fixed.

The model first says who speaks each line and to whom, and only then proposes
changes: the wrong person is the most common error left, and it cannot be
judged without deciding who talks. Whisper does not say who speaks, and
Japanese drops the subject, so this matters even more here than in manga.

``aplicar`` then writes the approved corrections into the translated file.
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import pysrt

from whisperjav.translate.ficha import diccionarios, etiquetas
from whisperjav.translate.ficha.estudio import minuto
from whisperjav.translate.ficha.lmstudio import LMStudio
from whisperjav.translate.ficha.obra import Obra, _filas, limpiar

# Lines per request: enough context to tell who talks, few enough to review well.
BLOQUE = 25

PROMPT = """Eres revisor de la traducción al español latino neutro de los subtítulos de un \
video para adultos japonés (JAV).
Recibes la ficha de la obra, su glosario, un diccionario de jerga con las palabras de este \
tramo y un tramo de subtítulos, cada uno con el minuto, el original y la traducción \
actual, en orden. Si un original largo se partió en varios subtítulos, cada parte trae \
el original completo: juzga solo si la traducción de esa parte encaja con él y con las \
partes vecinas. El original lo transcribió automáticamente un reconocedor de voz: \
puede traer palabras mal oídas o frases que nadie dijo; no propongas cambios por eso, \
salvo que la traducción invente algo que el contexto contradice.

PRIMERO, en "hablantes", di para CADA subtítulo quién habla y a quién (nombres de la \
ficha, o "narrador", "varios", "?" si no se sabe). Si no estás seguro de quién habla, \
escribe la duda; si estás seguro, deja "duda" vacío.
DESPUÉS, en "cambios", propón correcciones usando esos hablantes.

Propón un cambio SOLO si el subtítulo tiene alguno de estos errores:
- gramática: ortografía, concordancia de género o número, conjugación, signos mal puestos.
- persona: el sujeto, el objeto o el trato no coinciden con el original o con quién habla \
según la ficha (quién hace qué a quién; tú, usted o ustedes).
- sentido: la traducción dice algo que el original no dice, o se come una parte importante.
- glosario: un término del glosario aparece traducido de otra forma.
- sin traducir: quedaron palabras en japonés o en inglés.
- regionalismo: la traducción usa una palabra que la jerga marca como "españa" o \
"regional"; cámbiala por la que indica.

Para revisar la persona, en cada subtítulo pregúntate quién hace cada acción en el \
original (yo, tú, él, ellos) y si la traducción conjuga el verbo con esa misma persona. En \
japonés el sujeto suele omitirse: dedúcelo por el hablante, la ficha y los subtítulos vecinos.

NO cambies el estilo, los sinónimos, las groserías ni el registro si ya son correctos.
NO cambies palabras correctas por regionalismos (dormitorio, recámara, etc. valen igual).
"jerga_del_original" da el significado de palabras sexuales o vulgares del ORIGINAL; úsalo \
para juzgar el sentido (si la traducción suavizó o cambió una de ellas, es error de \
sentido), no como traducción obligatoria.
Las palabras que la jerga marca como "latina" son correctas: no las corrijas; su \
significado está ahí para que entiendas la frase.
Las marcadas "uso" son reglas (conjunciones, adverbios, cuándo va cada forma): propón un \
cambio SOLO si la traducción incumple la regla; motivo "gramática". Las marcadas "error" \
son faltas conocidas: cámbialas siempre por la forma correcta; motivo "gramática".
NO toques la puntuación si ya es correcta en español (¡¿...?! es correcto).
NO corrijas gemidos, jadeos ni risas.
El cambio debe ser mínimo: conserva la frase y corrige solo lo necesario, que suene natural \
y quepa en un subtítulo.
Si un subtítulo está bien, no lo incluyas. Es normal que un tramo no tenga ningún cambio.

Responde solo el JSON pedido. "motivo" es una de: gramática, persona, sentido, glosario, \
sin traducir, regionalismo; seguida de una explicación de pocas palabras."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "hablantes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "habla": {"type": "string"},
                    "a_quien": {"type": "string"},
                    "duda": {"type": "string"},
                },
                "required": ["n", "habla", "a_quien", "duda"],
                "additionalProperties": False,
            },
        },
        "cambios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "propuesta": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["n", "propuesta", "motivo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["hablantes", "cambios"],
    "additionalProperties": False,
}

CABECERA = (
    "# Correcciones propuestas: decisión<TAB>n<TAB>minuto<TAB>original<TAB>actual<TAB>propuesta<TAB>motivo<TAB>habla\n"
    "# Pon s (aprobar) o n (rechazar) en la primera columna; puedes retocar la propuesta\n"
    "# antes de aprobarla (\" / \" es un salto de línea). Luego ejecuta: aplicar\n"
)

COLUMNAS = 8


def se_desdice(motivo: str) -> bool:
    """A reason that concludes the line was fine after all: the model argued
    itself out of the change but still sent it."""
    motivo = motivo.lower()
    return any(
        phrase in motivo
        for phrase in ("no hay un error", "no hay error", "se puede dejar", "puede quedar", "está bien así")
    )


def emparejar(originales: pysrt.SubRipFile, traducidos: pysrt.SubRipFile) -> List[Tuple]:
    """(original, translated) pairs, each translated line with the original it
    overlaps most in time.

    PySubtrans splits long subtitles before translating them
    (``preprocess_subtitles``), so one original can pair with several
    translated lines, each carrying a part of it.

    Subtitles that last no time at all (CrispASR writes some, start == end)
    overlap nothing; they pair with the unpaired original that starts at the
    same moment.
    """
    pares = []
    usados = set()
    for t in traducidos:
        best, overlap = None, 0
        for o in originales:
            shared = min(o.end.ordinal, t.end.ordinal) - max(o.start.ordinal, t.start.ordinal)
            if shared > overlap:
                best, overlap = o, shared
        if best is None:
            best = next(
                (o for o in originales if o.start.ordinal == t.start.ordinal and id(o) not in usados),
                None,
            )
        if best is not None:
            usados.add(id(best))
            pares.append((best, t))
    return pares


def rechazadas(obra: Obra) -> Set[Tuple[str, str]]:
    """(line number, current translation) pairs the user already turned down."""
    return {(f[0], f[1]) for f in _filas(obra.correcciones_rechazadas_path, 2)}


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def revisar(
    original: Path,
    traducido: Path,
    cliente: LMStudio,
    limite: Optional[int] = None,
    log: Callable[[str], None] = _log,
) -> Path:
    obra = Obra(traducido)
    etiquetas.comprobar(obra.lectura())
    pares = [
        (o, t) for o, t in emparejar(
            pysrt.open(str(original), encoding="utf-8"), pysrt.open(str(traducido), encoding="utf-8")
        )
        if t.text.strip()
    ]
    bloques = [pares[i:i + BLOQUE] for i in range(0, len(pares), BLOQUE)]
    if limite:
        bloques = bloques[:limite]
    ficha = obra.ficha() or ""
    glosario = obra.glosario()
    jerga = diccionarios.jerga_destino()
    jerga_origen = obra.jerga_origen()
    omitir = rechazadas(obra)
    log(f"revisando {sum(len(b) for b in bloques)} subtítulo(s) en {len(bloques)} tramo(s) con {cliente.ensure_ready()}")

    filas: List[List[str]] = []
    for index, bloque in enumerate(bloques, 1):
        originales = [o.text for o, _ in bloque]
        texto = "\n".join(t.text.lower() for _, t in bloque)
        mensaje = {
            "ficha": ficha,
            "glosario": [
                {"original": e.original, "traduccion": e.traduccion}
                for e in glosario.relevantes(originales)
            ],
            "jerga": [
                {"palabra": p.palabra, "tipo": p.tipo, "significado": p.significado}
                for p in jerga if diccionarios.tiene_palabra(texto, p.palabra)
            ],
            "jerga_del_original": [
                {"palabra": e.original, "significado": e.traduccion}
                for e in jerga_origen.relevantes(originales)
            ],
            "subtitulos": [
                {"n": n, "minuto": minuto(o.start), "original": o.text, "traduccion": t.text}
                for n, (o, t) in enumerate(bloque, 1)
            ],
        }
        try:
            respuesta = cliente.chat_json(
                PROMPT, json.dumps(mensaje, ensure_ascii=False), "revision", ESQUEMA,
                temperature=0.2, max_tokens=4096,
            )
        except Exception as error:
            log(f"  tramo {index}/{len(bloques)}: sin respuesta válida, se omite ({error})")
            continue

        hablantes: Dict[int, str] = {}
        for dicho in respuesta.get("hablantes") or []:
            habla = str(dicho.get("habla") or "").strip()
            if not habla:
                continue
            a_quien = str(dicho.get("a_quien") or "").strip()
            duda = str(dicho.get("duda") or "").strip()
            if a_quien and a_quien != "?":
                habla += f" → {a_quien}"
            if duda:
                habla += f" (duda: {duda})"
            hablantes[dicho.get("n")] = habla

        propuestas = 0
        for cambio in respuesta.get("cambios") or []:
            n = cambio.get("n")
            if not isinstance(n, int) or not 1 <= n <= len(bloque):
                continue
            o, t = bloque[n - 1]
            actual = limpiar(t.text)
            propuesta = limpiar(str(cambio.get("propuesta") or ""))
            motivo = limpiar(str(cambio.get("motivo") or ""))
            if not propuesta or propuesta == actual or se_desdice(motivo):
                continue
            if (str(t.index), actual) in omitir:
                continue
            filas.append([
                "", str(t.index), minuto(t.start), limpiar(o.text), actual, propuesta, motivo,
                limpiar(hablantes.get(n, "")),
            ])
            propuestas += 1
        log(f"  tramo {index}/{len(bloques)}: {propuestas} propuesta(s)")

    obra.dir.mkdir(parents=True, exist_ok=True)
    obra.correcciones_path.write_text(
        CABECERA + "".join("\t".join(fila) + "\n" for fila in filas), encoding="utf-8"
    )
    log(f"{len(filas)} propuesta(s) en {obra.correcciones_path}")
    return obra.correcciones_path


def aplicar(traducido: Path, log: Callable[[str], None] = _log) -> Tuple[int, int]:
    """Writes the approved corrections into ``traducido`` and files the rejected
    ones; also moves decided term proposals. Returns (applied, rejected)."""
    obra = Obra(traducido)
    terms_ok, terms_no = obra.aplicar_propuestas()
    if terms_ok or terms_no:
        log(f"términos: {terms_ok} aprobado(s) al glosario, {terms_no} rechazado(s)")

    filas = _filas(obra.correcciones_path, COLUMNAS)
    decididas = [f for f in filas if f[0].strip().lower() in ("s", "n")]
    if not decididas:
        log(f"no hay correcciones marcadas con s o n en {obra.correcciones_path}")
        return 0, 0

    subs = pysrt.open(str(traducido), encoding="utf-8")
    by_index = {str(sub.index): sub for sub in subs}
    aplicadas = rechazos = 0
    pendientes = [f for f in filas if f[0].strip().lower() not in ("s", "n")]
    for f in decididas:
        decision, n, _, _, actual, propuesta = (x.strip() for x in f[:6])
        if decision.lower() == "n":
            with obra.correcciones_rechazadas_path.open("a", encoding="utf-8") as out:
                out.write(f"{n}\t{actual}\n")
            rechazos += 1
            continue
        sub = by_index.get(n)
        if sub is None or limpiar(sub.text) != actual:
            log(f"  el subtítulo {n} ya no dice «{actual}»; se deja pendiente")
            pendientes.append(f)
            continue
        sub.text = propuesta.replace(" / ", "\n")
        aplicadas += 1

    if aplicadas:
        backup = obra.dir / "traduccion-anterior.srt"
        shutil.copyfile(traducido, backup)
        subs.save(str(traducido), encoding="utf-8")
        log(f"{aplicadas} corrección(es) aplicada(s) en {traducido} (copia previa en {backup})")
    obra.correcciones_path.write_text(
        CABECERA + "".join("\t".join(fila) + "\n" for fila in pendientes), encoding="utf-8"
    )
    return aplicadas, rechazos

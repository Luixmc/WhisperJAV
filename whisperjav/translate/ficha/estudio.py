"""Reading a video's subtitles before translating them.

Ported from Koharu's ``koharu-batch/src/estudio.rs``. ``estudiar`` hands a
local model the whole transcript in order and asks it for notes on the work:
who the characters are, how they relate, how each one talks, which words the
sex is translated with, and which terms recur. The notes (``ficha.md``) travel
with every batch the translator and the reviewer see afterwards; the terms
become proposals the user approves or rejects, because a rendering that fitted
one scene is not a rule.
"""

import json
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional

import pysrt

from whisperjav.translate.ficha import etiquetas
from whisperjav.translate.ficha.diccionarios import Entrada
from whisperjav.translate.ficha.lmstudio import LMStudio
from whisperjav.translate.ficha.obra import Obra

# Characters of transcript per request. Japanese costs about a token per
# character, so a part plus the notes so far fits a 16K context.
TRAMO = 2500

# Longer "terms" are sentences, which the glossary must not hold.
TERMINO_MAS_LARGO = 40

PROMPT = """\
Eres un editor que prepara la traducción al español latino de un video para adultos \
japonés (JAV). No traduces: lees los subtítulos originales y escribes una FICHA que el \
traductor consultará en cada tramo.

Los subtítulos los transcribió automáticamente un reconocedor de voz: pueden traer \
palabras mal oídas, frases repetidas que nadie dijo (ご視聴ありがとうございました, \
créditos, música) y gemidos transcritos como texto. Ignora ese ruido y deduce lo que \
realmente se dice por el contexto. No sabes quién dice cada línea: dedúcelo por el \
contenido, el trato y el género gramatical.

Recibes la ficha que llevas hasta ahora y un tramo nuevo de los subtítulos, en orden \
y con el minuto en que se dicen. Devuelve la ficha COMPLETA actualizada: conserva lo \
que ya sabías, corrígelo si el tramo nuevo lo contradice y añade lo nuevo.

- obra: género, tono (cómico, romántico, sucio, dramático...) y un resumen breve de \
lo que pasa hasta ahora. En género nombra la dinámica real de la historia \
(infidelidad, netorare, dominación, chantaje, incesto, romance, entrevista, \
programa...), no la etiqueta más amable: si hay infidelidad o degradación, dilo.
- consentimiento: no lo supongas. Si durante el sexo alguien dice que no, pide que \
pare o se resiste (やめて, やだ, いや, だめ, 痛い, 無理), dilo en el resumen y en \
habla_sexo con el minuto, aunque después ceda: no lo describas como consentido ni \
como romance. Si el título o la descripción del usuario dicen por qué ocurre (celos, \
chantaje, venganza), ponlo en el resumen. No suavices ni moralices: describe lo que \
pasa para que el traductor mantenga el tono.
- registro_sexual: cómo es el sexo en esta obra y con qué palabras se traduce. Di qué \
tan crudo es el texto y elige UNA palabra de español latino neutro para cada cosa que \
aparezca (pene, vagina, pechos, trasero, semen, coger, correrse/venirse, mamada...), \
según cómo hablen los personajes: si el original es vulgar, palabras vulgares (verga, \
concha, tetas, culo, leche, coger); si es suave, suaves. Sin eufemismos que el original \
no tenga. El traductor usará siempre esas palabras.
- personajes: nombre tal como aparece (o un nombre descriptivo si no se dice: "el \
marido", "el entrevistador"), quién es, sus relaciones con los demás (quién es \
esposa, madre, jefe de quién) y cómo habla (formal, vulgar, tímido, cómo llama a los \
demás). Esto decide el registro y el género gramatical de la traducción, así que sé \
concreto. En habla_sexo, cómo habla durante el sexo (gime, insulta, suplica, ordena, \
se vuelve sumisa) y, si cambia a lo largo del video, desde qué minuto; vacío si no \
tiene escenas.
- terminos: palabras o expresiones que se repiten y deben traducirse siempre igual: \
nombres propios, apodos, honoríficos (奥さん, 先輩, お兄ちゃん), formas de llamarse, \
jerga sexual recurrente y objetos importantes. En original pon SOLO el texto japonés, \
sin lectura en romaji. Propón la traducción al español latino neutro y una nota breve. \
No incluyas frases enteras ni palabras comunes que no necesitan regla.

- comparacion_usuario: si recibes datos_del_usuario (etiquetas y una descripción que \
escribió quien conoce la obra), úsalos como punto de partida: suelen ser fiables en \
género, tono y relaciones. guia_etiquetas explica qué implica cada etiqueta para el \
diálogo: aplícalo en tono, registro_sexual y habla_sexo. Compáralos con lo que \
muestran los subtítulos y escribe aquí, en pocas líneas, en qué coinciden, qué les \
falta y qué contradice el texto (cita el minuto). No cambies los datos del usuario; si \
el texto los contradice, la ficha sigue al texto y lo dices aquí. Sin datos del \
usuario, déjalo vacío.

Escribe todo en español."""

_TERMINOS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "original": {"type": "string"},
            "traduccion": {"type": "string"},
            "nota": {"type": "string"},
        },
        "required": ["original", "traduccion", "nota"],
        "additionalProperties": False,
    },
}

ESQUEMA = {
    "type": "object",
    "properties": {
        "obra": {
            "type": "object",
            "properties": {
                "genero": {"type": "string"},
                "tono": {"type": "string"},
                "resumen": {"type": "string"},
                "registro_sexual": {"type": "string"},
            },
            "required": ["genero", "tono", "resumen", "registro_sexual"],
            "additionalProperties": False,
        },
        "personajes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "relaciones": {"type": "string"},
                    "habla": {"type": "string"},
                    "habla_sexo": {"type": "string"},
                },
                "required": ["nombre", "descripcion", "relaciones", "habla", "habla_sexo"],
                "additionalProperties": False,
            },
        },
        "terminos": _TERMINOS,
        "comparacion_usuario": {"type": "string"},
    },
    "required": ["obra", "personajes", "terminos", "comparacion_usuario"],
    "additionalProperties": False,
}


def minuto(time: pysrt.SubRipTime) -> str:
    return f"{time.hours * 60 + time.minutes:02d}:{time.seconds:02d}"


def tramos(subs: pysrt.SubRipFile, largo: int = TRAMO) -> List[str]:
    """The transcript as ``[mm:ss] line`` parts of at most ``largo`` characters."""
    parts = [""]
    for sub in subs:
        text = " ".join(sub.text.split())
        # A lone "。" (CrispASR writes one every ~28 s through silence) says nothing.
        if not re.search(r"\w", text):
            continue
        line = f"[{minuto(sub.start)}] {text}\n"
        if parts[-1] and len(parts[-1]) + len(line) > largo:
            parts.append("")
        parts[-1] += line
    return [part for part in parts if part]


def _s(value) -> str:
    return str(value or "").strip()


def render(nombre: str, notas: dict, usuario: dict) -> str:
    obra = notas.get("obra") or {}
    text = (
        f"# Ficha: {nombre}\n\n"
        "<!-- Generada por estudiar. Edítala a mano: el traductor y el revisor la leen tal cual. -->\n\n"
        f"Género: {_s(obra.get('genero'))}\n"
        f"Tono: {_s(obra.get('tono'))}\n"
        f"Resumen: {_s(obra.get('resumen'))}\n"
    )
    if _s(obra.get("registro_sexual")):
        text += f"Registro sexual: {_s(obra.get('registro_sexual'))}\n"
    text += "\n## Personajes\n"
    for p in notas.get("personajes") or []:
        text += (
            f"- {_s(p.get('nombre'))}: {_s(p.get('descripcion'))} "
            f"Relaciones: {_s(p.get('relaciones'))} Habla: {_s(p.get('habla'))}"
        )
        if _s(p.get("habla_sexo")):
            text += f" En el sexo: {_s(p.get('habla_sexo'))}"
        text += "\n"
    if usuario.get("etiquetas") or _s(usuario.get("descripcion")):
        lectura = etiquetas.leer(usuario.get("etiquetas", []))
        text += "\n## Lo que dice el usuario\n"
        if lectura.etiquetas:
            text += f"Etiquetas: {', '.join(lectura.etiquetas)}\n"
        for guia in lectura.guia:
            text += f"- {guia}\n"
        if _s(usuario.get("descripcion")):
            text += f"Descripción: {_s(usuario.get('descripcion'))}\n"
        if _s(notas.get("comparacion_usuario")):
            text += f"Comparación con el texto: {_s(notas.get('comparacion_usuario'))}\n"
    return text


def separar_lectura(original: str) -> tuple:
    """Models like to write ``奥さん (okusan)``; the glossary is matched against
    the subtitle text, so the romanisation leaves the term for the note."""
    original = original.strip()
    for open_ in ("(", "（"):
        at = original.rfind(open_)
        if at > 0 and original.endswith((")", "）")):
            inner = original[at + 1:-1].strip()
            if inner and inner.isascii():
                return original[:at].strip(), inner
    return original, None


def a_entradas(terminos: List[dict]) -> List[Entrada]:
    entradas = []
    for t in terminos:
        original, lectura = separar_lectura(_s(t.get("original")))
        nota = _s(t.get("nota")).replace("\t", " ").replace("\n", " ")
        if lectura and lectura not in nota:
            nota = f"{lectura}. {nota}".strip()
        traduccion = _s(t.get("traduccion")).replace("\t", " ").replace("\n", " ")
        if original and traduccion and len(original) <= TERMINO_MAS_LARGO and "\t" not in original:
            entradas.append(Entrada(original, traduccion, nota))
    return entradas


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def estudiar(
    srt: Path,
    cliente: LMStudio,
    tags: Optional[List[str]] = None,
    descripcion: Optional[str] = None,
    log: Callable[[str], None] = _log,
) -> Path:
    """Studies ``srt`` (the original subtitles) and writes the work's notes."""
    obra = Obra(srt)
    if tags is not None or descripcion is not None:
        previo = obra.usuario()
        obra.escribir_usuario(
            tags if tags is not None else previo.get("etiquetas", []),
            descripcion if descripcion is not None else previo.get("descripcion", ""),
        )
    usuario = obra.usuario()
    lectura = etiquetas.leer(usuario.get("etiquetas", []))
    etiquetas.comprobar(lectura)

    subs = pysrt.open(str(srt), encoding="utf-8")
    partes = tramos(subs)
    if not partes:
        raise ValueError(f"{srt} no tiene subtítulos")
    log(f"estudiando {len(subs)} subtítulo(s) en {len(partes)} parte(s) con {cliente.ensure_ready()}")
    if usuario.get("etiquetas") or usuario.get("descripcion"):
        log(f"partiendo de {len(usuario.get('etiquetas', []))} etiqueta(s) y la descripción del usuario")

    notas: dict = {"obra": {}, "personajes": [], "terminos": [], "comparacion_usuario": ""}
    for index, parte in enumerate(partes, 1):
        mensaje = {"ficha_actual": notas, "texto": parte}
        if usuario.get("etiquetas") or usuario.get("descripcion"):
            mensaje["datos_del_usuario"] = {
                "etiquetas": lectura.etiquetas,
                "guia_etiquetas": lectura.guia,
                "descripcion": usuario.get("descripcion", ""),
            }
        try:
            notas = cliente.chat_json(
                PROMPT, json.dumps(mensaje, ensure_ascii=False), "ficha", ESQUEMA,
                temperature=0.2, max_tokens=8192,
            )
        except Exception as error:
            log(f"  parte {index}/{len(partes)}: sin respuesta válida, se omite ({error})")
            continue
        log(
            f"  parte {index}/{len(partes)}: {len(notas.get('personajes') or [])} personaje(s), "
            f"{len(notas.get('terminos') or [])} término(s)"
        )

    backup = obra.escribir_ficha(render(obra.nombre, notas, usuario))
    if backup:
        log(f"la ficha anterior quedó en {backup}")
    log(f"ficha escrita en {obra.ficha_path}")
    added = obra.proponer(a_entradas(notas.get("terminos") or []))
    log(f"{added} término(s) nuevo(s) propuesto(s) en {obra.propuestas_path}")
    return obra.ficha_path

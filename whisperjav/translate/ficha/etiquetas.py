"""The user's tags for a work, turned into guidance for the study.

Ported from Koharu's ``koharu-batch/src/etiquetas.rs`` and adapted to JAV:
tags come as FANZA genres in Japanese or as their usual English names, and
the ones that decide tone, register or vocabulary carry a short guide in
Spanish so the model does not have to guess what they imply for dialogue.

Tags that mean minors are involved stop the process. That check is fixed and
not configurable.

Two optional files next to the translate settings (``%APPDATA%/WhisperJAV/
translate`` on Windows) let the user adjust the rest:

- ``etiquetas.tsv`` (tag, tab, guide) adds or overrides guides; a guide of
  ``-`` drops the tag.
- ``etiquetas-que-detienen.txt`` lists, one per line, the tags the user does
  not want to translate: the study stops on them, as it always does on
  minors. ``#`` starts a comment in both files.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from whisperjav.translate.settings import get_settings_path

# Tags that mean the work involves minors: the study and the translation stop.
MENORES = frozenset({
    "loli", "lolicon", "lolita", "shota", "shotacon", "child", "children",
    "underage", "minor", "ロリ", "ロリ系", "ロリコン", "ショタ", "児童", "幼女",
    "小学生", "中学生",
})

# (aliases, guide). The first alias is the name shown in the ficha.
_GUIAS = [
    (("人妻", "married woman", "wife", "人妻・主婦", "主婦", "housewife"),
     "mujer casada: habla adulta; el marido y la familia pesan en la culpa y en las excusas"),
    (("熟女", "mature woman", "milf", "mature"),
     "mujer madura: habla adulta, a veces maternal o autoritaria; los jóvenes pueden tratarla de usted al inicio"),
    (("近親相姦", "incest"),
     "incesto: los títulos familiares (mamá, hijo, hermana) se mantienen siempre, también en el sexo"),
    (("義母", "stepmother", "義姉", "義妹", "義父", "in-laws"),
     "familia política o adoptiva: mantener el título familiar (suegra, madrastra, cuñada) en todo el diálogo"),
    (("寝取り・寝取られ・NTR", "ntr", "netorare", "cuckold", "寝取られ"),
     "infidelidad desde el engañado: humillación, celos, culpa; el amante habla con burla y superioridad"),
    (("不倫", "adultery", "cheating"),
     "infidelidad: culpa y excusas al principio, descaro después; no suavizar"),
    (("痴女", "slut", "female initiative"),
     "ella lleva la iniciativa: provoca, ordena y habla sucio sin pudor"),
    (("女王様", "ドS", "femdom", "dominatrix"),
     "ella domina: órdenes cortas, desprecio, apodos humillantes"),
    (("辱め", "humiliation", "insult"),
     "humillación: insultos y apodos degradantes; traducirlos con la misma dureza"),
    (("羞恥", "shame"),
     "vergüenza: frases cortadas, susurros, negaciones que no se cumplen"),
    (("露出", "exhibitionism"),
     "exhibicionismo: miedo a ser descubiertos, susurros, frases cortadas"),
    (("企画", "variety", "variety show", "planning"),
     "formato de programa o concurso: el presentador y el staff usan fórmulas de televisión "
     "(reglas, premios, conteo); los participantes, habla natural"),
    (("ナンパ", "nampa", "pick up"),
     "ligue en la calle: él habla con labia y halagos; ella con desconfianza al principio"),
    (("素人", "amateur"),
     "aficionados: habla natural, con muletillas y dudas; no pulirla"),
    (("ドキュメンタリー", "documentary"),
     "documental: entrevistas con preguntas y respuestas; registro natural"),
    (("主観", "pov"),
     "punto de vista: le hablan a la cámara, que es un hombre; segunda persona en masculino"),
    (("痴漢", "molester", "chikan"),
     "acoso sin consentimiento: silencio forzado, resistencia; no volverlo consentido"),
    (("レイプ", "凌辱", "rape"),
     "sexo no consentido: resistencia, súplicas y amenazas; no suavizar ni volverlo consentido"),
    (("調教", "training", "slave"),
     "adiestramiento sexual: órdenes y sumisión progresiva (\"sí, amo\"); anotar cuándo cambia el habla"),
    (("拘束", "bondage", "restraint"),
     "ataduras: órdenes, forcejeo, súplicas"),
    (("催眠", "hypnosis"),
     "hipnosis: habla plana y obediente de quien está hipnotizado"),
    (("媚薬", "aphrodisiac", "drugs"),
     "afrodisíaco o drogas: habla confusa, jadeos, pierde el control del registro"),
    (("淫語", "dirty talk"),
     "hablan sucio a propósito: vocabulario explícito, sin eufemismos"),
    (("中出し", "creampie", "impregnation"),
     "acabar adentro: usar siempre el mismo verbo (\"venirse adentro\", \"echarla adentro\")"),
    (("フェラ", "blowjob", "fellatio"),
     "sexo oral a él: \"mamada\", \"chupar\"; mantener la misma palabra"),
    (("クンニ", "cunnilingus"),
     "sexo oral a ella: \"comer\", \"lamer\"; mantener la misma palabra"),
    (("パイズリ", "titty fuck", "paizuri"),
     "entre los pechos: usar siempre la misma expresión"),
    (("アナル", "anal"),
     "sexo anal: \"culo\", \"por atrás\"; mantener la misma palabra en toda la obra"),
    (("3P・4P", "threesome", "foursome"),
     "tres o más: distinguir quién habla; órdenes y comentarios entre ellos"),
    (("乱交", "orgy", "gangbang"),
     "varios a la vez: muchas voces cortas; mantener quién es quién por la ficha"),
    (("ハーレム", "harem"),
     "harén: varias mujeres alrededor de un hombre; cada una con su forma de hablar"),
    (("女教師", "teacher", "female teacher"),
     "docente: trato de usted o \"profesora\" por parte de los demás"),
    (("OL", "office lady"),
     "oficina: trato formal con jefes y clientes, más suelto entre colegas"),
    (("看護婦・ナース", "nurse", "ナース"),
     "enfermera: trato de usted con el paciente al inicio"),
    (("ラブラブ", "vanilla", "romance"),
     "sexo consentido y cariñoso: tono afectuoso, sin insultos"),
    (("母", "mother", "母親", "お母さん", "実母"),
     "madre: es \"mamá\" para sus hijos en todo el diálogo, también en el sexo; les habla con "
     "cariño, preocupación o regaño de madre"),
    (("姉・妹", "sister", "姉", "妹", "older sister", "younger sister"),
     "hermanos: él la llama \"hermana\" o \"hermanita\" (o por su nombre, como el original) en todo "
     "el diálogo, también en el sexo; trato de confianza y roces de hermanos"),
    (("親子丼", "mother and daughter", "parent-child bowl"),
     "madre e hija con el mismo hombre: distinguir quién habla; \"mamá\" e \"hija\" se mantienen"),
    (("ニート・引きこもり", "neet", "hikikomori", "shut-in", "ニート", "引きこもり", "引き籠もり"),
     "alguien que no sale de casa ni trabaja: habla hosca, torpe o caprichosa de quien casi no trata "
     "con gente; los demás le hablan con lástima, reproche o condescendencia"),
    (("性処理", "sexual processing", "性処理係", "性欲処理", "sex processing"),
     "ella se ofrece como desahogo sexual (\"yo te ayudo\", \"desahógate conmigo\"): lo presenta como "
     "deber, ayuda o servicio, no como romance; mantener ese tono de obligación"),
    (("オナホ", "onahole", "cocksleeve"),
     "ella se deja usar como juguete sexual: tono de uso y entrega, no de romance"),
    (("鬼畜", "abuse", "brutal"),
     "trato cruel: desprecio, órdenes, amenazas; no suavizarlo"),
    (("ドラマ", "drama"),
     "drama con trama: diálogo de guion, emociones y conflicto; traducirlo natural y con peso"),
]

# Kanji that, right before an alias found in a title, make it another word
# (母 in 義母, 叔母, 祖母).
_NO_ANTES = "義叔伯祖養継"
# Kanji that, right after, do the same (母乳 is breast milk, not a mother).
_NO_DESPUES = "乳"

GUIAS: Dict[str, str] = {}
NOMBRE: Dict[str, str] = {}
for _aliases, _guia in _GUIAS:
    for _alias in _aliases:
        GUIAS[_alias.lower()] = _guia
        NOMBRE[_alias.lower()] = _aliases[0]


class EtiquetasQueDetienen(RuntimeError):
    """The work has tags that stop the study and the translation."""


@dataclass
class Lectura:
    """What the tags say, ready for the study."""

    etiquetas: List[str] = field(default_factory=list)
    guia: List[str] = field(default_factory=list)
    menores: List[str] = field(default_factory=list)
    # Tags from the user's stop list.
    rechazadas: List[str] = field(default_factory=list)


def carpeta() -> Path:
    """Where the user's tag files live: next to the translate settings."""
    return get_settings_path().parent


def guias_del_usuario(path: Optional[Path] = None) -> Dict[str, str]:
    """The user's ``etiquetas.tsv``: tag, tab, guide; ``-`` drops the tag."""
    path = path or carpeta() / "etiquetas.tsv"
    guias: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return guias
    for line in lines:
        if line.lstrip().startswith("#") or "\t" not in line:
            continue
        tag, guia = line.split("\t", 1)
        tag = tag.strip().lower()
        if tag:
            guias[tag] = guia.strip()
    return guias


def detenidas_por_el_usuario(path: Optional[Path] = None) -> List[str]:
    """The user's stop list, one tag per line; ``#`` starts a comment."""
    path = path or carpeta() / "etiquetas-que-detienen.txt"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    return [
        line.strip().lower() for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def leer(tags: Iterable[str]) -> Lectura:
    return leer_con(tags, guias_del_usuario(), detenidas_por_el_usuario())


def leer_con(
    tags: Iterable[str], extra: Mapping[str, str], detenidas: Sequence[str]
) -> Lectura:
    lectura = Lectura()
    for raw in tags:
        tag = (raw or "").strip()
        if not tag:
            continue
        key = tag.lower()
        # "female:loli" style namespaces (e-hentai) are read by their name.
        name = key.split(":", 1)[1].strip() if ":" in key else key
        if key in MENORES or name in MENORES:
            if name not in lectura.menores:
                lectura.menores.append(name)
            continue
        if key in detenidas or name in detenidas:
            if name not in lectura.rechazadas:
                lectura.rechazadas.append(name)
            continue
        guia = extra.get(key) or extra.get(name) or GUIAS.get(key) or GUIAS.get(name)
        if guia == "-":
            continue
        if tag not in lectura.etiquetas:
            lectura.etiquetas.append(tag)
        if guia:
            shown = NOMBRE.get(key) or NOMBRE.get(name) or tag
            line = f"{shown}: {guia}"
            if line not in lectura.guia:
                lectura.guia.append(line)
    return lectura


def comprobar(lectura: Lectura) -> None:
    """Stop the study if the tags say the work must not be translated."""
    if lectura.menores:
        raise EtiquetasQueDetienen(
            f"la obra tiene etiquetas de menores ({', '.join(lectura.menores)}): "
            "no se estudia ni se traduce"
        )
    if lectura.rechazadas:
        raise EtiquetasQueDetienen(
            "la obra tiene etiquetas de tu lista que detienen el estudio "
            f"({', '.join(lectura.rechazadas)}); edita "
            f"{carpeta() / 'etiquetas-que-detienen.txt'} si quieres procesarla"
        )


def del_titulo(titulo: str) -> List[str]:
    """Tags the Japanese title shows but the site's tags miss
    (母は僕専用のオナホになって、性処理を… -> 母, オナホ, 性処理)."""
    found: List[str] = []
    for aliases, _guia in _GUIAS:
        for alias in aliases:
            if alias.isascii():
                continue
            at = titulo.find(alias)
            while at >= 0 and (
                (at > 0 and titulo[at - 1] in _NO_ANTES)
                or titulo[at + len(alias):at + len(alias) + 1] in tuple(_NO_DESPUES)
            ):
                at = titulo.find(alias, at + 1)
            if at >= 0:
                if aliases[0] not in found:
                    found.append(aliases[0])
                break
    return found


def partir(texto: str) -> List[str]:
    """Split a tag list typed by the user ("a, b、c; d")."""
    for sep in ("、", ";", "\n", "，"):
        texto = texto.replace(sep, ",")
    return [part.strip() for part in texto.split(",") if part.strip()]

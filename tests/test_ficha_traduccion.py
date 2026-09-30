"""Dictionaries, work notes, review and the translator hook of translate/ficha."""

import json
from types import SimpleNamespace

import pytest

from whisperjav.translate.ficha import contexto, diccionarios, estudio, etiquetas, revision
from whisperjav.translate.ficha.diccionarios import Entrada, Glosario
from whisperjav.translate.ficha.obra import Obra

SRT_JA = """1
00:00:01,000 --> 00:00:03,000
奥さん、旦那さんには内緒ですよ

2
00:01:05,000 --> 00:01:07,000
中出ししてもいい？

3
00:02:10,000 --> 00:02:12,000
いいよ
"""

SRT_ES = """1
00:00:01,000 --> 00:00:03,000
Señora, esto es un secreto para vuestro marido

2
00:01:05,000 --> 00:01:07,000
¿Puedo acabar adentro?

3
00:02:10,000 --> 00:02:12,000
Sí
"""


@pytest.fixture(autouse=True)
def sin_r18(monkeypatch):
    """No test reaches r18.dev: it has nothing unless a test says otherwise."""
    from whisperjav.translate.ficha import r18
    monkeypatch.setattr(r18, "_json", lambda path, timeout: None)


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Dictionaries in a temporary settings folder."""
    config = tmp_path / "config"
    (config / "jerga-origen").mkdir(parents=True)
    (config / "glosario.tsv").write_text("# global\n奥さん\tseñora\tla esposa\n", encoding="utf-8")
    (config / "jerga-origen" / "ja.tsv").write_text(
        "中出し\teyacular dentro\tvulgar\nいい\tbueno\n", encoding="utf-8"
    )
    (config / "jerga.tsv").write_text("vuestro\tespaña\tsu / de ustedes\n", encoding="utf-8")
    monkeypatch.setattr(etiquetas, "carpeta", lambda: config)
    monkeypatch.setattr(diccionarios, "carpeta", lambda: config)
    return config


@pytest.fixture
def srts(tmp_path):
    ja = tmp_path / "SONE-853.japanese.srt"
    es = tmp_path / "SONE-853.spanish.srt"
    ja.write_text(SRT_JA, encoding="utf-8")
    es.write_text(SRT_ES, encoding="utf-8")
    return ja, es


class FakeClient:
    """Answers each request with the next reply and keeps what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def ensure_ready(self):
        return "modelo-falso"

    def chat_json(self, system, user, schema_name, schema, **kwargs):
        self.sent.append(json.loads(user))
        return self.replies.pop(0)


# -- dictionaries ----------------------------------------------------------------

def test_latin_terms_match_whole_words_and_plurals():
    assert diccionarios.ocurre("nice cocks here", "cock")
    assert not diccionarios.ocurre("first class", "ass")
    assert diccionarios.ocurre("もう中出しして", "中出し")


def test_short_kana_are_left_out_of_the_slang(carpeta):
    jerga = diccionarios.jerga_origen()
    assert [e.original for e in jerga.entradas] == ["中出し"]


def test_a_works_glossary_overrides_the_global_one():
    glosario = Glosario.parse("奥さん\tseñora\n先輩\tsenpai\n")
    glosario.unir(Glosario([Entrada("奥さん", "esposa")]))
    assert [(e.original, e.traduccion) for e in glosario.entradas] == [("先輩", "senpai"), ("奥さん", "esposa")]


def test_only_terms_in_the_batch_reach_the_model():
    glosario = Glosario.parse("奥さん\tseñora\n先輩\tsenpai\n")
    block = diccionarios.bloque_glosario(glosario, ["奥さん、こっち"])
    assert '"奥さん" -> "señora"' in block and "先輩" not in block
    assert diccionarios.bloque_glosario(glosario, ["こんにちは"]) == ""


def test_slang_keeps_the_longest_terms():
    jerga = Glosario([Entrada("出し", "sacar"), Entrada("中出し", "eyacular dentro")])
    block = diccionarios.bloque_jerga(jerga, ["中出し"])
    assert block.index("中出し") < block.index('"出し"')


# -- work folder -----------------------------------------------------------------

def test_original_and_translation_share_the_work_folder(srts):
    ja, es = srts
    assert Obra(ja).dir == Obra(es).dir == ja.parent / "SONE-853.ficha"
    for name in ("SONE-853.ja.whisperjav.srt", "SONE-853.srt"):
        assert Obra(ja.parent / name).dir == ja.parent / "SONE-853.ficha"


def test_term_proposals_are_approved_or_rejected(carpeta, srts):
    obra = Obra(srts[0])
    added = obra.proponer([Entrada("奥さん", "x"), Entrada("先輩", "senpai"), Entrada("旦那", "marido")])
    assert added == 2  # 奥さん is already in the global glossary
    filas = obra.propuestas()
    filas[0][0] = "s"
    filas[1][0] = "n"
    obra.escribir_propuestas(filas)
    assert obra.aplicar_propuestas() == (1, 1)
    assert obra.glosario().contiene("先輩")
    assert obra.propuestas() == []
    assert obra.proponer([Entrada("旦那", "esposo")]) == 0  # rejected before


# -- study -----------------------------------------------------------------------

NOTAS = {
    "obra": {"genero": "infidelidad", "tono": "sucio", "resumen": "r", "registro_sexual": "verga"},
    "personajes": [{"nombre": "Anon", "descripcion": "esposa.", "relaciones": "casada.",
                    "habla": "formal.", "habla_sexo": ""}],
    "terminos": [{"original": "旦那さん (danna-san)", "traduccion": "su marido", "nota": "el esposo"}],
    "comparacion_usuario": "coincide",
}


def test_study_writes_the_notes_and_proposes_terms(carpeta, srts):
    client = FakeClient(NOTAS)
    estudio.estudiar(srts[0], client, tags=["人妻", "NTR"], log=lambda _: None)
    obra = Obra(srts[0])
    ficha = obra.ficha()
    assert "Género: infidelidad" in ficha and "- Anon: esposa." in ficha
    assert "人妻: mujer casada" in ficha
    assert obra.propuestas() == [["", "旦那さん", "su marido", "danna-san. el esposo"]]
    sent = client.sent[0]
    assert "[00:01] 奥さん" in sent["texto"] and sent["datos_del_usuario"]["etiquetas"] == ["人妻", "NTR"]


def test_study_stops_on_minors(carpeta, srts):
    with pytest.raises(etiquetas.EtiquetasQueDetienen):
        estudio.estudiar(srts[0], FakeClient(), tags=["中学生"], log=lambda _: None)


# -- review ----------------------------------------------------------------------

def test_review_proposes_and_apply_writes_only_what_was_approved(carpeta, srts):
    ja, es = srts
    client = FakeClient({
        "hablantes": [{"n": 1, "habla": "el vecino", "a_quien": "Anon", "duda": ""}],
        "cambios": [
            {"n": 1, "propuesta": "Señora, esto es un secreto para su marido", "motivo": "regionalismo: vuestro"},
            {"n": 2, "propuesta": "¿Puedo venirme adentro?", "motivo": "sentido: no hay error grave, se puede dejar"},
            {"n": 3, "propuesta": "Sí, está bien", "motivo": "sentido: falta"},
        ],
    })
    revision.revisar(ja, es, client, log=lambda _: None)
    sent = client.sent[0]
    assert sent["jerga"] == [{"palabra": "vuestro", "tipo": "españa", "significado": "su / de ustedes"}]
    assert sent["jerga_del_original"] == [{"palabra": "中出し", "significado": "eyacular dentro"}]

    obra = Obra(es)
    filas = revision._filas(obra.correcciones_path, revision.COLUMNAS)
    assert [f[1] for f in filas] == ["1", "3"]  # the one that talked itself out is gone
    assert filas[0][7] == "el vecino → Anon"
    filas[0][0], filas[1][0] = "s", "n"
    obra.correcciones_path.write_text(
        revision.CABECERA + "".join("\t".join(f) + "\n" for f in filas), encoding="utf-8"
    )
    assert revision.aplicar(es, log=lambda _: None) == (1, 1)
    text = es.read_text(encoding="utf-8")
    assert "secreto para su marido" in text and "Sí, está bien" not in text
    assert (obra.dir / "traduccion-anterior.srt").exists()
    assert revision.rechazadas(obra) == {("3", "Sí")}


# -- translator hook -------------------------------------------------------------

class FakeTranslator:
    def PreprocessBatch(self, batch, context=None):
        return batch, dict(context or {})


def test_the_translator_gets_notes_and_only_the_batchs_terms(carpeta, srts):
    ja, _ = srts
    Obra(ja).escribir_ficha("# Ficha\nPersonajes: Anon")
    ctx = contexto.cargar(ja, "spanish")
    translator = FakeTranslator()
    ctx.enganchar(translator)
    lines = [SimpleNamespace(text="奥さん、中出しして")]
    _, context = translator.PreprocessBatch(lines, {"terminology": "previa"})
    assert context["description"].startswith("REGLAS DE ESTILO")
    assert "FICHA DE LA OBRA" in context["description"] and "Anon" in context["description"]
    assert context["terminology"].startswith("previa\n\nGLOSARIO OBLIGATORIO")
    assert '"奥さん" -> "señora"' in context["terminology"]
    assert '"中出し": eyacular dentro' in context["terminology"]
    _, context = translator.PreprocessBatch([SimpleNamespace(text="こんにちは")], {})
    assert "terminology" not in context


def test_other_targets_get_only_the_slang(carpeta, srts):
    ja, _ = srts
    Obra(ja).escribir_ficha("# Ficha")
    ctx = contexto.cargar(ja, "english")
    assert ctx.ficha == "" and not ctx.glosario.entradas and ctx.jerga.entradas


def test_nothing_to_add_means_no_hook(tmp_path, monkeypatch, srts):
    monkeypatch.setattr(etiquetas, "carpeta", lambda: tmp_path / "vacia")
    monkeypatch.setattr(diccionarios, "carpeta", lambda: tmp_path / "vacia")
    assert contexto.cargar(srts[0], "english") is None


def test_spanish_always_gets_the_style_rules(tmp_path, monkeypatch, srts):
    monkeypatch.setattr(etiquetas, "carpeta", lambda: tmp_path / "vacia")
    monkeypatch.setattr(diccionarios, "carpeta", lambda: tmp_path / "vacia")
    ctx = contexto.cargar(srts[0], "spanish")
    assert ctx.descripcion().startswith("REGLAS DE ESTILO") and "vosotros" in ctx.descripcion()


def test_the_users_style_file_replaces_or_turns_off_the_rules(carpeta, srts):
    (carpeta / "estilo-es.txt").write_text("MIS REGLAS", encoding="utf-8")
    assert contexto.cargar(srts[0], "spanish").estilo == "MIS REGLAS"
    (carpeta / "estilo-es.txt").write_text("", encoding="utf-8")
    assert contexto.cargar(srts[0], "spanish").estilo == ""


def test_translation_stops_on_minors(carpeta, srts):
    Obra(srts[0]).escribir_usuario(["loli"], "")
    with pytest.raises(etiquetas.EtiquetasQueDetienen, match="menores"):
        contexto.cargar(srts[0], "spanish")


def test_notes_make_room_in_a_local_models_batch():
    ctx = contexto.Contexto(ficha="x" * 3000, glosario=Glosario(), jerga=Glosario())
    # (3000 + header) / 3 + 1000 tokens displace 5 lines of 500.
    assert ctx.ajustar_lote(11) == 6
    assert ctx.ajustar_lote(4) == 3
    assert ctx.ajustar_lote(2) == 2


def test_core_shrinks_the_batch_only_for_local_models():
    import inspect
    from whisperjav.translate import core
    source = inspect.getsource(core.translate_subtitle)
    load = source.index("ficha_contexto.cargar(")
    assert load < source.index("resolve_batch_window(max_batch_size)")
    assert load < source.index("init_project(")
    assert "_ficha.ajustar_lote(max_batch_size)" in source
    assert "_ficha.enganchar(translator)" in source


def test_split_translations_pair_with_the_original_they_overlap():
    import pysrt
    originales = pysrt.from_string(
        "1\n00:00:13,600 --> 00:00:27,120\nながい原文\n\n2\n00:00:27,120 --> 00:00:27,840\nそうなんですよ\n"
    )
    traducidos = pysrt.from_string(
        "1\n00:00:13,600 --> 00:00:14,717\nLa verdad es que...\n\n"
        "2\n00:00:14,767 --> 00:00:21,191\nLes ofrecemos...\n\n"
        "3\n00:00:23,984 --> 00:00:27,120\n¿No es solo la encuesta?\n\n"
        "4\n00:00:27,120 --> 00:00:27,840\nAsí es.\n"
    )
    pares = revision.emparejar(originales, traducidos)
    assert [o.text for o, _ in pares] == ["ながい原文"] * 3 + ["そうなんですよ"]


def test_gemma_4_reasoning_is_turned_off():
    from whisperjav.translate import core
    assert core.should_disable_gemma_reasoning("gemma-4-12b-it-qat")
    assert core.should_disable_gemma_reasoning("gemma4:12b")
    assert not core.should_disable_gemma_reasoning("gemma-3-12b")
    client = SimpleNamespace(_generate_request_body=lambda request, temperature: {"model": "m"})
    translator = SimpleNamespace(client=client)
    assert core.apply_gemma_reasoning_patch(translator, "gemma-4-12b-it-qat")
    assert translator.client._generate_request_body(None, 0.5)["reasoning_effort"] == "none"
    assert not core.apply_gemma_reasoning_patch(SimpleNamespace(client=client), "qwen2.5")


def _split(text, start="00:08:32,890", end="00:09:01,770"):
    from PySubtrans.Options import Options
    from PySubtrans.SubtitleLine import SubtitleLine
    from PySubtrans.SubtitleProcessor import SubtitleProcessor
    processor = SubtitleProcessor(Options({"max_line_duration": 4.0, "min_line_duration": 0.8}))
    return [line.text for line in processor.PreprocessSubtitles([SubtitleLine.Construct(1, start, end, text)])]


def test_long_unpunctuated_japanese_lines_are_split_at_sentence_ends():
    from whisperjav.translate import core
    core.add_japanese_split_points()
    assert not core.add_japanese_split_points()  # only once per process
    # Subtitle 120 of the test video: 30 seconds, no punctuation.
    parts = _split(
        "ああこういう角度がこうなんですねまずお父さんの角度がまず斜めでちょっとお母さんがこう正面なんですね"
        "お母さんの口はちょっと笑ってたり笑ってなかったりしたりお父さんがそのままこれ何秒ぐらいしてるんですか"
        "あもうそのままでいいんでしょうかねえまだまだそんなやりですか何秒ぐらいやってるんですか"
    )
    assert len(parts) >= 6
    assert parts[0] == "ああこういう角度がこうなんですね"
    assert not any(part.startswith(("ね", "父さん", "けれ")) for part in parts)


def test_a_conjunction_keeps_its_sentence():
    from whisperjav.translate import core
    core.add_japanese_split_points()
    parts = _split("謝礼が10万っていうお話なんですけれども条件がございましてアンケートだけじゃないんですか",
                   start="00:00:17,000", end="00:00:27,000")
    assert not any(part.startswith("けれ") for part in parts)


# -- shifted translations --------------------------------------------------------

ALINEADAS = [
    (1, "実はですね、", "La verdad es que..."),
    (2, "このアンケートに答えれば", "Si responden esta encuesta"),
    (3, "謝礼が10万っていうお話なんです", "les damos 100,000 yenes de recompensa"),
    (4, "もう一つ条件がございまして、", "pero hay una condición más."),
    (5, "アンケートだけじゃないんですか？", "¿No es solo la encuesta?"),
    (6, "そうなんですよ。", "Así es."),
    (7, "あのですね、息子さんとお母さんでちょっとエッジなゲームにチャレンジしていただきたいんです。",
     "Verá, quisiéramos que el hijo y la madre aceptaran un reto algo arriesgado."),
    (8, "はい", "Sí"),
    (9, "でもですね、息子さんがお母さんに中出しをするたびに。", "Pero por cada vez que su hijo se venga adentro de usted,"),
    (10, "十万円です。", "son 100,000 yenes."),
    (11, "何、中出しってそれセックスするってことですよね。", "¿Qué? Venirse adentro significa tener sexo, ¿verdad?"),
    (12, "無理です、無理です。", "Es imposible, es imposible."),
]


def test_aligned_translations_pass():
    from whisperjav.translate import alineacion
    assert alineacion.desplazada(ALINEADAS) is None
    assert len(alineacion.lineas_sospechosas(ALINEADAS)) < alineacion.SOSPECHOSAS


def test_translations_shifted_down_are_caught():
    from whisperjav.translate import alineacion
    textos = [es for _, _, es in ALINEADAS]
    corridas = textos[:6] + ["Verá, quisiéramos que el hijo y la madre", "aceptaran un reto algo arriesgado."] + textos[7:11]
    pares = [(n, ja, es) for (n, ja, _), es in zip(ALINEADAS, corridas)]
    assert alineacion.desplazada(pares) is not None


def test_a_shifted_batch_gets_a_validation_error():
    from whisperjav.translate import alineacion

    def line(number, text):
        return SimpleNamespace(number=number, text=text)

    textos = [es for _, _, es in ALINEADAS]
    corridas = textos[:6] + ["Verá, quisiéramos que el hijo y la madre", "aceptaran un reto algo arriesgado."] + textos[7:11]
    batch = SimpleNamespace(
        scene=1, number=1, errors=[],
        originals=[line(n, ja) for n, ja, _ in ALINEADAS],
        translated=[line(n, es) for (n, _, _), es in zip(ALINEADAS, corridas)],
    )
    translator = SimpleNamespace(ProcessBatchTranslation=lambda b, t, n: None)
    alineacion.vigilar(translator)
    translator.ProcessBatchTranslation(batch, None, None)
    assert len(batch.errors) == 1 and isinstance(batch.errors[0], alineacion.DesfaseError)

    batch.errors.clear()
    batch.translated = [line(n, es) for n, _, es in ALINEADAS]
    translator.ProcessBatchTranslation(batch, None, None)
    assert batch.errors == []


def test_batch_translation_picks_originals_and_skips_translations(tmp_path):
    from whisperjav.translate.ficha.__main__ import originales
    for name in ("A.ja.whisperjav.srt", "A.ja.whisperjav.spanish.srt", "B.japanese.srt", "C.srt",
                 "C.english.srt", "notas.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "A.srt").write_text("", encoding="utf-8")  # the translation of A
    for name in ("A.mp4", "D.mkv"):
        (tmp_path / name).write_text("", encoding="utf-8")
    found = [(srt.name, video and video.name)
             for srt, video in originales([tmp_path, tmp_path / "C.srt"], "spanish")]
    # A.mp4 already has its transcription; D.mkv has none and is transcribed first.
    assert found == [("A.ja.whisperjav.srt", None), ("B.japanese.srt", None), ("C.srt", None),
                     ("D.ja.whisperjav.srt", "D.mkv")]


def test_the_translation_is_named_after_the_video(tmp_path):
    from whisperjav.translate.ficha.__main__ import salida_de
    assert salida_de(tmp_path / "test.ja.whisperjav.srt", "spanish") == tmp_path / "test.srt"
    assert salida_de(tmp_path / "B.japanese.srt", "spanish") == tmp_path / "B.srt"
    assert salida_de(tmp_path / "C.srt", "spanish") == tmp_path / "C.spanish.srt"


def test_subtitles_without_words_are_left_out():
    from PySubtrans.Options import Options
    from PySubtrans.SubtitleLine import SubtitleLine
    from PySubtrans.SubtitleProcessor import SubtitleProcessor
    from whisperjav.translate import core
    core.drop_punctuation_only_lines()
    assert not core.drop_punctuation_only_lines()  # only once per process
    lines = [
        SubtitleLine.Construct(1, "00:43:00,000", "00:43:01,000", "。"),
        SubtitleLine.Construct(2, "00:43:02,000", "00:43:03,000", "やめて。"),
        SubtitleLine.Construct(3, "00:43:04,000", "00:43:05,000", "…！"),
        SubtitleLine.Construct(4, "00:43:06,000", "00:43:07,000", "ああ"),
    ]
    processor = SubtitleProcessor(Options({"max_line_duration": 4.0, "min_line_duration": 0.8}))
    assert [line.text for line in processor.PreprocessSubtitles(lines)] == ["やめて。", "ああ"]


def test_zero_length_subtitles_pair_by_start():
    import pysrt
    originales = pysrt.from_string(
        "1\n00:10:33,280 --> 00:10:33,920\nやめ！\n\n2\n00:10:33,920 --> 00:10:33,920\nねえ！\n\n"
        "3\n00:11:24,220 --> 00:11:24,220\nうん。\n\n4\n00:11:24,220 --> 00:11:24,220\n飲む\n"
    )
    traducidos = pysrt.from_string(
        "1\n00:10:33,280 --> 00:10:33,920\n¡Detente!\n\n2\n00:10:33,920 --> 00:10:33,920\n¡Oye!\n\n"
        "3\n00:11:24,220 --> 00:11:24,220\nAjá.\n\n4\n00:11:24,220 --> 00:11:24,220\nBébete...\n"
    )
    assert [(o.text, t.text) for o, t in revision.emparejar(originales, traducidos)] == [
        ("やめ！", "¡Detente!"), ("ねえ！", "¡Oye!"), ("うん。", "Ajá."), ("飲む", "Bébete..."),
    ]


# -- JavDB -----------------------------------------------------------------------

PAGINA_JAVDB = """
<h2 class="title is-4"><strong>SDMF-018 </strong><strong class="current-title">僕は妹と同じ子供部屋で暮らしてます。 </strong></h2>
<nav class="panel movie-panel-info">
<div class="panel-block first-block"><strong>ID:</strong> &nbsp;<span class="value"><a href="/video_codes/SDMF">SDMF</a>-018</span></div>
<div class="panel-block"><strong>Released Date:</strong> &nbsp;<span class="value">2020-12-24</span></div>
<div class="panel-block"><strong>Maker:</strong> &nbsp;<span class="value"><a href="/makers/x">SODクリエイト</a></span></div>
<div class="panel-block"><strong>Series:</strong> &nbsp;<span class="value"><a href="/series/y">桃色かぞく</a></span></div>
<div class="panel-block"><strong>Tags:</strong> &nbsp;<span class="value"><a href="/tags?c1=1">Insult</a>, <a href="/tags?c3=2">Creampie</a></span></div>
<div class="panel-block"><strong>Actor(s):</strong> &nbsp;<span class="value">
<a class="actor-female" href="/actors/90vR">天然かのん</a>, <a href="/actors/p3NEk">井口</a></span></div>
</nav>
"""


def test_javdb_page_is_read():
    from whisperjav.translate.ficha import javdb
    obra = javdb.leer(PAGINA_JAVDB, "SDMF-018", "https://javdb.com/v/QbGm9")
    assert obra.titulo == "僕は妹と同じ子供部屋で暮らしてます。"
    assert (obra.fecha, obra.estudio, obra.serie) == ("2020-12-24", "SODクリエイト", "桃色かぞく")
    assert obra.etiquetas == ["Insult", "Creampie"]
    assert obra.actrices == ["天然かのん"]  # the male actor is left out
    assert "Serie: 桃色かぞく" in obra.descripcion()


def test_the_code_comes_from_the_file_name():
    from whisperjav.translate.ficha import javdb
    assert javdb.codigo("SDMF-018.ja.whisperjav.srt") == "SDMF-018"
    assert javdb.codigo("sone853.mp4") == "SONE-853"
    assert javdb.codigo("test.srt") is None


def test_javdb_fills_usuario_json_but_never_overwrites_the_users(carpeta, tmp_path, monkeypatch):
    from whisperjav.translate.ficha import javdb
    from whisperjav.translate.ficha.__main__ import desde_javdb
    encontrada = javdb.leer(PAGINA_JAVDB, "SDMF-018", "u")
    monkeypatch.setattr(javdb, "consultar", lambda codigo: encontrada)
    srt = tmp_path / "SDMF-018.ja.whisperjav.srt"
    assert desde_javdb(srt)
    # 妹 in the title adds what the site's tags miss.
    assert Obra(srt).usuario()["etiquetas"] == ["Insult", "Creampie", "姉・妹"]
    Obra(srt).escribir_usuario(["人妻"], "mía")
    assert not desde_javdb(srt)
    assert Obra(srt).usuario() == {"etiquetas": ["人妻"], "descripcion": "mía"}


def test_javdb_minors_tags_stop_the_work(carpeta, tmp_path, monkeypatch):
    from whisperjav.translate.ficha import javdb
    from whisperjav.translate.ficha.__main__ import desde_javdb
    encontrada = javdb.Obra(codigo="ABC-001", url="u", etiquetas=["Loli", "Creampie"])
    monkeypatch.setattr(javdb, "consultar", lambda codigo: encontrada)
    srt = tmp_path / "ABC-001.srt"
    desde_javdb(srt)
    with pytest.raises(etiquetas.EtiquetasQueDetienen, match="menores"):
        etiquetas.comprobar(Obra(srt).lectura())


def test_a_javdb_failure_is_only_a_warning(tmp_path, monkeypatch):
    from whisperjav.translate.ficha import javdb
    from whisperjav.translate.ficha.__main__ import desde_javdb

    def falla(codigo):
        raise javdb.JavDBError("JavDB pidió una verificación de Cloudflare")

    monkeypatch.setattr(javdb, "consultar", falla)
    assert not desde_javdb(tmp_path / "SDMF-018.srt")


def test_r18_goes_first_and_keeps_the_japanese_genres(carpeta, tmp_path, monkeypatch):
    from whisperjav.translate.ficha import javdb, r18
    from whisperjav.translate.ficha.__main__ import desde_javdb
    datos = {
        "dvd_id=ATID-454": {"content_id": "atid00454"},
        "combined=atid00454": {
            "title_ja": "母は僕専用のオナホになって、性処理を手伝ってくれます。",
            "title_en": "My Stepmother Has Become My Very Own Glory Hole",
            "maker_name_ja": "アタッカーズ",
            "actresses": [{"name_kanji": "舞原聖"}],
            "categories": [
                {"name_en": "Stepfamily", "name_ja": "近親相姦"},
                {"name_en": "Hi-Def", "name_ja": "ハイビジョン"},
            ],
        },
    }
    monkeypatch.setattr(r18, "_json", lambda path, timeout: datos.get(path))
    monkeypatch.setattr(javdb, "consultar", lambda codigo: pytest.fail("JavDB no hacía falta"))
    srt = tmp_path / "ATID-454.ja.whisperjav.srt"
    assert desde_javdb(srt)
    usuario = Obra(srt).usuario()
    assert usuario["etiquetas"] == ["近親相姦", "母", "性処理", "オナホ"]
    assert "Título en inglés: My Stepmother" in usuario["descripcion"]
    assert "(r18.dev ATID-454)" in usuario["descripcion"]


def test_the_title_gives_tags_but_not_from_other_words():
    assert etiquetas.del_titulo("母は僕専用のオナホになって、性処理を") == ["母", "性処理", "オナホ"]
    assert etiquetas.del_titulo("引きこもりの息子と義母 企画") == ["義母", "企画", "ニート・引きこもり"]
    assert etiquetas.del_titulo("母乳が出る叔母") == []


def test_a_finished_work_goes_to_output_in_its_own_folder(tmp_path):
    from whisperjav.translate.ficha.__main__ import archivar, carpeta_salida
    entrada = tmp_path / "input"
    entrada.mkdir()
    for nombre in ("ATID-454.mp4", "ATID-454.ja.whisperjav.srt", "ATID-454.srt", "ATID-4540.mp4", "run.json"):
        (entrada / nombre).write_text("x")
    (entrada / "ATID-454.ficha").mkdir()
    (entrada / "ATID-454.ficha" / "ficha.md").write_text("x")
    destino = archivar(entrada / "ATID-454.ja.whisperjav.srt", carpeta_salida(entrada, None))
    assert destino == tmp_path / "output" / "ATID-454"
    assert sorted(p.name for p in destino.iterdir()) == [
        "ATID-454.ficha", "ATID-454.ja.whisperjav.srt", "ATID-454.mp4", "ATID-454.srt"]
    assert sorted(p.name for p in entrada.iterdir()) == ["ATID-4540.mp4", "run.json"]
    assert carpeta_salida(tmp_path / "videos", None) == tmp_path / "videos" / "output"


def test_the_batch_shuts_down_only_when_asked_and_after_unloading(monkeypatch):
    from whisperjav.translate.ficha import __main__ as cli
    orden = []
    monkeypatch.setattr(cli, "_traducir", lambda args: orden.append("traducir") or 0)
    monkeypatch.setattr(cli, "descargar", lambda args, motivo: orden.append("descargar"))
    monkeypatch.setattr(cli, "apagar", lambda: orden.append("apagar"))
    cli.traducir(SimpleNamespace(descargar=True, apagar=False))
    assert orden == ["traducir", "descargar"]
    orden.clear()
    cli.traducir(SimpleNamespace(descargar=True, apagar=True))
    assert orden == ["traducir", "descargar", "apagar"]


def test_shutdown_leaves_a_minute_to_cancel(monkeypatch):
    from whisperjav.translate.ficha import __main__ as cli
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda command: ran.append(command))
    monkeypatch.setattr(cli.os, "name", "nt")
    cli.apagar()
    assert ran == [["shutdown", "/s", "/t", "60", "/c", "WhisperJAV terminó el lote de traducción"]]

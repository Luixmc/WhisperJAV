"""The desktop app's logic: queue stages, the library's folders and listing."""

from pathlib import Path

import pytest

from whisperjav.translate.ficha.app import biblioteca
from whisperjav.translate.ficha.app.cola import Cola, Trabajo, etapa_de, video_final, videos_en

JA = "1\n00:00:01,000 --> 00:00:02,000\nお母さん、ちょっと待って\n"
ES = "1\n00:00:01,000 --> 00:00:02,000\nMamá, espera un momento\n"


def _obra(carpeta: Path, nombre: str, traducida: bool = True) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    video = carpeta / f"{nombre}.mp4"
    video.write_bytes(b"video")
    (carpeta / f"{nombre}.ja.whisperjav.srt").write_text(JA, encoding="utf-8")
    if traducida:
        (carpeta / f"{nombre}.srt").write_text(ES, encoding="utf-8")
    (carpeta / f"{nombre}.ficha").mkdir()
    (carpeta / f"{nombre}.ficha" / "ficha.md").write_text("notas", encoding="utf-8")
    return video


@pytest.mark.parametrize("linea, etapa", [
    ("convirtiendo PIYO-240.ts a .mp4...", "convertir"),
    ("transcribiendo PIYO-240.mp4 con CrispASR...", "transcribir"),
    ("r18.dev PIYO-240: タイトル", "ficha"),
    ("  WHISPERJAV TRANSLATE - CLI Configuration", "traducir"),
    ("listo: D:/x/PIYO-240.srt", None),
])
def test_etapa_de(linea, etapa):
    assert etapa_de(linea) == etapa


def test_etapas_segun_archivo(tmp_path):
    assert Trabajo(tmp_path / "A-1.ts", "ambos", {}).etapas == ["convertir", "transcribir", "ficha", "traducir"]
    assert Trabajo(tmp_path / "A-1.mp4", "ambos", {}).etapas == ["transcribir", "ficha", "traducir"]
    assert Trabajo(tmp_path / "A-1.srt", "traducir", {}).etapas == ["ficha", "traducir"]


def test_carpeta_de(tmp_path):
    assert biblioteca.carpeta_de(tmp_path, "PIYO-240", "x") == tmp_path / "PIYO" / "PIYO-240"
    assert biblioteca.carpeta_de(tmp_path, None, "raro") == tmp_path / biblioteca.SIN_CODIGO / "raro"


def test_subtitulo_traducido_ignora_el_japones(tmp_path):
    video = _obra(tmp_path, "ABC-123", traducida=False)
    (tmp_path / "ABC-123.srt").write_text(JA, encoding="utf-8")  # a Japanese .srt with the video's name
    assert biblioteca.subtitulo_traducido(video) is None
    (tmp_path / "ABC-123.spanish.srt").write_text(ES, encoding="utf-8")
    assert biblioteca.subtitulo_traducido(video).name == "ABC-123.spanish.srt"


def test_ingresar_mueve_y_renombra(tmp_path):
    entrada, raiz = tmp_path / "entrada", tmp_path / "J"
    video = _obra(entrada, "piyo-240 hd")
    (entrada / "piyo-240 hd.ts").write_bytes(b"ts")
    destino = biblioteca.ingresar(video, raiz, datos=False)
    assert destino == raiz / "PIYO" / "PIYO-240"
    nombres = sorted(p.name for p in destino.iterdir())
    assert nombres == ["PIYO-240.ficha", "PIYO-240.ja.whisperjav.srt", "PIYO-240.mp4", "PIYO-240.srt"]
    assert (destino / "PIYO-240.ficha" / "ficha.md").read_text(encoding="utf-8") == "notas"
    assert (entrada / "piyo-240 hd.ts").exists()  # the .ts is never touched


def test_ingresar_una_obra_que_ya_esta_no_borra_nada(tmp_path):
    raiz = tmp_path / "J"
    video = _obra(raiz / "ABC" / "ABC-123", "ABC-123")
    destino = biblioteca.ingresar(video, raiz, datos=False)
    assert (destino / "ABC-123.mp4").read_bytes() == b"video"
    assert (destino / "ABC-123.srt").read_text(encoding="utf-8") == ES


def test_listar_solo_traducidas(tmp_path):
    _obra(tmp_path / "ABC" / "ABC-123", "ABC-123")
    _obra(tmp_path / "XYZ" / "XYZ-001", "XYZ-001", traducida=False)
    (tmp_path / "ABC" / "ABC-123" / "obra.json").write_text('{"titulo": "Título"}', encoding="utf-8")
    entradas = biblioteca.listar(tmp_path)
    assert [e.codigo for e in entradas] == ["ABC-123"]
    assert entradas[0].titulo == "Título"
    assert "título" in entradas[0].texto_busqueda()


def test_recortar_frente():
    from PIL import Image

    portada = biblioteca.recortar_frente(Image.new("RGB", (2184, 1470)))
    assert portada.size == (int(1470 * biblioteca.FRENTE), 1470)
    vertical = Image.new("RGB", (400, 560))
    assert biblioteca.recortar_frente(vertical).size == (400, 560)


def test_video_final_y_videos_en(tmp_path):
    ts = tmp_path / "A-1.ts"
    ts.write_bytes(b"")
    assert video_final(ts) is None
    (tmp_path / "A-1.mp4").write_bytes(b"")
    assert video_final(ts) == tmp_path / "A-1.mp4"
    assert video_final(tmp_path / "A-1.ja.whisperjav.srt") == tmp_path / "A-1.mp4"
    assert [p.name for p in videos_en(tmp_path)] == ["A-1.mp4", "A-1.ts"]


def test_comando_mantiene_el_modelo_si_queda_otra(tmp_path):
    cola = Cola(tmp_path)
    o = {"tono": "contextual", "lote": 16, "descargar": True}
    t = Trabajo(tmp_path / "A-1.mp4", "traducir", o)
    assert "--mantener-modelo" not in cola.comando(t)
    assert "--sin-mover" in cola.comando(t)
    cola.pendientes.append(Trabajo(tmp_path / "A-2.mp4", "traducir", o))
    assert "--mantener-modelo" in cola.comando(t)

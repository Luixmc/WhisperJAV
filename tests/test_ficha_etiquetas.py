"""Tags of a work: guides, the user's files and the tags that stop the study."""

import pytest

from whisperjav.translate.ficha import etiquetas
from whisperjav.translate.ficha.etiquetas import (
    EtiquetasQueDetienen,
    comprobar,
    detenidas_por_el_usuario,
    guias_del_usuario,
    leer_con,
)


def test_tags_with_a_guide_and_tags_without_one():
    lectura = leer_con(
        ["Adopted Daughter", "Cuckold", "incest", "Kiss", "schoolgirls", "Solowork"], {}, []
    )
    assert lectura.etiquetas == [
        "Adopted Daughter", "Cuckold", "incest", "Kiss", "schoolgirls", "Solowork"
    ]
    assert [line.split(":")[0] for line in lectura.guia] == ["寝取り・寝取られ・NTR", "近親相姦"]
    assert not lectura.menores and not lectura.rechazadas
    comprobar(lectura)


def test_minors_always_stop_even_with_a_user_guide():
    lectura = leer_con(["人妻", "female:loli", "中学生"], {"loli": "otra cosa"}, [])
    assert lectura.menores == ["loli", "中学生"]
    assert lectura.etiquetas == ["人妻"]
    with pytest.raises(EtiquetasQueDetienen, match="menores"):
        comprobar(lectura)


def test_the_users_stop_list_stops_the_study():
    lectura = leer_con(["incest", "Schoolgirls"], {}, ["schoolgirls"])
    assert lectura.rechazadas == ["schoolgirls"]
    assert lectura.etiquetas == ["incest"]
    with pytest.raises(EtiquetasQueDetienen, match="etiquetas-que-detienen.txt"):
        comprobar(lectura)


def test_the_users_guides_add_override_and_drop():
    extra = {"kiss": "besos largos", "incest": "mi guía", "solowork": "-"}
    lectura = leer_con(["Kiss", "incest", "Solowork"], extra, [])
    assert lectura.etiquetas == ["Kiss", "incest"]
    assert lectura.guia == ["Kiss: besos largos", "近親相姦: mi guía"]


def test_the_users_files_are_read(tmp_path):
    guias = tmp_path / "etiquetas.tsv"
    guias.write_text("# comentario\nKiss\tbesos largos\nsin tabulador\n", encoding="utf-8")
    detenidas = tmp_path / "etiquetas-que-detienen.txt"
    detenidas.write_text("# comentario\nSchoolgirls\n\n制服\n", encoding="utf-8")
    assert guias_del_usuario(guias) == {"kiss": "besos largos"}
    assert detenidas_por_el_usuario(detenidas) == ["schoolgirls", "制服"]


def test_missing_files_change_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(etiquetas, "carpeta", lambda: tmp_path)
    lectura = etiquetas.leer(["incest", "schoolgirls"])
    assert lectura.etiquetas == ["incest", "schoolgirls"]
    assert not lectura.rechazadas

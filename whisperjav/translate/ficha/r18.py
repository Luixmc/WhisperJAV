"""A work's genres and titles from r18.dev, to start its notes from.

r18.dev serves FANZA's data as JSON: ``dvd_id=ATID-454/json`` gives the
``content_id`` and ``combined=<content_id>/json`` the whole work. Its genres
come with FANZA's Japanese names, which say what the English ones soften
("Stepfamily" is 近親相姦, incest, and the English title turns 母 into
"stepmother"), so the Japanese names are the ones kept; the English title,
translated by hand, goes to the description to help the study.

Like ``javdb``, every failure here is a warning, never a stop.
"""

from typing import Optional

import requests

from whisperjav.translate.ficha.javdb import AGENTE, JavDBError, Obra

BASE = "https://r18.dev/videos/vod/movies/detail/-/"

# FANZA genres about the product, not the work.
RUIDO = frozenset({
    "ハイビジョン", "独占配信", "単体作品", "サンプル動画", "4時間以上作品", "4K",
    "8KVR", "ハイクオリティVR", "VR専用", "デジモ", "期間限定セール", "アウトレット",
    "ベスト・総集編", "特典あり（AVベースボール）",
})


def _json(path: str, timeout: float) -> Optional[dict]:
    try:
        response = requests.get(f"{BASE}{path}/json", headers={"User-Agent": AGENTE}, timeout=timeout)
    except requests.RequestException as error:
        raise JavDBError(f"no se pudo conectar con r18.dev: {error}") from error
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise JavDBError(f"r18.dev respondió {response.status_code}")
    try:
        return response.json()
    except ValueError as error:
        raise JavDBError("r18.dev no devolvió JSON") from error


def consultar(codigo_obra: str, timeout: float = 20) -> Optional[Obra]:
    """The work on r18.dev, or None if it is not listed."""
    corto = _json(f"dvd_id={codigo_obra}", timeout)
    if not corto or not corto.get("content_id"):
        return None
    datos = _json(f"combined={corto['content_id']}", timeout)
    if not datos:
        return None
    obra = Obra(codigo=codigo_obra, url=f"{BASE}combined={corto['content_id']}/json", fuente="r18.dev")
    obra.titulo = (datos.get("title_ja") or "").strip()
    obra.titulo_en = (datos.get("title_en_uncensored") or datos.get("title_en") or "").strip()
    obra.sinopsis = (datos.get("comment_en") or "").strip()
    obra.fecha = datos.get("release_date") or ""
    obra.estudio = datos.get("maker_name_ja") or datos.get("maker_name_en") or ""
    obra.serie = datos.get("series_name_ja") or datos.get("series_name_en") or ""
    obra.actrices = [
        a.get("name_kanji") or a.get("name_romaji") or "" for a in datos.get("actresses") or []
    ]
    obra.actrices = [a for a in obra.actrices if a]
    for categoria in datos.get("categories") or []:
        nombre = (categoria.get("name_ja") or categoria.get("name_en") or "").strip()
        if nombre and nombre not in RUIDO and nombre not in obra.etiquetas:
            obra.etiquetas.append(nombre)
    return obra

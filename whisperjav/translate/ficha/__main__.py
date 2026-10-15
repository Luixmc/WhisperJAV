"""Command line: study a work, review its translation, apply what was approved.

    python -m whisperjav.translate.ficha estudiar VIDEO.japanese.srt --etiquetas "人妻, NTR"
    whisperjav-translate -i VIDEO.japanese.srt -t spanish ...   (uses the notes on its own)
    python -m whisperjav.translate.ficha revisar VIDEO.japanese.srt VIDEO.spanish.srt
    python -m whisperjav.translate.ficha aplicar VIDEO.spanish.srt
    python -m whisperjav.translate.ficha traducir CARPETA_VIDEO_O_SRT...   (por lotes)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from whisperjav.translate.ficha import etiquetas
from whisperjav.translate.ficha.lmstudio import DEFAULT_URL, LMStudio, LMStudioError
from whisperjav.translate.ficha.obra import Obra


def _cliente(args) -> LMStudio:
    return LMStudio(base_url=args.url, model=args.modelo, context_length=args.contexto)


def _modelo(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--modelo", help="modelo de LM Studio (por defecto, el que esté cargado)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"servidor de LM Studio (por defecto {DEFAULT_URL})")
    parser.add_argument("--contexto", type=int, default=16384,
                        help="contexto con que se carga el modelo (por defecto 16384)")


def salida_de(srt: Path, idioma: str) -> Path:
    """Where the translation of ``srt`` goes: named after the video
    (``test.ja.whisperjav.srt`` -> ``test.srt``), so players load it on their
    own; ``test.<idioma>.srt`` when the original already has that name."""
    from whisperjav.translate.ficha.obra import nombre_video

    salida = srt.parent / f"{nombre_video(srt)}.srt"
    if salida.resolve() == srt.resolve():
        salida = srt.parent / f"{nombre_video(srt)}.{idioma}.srt"
    return salida


VIDEOS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".flv", ".webm"}

# The CrispASR build that ships with this checkout (see docs/en/pipelines/crispasr.md).
CRISPASR = (Path(__file__).resolve().parents[3] / ".tools" / "crispasr"
            / "crispasr-windows-x86_64-vulkan" / "crispasr.exe")


def _srts(rutas: List[Path], idioma: str) -> List[Path]:
    """The Japanese .srt files given or in the folders given, leaving out
    translations (``x.spanish.srt``, and the ``x.srt`` written for an
    ``x.ja.whisperjav.srt`` next to it)."""
    from whisperjav.translate.output_naming import language_suffixes

    translated = language_suffixes() - {"japanese", "ja", "jp"}
    found: List[Path] = []
    for ruta in rutas:
        files = sorted(ruta.glob("*.srt")) if ruta.is_dir() else [ruta]
        for srt in files:
            if srt.suffix.lower() != ".srt" or not srt.is_file():
                continue
            parts = srt.stem.split(".")
            if len(parts) > 1 and parts[-1].lower() in translated:
                continue
            if srt not in found:
                found.append(srt)
    salidas = {salida_de(srt, idioma).resolve() for srt in found}
    return [srt for srt in found if srt.resolve() not in salidas]


def originales(rutas: List[Path], idioma: str) -> List[Tuple[Path, Optional[Path]]]:
    """What to translate, as (Japanese .srt, video to transcribe first or None).

    Videos count too: one that already has a Japanese .srt beside it is
    translated from it; one that has none is transcribed first into
    ``<video>.ja.whisperjav.srt``.
    """
    from whisperjav.translate.ficha.obra import nombre_video

    trabajos: List[Tuple[Path, Optional[Path]]] = [(srt, None) for srt in _srts(rutas, idioma)]
    for ruta in rutas:
        videos = sorted(f for f in ruta.iterdir() if f.suffix.lower() in VIDEOS) if ruta.is_dir() else [ruta]
        for video in videos:
            if video.suffix.lower() not in VIDEOS or not video.is_file():
                continue
            if any(nombre_video(srt) == video.stem and srt.parent == video.parent for srt, _ in trabajos):
                continue
            beside = [srt for srt in _srts([video.parent], idioma) if nombre_video(srt) == video.stem]
            if beside:
                trabajos.append((beside[0], None))
            else:
                trabajos.append((video.parent / f"{video.stem}.ja.whisperjav.srt", video))
    return trabajos


# Tones of the window and the batch: the name passed to whisperjav.translate.cli
# and its temperature. extrapornify is pornify (same instructions) running hotter.
TONOS = {
    "standard": ("standard", None),
    "contextual": ("contextual", None),
    "pornify": ("pornify", 1.0),
    "extrapornify": ("pornify", 1.2),
}


def transcribir(video: Path, crispasr: Path) -> bool:
    """Japanese subtitles for ``video`` with CrispASR (Vulkan, Qwen3-ASR)."""
    if not crispasr.is_file():
        print(f"error: no encuentro CrispASR en {crispasr} (usa --crispasr)", file=sys.stderr)
        return False
    command = [
        sys.executable, "-m", "whisperjav.main", str(video), "--mode", "crispasr",
        "--crispasr-exe", str(crispasr), "--crispasr-backend", "qwen3-1.7b",
        "--language", "japanese", "--output-dir", str(video.parent),
        # CrispASR runs on its own Vulkan build; the check is about torch's GPU.
        "--accept-cpu-mode",
    ]
    print(f"transcribiendo {video.name} con CrispASR...", file=sys.stderr, flush=True)
    return subprocess.run(command).returncode == 0


def desde_javdb(srt: Path, forzar: bool = False) -> bool:
    """Fill the work's ``usuario.json`` from r18.dev, or from JavDB when
    r18.dev does not have it, unless the user has written one (or ``forzar``);
    True if it was filled. Never stops the work.

    r18.dev goes first: its genres keep FANZA's Japanese names (近親相姦),
    while JavDB's English ones soften them ("Stepfamily")."""
    from whisperjav.translate.ficha import javdb, r18

    obra = Obra(srt)
    previo = obra.usuario()
    if not forzar and (previo.get("etiquetas") or previo.get("descripcion")):
        return False
    codigo = javdb.codigo(obra.nombre)
    if not codigo:
        return False
    encontrada = None
    for nombre, sitio in (("r18.dev", r18), ("JavDB", javdb)):
        try:
            encontrada = sitio.consultar(codigo)
        except javdb.JavDBError as error:
            print(f"aviso: {nombre}: {error}", file=sys.stderr)
            continue
        if encontrada:
            break
        print(f"{nombre} no tiene {codigo}", file=sys.stderr)
    if not encontrada:
        return False
    # JavDB's tags miss what only the title says (性処理, ニート...).
    tags = list(encontrada.etiquetas)
    vistas = {etiquetas.NOMBRE.get(t.lower(), t) for t in tags}
    tags += [t for t in etiquetas.del_titulo(encontrada.titulo) if t not in vistas]
    encontrada.etiquetas = tags
    obra.escribir_usuario(encontrada.etiquetas, encontrada.descripcion())
    print(
        f"{encontrada.fuente} {codigo}: {encontrada.titulo}\n"
        f"  etiquetas: {', '.join(encontrada.etiquetas) or '(ninguna)'}\n"
        f"  guardado en {obra.usuario_path}",
        file=sys.stderr, flush=True,
    )
    return True


def descargar(args, motivo: str) -> None:
    """Unload LM Studio's model, so the VRAM is free for CrispASR or for
    whatever runs next."""
    try:
        if _cliente(args).unload_all():
            print(f"modelo descargado de LM Studio ({motivo})", file=sys.stderr, flush=True)
    except LMStudioError as error:
        print(f"aviso: no se pudo descargar el modelo: {error}", file=sys.stderr)


def carpeta_salida(origen: Path, salida: Optional[Path]) -> Path:
    """Where finished works go: ``salida`` if given, else ``output`` beside an
    ``input`` folder (``I:/j/input`` -> ``I:/j/output``), else ``output`` inside
    the folder the video is in."""
    if salida:
        return salida
    if origen.name.lower() == "input":
        return origen.parent / "output"
    return origen / "output"


def _mover(src: Path, dst: Path) -> None:
    """Move a file or folder, replacing what is there; folders are merged."""
    if src.is_dir() and dst.is_dir():
        for hijo in list(src.iterdir()):
            _mover(hijo, dst / hijo.name)
        src.rmdir()
        return
    if dst.is_file():
        dst.unlink()
    shutil.move(str(src), str(dst))


def archivar(srt: Path, raiz: Path) -> Path:
    """Move the video and everything named after it (subtitles, .subtrans,
    the ``.ficha`` folder) to ``raiz/<video>/``."""
    from whisperjav.translate.ficha.obra import nombre_video

    nombre = nombre_video(srt)
    destino = raiz / nombre
    destino.mkdir(parents=True, exist_ok=True)
    for item in list(srt.parent.iterdir()):
        es_video = item.is_file() and item.stem == nombre and item.suffix.lower() in VIDEOS
        if es_video or item.name.startswith(f"{nombre}."):
            _mover(item, destino / item.name)
    return destino


APAGAR_EN = 60  # seconds to cancel the shutdown with "shutdown /a"


def apagar() -> None:
    """Shut the computer down in APAGAR_EN seconds, leaving time to cancel."""
    if os.name == "nt":
        command = ["shutdown", "/s", "/t", str(APAGAR_EN), "/c", "WhisperJAV terminó el lote de traducción"]
        cancelar = "shutdown /a"
    else:
        command = ["shutdown", "-h", f"+{max(1, APAGAR_EN // 60)}"]
        cancelar = "shutdown -c"
    print(f"la computadora se apagará en {APAGAR_EN} s (para cancelarlo: {cancelar})",
          file=sys.stderr, flush=True)
    subprocess.run(command)


def traducir(args) -> int:
    """Transcribe the videos that need it, study the works that have no notes
    yet, and translate every file; unload the model at the end, and shut the
    computer down if asked to, whether the batch went well or not."""
    try:
        return _traducir(args)
    finally:
        if args.descargar:
            descargar(args, "fin")
        if args.apagar:
            apagar()


def _traducir(args) -> int:
    if args.convertir:
        from whisperjav.translate.ficha.convertir import con_mp4, convertir
        convertir(args.rutas)
        args.rutas = con_mp4(args.rutas)
    trabajos = originales(args.rutas, args.idioma)
    if not trabajos:
        print("error: no hay videos ni .srt para traducir en lo que se indicó", file=sys.stderr)
        return 1
    fallos = 0

    def terminar(srt: Path) -> None:
        # A work already in output/<video>/ stays where it is.
        if not args.mover or srt.parent.parent.name.lower() == "output":
            return
        destino = archivar(srt, carpeta_salida(srt.parent, args.salida))
        print(f"movido a {destino}", file=sys.stderr, flush=True)

    for n, (srt, video) in enumerate(trabajos, 1):
        salida = salida_de(srt, args.idioma)
        print(f"\n=== [{n}/{len(trabajos)}] {(video or srt).name}", file=sys.stderr, flush=True)
        if salida.exists() and not args.rehacer:
            print(f"ya traducido: {salida.name} (usa --rehacer para repetirlo)", file=sys.stderr)
            terminar(srt)
            continue
        if video and not srt.exists():
            # CrispASR and the language model do not share 16 GB of VRAM well.
            descargar(args, "para transcribir")
            if not (transcribir(video, args.crispasr) and srt.exists()):
                print(f"error: no se pudo transcribir {video.name}", file=sys.stderr)
                fallos += 1
                continue
        if args.javdb:
            desde_javdb(srt)
        try:
            etiquetas.comprobar(Obra(srt).lectura())
            # Load the model with the context we want; left to LM Studio, a
            # request loads it with its own default context.
            _cliente(args).ensure_ready()
            if args.estudiar and not Obra(srt).ficha():
                from whisperjav.translate.ficha.estudio import estudiar as run
                run(srt, _cliente(args))
        except (etiquetas.EtiquetasQueDetienen, LMStudioError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            fallos += 1
            continue
        if args.rehacer:
            for stale in (salida, srt.with_suffix(".subtrans")):
                if stale.exists():
                    stale.unlink()
        command = [
            sys.executable, "-m", "whisperjav.translate.cli", "-i", str(srt), "-o", str(salida),
            "--provider", "custom", "--endpoint", f"{args.url.rstrip('/')}/v1",
            "--api-key", "lm-studio", "-t", args.idioma, "--tone", TONOS[args.tono][0],
            "--max-batch-size", str(args.lote),
        ]
        if TONOS[args.tono][1] is not None:
            command += ["--temperature", str(TONOS[args.tono][1])]
        if args.modelo:
            command += ["--model", args.modelo]
        if subprocess.run(command).returncode != 0 or not salida.exists():
            print(f"error: no se pudo traducir {srt.name}", file=sys.stderr)
            fallos += 1
        else:
            print(f"listo: {salida}", file=sys.stderr, flush=True)
            terminar(srt)
    print(f"\n{len(trabajos) - fallos} de {len(trabajos)} archivo(s) listos", file=sys.stderr)
    return 1 if fallos else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m whisperjav.translate.ficha",
        description="Ficha, glosario y revisión de la traducción al español de un video.",
    )
    commands = parser.add_subparsers(dest="comando", required=True)

    estudiar = commands.add_parser("estudiar", help="leer los subtítulos originales y escribir la ficha")
    estudiar.add_argument("srt", type=Path, help="subtítulos originales (japonés)")
    estudiar.add_argument("--etiquetas", help='etiquetas de la obra, separadas por comas ("人妻, NTR")')
    estudiar.add_argument("--descripcion", help="descripción breve de la obra")
    _modelo(estudiar)

    revisar = commands.add_parser("revisar", help="proponer correcciones de la traducción")
    revisar.add_argument("original", type=Path, help="subtítulos originales (japonés)")
    revisar.add_argument("traducido", type=Path, help="subtítulos traducidos (español)")
    revisar.add_argument("--limite", type=int, help="revisar solo los primeros N tramos")
    _modelo(revisar)

    aplicar = commands.add_parser("aplicar", help="aplicar las correcciones y términos marcados con s o n")
    aplicar.add_argument("traducido", type=Path, help="subtítulos traducidos (español)")

    lote = commands.add_parser(
        "traducir", help="transcribir y estudiar si hace falta, y traducir varios videos, .srt o carpetas")
    lote.add_argument("rutas", type=Path, nargs="+", help="videos, archivos .srt o carpetas")
    lote.add_argument("--crispasr", type=Path, default=CRISPASR,
                      help=f"ejecutable de CrispASR para transcribir videos (por defecto {CRISPASR})")
    lote.add_argument("--idioma", default="spanish", help="idioma de destino (por defecto spanish)")
    lote.add_argument("--tono", default="contextual", choices=list(TONOS),
                      help="tono de la traducción (por defecto contextual); pornify va a "
                           "temperatura 1.0 y extrapornify, con sus mismas instrucciones, a 1.2")
    lote.add_argument("--lote", type=int, default=25, help="líneas por lote (por defecto 25)")
    lote.add_argument("--sin-estudio", dest="estudiar", action="store_false",
                      help="no estudiar las obras que no tienen ficha")
    lote.add_argument("--rehacer", action="store_true", help="volver a traducir aunque ya exista la traducción")
    lote.add_argument("--mantener-modelo", dest="descargar", action="store_false",
                      help="no descargar el modelo de LM Studio al terminar")
    lote.add_argument("--sin-javdb", dest="javdb", action="store_false",
                      help="no buscar en r18.dev ni JavDB las etiquetas de las obras sin usuario.json")
    lote.add_argument("--salida", type=Path,
                      help="carpeta donde queda cada obra terminada, en una subcarpeta con el nombre "
                           "del video (por defecto output junto a la carpeta input, o output dentro "
                           "de la carpeta del video)")
    lote.add_argument("--sin-mover", dest="mover", action="store_false",
                      help="dejar todo junto al video en vez de moverlo a la carpeta de salida")
    lote.add_argument("--convertir", action="store_true",
                      help="convertir antes los .ts a .mp4 sin recodificar (los .ts no se borran)")
    lote.add_argument("--apagar", action="store_true",
                      help=f"apagar la computadora al terminar el lote (se cancela en {APAGAR_EN} s con shutdown /a)")

    jdb = commands.add_parser("javdb", help="traer de r18.dev (o JavDB) el título y las etiquetas de una obra")
    jdb.add_argument("srt", type=Path, help="el video o un subtítulo suyo (el código sale del nombre)")
    jdb.add_argument("--forzar", action="store_true", help="reemplazar el usuario.json que ya exista")
    _modelo(lote)

    conv = commands.add_parser("convertir", help="convertir videos .ts a .mp4 sin recodificar")
    conv.add_argument("rutas", type=Path, nargs="+", help="archivos .ts o carpetas")

    carpeta = commands.add_parser("carpeta", help="mostrar dónde van los diccionarios y la carpeta de una obra")
    carpeta.add_argument("srt", type=Path, nargs="?")

    args = parser.parse_args(argv)
    try:
        if args.comando == "estudiar":
            from whisperjav.translate.ficha.estudio import estudiar as run
            tags = etiquetas.partir(args.etiquetas) if args.etiquetas is not None else None
            run(args.srt, _cliente(args), tags=tags, descripcion=args.descripcion)
        elif args.comando == "revisar":
            from whisperjav.translate.ficha.revision import revisar as run
            run(args.original, args.traducido, _cliente(args), limite=args.limite)
        elif args.comando == "traducir":
            return traducir(args)
        elif args.comando == "convertir":
            from whisperjav.translate.ficha.convertir import convertir
            return 1 if convertir(args.rutas) else 0
        elif args.comando == "javdb":
            obra = Obra(args.srt)
            if not desde_javdb(args.srt, forzar=args.forzar):
                previo = obra.usuario()
                if previo.get("etiquetas") or previo.get("descripcion"):
                    print(f"ya hay {obra.usuario_path}; usa --forzar para reemplazarlo", file=sys.stderr)
                return 1
            lectura = obra.lectura()
            for guia in lectura.guia:
                print(f"  - {guia}")
            etiquetas.comprobar(lectura)
        elif args.comando == "aplicar":
            from whisperjav.translate.ficha.revision import aplicar as run
            run(args.traducido)
        else:
            print(f"diccionarios: {etiquetas.carpeta()}")
            if args.srt:
                print(f"obra: {Obra(args.srt).dir}")
    except (etiquetas.EtiquetasQueDetienen, LMStudioError, FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

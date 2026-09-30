"""
CrispASR — self-contained subprocess runner.

This module calls the ``crispasr`` external binary and reads the resulting
SRT file.  It has **NO** imports from WhisperJAV pipelines, config resolution,
ensemble, or BYOP internals — a standalone provider call, the way SubtitleEdit
and Susurrus cleanly shell out to crispasr.  This is deliberately *not* a
mirror of ``whisperjav/byop/xxl_runner.py``; it only shares the same
defensive subprocess *pattern* (build argv → run → tolerate the C++-shutdown
crash by checking the SRT before the exit code).

Argv form verified vs CrispASR ``docs/cli.md`` (Phase-1 R-2 resolution,
``docs/plans/crispasr_v190/08`` §1/§3a)::

    crispasr <-m auto | --hf-repo REPO:FILE>
             --backend <whisper|parakeet|cohere|qwen3-1.7b>
             -f <input.wav> -l <lang> -osrt -of <outbase>
             [per-backend defaults] [extra args]       -> writes <outbase>.srt

    (Per-backend defaults: VAD for whisper; VAD + Qwen3 forced aligner for
    qwen3-1.7b, which has no native timestamps. See BACKEND_DEFAULT_ARGS.)

Design:  docs/plans/crispasr_v190/08_crispasr_simple_pipeline_design.md
Durable record:  memory/feedback_crispasr_simple_standalone_pipeline.md
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# CL1 — backend curation (user decision 2026-05-15, revised 2026-05-16)
# ---------------------------------------------------------------------------
# v1.9.0 first release keeps the curated set minimal — all native
# word-timestamp, none needing a forced aligner:
#   * ``whispercpp`` — WhisperJAV-facing alias of crispasr's real ``whisper``
#                      backend (whisper.cpp). Defaults to large-v2 (the
#                      engine's bare ``whisper`` default is ggml-base — too
#                      weak), see BACKEND_MODEL_SPEC.
#   * ``parakeet``   — default backend.
#   * ``cohere``     — uses the OPEN ``cstr/cohere-transcribe-03-2026-GGUF``
#                      ggml asset (Apache-2.0); NOT the gated upstream
#                      CohereLabs repo, so no HF token is required
#                      (user clarification CL1(b), 2026-05-16).
# ``canary`` and ``kyutai-stt`` are intentionally REMOVED for the first
# release (CL1(d)/(e)): canary deferred to keep all forced-aligner /
# timestamp complexity out of v1.9.0; kyutai-stt deferred. Re-add later.
#
# AMD fork: ``qwen3-1.7b`` added (Qwen3-ASR 1.7B GGUF). On a Vulkan build it
# is the recommended backend for AMD GPUs, which have no CUDA path — see the
# per-backend args below for the forced aligner it needs.
CURATED_BACKENDS = ("whispercpp", "parakeet", "cohere", "qwen3-1.7b")
DEFAULT_BACKEND = "parakeet"

# WhisperJAV-facing name -> the actual crispasr ``--backend`` value.
# Only ``whispercpp`` is aliased (to crispasr's real backend id ``whisper``);
# the others pass through unchanged.
BACKEND_ALIAS = {"whispercpp": "whisper"}

# Per-backend model selection argv. ``-m auto`` lets the engine self-download
# its registry-default GGUF (D5a — WhisperJAV writes no download code).
# ``whispercpp`` needs an explicit model because the engine's bare ``whisper``
# default is ggml-base (too weak). Runtime-verified on CrispASR 0.8.38: the
# ``-m auto:large-v2`` variant form is NOT parsed — it resolves to ggml-base —
# so the model is named via ``--hf-repo`` instead. large-v3 q5_0 (1.1 GB) is
# the build validated on Japanese audio. A model flag in ``--crispasr-args``
# replaces this default (see build_crispasr_argv).
DEFAULT_MODEL_ARGS = ["-m", "auto"]
BACKEND_MODEL_ARGS = {
    "whispercpp": ["--hf-repo", "ggerganov/whisper.cpp:ggml-large-v3-q5_0.bin"],
}

# Per-backend default argv, validated on a 20-minute JAV sample (CrispASR
# 0.8.38, Vulkan):
#   * whispercpp — without VAD it looped one hallucinated line 247 times over
#     non-speech stretches; Silero VAD removes the loop.
#   * qwen3-1.7b — no native timestamps. ``-am auto`` picks the canary CTC
#     aligner, which collapsed on Japanese in 64 of 73 slices; the Qwen3
#     forced aligner works. ``-sp`` splits the aligned text into one sentence
#     per cue (avg 3.1 s instead of 10.6 s).
BACKEND_DEFAULT_ARGS = {
    "whispercpp": ["--vad", "-vm", "silero"],
    "qwen3-1.7b": ["-am", "qwen3-forced-aligner", "--vad", "-vm", "silero", "-sp"],
}

_MODEL_FLAGS = {"-m", "--model", "-hfr", "--hf-repo"}


def run_crispasr(
    input_file: str,
    exe_path: str,
    backend: str = DEFAULT_BACKEND,
    language: str = "ja",
    output_dir: Optional[str] = None,
    extra_args: str = "",
) -> Path:
    """
    Call ``crispasr`` as a subprocess and return the path to the generated SRT.

    Args:
        input_file: Path to the (already-extracted) input WAV.
        exe_path: Absolute path to the ``crispasr`` executable.
        backend: One of :data:`CURATED_BACKENDS`.
        language: Source language code (e.g. ``"ja"``).
        output_dir: Directory where crispasr writes the SRT.  If ``None``,
            uses ``_whisperjav_crispasr_output`` next to the exe.
        extra_args: Whitespace-separated extra arguments passed verbatim to
            crispasr (shlex-split).  The single escape valve for all advanced
            CrispASR-internal control (doc 08 §3a Probe4).

    Returns:
        Path to the generated SRT file.

    Raises:
        FileNotFoundError: If the crispasr executable does not exist.
        ValueError: If ``backend`` is not curated for this release.
        RuntimeError: If crispasr produced no SRT.
    """
    exe = Path(exe_path)
    if not exe.is_file():
        raise FileNotFoundError(f"CrispASR executable not found: {exe_path}")

    if backend not in CURATED_BACKENDS:
        raise ValueError(
            f"CrispASR backend '{backend}' is not curated for this release. "
            f"Available: {', '.join(CURATED_BACKENDS)}"
        )

    if output_dir is None:
        output_dir = str(exe.parent / "_whisperjav_crispasr_output")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # crispasr writes ``<outbase>.srt`` for the -osrt/-of output pair.
    outbase = str(Path(output_dir) / Path(input_file).stem)

    # The engine cannot open non-ASCII paths on Windows (Japanese file names,
    # accented user names): run it on an ASCII-named copy, move the SRT back.
    run_input, run_outbase, stage_dir = _stage_for_engine(input_file, outbase, exe)
    try:
        srt_path = _run_engine(
            exe, backend, run_input, language, run_outbase, extra_args
        )
        if stage_dir is not None:
            final = Path(outbase + ".srt")
            shutil.move(str(srt_path), str(final))
            srt_path = final
        return srt_path
    finally:
        if stage_dir is not None:
            shutil.rmtree(stage_dir, ignore_errors=True)


def build_crispasr_argv(
    exe: Path,
    backend: str,
    input_file: str,
    language: str,
    outbase: str,
    extra_args: str = "",
) -> List[str]:
    """Build the crispasr argv for a curated ``backend``.

    Order: model selection, the fixed I/O flags, the per-backend defaults
    (VAD / aligner), then the user's ``extra_args`` last so they can
    override scalar options. A model flag in ``extra_args`` replaces the
    default model selection instead of being passed alongside it.
    """
    user_args = shlex.split(extra_args.strip()) if extra_args and extra_args.strip() else []
    if _MODEL_FLAGS.intersection(user_args):
        model_args = []
    else:
        model_args = BACKEND_MODEL_ARGS.get(backend, DEFAULT_MODEL_ARGS)

    return [
        str(exe),
        *model_args,             # engine self-manages its model cache (D5a)
        "--backend", BACKEND_ALIAS.get(backend, backend),
        "-f", str(input_file),   # documented canonical input flag
        "-l", language,
        "-osrt",
        "-of", outbase,
        *BACKEND_DEFAULT_ARGS.get(backend, []),
        *user_args,
    ]


def _run_engine(
    exe: Path,
    backend: str,
    input_file: str,
    language: str,
    outbase: str,
    extra_args: str,
) -> Path:
    """Run crispasr once and return the SRT it produced (see run_crispasr)."""
    cmd = build_crispasr_argv(exe, backend, input_file, language, outbase, extra_args)
    engine_out_dir = str(Path(outbase).parent)

    # WhisperJAV writes NO download/verify code (D5a).  The engine
    # self-manages models via ``-m auto``; we only redirect its cache into a
    # dedicated subdir under WhisperJAV's cache namespace without colliding
    # with the HF-hub blob layout, honoring I1's intent.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    env.setdefault("CRISPASR_CACHE_DIR", str(_default_cache_dir(exe)))

    start_time = time.monotonic()
    # Wall-clock stamp for _find_srt: only SRT files written DURING this run
    # may be attributed to it (v1.9.0 code-review fix — see _find_srt).
    run_started_wall = time.time()

    # CAPTURE stdout (not stream).  CrispASR reliably prints the full
    # transcription to stdout in a fixed, documented format
    # (``[HH:MM:SS.fff --> HH:MM:SS.fff]  text``, docs/plans/crispasr_v190
    # 02b §1.6); the ``-osrt``/``-of`` file-output flags are version-fragile
    # and were observed (2026-05-16 acceptance test T1) to produce NO file
    # while the transcription still succeeded and went to stdout.  Susurrus —
    # the reference Python consumer — likewise parses stdout for exactly this
    # reason (02a §3 / 02b §4).  We therefore synthesise the SRT from stdout,
    # keeping engine-written-file detection as a first-choice fallback for
    # builds that DO honour ``-osrt``.
    # errors="replace" prevents UnicodeDecodeError when the external process
    # emits non-UTF-8 bytes (e.g. Windows system codepage for CJK filenames).
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(exe.parent),
        env=env,
    )

    elapsed = time.monotonic() - start_time
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""

    # Echo the engine's stdout through so it still appears in the
    # WhisperJAV/GUI console (we captured it instead of streaming).
    if stdout_text:
        print(stdout_text, end="" if stdout_text.endswith("\n") else "\n")

    # Acquire the SRT BEFORE judging the exit code.  CrispASR is built on the
    # ggml / whisper.cpp stack which can crash in its C++ destructor on
    # process shutdown (the class of issue WhisperJAV's Nuclear Exit solves);
    # the transcription completes first.
    # 1) A file the engine wrote (if this build honours -osrt/-of).
    srt_path = _find_srt(engine_out_dir, input_file, outbase, run_started_wall)
    # 2) Otherwise synthesise it from the captured stdout (the robust path).
    if srt_path is None:
        srt_path = _srt_from_stdout(stdout_text, outbase)

    if srt_path is not None:
        if result.returncode != 0:
            print(
                f"[CrispASR] Warning: process exited non-zero "
                f"(code {result.returncode}) but a transcript was produced "
                f"successfully ({elapsed:.1f}s)",
                file=sys.stderr,
            )
        return srt_path

    tail = (stderr_text or stdout_text or "(no output)")[-2000:]
    if result.returncode != 0:
        raise RuntimeError(
            f"CrispASR failed (exit {result.returncode}, {elapsed:.1f}s):\n{tail}"
        )
    raise RuntimeError(
        f"CrispASR completed ({elapsed:.1f}s) but produced no SRT file and "
        f"no parseable transcript on stdout (output dir: {engine_out_dir})\n{tail}"
    )


def _find_srt(
    output_dir: str, input_file: str, outbase: str, run_started_wall: float
) -> Optional[Path]:
    """Locate an SRT the engine wrote DURING this run.

    Returns Path if non-empty and freshly written, else None.

    v1.9.0 fix (code-review): every candidate is gated on mtime >= run start
    (2s filesystem-granularity slack). The pipeline reuses ONE shared output
    dir across a batch, so without the gate, fallback #3 (newest *.srt, no
    stem filter) could return the PREVIOUS file's SRT as this file's
    transcript whenever the engine wrote no file for the current input (the
    documented -osrt-fragile case) and stale files survived (--keep-temp or
    a skipped cleanup). A pre-existing file is by definition not this run's
    output — stdout synthesis then takes over, which is the robust path.
    """
    in_parent = Path(input_file).parent
    in_stem = Path(input_file).stem
    min_mtime = run_started_wall - 2.0  # slack for coarse filesystem timestamps

    def _fresh(p: Path) -> bool:
        try:
            st = p.stat()
        except OSError:
            return False
        return st.st_size > 0 and st.st_mtime >= min_mtime

    # 1. The exact -of target.
    expected = Path(outbase + ".srt")
    if expected.is_file() and _fresh(expected):
        return expected

    # 2. <input_stem>.srt in output_dir or next to the input (whisper.cpp
    #    style — some builds write the SRT beside the input file).
    for cand in (
        Path(output_dir) / f"{in_stem}.srt",
        in_parent / f"{in_stem}.srt",
    ):
        if cand.is_file() and _fresh(cand):
            return cand

    # 3. Newest non-empty .srt in output_dir or the input's directory —
    #    fresh-only, so a previous batch item's SRT can never be returned.
    srts = sorted(
        list(Path(output_dir).glob("*.srt")) + list(in_parent.glob("*.srt")),
        key=os.path.getmtime,
        reverse=True,
    )
    for s in srts:
        if _fresh(s):
            return s

    return None


# CrispASR stdout line: ``[H:MM:SS.fff --> H:MM:SS.fff]  text``
# (period before ms, two spaces after ``]``; 02b §1.6 + Susurrus 02b §4).
_TS_LINE_RE = re.compile(
    r"^\s*\[\s*(\d+):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
    r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})\s*\]\s*(.*)$"
)


def _fmt_ts(h: str, m: str, s: str, ms: str) -> str:
    """`H, MM, SS, fff` strings -> SRT `HH:MM:SS,mmm`."""
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(ms):03d}"


def _srt_from_stdout(stdout_text: str, outbase: str) -> Optional[Path]:
    """Parse the engine's stdout transcript lines and write an SRT.

    Returns the written Path, or None if no parseable lines were found.
    """
    blocks = []
    for line in stdout_text.splitlines():
        mobj = _TS_LINE_RE.match(line)
        if not mobj:
            continue
        text = (mobj.group(9) or "").strip()
        if not text:
            continue
        start = _fmt_ts(*mobj.group(1, 2, 3, 4))
        end = _fmt_ts(*mobj.group(5, 6, 7, 8))
        blocks.append((start, end, text))

    if not blocks:
        return None

    srt_path = Path(outbase + ".srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(srt_path, "w", encoding="utf-8") as fh:
        for idx, (start, end, text) in enumerate(blocks, 1):
            fh.write(f"{idx}\n{start} --> {end}\n{text}\n\n")
    return srt_path


# ---------------------------------------------------------------------------
# Minimum on-screen time
# ---------------------------------------------------------------------------
# Owner decision 2026-09-29, the one change to the engine's SRT besides its
# placement: timings only, never text. Through bursts of screams and moans the
# Qwen3 forced aligner finds no word boundaries and writes subtitles that last
# no time at all (``10:33,920 --> 10:33,920``: 21 of 198 on a 105-minute
# video, plus 10 under 0.3 s), often several at the same instant. A player
# never shows them, translated or not.

MIN_DURATION_MS = 800


def _ms(ts: str) -> int:
    h, m, rest = ts.split(":")
    s, ms = rest.replace(".", ",").split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def _ts(ms: int) -> str:
    h, rest = divmod(max(0, ms), 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def spread_short_subtitles(times: List[List[int]], minimum: int = MIN_DURATION_MS) -> int:
    """Give every [start, end] (ms, in order) at least ``minimum`` on screen.

    A subtitle that grows over the next one pushes the next one's start to its
    own end, so several lines said at the same instant play one after another;
    when the next one is long and a push would leave it under ``minimum``, the
    two share the time up to the next one's end (at most ``minimum`` for this
    one). Returns how many changed.
    """
    before = [tuple(t) for t in times]
    shared = set()  # lines that split their time with the previous one
    for i, (start, end) in enumerate(times):
        if i not in shared:
            end = max(end, start + minimum)
        if i + 1 < len(times):
            nxt = times[i + 1]
            if nxt[0] < end:
                if nxt[1] - end >= minimum or nxt[1] - nxt[0] < minimum:
                    nxt[0] = end  # a short next line is lengthened on its own turn
                else:
                    end = max(nxt[0], start + min(minimum, (nxt[1] - start) // 2))
                    nxt[0] = max(nxt[0], end)
                    shared.add(i + 1)
        times[i] = [start, end]
    return sum(tuple(t) != b for t, b in zip(times, before))


def enforce_min_duration(srt_path: Path, minimum: int = MIN_DURATION_MS) -> int:
    """Rewrite ``srt_path`` so no subtitle is too short to be seen; returns how
    many were changed (the file is left untouched when none)."""
    text = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    blocks = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        at = next((k for k, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue
        start, end = (part.strip().split(" ")[0] for part in lines[at].split("-->"))
        blocks.append(([_ms(start), _ms(end)], "\n".join(lines[at + 1:])))
    times = [t for t, _ in blocks]
    changed = spread_short_subtitles(times, minimum)
    if changed:
        with open(srt_path, "w", encoding="utf-8") as fh:
            for idx, ((start, end), body) in enumerate(zip(times, (b for _, b in blocks)), 1):
                fh.write(f"{idx}\n{_ts(start)} --> {_ts(end)}\n{body}\n\n")
    return changed


# ---------------------------------------------------------------------------
# Engine-safe paths (Windows)
# ---------------------------------------------------------------------------
# CrispASR 0.8.38 on Windows cannot open a path containing any non-ASCII
# character: an input under a user folder such as ``C:\Users\José`` fails
# with "input file not found", and a model cache there fails to download.
# Japanese media names hit the same wall. The helpers below keep every path
# handed to the engine ASCII.


def _engine_safe(path) -> bool:
    """True if crispasr can open ``path`` (always true off Windows)."""
    return sys.platform != "win32" or str(path).isascii()


def _windows_short_path(path: Path) -> Optional[Path]:
    """8.3 short form of an existing path (e.g. ``JOSE~1``), if any."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
        if 0 < n < len(buf):
            return Path(buf.value)
    except Exception:  # noqa: BLE001 - no short name is a normal outcome
        pass
    return None


def _engine_safe_dir(preferred: Path, exe: Path, name: str) -> Path:
    """First writable directory crispasr can open, trying ``preferred`` first.

    Each candidate is also tried in its 8.3 short form, which keeps the same
    physical location when the long form is not ASCII. Falls back to
    ``preferred`` when nothing better exists; the engine's own error then
    names the path.
    """
    candidates = [
        preferred,
        Path(tempfile.gettempdir()) / "whisperjav" / name,
        exe.parent / f"_whisperjav_{name}",
    ]
    program_data = os.environ.get("ProgramData")
    if program_data:
        candidates.append(Path(program_data) / "WhisperJAV" / name)

    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if _engine_safe(cand):
            return cand
        short = _windows_short_path(cand)
        if short is not None and _engine_safe(short):
            return short
    return preferred


def _default_cache_dir(exe: Path) -> Path:
    """Model cache for the engine: WhisperJAV's cache namespace, engine-safe."""
    preferred = (
        Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
        / "whisperjav"
        / "crispasr_models"
    )
    return _engine_safe_dir(preferred, exe, "crispasr_models")


def _stage_for_engine(input_file: str, outbase: str, exe: Path):
    """Return ``(input, outbase, stage_dir)`` the engine can open.

    Paths that are already safe pass through with ``stage_dir=None``.
    Otherwise the input is hard-linked (or copied) into a fresh ASCII-named
    run directory, which the caller removes after moving the SRT back.
    """
    if _engine_safe(input_file) and _engine_safe(outbase):
        return input_file, outbase, None

    stage_root = _engine_safe_dir(Path(outbase).parent, exe, "crispasr_work")
    stage_dir = Path(tempfile.mkdtemp(prefix="run_", dir=str(stage_root)))
    staged_input = stage_dir / ("input" + Path(input_file).suffix)
    try:
        os.link(input_file, staged_input)
    except OSError:
        shutil.copy2(input_file, staged_input)
    return str(staged_input), str(stage_dir / "output"), stage_dir

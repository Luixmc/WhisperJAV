#!/usr/bin/env python3
"""CrispASR runner: argv per backend, and paths the engine can open.

The argv defaults were validated on a 20-minute JAV sample with CrispASR 0.8.38
(Vulkan build, AMD RX 9060 XT):

- ``-m auto:large-v2`` is not parsed by the engine and silently resolves to
  ggml-base, so ``whispercpp`` names its model with ``--hf-repo``.
- ``whispercpp`` without VAD looped one hallucinated line 247 times.
- ``qwen3-1.7b`` needs the Qwen3 forced aligner; ``-am auto`` picks canary's
  CTC aligner, which collapses on Japanese.
- The engine cannot open non-ASCII paths on Windows, so such inputs are run
  from an ASCII-named copy and the SRT is moved back.

Imports ``whisperjav.pipelines.crispasr_runner`` only — no engine binary is run.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from whisperjav.pipelines import crispasr_runner as runner
from whisperjav.pipelines.crispasr_runner import (
    CURATED_BACKENDS,
    build_crispasr_argv,
    run_crispasr,
)

EXE = Path("C:/tools/crispasr/crispasr.exe")


def _argv(backend, extra=""):
    return build_crispasr_argv(EXE, backend, "in.wav", "ja", "out/base", extra)


def _value_after(argv, flag):
    return argv[argv.index(flag) + 1]


class TestArgv:
    def test_fixed_io_flags(self):
        argv = _argv("parakeet")
        assert argv[0] == str(EXE)
        assert _value_after(argv, "-f") == "in.wav"
        assert _value_after(argv, "-l") == "ja"
        assert _value_after(argv, "-of") == "out/base"
        assert "-osrt" in argv

    @pytest.mark.parametrize("backend", ["parakeet", "cohere", "qwen3-1.7b"])
    def test_auto_model_backends(self, backend):
        argv = _argv(backend)
        assert _value_after(argv, "-m") == "auto"
        assert _value_after(argv, "--backend") == backend

    def test_whispercpp_names_its_model(self):
        argv = _argv("whispercpp")
        assert _value_after(argv, "--backend") == "whisper"
        assert "-m" not in argv, "-m auto:<variant> resolves to ggml-base"
        assert _value_after(argv, "--hf-repo").endswith("ggml-large-v3-q5_0.bin")

    def test_whispercpp_runs_with_vad(self):
        argv = _argv("whispercpp")
        assert "--vad" in argv
        assert _value_after(argv, "-vm") == "silero"

    def test_qwen3_uses_qwen3_aligner_not_auto(self):
        argv = _argv("qwen3-1.7b")
        assert _value_after(argv, "-am") == "qwen3-forced-aligner"
        assert "--vad" in argv
        assert "-sp" in argv

    @pytest.mark.parametrize("backend", ["parakeet", "cohere"])
    def test_native_timestamp_backends_get_no_extras(self, backend):
        argv = _argv(backend)
        assert "--vad" not in argv
        assert "-am" not in argv

    def test_qwen3_is_curated(self):
        assert "qwen3-1.7b" in CURATED_BACKENDS

    def test_user_args_come_last(self):
        argv = _argv("qwen3-1.7b", "-vt 0.3 --beam-size 5")
        assert argv[-4:] == ["-vt", "0.3", "--beam-size", "5"]

    @pytest.mark.parametrize("extra", [
        "-m C:/models/custom.gguf",
        "--model C:/models/custom.gguf",
        "--hf-repo someone/repo:model.bin",
    ])
    def test_user_model_replaces_default_model(self, extra):
        argv = _argv("whispercpp", extra)
        model_flags = [a for a in argv if a in ("-m", "--model", "--hf-repo", "-hfr")]
        assert len(model_flags) == 1
        assert "ggml-large-v3-q5_0.bin" not in " ".join(argv)


class TestEngineSafePaths:
    def test_ascii_is_safe(self):
        assert runner._engine_safe("C:/Users/Jose/a.wav")

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only engine limitation")
    @pytest.mark.parametrize("path", ["C:/Users/José/a.wav", "D:/媒体/SONE-853.wav"])
    def test_non_ascii_is_unsafe_on_windows(self, path):
        assert not runner._engine_safe(path)

    @pytest.mark.skipif(sys.platform == "win32", reason="non-Windows passthrough")
    def test_non_ascii_is_safe_elsewhere(self):
        assert runner._engine_safe("/home/josé/媒体.wav")

    def test_safe_paths_are_not_staged(self, tmp_path):
        wav = tmp_path / "in.wav"
        wav.write_bytes(b"RIFF")
        got = runner._stage_for_engine(str(wav), str(tmp_path / "out"), EXE)
        if runner._engine_safe(tmp_path):
            assert got == (str(wav), str(tmp_path / "out"), None)


def _fake_engine(seen):
    """subprocess.run stand-in: records argv, writes ``<-of>.srt``."""
    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env", {})
        seen["input_bytes"] = Path(cmd[cmd.index("-f") + 1]).read_bytes()
        outbase = cmd[cmd.index("-of") + 1]
        Path(outbase + ".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nはい\n\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return fake_run


class TestRunCrispasr:
    @pytest.fixture
    def exe(self, tmp_path):
        exe = tmp_path / "engine" / "crispasr.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        return exe

    def test_unknown_backend_rejected(self, exe, tmp_path):
        with pytest.raises(ValueError):
            run_crispasr(str(tmp_path / "a.wav"), str(exe), backend="canary")

    def test_non_ascii_input_runs_from_ascii_copy(self, exe, tmp_path, monkeypatch):
        media = tmp_path / "José 媒体"
        media.mkdir()
        wav = media / "SONE-853 日本語_extracted.wav"
        wav.write_bytes(b"RIFF....WAVE")
        out_dir = media / "crispasr_out"
        seen = {}
        monkeypatch.setattr(runner.subprocess, "run", _fake_engine(seen))
        monkeypatch.setattr(runner, "_engine_safe", lambda p: str(p).isascii())

        srt = run_crispasr(str(wav), str(exe), backend="qwen3-1.7b",
                           output_dir=str(out_dir))

        engine_input = seen["cmd"][seen["cmd"].index("-f") + 1]
        engine_outbase = seen["cmd"][seen["cmd"].index("-of") + 1]
        assert engine_input.isascii() and engine_outbase.isascii()
        assert seen["input_bytes"] == b"RIFF....WAVE"
        # The SRT lands where the caller expects it, under the original stem.
        assert srt == out_dir / "SONE-853 日本語_extracted.srt"
        assert srt.read_text(encoding="utf-8").strip().endswith("はい")
        # The ASCII staging directory is removed afterwards.
        assert not Path(engine_input).parent.exists()

    def test_ascii_input_runs_in_place(self, exe, tmp_path, monkeypatch):
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF")
        out_dir = tmp_path / "out"
        seen = {}
        monkeypatch.setattr(runner.subprocess, "run", _fake_engine(seen))
        monkeypatch.setattr(runner, "_engine_safe", lambda p: True)

        srt = run_crispasr(str(wav), str(exe), backend="whispercpp",
                           output_dir=str(out_dir))

        assert seen["cmd"][seen["cmd"].index("-f") + 1] == str(wav)
        assert srt == out_dir / "clip.srt"

    def test_cache_dir_env_is_respected(self, exe, tmp_path, monkeypatch):
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF")
        seen = {}
        monkeypatch.setattr(runner.subprocess, "run", _fake_engine(seen))
        monkeypatch.setenv("CRISPASR_CACHE_DIR", "D:/my_models")

        run_crispasr(str(wav), str(exe), output_dir=str(tmp_path / "out"))

        assert seen["env"]["CRISPASR_CACHE_DIR"] == "D:/my_models"

    def test_default_cache_dir_is_engine_safe(self, exe, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cachê"))
        monkeypatch.setattr(runner, "_engine_safe", lambda p: str(p).isascii())
        monkeypatch.setattr(runner, "_windows_short_path", lambda p: None)
        monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))

        cache = runner._default_cache_dir(exe)

        assert str(cache).isascii()
        assert cache.is_dir()

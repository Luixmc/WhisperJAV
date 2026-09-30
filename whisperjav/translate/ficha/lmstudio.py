"""Minimal client for the local LM Studio server (OpenAI-compatible API).

Every request asks for a JSON answer that follows a schema (structured
output), so the ficha, the notes and the translations come back as data
instead of prose to be parsed. If the server is down it is started with the
``lms`` CLI, and the model is loaded with the requested context when it is
not loaded yet: only one model is kept resident, the way Koharu's batch
tool does it.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

DEFAULT_URL = "http://localhost:1234"
DEFAULT_CONTEXT = 8192


class LMStudioError(RuntimeError):
    """The server or the model could not be used."""


def lms_executable() -> Optional[str]:
    """Path to the ``lms`` CLI, from PATH or LM Studio's default install."""
    found = shutil.which("lms")
    if found:
        return found
    name = "lms.exe" if os.name == "nt" else "lms"
    candidate = Path.home() / ".lmstudio" / "bin" / name
    return str(candidate) if candidate.is_file() else None


def extract_json(text: str) -> dict:
    """The JSON object in a model reply, tolerating think blocks and fences."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


class LMStudio:
    """One model on one LM Studio server."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: Optional[str] = None,
        context_length: int = DEFAULT_CONTEXT,
        timeout: float = 900,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_length = context_length
        self.timeout = timeout

    # -- server and model ---------------------------------------------------

    def _get(self, path: str) -> dict:
        response = requests.get(f"{self.base_url}{path}", timeout=10)
        response.raise_for_status()
        return response.json()

    def server_up(self) -> bool:
        try:
            self._get("/v1/models")
            return True
        except requests.RequestException:
            return False

    def _run_lms(self, *args: str) -> None:
        lms = lms_executable()
        if not lms:
            raise LMStudioError(
                "LM Studio's 'lms' CLI was not found. Start the server from the "
                "LM Studio app (Developer > Start server) and load a model."
            )
        result = subprocess.run(
            [lms, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise LMStudioError(
                f"lms {' '.join(args)} failed: {(result.stderr or result.stdout).strip()[-800:]}"
            )

    def models(self) -> List[Dict]:
        """Every model the server knows, with ``state`` loaded / not-loaded."""
        return self._get("/api/v0/models").get("data", [])

    def ensure_ready(self) -> str:
        """Start the server and load the model if needed; return the model id."""
        if not self.server_up():
            self._run_lms("server", "start")
            for _ in range(30):
                if self.server_up():
                    break
                time.sleep(1)
            else:
                raise LMStudioError(f"LM Studio server did not start at {self.base_url}")

        llms = [m for m in self.models() if m.get("type") in ("llm", "vlm")]
        loaded = [m["id"] for m in llms if m.get("state") == "loaded"]
        if self.model is None:
            if loaded:
                self.model = loaded[0]
                return self.model
            available = ", ".join(m["id"] for m in llms) or "(none)"
            raise LMStudioError(
                f"No model is loaded in LM Studio. Pass --modelo. Available: {available}"
            )
        if self.model not in loaded:
            known = [m["id"] for m in llms]
            if known and self.model not in known:
                raise LMStudioError(
                    f"LM Studio has no model '{self.model}'. Available: {', '.join(known)}"
                )
            # One resident model at a time: 16 GB of VRAM does not hold two.
            if loaded:
                self._run_lms("unload", "--all")
            self._run_lms(
                "load", self.model, "--context-length", str(self.context_length),
                "--parallel", "1", "-y",
            )
        return self.model

    def unload_all(self) -> bool:
        """Release every loaded model, freeing the VRAM; True if it was done."""
        if not self.server_up():
            return False
        self._run_lms("unload", "--all")
        return True

    # -- requests -----------------------------------------------------------

    def chat_json(
        self,
        system: str,
        user: str,
        schema_name: str,
        schema: dict,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        retries: int = 2,
        think: bool = False,
    ) -> dict:
        """Ask for a JSON answer that follows ``schema``.

        Reasoning is off unless ``think``: Gemma 4 otherwise spends most of its
        reply thinking, as Koharu found (``reasoning_effort`` in khr).
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "reasoning_effort": "medium" if think else "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        last_error: Optional[Exception] = None
        for _ in range(retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions", json=payload, timeout=self.timeout
                )
                response.raise_for_status()
                message = response.json()["choices"][0]["message"]
                content = message.get("content") or message.get("reasoning_content") or ""
                return extract_json(content)
            except (requests.RequestException, KeyError, ValueError) as error:
                last_error = error
        raise LMStudioError(f"{schema_name}: no valid answer from {self.model}: {last_error}")

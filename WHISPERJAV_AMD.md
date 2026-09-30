# WhisperJAV para AMD — brief para Claude Code

## Contexto
Fork de [meizhong986/WhisperJAV](https://github.com/meizhong986/WhisperJAV) (MIT). Hoy el instalador solo detecta NVIDIA (CUDA) o cae a CPU. Objetivo: que corra con aceleración en GPUs AMD, empezando por mi **RX 9060 XT (RDNA 4, gfx1200) en Windows 11**, y con un respaldo razonable para otras AMD.

## Stack objetivo (mi máquina)
- Windows 11, RX 9060 XT 16 GB
- **PyTorch 2.9 + ROCm 7.2.1**, Python **3.12**, wheels del índice de AMD (**TheRock**), NO los de `download.pytorch.org/whl/cu128`
- Verifica tú mismo la URL exacta del índice y la matriz de compatibilidad vigente antes de fijar versiones; no las inventes.
- ROCm se expone como `cuda` dentro de PyTorch (`torch.cuda.is_available()` → True, `torch.version.hip` no es None). Usa eso para distinguir AMD de NVIDIA.

## Qué funciona y qué no en AMD
| Componente | Estado en AMD | Decisión |
|---|---|---|
| ChronosJAV: Qwen3-ASR, anime-whisper | PyTorch puro → ROCm OK | **Ruta principal en AMD** |
| transformers (Kotoba, etc.) | PyTorch puro → ROCm OK | Soportado |
| balanced / faster (faster-whisper, CTranslate2) | Sin backend ROCm | CPU, o reemplazo por whisper.cpp |
| fidelity / fast (openai-whisper) | PyTorch → debería funcionar | Probar |
| Traducción (Ollama / llama-cpp) | Vulkan/ROCm OK | Sin cambios, solo verificar |
| Triton / Flash Attention / bitsandbytes | Frágiles en Windows | Evitar dependencias duras; fallback a SDPA estándar |

## Fases

### Fase 0 — Reconocimiento (no escribir código aún)
1. Mapear dónde se decide el dispositivo (`cuda`/`cpu`/`mps`) en todo el paquete `whisperjav/`.
2. Mapear el instalador: `install.py`, `installer/install_windows.bat`, `installer/install_linux.sh`, `pyproject.toml`, `uv.lock`. Encontrar la detección de GPU NVIDIA y la selección del índice de PyTorch.
3. Listar dependencias que traigan kernels CUDA compilados o paquetes `nvidia-*`.
4. Entregarme un resumen corto con archivos y líneas clave antes de pasar a la Fase 1.

### Fase 1 — Detección e instalación AMD
- Detectar GPU AMD en Windows (p. ej. `Get-CimInstance Win32_VideoController` o `wmic`) y en Linux (`rocminfo` / `lspci`).
- Si es RDNA 4 (gfx1200/gfx1201) en Windows → instalar PyTorch ROCm desde TheRock.
- Si es otra AMD en Windows → CPU para PyTorch + ofrecer backend Vulkan (Fase 3).
- Linux con AMD → PyTorch ROCm oficial.
- Flag manual: `--amd`, `--cpu-only` siguen funcionando; no romper la ruta NVIDIA.

### Fase 2 — Runtime
- Helper único de dispositivo (`get_device()` o similar) que reporte `nvidia`, `amd-rocm`, `mps`, `cpu`.
- En AMD: si el usuario elige un modo CTranslate2, avisar y sugerir `qwen`/`anime-whisper`, o correr en CPU.
- Default en AMD: ensemble anime-whisper + Qwen3-ASR (el default v1.9 ya es ChronosJAV).
- Log al inicio: GPU detectada, versión de torch, `torch.version.hip`, VRAM.

### Fase 3 — Backend whisper.cpp Vulkan (para AMD no-RDNA4 y como pase extra)
- Integrarlo como motor externo, siguiendo el patrón existente de `crispasr` / `xxl` (pase externo del ensemble).
- Parámetros: ruta al ejecutable, modelo GGUF (p. ej. large-v3-turbo Q5), idioma `ja`.

### Fase 4 — Pruebas
- Correr `python -m pytest tests/` y que no se rompa nada existente.
- Test de humo: clip japonés corto → `.srt` en modo `qwen` sobre ROCm.
- Medir tiempo por hora de audio en la 9060 XT (ROCm) vs CPU.

### Fase 5 — Traducción natural (japonés → español latino)
El objetivo es que suene como diálogo real escrito por un subtitulador, no como traducción literal línea a línea.

**Reconocimiento primero:** revisar cómo el proyecto usa PySubtrans (llm-subtrans) para traducir: tamaño de lote, prompt de sistema y cuánto contexto recibe cada línea. Resumirme eso antes de cambiar nada.

**Contexto:**
- Traducir por escenas o bloques (p. ej. 30–60 líneas), no línea por línea, con algunas líneas previas como contexto de solo lectura.
- Aprovechar los cortes de escena que ya genera el pipeline para armar los lotes.
- Resumen corto por archivo (personajes, relación, tono) que acompañe cada lote.

**Glosario y ficha por obra** (misma idea que mi fork de Koharu):
- Un archivo JSON/YAML opcional junto al video con nombres, cómo se dirigen entre sí, términos fijos y tono.
- Si no existe, generar un borrador automático en una primera pasada para que yo lo edite.

**Prompt de sistema con reglas de estilo:**
- Español latino neutro y coloquial, según el registro de cada personaje. Nada de español de manual.
- Honoríficos (-san, -chan, senpai): adaptarlos a cómo lo diría un hispanohablante; no dejarlos literales salvo que el glosario lo indique.
- Yakuwarigo y muletillas (ね, よ, わ): transmitir el tono, no traducir la partícula.
- Aizuchi (うん, はい, ええ) y exclamaciones: versión natural corta o eliminarlos si no aportan.
- Vocalizaciones y sonidos sin contenido: no traducirlos; dejarlos fuera o marcarlos para eliminar.
- Máximo 2 líneas por subtítulo, ~42 caracteres por línea; condensar si hace falta para que se pueda leer a tiempo.
- Mantener la numeración: mismo número de subtítulos de entrada y de salida, sin fusionar ni partir (los tiempos ya vienen del pipeline).

**Segunda pasada opcional ("pulido"):**
- El mismo modelo u otro revisa solo el español: naturalidad, consistencia con el glosario y longitud. Sin volver al japonés, salvo en líneas marcadas como dudosas.
- Activable con un flag (p. ej. `--polish`).

**Modelo local:**
- Ollama o llama.cpp con Vulkan/ROCm en 16 GB de VRAM. Hacer la elección de modelo configurable y comparar 2–3 candidatos sobre el mismo clip.
- Validar la salida: JSON o formato estricto, reintentar si faltan líneas o cambia el conteo.

**Evaluación:**
- Un clip de prueba con una traducción de referencia hecha a mano, y comparar literal vs. con contexto vs. con contexto + pulido.

## Reglas
- Cambios mínimos y localizados; no reescribir el núcleo del pipeline.
- No romper NVIDIA, Mac ni CPU.
- Cada fase en su propio commit/rama, con resumen de qué cambió.
- Si algo depende de una versión que no puedes verificar, pregúntame en vez de asumir.
- Ojo: ROCm en Windows puede requerir un driver AMD distinto al Adrenalin de juegos. Si lo detectas, avísame; no cambies drivers.

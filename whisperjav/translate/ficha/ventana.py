"""A small window over ``python -m whisperjav.translate.ficha traducir``.

Add videos, .srt files or folders, pick the model and the tone, and press
Traducir: each video is transcribed if it has no Japanese subtitles, each work
is studied if it has no notes, and each translation is written next to its
video with the video's name. The window only builds the command line and shows
its output; the work is the same as from traducir_es.bat.

    pythonw -m whisperjav.translate.ficha.ventana [videos, .srt o carpetas...]
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from whisperjav.translate.ficha.__main__ import VIDEOS
from whisperjav.translate.ficha.lmstudio import DEFAULT_URL, LMStudio

MODELO = "gemma-4-12b-it-qat"
TONOS = ["contextual", "standard", "pornify"]


def _python() -> str:
    """python.exe beside this interpreter, so the child's output reaches the
    window even when the window runs under pythonw.exe."""
    console = Path(sys.executable).with_name("python.exe")
    return str(console) if console.exists() else sys.executable


class Ventana:
    def __init__(self, root: tk.Tk, rutas):
        self.root = root
        self.proceso = None
        self.salida: "queue.Queue[str]" = queue.Queue()
        root.title("WhisperJAV — traducir al español")
        root.geometry("820x600")
        root.minsize(640, 460)

        archivos = ttk.LabelFrame(root, text="Videos, subtítulos o carpetas")
        archivos.pack(fill="x", padx=10, pady=(10, 5))
        self.lista = tk.Listbox(archivos, height=6, selectmode="extended")
        self.lista.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        botones = ttk.Frame(archivos)
        botones.pack(side="right", fill="y", padx=(4, 8), pady=8)
        ttk.Button(botones, text="Agregar archivos…", command=self.agregar_archivos).pack(fill="x")
        ttk.Button(botones, text="Agregar carpeta…", command=self.agregar_carpeta).pack(fill="x", pady=4)
        ttk.Button(botones, text="Quitar", command=self.quitar).pack(fill="x")
        ttk.Button(botones, text="Vaciar", command=lambda: self.lista.delete(0, "end")).pack(fill="x", pady=4)

        opciones = ttk.LabelFrame(root, text="Opciones")
        opciones.pack(fill="x", padx=10, pady=5)
        ttk.Label(opciones, text="Modelo de LM Studio").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.modelo = tk.StringVar(value=MODELO)
        self.modelos = ttk.Combobox(opciones, textvariable=self.modelo, width=36)
        self.modelos.grid(row=0, column=1, sticky="w", pady=6)
        ttk.Label(opciones, text="Tono").grid(row=0, column=2, sticky="w", padx=(16, 4))
        self.tono = tk.StringVar(value=TONOS[0])
        ttk.Combobox(opciones, textvariable=self.tono, values=TONOS, state="readonly", width=12).grid(
            row=0, column=3, sticky="w")
        ttk.Label(opciones, text="Líneas por lote").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.lote = tk.IntVar(value=25)
        ttk.Spinbox(opciones, from_=5, to=40, textvariable=self.lote, width=6).grid(row=1, column=1, sticky="w")
        self.estudiar = tk.BooleanVar(value=True)
        ttk.Checkbutton(opciones, text="Estudiar las obras sin ficha", variable=self.estudiar).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(16, 0))
        self.rehacer = tk.BooleanVar(value=False)
        ttk.Checkbutton(opciones, text="Volver a traducir lo ya traducido", variable=self.rehacer).grid(
            row=2, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=(0, 6))
        self.descargar = tk.BooleanVar(value=True)
        ttk.Checkbutton(opciones, text="Descargar el modelo al terminar", variable=self.descargar).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self.javdb = tk.BooleanVar(value=True)
        ttk.Checkbutton(opciones, text="Buscar título y etiquetas en JavDB", variable=self.javdb).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self.apagar = tk.BooleanVar(value=False)
        ttk.Checkbutton(opciones, text="Apagar la PC al terminar el lote", variable=self.apagar).grid(
            row=3, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=(0, 6))

        acciones = ttk.Frame(root)
        acciones.pack(fill="x", padx=10, pady=5)
        self.boton = ttk.Button(acciones, text="Traducir", command=self.traducir)
        self.boton.pack(side="left")
        self.detener = ttk.Button(acciones, text="Detener", command=self.parar, state="disabled")
        self.detener.pack(side="left", padx=6)
        self.estado = ttk.Label(acciones, text="")
        self.estado.pack(side="left", padx=10)

        registro = ttk.LabelFrame(root, text="Progreso")
        registro.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.texto = tk.Text(registro, wrap="word", height=12, state="disabled")
        barra = ttk.Scrollbar(registro, command=self.texto.yview)
        self.texto.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y", pady=8)
        self.texto.pack(fill="both", expand=True, padx=(8, 0), pady=8)

        for ruta in rutas:
            self.lista.insert("end", str(Path(ruta)))
        threading.Thread(target=self.cargar_modelos, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.root.after(100, self.leer)

    # -- files ---------------------------------------------------------------

    def agregar_archivos(self):
        tipos = " ".join(f"*{ext}" for ext in sorted(VIDEOS))
        for ruta in filedialog.askopenfilenames(
            title="Videos o subtítulos en japonés",
            filetypes=[("Videos y subtítulos", f"{tipos} *.srt"), ("Todos", "*.*")],
        ):
            self.lista.insert("end", ruta)

    def agregar_carpeta(self):
        ruta = filedialog.askdirectory(title="Carpeta con videos o subtítulos")
        if ruta:
            self.lista.insert("end", ruta)

    def quitar(self):
        for index in reversed(self.lista.curselection()):
            self.lista.delete(index)

    def cargar_modelos(self):
        try:
            nombres = [m["id"] for m in LMStudio(DEFAULT_URL).models() if m.get("type") in ("llm", "vlm")]
        except Exception:
            return
        if nombres:
            self.root.after(0, lambda: self.modelos.configure(values=nombres))

    # -- running -------------------------------------------------------------

    def escribir(self, linea: str):
        self.texto.configure(state="normal")
        self.texto.insert("end", linea)
        self.texto.see("end")
        self.texto.configure(state="disabled")

    def traducir(self):
        rutas = list(self.lista.get(0, "end"))
        if not rutas:
            messagebox.showinfo("Traducir", "Agrega al menos un video, un .srt o una carpeta.")
            return
        command = [
            _python(), "-m", "whisperjav.translate.ficha", "traducir", *rutas,
            "--tono", self.tono.get(), "--lote", str(self.lote.get()),
        ]
        if self.modelo.get().strip():
            command += ["--modelo", self.modelo.get().strip()]
        if not self.estudiar.get():
            command.append("--sin-estudio")
        if self.rehacer.get():
            command.append("--rehacer")
        if not self.descargar.get():
            command.append("--mantener-modelo")
        if not self.javdb.get():
            command.append("--sin-javdb")
        if self.apagar.get():
            command.append("--apagar")
        self.escribir("\n> " + " ".join(command[2:]) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proceso = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self.bombear, args=(self.proceso,), daemon=True).start()
        self.boton.configure(state="disabled")
        self.detener.configure(state="normal")
        self.estado.configure(text="Trabajando…")

    def bombear(self, proceso):
        for linea in proceso.stdout:
            self.salida.put(linea)
        proceso.wait()
        self.salida.put(None)

    def leer(self):
        try:
            while True:
                linea = self.salida.get_nowait()
                if linea is None:
                    codigo = self.proceso.returncode if self.proceso else 0
                    self.proceso = None
                    self.boton.configure(state="normal")
                    self.detener.configure(state="disabled")
                    self.estado.configure(text="Listo" if codigo == 0 else "Terminó con errores (mira el progreso)")
                else:
                    self.escribir(linea)
        except queue.Empty:
            pass
        self.root.after(100, self.leer)

    def parar(self):
        if not self.proceso:
            return
        # The translation runs in child processes; stop the whole tree.
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proceso.pid)],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            self.proceso.terminate()
        self.estado.configure(text="Detenido")

    def cerrar(self):
        if self.proceso and not messagebox.askyesno("Salir", "Hay una traducción en curso. ¿Detenerla y salir?"):
            return
        self.parar()
        self.root.destroy()


def main(argv=None):
    root = tk.Tk()
    Ventana(root, sys.argv[1:] if argv is None else argv)
    root.mainloop()


if __name__ == "__main__":
    main()

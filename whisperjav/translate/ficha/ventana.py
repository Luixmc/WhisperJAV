"""A small window over ``python -m whisperjav.translate.ficha``.

Add videos, .srt files or folders, pick the model and the tone, and add the
work to the queue with Convertir (.ts to .mp4 without re-encoding), Traducir or
Convertir y traducir. Translating transcribes each video that has no Japanese
subtitles, studies each work that has no notes and writes each translation
next to its video with the video's name. The queue runs one job after another,
each with the files and options it was added with; Detener stops the job in
course and pauses the queue until Reanudar. The window only builds the command
lines and shows their output; the work is the same as from traducir_es.bat.

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

from whisperjav.translate.ficha.__main__ import TONOS, VIDEOS, apagar
from whisperjav.translate.ficha.lmstudio import DEFAULT_URL, LMStudio

MODELO = "gemma-4-12b-it-qat"
ACCIONES = {
    "convertir": "Convertir",
    "traducir": "Traducir",
    "ambos": "Convertir y traducir",
}


def _python() -> str:
    """python.exe beside this interpreter, so the child's output reaches the
    window even when the window runs under pythonw.exe."""
    console = Path(sys.executable).with_name("python.exe")
    return str(console) if console.exists() else sys.executable


class Ventana:
    def __init__(self, root: tk.Tk, rutas):
        self.root = root
        self.proceso = None
        self.actual = None
        self.pendientes = []
        self.pausada = False
        self.detenido = False
        self.salida: "queue.Queue[str]" = queue.Queue()
        root.title("WhisperJAV — convertir y traducir al español")
        root.geometry("860x720")
        root.minsize(680, 560)

        archivos = ttk.LabelFrame(root, text="Videos, subtítulos o carpetas")
        archivos.pack(fill="x", padx=10, pady=(10, 5))
        self.lista = tk.Listbox(archivos, height=5, selectmode="extended")
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
        self.tono = tk.StringVar(value="contextual")
        ttk.Combobox(opciones, textvariable=self.tono, values=list(TONOS), state="readonly", width=12).grid(
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
        ttk.Checkbutton(opciones, text="Apagar la PC al terminar la cola", variable=self.apagar).grid(
            row=3, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=(0, 6))

        acciones = ttk.Frame(root)
        acciones.pack(fill="x", padx=10, pady=5)
        ttk.Label(acciones, text="Añadir a la cola:").pack(side="left")
        for accion, texto in ACCIONES.items():
            ttk.Button(acciones, text=texto, command=lambda a=accion: self.encolar(a)).pack(side="left", padx=(6, 0))

        cola = ttk.LabelFrame(root, text="Cola")
        cola.pack(fill="x", padx=10, pady=5)
        self.vista = tk.Listbox(cola, height=5, selectmode="extended")
        self.vista.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        mandos = ttk.Frame(cola)
        mandos.pack(side="right", fill="y", padx=(4, 8), pady=8)
        self.detener = ttk.Button(mandos, text="Detener", command=self.parar, state="disabled")
        self.detener.pack(fill="x")
        self.reanudar = ttk.Button(mandos, text="Reanudar", command=self.seguir, state="disabled")
        self.reanudar.pack(fill="x", pady=4)
        ttk.Button(mandos, text="Quitar de la cola", command=self.desencolar).pack(fill="x")
        self.estado = ttk.Label(mandos, text="", wraplength=150)
        self.estado.pack(fill="x", pady=(8, 0))

        registro = ttk.LabelFrame(root, text="Progreso")
        registro.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.texto = tk.Text(registro, wrap="word", height=10, state="disabled")
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

    # -- queue ---------------------------------------------------------------

    def encolar(self, accion: str):
        rutas = list(self.lista.get(0, "end"))
        if not rutas:
            messagebox.showinfo(ACCIONES[accion], "Agrega al menos un video, un .srt o una carpeta.")
            return
        # Each job keeps the options it was added with.
        trabajo = {
            "accion": accion, "rutas": rutas, "tono": self.tono.get(), "lote": self.lote.get(),
            "modelo": self.modelo.get().strip(), "estudiar": self.estudiar.get(),
            "rehacer": self.rehacer.get(), "descargar": self.descargar.get(), "javdb": self.javdb.get(),
        }
        self.pendientes.append(trabajo)
        self.lista.delete(0, "end")
        self.escribir(f"\n+ a la cola: {self.titulo(trabajo)}\n")
        self.mostrar_cola()
        if not self.proceso and not self.pausada:
            self.siguiente()

    def titulo(self, trabajo) -> str:
        nombres = [Path(r).name or r for r in trabajo["rutas"]]
        archivos = ", ".join(nombres[:3]) + (f" y {len(nombres) - 3} más" if len(nombres) > 3 else "")
        extra = f" ({trabajo['tono']})" if trabajo["accion"] != "convertir" else ""
        return f"{ACCIONES[trabajo['accion']]}{extra}: {archivos}"

    def mostrar_cola(self):
        self.vista.delete(0, "end")
        if self.actual:
            self.vista.insert("end", "▶ " + self.titulo(self.actual))
        for trabajo in self.pendientes:
            self.vista.insert("end", "   " + self.titulo(trabajo))
        self.reanudar.configure(state="normal" if self.pausada and self.pendientes else "disabled")
        if self.proceso:
            self.estado.configure(text=f"Trabajando… ({len(self.pendientes)} en espera)")
        elif self.pausada and self.pendientes:
            self.estado.configure(text=f"Cola en pausa ({len(self.pendientes)} en espera)")

    def desencolar(self):
        # The job in course is stopped with Detener, not removed from here.
        desfase = 1 if self.actual else 0
        for index in reversed(self.vista.curselection()):
            if index >= desfase:
                del self.pendientes[index - desfase]
        self.mostrar_cola()

    def comando(self, trabajo) -> list:
        if trabajo["accion"] == "convertir":
            return [_python(), "-m", "whisperjav.translate.ficha", "convertir", *trabajo["rutas"]]
        command = [
            _python(), "-m", "whisperjav.translate.ficha", "traducir", *trabajo["rutas"],
            "--tono", trabajo["tono"], "--lote", str(trabajo["lote"]),
        ]
        if trabajo["accion"] == "ambos":
            command.append("--convertir")
        if trabajo["modelo"]:
            command += ["--modelo", trabajo["modelo"]]
        if not trabajo["estudiar"]:
            command.append("--sin-estudio")
        if trabajo["rehacer"]:
            command.append("--rehacer")
        # Keep the model loaded when another translation waits in the queue.
        otra = any(t["accion"] != "convertir" for t in self.pendientes)
        if not trabajo["descargar"] or otra:
            command.append("--mantener-modelo")
        if not trabajo["javdb"]:
            command.append("--sin-javdb")
        return command

    def siguiente(self):
        if not self.pendientes:
            self.actual = None
            self.mostrar_cola()
            if self.apagar.get() and not self.detenido:
                self.escribir("\nCola terminada: apagando la PC.\n")
                threading.Thread(target=apagar, daemon=True).start()
            return
        self.actual = self.pendientes.pop(0)
        self.detenido = False
        command = self.comando(self.actual)
        self.escribir(f"\n=== {self.titulo(self.actual)}\n> " + " ".join(command[2:]) + "\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proceso = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self.bombear, args=(self.proceso,), daemon=True).start()
        self.detener.configure(state="normal")
        self.mostrar_cola()

    # -- running -------------------------------------------------------------

    def escribir(self, linea: str):
        self.texto.configure(state="normal")
        self.texto.insert("end", linea)
        self.texto.see("end")
        self.texto.configure(state="disabled")

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
                    self.terminado()
                else:
                    self.escribir(linea)
        except queue.Empty:
            pass
        self.root.after(100, self.leer)

    def terminado(self):
        codigo = self.proceso.returncode if self.proceso else 0
        self.proceso = None
        self.detener.configure(state="disabled")
        if self.detenido:
            self.escribir("\n(detenido; la cola queda en pausa)\n")
            self.actual = None
            self.estado.configure(text="Detenido")
            self.mostrar_cola()
            return
        if codigo != 0:
            self.escribir("\n(terminó con errores; la cola sigue)\n")
        self.estado.configure(text="Listo" if codigo == 0 else "Hubo errores (mira el progreso)")
        self.siguiente()

    def parar(self):
        if not self.proceso:
            return
        self.detenido = True
        self.pausada = True
        # The work runs in child processes; stop the whole tree.
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proceso.pid)],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            self.proceso.terminate()

    def seguir(self):
        self.pausada = False
        if not self.proceso:
            self.siguiente()

    def cerrar(self):
        if (self.proceso or self.pendientes) and not messagebox.askyesno(
                "Salir", "Hay trabajos en curso o en la cola. ¿Detenerlos y salir?"):
            return
        self.pendientes.clear()
        self.parar()
        self.root.destroy()


def main(argv=None):
    root = tk.Tk()
    Ventana(root, sys.argv[1:] if argv is None else argv)
    root.mainloop()


if __name__ == "__main__":
    main()

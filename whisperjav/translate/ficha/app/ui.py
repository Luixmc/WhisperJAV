"""The desktop app: a translation queue with a card per work, and the library
of what is already translated.

    pythonw -m whisperjav.translate.ficha.app [videos, .srt o carpetas...]
"""

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image

from whisperjav.translate.ficha.__main__ import TONOS, VIDEOS, apagar
from whisperjav.translate.ficha.app import biblioteca
from whisperjav.translate.ficha.app.cola import ACCIONES, NOMBRE_ETAPA, Cola, Trabajo, videos_en
from whisperjav.translate.ficha.lmstudio import DEFAULT_URL, LMStudio

MODELO = "gemma-4-12b-it-qat"
VERDE, NARANJA, GRIS, ROJO = "#3fa66b", "#d98a2b", "#5a5a5a", "#c0504d"
MINI = (150, 213)  # cover thumbnails, a DMM front's proportions
POR_PAGINA = 60
ESTADOS = {"espera": "En cola", "trabajando": "Trabajando", "listo": "✓ Listo", "error": "✗ Error"}


def abrir(ruta: Path) -> None:
    if os.name == "nt":
        os.startfile(str(ruta))  # noqa: S606 - the user's own files
    else:
        subprocess.Popen(["xdg-open", str(ruta)])


# -- translation queue ---------------------------------------------------------


class Tarjeta(ctk.CTkFrame):
    def __init__(self, master, app: "App", t: Trabajo):
        super().__init__(master, corner_radius=8)
        self.app, self.t = app, t
        self.grid_columnconfigure(1, weight=1)
        self.nombre = ctk.CTkLabel(self, text=t.nombre, font=ctk.CTkFont(size=15, weight="bold"), anchor="w")
        self.nombre.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(8, 0))
        self.accion = ctk.CTkLabel(self, text=ACCIONES[t.accion], text_color=GRIS, anchor="w")
        self.accion.grid(row=0, column=1, sticky="w", pady=(8, 0))
        self.estado = ctk.CTkLabel(self, text="", anchor="e")
        self.estado.grid(row=0, column=2, sticky="e", padx=12, pady=(8, 0))

        self.chips = ctk.CTkFrame(self, fg_color="transparent")
        self.chips.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        self.etiquetas: Dict[str, ctk.CTkLabel] = {}
        for etapa in t.etapas:
            l = ctk.CTkLabel(self.chips, text=NOMBRE_ETAPA[etapa], corner_radius=6, fg_color=GRIS,
                             text_color="white", padx=8)
            l.pack(side="left", padx=(0, 6))
            self.etiquetas[etapa] = l

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=1, column=2, sticky="e", padx=12)
        self.b_registro = ctk.CTkButton(botones, text="Registro", width=80, fg_color=GRIS,
                                        command=lambda: app.ver_registro(t))
        self.b_registro.pack(side="left", padx=(0, 6))
        self.b_carpeta = ctk.CTkButton(botones, text="Abrir carpeta", width=100,
                                       command=lambda: abrir(t.destino or t.ruta.parent))
        self.b_quitar = ctk.CTkButton(botones, text="Quitar", width=70, fg_color=ROJO,
                                      command=lambda: app.quitar(t))
        self.b_quitar.pack(side="left")
        self.b_otra = ctk.CTkButton(botones, text="Reintentar", width=90, command=lambda: app.reintentar(t))

        self.barra = ctk.CTkProgressBar(self, mode="indeterminate", height=6)
        self.mensaje = ctk.CTkLabel(self, text="", text_color=GRIS, anchor="w", wraplength=700, justify="left")
        self.mensaje.grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 8))
        self.actualizar()

    def actualizar(self):
        t = self.t
        color = {"listo": VERDE, "error": ROJO, "trabajando": NARANJA}.get(t.estado)
        texto = ESTADOS[t.estado]
        if t.estado == "trabajando" and t.etapa:
            texto = f"{NOMBRE_ETAPA[t.etapa]}…"
        self.estado.configure(text=texto, text_color=color or ("gray10", "gray90"))
        for etapa, l in self.etiquetas.items():
            l.configure(fg_color=VERDE if etapa in t.hechas else NARANJA if etapa == t.etapa else GRIS)
        if t.estado == "trabajando":
            self.barra.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 4))
            self.barra.start()
        else:
            self.barra.stop()
            self.barra.grid_forget()
        self.mensaje.configure(text=t.mensaje)
        for b in (self.b_quitar, self.b_carpeta, self.b_otra):
            b.pack_forget()
        if t.estado == "espera":
            self.b_quitar.pack(side="left")
        if t.estado in ("listo", "error"):
            self.b_carpeta.pack(side="left", padx=(0, 6))
        if t.estado == "error":
            self.b_otra.pack(side="left")


class PestanaTraducir(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        fila = ctk.CTkFrame(self)
        fila.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(fila, text="+ Archivos", width=100, command=self.agregar_archivos).pack(side="left", padx=8, pady=8)
        ctk.CTkButton(fila, text="+ Carpeta", width=100, command=self.agregar_carpeta).pack(side="left", pady=8)
        ctk.CTkLabel(fila, text="Al agregar:").pack(side="left", padx=(16, 6))
        self.accion = ctk.CTkSegmentedButton(fila, values=list(ACCIONES.values()))
        self.accion.set(ACCIONES["ambos"])
        self.accion.pack(side="left", pady=8)

        op = ctk.CTkFrame(self)
        op.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(op, text="Modelo").grid(row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        self.modelo = ctk.CTkComboBox(op, values=[MODELO], width=260)
        self.modelo.set(MODELO)
        self.modelo.grid(row=0, column=1, pady=8, sticky="w")
        ctk.CTkLabel(op, text="Tono").grid(row=0, column=2, padx=(16, 4))
        self.tono = ctk.CTkOptionMenu(op, values=list(TONOS), width=130)
        self.tono.set("contextual")
        self.tono.grid(row=0, column=3)
        ctk.CTkLabel(op, text="Líneas por lote").grid(row=0, column=4, padx=(16, 4))
        self.lote = ctk.CTkEntry(op, width=50)
        self.lote.insert(0, "16")
        self.lote.grid(row=0, column=5, padx=(0, 8))

        self.estudiar = ctk.BooleanVar(value=True)
        self.javdb = ctk.BooleanVar(value=True)
        self.rehacer = ctk.BooleanVar(value=False)
        self.descargar = ctk.BooleanVar(value=True)
        self.apagar = ctk.BooleanVar(value=False)
        marcas = [("Estudiar obras sin ficha", self.estudiar), ("Buscar datos en r18.dev/JavDB", self.javdb),
                  ("Volver a traducir lo ya traducido", self.rehacer),
                  ("Descargar el modelo al terminar", self.descargar), ("Apagar la PC al terminar", self.apagar)]
        for i, (texto, var) in enumerate(marcas):
            ctk.CTkCheckBox(op, text=texto, variable=var).grid(
                row=1 + i // 3, column=(i % 3) * 2, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        mandos = ctk.CTkFrame(self, fg_color="transparent")
        mandos.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.b_detener = ctk.CTkButton(mandos, text="Detener", width=90, fg_color=ROJO, command=self.detener)
        self.b_detener.pack(side="left")
        self.b_reanudar = ctk.CTkButton(mandos, text="Reanudar", width=90, command=self.reanudar)
        self.b_reanudar.pack(side="left", padx=6)
        ctk.CTkButton(mandos, text="Limpiar terminadas", width=140, fg_color=GRIS,
                      command=self.limpiar).pack(side="left")
        self.resumen = ctk.CTkLabel(mandos, text="", text_color=GRIS)
        self.resumen.pack(side="right", padx=8)

        self.lista = ctk.CTkScrollableFrame(self, label_text="")
        self.lista.grid(row=3, column=0, sticky="nsew")
        self.lista.grid_columnconfigure(0, weight=1)
        self.vacia = ctk.CTkLabel(self.lista, text="Agrega videos, .srt o carpetas para empezar.\n"
                                  "También puedes arrastrarlos sobre traducir_app.bat.", text_color=GRIS)
        self.vacia.grid(row=0, column=0, pady=40)
        self.tarjetas: Dict[int, Tarjeta] = {}

    def opciones(self) -> Dict:
        try:
            lote = max(5, min(40, int(self.lote.get())))
        except ValueError:
            lote = 16
        return {"modelo": self.modelo.get().strip(), "tono": self.tono.get(), "lote": lote,
                "estudiar": self.estudiar.get(), "javdb": self.javdb.get(), "rehacer": self.rehacer.get(),
                "descargar": self.descargar.get()}

    def accion_elegida(self) -> str:
        return next(k for k, v in ACCIONES.items() if v == self.accion.get())

    def encolar(self, rutas: List[Path], accion: Optional[str] = None, opciones: Optional[Dict] = None):
        accion = accion or self.accion_elegida()
        opciones = opciones or self.opciones()
        en_cola = {t.ruta.resolve() for t in self.app.todos()}
        nuevos = []
        for ruta in rutas:
            for r in videos_en(ruta):
                if accion == "convertir" and r.suffix.lower() != ".ts":
                    continue
                if r.resolve() not in en_cola:
                    nuevos.append(Trabajo(r, accion, dict(opciones)))
                    en_cola.add(r.resolve())
        if not nuevos:
            messagebox.showinfo("Agregar", "No hay videos nuevos para esa acción en lo que elegiste.")
            return
        self.app.cola.agregar(nuevos)

    def agregar_archivos(self):
        tipos = " ".join(f"*{e}" for e in sorted(VIDEOS))
        rutas = filedialog.askopenfilenames(title="Videos o subtítulos en japonés",
                                            filetypes=[("Videos y subtítulos", f"{tipos} *.srt"), ("Todos", "*.*")])
        if rutas:
            self.encolar([Path(r) for r in rutas])

    def agregar_carpeta(self):
        ruta = filedialog.askdirectory(title="Carpeta con videos")
        if ruta:
            self.encolar([Path(ruta)])

    def mostrar(self, t: Trabajo):
        tarjeta = self.tarjetas.get(t.id)
        if not tarjeta:
            self.vacia.grid_forget()
            tarjeta = Tarjeta(self.lista, self.app, t)
            tarjeta.grid(row=len(self.tarjetas) + 1, column=0, sticky="ew", pady=4, padx=4)
            self.tarjetas[t.id] = tarjeta
        else:
            tarjeta.actualizar()
        self.resumir()

    def borrar(self, trabajo_id: int):
        tarjeta = self.tarjetas.pop(trabajo_id, None)
        if tarjeta:
            tarjeta.destroy()
        if not self.tarjetas:
            self.vacia.grid(row=0, column=0, pady=40)
        self.resumir()

    def limpiar(self):
        for tid, tarjeta in list(self.tarjetas.items()):
            if tarjeta.t.estado in ("listo", "error"):
                self.borrar(tid)

    def resumir(self):
        cola = self.app.cola
        n = len(cola.pendientes)
        if cola.ocupada:
            texto = f"Trabajando · {n} en espera"
        elif cola.pausada and n:
            texto = f"En pausa · {n} en espera"
        else:
            texto = "Sin trabajo"
        self.resumen.configure(text=texto)
        self.b_detener.configure(state="normal" if cola.ocupada else "disabled")
        self.b_reanudar.configure(state="normal" if cola.pausada and n else "disabled")

    def detener(self):
        self.app.cola.detener()
        self.resumir()

    def reanudar(self):
        self.app.cola.reanudar()
        self.resumir()


# -- library -------------------------------------------------------------------


class PestanaBiblioteca(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.entradas: List[biblioteca.Entrada] = []
        self.mostradas = 0
        self.imagenes = {}  # keep CTkImage objects alive
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        barra = ctk.CTkFrame(self)
        barra.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.buscar = ctk.CTkEntry(barra, placeholder_text="Buscar código, título, actriz, serie o etiqueta…",
                                   width=380)
        self.buscar.pack(side="left", padx=8, pady=8)
        self.buscar.bind("<KeyRelease>", lambda _e: self.pintar())
        ctk.CTkButton(barra, text="Actualizar", width=90, command=self.cargar).pack(side="left")
        ctk.CTkButton(barra, text="Abrir D:\\J", width=90, fg_color=GRIS,
                      command=lambda: abrir(app.cola.raiz)).pack(side="left", padx=6)
        self.cuenta = ctk.CTkLabel(barra, text="", text_color=GRIS)
        self.cuenta.pack(side="right", padx=8)

        self.rejilla = ctk.CTkScrollableFrame(self)
        self.rejilla.grid(row=1, column=0, sticky="nsew")
        self.columnas = 5
        self.rejilla.bind("<Configure>", self._ancho)

    def _ancho(self, evento):
        columnas = max(2, evento.width // (MINI[0] + 24))
        if columnas != self.columnas:
            self.columnas = columnas
            self.pintar()

    def cargar(self):
        self.cuenta.configure(text="Leyendo…")

        def leer():
            entradas = biblioteca.listar(self.app.cola.raiz)
            self.after(0, lambda: self._cargadas(entradas))
        threading.Thread(target=leer, daemon=True).start()

    def _cargadas(self, entradas):
        self.entradas = entradas
        self.pintar()

    def filtradas(self) -> List[biblioteca.Entrada]:
        q = self.buscar.get().strip().lower()
        if not q:
            return self.entradas
        palabras = q.split()
        return [e for e in self.entradas if all(p in e.texto_busqueda() for p in palabras)]

    def pintar(self, mas: bool = False):
        lista = self.filtradas()
        if not mas:
            for hijo in self.rejilla.winfo_children():
                hijo.destroy()
            self.mostradas = 0
        else:
            for hijo in self.rejilla.winfo_children():
                if getattr(hijo, "_es_mas", False):
                    hijo.destroy()
        total = len(self.entradas)
        self.cuenta.configure(text=f"{len(lista)} de {total} obras traducidas" if lista != self.entradas
                              else f"{total} obras traducidas")
        if not lista:
            ctk.CTkLabel(self.rejilla, text="Aún no hay obras traducidas en la biblioteca." if not total
                         else "Nada coincide con la búsqueda.", text_color=GRIS).grid(row=0, column=0, pady=40)
            return
        hasta = min(len(lista), self.mostradas + POR_PAGINA)
        for i in range(self.mostradas, hasta):
            self._tarjeta(lista[i], i)
        self.mostradas = hasta
        if hasta < len(lista):
            b = ctk.CTkButton(self.rejilla, text=f"Mostrar más ({len(lista) - hasta})",
                              command=lambda: self.pintar(mas=True))
            b._es_mas = True
            b.grid(row=hasta // self.columnas + 1, column=0, columnspan=self.columnas, pady=12)

    def _tarjeta(self, e: biblioteca.Entrada, i: int):
        marco = ctk.CTkFrame(self.rejilla, corner_radius=8)
        marco.grid(row=i // self.columnas, column=i % self.columnas, padx=6, pady=6, sticky="n")
        foto = ctk.CTkLabel(marco, text="…", width=MINI[0], height=MINI[1], fg_color=GRIS, corner_radius=6)
        foto.pack(padx=6, pady=(6, 2))
        titulo = e.titulo
        corto = (titulo[:22] + "…") if len(titulo) > 23 else titulo
        ctk.CTkLabel(marco, text=e.codigo, font=ctk.CTkFont(weight="bold")).pack()
        ctk.CTkLabel(marco, text=corto, text_color=GRIS, wraplength=MINI[0]).pack(padx=6, pady=(0, 6))
        for w in (marco, foto):
            w.bind("<Button-1>", lambda _ev, e=e: Ficha(self, self.app, e))
        self._cargar_foto(e, foto, MINI)

    def _cargar_foto(self, e: biblioteca.Entrada, label, tam):
        clave = (str(e.portada), tam)
        if clave in self.imagenes:
            label.configure(image=self.imagenes[clave], text="")
            return
        if not e.portada:
            label.configure(text="sin carátula")
            return

        def leer():
            try:
                im = Image.open(e.portada).convert("RGB")
                im.thumbnail(tam)
            except OSError:
                return
            self.after(0, lambda: self._poner(label, clave, im))
        threading.Thread(target=leer, daemon=True).start()

    def _poner(self, label, clave, im):
        if not label.winfo_exists():
            return
        img = ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
        self.imagenes[clave] = img
        label.configure(image=img, text="")


class Ficha(ctk.CTkToplevel):
    def __init__(self, pestana: PestanaBiblioteca, app: "App", e: biblioteca.Entrada):
        super().__init__(app)
        self.e, self.app, self.pestana = e, app, pestana
        self.title(e.codigo)
        self.geometry("820x560")
        self.after(50, self.lift)
        self.grid_columnconfigure(1, weight=1)
        foto = ctk.CTkLabel(self, text="", width=340, height=480)
        foto.grid(row=0, column=0, rowspan=2, padx=16, pady=16, sticky="n")
        pestana._cargar_foto(e, foto, (340, 484))

        m = e.meta
        datos = ctk.CTkScrollableFrame(self, fg_color="transparent")
        datos.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=16)
        self.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(datos, text=e.codigo, font=ctk.CTkFont(size=22, weight="bold"), anchor="w").pack(fill="x")
        for texto, tamano in ((m.get("titulo", ""), 14), (m.get("titulo_en", ""), 12)):
            if texto:
                ctk.CTkLabel(datos, text=texto, wraplength=420, justify="left", anchor="w",
                             font=ctk.CTkFont(size=tamano)).pack(fill="x", pady=(6, 0))
        filas = [("Actrices", ", ".join(m.get("actrices", []))), ("Serie", m.get("serie", "")),
                 ("Estudio", m.get("estudio", "")), ("Fecha", m.get("fecha", "")),
                 ("Etiquetas", ", ".join(m.get("etiquetas", []))), ("Subtítulo", e.subtitulo.name),
                 ("Datos de", m.get("fuente", "") or "— (sin datos; prueba «Buscar datos»)")]
        for nombre, valor in filas:
            if valor:
                ctk.CTkLabel(datos, text=f"{nombre}: {valor}", wraplength=420, justify="left", anchor="w",
                             text_color=("gray20", "gray75")).pack(fill="x", pady=(8, 0))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=1, column=1, sticky="sew", padx=(0, 16), pady=(0, 16))
        ctk.CTkButton(botones, text="▶ Reproducir", command=lambda: abrir(e.video)).pack(side="left")
        ctk.CTkButton(botones, text="Abrir carpeta", fg_color=GRIS,
                      command=lambda: abrir(e.carpeta)).pack(side="left", padx=6)
        self.b_datos = ctk.CTkButton(botones, text="Buscar datos", fg_color=GRIS, command=self.buscar_datos)
        self.b_datos.pack(side="left")
        ctk.CTkButton(botones, text="Volver a traducir", fg_color=NARANJA,
                      command=self.retraducir).pack(side="left", padx=6)

    def buscar_datos(self):
        self.b_datos.configure(state="disabled", text="Buscando…")

        def correr():
            biblioteca.completar(self.e.carpeta, forzar=True)
            self.after(0, self._datos_listos)
        threading.Thread(target=correr, daemon=True).start()

    def _datos_listos(self):
        self.pestana.imagenes = {k: v for k, v in self.pestana.imagenes.items() if k[0] != str(self.e.portada)}
        self.destroy()
        self.pestana.cargar()

    def retraducir(self):
        if not messagebox.askyesno("Volver a traducir",
                                   f"¿Traducir {self.e.codigo} de nuevo? La traducción actual se reemplaza.",
                                   parent=self):
            return
        opciones = dict(self.app.traducir.opciones(), rehacer=True)
        self.app.traducir.encolar([self.e.video], accion="traducir", opciones=opciones)
        self.app.tabs.set("Traducir")
        self.destroy()


# -- window --------------------------------------------------------------------


class App(ctk.CTk):
    def __init__(self, rutas: List[str]):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("WhisperJAV")
        self.geometry("1100x780")
        self.minsize(820, 600)
        self.cola = Cola()
        self.registros: Dict[int, ctk.CTkTextbox] = {}

        cabeza = ctk.CTkFrame(self, fg_color="transparent")
        cabeza.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(cabeza, text="WhisperJAV", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        self.lm = ctk.CTkLabel(cabeza, text="LM Studio: …", text_color=GRIS)
        self.lm.pack(side="right")

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.traducir = PestanaTraducir(self.tabs.add("Traducir"), self)
        self.traducir.pack(fill="both", expand=True)
        self.biblio = PestanaBiblioteca(self.tabs.add("Biblioteca"), self)
        self.biblio.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.after(100, self.leer_eventos)
        self.after(200, self.biblio.cargar)
        self.vigilar_lm()
        if rutas:
            self.after(300, lambda: self.traducir.encolar([Path(r) for r in rutas]))

    def todos(self) -> List[Trabajo]:
        return [t.t for t in self.traducir.tarjetas.values()]

    def quitar(self, t: Trabajo):
        self.cola.quitar(t.id)

    def reintentar(self, t: Trabajo):
        self.traducir.borrar(t.id)
        self.cola.agregar([Trabajo(t.ruta, t.accion, dict(t.opciones))])

    def leer_eventos(self):
        try:
            while True:
                evento = self.cola.eventos.get_nowait()
                tipo = evento[0]
                if tipo == "cambio":
                    self.traducir.mostrar(evento[1])
                elif tipo == "quitado":
                    self.traducir.borrar(evento[1])
                elif tipo == "linea":
                    caja = self.registros.get(evento[1].id)
                    if caja and caja.winfo_exists():
                        caja.insert("end", evento[2])
                        caja.see("end")
                elif tipo == "biblioteca":
                    self.biblio.cargar()
                elif tipo == "fin":
                    self.traducir.resumir()
                    if self.traducir.apagar.get() and not evento[1]:
                        threading.Thread(target=apagar, daemon=True).start()
        except Exception as error:  # queue.Empty, or a widget already gone
            if type(error).__name__ != "Empty":
                print(f"aviso: {error}", file=sys.stderr)
        self.after(150, self.leer_eventos)

    def ver_registro(self, t: Trabajo):
        ventana = ctk.CTkToplevel(self)
        ventana.title(f"Registro — {t.nombre}")
        ventana.geometry("900x500")
        ventana.after(50, ventana.lift)
        caja = ctk.CTkTextbox(ventana, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        caja.pack(fill="both", expand=True, padx=8, pady=8)
        caja.insert("end", "".join(t.registro))
        caja.see("end")
        self.registros[t.id] = caja

    def vigilar_lm(self):
        def mirar():
            try:
                modelos = LMStudio(DEFAULT_URL).models()
            except Exception:
                self.after(0, lambda: self.lm.configure(text="LM Studio: ● apagado", text_color=ROJO))
                return
            cargados = [m["id"] for m in modelos if m.get("state") == "loaded"]
            nombres = [m["id"] for m in modelos if m.get("type") in ("llm", "vlm")]
            texto = "LM Studio: ● " + (", ".join(cargados) if cargados else "sin modelo cargado")
            self.after(0, lambda: (self.lm.configure(text=texto, text_color=VERDE),
                                   nombres and self.traducir.modelo.configure(values=nombres)))
        threading.Thread(target=mirar, daemon=True).start()
        self.after(15000, self.vigilar_lm)

    def cerrar(self):
        if (self.cola.ocupada or self.cola.pendientes) and not messagebox.askyesno(
                "Salir", "Hay trabajos en curso o en la cola. ¿Detenerlos y salir?"):
            return
        self.cola.pendientes.clear()
        self.cola.detener()
        self.destroy()


def main(argv=None):
    app = App(sys.argv[1:] if argv is None else argv)
    app.mainloop()

"""Built-in user guidance that remains available without network access."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True, slots=True)
class HelpTopic:
    key: str
    title: str
    body: str


HELP_TOPICS = (
    HelpTopic(
        key="getting-started",
        title="Primeros pasos",
        body="""1. Comprueba que Ollama esté activo si usarás el tab LLM.
2. Abre cada suite y confirma que el modelo requerido figure como disponible localmente.
3. Usa Monitor antes de una carga pesada para revisar RAM y VRAM.
4. Configura PostgreSQL sólo si lo necesitas; SQLite funciona sin servicios externos.
5. Cierra AIOpenStudio desde la ventana principal para permitir el apagado ordenado.

La aplicación no descarga modelos desde sus tabs. La biblioteca compartida se administra mediante
una acción explícita y solicita confirmación de fuente y licencia antes de descargar.""",
    ),
    HelpTopic(
        key="data",
        title="Datos e historiales",
        body="""En desarrollo, los datos relativos viven bajo data/. En una distribución Windows,
configuración, bases, logs, modelos y resultados viven en los directorios de usuario; nunca junto
al ejecutable.

• Conversaciones LLM: SQLite local.
• Transcripciones: carpeta de salida elegida o data/outputs/whisper.
• Imágenes Fooocus: data/outputs/fooocus, separadas por ejecución.
• Diagnósticos y logs: data/logs; el ZIP de soporte se crea sólo donde el usuario elija.
• Credenciales PostgreSQL: variable de entorno o almacén seguro del sistema, nunca en la base ni
  en el paquete.

PostgreSQL conserva configuraciones, ejecuciones y metadatos según el modo seleccionado. Los
binarios grandes permanecen siempre fuera de las bases de datos.""",
    ),
    HelpTopic(
        key="persistence",
        title="Persistencia",
        body="""Solo SQLite
No requiere PostgreSQL y conserva el funcionamiento local completo.

SQLite + réplica PostgreSQL
SQLite recibe cada escritura y PostgreSQL conserva una réplica cuando está conectado.

PostgreSQL principal
Escribe directamente en PostgreSQL. Si la conexión falla, la aplicación avisa y activa un fallback
duradero en SQLite. La preferencia no se cambia silenciosamente: abre Configuración para reconectar
o seleccionar manualmente otro modo.""",
    ),
    HelpTopic(
        key="troubleshooting",
        title="Solución de problemas",
        body="""La aplicación no inicia
Ejecuta Diagnósticos si la ventana alcanza a abrir. Desde una terminal, usa el Python del entorno
principal y conserva el mensaje completo del error.

No aparecen modelos LLM
Confirma que Ollama responda en 127.0.0.1:11434 y que el modelo figure en `ollama list`. Reinicia la
aplicación después de cambiar la biblioteca administrada por Ollama.

Whisper o Fooocus no están disponibles
La ausencia de su entorno, activos o dependencias degrada sólo esa suite. Revisa el detalle del
preflight y no permitas descargas automáticas para resolverlo.

PostgreSQL no conecta
Verifica servidor, puerto, base, rol, contraseña y permisos CREATE sobre el esquema. La aplicación
continúa con SQLite cuando corresponde.

Presión de memoria u OOM
Cancela la tarea, libera modelos residentes y revisa Monitor. No repitas cargas pesadas en paralelo.
Un OOM deliberado sólo debe realizarse como validación supervisada.

Proceso caído o cierre incompleto
Espera el cierre ordenado. Al siguiente inicio, las ejecuciones abandonadas se marcan interrumpidas
y Whisper/Fooocus aplican un presupuesto acotado de reinicio.""",
    ),
    HelpTopic(
        key="support",
        title="Diagnósticos y privacidad",
        body="""Abre Configuración → Diagnósticos para revisar el estado de sistema, runtimes y
persistencia. El ZIP de soporte contiene un snapshot y colas redactadas de logs; excluye bases,
modelos, prompts, respuestas, audios e imágenes.

Revisa el ZIP antes de compartirlo. No envíes `.env`, perfiles PostgreSQL, bases SQLite, catálogos
de modelos ni carpetas de datos. Las actualizaciones tampoco deben sustituir o migrar esos archivos
sin una operación versionada y visible.""",
    ),
)


class HelpDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        *,
        initial_topic: str = "getting-started",
        open_diagnostics: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Ayuda de AIOpenStudio")
        self.geometry("820x560")
        self.minsize(680, 420)
        self.transient(parent)
        self._open_diagnostics = open_diagnostics
        self._topic_by_item: dict[str, HelpTopic] = {}
        self._build(initial_topic)

    def _build(self, initial_topic: str) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        topics = ttk.Treeview(body, show="tree", selectmode="browse", height=12)
        topics.column("#0", width=190, stretch=False)
        topics.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        selected_item = ""
        for topic in HELP_TOPICS:
            item = topics.insert("", tk.END, text=topic.title)
            self._topic_by_item[item] = topic
            if topic.key == initial_topic:
                selected_item = item

        text_frame = ttk.Frame(body)
        text_frame.grid(row=0, column=1, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(1, weight=1)
        self._title = ttk.Label(text_frame, font=("TkDefaultFont", 13, "bold"))
        self._title.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._text = tk.Text(text_frame, wrap=tk.WORD, padx=8, pady=8)
        self._text.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self._text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self._text.configure(yscrollcommand=scrollbar.set, state=tk.DISABLED)

        actions = ttk.Frame(body)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        if self._open_diagnostics is not None:
            ttk.Button(
                actions,
                text="Abrir diagnósticos…",
                command=self._open_diagnostics,
            ).pack(side=tk.LEFT)
        ttk.Button(actions, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

        topics.bind("<<TreeviewSelect>>", lambda _event: self._show_selected(topics))
        if not selected_item:
            selected_item = next(iter(self._topic_by_item))
        topics.selection_set(selected_item)
        topics.focus(selected_item)
        self._show_selected(topics)

    def _show_selected(self, topics: ttk.Treeview) -> None:
        selection = topics.selection()
        if not selection:
            return
        topic = self._topic_by_item[selection[0]]
        self._title.configure(text=topic.title)
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", topic.body)
        self._text.configure(state=tk.DISABLED)

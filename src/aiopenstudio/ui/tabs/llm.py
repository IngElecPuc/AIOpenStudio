"""Tkinter workspace for persistent, contextual LLM conversations."""

from __future__ import annotations

import asyncio
import base64
import io
import random
import tkinter as tk
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from functools import partial
from importlib import import_module
from pathlib import Path
from queue import Empty, SimpleQueue
from time import monotonic
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    ContextInspection,
    ContextItemState,
    ContextKind,
    ContextSendPolicy,
    Conversation,
    ConversationContextItem,
    ConversationMessage,
    ConversationSummary,
    LoadPolicy,
    MessageStatus,
    ModelChatCapabilities,
    ModelDescriptor,
    ModelId,
    ModelState,
    RuntimeEvent,
    RuntimeEventKind,
    StructuredOutputMode,
    ThinkingCapability,
    TranscriptionEventKind,
)
from aiopenstudio.services import LLMDictationService, LLMService
from aiopenstudio.ui.async_runner import AsyncLoopRunner
from aiopenstudio.ui.llm_settings import (
    OUTPUT_JSON,
    OUTPUT_SCHEMA,
    OUTPUT_TEXT,
    OVERFLOW_REJECT,
    OVERFLOW_TRUNCATE,
    THINK_DEFAULT,
    THINK_HIGH,
    THINK_LOW,
    THINK_MEDIUM,
    THINK_OFF,
    THINK_ON,
    GenerationSelection,
    parse_generation_selection,
)
from aiopenstudio.ui.llm_transcript import LLMTranscript

T = TypeVar("T")
_CONTEXT_FILE_TYPES = (
    (
        "Contexto admitido",
        "*.txt *.json *.yaml *.yml *.md *.py *.c *.cpp *.h *.hpp "
        "*.js *.ts *.tsx *.html *.css *.sql *.png *.jpg *.jpeg *.bmp",
    ),
    ("Todos los archivos", "*.*"),
)


class LLMTab(ttk.Frame):
    """Operate LLM use cases without calling the runtime or storage from Tk directly."""

    def __init__(
        self,
        parent: tk.Misc,
        service: LLMService,
        runner: AsyncLoopRunner,
        dictation: LLMDictationService | None = None,
    ) -> None:
        super().__init__(parent, padding=8)
        self._service = service
        self._runner = runner
        self._dictation = dictation
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._models: dict[str, ModelDescriptor] = {}
        self._conversations: dict[str, Conversation] = {}
        self._context_items: dict[str, ConversationContextItem] = {}
        self._messages: tuple[ConversationMessage, ...] = ()
        self._summaries: dict[str, ConversationSummary] = {}
        self._summary_message_ids: dict[str, str] = {}
        self._conversation_id: str | None = None
        self._operation_id: str | None = None
        self._recording = False
        self._capabilities = ModelChatCapabilities()
        self._status = tk.StringVar(value="Ollama: comprobación pendiente")
        self._active_title = tk.StringVar(value="Abriendo conversaciones…")
        self._model_name = tk.StringVar()
        self._keep_alive = tk.StringVar(value="600")
        self._search = tk.StringVar()
        self._show_archived = tk.BooleanVar()
        self._remember_context = tk.BooleanVar()
        self._context_policy = tk.StringVar(value="Una vez")
        self._plain_text = tk.BooleanVar()
        self._show_thinking = tk.BooleanVar()
        self._thinking = tk.StringVar(value=THINK_DEFAULT)
        self._overflow = tk.StringVar(value=OVERFLOW_REJECT)
        self._output_mode = tk.StringVar(value=OUTPUT_TEXT)
        self._capability_status = tk.StringVar(value="Capacidades pendientes")
        self._budget_status = tk.StringVar(value="Presupuesto: se calcula antes de enviar")
        self._setting_values = {
            name: tk.StringVar()
            for name in (
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "seed",
                "context_length",
                "max_new_tokens",
                "repeat_penalty",
            )
        }
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh_models()
        self._refresh_conversations(open_latest=True)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_status_bar()
        self._build_model_bar()
        workspace = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        workspace.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        workspace.add(self._build_conversation_browser(workspace), weight=0)
        workspace.add(self._build_conversation_area(workspace), weight=1)
        workspace.add(self._build_inspector(workspace), weight=0)

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Actualizar modelos", command=self.refresh_models).grid(
            row=0,
            column=1,
        )

    def _build_model_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Modelo").grid(row=0, column=0, padx=(0, 6))
        self._model_selector = ttk.Combobox(bar, textvariable=self._model_name, state="readonly")
        self._model_selector.grid(row=0, column=1, sticky="ew")
        self._model_selector.bind("<<ComboboxSelected>>", self._model_changed)
        ttk.Label(bar, text="Keep-alive (s)").grid(row=0, column=2, padx=(12, 6))
        ttk.Entry(bar, textvariable=self._keep_alive, width=8).grid(row=0, column=3)
        ttk.Button(bar, text="Cargar", command=self._load_model).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Button(bar, text="Liberar", command=self._unload_model).grid(
            row=0, column=5, padx=(6, 0)
        )

    def _build_conversation_browser(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=(0, 0, 8, 0), width=250)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        search = ttk.Entry(frame, textvariable=self._search)
        search.grid(row=0, column=0, sticky="ew")
        search.bind("<Return>", lambda _event: self._refresh_conversations())
        ttk.Button(frame, text="Buscar", command=self._refresh_conversations).grid(
            row=0, column=1, padx=(4, 0)
        )
        ttk.Checkbutton(
            frame,
            text="Mostrar archivadas",
            variable=self._show_archived,
            command=self._refresh_conversations,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        self._conversation_tree = ttk.Treeview(frame, show="tree", selectmode="browse", height=18)
        self._conversation_tree.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self._conversation_tree.bind("<<TreeviewSelect>>", self._conversation_selected)
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        buttons.columnconfigure((0, 1), weight=1)
        actions = (
            ("Nueva", self._new_conversation),
            ("Renombrar", self._rename_conversation),
            ("Archivar/restaurar", self._toggle_archive),
            ("Exportar", self._export_conversation),
            ("Eliminar", self._delete_conversation),
        )
        for index, (label, command) in enumerate(actions):
            ttk.Button(buttons, text=label, command=command).grid(
                row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2
            )
        return frame

    def _build_conversation_area(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=(4, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        heading = ttk.Frame(frame)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading, textvariable=self._active_title, font=("TkDefaultFont", 11, "bold")
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            heading,
            text="Texto plano",
            variable=self._plain_text,
            command=self._render_messages,
        ).grid(row=0, column=1)
        self._transcript = LLMTranscript(frame)
        self._transcript.grid(row=1, column=0, sticky="nsew")
        composer = ttk.Frame(frame)
        composer.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        composer.columnconfigure(0, weight=1)
        self._prompt = tk.Text(composer, height=4, wrap=tk.WORD)
        self._prompt.grid(row=0, column=0, rowspan=3, sticky="ew")
        self._send_button = ttk.Button(
            composer, text="Enviar", command=self._send, state=tk.DISABLED
        )
        self._send_button.grid(row=0, column=1, padx=(7, 0), sticky="ew")
        self._cancel_button = ttk.Button(
            composer, text="Cancelar", command=self._cancel, state=tk.DISABLED
        )
        self._cancel_button.grid(row=1, column=1, padx=(7, 0), pady=(4, 0), sticky="ew")
        available = self._dictation is not None and self._dictation.microphone_available
        self._microphone_button = ttk.Button(
            composer,
            text="Micrófono",
            command=self._toggle_microphone,
            state=tk.NORMAL if available else tk.DISABLED,
        )
        self._microphone_button.grid(row=2, column=1, padx=(7, 0), pady=(4, 0), sticky="ew")
        return frame

    def _build_inspector(self, parent: tk.Misc) -> ttk.Notebook:
        notebook = ttk.Notebook(parent, width=360)
        context = ttk.Frame(notebook, padding=8)
        settings = ttk.Frame(notebook, padding=8)
        summaries = ttk.Frame(notebook, padding=8)
        notebook.add(context, text="Contexto")
        notebook.add(settings, text="Generación")
        notebook.add(summaries, text="Resumen")
        self._build_context_panel(context)
        self._build_settings_panel(settings)
        self._build_summary_panel(summaries)
        return notebook

    def _build_summary_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="Los resúmenes compactan el request sin borrar el historial completo.",
            wraplength=320,
        ).grid(row=0, column=0, sticky="w")
        self._summary_tree = ttk.Treeview(
            frame,
            columns=("active", "messages"),
            show="tree headings",
            height=6,
        )
        self._summary_tree.heading("#0", text="Versión")
        self._summary_tree.heading("active", text="Estado")
        self._summary_tree.heading("messages", text="Mensajes")
        self._summary_tree.column("#0", width=75)
        self._summary_tree.column("active", width=75)
        self._summary_tree.column("messages", width=75)
        self._summary_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self._summary_tree.bind("<<TreeviewSelect>>", self._summary_selected)
        ttk.Label(frame, text="Contenido de la nueva versión").grid(
            row=2, column=0, sticky="w", pady=(7, 0)
        )
        self._summary_content = scrolledtext.ScrolledText(frame, height=7, wrap=tk.WORD)
        self._summary_content.grid(row=3, column=0, sticky="ew")
        ttk.Label(frame, text="Cubrir hasta").grid(row=4, column=0, sticky="w", pady=(5, 0))
        self._summary_through = ttk.Combobox(frame, state="readonly")
        self._summary_through.grid(row=5, column=0, sticky="ew")
        ttk.Label(frame, text="Hechos protegidos (uno por línea)").grid(
            row=6, column=0, sticky="w", pady=(5, 0)
        )
        self._summary_facts = tk.Text(frame, height=4, wrap=tk.WORD)
        self._summary_facts.grid(row=7, column=0, sticky="ew")
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(
            actions,
            text="Guardar nueva versión",
            command=self._create_summary,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        ttk.Button(
            actions,
            text="Descartar seleccionada",
            command=self._discard_summary,
        ).grid(row=0, column=1, sticky="ew", padx=(2, 0))
        ttk.Label(
            frame,
            text="El texto se escribe y revisa localmente; no se genera otro request implícito.",
            wraplength=320,
            foreground="#725800",
        ).grid(row=9, column=0, sticky="w", pady=(7, 0))

    def _build_context_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="☐ no se envía · doble clic cambia el checkbox").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(
            frame,
            text="Recordar cola en esta conversación",
            variable=self._remember_context,
            command=self._remember_context_changed,
        ).grid(row=1, column=0, sticky="w", pady=5)
        self._context_tree = ttk.Treeview(
            frame,
            columns=("enabled", "policy"),
            show="tree headings",
            selectmode="browse",
            height=12,
        )
        self._context_tree.heading("#0", text="Archivo")
        self._context_tree.heading("enabled", text="Enviar")
        self._context_tree.heading("policy", text="Política")
        self._context_tree.column("#0", width=165)
        self._context_tree.column("enabled", width=55, anchor=tk.CENTER)
        self._context_tree.column("policy", width=80)
        self._context_tree.grid(row=2, column=0, sticky="nsew")
        self._context_tree.bind("<Double-1>", self._toggle_context_enabled)
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        actions.columnconfigure((0, 1, 2), weight=1)
        context_actions: tuple[tuple[str, Callable[[], None]], ...] = (
            ("Agregar", self._add_context),
            ("Quitar", self._remove_context),
            ("Vista previa", self._preview_context),
            ("Subir", partial(self._move_context, -1)),
            ("Bajar", partial(self._move_context, 1)),
        )
        for index, (label, command) in enumerate(context_actions):
            ttk.Button(actions, text=label, command=command).grid(
                row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2
            )
        policy = ttk.Frame(frame)
        policy.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        policy.columnconfigure(0, weight=1)
        ttk.Combobox(
            policy,
            values=("Una vez", "Cada turno"),
            textvariable=self._context_policy,
            state="readonly",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(policy, text="Aplicar", command=self._apply_context_policy).grid(
            row=0, column=1, padx=(5, 0)
        )
        ttk.Label(
            frame,
            text="Los archivos son datos externos no confiables. Agregar no los habilita.",
            wraplength=320,
            foreground="#725800",
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _build_settings_panel(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, textvariable=self._capability_status, wraplength=310).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        labels = (
            ("Temperatura", "temperature"),
            ("top_p", "top_p"),
            ("top_k", "top_k"),
            ("min_p", "min_p"),
            ("Seed", "seed"),
            ("Ventana (num_ctx)", "context_length"),
            ("Tokens nuevos", "max_new_tokens"),
            ("Repetición", "repeat_penalty"),
        )
        for row, (label, name) in enumerate(labels, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=1)
            ttk.Entry(frame, textvariable=self._setting_values[name], width=18).grid(
                row=row, column=1, sticky="ew", pady=1
            )
            if name == "seed":
                ttk.Button(frame, text="⚄", width=3, command=self._random_seed).grid(
                    row=row, column=2, padx=(4, 0)
                )
        ttk.Label(
            frame,
            text="Vacío = valor del modelo. Aumentar num_ctx puede consumir mucha RAM/VRAM.",
            wraplength=310,
            foreground="#725800",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 6))
        ttk.Label(frame, text="Secuencias stop (una por línea)").grid(
            row=10, column=0, columnspan=3, sticky="w"
        )
        self._stop_sequences = tk.Text(frame, height=2, wrap=tk.NONE)
        self._stop_sequences.grid(row=11, column=0, columnspan=3, sticky="ew")
        ttk.Label(frame, text="Prompt de sistema").grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(5, 0)
        )
        self._system_prompt = tk.Text(frame, height=3, wrap=tk.WORD)
        self._system_prompt.grid(row=13, column=0, columnspan=3, sticky="ew")
        ttk.Label(frame, text="Overflow").grid(row=14, column=0, sticky="w", pady=(5, 0))
        ttk.Combobox(
            frame,
            values=(OVERFLOW_REJECT, OVERFLOW_TRUNCATE),
            textvariable=self._overflow,
            state="readonly",
        ).grid(row=14, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(frame, text="Thinking").grid(row=15, column=0, sticky="w", pady=(5, 0))
        self._thinking_selector = ttk.Combobox(
            frame, values=(THINK_DEFAULT,), textvariable=self._thinking, state="disabled"
        )
        self._thinking_selector.grid(row=15, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Checkbutton(
            frame,
            text="Mostrar traza durante streaming (no se guarda)",
            variable=self._show_thinking,
        ).grid(row=16, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="Salida").grid(row=17, column=0, sticky="w", pady=(5, 0))
        self._output_selector = ttk.Combobox(
            frame, values=(OUTPUT_TEXT,), textvariable=self._output_mode, state="readonly"
        )
        self._output_selector.grid(row=17, column=1, columnspan=2, sticky="ew", pady=(5, 0))
        self._output_selector.bind("<<ComboboxSelected>>", self._output_changed)
        ttk.Label(frame, text="JSON Schema (subconjunto seguro)").grid(
            row=18, column=0, columnspan=3, sticky="w", pady=(5, 0)
        )
        self._json_schema = scrolledtext.ScrolledText(
            frame, height=6, wrap=tk.NONE, state=tk.DISABLED
        )
        self._json_schema.grid(row=19, column=0, columnspan=3, sticky="nsew")
        ttk.Button(
            frame,
            text="Restaurar valores del modelo",
            command=self._reset_settings,
        ).grid(row=20, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(frame, textvariable=self._budget_status, wraplength=310).grid(
            row=21, column=0, columnspan=3, sticky="w", pady=(7, 0)
        )

    # Model lifecycle and capability discovery

    def refresh_models(self) -> None:
        self._status.set("Ollama: consultando catálogo y capacidades…")
        self._submit(self._service.refresh_models(), self._models_refreshed)

    def _models_refreshed(self, models: Sequence[ModelDescriptor]) -> None:
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            preferred = self._display_name_for_model_key(self._current_last_model_key())
            self._model_name.set(preferred or names[0])
        self._apply_model_capabilities()
        self._status.set(f"Ollama: disponible · {len(names)} modelo(s) instalado(s)")

    def _model_changed(self, _event: tk.Event[tk.Misc]) -> None:
        conversation = self._current_conversation()
        selected = self._selected_model(show_error=False)
        changed = (
            conversation is not None
            and selected is not None
            and conversation.last_model_key is not None
            and conversation.last_model_key != selected.key
            and bool(self._messages)
        )
        if conversation is not None and changed and not messagebox.askyesno(
            "Cambiar modelo",
            "El historial se enviará a un tag distinto. ¿Quieres continuar?",
        ):
            previous = self._display_name_for_model_key(conversation.last_model_key)
            if previous:
                self._model_name.set(previous)
        self._apply_model_capabilities()

    def _apply_model_capabilities(self) -> None:
        descriptor = self._models.get(self._model_name.get())
        raw = descriptor.metadata.get("chat_capabilities") if descriptor else None
        self._capabilities = ModelChatCapabilities.model_validate(raw or {})
        capabilities = self._capabilities
        features = ["visión" if capabilities.supports_vision else "sólo texto"]
        if capabilities.thinking is not ThinkingCapability.UNAVAILABLE:
            features.append("thinking declarado")
        if capabilities.supports_structured_output:
            features.append("JSON validable")
        if capabilities.max_context_tokens:
            features.append(f"contexto máx. {capabilities.max_context_tokens}")
        self._capability_status.set(" · ".join(features))
        if capabilities.thinking is ThinkingCapability.UNAVAILABLE:
            values: tuple[str, ...] = (THINK_DEFAULT,)
            state = "disabled"
            self._thinking.set(THINK_DEFAULT)
        elif capabilities.thinking in {ThinkingCapability.DECLARED, ThinkingCapability.BOOLEAN}:
            values = (THINK_DEFAULT, THINK_OFF, THINK_ON)
            state = "readonly"
        else:
            values = (
                THINK_DEFAULT,
                THINK_OFF,
                THINK_ON,
                THINK_LOW,
                THINK_MEDIUM,
                THINK_HIGH,
            )
            state = "readonly"
        self._thinking_selector.configure(values=values, state=state)
        output_values = (
            (OUTPUT_TEXT, OUTPUT_JSON, OUTPUT_SCHEMA)
            if capabilities.supports_structured_output
            else (OUTPUT_TEXT,)
        )
        self._output_selector.configure(values=output_values)
        if self._output_mode.get() not in output_values:
            self._output_mode.set(OUTPUT_TEXT)
            self._output_changed()

    def _load_model(self) -> None:
        model = self._selected_model()
        if model is None:
            return
        try:
            policy = LoadPolicy(idle_timeout_seconds=float(self._keep_alive.get()))
        except ValueError:
            messagebox.showerror(
                "Keep-alive inválido",
                "Ingresa una cantidad positiva de segundos.",
            )
            return
        self._status.set(f"Cargando {model.name}…")
        self._submit(self._service.load_model(model, policy), self._state_received)

    def _unload_model(self) -> None:
        model = self._selected_model()
        if model:
            self._status.set(f"Liberando {model.name}…")
            self._submit(self._service.unload_model(model), self._state_received)

    def _state_received(self, state: ModelState) -> None:
        self._status.set(
            f"{state.model.name} · RAM {state.ram_residency.value} · "
            f"GPU {state.gpu_residency.value}"
        )

    # Conversation browser

    def _refresh_conversations(self, open_latest: bool = False) -> None:
        async def load() -> Sequence[Conversation]:
            return await asyncio.to_thread(
                self._service.list_conversations,
                200,
                include_archived=self._show_archived.get(),
                query=self._search.get().strip() or None,
            )

        self._submit(load(), lambda items: self._conversations_refreshed(items, open_latest))

    def _conversations_refreshed(
        self, conversations: Sequence[Conversation], open_latest: bool = False
    ) -> None:
        current = self._conversation_id
        active = self._current_conversation()
        self._conversations = {item.id: item for item in conversations}
        if active is not None:
            self._conversations.setdefault(active.id, active)
        self._conversation_tree.delete(*self._conversation_tree.get_children())
        for item in conversations:
            marker = " [archivada]" if item.archived_at else ""
            self._conversation_tree.insert("", tk.END, iid=item.id, text=f"{item.title}{marker}")
        if current in self._conversations:
            self._conversation_tree.selection_set(current)
        elif conversations and (open_latest or current is None):
            self._open_conversation(conversations[0].id)
        elif not conversations and open_latest:
            self._new_conversation()

    def _conversation_selected(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self._conversation_tree.selection()
        if not selection or selection[0] == self._conversation_id:
            return
        if self._operation_id:
            messagebox.showinfo(
                "Generación activa", "Cancela o espera antes de cambiar de conversación."
            )
            if self._conversation_id:
                self._conversation_tree.selection_set(self._conversation_id)
            return
        self._open_conversation(selection[0])

    def _open_conversation(self, conversation_id: str) -> None:
        self._submit(self._load_conversation_bundle(conversation_id), self._conversation_loaded)

    async def _load_conversation_bundle(
        self, conversation_id: str
    ) -> tuple[Conversation, tuple[ConversationMessage, ...], tuple[ConversationContextItem, ...]]:
        conversation = await asyncio.to_thread(self._service.get_conversation, conversation_id)
        if conversation is None:
            raise ValueError("La conversación ya no existe.")
        messages = tuple(await asyncio.to_thread(self._service.list_messages, conversation_id))
        contexts = await self._service.list_context(conversation_id)
        return conversation, messages, contexts

    def _conversation_loaded(
        self,
        bundle: tuple[
            Conversation,
            tuple[ConversationMessage, ...],
            tuple[ConversationContextItem, ...],
        ],
    ) -> None:
        conversation, self._messages, contexts = bundle
        self._conversation_id = conversation.id
        self._conversations[conversation.id] = conversation
        self._active_title.set(conversation.title)
        self._remember_context.set(conversation.remember_context_queue)
        self._set_context_items(contexts)
        self._refresh_summaries()
        self._refresh_summary_message_choices()
        self._render_messages()
        self._send_button.configure(state=tk.DISABLED if conversation.archived_at else tk.NORMAL)
        if conversation.last_model_key:
            display = self._display_name_for_model_key(conversation.last_model_key)
            if display:
                self._model_name.set(display)
        self._apply_model_capabilities()

    def _new_conversation(self) -> None:
        self._submit_blocking(self._service.create_conversation, self._conversation_created)

    def _conversation_created(self, conversation: Conversation) -> None:
        self._conversation_id = None
        self._refresh_conversations()
        self._open_conversation(conversation.id)

    def _rename_conversation(self) -> None:
        conversation = self._current_conversation()
        if conversation is None:
            return
        title = simpledialog.askstring(
            "Renombrar conversación",
            "Título:",
            initialvalue=conversation.title,
            parent=self,
        )
        if title is not None:
            self._submit_blocking(
                self._service.rename_conversation,
                self._conversation_mutated,
                conversation.id,
                title,
            )

    def _toggle_archive(self) -> None:
        conversation = self._current_conversation()
        if conversation is None:
            return
        operation = (
            self._service.restore_conversation
            if conversation.archived_at
            else self._service.archive_conversation
        )
        self._submit_blocking(operation, self._conversation_mutated, conversation.id)

    def _conversation_mutated(self, conversation: Conversation) -> None:
        self._conversations[conversation.id] = conversation
        self._refresh_conversations()
        self._open_conversation(conversation.id)

    def _delete_conversation(self) -> None:
        conversation = self._current_conversation()
        if conversation is None or not messagebox.askyesno(
            "Eliminar conversación",
            "Se eliminarán de SQLite la conversación, mensajes, resúmenes y referencias. "
            "Los archivos originales no se borrarán. No se puede deshacer.",
        ):
            return
        self._submit(
            self._service.delete_conversation_with_context(conversation.id),
            self._conversation_deleted,
        )

    def _conversation_deleted(self, deleted: bool) -> None:
        if deleted:
            self._conversation_id = None
            self._messages = ()
            self._render_messages()
            self._refresh_conversations(open_latest=True)

    def _export_conversation(self) -> None:
        conversation = self._current_conversation()
        if conversation is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar conversación",
            defaultextension=".md",
            initialfile=_safe_filename(conversation.title) + ".md",
            filetypes=(("Markdown", "*.md"), ("JSON", "*.json")),
        )
        if destination:
            target = Path(destination)
            self._submit(
                self._service.export_conversation(
                    conversation.id,
                    target,
                    as_json=target.suffix.casefold() == ".json",
                ),
                self._export_finished,
            )

    @staticmethod
    def _export_finished(path: Path) -> None:
        messagebox.showinfo("Exportación completa", f"Guardado en:\n{path}")

    # Context queue

    def _add_context(self) -> None:
        if self._conversation_id is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Agregar contexto externo", filetypes=_CONTEXT_FILE_TYPES
        )
        if not selected:
            return
        snapshot = messagebox.askyesno(
            "Copia reproducible",
            "¿Copiar un snapshot privado? Sí conserva una copia consentida; No guarda sólo "
            "la referencia y bloqueará el envío si cambia.",
        )
        self._submit(
            self._service.add_context(
                self._conversation_id, Path(selected), enabled=False, snapshot=snapshot
            ),
            lambda _item: self._refresh_context(),
        )

    def _refresh_context(self) -> None:
        if self._conversation_id:
            self._submit(self._service.list_context(self._conversation_id), self._set_context_items)

    def _set_context_items(self, items: Sequence[ConversationContextItem]) -> None:
        self._context_items = {item.id: item for item in items}
        self._context_tree.delete(*self._context_tree.get_children())
        for item in sorted(items, key=lambda candidate: candidate.order):
            policy = "Una vez" if item.send_policy is ContextSendPolicy.ONCE else "Cada turno"
            self._context_tree.insert(
                "",
                tk.END,
                iid=item.id,
                text=item.display_name,
                values=("☑" if item.enabled else "☐", policy),
            )

    def _toggle_context_enabled(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        item = self._selected_context()
        if item and self._conversation_id:
            self._submit(
                self._service.set_context_enabled(
                    self._conversation_id, item.id, not item.enabled
                ),
                lambda _item: self._refresh_context(),
            )

    def _remove_context(self) -> None:
        item = self._selected_context()
        if item and self._conversation_id and messagebox.askyesno(
            "Quitar contexto", "Se quitará de la cola. El archivo original no se borrará."
        ):
            self._submit(
                self._service.remove_context(self._conversation_id, item.id),
                lambda _removed: self._refresh_context(),
            )

    def _move_context(self, direction: int) -> None:
        item = self._selected_context()
        if item is None or self._conversation_id is None:
            return
        ordered = sorted(self._context_items.values(), key=lambda candidate: candidate.order)
        index = next(i for i, candidate in enumerate(ordered) if candidate.id == item.id)
        target = index + direction
        if not 0 <= target < len(ordered):
            return
        ordered[index], ordered[target] = ordered[target], ordered[index]
        self._submit(
            self._service.reorder_context(
                self._conversation_id, [candidate.id for candidate in ordered]
            ),
            lambda _none: self._refresh_context(),
        )

    def _apply_context_policy(self) -> None:
        item = self._selected_context()
        if item is None or self._conversation_id is None:
            return
        policy = (
            ContextSendPolicy.EVERY_TURN
            if self._context_policy.get() == "Cada turno"
            else ContextSendPolicy.ONCE
        )
        self._submit(
            self._service.set_context_send_policy(self._conversation_id, item.id, policy),
            lambda _item: self._refresh_context(),
        )

    def _remember_context_changed(self) -> None:
        if self._conversation_id:
            self._submit(
                self._service.remember_context_queue(
                    self._conversation_id, self._remember_context.get()
                ),
                self._ignore_result,
            )

    def _preview_context(self) -> None:
        item = self._selected_context()
        if item and self._conversation_id:
            self._submit(
                self._context_preview_bundle(self._conversation_id, item),
                self._show_context_preview,
            )

    async def _context_preview_bundle(
        self, conversation_id: str, item: ConversationContextItem
    ) -> tuple[ContextInspection, str | None]:
        inspection = await self._service.inspect_context(conversation_id, item.id)
        thumbnail = None
        if item.kind is ContextKind.IMAGE and inspection.state in {
            ContextItemState.READY,
            ContextItemState.CHANGED,
        }:
            thumbnail = await asyncio.to_thread(
                _thumbnail_base64, item.snapshot_path or item.source_path
            )
        return inspection, thumbnail

    def _show_context_preview(self, bundle: tuple[ContextInspection, str | None]) -> None:
        inspection, thumbnail = bundle
        window = tk.Toplevel(self)
        window.title(f"Contexto · {inspection.item.display_name}")
        window.geometry("620x480")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)
        detail = (
            f"Estado: {inspection.state.value} · {inspection.item.size_bytes} bytes · "
            f"SHA-256 {inspection.item.sha256[:16]}…"
        )
        ttk.Label(window, text=detail, wraplength=590).grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        if thumbnail:
            image = tk.PhotoImage(data=thumbnail)
            label = ttk.Label(window, image=image)
            label.image = image  # type: ignore[attr-defined]
            label.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        else:
            preview = scrolledtext.ScrolledText(window, wrap=tk.WORD)
            preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
            preview.insert("1.0", inspection.preview or inspection.warning or "Sin vista previa")
            preview.configure(state=tk.DISABLED)
        if inspection.warning:
            ttk.Label(window, text=inspection.warning, foreground="#9d2222").grid(
                row=2, column=0, sticky="w", padx=10
            )
        if inspection.state is ContextItemState.CHANGED and self._conversation_id:
            ttk.Button(
                window,
                text="Aceptar versión actual",
                command=lambda: self._accept_context_change(inspection.item.id, window),
            ).grid(row=3, column=0, pady=8)

    def _accept_context_change(self, item_id: str, window: tk.Toplevel) -> None:
        if self._conversation_id:
            self._submit(
                self._service.accept_context_changes(self._conversation_id, item_id),
                partial(self._context_change_accepted, window),
            )

    def _context_change_accepted(
        self,
        window: tk.Toplevel,
        _item: ConversationContextItem,
    ) -> None:
        window.destroy()
        self._refresh_context()

    # Versioned summaries

    def _refresh_summaries(self) -> None:
        if self._conversation_id:
            self._submit_blocking(
                self._service.list_summaries,
                self._set_summaries,
                self._conversation_id,
            )

    def _set_summaries(self, summaries: Sequence[ConversationSummary]) -> None:
        self._summaries = {summary.id: summary for summary in summaries}
        self._summary_tree.delete(*self._summary_tree.get_children())
        for summary in sorted(summaries, key=lambda item: item.version, reverse=True):
            self._summary_tree.insert(
                "",
                tk.END,
                iid=summary.id,
                text=f"v{summary.version}",
                values=("Activo" if summary.active else "Descartado", summary.source_message_count),
            )

    def _refresh_summary_message_choices(self) -> None:
        self._summary_message_ids = {}
        for index, message in enumerate(self._messages, start=1):
            if message.status is not MessageStatus.COMPLETE:
                continue
            label = f"{index}. {message.role.value}: {' '.join(message.content.split())[:55]}"
            self._summary_message_ids[label] = message.id
        choices = tuple(self._summary_message_ids)
        self._summary_through.configure(values=choices)
        if choices:
            self._summary_through.set(choices[-1])
        else:
            self._summary_through.set("")

    def _summary_selected(self, _event: tk.Event[tk.Misc]) -> None:
        selection = self._summary_tree.selection()
        summary = self._summaries.get(selection[0]) if selection else None
        if summary is None:
            return
        self._summary_content.delete("1.0", tk.END)
        self._summary_content.insert("1.0", summary.content)
        self._summary_facts.delete("1.0", tk.END)
        self._summary_facts.insert("1.0", "\n".join(summary.protected_facts))

    def _create_summary(self) -> None:
        conversation_id = self._conversation_id
        through_id = self._summary_message_ids.get(self._summary_through.get())
        if conversation_id is None or through_id is None:
            messagebox.showinfo("Resumen", "Selecciona el último mensaje que cubrirá el resumen.")
            return
        content = self._summary_content.get("1.0", tk.END)
        facts = tuple(
            line.strip()
            for line in self._summary_facts.get("1.0", tk.END).splitlines()
            if line.strip()
        )
        model = self._selected_model(show_error=False)
        self._submit_blocking(
            self._service.create_summary,
            lambda _summary: self._refresh_summaries(),
            conversation_id,
            content,
            through_message_id=through_id,
            model=model,
            protected_facts=facts,
        )

    def _discard_summary(self) -> None:
        selection = self._summary_tree.selection()
        if not selection or self._conversation_id is None:
            messagebox.showinfo("Resumen", "Selecciona una versión.")
            return
        if messagebox.askyesno(
            "Descartar resumen",
            "La versión quedará conservada para auditoría, pero no entrará en nuevos requests.",
        ):
            self._submit_blocking(
                self._service.discard_summary,
                lambda _summary: self._refresh_summaries(),
                self._conversation_id,
                selection[0],
            )

    # Generation and streaming

    def _send(self) -> None:
        model = self._selected_model()
        prompt = self._prompt.get("1.0", tk.END).strip()
        if model is None or self._conversation_id is None or not prompt:
            return
        try:
            keep_alive = float(self._keep_alive.get())
            if keep_alive < 0:
                raise ValueError("Keep-alive debe ser cero o positivo.")
            selection = self._generation_selection()
            self._validate_selection_against_capabilities(selection)
        except (ValueError, TypeError) as error:
            messagebox.showerror("Ajustes inválidos", str(error))
            return
        operation_id = self._service.create_operation_id()
        self._operation_id = operation_id
        self._prompt.delete("1.0", tk.END)
        self._transcript.begin_stream(prompt, model_name=model.name)
        self._send_button.configure(state=tk.DISABLED)
        self._cancel_button.configure(state=tk.NORMAL)
        self._status.set(f"Generando con {model.name}…")
        self._submit(
            self._consume_chat(
                operation_id,
                self._conversation_id,
                model,
                prompt,
                keep_alive,
                selection,
                self._show_thinking.get(),
            ),
            self._chat_finished,
        )

    async def _consume_chat(
        self,
        operation_id: str,
        conversation_id: str,
        model: ModelId,
        prompt: str,
        keep_alive: float,
        selection: GenerationSelection,
        show_thinking: bool,
    ) -> None:
        response: list[str] = []
        thinking: list[str] = []
        last_flush = monotonic()
        async for event in self._service.stream_chat(
            operation_id=operation_id,
            conversation_id=conversation_id,
            model=model,
            prompt=prompt,
            options=selection.options,
            system_prompt=selection.system_prompt,
            overflow_policy=selection.overflow_policy,
            keep_alive_seconds=keep_alive,
            think=selection.think,
            output=selection.output,
        ):
            value = event.payload.get("text")
            if event.kind is RuntimeEventKind.TEXT_DELTA and isinstance(value, str):
                response.append(value)
            elif event.kind is RuntimeEventKind.THINKING_DELTA and isinstance(value, str):
                if show_thinking:
                    thinking.append(value)
            else:
                self._flush_stream_buffers(response, thinking, show_thinking)
                self._post(partial(self._handle_runtime_event, event))
            if monotonic() - last_flush >= 0.05:
                self._flush_stream_buffers(response, thinking, show_thinking)
                last_flush = monotonic()
        self._flush_stream_buffers(response, thinking, show_thinking)

    def _flush_stream_buffers(
        self, response: list[str], thinking: list[str], show_thinking: bool
    ) -> None:
        response_text, thinking_text = "".join(response), "".join(thinking)
        response.clear()
        thinking.clear()
        if response_text or thinking_text:
            self._post(
                partial(
                    self._append_stream_batch, response_text, thinking_text, show_thinking
                )
            )

    def _append_stream_batch(self, response: str, thinking: str, show_thinking: bool) -> None:
        if thinking:
            self._transcript.append_thinking(thinking, visible=show_thinking)
        if response:
            self._transcript.append_response(response)

    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        if event.kind is RuntimeEventKind.PREFLIGHT:
            budget = event.payload.get("token_budget")
            if isinstance(budget, dict):
                self._budget_status.set(
                    "Preflight: "
                    f"{budget.get('estimated_input_tokens', '?')}/"
                    f"{budget.get('available_input_tokens', '?')} tokens estimados · "
                    f"{budget.get('truncated_message_count', 0)} mensaje(s) omitido(s)"
                )
        elif event.kind is RuntimeEventKind.STARTED:
            self._status.set("Runtime iniciado; generando…")
        elif event.kind is RuntimeEventKind.CANCELLED:
            self._transcript.append_notice("respuesta parcial cancelada")
            self._finish_operation("Generación cancelada")
        elif event.kind is RuntimeEventKind.COMPLETED:
            self._finish_operation("Generación completada y validada")
        elif event.kind is RuntimeEventKind.ERROR:
            message = str(event.payload.get("message") or "Error desconocido")
            self._transcript.append_notice(message, error=True)
            self._finish_operation("Generación fallida")
            messagebox.showerror("Error durante la generación", message)

    def _chat_finished(self, _: object) -> None:
        self._finish_operation(self._status.get())
        if self._conversation_id:
            self._open_conversation(self._conversation_id)
            self._refresh_conversations()

    def _cancel(self) -> None:
        if self._operation_id:
            self._status.set("Cancelando…")
            self._submit(self._service.cancel(self._operation_id), self._ignore_result)

    def _generation_selection(self) -> GenerationSelection:
        return parse_generation_selection(
            {name: value.get() for name, value in self._setting_values.items()},
            system_prompt=self._system_prompt.get("1.0", tk.END),
            stop_sequences=self._stop_sequences.get("1.0", tk.END),
            thinking=self._thinking.get(),
            overflow=self._overflow.get(),
            output_mode=self._output_mode.get(),
            json_schema=self._json_schema.get("1.0", tk.END),
        )

    def _validate_selection_against_capabilities(self, selection: GenerationSelection) -> None:
        if (
            selection.think is not None
            and self._capabilities.thinking is ThinkingCapability.UNAVAILABLE
        ):
            raise ValueError("El tag exacto no declara thinking.")
        if (
            selection.output.mode is not StructuredOutputMode.TEXT
            and not self._capabilities.supports_structured_output
        ):
            raise ValueError("El tag/runtime no declara salida estructurada.")

    def _reset_settings(self) -> None:
        for value in self._setting_values.values():
            value.set("")
        self._stop_sequences.delete("1.0", tk.END)
        self._system_prompt.delete("1.0", tk.END)
        self._thinking.set(THINK_DEFAULT)
        self._overflow.set(OVERFLOW_REJECT)
        self._output_mode.set(OUTPUT_TEXT)
        self._json_schema.configure(state=tk.NORMAL)
        self._json_schema.delete("1.0", tk.END)
        self._json_schema.configure(state=tk.DISABLED)
        self._budget_status.set("Presupuesto: se calcula antes de enviar")

    def _random_seed(self) -> None:
        self._setting_values["seed"].set(str(random.SystemRandom().randint(0, 2_147_483_647)))

    def _output_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if self._output_mode.get() == OUTPUT_SCHEMA:
            self._json_schema.configure(state="normal")
        else:
            self._json_schema.configure(state="disabled")

    # Dictation and UI plumbing

    def _toggle_microphone(self) -> None:
        if self._dictation is None:
            return
        if not self._recording:
            self._status.set("Iniciando micrófono…")
            self._submit(self._dictation.start_recording(), self._recording_started)
            return
        self._recording = False
        self._microphone_button.configure(text="Micrófono", state=tk.DISABLED)
        self._status.set("Transcribiendo dictado; esperando al LLM si está ocupado…")
        self._submit(self._stop_and_transcribe(), self._dictation_finished)

    def _recording_started(self, _: object) -> None:
        self._recording = True
        self._microphone_button.configure(text="Detener y transcribir")
        self._status.set("Grabando dictado…")

    async def _stop_and_transcribe(self) -> str:
        if self._dictation is None:
            return ""
        source = await self._dictation.stop_recording()
        descriptor = self._models.get(self._model_name.get())
        parts: list[str] = []
        async for event in self._dictation.transcribe_for_llm(
            source, descriptor.id if descriptor else None
        ):
            if event.kind is TranscriptionEventKind.SEGMENT and event.segment:
                parts.append(event.segment.text)
        return "".join(parts).strip()

    def _dictation_finished(self, text: str) -> None:
        if text:
            current = self._prompt.get("1.0", tk.END).strip()
            self._prompt.delete("1.0", tk.END)
            self._prompt.insert("1.0", f"{current} {text}".strip())
        available = self._dictation is not None and self._dictation.microphone_available
        self._microphone_button.configure(
            text="Micrófono", state=tk.NORMAL if available else tk.DISABLED
        )
        self._status.set("Dictado transcrito en el mensaje")

    def _finish_operation(self, status: str) -> None:
        self._operation_id = None
        conversation = self._current_conversation()
        can_send = conversation is not None and conversation.archived_at is None
        self._send_button.configure(state=tk.NORMAL if can_send else tk.DISABLED)
        self._cancel_button.configure(state=tk.DISABLED)
        self._status.set(status)

    def _render_messages(self) -> None:
        self._transcript.render(self._messages, plain_text=self._plain_text.get())

    def _selected_model(self, *, show_error: bool = True) -> ModelId | None:
        descriptor = self._models.get(self._model_name.get())
        if descriptor is None and show_error:
            messagebox.showinfo("Sin modelo", "Actualiza y selecciona un modelo instalado.")
        return descriptor.id if descriptor else None

    def _selected_context(self) -> ConversationContextItem | None:
        selection = self._context_tree.selection()
        if not selection:
            messagebox.showinfo("Contexto", "Selecciona un elemento de la cola.")
            return None
        return self._context_items.get(selection[0])

    def _current_conversation(self) -> Conversation | None:
        return self._conversations.get(self._conversation_id or "")

    def _current_last_model_key(self) -> str | None:
        conversation = self._current_conversation()
        return conversation.last_model_key if conversation else None

    def _display_name_for_model_key(self, model_key: str | None) -> str | None:
        return next(
            (name for name, item in self._models.items() if item.id.key == model_key),
            None,
        )

    def _submit_blocking(
        self,
        function: Callable[..., T],
        on_success: Callable[[T], None],
        *args: object,
        **kwargs: object,
    ) -> None:
        async def invoke() -> T:
            return await asyncio.to_thread(function, *args, **kwargs)

        self._submit(invoke(), on_success)

    def _submit(self, coroutine: Coroutine[Any, Any, T], on_success: Callable[[T], None]) -> None:
        future = self._runner.submit(coroutine)
        future.add_done_callback(lambda completed: self._post_result(completed, on_success))

    def _post_result(self, future: Future[T], on_success: Callable[[T], None]) -> None:
        try:
            result = future.result()
        except Exception as error:
            self._post(partial(self._show_error, error))
        else:
            self._post(partial(on_success, result))

    @staticmethod
    def _ignore_result(_: object) -> None:
        return None

    def _show_error(self, error: BaseException) -> None:
        self._recording = False
        self._finish_operation("Operación fallida")
        messagebox.showerror("AIOpenStudio", str(error))

    def _post(self, callback: Callable[[], None]) -> None:
        self._callbacks.put(callback)

    def _drain_callbacks(self) -> None:
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)


def _thumbnail_base64(path: Path) -> str:
    image_module = import_module("PIL.Image")
    image_ops = import_module("PIL.ImageOps")
    with image_module.open(path) as opened:
        normalized = image_ops.exif_transpose(opened).convert("RGB")
        normalized.thumbnail((560, 380))
        buffer = io.BytesIO()
        normalized.save(buffer, format="PNG")
        normalized.close()
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _safe_filename(title: str) -> str:
    forbidden = '<>:"/\\|?*'
    normalized = "".join("_" if char in forbidden else char for char in title)
    return normalized.strip(" .")[:80] or "conversacion"

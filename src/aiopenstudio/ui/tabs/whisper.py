"""Tkinter tab for queued, editable faster-whisper transcription."""

from __future__ import annotations

import asyncio
import tkinter as tk
from collections.abc import Callable, Coroutine, Iterable, Sequence
from concurrent.futures import Future
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    AudioInterval,
    ComputeDevice,
    ExperimentalDictationEvent,
    ExperimentalDictationEventKind,
    ExperimentalDictationOptions,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    TranscriptionDecodingOptions,
    TranscriptionDocument,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionOptions,
    TranscriptionPromptOptions,
    TranscriptionRequest,
    TranscriptionSegment,
    TranscriptionTask,
    VadMode,
    VadParameters,
)
from aiopenstudio.services import TranscriptionService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")
_AUTO_LANGUAGE = "automático"
_BACKEND_DEFAULT = "backend"


class WhisperTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        service: TranscriptionService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent, padding=12)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._models: dict[str, ModelDescriptor] = {}
        self._loaded_model: ModelId | None = None
        self._operation_id: str | None = None
        self._experimental_operation_id: str | None = None
        self._recording = False
        self._temporary_recording: Path | None = None
        self._queue_counter = 0
        self._queue_paths: dict[str, Path] = {}
        self._operation_rows: dict[str, str] = {}
        self._row_operations: dict[str, str] = {}
        self._documents: dict[str, TranscriptionDocument] = {}
        self._visible_operation: str | None = None
        self._detail_segments: dict[str, int] = {}

        self._source = tk.StringVar()
        self._model_name = tk.StringVar()
        self._device = tk.StringVar(value=ComputeDevice.AUTO.value)
        self._language = tk.StringVar(value=_AUTO_LANGUAGE)
        self._task = tk.StringVar(value=TranscriptionTask.TRANSCRIBE.value)
        self._word_timestamps = tk.BooleanVar(value=False)
        self._vad_mode = tk.StringVar(value=VadMode.AUTOMATIC.value)
        self._intervals = tk.StringVar()
        self._initial_prompt = tk.StringVar()
        self._prefix = tk.StringVar()
        self._hotwords = tk.StringVar()
        self._preset = tk.StringVar(value="Valores del backend")
        self._detail_query = tk.StringVar()
        self._show_words = tk.BooleanVar(value=False)
        self._status = tk.StringVar(value="Whisper: comprobación pendiente")
        self._residency = tk.StringVar(value="Modelo residente: ninguno")
        self._progress_text = tk.StringVar(value="Sin operación activa")
        self._capability_text = tk.StringVar(value="Selecciona un modelo local.")
        self._result_metadata = tk.StringVar(value="Sin resultado seleccionado")
        self._experimental_chunk_seconds = tk.StringVar(value="30")
        self._experimental_overlap_seconds = tk.StringVar(value="3")
        self._experimental_deduplication_words = tk.StringVar(value="12")
        self._experimental_status = tk.StringVar(
            value="Inactivo: esta vista no es streaming nativo."
        )

        self._advanced = {
            name: tk.StringVar()
            for name in (
                "vad_threshold",
                "vad_negative_threshold",
                "vad_minimum_speech_ms",
                "vad_maximum_speech_seconds",
                "vad_minimum_silence_ms",
                "vad_speech_padding_ms",
                "beam_size",
                "best_of",
                "patience",
                "temperatures",
                "compression_ratio_threshold",
                "log_probability_threshold",
                "no_speech_threshold",
                "repetition_penalty",
                "no_repeat_ngram_size",
                "max_new_tokens",
                "hallucination_silence_seconds",
                "prepend_punctuations",
                "append_punctuations",
                "language_detection_threshold",
                "language_detection_segments",
            )
        }
        self._condition_previous = tk.StringVar(value=_BACKEND_DEFAULT)
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh_models()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        self._build_status()
        self._build_source()
        self._build_basic_options()
        self._build_actions()
        self._build_workspace()
        self._build_progress()

    def _build_status(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(frame, text="Actualizar", command=self.refresh_models).grid(row=0, column=1)

    def _build_source(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Audio").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(frame, textvariable=self._source, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(frame, text="Seleccionar…", command=self._choose_source).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(frame, text="Agregar audios…", command=self._add_sources).grid(
            row=0, column=3, padx=(6, 0)
        )
        self._microphone_button = ttk.Button(
            frame,
            text="Grabar micrófono",
            command=self._toggle_recording,
            state=tk.NORMAL if self._service.microphone_available else tk.DISABLED,
        )
        self._microphone_button.grid(row=0, column=4, padx=(6, 0))

    def _build_basic_options(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Modelo").grid(row=0, column=0, padx=(0, 6))
        self._model_selector = ttk.Combobox(
            frame, textvariable=self._model_name, state="readonly"
        )
        self._model_selector.grid(row=0, column=1, sticky="ew")
        self._model_selector.bind("<<ComboboxSelected>>", self._selection_changed)
        ttk.Label(frame, text="Dispositivo").grid(row=0, column=2, padx=(12, 6))
        ttk.Combobox(
            frame,
            textvariable=self._device,
            values=tuple(device.value for device in ComputeDevice),
            state="readonly",
            width=8,
        ).grid(row=0, column=3)
        ttk.Label(frame, text="Entrada").grid(row=0, column=4, padx=(12, 6))
        self._language_selector = ttk.Combobox(
            frame, textvariable=self._language, state="readonly", width=12
        )
        self._language_selector.grid(row=0, column=5)
        ttk.Label(frame, text="Tarea").grid(row=0, column=6, padx=(12, 6))
        self._task_selector = ttk.Combobox(
            frame, textvariable=self._task, state="readonly", width=12
        )
        self._task_selector.grid(row=0, column=7)
        ttk.Checkbutton(
            frame,
            text="Timestamps por palabra",
            variable=self._word_timestamps,
        ).grid(row=0, column=8, padx=(12, 0))

    def _build_actions(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._transcribe_button = ttk.Button(
            frame, text="Procesar cola", command=self._start
        )
        self._transcribe_button.pack(side=tk.LEFT)
        self._cancel_button = ttk.Button(
            frame, text="Cancelar tarea", command=self._cancel, state=tk.DISABLED
        )
        self._cancel_button.pack(side=tk.LEFT, padx=(6, 0))
        self._load_button = ttk.Button(frame, text="Cargar / cambiar", command=self._load)
        self._load_button.pack(side=tk.LEFT, padx=(12, 0))
        self._unload_button = ttk.Button(
            frame, text="Liberar residente", command=self._unload, state=tk.DISABLED
        )
        self._unload_button.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(frame, textvariable=self._residency).pack(side=tk.LEFT, padx=(14, 0))
        self._export_button = ttk.Button(
            frame, text="Exportar…", command=self._export, state=tk.DISABLED
        )
        self._export_button.pack(side=tk.RIGHT)

    def _build_workspace(self) -> None:
        workspace = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        workspace.grid(row=4, column=0, sticky="nsew")
        queue_frame = ttk.LabelFrame(workspace, text="Cola FIFO", padding=6)
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)
        self._queue = ttk.Treeview(
            queue_frame,
            columns=("audio", "status"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self._queue.heading("audio", text="Audio")
        self._queue.heading("status", text="Estado")
        self._queue.column("audio", width=220)
        self._queue.column("status", width=95, stretch=False)
        self._queue.grid(row=0, column=0, sticky="nsew")
        self._queue.bind("<<TreeviewSelect>>", self._queue_selected)
        queue_actions = ttk.Frame(queue_frame)
        queue_actions.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(queue_actions, text="Subir", command=partial(self._move_row, -1)).pack(
            side=tk.LEFT
        )
        ttk.Button(queue_actions, text="Bajar", command=partial(self._move_row, 1)).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Button(queue_actions, text="Quitar", command=self._remove_row).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        workspace.add(queue_frame, weight=1)

        notebook = ttk.Notebook(workspace)
        text_frame = ttk.Frame(notebook)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(1, weight=1)
        ttk.Label(text_frame, textvariable=self._result_metadata).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self._transcript = scrolledtext.ScrolledText(
            text_frame, wrap=tk.WORD, state=tk.DISABLED
        )
        self._transcript.grid(row=1, column=0, sticky="nsew")
        notebook.add(text_frame, text="Texto limpio")

        detail_frame = self._build_detail(notebook)
        notebook.add(detail_frame, text="Detalle opcional")
        settings_frame = self._build_settings(notebook)
        notebook.add(settings_frame, text="Ajustes")
        experimental_frame = self._build_experimental(notebook)
        notebook.add(experimental_frame, text="Dictado experimental")
        workspace.add(notebook, weight=4)

    def _build_detail(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=6)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        filters = ttk.Frame(frame)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="Buscar").grid(row=0, column=0, padx=(0, 6))
        entry = ttk.Entry(filters, textvariable=self._detail_query)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<KeyRelease>", self._refresh_detail)
        ttk.Checkbutton(
            filters,
            text="Mostrar palabras",
            variable=self._show_words,
            command=self._refresh_detail,
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(filters, text="Corregir segmento…", command=self._edit_segment).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(filters, text="Restaurar original", command=self._discard_correction).grid(
            row=0, column=4, padx=(6, 0)
        )
        columns = ("kind", "start", "end", "text", "confidence")
        self._detail = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for column, label, width in (
            ("kind", "Tipo", 75),
            ("start", "Inicio", 80),
            ("end", "Fin", 80),
            ("text", "Texto", 500),
            ("confidence", "Confianza", 90),
        ):
            self._detail.heading(column, text=label)
            self._detail.column(column, width=width, stretch=column == "text")
        self._detail.grid(row=1, column=0, sticky="nsew")
        self._detail.bind("<Double-1>", self._edit_segment)
        return frame

    def _build_settings(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=8)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, textvariable=self._capability_text, wraplength=850).grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )
        preset = ttk.Frame(frame)
        preset.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(preset, text="Preset").pack(side=tk.LEFT)
        ttk.Combobox(
            preset,
            textvariable=self._preset,
            values=("Valores del backend", "Rápido", "Preciso", "Audio con pausas"),
            state="readonly",
            width=22,
        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(preset, text="Aplicar", command=self._apply_preset).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(preset, text="Restaurar backend", command=self._reset_advanced).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        vad = ttk.LabelFrame(frame, text="VAD e intervalos", padding=6)
        vad.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(vad, text="Modo").grid(row=0, column=0)
        ttk.Combobox(
            vad,
            textvariable=self._vad_mode,
            values=tuple(mode.value for mode in VadMode),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, padx=(4, 10))
        self._setting_entry(vad, 0, 2, "Umbral", "vad_threshold", 8)
        self._setting_entry(vad, 0, 4, "Silencio", "vad_negative_threshold", 8)
        self._setting_entry(vad, 0, 6, "Voz mín. ms", "vad_minimum_speech_ms", 8)
        self._setting_entry(vad, 1, 0, "Voz máx. s", "vad_maximum_speech_seconds", 8)
        self._setting_entry(vad, 1, 2, "Silencio mín. ms", "vad_minimum_silence_ms", 8)
        self._setting_entry(vad, 1, 4, "Padding ms", "vad_speech_padding_ms", 8)
        ttk.Label(vad, text="Intervalos").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(vad, textvariable=self._intervals).grid(
            row=2, column=1, columnspan=7, sticky="ew", pady=(4, 0)
        )
        vad.columnconfigure(7, weight=1)
        ttk.Label(
            vad,
            text="Ej.: 0-30, 01:15-02:00. Los intervalos requieren VAD desactivado.",
        ).grid(row=3, column=0, columnspan=8, sticky="w")

        prompts = ttk.LabelFrame(frame, text="Contexto lingüístico", padding=6)
        prompts.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        prompts.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(
            (
                ("Prompt inicial", self._initial_prompt),
                ("Prefijo", self._prefix),
                ("Hotwords", self._hotwords),
            )
        ):
            ttk.Label(prompts, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(prompts, textvariable=variable).grid(
                row=row, column=1, sticky="ew", padx=(6, 0), pady=2
            )
        ttk.Label(
            prompts, text="Prefijo y hotwords son incompatibles; su contenido no se ejecuta."
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        decoding = ttk.LabelFrame(frame, text="Decodificación avanzada", padding=6)
        decoding.grid(row=4, column=0, sticky="ew")
        fields = (
            ("Beam", "beam_size"),
            ("Best of", "best_of"),
            ("Patience", "patience"),
            ("Temperaturas", "temperatures"),
            ("Compresión", "compression_ratio_threshold"),
            ("Logprob", "log_probability_threshold"),
            ("No habla", "no_speech_threshold"),
            ("Repetición", "repetition_penalty"),
            ("N-gram", "no_repeat_ngram_size"),
            ("Tokens nuevos", "max_new_tokens"),
            ("Silencio alucinado", "hallucination_silence_seconds"),
            ("Detección idioma", "language_detection_threshold"),
            ("Segmentos detección", "language_detection_segments"),
            ("Puntuación previa", "prepend_punctuations"),
            ("Puntuación posterior", "append_punctuations"),
        )
        for index, (label, name) in enumerate(fields):
            row, pair = divmod(index, 4)
            self._setting_entry(decoding, row, pair * 2, label, name, 10)
        ttk.Label(decoding, text="Texto previo").grid(row=4, column=0, sticky="w")
        ttk.Combobox(
            decoding,
            textvariable=self._condition_previous,
            values=(_BACKEND_DEFAULT, "sí", "no"),
            state="readonly",
            width=10,
        ).grid(row=4, column=1, sticky="w", padx=(4, 12))
        ttk.Label(
            decoding,
            text=(
                "Vacío conserva el valor del backend. Timestamps por palabra siguen "
                "apagados por defecto."
            ),
        ).grid(row=5, column=0, columnspan=8, sticky="w", pady=(4, 0))
        return frame

    def _build_experimental(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(
            frame,
            text=(
                "Experimental: procesa un audio cerrado en ventanas solapadas y elimina palabras "
                "repetidas entre ventanas. No captura audio en vivo, no es streaming nativo y su "
                "latencia depende de cada fragmento."
            ),
            wraplength=850,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        controls = ttk.Frame(frame)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(controls, text="Fragmento (s)").pack(side=tk.LEFT)
        ttk.Entry(
            controls, textvariable=self._experimental_chunk_seconds, width=7
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(controls, text="Solapamiento (s)").pack(side=tk.LEFT)
        ttk.Entry(
            controls, textvariable=self._experimental_overlap_seconds, width=7
        ).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(controls, text="Máx. palabras deduplicadas").pack(side=tk.LEFT)
        ttk.Entry(
            controls, textvariable=self._experimental_deduplication_words, width=7
        ).pack(side=tk.LEFT, padx=(4, 10))
        self._experimental_button = ttk.Button(
            controls,
            text="Procesar audio seleccionado",
            command=self._start_experimental,
            state=(
                tk.NORMAL
                if self._service.experimental_dictation_available
                else tk.DISABLED
            ),
        )
        self._experimental_button.pack(side=tk.LEFT)
        ttk.Button(controls, text="Copiar texto", command=self._copy_experimental).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Label(frame, textvariable=self._experimental_status).grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        self._experimental_text = scrolledtext.ScrolledText(
            frame, wrap=tk.WORD, state=tk.DISABLED
        )
        self._experimental_text.grid(row=3, column=0, sticky="nsew")
        return frame

    def _setting_entry(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        label: str,
        name: str,
        width: int,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 4))
        ttk.Entry(parent, textvariable=self._advanced[name], width=width).grid(
            row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=2
        )

    def _build_progress(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        frame.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self._progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(frame, textvariable=self._progress_text).grid(row=0, column=1, padx=(8, 0))

    def refresh_models(self) -> None:
        self._status.set("Whisper: buscando modelos locales…")
        self._submit(self._service.refresh_models(), self._models_refreshed)

    def _models_refreshed(self, models: Sequence[ModelDescriptor]) -> None:
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            self._model_name.set(next((n for n in names if "small" in n.casefold()), names[0]))
        self._status.set(f"Whisper: {len(names)} modelo(s) local(es)")
        self._submit(self._service.active_model_state(), self._active_state_received)
        self._selection_changed()

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(title="Seleccionar audio", filetypes=_audio_types())
        if selected:
            self._discard_temporary_recording()
            self._source.set(selected)

    def _add_sources(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Agregar audios a la cola", filetypes=_audio_types()
        )
        self._enqueue_paths(Path(path) for path in selected)

    def _enqueue_paths(self, paths: Iterable[Path]) -> None:
        for path in paths:
            if not path.is_file():
                continue
            self._queue_counter += 1
            row = f"audio-{self._queue_counter}"
            self._queue_paths[row] = path.resolve()
            self._queue.insert("", tk.END, iid=row, values=(path.name, "pendiente"))

    def _move_row(self, offset: int) -> None:
        selection = self._queue.selection()
        if not selection:
            return
        row = selection[0]
        if self._row_operations.get(row):
            return
        children = list(self._queue.get_children())
        current = children.index(row)
        target = current + offset
        if 0 <= target < len(children):
            self._queue.move(row, "", target)

    def _remove_row(self) -> None:
        selection = self._queue.selection()
        if not selection:
            return
        row = selection[0]
        operation_id = self._row_operations.get(row)
        values = self._queue.item(row, "values")
        status = str(values[1]) if len(values) > 1 else ""
        if status in {"en cola", "procesando"}:
            if operation_id is not None:
                self._submit(self._service.cancel(operation_id), self._ignore_result)
            return
        self._queue.delete(row)
        self._queue_paths.pop(row, None)
        if operation_id is not None:
            self._documents.pop(operation_id, None)
            self._operation_rows.pop(operation_id, None)
            self._row_operations.pop(row, None)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._microphone_button.configure(state=tk.DISABLED)
            self._status.set("Finalizando grabación…")
            self._submit(self._service.stop_recording(), self._recording_stopped)
            return
        self._discard_temporary_recording()
        self._microphone_button.configure(state=tk.DISABLED)
        self._status.set("Iniciando micrófono…")
        self._submit(self._service.start_recording(), self._recording_started)

    def _recording_started(self, _: None) -> None:
        self._recording = True
        self._microphone_button.configure(text="Detener y transcribir", state=tk.NORMAL)
        self._status.set("Grabando desde el micrófono…")

    def _recording_stopped(self, source: Path) -> None:
        self._recording = False
        self._temporary_recording = source
        self._source.set(str(source))
        self._microphone_button.configure(text="Grabar micrófono", state=tk.NORMAL)
        self._enqueue_paths((source,))
        self._start()

    def _load(self) -> None:
        model = self._selected_model()
        if model is None:
            return
        self._status.set(f"Cargando {model.name}…")
        self._set_lifecycle_controls(False)
        self._submit(
            self._service.load_model(model, LoadPolicy(device=ComputeDevice(self._device.get()))),
            partial(self._state_received, announce=True),
        )

    def _unload(self) -> None:
        if self._loaded_model is None:
            return
        self._set_lifecycle_controls(False)
        self._submit(
            self._service.unload_model(self._loaded_model),
            partial(self._state_received, announce=True),
        )

    def _state_received(self, state: ModelState, *, announce: bool = False) -> None:
        loaded = state.loaded_in_ram or state.loaded_in_gpu
        self._loaded_model = state.model if loaded else None
        if loaded:
            device = state.active_device.value if state.active_device is not None else "desconocido"
            self._residency.set(f"Modelo residente: {state.model.name} ({device})")
        else:
            self._residency.set("Modelo residente: ninguno")
        self._set_lifecycle_controls(True)
        self._selection_changed()
        if announce:
            self._status.set(f"{state.model.name}: {'cargado' if loaded else 'liberado'}")

    def _active_state_received(self, state: ModelState | None) -> None:
        if state is None:
            self._loaded_model = None
            self._residency.set("Modelo residente: ninguno")
            self._set_lifecycle_controls(True)
            return
        self._state_received(state)

    def _selection_changed(self, _: object | None = None) -> None:
        model = self._selected_model()
        if model is None:
            return
        capabilities = self._service.transcription_capabilities(model)
        languages = (_AUTO_LANGUAGE,) + capabilities.source_language_codes
        self._language_selector.configure(values=languages)
        if self._language.get() not in languages:
            self._language.set(_AUTO_LANGUAGE)
        tasks = [TranscriptionTask.TRANSCRIBE.value]
        if capabilities.supports_translation:
            tasks.append(TranscriptionTask.TRANSLATE.value)
        self._task_selector.configure(values=tuple(tasks))
        if self._task.get() not in tasks:
            self._task.set(TranscriptionTask.TRANSCRIBE.value)
        target = "inglés" if capabilities.supports_translation else "sin traducción"
        verification = "verificado" if capabilities.capabilities_verified else "no verificado"
        detail = capabilities.limitation or "Capacidades locales disponibles."
        self._capability_text.set(
            f"Entrada: {len(capabilities.source_language_codes)} idioma(s); salida translate: "
            f"{target}; {verification}. {detail}"
        )
        if self._loaded_model is not None:
            selected = (
                "seleccionado"
                if model == self._loaded_model
                else f"seleccionado: {model.name}"
            )
            self._residency.set(f"Residente: {self._loaded_model.name} · {selected}")

    def _set_lifecycle_controls(self, enabled: bool) -> None:
        self._load_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self._unload_button.configure(
            state=tk.NORMAL if enabled and self._loaded_model is not None else tk.DISABLED
        )

    def _start(self) -> None:
        model = self._selected_model()
        if model is None:
            messagebox.showinfo("Sin modelo", "Selecciona un modelo Whisper local.")
            return
        pending = [
            row
            for row in self._queue.get_children()
            if str(self._queue.item(row, "values")[1]) == "pendiente"
        ]
        source = Path(self._source.get())
        if not pending and source.is_file():
            self._enqueue_paths((source,))
            pending = [self._queue.get_children()[-1]]
        if not pending:
            messagebox.showinfo("Cola vacía", "Selecciona o agrega al menos un audio.")
            return
        try:
            options = self._build_options()
        except (TypeError, ValueError) as error:
            messagebox.showerror("Opciones Whisper", str(error))
            return
        requests: list[TranscriptionRequest] = []
        for row in pending:
            operation_id = self._service.create_operation_id()
            request = TranscriptionRequest(
                operation_id=operation_id,
                model=model,
                source_path=self._queue_paths[row],
                options=options,
            )
            requests.append(request)
            self._operation_rows[operation_id] = row
            self._row_operations[row] = operation_id
            self._queue.item(row, values=(self._queue_paths[row].name, "en cola"))
        self._progress.configure(value=0)
        self._transcribe_button.configure(state=tk.DISABLED)
        self._experimental_button.configure(state=tk.DISABLED)
        self._microphone_button.configure(state=tk.DISABLED)
        self._set_lifecycle_controls(False)
        self._cancel_button.configure(state=tk.NORMAL)
        self._status.set(f"Procesando {len(requests)} audio(s) en orden FIFO…")
        self._submit(
            self._consume_queue(
                tuple(requests), LoadPolicy(device=ComputeDevice(self._device.get()))
            ),
            self._queue_finished,
        )

    def _start_experimental(self) -> None:
        model = self._selected_model()
        source = Path(self._source.get())
        if model is None or not source.is_file():
            messagebox.showinfo(
                "Sin audio",
                "Selecciona un archivo de audio local para la vista experimental.",
            )
            return
        try:
            transcription_options = self._build_options()
            if transcription_options.intervals:
                raise ValueError(
                    "Quita los intervalos manuales: este modo crea sus propias ventanas."
                )
            experimental_options = ExperimentalDictationOptions(
                chunk_seconds=float(self._experimental_chunk_seconds.get()),
                overlap_seconds=float(self._experimental_overlap_seconds.get()),
                deduplication_words=int(
                    self._experimental_deduplication_words.get()
                ),
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("Dictado experimental", str(error))
            return
        operation_id = self._service.create_operation_id()
        request = TranscriptionRequest(
            operation_id=operation_id,
            model=model,
            source_path=source,
            options=transcription_options,
        )
        self._experimental_operation_id = operation_id
        self._set_experimental_text("")
        self._experimental_status.set(
            "Inspeccionando duración; VAD se desactiva dentro de cada ventana…"
        )
        self._progress.configure(value=0)
        self._transcribe_button.configure(state=tk.DISABLED)
        self._experimental_button.configure(state=tk.DISABLED)
        self._microphone_button.configure(state=tk.DISABLED)
        self._cancel_button.configure(state=tk.NORMAL)
        self._set_lifecycle_controls(False)
        self._submit(
            self._consume_experimental(
                request,
                experimental_options,
                LoadPolicy(device=ComputeDevice(self._device.get())),
            ),
            self._experimental_finished,
        )

    async def _consume_experimental(
        self,
        request: TranscriptionRequest,
        options: ExperimentalDictationOptions,
        policy: LoadPolicy,
    ) -> None:
        try:
            async for event in self._service.stream_experimental_dictation(
                request,
                options,
                load_policy=policy,
            ):
                self._post(partial(self._handle_experimental_event, event))
        finally:
            state = await self._service.active_model_state()
            self._post(partial(self._active_state_received, state))

    def _handle_experimental_event(self, event: ExperimentalDictationEvent) -> None:
        if event.duration_seconds:
            self._progress.configure(
                value=min(event.processed_seconds / event.duration_seconds, 1.0) * 100
            )
        if event.kind is ExperimentalDictationEventKind.PROCESSING:
            self._experimental_status.set(event.message or "Procesando fragmento…")
        elif event.kind is ExperimentalDictationEventKind.CHUNK:
            self._set_experimental_text(event.cumulative_text)
            if event.chunk is not None:
                self._experimental_status.set(
                    f"Fragmento {event.chunk.index + 1} incorporado; "
                    f"{event.chunk.removed_prefix_words} palabra(s) repetida(s) eliminada(s)."
                )
        elif event.kind is ExperimentalDictationEventKind.COMPLETED:
            self._set_experimental_text(event.cumulative_text)
            self._experimental_status.set(
                "Completado. Revisa el texto: la deduplicación es heurística."
            )
            self._experimental_operation_id = None
        elif event.kind is ExperimentalDictationEventKind.CANCELLED:
            self._set_experimental_text(event.cumulative_text)
            self._experimental_status.set(event.message or "Vista experimental cancelada.")
            self._experimental_operation_id = None

    def _experimental_finished(self, _: None) -> None:
        self._experimental_operation_id = None
        self._transcribe_button.configure(state=tk.NORMAL)
        self._experimental_button.configure(
            state=(
                tk.NORMAL
                if self._service.experimental_dictation_available
                else tk.DISABLED
            )
        )
        self._microphone_button.configure(
            state=tk.NORMAL if self._service.microphone_available else tk.DISABLED
        )
        self._cancel_button.configure(state=tk.DISABLED)
        self._set_lifecycle_controls(True)

    def _copy_experimental(self) -> None:
        text = self._experimental_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._experimental_status.set("Texto copiado al portapapeles.")

    def _set_experimental_text(self, text: str) -> None:
        self._experimental_text.configure(state=tk.NORMAL)
        self._experimental_text.delete("1.0", tk.END)
        self._experimental_text.insert(tk.END, text)
        self._experimental_text.configure(state=tk.DISABLED)

    def _build_options(self) -> TranscriptionOptions:
        vad_mode = VadMode(self._vad_mode.get())
        vad_parameters = None
        if vad_mode is VadMode.CUSTOM:
            vad_parameters = VadParameters(
                threshold=_optional_float(self._advanced["vad_threshold"].get()),
                negative_threshold=_optional_float(
                    self._advanced["vad_negative_threshold"].get()
                ),
                minimum_speech_ms=_optional_int(
                    self._advanced["vad_minimum_speech_ms"].get()
                ),
                maximum_speech_seconds=_optional_float(
                    self._advanced["vad_maximum_speech_seconds"].get()
                ),
                minimum_silence_ms=_optional_int(
                    self._advanced["vad_minimum_silence_ms"].get()
                ),
                speech_padding_ms=_optional_int(
                    self._advanced["vad_speech_padding_ms"].get()
                ),
            )
        previous = self._condition_previous.get()
        condition_previous = None if previous == _BACKEND_DEFAULT else previous == "sí"
        language = self._language.get()
        return TranscriptionOptions(
            source_language=None if language == _AUTO_LANGUAGE else language,
            task=TranscriptionTask(self._task.get()),
            word_timestamps=self._word_timestamps.get(),
            vad_mode=vad_mode,
            vad_parameters=vad_parameters,
            intervals=_parse_intervals(self._intervals.get()),
            prompt=TranscriptionPromptOptions(
                initial_prompt=self._initial_prompt.get(),
                prefix=self._prefix.get(),
                hotwords=self._hotwords.get(),
            ),
            decoding=TranscriptionDecodingOptions(
                beam_size=_optional_int(self._advanced["beam_size"].get()),
                best_of=_optional_int(self._advanced["best_of"].get()),
                patience=_optional_float(self._advanced["patience"].get()),
                temperatures=_parse_temperatures(self._advanced["temperatures"].get()),
                compression_ratio_threshold=_optional_float(
                    self._advanced["compression_ratio_threshold"].get()
                ),
                log_probability_threshold=_optional_float(
                    self._advanced["log_probability_threshold"].get()
                ),
                no_speech_threshold=_optional_float(
                    self._advanced["no_speech_threshold"].get()
                ),
                repetition_penalty=_optional_float(
                    self._advanced["repetition_penalty"].get()
                ),
                no_repeat_ngram_size=_optional_int(
                    self._advanced["no_repeat_ngram_size"].get()
                ),
                max_new_tokens=_optional_int(self._advanced["max_new_tokens"].get()),
                hallucination_silence_seconds=_optional_float(
                    self._advanced["hallucination_silence_seconds"].get()
                ),
                prepend_punctuations=self._advanced["prepend_punctuations"].get() or None,
                append_punctuations=self._advanced["append_punctuations"].get() or None,
                language_detection_threshold=_optional_float(
                    self._advanced["language_detection_threshold"].get()
                ),
                language_detection_segments=_optional_int(
                    self._advanced["language_detection_segments"].get()
                ),
                condition_on_previous_text=condition_previous,
            ),
        )

    async def _consume_queue(
        self,
        requests: Sequence[TranscriptionRequest],
        policy: LoadPolicy,
    ) -> None:
        try:
            async for event in self._service.stream_queue(requests, load_policy=policy):
                self._post(partial(self._handle_event, event))
        finally:
            state = await self._service.active_model_state()
            self._post(partial(self._active_state_received, state))

    def _handle_event(self, event: TranscriptionEvent) -> None:
        row = self._operation_rows.get(event.operation_id)
        if row is None or not self._queue.exists(row):
            return
        audio = self._queue_paths[row].name
        if event.kind is TranscriptionEventKind.STARTED:
            self._operation_id = event.operation_id
            self._visible_operation = event.operation_id
            self._queue.item(row, values=(audio, "procesando"))
            self._queue.selection_set(row)
            self._set_text("")
            self._result_metadata.set("Procesando…")
        elif event.kind is TranscriptionEventKind.SEGMENT and event.segment is not None:
            if self._visible_operation == event.operation_id:
                self._append(event.segment.text)
        elif event.kind is TranscriptionEventKind.PROGRESS and event.progress is not None:
            if event.progress.fraction is not None:
                self._progress.configure(value=event.progress.fraction * 100)
            self._progress_text.set(event.progress.detail or event.progress.stage.value)
        elif event.kind in {TranscriptionEventKind.COMPLETED, TranscriptionEventKind.CANCELLED}:
            status = "completado" if event.kind is TranscriptionEventKind.COMPLETED else "cancelado"
            self._queue.item(row, values=(audio, status))
            if event.result is not None:
                self._documents[event.operation_id] = self._service.create_document(event.result)
                self._show_document(event.operation_id)
            self._operation_id = None
        elif event.kind is TranscriptionEventKind.ERROR:
            self._queue.item(row, values=(audio, "falló"))
            self._operation_id = None
            messagebox.showerror("Whisper", event.message or "Error desconocido")

    def _queue_finished(self, _: None) -> None:
        self._operation_id = None
        self._status.set("Cola finalizada")
        self._transcribe_button.configure(state=tk.NORMAL)
        self._experimental_button.configure(
            state=(
                tk.NORMAL
                if self._service.experimental_dictation_available
                else tk.DISABLED
            )
        )
        self._microphone_button.configure(
            state=tk.NORMAL if self._service.microphone_available else tk.DISABLED
        )
        self._cancel_button.configure(state=tk.DISABLED)
        self._set_lifecycle_controls(True)
        self._discard_temporary_recording()

    def _cancel(self) -> None:
        if self._experimental_operation_id is not None:
            self._status.set("Cancelando vista experimental…")
            self._submit(
                self._service.cancel(self._experimental_operation_id),
                self._ignore_result,
            )
            return
        selection = self._queue.selection()
        operation_id = None
        if selection:
            operation_id = self._row_operations.get(selection[0])
        operation_id = operation_id or self._operation_id
        if operation_id is not None:
            self._status.set("Cancelando tarea seleccionada…")
            self._submit(self._service.cancel(operation_id), self._ignore_result)

    def _queue_selected(self, _: object | None = None) -> None:
        selection = self._queue.selection()
        if not selection:
            return
        operation_id = self._row_operations.get(selection[0])
        if operation_id in self._documents:
            self._show_document(operation_id)

    def _show_document(self, operation_id: str) -> None:
        document = self._documents.get(operation_id)
        if document is None:
            return
        self._visible_operation = operation_id
        result = document.corrected_result
        self._set_text(result.text)
        probability = (
            f" {result.source_language_probability:.1%}"
            if result.source_language_probability is not None
            else ""
        )
        vad = (
            f" · VAD descartó {result.vad_removed_seconds:.2f}s"
            if result.vad_removed_seconds is not None
            else ""
        )
        device = result.device.value if result.device is not None else "dispositivo desconocido"
        corrected = f" · {len(document.corrections)} corrección(es)" if document.corrections else ""
        self._result_metadata.set(
            f"Entrada {result.source_language or '?'}{probability} · salida "
            f"{result.output_language or '?'} · {device} {result.compute_type or ''}"
            f"{vad}{corrected}"
        )
        self._export_button.configure(state=tk.NORMAL)
        self._refresh_detail()

    def _refresh_detail(self, _: object | None = None) -> None:
        self._detail.delete(*self._detail.get_children())
        self._detail_segments.clear()
        document = self._current_document()
        if document is None:
            return
        query = self._detail_query.get()
        hits = self._service.search(document, query, include_words=self._show_words.get())
        for count, hit in enumerate(hits):
            kind = "Palabra" if hit.word_index is not None else "Segmento"
            segment = _segment_by_index(document, hit.segment_index)
            confidence = (
                f"logp {segment.average_log_probability:.3f}"
                if hit.word_index is None
                and segment.average_log_probability is not None
                else ""
            )
            if hit.word_index is not None:
                probability = segment.words[hit.word_index].probability
                confidence = f"{probability:.1%}" if probability is not None else ""
            row = f"detail-{count}"
            marker = " ✎" if hit.corrected else ""
            self._detail.insert(
                "",
                tk.END,
                iid=row,
                values=(
                    kind + marker,
                    _format_seconds(hit.start_seconds),
                    _format_seconds(hit.end_seconds),
                    hit.text.strip(),
                    confidence,
                ),
            )
            if hit.word_index is None:
                self._detail_segments[row] = hit.segment_index

    def _edit_segment(self, _: object | None = None) -> None:
        document = self._current_document()
        selection = self._detail.selection()
        if document is None or not selection:
            return
        segment_index = self._detail_segments.get(selection[0])
        if segment_index is None:
            return
        current = _segment_by_index(document, segment_index).text
        replacement = simpledialog.askstring(
            "Corregir segmento",
            "La salida original se conservará en el JSON detallado:",
            initialvalue=current,
            parent=self,
        )
        if replacement is None:
            return
        try:
            updated = self._service.correct_segment(document, segment_index, replacement)
        except ValueError as error:
            messagebox.showerror("Corrección", str(error))
            return
        if self._visible_operation is not None:
            self._documents[self._visible_operation] = updated
            self._show_document(self._visible_operation)

    def _discard_correction(self) -> None:
        document = self._current_document()
        selection = self._detail.selection()
        if document is None or not selection or self._visible_operation is None:
            return
        segment_index = self._detail_segments.get(selection[0])
        if segment_index is None:
            return
        updated = self._service.discard_correction(document, segment_index)
        self._documents[self._visible_operation] = updated
        self._show_document(self._visible_operation)

    def _current_document(self) -> TranscriptionDocument | None:
        if self._visible_operation is None:
            return None
        return self._documents.get(self._visible_operation)

    def _apply_preset(self) -> None:
        preset = self._preset.get()
        self._reset_advanced()
        if preset == "Rápido":
            self._advanced["beam_size"].set("1")
            self._advanced["best_of"].set("1")
            self._advanced["temperatures"].set("0")
        elif preset == "Preciso":
            self._advanced["beam_size"].set("5")
            self._advanced["best_of"].set("5")
            self._advanced["temperatures"].set("0, 0.2, 0.4, 0.6, 0.8, 1")
        elif preset == "Audio con pausas":
            self._vad_mode.set(VadMode.CUSTOM.value)
            self._advanced["vad_threshold"].set("0.5")
            self._advanced["vad_minimum_silence_ms"].set("1200")

    def _reset_advanced(self) -> None:
        for variable in self._advanced.values():
            variable.set("")
        self._condition_previous.set(_BACKEND_DEFAULT)
        self._vad_mode.set(VadMode.AUTOMATIC.value)

    def _discard_temporary_recording(self) -> None:
        path = self._temporary_recording
        self._temporary_recording = None
        if path is not None:
            if self._source.get() == str(path):
                self._source.set("")
            self._submit(self._service.remove_temporary_recording(path), self._ignore_result)

    def _export(self) -> None:
        document = self._current_document()
        if document is None:
            return
        destination = filedialog.asksaveasfilename(
            title="Exportar transcripción",
            defaultextension=".txt",
            filetypes=(
                ("Texto", "*.txt"),
                ("SubRip", "*.srt"),
                ("WebVTT", "*.vtt"),
                ("CSV detallado", "*.csv"),
                ("TSV detallado", "*.tsv"),
                ("JSON reproducible", "*.json"),
            ),
        )
        if destination:
            self._submit(
                asyncio.to_thread(self._service.export, document, Path(destination)),
                lambda path: self._status.set(f"Exportado: {path.name}"),
            )

    def _selected_model(self) -> ModelId | None:
        descriptor = self._models.get(self._model_name.get())
        return descriptor.id if descriptor is not None else None

    def _set_text(self, text: str) -> None:
        self._transcript.configure(state=tk.NORMAL)
        self._transcript.delete("1.0", tk.END)
        self._transcript.insert(tk.END, text)
        self._transcript.configure(state=tk.DISABLED)

    def _append(self, text: str) -> None:
        self._transcript.configure(state=tk.NORMAL)
        self._transcript.insert(tk.END, text)
        self._transcript.see(tk.END)
        self._transcript.configure(state=tk.DISABLED)

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
        if self._recording:
            self._recording = False
            self._submit(self._service.cancel_recording(), self._ignore_result)
        self._microphone_button.configure(text="Grabar micrófono")
        self._transcribe_button.configure(state=tk.NORMAL)
        self._experimental_operation_id = None
        self._experimental_button.configure(
            state=(
                tk.NORMAL
                if self._service.experimental_dictation_available
                else tk.DISABLED
            )
        )
        self._cancel_button.configure(state=tk.DISABLED)
        self._set_lifecycle_controls(True)
        self._status.set("Operación fallida")
        self._submit(self._service.active_model_state(), self._active_state_received)
        messagebox.showerror("Whisper", str(error))

    def _post(self, callback: Callable[[], None]) -> None:
        self._callbacks.put(callback)

    def _drain_callbacks(self) -> None:
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)


def _audio_types() -> tuple[tuple[str, str], ...]:
    return (
        ("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.webm *.mp4"),
        ("Todos", "*.*"),
    )


def _optional_float(value: str) -> float | None:
    normalized = value.strip().replace(",", ".")
    return float(normalized) if normalized else None


def _optional_int(value: str) -> int | None:
    normalized = value.strip()
    return int(normalized) if normalized else None


def _parse_temperatures(value: str) -> tuple[float, ...] | None:
    normalized = value.strip()
    if not normalized:
        return None
    return tuple(float(item.strip().replace(",", ".")) for item in normalized.split(","))


def _parse_intervals(value: str) -> tuple[AudioInterval, ...]:
    normalized = value.strip()
    if not normalized:
        return ()
    intervals: list[AudioInterval] = []
    for raw_interval in normalized.split(","):
        limits = raw_interval.strip().split("-", maxsplit=1)
        if len(limits) != 2:
            raise ValueError("Usa intervalos inicio-fin separados por coma.")
        intervals.append(
            AudioInterval(
                start_seconds=_parse_time(limits[0]),
                end_seconds=_parse_time(limits[1]),
            )
        )
    return tuple(intervals)


def _parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) > 3 or not parts:
        raise ValueError(f"Tiempo no válido: {value!r}.")
    try:
        numbers = [float(part.replace(",", ".")) for part in parts]
    except ValueError as error:
        raise ValueError(f"Tiempo no válido: {value!r}.") from error
    if len(numbers) > 1 and any(number < 0 or number >= 60 for number in numbers[1:]):
        raise ValueError(f"Minutos y segundos deben estar entre 0 y 59: {value!r}.")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds < 0:
        raise ValueError("Los tiempos no pueden ser negativos.")
    return seconds


def _format_seconds(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:06.3f}"
    return f"{minutes:02d}:{remainder:06.3f}"


def _segment_by_index(document: TranscriptionDocument, index: int) -> TranscriptionSegment:
    return next(segment for segment in document.corrected_result.segments if segment.index == index)

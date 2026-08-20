"""Tkinter tab for local faster-whisper transcription."""

from __future__ import annotations

import asyncio
import tkinter as tk
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from functools import partial
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    TranscriptionEvent,
    TranscriptionEventKind,
    TranscriptionOptions,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionTask,
)
from aiopenstudio.services import TranscriptionService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")


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
        self._recording = False
        self._temporary_recording: Path | None = None
        self._result: TranscriptionResult | None = None
        self._source = tk.StringVar()
        self._model_name = tk.StringVar()
        self._device = tk.StringVar(value=ComputeDevice.AUTO.value)
        self._language = tk.StringVar()
        self._task = tk.StringVar(value=TranscriptionTask.TRANSCRIBE.value)
        self._status = tk.StringVar(value="Whisper: comprobación pendiente")
        self._residency = tk.StringVar(value="Modelo residente: ninguno")
        self._progress_text = tk.StringVar(value="Sin operación activa")
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh_models()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        status = ttk.Frame(self)
        status.grid(row=0, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(status, text="Actualizar", command=self.refresh_models).grid(row=0, column=1)

        source = ttk.Frame(self)
        source.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="Audio").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(source, textvariable=self._source, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(source, text="Examinar…", command=self._choose_source).grid(
            row=0, column=2, padx=(8, 0)
        )
        self._microphone_button = ttk.Button(
            source,
            text="Grabar micrófono",
            command=self._toggle_recording,
            state=tk.NORMAL if self._service.microphone_available else tk.DISABLED,
        )
        self._microphone_button.grid(row=0, column=3, padx=(6, 0))

        options = ttk.Frame(self)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Modelo").grid(row=0, column=0, padx=(0, 6))
        self._model_selector = ttk.Combobox(
            options, textvariable=self._model_name, state="readonly"
        )
        self._model_selector.grid(row=0, column=1, sticky="ew")
        self._model_selector.bind("<<ComboboxSelected>>", self._selection_changed)
        ttk.Label(options, text="Dispositivo").grid(row=0, column=2, padx=(12, 6))
        ttk.Combobox(
            options,
            textvariable=self._device,
            values=tuple(device.value for device in ComputeDevice),
            state="readonly",
            width=8,
        ).grid(row=0, column=3)
        ttk.Label(options, text="Idioma").grid(row=0, column=4, padx=(12, 6))
        ttk.Entry(options, textvariable=self._language, width=8).grid(row=0, column=5)
        ttk.Label(options, text="Tarea").grid(row=0, column=6, padx=(12, 6))
        ttk.Combobox(
            options,
            textvariable=self._task,
            values=tuple(task.value for task in TranscriptionTask),
            state="readonly",
            width=11,
        ).grid(row=0, column=7)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._transcribe_button = ttk.Button(actions, text="Transcribir", command=self._start)
        self._transcribe_button.pack(side=tk.LEFT)
        self._cancel_button = ttk.Button(
            actions, text="Cancelar", command=self._cancel, state=tk.DISABLED
        )
        self._cancel_button.pack(side=tk.LEFT, padx=(6, 0))
        self._load_button = ttk.Button(actions, text="Cargar / cambiar", command=self._load)
        self._load_button.pack(side=tk.LEFT, padx=(12, 0))
        self._unload_button = ttk.Button(
            actions, text="Liberar residente", command=self._unload, state=tk.DISABLED
        )
        self._unload_button.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(actions, textvariable=self._residency).pack(side=tk.LEFT, padx=(14, 0))
        self._export_button = ttk.Button(
            actions, text="Exportar…", command=self._export, state=tk.DISABLED
        )
        self._export_button.pack(side=tk.RIGHT)

        self._transcript = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED)
        self._transcript.grid(row=4, column=0, sticky="nsew")

        progress = ttk.Frame(self)
        progress.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        progress.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self._progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress, textvariable=self._progress_text).grid(row=0, column=1, padx=(8, 0))

    def refresh_models(self) -> None:
        self._status.set("Whisper: buscando modelos locales…")
        self._submit(self._service.refresh_models(), self._models_refreshed)

    def _models_refreshed(self, models: Sequence[ModelDescriptor]) -> None:
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            small = next((name for name in names if "small" in name.casefold()), names[0])
            self._model_name.set(small)
        self._status.set(f"Whisper: {len(names)} modelo(s) local(es)")
        self._submit(self._service.active_model_state(), self._active_state_received)
        self._selection_changed()

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="Seleccionar audio",
            filetypes=(
                ("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus *.webm *.mp4"),
                ("Todos", "*.*"),
            ),
        )
        if selected:
            self._discard_temporary_recording()
            self._source.set(selected)

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
        self._start()

    def _load(self) -> None:
        model = self._selected_model()
        if model is None:
            return
        if self._loaded_model is not None and self._loaded_model != model:
            self._status.set(f"Cambiando {self._loaded_model.name} → {model.name}…")
        else:
            self._status.set(f"Cargando {model.name}…")
        self._set_lifecycle_controls(False)
        self._submit(
            self._service.load_model(model, LoadPolicy(device=ComputeDevice(self._device.get()))),
            partial(self._state_received, announce=True),
        )

    def _unload(self) -> None:
        model = self._loaded_model
        if model is None:
            messagebox.showinfo("Sin modelo residente", "Whisper no tiene un modelo cargado.")
            return
        self._status.set(f"Liberando {model.name}…")
        self._set_lifecycle_controls(False)
        self._submit(
            self._service.unload_model(model), partial(self._state_received, announce=True)
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
            action = "cargado" if loaded else "liberado"
            self._status.set(f"{state.model.name}: {action}")

    def _active_state_received(self, state: ModelState | None) -> None:
        if state is None:
            self._loaded_model = None
            self._residency.set("Modelo residente: ninguno")
            self._set_lifecycle_controls(True)
            self._selection_changed()
            return
        self._state_received(state)

    def _selection_changed(self, _: object | None = None) -> None:
        selected = self._selected_model()
        if self._loaded_model is None or selected is None:
            return
        if selected == self._loaded_model:
            self._residency.set(f"Modelo residente: {self._loaded_model.name} · seleccionado")
        else:
            self._residency.set(
                f"Residente: {self._loaded_model.name} · seleccionado: {selected.name}"
            )

    def _set_lifecycle_controls(self, enabled: bool) -> None:
        self._load_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self._unload_button.configure(
            state=tk.NORMAL if enabled and self._loaded_model is not None else tk.DISABLED
        )

    def _start(self) -> None:
        model = self._selected_model()
        source = Path(self._source.get())
        if model is None or not source.is_file():
            messagebox.showinfo("Sin audio", "Selecciona un archivo de audio local.")
            return
        operation_id = self._service.create_operation_id()
        request = TranscriptionRequest(
            operation_id=operation_id,
            model=model,
            source_path=source,
            options=TranscriptionOptions(
                language=self._language.get().strip() or None,
                task=TranscriptionTask(self._task.get()),
            ),
        )
        self._operation_id = operation_id
        self._result = None
        self._set_text("")
        self._progress.configure(value=0)
        self._transcribe_button.configure(state=tk.DISABLED)
        self._microphone_button.configure(state=tk.DISABLED)
        self._set_lifecycle_controls(False)
        self._cancel_button.configure(state=tk.NORMAL)
        self._export_button.configure(state=tk.DISABLED)
        self._status.set("Preparando transcripción…")
        self._submit(
            self._consume(request, LoadPolicy(device=ComputeDevice(self._device.get()))),
            self._ignore_result,
        )

    async def _consume(self, request: TranscriptionRequest, policy: LoadPolicy) -> None:
        try:
            async for event in self._service.stream_transcription(request, load_policy=policy):
                self._post(partial(self._handle_event, event))
        finally:
            state = await self._service.active_model_state()
            self._post(partial(self._active_state_received, state))

    def _handle_event(self, event: TranscriptionEvent) -> None:
        if event.kind is TranscriptionEventKind.SEGMENT and event.segment is not None:
            self._append(event.segment.text)
        elif event.kind is TranscriptionEventKind.PROGRESS and event.progress is not None:
            fraction = event.progress.fraction
            if fraction is not None:
                self._progress.configure(value=fraction * 100)
            self._progress_text.set(event.progress.detail or event.progress.stage.value)
        elif event.kind in {
            TranscriptionEventKind.COMPLETED,
            TranscriptionEventKind.CANCELLED,
        }:
            self._result = event.result
            completed = event.result is not None and not event.result.cancelled
            self._finish("Transcripción completada" if completed else "Transcripción cancelada")
        elif event.kind is TranscriptionEventKind.ERROR:
            self._finish("Transcripción fallida")
            messagebox.showerror("Whisper", event.message or "Error desconocido")

    def _cancel(self) -> None:
        if self._operation_id is not None:
            self._status.set("Cancelando…")
            self._submit(self._service.cancel(self._operation_id), self._ignore_result)

    def _finish(self, status: str) -> None:
        self._operation_id = None
        self._status.set(status)
        self._transcribe_button.configure(state=tk.NORMAL)
        self._microphone_button.configure(
            state=tk.NORMAL if self._service.microphone_available else tk.DISABLED
        )
        self._cancel_button.configure(state=tk.DISABLED)
        self._export_button.configure(state=tk.NORMAL if self._result else tk.DISABLED)
        self._set_lifecycle_controls(True)
        self._discard_temporary_recording()

    def _discard_temporary_recording(self) -> None:
        path = self._temporary_recording
        self._temporary_recording = None
        if path is not None:
            if self._source.get() == str(path):
                self._source.set("")
            self._submit(self._service.remove_temporary_recording(path), self._ignore_result)

    def _export(self) -> None:
        if self._result is None:
            return
        destination = filedialog.asksaveasfilename(
            title="Exportar transcripción",
            defaultextension=".txt",
            filetypes=(
                ("Texto", "*.txt"),
                ("SubRip", "*.srt"),
                ("WebVTT", "*.vtt"),
                ("JSON", "*.json"),
            ),
        )
        if destination:
            self._submit(
                asyncio.to_thread(self._service.export, self._result, Path(destination)),
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
        self._set_lifecycle_controls(True)
        self._finish("Operación fallida")
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

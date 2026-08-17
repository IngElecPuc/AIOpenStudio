"""Tkinter tab for the backend-neutral LLM service."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from functools import partial
from queue import Empty, SimpleQueue
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    ChatOptions,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    RuntimeEvent,
    RuntimeEventKind,
)
from aiopenstudio.services import LLMService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")


class LLMTab(ttk.Frame):
    """Chat and lifecycle controls without importing or invoking the Ollama SDK."""

    def __init__(self, parent: tk.Misc, service: LLMService, runner: AsyncLoopRunner) -> None:
        super().__init__(parent, padding=12)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._models: dict[str, ModelDescriptor] = {}
        self._conversation_id = service.create_conversation().id
        self._operation_id: str | None = None

        self._status = tk.StringVar(value="Ollama: comprobación pendiente")
        self._model_name = tk.StringVar()
        self._keep_alive = tk.StringVar(value="600")
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh_models()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        status_bar = ttk.Frame(self)
        status_bar.grid(row=0, column=0, sticky="ew")
        status_bar.columnconfigure(0, weight=1)
        ttk.Label(status_bar, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(status_bar, text="Actualizar", command=self.refresh_models).grid(
            row=0, column=1, padx=(8, 0)
        )

        controls = ttk.Frame(self)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Modelo").grid(row=0, column=0, padx=(0, 6))
        self._model_selector = ttk.Combobox(
            controls,
            textvariable=self._model_name,
            state="readonly",
        )
        self._model_selector.grid(row=0, column=1, sticky="ew")
        ttk.Label(controls, text="Keep-alive (s)").grid(row=0, column=2, padx=(12, 6))
        ttk.Entry(controls, textvariable=self._keep_alive, width=8).grid(row=0, column=3)
        ttk.Button(controls, text="Cargar", command=self._load_model).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Button(controls, text="Liberar", command=self._unload_model).grid(
            row=0, column=5, padx=(8, 0)
        )

        self._transcript = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED)
        self._transcript.grid(row=2, column=0, sticky="nsew")

        composer = ttk.Frame(self)
        composer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        composer.columnconfigure(0, weight=1)
        self._prompt = tk.Text(composer, height=4, wrap=tk.WORD)
        self._prompt.grid(row=0, column=0, rowspan=2, sticky="ew")
        self._send_button = ttk.Button(composer, text="Enviar", command=self._send)
        self._send_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")
        self._cancel_button = ttk.Button(
            composer,
            text="Cancelar",
            command=self._cancel,
            state=tk.DISABLED,
        )
        self._cancel_button.grid(row=1, column=1, padx=(8, 0), pady=(6, 0), sticky="ew")

    def refresh_models(self) -> None:
        self._status.set("Ollama: consultando catálogo…")
        self._submit(self._service.refresh_models(), self._models_refreshed)

    def _models_refreshed(self, models: Sequence[ModelDescriptor]) -> None:
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            self._model_name.set(names[0])
        self._status.set(f"Ollama: disponible · {len(names)} modelo(s) instalado(s)")

    def _load_model(self) -> None:
        model = self._selected_model()
        if model is None:
            return
        try:
            keep_alive = float(self._keep_alive.get())
            policy = LoadPolicy(idle_timeout_seconds=keep_alive)
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
        if model is None:
            return
        self._status.set(f"Liberando {model.name}…")
        self._submit(self._service.unload_model(model), self._state_received)

    def _state_received(self, state: ModelState) -> None:
        self._status.set(
            f"{state.model.name} · RAM {state.ram_residency.value} · "
            f"GPU {state.gpu_residency.value}"
        )

    def _send(self) -> None:
        model = self._selected_model()
        prompt = self._prompt.get("1.0", tk.END).strip()
        if model is None or not prompt:
            return
        try:
            keep_alive = float(self._keep_alive.get())
            if keep_alive < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Keep-alive inválido", "Ingresa cero o una cantidad positiva.")
            return

        operation_id = self._service.create_operation_id()
        self._operation_id = operation_id
        self._prompt.delete("1.0", tk.END)
        self._append(f"\nTú: {prompt}\n\nAsistente: ")
        self._send_button.configure(state=tk.DISABLED)
        self._cancel_button.configure(state=tk.NORMAL)
        self._status.set(f"Generando con {model.name}…")
        self._submit(
            self._consume_chat(operation_id, model, prompt, keep_alive),
            self._ignore_result,
        )

    async def _consume_chat(
        self,
        operation_id: str,
        model: ModelId,
        prompt: str,
        keep_alive: float,
    ) -> None:
        async for event in self._service.stream_chat(
            operation_id=operation_id,
            conversation_id=self._conversation_id,
            model=model,
            prompt=prompt,
            options=ChatOptions(),
            keep_alive_seconds=keep_alive,
        ):
            self._post(partial(self._handle_runtime_event, event))

    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        if event.kind is RuntimeEventKind.TEXT_DELTA:
            text = event.payload.get("text")
            if isinstance(text, str):
                self._append(text)
        elif event.kind is RuntimeEventKind.COMPLETED:
            self._finish_operation("Generación completada")
        elif event.kind is RuntimeEventKind.CANCELLED:
            self._append("\n[generación cancelada]")
            self._finish_operation("Generación cancelada")
        elif event.kind is RuntimeEventKind.ERROR:
            self._finish_operation("Error de Ollama")
            message = str(event.payload.get("message", "Error desconocido"))
            messagebox.showerror("Error durante la generación", message)

    def _cancel(self) -> None:
        if self._operation_id is None:
            return
        self._status.set("Cancelando…")
        self._submit(self._service.cancel(self._operation_id), self._ignore_result)

    def _finish_operation(self, status: str) -> None:
        self._operation_id = None
        self._send_button.configure(state=tk.NORMAL)
        self._cancel_button.configure(state=tk.DISABLED)
        self._status.set(status)
        self._append("\n")

    def _selected_model(self) -> ModelId | None:
        descriptor = self._models.get(self._model_name.get())
        if descriptor is None:
            messagebox.showinfo(
                "Sin modelo",
                "Actualiza el catálogo y selecciona un modelo instalado.",
            )
            return None
        return descriptor.id

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

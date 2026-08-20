"""Tkinter tab for queued, supervised Fooocus image generation."""

from __future__ import annotations

import asyncio
import tkinter as tk
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future
from functools import partial
from importlib import import_module
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationOptions,
    ImageGenerationRequest,
    ImagePerformance,
    ModelDescriptor,
    RuntimeHealth,
)
from aiopenstudio.services import ImageGenerationService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")


class FooocusTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        service: ImageGenerationService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent, padding=12)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._models: dict[str, ModelDescriptor] = {}
        self._thumbnails: list[Any] = []
        self._model_name = tk.StringVar()
        self._performance = tk.StringVar(value=ImagePerformance.SPEED.value)
        self._dimensions = tk.StringVar(value="1024×1024")
        self._count = tk.IntVar(value=1)
        self._seed = tk.StringVar()
        self._guidance = tk.DoubleVar(value=4.0)
        self._sharpness = tk.DoubleVar(value=2.0)
        self._styles = tk.StringVar(value="Fooocus V2")
        self._format = tk.StringVar(value="png")
        self._status = tk.StringVar(value="Fooocus: comprobación pendiente")
        self._progress_text = tk.StringVar(value="Sin operación activa")
        self._build()
        self.after(50, self._drain_callbacks)
        self.refresh()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self._status).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Actualizar", command=self.refresh).grid(row=0, column=1)

        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.grid(row=1, column=0, sticky="nsew")
        controls = ttk.Frame(panes, padding=(0, 0, 10, 0))
        results = ttk.Frame(panes)
        panes.add(controls, weight=2)
        panes.add(results, weight=3)
        self._build_controls(controls)
        self._build_results(results)

        progress = ttk.Frame(self)
        progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(progress, maximum=100, mode="determinate")
        self._progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress, textvariable=self._progress_text).grid(
            row=0, column=1, padx=(8, 0)
        )

    def _build_controls(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(7, weight=1)
        ttk.Label(parent, text="Checkpoint").grid(row=0, column=0, sticky="w")
        self._model_selector = ttk.Combobox(
            parent, textvariable=self._model_name, state="readonly"
        )
        self._model_selector.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        options = ttk.Frame(parent)
        options.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(options, text="Rendimiento").pack(side=tk.LEFT)
        ttk.Combobox(
            options,
            textvariable=self._performance,
            values=tuple(item.value for item in ImagePerformance),
            state="readonly",
            width=14,
        ).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(options, text="Tamaño").pack(side=tk.LEFT)
        ttk.Combobox(
            options,
            textvariable=self._dimensions,
            values=("1024×1024", "1152×896", "896×1152", "1344×768", "768×1344"),
            state="readonly",
            width=11,
        ).pack(side=tk.LEFT, padx=(6, 0))

        numeric = ttk.Frame(parent)
        numeric.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(numeric, text="Imágenes").pack(side=tk.LEFT)
        ttk.Spinbox(numeric, from_=1, to=8, textvariable=self._count, width=4).pack(
            side=tk.LEFT, padx=(6, 12)
        )
        ttk.Label(numeric, text="Seed (vacío = aleatorio)").pack(side=tk.LEFT)
        ttk.Entry(numeric, textvariable=self._seed, width=14).pack(side=tk.LEFT, padx=(6, 0))

        tuning = ttk.Frame(parent)
        tuning.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(tuning, text="Guidance").pack(side=tk.LEFT)
        ttk.Spinbox(
            tuning, from_=1, to=30, increment=0.5, textvariable=self._guidance, width=6
        ).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(tuning, text="Sharpness").pack(side=tk.LEFT)
        ttk.Spinbox(
            tuning, from_=0, to=30, increment=0.5, textvariable=self._sharpness, width=6
        ).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(tuning, text="Formato").pack(side=tk.LEFT)
        ttk.Combobox(
            tuning,
            textvariable=self._format,
            values=("png", "jpeg", "webp"),
            state="readonly",
            width=7,
        ).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(parent, text="Estilos (separados por coma)").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Entry(parent, textvariable=self._styles).grid(
            row=5, column=0, columnspan=2, sticky="ew"
        )
        ttk.Label(parent, text="Prompt").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        self._prompt = scrolledtext.ScrolledText(parent, height=8, wrap=tk.WORD)
        self._prompt.grid(row=7, column=0, columnspan=2, sticky="nsew")
        ttk.Label(parent, text="Prompt negativo").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        self._negative = scrolledtext.ScrolledText(parent, height=4, wrap=tk.WORD)
        self._negative.grid(row=9, column=0, columnspan=2, sticky="ew")
        actions = ttk.Frame(parent)
        actions.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._generate_button = ttk.Button(actions, text="Añadir a cola", command=self._start)
        self._generate_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="Cancelar seleccionado", command=self._cancel).pack(
            side=tk.LEFT, padx=(6, 0)
        )

    def _build_results(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        ttk.Label(parent, text="Cola y ejecuciones").grid(row=0, column=0, sticky="w")
        self._queue = ttk.Treeview(
            parent, columns=("id", "model", "status"), show="headings", height=6
        )
        self._queue.heading("id", text="Ejecución")
        self._queue.heading("model", text="Checkpoint")
        self._queue.heading("status", text="Estado")
        self._queue.column("id", width=90, stretch=False)
        self._queue.column("model", width=150)
        self._queue.column("status", width=170)
        self._queue.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(parent, text="Galería de esta sesión").grid(row=2, column=0, sticky="w")
        self._gallery = ttk.Frame(parent)
        self._gallery.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        for column in range(3):
            self._gallery.columnconfigure(column, weight=1)

    def refresh(self) -> None:
        self._status.set("Fooocus: comprobando instalación y checkpoints…")
        self._submit(self._refresh(), self._refreshed)

    async def _refresh(self) -> tuple[RuntimeHealth, Sequence[ModelDescriptor], Sequence[str]]:
        health = await self._service.health()
        models = await self._service.refresh_models()
        styles: Sequence[str] = ()
        if health is not RuntimeHealth.UNAVAILABLE:
            styles = await self._service.list_styles()
        return health, models, styles

    def _refreshed(
        self, payload: tuple[RuntimeHealth, Sequence[ModelDescriptor], Sequence[str]]
    ) -> None:
        health, models, styles = payload
        self._models = {model.display_name: model for model in models}
        names = tuple(self._models)
        self._model_selector.configure(values=names)
        if names and self._model_name.get() not in self._models:
            self._model_name.set(names[0])
        if styles:
            default_style = next(
                (style for style in styles if style.casefold() == "fooocus v2"), styles[0]
            )
            self._styles.set(default_style)
        issues = self._service.preflight()
        if issues:
            self._status.set(f"Fooocus no disponible: {issues[0]}")
        else:
            self._status.set(f"Fooocus {health.value}: {len(names)} checkpoint(s) local(es)")
        self._generate_button.configure(state=tk.NORMAL if names and not issues else tk.DISABLED)

    def _start(self) -> None:
        descriptor = self._models.get(self._model_name.get())
        prompt = self._prompt.get("1.0", tk.END).strip()
        if descriptor is None or not prompt:
            messagebox.showinfo("Fooocus", "Selecciona un checkpoint y escribe un prompt.")
            return
        try:
            width_text, height_text = self._dimensions.get().split("×", maxsplit=1)
            seed_text = self._seed.get().strip()
            options = ImageGenerationOptions(
                width=int(width_text),
                height=int(height_text),
                image_count=self._count.get(),
                seed=int(seed_text) if seed_text else None,
                performance=ImagePerformance(self._performance.get()),
                guidance_scale=self._guidance.get(),
                sharpness=self._sharpness.get(),
                styles=tuple(
                    item.strip() for item in self._styles.get().split(",") if item.strip()
                ),
                output_format=self._format.get(),
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("Parámetros Fooocus", str(error))
            return
        operation_id = self._service.create_operation_id()
        request = ImageGenerationRequest(
            operation_id=operation_id,
            model=descriptor.id,
            prompt=prompt,
            negative_prompt=self._negative.get("1.0", tk.END).strip(),
            options=options,
        )
        self._queue.insert(
            "",
            tk.END,
            iid=operation_id,
            values=(operation_id[:8], descriptor.display_name, "queued"),
        )
        self._status.set("Trabajo añadido a la cola local.")
        self._submit(self._consume(request), self._ignore_result)

    async def _consume(self, request: ImageGenerationRequest) -> None:
        async for event in self._service.stream_generation(request):
            self._post(partial(self._handle_event, event))

    def _handle_event(self, event: ImageGenerationEvent) -> None:
        if self._queue.exists(event.operation_id):
            status = event.kind.value
            if event.progress is not None:
                status = event.progress.stage.value
                if event.progress.fraction is not None:
                    self._progress.stop()
                    self._progress.configure(mode="determinate")
                    self._progress.configure(value=event.progress.fraction * 100)
                elif event.kind is not ImageGenerationEventKind.QUEUED:
                    self._progress.configure(mode="indeterminate")
                    self._progress.start(12)
                self._progress_text.set(event.progress.detail or status)
            values = self._queue.item(event.operation_id, "values")
            self._queue.item(event.operation_id, values=(values[0], values[1], status))
        if event.kind is ImageGenerationEventKind.IMAGE and event.source_path is not None:
            self._submit(self._thumbnail(event.source_path), self._add_thumbnail)
        elif event.kind is ImageGenerationEventKind.COMPLETED and event.result is not None:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._status.set(f"Completado: {event.result.run_directory}")
            self._progress.configure(value=100)
        elif event.kind is ImageGenerationEventKind.CANCELLED:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._status.set("Generación cancelada; recursos restaurados.")
        elif event.kind is ImageGenerationEventKind.ERROR:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._status.set("La generación falló; consulta el mensaje y los metadatos.")
            messagebox.showerror("Fooocus", event.message or "Error desconocido")

    async def _thumbnail(self, path: Path) -> tuple[Path, Any]:
        return path, await asyncio.to_thread(self._load_thumbnail, path)

    @staticmethod
    def _load_thumbnail(path: Path) -> Any:
        image_module = import_module("PIL.Image")
        with image_module.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((220, 220))
            return image.copy()

    def _add_thumbnail(self, payload: tuple[Path, Any]) -> None:
        path, image = payload
        photo = import_module("PIL.ImageTk").PhotoImage(image)
        self._thumbnails.append(photo)
        index = len(self._thumbnails) - 1
        item = ttk.Frame(self._gallery, padding=4)
        item.grid(row=index // 3, column=index % 3, sticky="n")
        ttk.Label(item, image=photo).pack()
        ttk.Label(item, text=path.name).pack()

    def _cancel(self) -> None:
        selection = self._queue.selection()
        if not selection:
            messagebox.showinfo("Fooocus", "Selecciona una ejecución de la cola.")
            return
        operation_id = selection[0]
        self._status.set(f"Solicitando cancelación de {operation_id[:8]}…")
        self._submit(self._service.cancel(operation_id), self._ignore_result)

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

    def _show_error(self, error: BaseException) -> None:
        self._status.set("Operación Fooocus fallida")
        messagebox.showerror("Fooocus", str(error))

    @staticmethod
    def _ignore_result(_: object) -> None:
        return None

    def _post(self, callback: Callable[[], None]) -> None:
        self._callbacks.put(callback)

    def _drain_callbacks(self) -> None:
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)

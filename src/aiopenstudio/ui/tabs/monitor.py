"""Tkinter resource monitor fed only by the application monitoring service."""

from __future__ import annotations

import tkinter as tk
from collections import deque
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from functools import partial
from queue import Empty, SimpleQueue
from tkinter import messagebox, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    MemoryCategory,
    MemoryLocation,
    ModelId,
    TelemetrySnapshot,
)
from aiopenstudio.services import ResourceMonitorService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")
_COLORS = {
    MemoryCategory.WEIGHTS: "#4e79a7",
    MemoryCategory.KV_CACHE: "#f28e2b",
    MemoryCategory.ACTIVATIONS: "#e15759",
    MemoryCategory.FRAMEWORK_RESERVED: "#76b7b2",
    MemoryCategory.RUNTIME_OTHER: "#9c6ade",
    MemoryCategory.PROCESS: "#bab0ac",
    MemoryCategory.UNKNOWN: "#79706e",
}


class MonitorTab(ttk.Frame):
    """Live telemetry; all collection runs on the shared async worker loop."""

    def __init__(
        self,
        parent: tk.Misc,
        service: ResourceMonitorService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent, padding=12)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._watch_future: Future[None] | None = None
        self._model_rows: dict[str, tuple[ModelId, bool]] = {}
        self._history: deque[tuple[float, float, float | None]] = deque(maxlen=120)
        self._enabled = tk.BooleanVar(value=service.enabled)
        self._auto_release = tk.BooleanVar(value=service.auto_release_enabled)
        self._status = tk.StringVar(value="Esperando primera muestra…")
        self._cpu = tk.StringVar(value="CPU —")
        self._ram = tk.StringVar(value="RAM —")
        self._gpu = tk.StringVar(value="GPU —")
        self._vram = tk.StringVar(value="VRAM —")
        self._tokens = tk.StringVar(value="Último prompt —")
        self._build()
        self.after(50, self._drain_callbacks)
        self.bind("<Destroy>", self._on_destroy, add=True)
        self._watch_future = self._runner.submit(self._watch())

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(5, weight=1)
        ttk.Checkbutton(
            controls,
            text="Telemetría activa",
            variable=self._enabled,
            command=self._toggle_monitoring,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            controls,
            text="Liberación automática",
            variable=self._auto_release,
            command=self._toggle_auto_release,
        ).grid(row=0, column=1, padx=(16, 0), sticky="w")
        ttk.Button(controls, text="Muestra ahora", command=self._sample_now).grid(
            row=0, column=2, padx=(16, 0)
        )
        ttk.Button(controls, text="Liberar selección", command=self._release_selected).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(controls, text="Liberar inactivos", command=self._release_inactive).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Label(controls, textvariable=self._status).grid(
            row=0, column=5, padx=(16, 0), sticky="e"
        )

        cards = ttk.Frame(self)
        cards.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        for column, variable in enumerate(
            (self._cpu, self._ram, self._gpu, self._vram, self._tokens)
        ):
            cards.columnconfigure(column, weight=1)
            ttk.Label(
                cards, textvariable=variable, anchor="center", relief="groove", padding=9
            ).grid(row=0, column=column, padx=(0 if column == 0 else 4, 0), sticky="ew")

        charts = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        charts.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._usage_chart = tk.Canvas(
            charts, height=145, background="#151923", highlightthickness=0
        )
        self._memory_chart = tk.Canvas(
            charts, height=145, background="#151923", highlightthickness=0
        )
        charts.add(self._usage_chart, weight=1)
        charts.add(self._memory_chart, weight=1)
        self._usage_chart.bind("<Configure>", lambda _: self._draw_usage())

        details = ttk.Notebook(self)
        details.grid(row=3, column=0, sticky="nsew")
        models_frame, self._models = self._tree(
            details,
            ("runtime", "ram", "vram", "context", "state", "quality"),
            ("Runtime", "RAM", "VRAM", "Contexto", "Control", "Medición"),
        )
        details.add(models_frame, text="Modelos y residencia")
        processes_frame, self._processes = self._tree(
            details,
            ("pid", "runtime", "cpu", "ram", "vram", "owner"),
            ("PID", "Runtime", "CPU", "RAM", "VRAM", "Responsabilidad"),
        )
        details.add(processes_frame, text="Procesos")
        settings_frame, self._settings = self._tree(
            details,
            ("name", "value", "source", "restart"),
            ("Variable", "Valor", "Origen", "Reinicio"),
        )
        details.add(settings_frame, text="Ollama y políticas")

    @staticmethod
    def _tree(
        parent: tk.Misc,
        columns: tuple[str, ...],
        labels: tuple[str, ...],
    ) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="browse")
        tree.heading("#0", text="Elemento")
        tree.column("#0", width=220, stretch=True)
        for column, label in zip(columns, labels, strict=True):
            tree.heading(column, text=label)
            tree.column(column, width=110, stretch=True)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        return frame, tree

    async def _watch(self) -> None:
        async for snapshot in self._service.watch():
            self._post(partial(self._render, snapshot))

    def _render(self, snapshot: TelemetrySnapshot) -> None:
        if not snapshot.enabled:
            self._status.set("Telemetría detenida")
            return
        self._status.set(snapshot.captured_at.astimezone().strftime("Muestra %H:%M:%S"))
        if snapshot.system:
            ram_ratio = _ratio(snapshot.system.ram_used_bytes, snapshot.system.ram_total_bytes)
            self._cpu.set(f"CPU {snapshot.system.cpu_percent:.0f}%")
            self._ram.set(
                f"RAM {_bytes(snapshot.system.ram_used_bytes)} / "
                f"{_bytes(snapshot.system.ram_total_bytes)}"
            )
        else:
            ram_ratio = 0.0
        gpu = snapshot.gpus[0] if snapshot.gpus else None
        if gpu:
            self._gpu.set(
                f"GPU {gpu.utilization_percent or 0:.0f}% · {gpu.temperature_celsius or 0:.0f} °C"
            )
            self._vram.set(f"VRAM {_bytes(gpu.vram_used_bytes)} / {_bytes(gpu.vram_total_bytes)}")
            vram_ratio: float | None = _ratio(gpu.vram_used_bytes, gpu.vram_total_bytes)
        else:
            self._gpu.set("GPU no disponible")
            self._vram.set("VRAM —")
            vram_ratio = None
        cpu = snapshot.system.cpu_percent / 100 if snapshot.system else 0.0
        self._history.append((cpu, ram_ratio, vram_ratio))
        self._draw_usage()
        self._draw_memory(snapshot)
        self._render_models(snapshot)
        self._render_processes(snapshot)
        self._render_settings(snapshot)
        if snapshot.last_inference:
            metric = snapshot.last_inference[0]
            rate = metric.output_tokens_per_second
            rate_text = f" · {rate:.1f} tok/s" if rate is not None else ""
            self._tokens.set(
                f"Tokens {metric.input_tokens or 0} entrada / "
                f"{metric.output_tokens or 0} salida{rate_text}"
            )
        if snapshot.warnings:
            self._status.set(f"⚠ {snapshot.warnings[0]}")

    def _render_models(self, snapshot: TelemetrySnapshot) -> None:
        self._clear(self._models)
        self._model_rows.clear()
        for runtime in snapshot.runtimes:
            parent = self._models.insert(
                "",
                tk.END,
                text=runtime.name,
                values=(runtime.process_state.value, "", "", "", runtime.health.value, ""),
                open=True,
            )
            for model in runtime.models:
                quality = next(
                    (
                        item.quality.value
                        for item in snapshot.allocations
                        if item.model and item.model.key == model.model.key
                    ),
                    "unknown",
                )
                row = self._models.insert(
                    parent,
                    tk.END,
                    text=model.model.name,
                    values=(
                        runtime.name,
                        _bytes(model.ram_bytes),
                        _bytes(model.vram_bytes),
                        model.context_length or "—",
                        "administrado" if model.owned_by_app else "externo",
                        quality,
                    ),
                )
                self._model_rows[row] = (model.model, model.owned_by_app)
        for queued in snapshot.queued_models:
            row = self._models.insert(
                "",
                tk.END,
                text=f"{queued.model.name} (en cola)",
                values=(
                    queued.model.runtime,
                    _bytes(queued.estimated_weight_bytes),
                    "—",
                    "—",
                    queued.requested_device,
                    "estimated" if queued.estimated_weight_bytes else "unknown",
                ),
            )
            self._model_rows[row] = (queued.model, True)

    def _render_processes(self, snapshot: TelemetrySnapshot) -> None:
        self._clear(self._processes)
        for process in snapshot.processes:
            self._processes.insert(
                "",
                tk.END,
                text=process.name,
                values=(
                    process.pid,
                    process.runtime or "—",
                    f"{process.cpu_percent:.1f}%" if process.cpu_percent is not None else "—",
                    _bytes(process.ram_bytes),
                    _bytes(process.vram_bytes),
                    "AIOpenStudio" if process.owned_by_app else "externo",
                ),
            )

    def _render_settings(self, snapshot: TelemetrySnapshot) -> None:
        self._clear(self._settings)
        for name, value in self._service.policy_summary().items():
            self._settings.insert("", tk.END, text="Política", values=(name, value, "config", "no"))
        for runtime in snapshot.runtimes:
            for setting in runtime.settings:
                self._settings.insert(
                    "",
                    tk.END,
                    text=runtime.name,
                    values=(
                        setting.name,
                        setting.value,
                        setting.source,
                        "sí" if setting.restart_required else "no",
                    ),
                )

    def _draw_usage(self) -> None:
        canvas = self._usage_chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 20)
        height = max(canvas.winfo_height(), 20)
        canvas.create_text(10, 8, text="Historial CPU / RAM / VRAM", anchor="nw", fill="#e8edf7")
        left, top, right, bottom = 10, 30, width - 10, height - 14
        for level in (0.25, 0.5, 0.75, 1.0):
            y = bottom - (bottom - top) * level
            canvas.create_line(left, y, right, y, fill="#2a3140")
        if len(self._history) < 2:
            return
        for index, color in ((0, "#f2c14e"), (1, "#4e79a7"), (2, "#9c6ade")):
            points: list[float] = []
            for offset, sample in enumerate(self._history):
                value = sample[index]
                if value is None:
                    continue
                x = left + (right - left) * offset / max(len(self._history) - 1, 1)
                y = bottom - (bottom - top) * min(max(value, 0.0), 1.0)
                points.extend((x, y))
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2, smooth=True)

    def _draw_memory(self, snapshot: TelemetrySnapshot) -> None:
        canvas = self._memory_chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 20)
        canvas.create_text(10, 8, text="Mapa atribuible de memoria", anchor="nw", fill="#e8edf7")
        bars: list[tuple[str, MemoryLocation, int, int]] = []
        if snapshot.system:
            bars.append(
                (
                    "RAM",
                    MemoryLocation.RAM,
                    snapshot.system.ram_total_bytes,
                    snapshot.system.ram_used_bytes,
                )
            )
        if snapshot.gpus:
            bars.append(
                (
                    "VRAM",
                    MemoryLocation.VRAM,
                    snapshot.gpus[0].vram_total_bytes,
                    snapshot.gpus[0].vram_used_bytes,
                )
            )
        for row, (label, location, total, used) in enumerate(bars):
            y0 = 40 + row * 48
            canvas.create_text(10, y0 + 10, text=label, anchor="w", fill="#e8edf7")
            x0, x1 = 62, width - 12
            canvas.create_rectangle(x0, y0, x1, y0 + 22, fill="#252b38", outline="#3c465a")
            cursor = float(x0)
            allocations = [
                item
                for item in snapshot.allocations
                if item.location is location and item.category is not MemoryCategory.PROCESS
            ]
            for allocation in allocations:
                segment = (x1 - x0) * min(allocation.bytes / max(total, 1), 1.0)
                canvas.create_rectangle(
                    cursor,
                    y0,
                    min(cursor + segment, x1),
                    y0 + 22,
                    fill=_COLORS[allocation.category],
                    outline="",
                )
                cursor = min(cursor + segment, x1)
            attributed = sum(item.bytes for item in allocations)
            unattributed = max(min(used, total) - min(attributed, total), 0)
            unknown_segment = (x1 - x0) * unattributed / max(total, 1)
            if unknown_segment:
                canvas.create_rectangle(
                    cursor,
                    y0,
                    min(cursor + unknown_segment, x1),
                    y0 + 22,
                    fill="#3d4658",
                    outline="",
                )
            canvas.create_text(
                x1,
                y0 + 30,
                text=f"atribuido {_bytes(attributed)} · uso total {_bytes(used)}",
                anchor="e",
                fill="#aeb8ca",
            )

    def _toggle_monitoring(self) -> None:
        enabled = self._enabled.get()
        self._submit(self._service.set_enabled(enabled), self._ignore_result)
        self._status.set("Reanudando…" if enabled else "Telemetría detenida")

    def _toggle_auto_release(self) -> None:
        self._service.set_auto_release(self._auto_release.get())

    def _sample_now(self) -> None:
        if not self._enabled.get():
            return
        self._submit(self._service.snapshot(), self._render)

    def _release_selected(self) -> None:
        selected = self._models.selection()
        if not selected or selected[0] not in self._model_rows:
            messagebox.showinfo("Sin selección", "Selecciona un modelo residente.")
            return
        model, owned = self._model_rows[selected[0]]
        if not owned and not messagebox.askyesno(
            "Modelo externo",
            "Este modelo no fue cargado por AIOpenStudio. ¿Liberarlo igualmente?",
        ):
            return
        self._status.set(f"Liberando {model.name}…")
        self._submit(self._service.release_model(model), lambda _: self._sample_now())

    def _release_inactive(self) -> None:
        self._status.set("Liberando modelos administrados inactivos…")
        self._submit(self._service.release_inactive(), lambda _: self._sample_now())

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
        self._status.set("Operación fallida")
        messagebox.showerror("Monitor de recursos", str(error))

    def _post(self, callback: Callable[[], None]) -> None:
        self._callbacks.put(callback)

    def _drain_callbacks(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)

    def _on_destroy(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self and self._watch_future is not None:
            self._watch_future.cancel()

    @staticmethod
    def _clear(tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children())

    @staticmethod
    def _ignore_result(_: object) -> None:
        return None


def _bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _ratio(used: int, total: int) -> float:
    return used / total if total else 0.0

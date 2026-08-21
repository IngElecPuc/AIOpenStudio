"""Read-only diagnostics dialog and redacted bundle export."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import DiagnosticSnapshot
from aiopenstudio.services import DiagnosticsService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")


class DiagnosticsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        service: DiagnosticsService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent)
        self.title("Diagnósticos")
        self.geometry("760x480")
        self.minsize(620, 360)
        self.transient(parent)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._status = tk.StringVar(value="Recopilando diagnósticos…")
        self._build()
        self.after(50, self._drain_callbacks)
        self._refresh()

    def _build(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(body, textvariable=self._status).grid(row=0, column=0, sticky="w")
        self._tree = ttk.Treeview(
            body,
            columns=("status", "detail"),
            show="headings",
        )
        self._tree.heading("status", text="Estado")
        self._tree.heading("detail", text="Componente y detalle")
        self._tree.column("status", width=90, stretch=False)
        self._tree.column("detail", width=600)
        self._tree.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        actions = ttk.Frame(body)
        actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(actions, text="Actualizar", command=self._refresh).pack(side=tk.LEFT)
        ttk.Button(actions, text="Exportar ZIP redactado…", command=self._export).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _refresh(self) -> None:
        self._status.set("Recopilando diagnósticos…")
        self._submit(self._service.collect(), self._show_snapshot)

    def _show_snapshot(self, snapshot: DiagnosticSnapshot) -> None:
        self._tree.delete(*self._tree.get_children())
        for item in snapshot.items:
            self._tree.insert(
                "",
                tk.END,
                values=(item.status.value, f"{item.name}: {item.detail}"),
            )
        self._status.set(
            f"AIOpenStudio {snapshot.application_version} · sesión {snapshot.session_id[:8]} "
            f"· {len(snapshot.items)} comprobaciones"
        )

    def _export(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar diagnósticos redactados",
            defaultextension=".zip",
            filetypes=(("Archivo ZIP", "*.zip"),),
            initialfile="aiopenstudio-diagnostics.zip",
        )
        if not destination:
            return
        self._status.set("Generando paquete redactado…")
        self._submit(self._service.export(Path(destination)), self._exported)

    def _exported(self, destination: Path) -> None:
        self._status.set(f"Diagnósticos exportados: {destination}")
        messagebox.showinfo(
            "Diagnósticos",
            f"Paquete redactado creado en:\n{destination}",
            parent=self,
        )

    def _submit(self, coroutine: Coroutine[Any, Any, T], callback: Callable[[T], None]) -> None:
        future = self._runner.submit(coroutine)
        future.add_done_callback(
            lambda completed: self._callbacks.put(
                lambda: self._complete(completed, callback)
            )
        )

    def _complete(self, future: Future[T], callback: Callable[[T], None]) -> None:
        try:
            result = future.result()
        except Exception as error:
            self._status.set(str(error))
            messagebox.showerror("Diagnósticos", str(error), parent=self)
        else:
            callback(result)

    def _drain_callbacks(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)

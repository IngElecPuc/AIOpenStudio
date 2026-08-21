"""Tkinter dialog for selectable SQLite and PostgreSQL persistence modes."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from queue import Empty, SimpleQueue
from tkinter import messagebox, ttk
from typing import Any, TypeVar

from aiopenstudio.core.contracts import (
    PersistenceMode,
    PersistenceState,
    PostgresConnectionProfile,
    PostgresConnectionResult,
    PostgresSslMode,
)
from aiopenstudio.services import PersistenceService
from aiopenstudio.ui.async_runner import AsyncLoopRunner

T = TypeVar("T")

MODE_LABELS = {
    "Solo SQLite": PersistenceMode.SQLITE_ONLY,
    "SQLite + réplica PostgreSQL": PersistenceMode.SQLITE_REPLICATED,
    "PostgreSQL principal": PersistenceMode.POSTGRES_PRIMARY,
}


class PostgresSettingsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk | tk.Toplevel,
        service: PersistenceService,
        runner: AsyncLoopRunner,
    ) -> None:
        super().__init__(parent)
        self.title("Configuración de persistencia")
        self.resizable(False, False)
        self.transient(parent)
        self._service = service
        self._runner = runner
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._host = tk.StringVar(value="127.0.0.1")
        self._mode = tk.StringVar(value="SQLite + réplica PostgreSQL")
        self._port = tk.StringVar(value="5432")
        self._database = tk.StringVar(value="aiopenstudio")
        self._username = tk.StringVar(value="aiopenstudio")
        self._password = tk.StringVar()
        self._ssl_mode = tk.StringVar(value=PostgresSslMode.PREFER.value)
        self._timeout = tk.StringVar(value="5")
        self._auto_create = tk.BooleanVar(value=True)
        self._remember_password = tk.BooleanVar(value=False)
        self._synchronize_existing = tk.BooleanVar(value=False)
        self._status = tk.StringVar(value="Cargando configuración…")
        self._build()
        self.after(50, self._drain_callbacks)
        self._submit(self._service.state(), self._loaded)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Modo").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(
            body,
            textvariable=self._mode,
            values=tuple(MODE_LABELS),
            state="readonly",
            width=32,
        ).grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=3)
        fields = (
            ("Servidor", self._host, False),
            ("Puerto", self._port, False),
            ("Base de datos", self._database, False),
            ("Usuario", self._username, False),
            ("Contraseña", self._password, True),
        )
        for row, (label, variable, secret) in enumerate(fields, start=1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(body, textvariable=variable, show="*" if secret else "").grid(
                row=row, column=1, sticky="ew", padx=(10, 0), pady=3
            )

        ttk.Label(body, text="SSL/TLS").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Combobox(
            body,
            textvariable=self._ssl_mode,
            values=tuple(mode.value for mode in PostgresSslMode),
            state="readonly",
            width=16,
        ).grid(row=6, column=1, sticky="w", padx=(10, 0), pady=3)
        ttk.Label(body, text="Timeout (segundos)").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Spinbox(body, from_=1, to=60, textvariable=self._timeout, width=8).grid(
            row=7, column=1, sticky="w", padx=(10, 0), pady=3
        )
        ttk.Checkbutton(
            body,
            text="Autocrear o actualizar tablas mediante Alembic",
            variable=self._auto_create,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(
            body,
            text="Guardar contraseña en el almacén seguro del sistema",
            variable=self._remember_password,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            body,
            text="Sincronizar también el historial local existente",
            variable=self._synchronize_existing,
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Separator(body).grid(row=11, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(body, textvariable=self._status, wraplength=540).grid(
            row=12, column=0, columnspan=2, sticky="w"
        )
        actions = ttk.Frame(body)
        actions.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="Probar conexión", command=self._test).pack(side=tk.LEFT)
        ttk.Button(actions, text="Aplicar / conectar", command=self._connect).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Desconectar", command=self._disconnect).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Deshabilitar", command=self._disable).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Olvidar credencial", command=self._forget).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(actions, text="Cerrar", command=self.destroy).pack(side=tk.RIGHT)

    def _profile(self) -> PostgresConnectionProfile | None:
        try:
            mode = MODE_LABELS[self._mode.get()]
            return PostgresConnectionProfile(
                enabled=mode is not PersistenceMode.SQLITE_ONLY,
                mode=mode,
                host=self._host.get(),
                port=int(self._port.get()),
                database=self._database.get(),
                username=self._username.get(),
                ssl_mode=PostgresSslMode(self._ssl_mode.get()),
                connect_timeout_seconds=int(self._timeout.get()),
                auto_create_tables=self._auto_create.get(),
                remember_password=self._remember_password.get(),
                synchronize_existing=self._synchronize_existing.get(),
            )
        except (TypeError, ValueError) as error:
            messagebox.showerror("Configuración PostgreSQL", str(error), parent=self)
            return None

    def _test(self) -> None:
        profile = self._profile()
        if profile is None:
            return
        self._status.set("Verificando conexión y esquema…")
        self._submit(
            self._service.test_connection(profile, self._password.get()),
            self._show_result,
        )

    def _connect(self) -> None:
        profile = self._profile()
        if profile is None:
            return
        if profile.mode is PersistenceMode.SQLITE_ONLY:
            self._status.set("Activando el modo local…")
            self._submit(
                self._service.configure_sqlite_only(profile),
                lambda _: self._refresh(),
            )
            return
        self._status.set("Conectando y guardando el perfil local…")
        self._submit(
            self._service.connect(profile, self._password.get()),
            self._show_result,
        )

    def _disconnect(self) -> None:
        self._submit(self._service.disconnect(), lambda _: self._refresh())

    def _disable(self) -> None:
        self._submit(self._service.disconnect(disable=True), lambda _: self._disabled())

    def _disabled(self) -> None:
        self._submit(self._service.state(), self._disabled_state)

    def _disabled_state(self, state: PersistenceState) -> None:
        self._loaded(state)
        if state.fallback_active:
            messagebox.showwarning(
                "Fallback de persistencia",
                state.message,
                parent=self,
            )

    def _forget(self) -> None:
        self._submit(self._service.forget_credentials(), lambda _: self._refresh())

    def _refresh(self) -> None:
        self._submit(self._service.state(), self._loaded)

    def _loaded(self, state: PersistenceState) -> None:
        profile = state.profile
        self._mode.set(
            next(
                label
                for label, mode in MODE_LABELS.items()
                if mode is profile.mode
            )
        )
        self._host.set(profile.host)
        self._port.set(str(profile.port))
        self._database.set(profile.database)
        self._username.set(profile.username)
        self._ssl_mode.set(profile.ssl_mode.value)
        self._timeout.set(str(profile.connect_timeout_seconds))
        self._auto_create.set(profile.auto_create_tables)
        self._remember_password.set(profile.remember_password)
        self._synchronize_existing.set(profile.synchronize_existing)
        self._status.set(
            f"Estado: {state.status.value}. {state.message} "
            f"Pendientes: {state.pending_operations}."
            + (" Fallback SQLite activo." if state.fallback_active else "")
        )

    def _show_result(self, result: PostgresConnectionResult) -> None:
        details = result.message
        if result.success:
            details += (
                f" Base: {result.database}; usuario: {result.username}; "
                f"latencia: {result.latency_ms or 0:.1f} ms; "
                f"migración: {result.schema_revision or 'sin crear'}."
            )
            messagebox.showinfo("PostgreSQL", details, parent=self)
        else:
            messagebox.showerror("PostgreSQL", details, parent=self)
        self._status.set(details)

    def _submit(self, coroutine: Coroutine[Any, Any, T], on_success: Callable[[T], None]) -> None:
        future = self._runner.submit(coroutine)
        future.add_done_callback(
            lambda completed: self._callbacks.put(
                lambda: self._complete(completed, on_success)
            )
        )

    def _complete(self, future: Future[T], on_success: Callable[[T], None]) -> None:
        try:
            result = future.result()
        except Exception as error:
            self._status.set(str(error))
            messagebox.showerror("PostgreSQL", str(error), parent=self)
        else:
            on_success(result)

    def _drain_callbacks(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self.after(50, self._drain_callbacks)

"""Main Tkinter window and suite tabs."""

import tkinter as tk
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial
from queue import Empty, SimpleQueue
from tkinter import messagebox, ttk

from aiopenstudio.core.contracts import (
    PersistenceMode,
    PersistenceState,
    PostgresConnectionResult,
)
from aiopenstudio.services import (
    ImageGenerationService,
    LLMDictationService,
    LLMService,
    PersistenceService,
    ResourceMonitorService,
    TranscriptionService,
)
from aiopenstudio.ui.async_runner import AsyncLoopRunner
from aiopenstudio.ui.postgres_settings import PostgresSettingsDialog
from aiopenstudio.ui.tabs.fooocus import FooocusTab
from aiopenstudio.ui.tabs.llm import LLMTab
from aiopenstudio.ui.tabs.monitor import MonitorTab
from aiopenstudio.ui.tabs.whisper import WhisperTab


class ApplicationWindow:
    def __init__(
        self,
        root: tk.Tk,
        llm_service: LLMService,
        monitor_service: ResourceMonitorService,
        runner: AsyncLoopRunner,
        transcription_service: TranscriptionService,
        dictation_service: LLMDictationService,
        image_generation_service: ImageGenerationService,
        persistence_service: PersistenceService,
    ) -> None:
        self._root = root
        self._runner = runner
        self._persistence = persistence_service
        self._persistence_mode = tk.StringVar(value=PersistenceMode.SQLITE_REPLICATED.value)
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        root.title("AIOpenStudio")
        root.geometry("1050x720")
        root.minsize(800, 560)
        self._build_menu()

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True)
        notebook.add(
            LLMTab(notebook, llm_service, runner, dictation_service),
            text="LLM",
        )
        notebook.add(MonitorTab(notebook, monitor_service, runner), text="Monitor")
        notebook.add(WhisperTab(notebook, transcription_service, runner), text="Whisper")
        notebook.add(FooocusTab(notebook, image_generation_service, runner), text="Fooocus")
        root.after(50, self._drain_callbacks)
        root.after(100, self._refresh_persistence_mode)
        root.after(250, self._reconnect_persistence)

    def _build_menu(self) -> None:
        menu = tk.Menu(self._root)
        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(
            label="Conexión PostgreSQL…",
            command=self._open_postgres_settings,
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="Modo de persistencia", state=tk.DISABLED)
        for label, mode in (
            ("Solo SQLite", PersistenceMode.SQLITE_ONLY),
            ("SQLite + réplica PostgreSQL", PersistenceMode.SQLITE_REPLICATED),
            ("PostgreSQL principal", PersistenceMode.POSTGRES_PRIMARY),
        ):
            settings_menu.add_radiobutton(
                label=label,
                variable=self._persistence_mode,
                value=mode.value,
                command=partial(self._select_persistence_mode, mode),
            )
        menu.add_cascade(label="Configuración", menu=settings_menu)
        self._root.configure(menu=menu)

    def _open_postgres_settings(self) -> None:
        dialog = PostgresSettingsDialog(self._root, self._persistence, self._runner)
        dialog.bind(
            "<Destroy>",
            lambda event: (
                self._root.after(0, self._refresh_persistence_mode)
                if event.widget is dialog
                else None
            ),
            add="+",
        )

    def _select_persistence_mode(self, mode: PersistenceMode) -> None:
        future = self._runner.submit(self._persistence.set_mode(mode))
        future.add_done_callback(
            lambda completed: self._callbacks.put(
                lambda: self._persistence_mode_changed(completed)
            )
        )

    def _refresh_persistence_mode(self) -> None:
        future = self._runner.submit(self._persistence.state())
        future.add_done_callback(
            lambda completed: self._callbacks.put(
                lambda: self._persistence_mode_refreshed(completed)
            )
        )

    def _persistence_mode_changed(self, future: Future[PersistenceState]) -> None:
        try:
            state = future.result()
        except Exception as error:
            self._refresh_persistence_mode()
            messagebox.showerror("Persistencia", str(error), parent=self._root)
            return
        self._apply_persistence_mode_state(state)
        if state.fallback_active:
            messagebox.showwarning(
                "Fallback de persistencia",
                state.message,
                parent=self._root,
            )

    def _persistence_mode_refreshed(self, future: Future[PersistenceState]) -> None:
        try:
            state = future.result()
        except Exception:
            return
        self._apply_persistence_mode_state(state)

    def _apply_persistence_mode_state(self, state: PersistenceState) -> None:
        self._persistence_mode.set(state.profile.mode.value)

    def _reconnect_persistence(self) -> None:
        future = self._runner.submit(self._persistence.reconnect())
        future.add_done_callback(
            lambda completed: self._callbacks.put(
                lambda: self._reconnect_completed(completed)
            )
        )

    def _reconnect_completed(
        self, future: Future[PostgresConnectionResult | None]
    ) -> None:
        try:
            result = future.result()
        except Exception as error:
            messagebox.showwarning(
                "Persistencia PostgreSQL",
                f"No fue posible restaurar la conexión: {error}",
                parent=self._root,
            )
            return
        if result is not None and not result.success:
            message = result.message
            if "SQLite" not in message:
                message += " La aplicación continuará usando SQLite."
            messagebox.showwarning(
                "Persistencia PostgreSQL",
                message,
                parent=self._root,
            )
        self._refresh_persistence_mode()

    def _drain_callbacks(self) -> None:
        try:
            while True:
                self._callbacks.get_nowait()()
        except Empty:
            pass
        self._root.after(50, self._drain_callbacks)

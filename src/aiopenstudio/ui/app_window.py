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
    ApplicationLifecycleService,
    DiagnosticsService,
    ImageGenerationService,
    LLMDictationService,
    LLMService,
    PersistenceService,
    ResourceMonitorService,
    TranscriptionService,
)
from aiopenstudio.ui.async_runner import AsyncLoopRunner
from aiopenstudio.ui.diagnostics import DiagnosticsDialog
from aiopenstudio.ui.help import HelpDialog
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
        diagnostics_service: DiagnosticsService,
        lifecycle_service: ApplicationLifecycleService,
    ) -> None:
        self._root = root
        self._runner = runner
        self._persistence = persistence_service
        self._diagnostics = diagnostics_service
        self._lifecycle = lifecycle_service
        self._persistence_mode = tk.StringVar(value=PersistenceMode.SQLITE_REPLICATED.value)
        self._callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        root.title("AIOpenStudio")
        root.geometry("1280x800")
        root.minsize(1000, 650)
        self._build_menu()

        self._notebook = ttk.Notebook(root)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        self._notebook.add(
            LLMTab(self._notebook, llm_service, runner, dictation_service),
            text="LLM",
        )
        self._notebook.add(
            MonitorTab(self._notebook, monitor_service, runner), text="Monitor"
        )
        self._notebook.add(
            WhisperTab(self._notebook, transcription_service, runner), text="Whisper"
        )
        self._notebook.add(
            FooocusTab(self._notebook, image_generation_service, runner), text="Fooocus"
        )
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
        settings_menu.add_separator()
        settings_menu.add_command(label="Diagnósticos…", command=self._open_diagnostics)
        menu.add_cascade(label="Configuración", menu=settings_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="Guía de uso…",
            command=lambda: self._open_help("getting-started"),
        )
        help_menu.add_command(
            label="Solución de problemas…",
            command=lambda: self._open_help("troubleshooting"),
        )
        help_menu.add_separator()
        help_menu.add_command(label="Diagnósticos…", command=self._open_diagnostics)
        menu.add_cascade(label="Ayuda", menu=help_menu)
        self._menu = menu
        self._root.configure(menu=menu)

    def begin_shutdown(self) -> None:
        self._menu.entryconfigure("Configuración", state=tk.DISABLED)
        self._menu.entryconfigure("Ayuda", state=tk.DISABLED)
        self._disable_tree(self._notebook)

    def _disable_tree(self, widget: tk.Misc) -> None:
        try:
            widget.configure(state=tk.DISABLED)  # type: ignore[call-arg]
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._disable_tree(child)

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

    def _open_diagnostics(self) -> None:
        DiagnosticsDialog(self._root, self._diagnostics, self._runner)

    def _open_help(self, initial_topic: str) -> None:
        HelpDialog(
            self._root,
            initial_topic=initial_topic,
            open_diagnostics=self._open_diagnostics,
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
        future = self._runner.submit(self._lifecycle.restore_persistence())
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

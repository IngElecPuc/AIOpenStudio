"""Main Tkinter window and suite tabs."""

import tkinter as tk
from tkinter import ttk

from aiopenstudio.services import LLMService, ResourceMonitorService
from aiopenstudio.ui.async_runner import AsyncLoopRunner
from aiopenstudio.ui.tabs.llm import LLMTab
from aiopenstudio.ui.tabs.monitor import MonitorTab


class ApplicationWindow:
    def __init__(
        self,
        root: tk.Tk,
        llm_service: LLMService,
        monitor_service: ResourceMonitorService,
        runner: AsyncLoopRunner,
    ) -> None:
        root.title("AIOpenStudio")
        root.geometry("1050x720")
        root.minsize(800, 560)

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True)
        notebook.add(LLMTab(notebook, llm_service, runner), text="LLM")
        notebook.add(MonitorTab(notebook, monitor_service, runner), text="Monitor")
        notebook.add(
            _placeholder(notebook, "Suite Whisper", "Se implementará en la fase 5."),
            text="Whisper",
        )
        notebook.add(
            _placeholder(notebook, "Suite Fooocus", "Se implementará en la fase 6."),
            text="Fooocus",
        )


def _placeholder(parent: tk.Misc, title: str, detail: str) -> ttk.Frame:
    frame = ttk.Frame(parent, padding=24)
    ttk.Label(frame, text=title, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
    ttk.Label(frame, text=detail).pack(anchor="w", pady=(8, 0))
    return frame

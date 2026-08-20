"""Main Tkinter window and suite tabs."""

import tkinter as tk
from tkinter import ttk

from aiopenstudio.services import (
    ImageGenerationService,
    LLMDictationService,
    LLMService,
    ResourceMonitorService,
    TranscriptionService,
)
from aiopenstudio.ui.async_runner import AsyncLoopRunner
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
    ) -> None:
        root.title("AIOpenStudio")
        root.geometry("1050x720")
        root.minsize(800, 560)

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True)
        notebook.add(
            LLMTab(notebook, llm_service, runner, dictation_service),
            text="LLM",
        )
        notebook.add(MonitorTab(notebook, monitor_service, runner), text="Monitor")
        notebook.add(WhisperTab(notebook, transcription_service, runner), text="Whisper")
        notebook.add(FooocusTab(notebook, image_generation_service, runner), text="Fooocus")

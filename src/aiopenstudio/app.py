"""Explicit composition root for the desktop application."""

from __future__ import annotations

import logging
import tkinter as tk
from concurrent.futures import TimeoutError as FutureTimeoutError

from aiopenstudio.core.config import AppSettings
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime
from aiopenstudio.services import LLMService
from aiopenstudio.services.logging import LoggingConfigurator
from aiopenstudio.ui.app_window import ApplicationWindow
from aiopenstudio.ui.async_runner import AsyncLoopRunner


def main() -> None:
    """Start the UI; importing this module never starts services or loads models."""
    settings = AppSettings()
    logger = LoggingConfigurator().configure(
        level=settings.log_level,
        log_dir=settings.resolve_path(settings.log_dir),
    )
    store = SQLiteStore(
        settings.resolve_path(settings.sqlite_path),
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        enable_vectors=settings.sqlite_enable_vectors,
    )
    store.initialize()
    runtime = OllamaRuntime(str(settings.ollama_base_url))
    llm_service = LLMService(runtime=runtime, catalog=store, memory=store)
    runner = AsyncLoopRunner()
    runner.start()

    root = tk.Tk()
    ApplicationWindow(root, llm_service, runner)

    def close_application() -> None:
        try:
            runner.submit(runtime.close()).result(timeout=3)
        except FutureTimeoutError:
            logger.warning("Timeout while closing the Ollama HTTP client")
        except Exception:
            logger.exception("Failed to close the Ollama HTTP client cleanly")
        finally:
            runner.stop()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_application)
    logging.getLogger("aiopenstudio").info("AIOpenStudio desktop started")
    root.mainloop()


if __name__ == "__main__":
    main()

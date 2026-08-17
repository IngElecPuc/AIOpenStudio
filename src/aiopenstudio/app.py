"""Explicit composition root for the desktop application."""

from __future__ import annotations

import logging
import tkinter as tk
from concurrent.futures import TimeoutError as FutureTimeoutError

from aiopenstudio.core.config import AppSettings
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.monitoring import (
    InProcessTelemetryRegistry,
    NvidiaTelemetryProvider,
    OllamaTelemetryProvider,
    SystemTelemetryProvider,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime
from aiopenstudio.services import LLMService, ResourceMonitorService
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
    monitor_service = ResourceMonitorService(
        providers=(
            SystemTelemetryProvider(),
            NvidiaTelemetryProvider(),
            OllamaTelemetryProvider(str(settings.ollama_base_url)),
            InProcessTelemetryRegistry(),
        ),
        runtimes={runtime.name: runtime},
        enabled=settings.monitoring_enabled,
        interval_seconds=settings.monitoring_interval_seconds,
        history_samples=settings.monitoring_history_samples,
        auto_release_enabled=settings.monitoring_auto_release_enabled,
        idle_timeout_seconds=settings.monitoring_idle_timeout_seconds,
        max_managed_models=settings.monitoring_max_managed_models,
        ram_soft_limit=settings.monitoring_ram_soft_limit,
        ram_hard_limit=settings.monitoring_ram_hard_limit,
        vram_soft_limit=settings.monitoring_vram_soft_limit,
        vram_hard_limit=settings.monitoring_vram_hard_limit,
    )
    llm_service = LLMService(
        runtime=runtime,
        catalog=store,
        memory=store,
        metrics_sink=monitor_service,
        residency_policy=monitor_service,
    )
    runner = AsyncLoopRunner()
    runner.start()

    root = tk.Tk()
    ApplicationWindow(root, llm_service, monitor_service, runner)

    def close_application() -> None:
        try:
            runner.submit(monitor_service.close()).result(timeout=3)
        except FutureTimeoutError:
            logger.warning("Timeout while closing resource telemetry providers")
        except Exception:
            logger.exception("Failed to close resource telemetry providers cleanly")
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

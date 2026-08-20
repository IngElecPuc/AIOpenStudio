"""Explicit composition root for the desktop application."""

from __future__ import annotations

import logging
import tkinter as tk
from concurrent.futures import TimeoutError as FutureTimeoutError

from aiopenstudio.core.config import AppSettings
from aiopenstudio.infrastructure.audio import SoundDeviceAudioRecorder
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.monitoring import (
    FooocusTelemetryProvider,
    InProcessTelemetryRegistry,
    NvidiaTelemetryProvider,
    OllamaTelemetryProvider,
    SystemTelemetryProvider,
    WhisperTelemetryProvider,
)
from aiopenstudio.infrastructure.runtimes.fooocus import (
    FooocusProcessSettings,
    FooocusProcessSupervisor,
    FooocusRuntime,
    GradioFooocusTransport,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime
from aiopenstudio.services import (
    DeviceLeaseCoordinator,
    ImageGenerationService,
    ImageRunStore,
    LLMDictationService,
    LLMService,
    ResourceMonitorService,
    TranscriptionService,
)
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
    whisper_runtime = FasterWhisperRuntime(
        settings.resolve_model_library_path(settings.whisper_models_dir),
        cancel_grace_seconds=settings.whisper_cancel_grace_seconds,
    )
    fooocus_runtime_root = settings.resolve_path(settings.data_dir / "runtime/fooocus")
    fooocus_staging_root = fooocus_runtime_root / "staging"
    fooocus_process_settings = FooocusProcessSettings(
        home=settings.resolve_path(settings.fooocus_home),
        python_executable=settings.resolve_path(settings.fooocus_python),
        models_root=settings.resolve_model_library_path(settings.fooocus_models_dir),
        staging_root=fooocus_staging_root,
        runtime_root=fooocus_runtime_root,
        host=settings.fooocus_host,
        port=settings.fooocus_port,
        startup_timeout_seconds=settings.fooocus_startup_timeout_seconds,
    )
    fooocus_supervisor = FooocusProcessSupervisor(fooocus_process_settings)
    fooocus_runtime = FooocusRuntime(
        fooocus_supervisor,
        GradioFooocusTransport(
            fooocus_process_settings.base_url,
            download_root=fooocus_runtime_root / "gradio",
        ),
        cancel_grace_seconds=settings.fooocus_cancel_grace_seconds,
    )
    monitor_service = ResourceMonitorService(
        providers=(
            SystemTelemetryProvider(),
            NvidiaTelemetryProvider(),
            OllamaTelemetryProvider(str(settings.ollama_base_url)),
            InProcessTelemetryRegistry(),
            WhisperTelemetryProvider(whisper_runtime),
            FooocusTelemetryProvider(fooocus_runtime),
        ),
        runtimes={
            runtime.name: runtime,
            whisper_runtime.name: whisper_runtime,
            fooocus_runtime.name: fooocus_runtime,
        },
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
    audio_recorder = SoundDeviceAudioRecorder()
    transcription_service = TranscriptionService(
        runtime=whisper_runtime,
        catalog=store,
        residency_policy=monitor_service,
        resource_monitor=monitor_service,
        recorder=audio_recorder,
        recordings_dir=settings.resolve_path(settings.data_dir / "runtime/whisper/recordings"),
        max_input_bytes=settings.whisper_max_input_bytes,
    )
    dictation_service = LLMDictationService(
        transcription=transcription_service,
        llm=llm_service,
        monitor=monitor_service,
    )
    device_leases = DeviceLeaseCoordinator(
        monitor_service,
        llm=llm_service,
        transcription=transcription_service,
    )
    image_generation_service = ImageGenerationService(
        runtime=fooocus_runtime,
        catalog=store,
        run_store=ImageRunStore(
            settings.resolve_path(settings.output_dir / "fooocus"),
            allowed_source_roots=(
                fooocus_staging_root,
                fooocus_runtime_root / "gradio",
            ),
            max_image_bytes=settings.fooocus_max_image_bytes,
        ),
        device_leases=device_leases,
        residency_policy=monitor_service,
        resource_monitor=monitor_service,
    )
    runner = AsyncLoopRunner()
    runner.start()

    root = tk.Tk()
    ApplicationWindow(
        root,
        llm_service,
        monitor_service,
        runner,
        transcription_service,
        dictation_service,
        image_generation_service,
    )

    def close_application() -> None:
        try:
            runner.submit(image_generation_service.close()).result(timeout=10)
        except FutureTimeoutError:
            logger.warning("Timeout while closing the Fooocus suite")
        except Exception:
            logger.exception("Failed to close the Fooocus suite cleanly")
        try:
            runner.submit(transcription_service.cancel_recording()).result(timeout=2)
        except Exception:
            logger.exception("Failed to stop microphone capture cleanly")
        try:
            runner.submit(monitor_service.close()).result(timeout=3)
        except FutureTimeoutError:
            logger.warning("Timeout while closing resource telemetry providers")
        except Exception:
            logger.exception("Failed to close resource telemetry providers cleanly")
        try:
            runner.submit(whisper_runtime.close()).result(timeout=5)
        except FutureTimeoutError:
            logger.warning("Timeout while closing the Whisper worker")
        except Exception:
            logger.exception("Failed to close the Whisper worker cleanly")
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

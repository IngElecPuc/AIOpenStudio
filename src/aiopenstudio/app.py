"""Explicit composition root for the desktop application."""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from uuid import uuid4

from aiopenstudio import __version__
from aiopenstudio.core.config import AppSettings
from aiopenstudio.infrastructure.audio import SoundDeviceAudioRecorder
from aiopenstudio.infrastructure.database import (
    KeyringCredentialStore,
    PostgresMigrationManager,
    PostgresProfileStore,
    PostgresRepository,
    SQLiteStore,
)
from aiopenstudio.infrastructure.diagnostics import SystemDiagnosticProbe
from aiopenstudio.infrastructure.monitoring import (
    FooocusTelemetryProvider,
    InProcessTelemetryRegistry,
    NvidiaTelemetryProvider,
    OllamaTelemetryProvider,
    SystemTelemetryProvider,
    WhisperTelemetryProvider,
)
from aiopenstudio.infrastructure.paths import ApplicationPaths
from aiopenstudio.infrastructure.runtimes.fooocus import (
    FooocusProcessSettings,
    FooocusProcessSupervisor,
    FooocusRuntime,
    GradioFooocusTransport,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime
from aiopenstudio.services import (
    ApplicationLifecycleService,
    DeviceLeaseCoordinator,
    DiagnosticsService,
    ImageGenerationService,
    ImageRunStore,
    LLMDictationService,
    LLMService,
    PersistenceService,
    ResourceMonitorService,
    ShutdownStep,
    TranscriptionService,
)
from aiopenstudio.services.logging import LoggingConfigurator
from aiopenstudio.ui.app_window import ApplicationWindow
from aiopenstudio.ui.async_runner import AsyncLoopRunner


def main() -> None:
    """Start the UI; importing this module never starts services or loads models."""
    filesystem = ApplicationPaths.discover()
    settings = AppSettings(
        _env_file=filesystem.env_file if filesystem.env_file.is_file() else None
    )
    resolve_path = filesystem.resolve_runtime
    model_library_root = resolve_path(settings.model_library_root)

    def resolve_model_path(path: Path) -> Path:
        return path.resolve() if path.is_absolute() else (model_library_root / path).resolve()

    session_id = str(uuid4())
    log_dir = resolve_path(settings.log_dir)
    logger = LoggingConfigurator().configure(
        level=settings.log_level,
        log_dir=log_dir,
        session_id=session_id,
    )
    store = SQLiteStore(
        resolve_path(settings.sqlite_path),
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        enable_vectors=settings.sqlite_enable_vectors,
    )
    store.initialize()
    postgres_migrations = PostgresMigrationManager(
        Path(__file__).resolve().parent / "infrastructure/database/migrations"
    )
    persistence_service = PersistenceService(
        store,
        PostgresProfileStore(
            resolve_path(settings.data_dir / "runtime/database/postgres-profile.json")
        ),
        KeyringCredentialStore(),
        lambda profile, password: PostgresRepository(
            profile,
            password,
            migrations=postgres_migrations,
        ),
        environment_password=settings.database_password,
    )
    runtime = OllamaRuntime(str(settings.ollama_base_url))
    whisper_runtime = FasterWhisperRuntime(
        resolve_model_path(settings.whisper_models_dir),
        cancel_grace_seconds=settings.whisper_cancel_grace_seconds,
        restart_limit=settings.whisper_restart_limit,
        restart_window_seconds=settings.whisper_restart_window_seconds,
    )
    fooocus_runtime_root = resolve_path(settings.data_dir / "runtime/fooocus")
    fooocus_staging_root = fooocus_runtime_root / "staging"
    fooocus_process_settings = FooocusProcessSettings(
        home=resolve_path(settings.fooocus_home),
        python_executable=resolve_path(settings.fooocus_python),
        models_root=resolve_model_path(settings.fooocus_models_dir),
        staging_root=fooocus_staging_root,
        runtime_root=fooocus_runtime_root,
        host=settings.fooocus_host,
        port=settings.fooocus_port,
        startup_timeout_seconds=settings.fooocus_startup_timeout_seconds,
        restart_limit=settings.fooocus_restart_limit,
        restart_window_seconds=settings.fooocus_restart_window_seconds,
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
        execution_history=persistence_service,
    )
    audio_recorder = SoundDeviceAudioRecorder()
    transcription_service = TranscriptionService(
        runtime=whisper_runtime,
        catalog=store,
        residency_policy=monitor_service,
        resource_monitor=monitor_service,
        recorder=audio_recorder,
        recordings_dir=resolve_path(settings.data_dir / "runtime/whisper/recordings"),
        max_input_bytes=settings.whisper_max_input_bytes,
        execution_history=persistence_service,
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
            resolve_path(settings.output_dir / "fooocus"),
            allowed_source_roots=(
                fooocus_staging_root,
                fooocus_runtime_root / "gradio",
            ),
            max_image_bytes=settings.fooocus_max_image_bytes,
        ),
        device_leases=device_leases,
        residency_policy=monitor_service,
        resource_monitor=monitor_service,
        execution_history=persistence_service,
    )
    runner = AsyncLoopRunner()
    runner.start()

    diagnostics_service = DiagnosticsService(
        application_version=__version__,
        session_id=session_id,
        environment=settings.environment,
        probe=SystemDiagnosticProbe(
            {
                "data": resolve_path(settings.data_dir),
                "models": model_library_root,
                "outputs": resolve_path(settings.output_dir),
                "logs": log_dir,
            }
        ),
        runtimes={
            runtime.name: runtime,
            whisper_runtime.name: whisper_runtime,
            fooocus_runtime.name: fooocus_runtime,
        },
        persistence=persistence_service,
        log_dir=log_dir,
    )
    lifecycle_service = ApplicationLifecycleService(
        persistence_service,
        (
            ShutdownStep("fooocus", image_generation_service.close, 10),
            ShutdownStep("microphone", transcription_service.cancel_recording, 2),
            ShutdownStep("monitor", monitor_service.close, 3),
            ShutdownStep("whisper", whisper_runtime.close, 5),
            ShutdownStep("ollama_client", runtime.close, 3),
            ShutdownStep("persistence", persistence_service.close, 3),
        ),
    )

    root = tk.Tk()
    application_window = ApplicationWindow(
        root,
        llm_service,
        monitor_service,
        runner,
        transcription_service,
        dictation_service,
        image_generation_service,
        persistence_service,
        diagnostics_service,
        lifecycle_service,
    )

    closing = False

    def close_application() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        application_window.begin_shutdown()
        root.title("AIOpenStudio · cerrando…")
        future = runner.submit(lifecycle_service.shutdown())

        def finish_when_ready() -> None:
            if not future.done():
                root.after(50, finish_when_ready)
                return
            try:
                result = future.result()
                if result.failed:
                    logger.warning(
                        "desktop.shutdown_incomplete",
                        extra={"component": "desktop", "failed_steps": result.failed},
                    )
            except Exception:
                logger.exception("desktop.shutdown_failed")
            runner.stop()
            root.destroy()

        root.after(50, finish_when_ready)

    root.protocol("WM_DELETE_WINDOW", close_application)
    logging.getLogger("aiopenstudio").info(
        "desktop.started",
        extra={
            "component": "desktop",
            "application_version": __version__,
            "environment": settings.environment,
        },
    )
    root.mainloop()


if __name__ == "__main__":
    main()

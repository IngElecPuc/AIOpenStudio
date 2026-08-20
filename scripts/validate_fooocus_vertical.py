"""Delegated Fooocus checks. This script never installs or downloads assets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aiopenstudio.core.config import AppSettings
from aiopenstudio.core.contracts import (
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationOptions,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImagePerformance,
)
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.infrastructure.monitoring import (
    FooocusTelemetryProvider,
    NvidiaTelemetryProvider,
    SystemTelemetryProvider,
)
from aiopenstudio.infrastructure.runtimes.fooocus import (
    FooocusProcessSettings,
    FooocusProcessSupervisor,
    FooocusRuntime,
    GradioFooocusTransport,
)
from aiopenstudio.services import (
    DeviceLeaseCoordinator,
    ImageGenerationService,
    ImageRunStore,
    ResourceMonitorService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="scenario", required=True)
    subparsers.add_parser("preflight", help="Comprueba rutas y dependencias sin iniciar Fooocus.")
    for name in ("smoke", "cancel"):
        command = subparsers.add_parser(name)
        command.add_argument("--model", required=True, help="Nombre o nombre visible local.")
        command.add_argument("--prompt", default="a small red cabin in a snowy forest")
        command.add_argument("--negative-prompt", default="watermark, text")
        command.add_argument(
            "--performance",
            choices=[item.value for item in ImagePerformance],
            default="speed",
        )
        command.add_argument("--width", type=int, default=1024)
        command.add_argument("--height", type=int, default=1024)
        command.add_argument("--count", type=int, default=1)
        command.add_argument("--seed", type=int)
        command.add_argument("--timeout", type=float, default=900)
        if name == "cancel":
            command.add_argument("--cancel-after", type=float, default=5)
    return parser


def _runtime(settings: AppSettings) -> tuple[FooocusRuntime, Path, Path]:
    runtime_root = settings.resolve_path(settings.data_dir / "runtime/fooocus")
    staging_root = runtime_root / "staging"
    process_settings = FooocusProcessSettings(
        home=settings.resolve_path(settings.fooocus_home),
        python_executable=settings.resolve_path(settings.fooocus_python),
        models_root=settings.resolve_model_library_path(settings.fooocus_models_dir),
        staging_root=staging_root,
        runtime_root=runtime_root,
        host=settings.fooocus_host,
        port=settings.fooocus_port,
        startup_timeout_seconds=settings.fooocus_startup_timeout_seconds,
    )
    runtime = FooocusRuntime(
        FooocusProcessSupervisor(process_settings),
        GradioFooocusTransport(
            process_settings.base_url,
            download_root=runtime_root / "gradio",
        ),
        cancel_grace_seconds=settings.fooocus_cancel_grace_seconds,
    )
    return runtime, runtime_root, staging_root


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _event_payload(event: ImageGenerationEvent) -> dict[str, object]:
    return {
        "kind": event.kind.value,
        "stage": event.progress.stage.value if event.progress else None,
        "message": event.message,
    }


def _print_event(event: ImageGenerationEvent, started_at: datetime) -> None:
    elapsed = (datetime.now(UTC) - started_at).total_seconds()
    stage = event.progress.stage.value if event.progress else event.kind.value
    detail = event.message or (event.progress.detail if event.progress else None)
    suffix = f" — {detail}" if detail else ""
    print(f"[{elapsed:7.1f} s] {stage}{suffix}", flush=True)


async def _stream_with_progress(
    service: ImageGenerationService,
    runtime: FooocusRuntime,
    request: ImageGenerationRequest,
    events: list[dict[str, object]],
    started_at: datetime,
) -> tuple[str, ImageGenerationResult | None]:
    stream = service.stream_generation(request).__aiter__()
    next_event: asyncio.Future[ImageGenerationEvent] | None = asyncio.ensure_future(
        anext(stream)
    )
    last_stage = "preparando"
    status = "failed"
    result: ImageGenerationResult | None = None
    try:
        while next_event is not None:
            done, _ = await asyncio.wait({next_event}, timeout=10.0)
            if not done:
                elapsed = (datetime.now(UTC) - started_at).total_seconds()
                process = runtime.process_id
                process_detail = f"PID {process}" if process is not None else "sin proceso"
                print(
                    f"[{elapsed:7.1f} s] en curso: {last_stage} ({process_detail})",
                    flush=True,
                )
                continue
            try:
                event = next_event.result()
            except StopAsyncIteration:
                next_event = None
                break
            events.append(_event_payload(event))
            _print_event(event, started_at)
            last_stage = event.progress.stage.value if event.progress else event.kind.value
            if event.kind in {
                ImageGenerationEventKind.COMPLETED,
                ImageGenerationEventKind.CANCELLED,
            }:
                status = event.kind.value
                result = event.result
            next_event = asyncio.ensure_future(anext(stream))
    finally:
        if next_event is not None and not next_event.done():
            next_event.cancel()
            await asyncio.gather(next_event, return_exceptions=True)
    return status, result


async def _preflight(settings: AppSettings) -> int:
    runtime, _, _ = _runtime(settings)
    models = await runtime.list_models()
    payload = {
        "health": (await runtime.health()).value,
        "issues": runtime.preflight(),
        "gradio_client": _package_version("gradio-client"),
        "models": [
            {"name": item.id.name, "path": str(item.weights_path), "bytes": item.size_bytes}
            for item in models
        ],
        "downloads_performed": False,
    }
    print("No se inicia Fooocus ni se genera reporte.")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not payload["issues"] and payload["gradio_client"] else 1


async def _run(settings: AppSettings, arguments: argparse.Namespace) -> int:
    runtime, runtime_root, staging_root = _runtime(settings)
    report_root = settings.resolve_path(settings.output_dir / "fooocus-validation")
    report_root.mkdir(parents=True, exist_ok=True)
    operation_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_root / f"fooocus-{arguments.scenario}-{timestamp}-{operation_id}.json"
    preflight_issues = runtime.preflight()
    if preflight_issues:
        preflight_payload = {
            "schema_version": 1,
            "scenario": arguments.scenario,
            "operation_id": operation_id,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "preflight_failed",
            "preflight_issues": preflight_issues,
            "downloads_performed": False,
            "events": [],
            "finished_at": datetime.now(UTC).isoformat(),
        }
        report_path.write_text(
            json.dumps(preflight_payload, ensure_ascii=False, indent=2), "utf-8"
        )
        print("Smoke bloqueado: el preflight no está listo; no se creó una ejecución Fooocus.")
        print(f"Reporte único: {report_path}")
        print("Resultado: preflight_failed")
        return 2
    store = SQLiteStore(
        settings.resolve_path(settings.sqlite_path),
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
        enable_vectors=settings.sqlite_enable_vectors,
    )
    store.initialize()
    monitor = ResourceMonitorService(
        providers=(
            SystemTelemetryProvider(),
            NvidiaTelemetryProvider(),
            FooocusTelemetryProvider(runtime),
        ),
        runtimes={runtime.name: runtime},
        max_managed_models=settings.monitoring_max_managed_models,
        ram_soft_limit=settings.monitoring_ram_soft_limit,
        ram_hard_limit=settings.monitoring_ram_hard_limit,
        vram_soft_limit=settings.monitoring_vram_soft_limit,
        vram_hard_limit=settings.monitoring_vram_hard_limit,
    )
    service = ImageGenerationService(
        runtime,
        store,
        ImageRunStore(
            report_root / "runs",
            allowed_source_roots=(staging_root, runtime_root / "gradio"),
            max_image_bytes=settings.fooocus_max_image_bytes,
        ),
        DeviceLeaseCoordinator(monitor),
        residency_policy=monitor,
        resource_monitor=monitor,
    )
    started_at = datetime.now(UTC)
    payload: dict[str, object] = {
        "schema_version": 1,
        "scenario": arguments.scenario,
        "operation_id": operation_id,
        "started_at": started_at.isoformat(),
        "prompt_sha256": hashlib.sha256(arguments.prompt.encode()).hexdigest(),
        "downloads_performed": False,
        "preflight_issues": preflight_issues,
        "events": [],
    }
    status = "failed"
    cancel_task: asyncio.Task[None] | None = None
    events: list[dict[str, object]] = []
    payload["events"] = events
    try:
        models = await service.refresh_models()
        descriptor = next(
            (
                item
                for item in models
                if arguments.model in {item.id.name, item.display_name}
            ),
            None,
        )
        if descriptor is None:
            raise ValueError(f"No existe el checkpoint local {arguments.model!r}.")
        request = ImageGenerationRequest(
            operation_id=operation_id,
            model=descriptor.id,
            prompt=arguments.prompt,
            negative_prompt=arguments.negative_prompt,
            options=ImageGenerationOptions(
                width=arguments.width,
                height=arguments.height,
                image_count=arguments.count,
                seed=arguments.seed,
                performance=ImagePerformance(arguments.performance),
            ),
        )
        if arguments.scenario == "cancel":
            async def cancel_later() -> None:
                await asyncio.sleep(arguments.cancel_after)
                await service.cancel(operation_id)

            cancel_task = asyncio.create_task(cancel_later())
        print(f"Ejecución: {operation_id}", flush=True)
        print(f"Reporte reservado: {report_path}", flush=True)
        async with asyncio.timeout(arguments.timeout):
            status, result = await _stream_with_progress(
                service, runtime, request, events, started_at
            )
            if result is not None:
                payload["result"] = result.model_dump(mode="json")
    except TimeoutError:
        last_stage = events[-1]["stage"] if events else "sin eventos"
        payload["error"] = (
            f"TimeoutError: se alcanzó el límite de {arguments.timeout:.0f} s; "
            f"última etapa: {last_stage}."
        )
        print(str(payload["error"]), flush=True)
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {error}"
        print(str(payload["error"]), flush=True)
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        await service.close()
        await monitor.close()
    payload["status"] = status
    payload["finished_at"] = datetime.now(UTC).isoformat()
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"Reporte único: {report_path}")
    print(f"Resultado: {status}")
    expected = "cancelled" if arguments.scenario == "cancel" else "completed"
    return 0 if status == expected else 1


async def _main(arguments: argparse.Namespace) -> int:
    settings = AppSettings()
    if arguments.scenario == "preflight":
        return await _preflight(settings)
    print("ETA de arranque: 30–300 s; generación: 1–15 min según checkpoint y parámetros.")
    print("No habrá instalaciones ni descargas implícitas; se generará exactamente un JSON.")
    return await _run(settings, arguments)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(_parser().parse_args())))

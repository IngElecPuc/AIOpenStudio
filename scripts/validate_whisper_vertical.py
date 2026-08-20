"""Validate the local Whisper vertical without downloading models or inputs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiopenstudio.core.config import AppSettings
from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    TranscriptionEventKind,
    TranscriptionRequest,
    TranscriptionResult,
)
from aiopenstudio.infrastructure.runtimes.whisper import FasterWhisperRuntime

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("preflight", "cpu", "gpu", "cancel"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="es")
    parser.add_argument("--cancel-after", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data/outputs/whisper-validation",
    )
    return parser.parse_args()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_eta(scenario: str) -> None:
    if scenario == "preflight":
        print("ETA local/global estimada: 1–10 s; no se genera reporte.")
    else:
        print("ETA de carga: 15–180 s; transcripción: 0,2–3× la duración del audio.")
        print("ETA global máxima configurada: 15 min; se generará exactamente un JSON.")


async def preflight(runtime: FasterWhisperRuntime) -> int:
    models = tuple(await runtime.list_models())
    packages = {
        name: package_version(name)
        for name in ("faster-whisper", "ctranslate2", "av", "sounddevice")
    }
    print(json.dumps({"health": (await runtime.health()).value, "packages": packages}, indent=2))
    for model in models:
        print(f"{model.id.variant}: {model.weights_path} ({model.size_bytes} bytes)")
    return 0 if models and packages["faster-whisper"] else 1


async def run_scenario(runtime: FasterWhisperRuntime, args: argparse.Namespace) -> int:
    if args.source is None or not args.source.is_file():
        raise ValueError("Los escenarios reales requieren --source con un audio local existente.")
    models = tuple(await runtime.list_models())
    descriptor = next((item for item in models if item.id.variant == args.model), None)
    if descriptor is None:
        raise ValueError(f"El modelo local {args.model!r} no está disponible.")
    device = ComputeDevice.CPU if args.scenario == "cpu" else ComputeDevice.GPU
    run_id = str(uuid4())
    request = TranscriptionRequest(
        operation_id=run_id,
        model=descriptor.id,
        source_path=args.source,
        options={"language": args.language},
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario": args.scenario,
        "status": "failed",
        "started_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: package_version(name) for name in ("faster-whisper", "ctranslate2", "av")
            },
        },
        "model": descriptor.model_dump(mode="json", exclude={"weights_path"}),
        "input": {
            "bytes": args.source.stat().st_size,
            "sha256": file_sha256(args.source),
        },
    }
    started = time.perf_counter()
    segment_count = 0
    character_count = 0
    terminal: str | None = None
    result: TranscriptionResult | None = None
    try:
        await runtime.load(descriptor.id, LoadPolicy(device=device))

        async def consume() -> None:
            nonlocal segment_count, character_count, terminal, result
            async for event in runtime.transcribe(request):
                if event.kind is TranscriptionEventKind.SEGMENT and event.segment is not None:
                    segment_count += 1
                    character_count += len(event.segment.text)
                if event.kind in {
                    TranscriptionEventKind.COMPLETED,
                    TranscriptionEventKind.CANCELLED,
                    TranscriptionEventKind.ERROR,
                }:
                    terminal = event.kind.value
                    result = event.result

        task = asyncio.create_task(consume())
        if args.scenario == "cancel":
            await asyncio.sleep(args.cancel_after)
            await runtime.cancel(run_id)
        await task
        expected = "cancelled" if args.scenario == "cancel" else "completed"
        report["status"] = "passed" if terminal == expected else "failed"
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        try:
            await runtime.unload(descriptor.id)
        except Exception as error:
            report["unload_error"] = {"type": type(error).__name__, "message": str(error)}
            report["status"] = "failed"
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = time.perf_counter() - started
        report["output"] = {
            "terminal_event": terminal,
            "segments": segment_count,
            "characters": character_count,
            "audio_duration_seconds": result.duration_seconds if result else None,
            "backend_elapsed_seconds": result.elapsed_seconds if result else None,
            "realtime_factor": (
                result.elapsed_seconds / result.duration_seconds
                if result and result.duration_seconds
                else None
            ),
            "detected_language": result.language if result else None,
            "language_probability": result.language_probability if result else None,
            "content_included": False,
        }
        args.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = args.report_dir / f"whisper-{args.scenario}-{stamp}-{run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte único: {path.resolve()}")
        print(f"Resultado: {report['status']}")
        await runtime.close()
    return 0 if report["status"] == "passed" else 1


async def async_main(args: argparse.Namespace) -> int:
    settings = AppSettings()
    runtime = FasterWhisperRuntime(
        settings.resolve_model_library_path(settings.whisper_models_dir),
        cancel_grace_seconds=settings.whisper_cancel_grace_seconds,
    )
    print_eta(args.scenario)
    if args.scenario == "preflight":
        return await preflight(runtime)
    async with asyncio.timeout(args.timeout):
        return await run_scenario(runtime, args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))

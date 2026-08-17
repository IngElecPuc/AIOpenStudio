"""Validate telemetry without downloading, loading or generating with a model."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aiopenstudio.core.config import AppSettings  # noqa: E402
from aiopenstudio.infrastructure.monitoring import (  # noqa: E402
    InProcessTelemetryRegistry,
    NvidiaTelemetryProvider,
    OllamaTelemetryProvider,
    SystemTelemetryProvider,
)
from aiopenstudio.services import ResourceMonitorService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("preflight", "observe"))
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "outputs" / "resource-monitor-validation",
    )
    return parser.parse_args()


def print_eta(scenario: str, duration: float, interval: float) -> None:
    if scenario == "preflight":
        print("ETA local/global estimada: 1–10 s")
        print("Reporte: no corresponde; la salida breve queda en consola.")
        return
    samples = max(int(duration / interval), 1)
    print(f"ETA local estimada por muestra: <1 s + intervalo de {interval:g} s")
    print(f"ETA global estimada: {duration:g}–{duration + 10:g} s ({samples} muestras)")
    print("Reporte: se generará exactamente un JSON al terminar.")


def build_service(settings: AppSettings) -> ResourceMonitorService:
    return ResourceMonitorService(
        providers=(
            SystemTelemetryProvider(),
            NvidiaTelemetryProvider(),
            OllamaTelemetryProvider(str(settings.ollama_base_url)),
            InProcessTelemetryRegistry(),
        ),
        runtimes={},
        interval_seconds=settings.monitoring_interval_seconds,
        history_samples=settings.monitoring_history_samples,
        ram_soft_limit=settings.monitoring_ram_soft_limit,
        ram_hard_limit=settings.monitoring_ram_hard_limit,
        vram_soft_limit=settings.monitoring_vram_soft_limit,
        vram_hard_limit=settings.monitoring_vram_hard_limit,
    )


def snapshot_summary(snapshot: Any) -> str:
    cpu = f"{snapshot.system.cpu_percent:.0f}%" if snapshot.system else "—"
    ram = (
        f"{snapshot.system.ram_used_bytes / 1024**3:.2f}/"
        f"{snapshot.system.ram_total_bytes / 1024**3:.2f} GiB"
        if snapshot.system
        else "—"
    )
    vram = (
        f"{snapshot.gpus[0].vram_used_bytes / 1024**3:.2f}/"
        f"{snapshot.gpus[0].vram_total_bytes / 1024**3:.2f} GiB"
        if snapshot.gpus
        else "no disponible"
    )
    models = sum(len(runtime.models) for runtime in snapshot.runtimes)
    return f"CPU {cpu}; RAM {ram}; VRAM {vram}; modelos residentes {models}"


async def async_main(args: argparse.Namespace) -> int:
    if args.duration <= 0 or args.interval < 0.5:
        print("--duration debe ser positivo y --interval al menos 0.5 s.", file=sys.stderr)
        return 2
    settings = AppSettings()
    service = build_service(settings)
    print_eta(args.scenario, args.duration, args.interval)
    try:
        if args.scenario == "preflight":
            snapshot = await service.snapshot()
            print(snapshot_summary(snapshot))
            for provider, status in snapshot.provider_status.items():
                print(f"  - {provider}: {status.value}")
            for warning in snapshot.warnings:
                print(f"  - advertencia: {warning}")
            return 0 if snapshot.system is not None else 1

        run_id = str(uuid4())
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        samples: list[dict[str, Any]] = []
        sample_count = max(int(args.duration / args.interval), 1)
        status = "passed"
        error: dict[str, str] | None = None
        try:
            for index in range(sample_count):
                sample = await service.snapshot()
                samples.append(sample.model_dump(mode="json"))
                print(f"[{index + 1}/{sample_count}] {snapshot_summary(sample)}")
                if index + 1 < sample_count:
                    await asyncio.sleep(args.interval)
        except Exception as caught:
            status = "failed"
            error = {"type": type(caught).__name__, "message": str(caught)}
        report = {
            "schema_version": 1,
            "run_id": run_id,
            "scenario": args.scenario,
            "status": status,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": time.perf_counter() - started,
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "input": {"duration_seconds": args.duration, "interval_seconds": args.interval},
            "samples": samples,
            "error": error,
        }
        args.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        path = args.report_dir / f"resource-monitor-{stamp}-{run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Reporte único: {path.resolve()}")
        print(f"Resultado: {status}")
        return 0 if status == "passed" and samples else 1
    finally:
        await service.close()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

"""Run explicit Ollama validations without downloading models.

``preflight`` is short and writes only to the console. ``smoke`` and ``cancel``
may load a model and each invocation writes exactly one JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aiopenstudio.core.contracts import (  # noqa: E402
    ChatInput,
    ChatMessage,
    ChatOptions,
    InferenceRequest,
    LoadPolicy,
    MessageRole,
    ModelDescriptor,
    RuntimeEventKind,
    RuntimeHealth,
)
from aiopenstudio.infrastructure.runtimes.ollama import OllamaRuntime  # noqa: E402

DEFAULT_PROMPT = "Responde en una sola frase: ¿qué comprueba esta validación local?"
DEFAULT_CANCEL_PROMPT = (
    "Enumera y explica brevemente doscientas prácticas para mantener una aplicación Python local."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("preflight", "smoke", "cancel"))
    parser.add_argument("--model", help="Nombre exacto mostrado por `ollama list`")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--prompt")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--keep-alive", type=float, default=600.0)
    parser.add_argument("--cancel-after", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "outputs" / "ollama-validation",
    )
    return parser.parse_args()


def estimate(descriptor: ModelDescriptor | None, scenario: str) -> dict[str, Any]:
    large = descriptor is None or (descriptor.size_bytes or 0) > 3_500_000_000
    steps: dict[str, str] = {"health_and_catalog": "1–5 s"}
    if scenario != "preflight":
        steps.update(
            {
                "load": "30–180 s" if large else "15–90 s",
                "first_token": "5–60 s",
                "generation_or_cancel": "15–120 s" if scenario == "smoke" else "5–20 s",
                "unload": "2–30 s",
            }
        )
    global_eta = "1–5 s" if scenario == "preflight" else ("2–7 min" if large else "1–5 min")
    return {"steps": steps, "global": global_eta}


def print_eta(eta: dict[str, Any]) -> None:
    print(f"ETA global estimada: {eta['global']}")
    for name, value in eta["steps"].items():
        print(f"  - {name}: {value}")


async def preflight(
    runtime: OllamaRuntime,
    model_name: str | None,
) -> tuple[list[ModelDescriptor], bool]:
    started = time.perf_counter()
    health = await runtime.health()
    models = list(await runtime.list_models()) if health is RuntimeHealth.READY else []
    elapsed = time.perf_counter() - started
    print(f"Ollama: {health.value}; modelos instalados: {len(models)}; duración: {elapsed:.2f} s")
    for descriptor in models:
        size_gib = (descriptor.size_bytes or 0) / 1024**3
        print(f"  - {descriptor.id.name} ({size_gib:.2f} GiB)")
    selected_ok = model_name is None or any(model.id.name == model_name for model in models)
    if model_name and not selected_ok:
        print(f"El modelo solicitado no está instalado: {model_name}", file=sys.stderr)
    return models, health is RuntimeHealth.READY and selected_ok


async def run_reported_scenario(
    runtime: OllamaRuntime,
    args: argparse.Namespace,
    descriptor: ModelDescriptor,
    eta: dict[str, Any],
) -> dict[str, Any]:
    model_id = descriptor.id
    operation_id = str(uuid4())
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": operation_id,
        "scenario": args.scenario,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "model": descriptor.model_dump(mode="json"),
        "eta": eta,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ollama_python": package_version("ollama"),
            "base_url": args.base_url,
        },
        "input": {
            "prompt_length": len(args.prompt),
            "prompt_sha256": hashlib.sha256(args.prompt.encode("utf-8")).hexdigest(),
            "max_new_tokens": args.max_new_tokens,
            "keep_alive_seconds": args.keep_alive,
        },
        "steps": [],
        "events": {},
    }
    response_parts: list[str] = []
    metrics: dict[str, Any] = {}
    kinds: list[str] = []
    loaded = False
    total_started = time.perf_counter()

    def record_step(name: str, started: float, status: str, **details: Any) -> None:
        report["steps"].append(
            {
                "name": name,
                "status": status,
                "duration_seconds": time.perf_counter() - started,
                **details,
            }
        )

    try:
        load_started = time.perf_counter()
        state = await runtime.load(model_id, LoadPolicy(idle_timeout_seconds=args.keep_alive))
        loaded = True
        record_step("load", load_started, "passed", state=state.model_dump(mode="json"))

        chat_input = ChatInput(
            messages=(ChatMessage(role=MessageRole.USER, content=args.prompt),),
            options=ChatOptions(max_new_tokens=args.max_new_tokens, temperature=0),
            keep_alive_seconds=args.keep_alive,
        )
        request = InferenceRequest(
            operation_id=operation_id,
            model=model_id,
            inputs=chat_input.model_dump(mode="json"),
        )
        chat_started = time.perf_counter()
        cancel_task: asyncio.Task[None] | None = None
        if args.scenario == "cancel":
            cancel_task = asyncio.create_task(
                cancel_later(runtime, operation_id, args.cancel_after)
            )
        try:
            async for event in runtime.run(request):
                kinds.append(event.kind.value)
                if event.kind is RuntimeEventKind.TEXT_DELTA:
                    text = event.payload.get("text")
                    if isinstance(text, str):
                        response_parts.append(text)
                elif event.kind is RuntimeEventKind.METRICS:
                    metrics.update(event.payload)
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
        expected = (
            RuntimeEventKind.CANCELLED.value
            if args.scenario == "cancel"
            else RuntimeEventKind.COMPLETED.value
        )
        chat_status = "passed" if expected in kinds else "failed"
        record_step("chat", chat_started, chat_status, expected_terminal_event=expected)
        report["status"] = chat_status
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if loaded:
            unload_started = time.perf_counter()
            try:
                state = await asyncio.shield(runtime.unload(model_id))
                record_step("unload", unload_started, "passed", state=state.model_dump(mode="json"))
            except Exception as error:
                record_step(
                    "unload",
                    unload_started,
                    "failed",
                    error={"type": type(error).__name__, "message": str(error)},
                )
                report["status"] = "failed"
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = time.perf_counter() - total_started
        report["events"] = {"kinds": kinds, "metrics": metrics}
        report["response"] = {
            "characters": sum(len(part) for part in response_parts),
            "excerpt": "".join(response_parts)[:2000],
        }
    return report


async def cancel_later(runtime: OllamaRuntime, operation_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    await runtime.cancel(operation_id)


def package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def write_report(report_dir: Path, report: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"ollama-{report['scenario']}-{stamp}-{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def async_main(args: argparse.Namespace) -> int:
    runtime = OllamaRuntime(args.base_url)
    try:
        if args.scenario == "preflight":
            print_eta(estimate(None, args.scenario))
            _, success = await preflight(runtime, args.model)
            return 0 if success else 1

        if not args.model:
            print("--model es obligatorio para smoke y cancel.", file=sys.stderr)
            return 2
        models, ready = await preflight(runtime, args.model)
        descriptor = next((model for model in models if model.id.name == args.model), None)
        eta = estimate(descriptor, args.scenario)
        print_eta(eta)
        if not ready or descriptor is None:
            return 1
        try:
            async with asyncio.timeout(args.timeout):
                report = await run_reported_scenario(runtime, args, descriptor, eta)
        except TimeoutError as error:
            report = {
                "schema_version": 1,
                "run_id": str(uuid4()),
                "scenario": args.scenario,
                "status": "failed",
                "error": {
                    "type": type(error).__name__,
                    "message": f"Timeout global de {args.timeout} s",
                },
                "eta": eta,
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        report_path = write_report(args.report_dir, report)
        print(f"Reporte único: {report_path}")
        print(f"Resultado: {report['status']}")
        return 0 if report["status"] == "passed" else 1
    finally:
        await runtime.close()


def main() -> None:
    args = parse_args()
    if args.prompt is None:
        args.prompt = DEFAULT_CANCEL_PROMPT if args.scenario == "cancel" else DEFAULT_PROMPT
    invalid_limits = (
        args.max_new_tokens < 1
        or args.keep_alive < 0
        or args.cancel_after <= 0
        or args.timeout <= 0
    )
    if invalid_limits:
        raise SystemExit(
            "Los límites y tiempos deben ser positivos; keep-alive también admite cero."
        )
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()

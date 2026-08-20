import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    ComputeDevice,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationRequest,
    LoadPolicy,
    ProcessState,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.infrastructure.runtimes.fooocus import (
    FooocusProcessSettings,
    FooocusProcessSupervisor,
    FooocusRuntime,
)


class FakeSupervisor:
    def __init__(self, settings: FooocusProcessSettings) -> None:
        self.settings = settings
        self.running = False
        self.process_id: int | None = None
        self.recent_logs: tuple[str, ...] = ()
        self.selected_checkpoint: str | None = None

    def preflight(self) -> tuple[str, ...]:
        return ()

    async def start(self) -> None:
        self.running = True
        self.process_id = 123

    def select_checkpoint(self, name: str) -> None:
        self.selected_checkpoint = name

    async def stop(self) -> None:
        self.running = False
        self.process_id = None


class FakeTransport:
    def __init__(self, image: Path) -> None:
        self.image = image
        self.cancelled: list[str] = []

    def preflight(self) -> tuple[str, ...]:
        return ()

    async def health(self) -> bool:
        return True

    async def list_models(self) -> Sequence[str]:
        return ()

    async def list_styles(self) -> Sequence[str]:
        return ("Fooocus V2", "Fooocus Photograph")

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        yield ImageGenerationEvent(
            operation_id=request.operation_id,
            kind=ImageGenerationEventKind.IMAGE,
            source_path=self.image,
        )

    async def cancel(self, operation_id: str) -> None:
        self.cancelled.append(operation_id)

    async def close(self) -> None:
        return None


class FailingTransport(FakeTransport):
    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        del request
        raise ConnectionResetError("connection reset")
        yield  # pragma: no cover


class HangingTransport(FakeTransport):
    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        del request
        await asyncio.Future()
        yield  # pragma: no cover


class CancelAwareTransport(FakeTransport):
    def __init__(self, image: Path) -> None:
        super().__init__(image)
        self.cancel_requested = asyncio.Event()

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        del request
        await self.cancel_requested.wait()
        raise RuntimeRequestError("job cancelled")
        yield  # pragma: no cover

    async def cancel(self, operation_id: str) -> None:
        await super().cancel(operation_id)
        self.cancel_requested.set()


class CancelEventTransport(CancelAwareTransport):
    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        await self.cancel_requested.wait()
        yield ImageGenerationEvent(
            operation_id=request.operation_id,
            kind=ImageGenerationEventKind.CANCELLED,
        )


def test_runtime_discovers_loads_and_streams_local_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        models = tmp_path / "models"
        checkpoints = models / "checkpoints"
        checkpoints.mkdir(parents=True)
        checkpoint = checkpoints / "juggernautXL.safetensors"
        checkpoint.write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=models,
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
            startup_timeout_seconds=1,
        )
        supervisor = FakeSupervisor(settings)
        transport = FakeTransport(tmp_path / "image.png")
        runtime = FooocusRuntime(supervisor, transport)
        descriptor = (await runtime.list_models())[0]

        state = await runtime.load(descriptor.id, LoadPolicy(device=ComputeDevice.GPU))
        events = [
            event
            async for event in runtime.generate(
                ImageGenerationRequest(
                    operation_id="run-1", model=descriptor.id, prompt="local landscape"
                )
            )
        ]

        assert state.loaded_in_gpu
        assert state.process_state is ProcessState.RUNNING
        assert supervisor.selected_checkpoint == checkpoint.name
        assert events[0].source_path == tmp_path / "image.png"
        assert await runtime.list_styles() == ("Fooocus V2", "Fooocus Photograph")
        await runtime.unload(descriptor.id)
        assert supervisor.running is False

    asyncio.run(scenario())


def test_runtime_rejects_cpu_load(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoints = tmp_path / "models" / "checkpoints"
        checkpoints.mkdir(parents=True)
        (checkpoints / "model.safetensors").write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
        runtime = FooocusRuntime(FakeSupervisor(settings), FakeTransport(tmp_path / "x.png"))
        model = (await runtime.list_models())[0].id

        with pytest.raises(RuntimeRequestError, match="requiere GPU"):
            await runtime.load(
                model,
                LoadPolicy(device=ComputeDevice.CPU),
            )

    asyncio.run(scenario())


def test_runtime_generation_error_includes_recent_process_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoints = tmp_path / "models" / "checkpoints"
        checkpoints.mkdir(parents=True)
        (checkpoints / "model.safetensors").write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
        supervisor = FakeSupervisor(settings)
        supervisor.recent_logs = ("CUDA failure detail",)
        runtime = FooocusRuntime(supervisor, FailingTransport(tmp_path / "x.png"))
        model = (await runtime.list_models())[0].id
        await runtime.load(model, LoadPolicy(device=ComputeDevice.GPU))

        with pytest.raises(RuntimeRequestError, match="CUDA failure detail"):
            async for _ in runtime.generate(
                ImageGenerationRequest(operation_id="run-fail", model=model, prompt="x")
            ):
                pass

    asyncio.run(scenario())


def test_supervisor_preflight_requires_offline_support_assets(tmp_path: Path) -> None:
    models = tmp_path / "models"
    checkpoints = models / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "model.safetensors").write_bytes(b"weights")
    (tmp_path / "launch.py").write_text("", encoding="utf-8")
    bundled = tmp_path / "models/prompt_expansion/fooocus_expansion"
    bundled.mkdir(parents=True)
    for filename in FooocusProcessSupervisor._PROMPT_EXPANSION_SUPPORT_FILES:
        (bundled / filename).write_text(filename, encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    supervisor = FooocusProcessSupervisor(
        FooocusProcessSettings(
            home=tmp_path,
            python_executable=python,
            models_root=models,
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
    )

    issues = supervisor.preflight()

    assert len(issues) == 4
    assert all("activo auxiliar Fooocus" in issue for issue in issues)


def test_supervisor_stages_bundled_prompt_expansion_files(tmp_path: Path) -> None:
    source = tmp_path / "app/models/prompt_expansion/fooocus_expansion"
    source.mkdir(parents=True)
    for filename in FooocusProcessSupervisor._PROMPT_EXPANSION_SUPPORT_FILES:
        (source / filename).write_text(f"official-{filename}", encoding="utf-8")
    models = tmp_path / "shared-models"
    supervisor = FooocusProcessSupervisor(
        FooocusProcessSettings(
            home=tmp_path / "app",
            python_executable=tmp_path / "python.exe",
            models_root=models,
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
    )

    supervisor._write_runtime_config()

    destination = models / "prompt_expansion/fooocus_expansion"
    for filename in FooocusProcessSupervisor._PROMPT_EXPANSION_SUPPORT_FILES:
        assert (destination / filename).read_text("utf-8") == f"official-{filename}"


def test_supervisor_keeps_fooocus_temp_files_inside_runtime(tmp_path: Path) -> None:
    supervisor = FooocusProcessSupervisor(
        FooocusProcessSettings(
            home=tmp_path / "app",
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
    )

    arguments = supervisor._launch_arguments()

    temp_index = arguments.index("--temp-path")
    assert Path(arguments[temp_index + 1]) == tmp_path / "runtime/gradio"


def test_runtime_detects_crashed_internal_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoints = tmp_path / "models/checkpoints"
        checkpoints.mkdir(parents=True)
        (checkpoints / "model.safetensors").write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
        supervisor = FakeSupervisor(settings)
        transport = HangingTransport(tmp_path / "x.png")
        runtime = FooocusRuntime(
            supervisor,
            transport,
            worker_watchdog_seconds=0.01,
        )
        model = (await runtime.list_models())[0].id
        await runtime.load(model, LoadPolicy(device=ComputeDevice.GPU))

        async def publish_failure() -> None:
            await asyncio.sleep(0.02)
            supervisor.recent_logs = (
                "Exception in thread Thread-1 (worker):",
                "OSError: missing config.json",
            )

        publisher = asyncio.create_task(publish_failure())
        with pytest.raises(RuntimeRequestError, match="worker interno"):
            async for _ in runtime.generate(
                ImageGenerationRequest(operation_id="run-crash", model=model, prompt="x")
            ):
                pass
        await publisher

        assert transport.cancelled == ["run-crash"]
        assert supervisor.running is False

    asyncio.run(scenario())


def test_runtime_classifies_active_job_cancellation_as_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoints = tmp_path / "models/checkpoints"
        checkpoints.mkdir(parents=True)
        (checkpoints / "model.safetensors").write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
        supervisor = FakeSupervisor(settings)
        transport = CancelAwareTransport(tmp_path / "x.png")
        runtime = FooocusRuntime(supervisor, transport, cancel_grace_seconds=0)
        model = (await runtime.list_models())[0].id
        await runtime.load(model, LoadPolicy(device=ComputeDevice.GPU))
        request = ImageGenerationRequest(operation_id="run-cancel", model=model, prompt="x")

        async def consume() -> list[ImageGenerationEvent]:
            return [event async for event in runtime.generate(request)]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await runtime.cancel(request.operation_id)
        events = await consumer

        assert [event.kind for event in events] == [ImageGenerationEventKind.CANCELLED]
        assert transport.cancelled == ["run-cancel"]

    asyncio.run(scenario())


def test_runtime_does_not_duplicate_transport_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        checkpoints = tmp_path / "models/checkpoints"
        checkpoints.mkdir(parents=True)
        (checkpoints / "model.safetensors").write_bytes(b"weights")
        settings = FooocusProcessSettings(
            home=tmp_path,
            python_executable=tmp_path / "python.exe",
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
        transport = CancelEventTransport(tmp_path / "x.png")
        runtime = FooocusRuntime(
            FakeSupervisor(settings), transport, cancel_grace_seconds=0
        )
        model = (await runtime.list_models())[0].id
        await runtime.load(model, LoadPolicy(device=ComputeDevice.GPU))
        request = ImageGenerationRequest(operation_id="run-cancel-event", model=model, prompt="x")

        async def consume() -> list[ImageGenerationEvent]:
            return [event async for event in runtime.generate(request)]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await runtime.cancel(request.operation_id)
        events = await consumer

        assert [event.kind for event in events] == [ImageGenerationEventKind.CANCELLED]

    asyncio.run(scenario())


def test_supervisor_preflight_rejects_incompatible_gradio_server_stack(
    tmp_path: Path,
) -> None:
    python = tmp_path / "env" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    site_packages = tmp_path / "env" / "Lib" / "site-packages"
    for package, version in {
        "gradio": "3.41.2",
        "fastapi": "0.141.1",
        "starlette": "1.6.0",
    }.items():
        metadata = site_packages / f"{package}-{version}.dist-info" / "METADATA"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(f"Name: {package}\nVersion: {version}\n", encoding="utf-8")
    supervisor = FooocusProcessSupervisor(
        FooocusProcessSettings(
            home=tmp_path,
            python_executable=python,
            models_root=tmp_path / "models",
            staging_root=tmp_path / "staging",
            runtime_root=tmp_path / "runtime",
        )
    )

    issues = supervisor.preflight()

    assert any("fastapi==0.101.0" in issue and "0.141.1" in issue for issue in issues)
    assert any("starlette==0.27.0" in issue and "1.6.0" in issue for issue in issues)

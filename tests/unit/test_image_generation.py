from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from PIL import Image

from aiopenstudio.core.contracts import (
    ComputeDevice,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationOptions,
    ImageGenerationRequest,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeHealth,
    UnloadTarget,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.services import ImageGenerationService, ImageRunStore


class MemoryCatalog:
    def __init__(self, descriptor: ModelDescriptor) -> None:
        self._models = {descriptor.id.key: descriptor}

    def save(self, descriptor: ModelDescriptor) -> None:
        self._models[descriptor.id.key] = descriptor

    def get(self, model: ModelId) -> ModelDescriptor | None:
        return self._models.get(model.key)

    def list(self, runtime: str | None = None) -> Sequence[ModelDescriptor]:
        return tuple(
            item for item in self._models.values() if runtime is None or item.id.runtime == runtime
        )

    def remove(self, model: ModelId) -> None:
        self._models.pop(model.key, None)


class NoopLease:
    async def __aenter__(self) -> NoopLease:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class NoopLeases:
    def lease(self, _: ModelId) -> NoopLease:
        return NoopLease()


class FakeImageRuntime:
    name = "fooocus"
    capabilities = RuntimeCapabilities(
        manages_process=True, supports_streaming=True, supports_cancellation=True
    )

    def __init__(self, descriptor: ModelDescriptor, image: Path) -> None:
        self.descriptor = descriptor
        self.image = image
        self.loaded = False
        self.cancelled: list[str] = []
        self.generated = False

    @property
    def process_id(self) -> int | None:
        return 123 if self.loaded else None

    def preflight(self) -> tuple[str, ...]:
        return ()

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        return ProcessState.RUNNING if self.loaded else ProcessState.STOPPED

    async def start(self) -> ProcessState:
        return ProcessState.RUNNING

    async def stop(self) -> ProcessState:
        self.loaded = False
        return ProcessState.STOPPED

    async def list_models(self) -> Sequence[ModelDescriptor]:
        return (self.descriptor,)

    async def list_styles(self) -> Sequence[str]:
        return ("Fooocus V2",)

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        assert policy.device is ComputeDevice.GPU
        self.loaded = True
        return await self.state(model)

    async def unload(
        self, model: ModelId, target: UnloadTarget = UnloadTarget.ALL
    ) -> ModelState:
        del target
        self.loaded = False
        return await self.state(model)

    async def state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            process_state=ProcessState.RUNNING if self.loaded else ProcessState.STOPPED,
            ram_residency=ResidencyState.LOADED if self.loaded else ResidencyState.UNLOADED,
            gpu_residency=ResidencyState.LOADED if self.loaded else ResidencyState.UNLOADED,
            active_device=ComputeDevice.GPU if self.loaded else None,
        )

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        self.generated = True
        yield ImageGenerationEvent(
            operation_id=request.operation_id,
            kind=ImageGenerationEventKind.IMAGE,
            source_path=self.image,
            seed=42,
        )

    async def cancel(self, operation_id: str) -> None:
        self.cancelled.append(operation_id)


def test_image_options_require_dimensions_divisible_by_64() -> None:
    with pytest.raises(ValueError, match="multiples of 64"):
        ImageGenerationOptions(width=1000, height=1024)


def test_run_store_isolates_images_metadata_and_rejects_foreign_paths(tmp_path: Path) -> None:
    async def scenario() -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        source = staging / "result.png"
        Image.new("RGB", (64, 32), "blue").save(source)
        model = ModelId(runtime="fooocus", name="model.safetensors")
        request = ImageGenerationRequest(operation_id="safe-run", model=model, prompt="blue")
        store = ImageRunStore(tmp_path / "outputs", allowed_source_roots=(staging,))

        await store.begin(request)
        artifact = await store.add_image(request, source, 1, 12)
        result = await store.finish(
            request, (artifact,), elapsed_seconds=1.5, status="completed"
        )
        metadata = json.loads((result.run_directory / "metadata.json").read_text("utf-8"))

        assert artifact.path == result.run_directory / "images" / "0001.png"
        assert (artifact.width, artifact.height) == (64, 32)
        assert metadata["request"]["prompt"] == "blue"
        assert metadata["images"][0]["sha256"] == artifact.sha256
        with pytest.raises(RuntimeRequestError, match="staging permitido"):
            await store.add_image(request, tmp_path / "foreign.png", 2, None)

    asyncio.run(scenario())


def test_service_honors_cancellation_requested_during_model_load(tmp_path: Path) -> None:
    class SlowLoadRuntime(FakeImageRuntime):
        async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
            await asyncio.sleep(0.05)
            return await super().load(model, policy)

    async def scenario() -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        generated = staging / "generated.png"
        Image.new("RGB", (64, 64), "blue").save(generated)
        model = ModelId(runtime="fooocus", name="model.safetensors")
        descriptor = ModelDescriptor(id=model, display_name="model", installed=True)
        runtime = SlowLoadRuntime(descriptor, generated)
        service = ImageGenerationService(
            runtime,
            MemoryCatalog(descriptor),
            ImageRunStore(tmp_path / "outputs", allowed_source_roots=(staging,)),
            NoopLeases(),  # type: ignore[arg-type]
        )
        request = ImageGenerationRequest(operation_id="run-cancel-load", model=model, prompt="x")

        async def consume() -> list[ImageGenerationEvent]:
            return [event async for event in service.stream_generation(request)]

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await service.cancel(request.operation_id)
        events = await consumer

        terminal = events[-1]
        assert terminal.kind is ImageGenerationEventKind.CANCELLED
        assert runtime.generated is False
        assert runtime.loaded is False
        await service.close()

    asyncio.run(scenario())


def test_service_publishes_one_terminal_cancellation_event(tmp_path: Path) -> None:
    class CancelEventRuntime(FakeImageRuntime):
        async def generate(
            self, request: ImageGenerationRequest
        ) -> AsyncIterator[ImageGenerationEvent]:
            yield ImageGenerationEvent(
                operation_id=request.operation_id,
                kind=ImageGenerationEventKind.CANCELLED,
            )

    async def scenario() -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        model = ModelId(runtime="fooocus", name="model.safetensors")
        descriptor = ModelDescriptor(id=model, display_name="model", installed=True)
        runtime = CancelEventRuntime(descriptor, staging / "unused.png")
        service = ImageGenerationService(
            runtime,
            MemoryCatalog(descriptor),
            ImageRunStore(tmp_path / "outputs", allowed_source_roots=(staging,)),
            NoopLeases(),  # type: ignore[arg-type]
        )
        request = ImageGenerationRequest(operation_id="run-cancel-event", model=model, prompt="x")

        events = [event async for event in service.stream_generation(request)]
        cancellations = [
            event for event in events if event.kind is ImageGenerationEventKind.CANCELLED
        ]

        assert len(cancellations) == 1
        assert cancellations[0].result is not None
        assert cancellations[0].result.cancelled is True
        await service.close()

    asyncio.run(scenario())


def test_service_streams_completed_artifact_and_releases_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        generated = staging / "generated.png"
        Image.new("RGB", (128, 128), "green").save(generated)
        model = ModelId(runtime="fooocus", name="model.safetensors")
        descriptor = ModelDescriptor(
            id=model,
            display_name="model",
            size_bytes=10,
            installed=True,
        )
        runtime = FakeImageRuntime(descriptor, generated)
        service = ImageGenerationService(
            runtime,
            MemoryCatalog(descriptor),
            ImageRunStore(tmp_path / "outputs", allowed_source_roots=(staging,)),
            NoopLeases(),  # type: ignore[arg-type]
        )
        request = ImageGenerationRequest(operation_id="run-1", model=model, prompt="green")

        events = [event async for event in service.stream_generation(request)]
        completed = next(
            event for event in events if event.kind is ImageGenerationEventKind.COMPLETED
        )

        assert completed.result is not None
        assert completed.result.images[0].path.is_file()
        assert completed.result.images[0].seed == 42
        assert runtime.loaded is False
        await service.close()

    asyncio.run(scenario())


def test_service_cancels_runtime_when_generated_path_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        foreign = tmp_path / "foreign.png"
        Image.new("RGB", (64, 64), "red").save(foreign)
        model = ModelId(runtime="fooocus", name="model.safetensors")
        descriptor = ModelDescriptor(id=model, display_name="model", installed=True)
        runtime = FakeImageRuntime(descriptor, foreign)
        service = ImageGenerationService(
            runtime,
            MemoryCatalog(descriptor),
            ImageRunStore(tmp_path / "outputs", allowed_source_roots=(staging,)),
            NoopLeases(),  # type: ignore[arg-type]
        )
        request = ImageGenerationRequest(operation_id="run-rejected", model=model, prompt="red")

        events = [event async for event in service.stream_generation(request)]

        assert any(event.kind is ImageGenerationEventKind.ERROR for event in events)
        assert runtime.cancelled == ["run-rejected"]
        assert runtime.loaded is False
        await service.close()

    asyncio.run(scenario())

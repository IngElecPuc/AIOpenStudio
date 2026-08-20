import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from aiopenstudio.core.contracts import (
    InferenceRequest,
    InferenceTelemetry,
    LoadPolicy,
    MessageRole,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeHealth,
    UnloadTarget,
)
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.services import LLMService


class FakeRuntime:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.loaded: set[str] = set()
        self.descriptor = ModelDescriptor(
            id=ModelId(runtime="fake", name="test-model"),
            display_name="Test model",
            installed=True,
            capabilities=frozenset({"chat"}),
        )

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(supports_streaming=True, supports_cancellation=True)

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth.READY

    async def process_state(self) -> ProcessState:
        return ProcessState.RUNNING

    async def start(self) -> ProcessState:
        return ProcessState.RUNNING

    async def stop(self) -> ProcessState:
        return ProcessState.STOPPED

    async def list_models(self) -> Sequence[ModelDescriptor]:
        return [self.descriptor]

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        self.loaded.add(model.key)
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            ram_residency=ResidencyState.LOADED,
        )

    async def unload(
        self,
        model: ModelId,
        target: UnloadTarget = UnloadTarget.ALL,
    ) -> ModelState:
        self.loaded.discard(model.key)
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def state(self, model: ModelId) -> ModelState:
        return ModelState(
            model=model,
            runtime_health=RuntimeHealth.READY,
            ram_residency=(
                ResidencyState.LOADED if model.key in self.loaded else ResidencyState.UNLOADED
            ),
        )

    async def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]:
        self.loaded.add(request.model.key)
        yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.STARTED)
        yield RuntimeEvent(
            operation_id=request.operation_id,
            kind=RuntimeEventKind.TEXT_DELTA,
            payload={"text": "Respuesta de prueba"},
        )
        yield RuntimeEvent(
            operation_id=request.operation_id,
            kind=RuntimeEventKind.METRICS,
            payload={"eval_count": 3},
        )
        yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.COMPLETED)

    async def cancel(self, operation_id: str) -> None:
        self.cancelled.append(operation_id)


def test_service_reconciles_catalog_and_persists_chat(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        service = LLMService(runtime=runtime, catalog=store, memory=store)
        stale = ModelDescriptor(
            id=ModelId(runtime="fake", name="removed-model"),
            display_name="Removed model",
            installed=True,
        )
        store.save(stale)

        models = await service.refresh_models()
        conversation = service.create_conversation()
        events = [
            event
            async for event in service.stream_chat(
                operation_id="operation-1",
                conversation_id=conversation.id,
                model=runtime.descriptor.id,
                prompt="Pregunta de prueba",
            )
        ]

        assert models == (runtime.descriptor,)
        assert store.get(stale.id) is None
        assert store.get(runtime.descriptor.id) == runtime.descriptor
        assert events[-1].kind is RuntimeEventKind.COMPLETED
        messages = service.list_messages(conversation.id)
        assert [message.role for message in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert messages[1].content == "Respuesta de prueba"
        assert messages[1].metadata["metrics"] == {"eval_count": 3}
        assert service.list_conversations()[0].title == "Pregunta de prueba"

    asyncio.run(scenario())


class RecordingCoordinator:
    def __init__(self) -> None:
        self.metrics: list[InferenceTelemetry] = []
        self.lifecycle: list[str] = []

    async def before_load(
        self,
        model: ModelId,
        policy: LoadPolicy,
        estimated_weight_bytes: int | None = None,
    ) -> None:
        self.lifecycle.append(f"before:{model.name}:{estimated_weight_bytes}")

    def model_loaded(self, state: ModelState, policy: LoadPolicy) -> None:
        self.lifecycle.append(f"loaded:{state.model.name}")

    def model_load_failed(self, model: ModelId) -> None:
        self.lifecycle.append(f"failed:{model.name}")

    def model_used(self, model: ModelId) -> None:
        self.lifecycle.append(f"used:{model.name}")

    def model_unloaded(self, model: ModelId) -> None:
        self.lifecycle.append(f"unloaded:{model.name}")

    def record_inference(self, metrics: InferenceTelemetry) -> None:
        self.metrics.append(metrics)


def test_service_emits_lifecycle_and_inference_telemetry(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        runtime.descriptor.size_bytes = 2048
        coordinator = RecordingCoordinator()
        service = LLMService(
            runtime=runtime,
            catalog=store,
            memory=store,
            metrics_sink=coordinator,
            residency_policy=coordinator,
        )
        await service.refresh_models()
        await service.load_model(runtime.descriptor.id, LoadPolicy())
        conversation = service.create_conversation()
        events = service.stream_chat(
            operation_id="operation-metrics",
            conversation_id=conversation.id,
            model=runtime.descriptor.id,
            prompt="Mide esto",
        )
        _ = [event async for event in events]
        await service.unload_model(runtime.descriptor.id)
        implicit = service.create_conversation()
        _ = [
            event
            async for event in service.stream_chat(
                operation_id="operation-implicit-load",
                conversation_id=implicit.id,
                model=runtime.descriptor.id,
                prompt="Carga implícita",
            )
        ]

        assert coordinator.lifecycle == [
            "before:test-model:2048",
            "loaded:test-model",
            "used:test-model",
            "unloaded:test-model",
            "before:test-model:2048",
            "loaded:test-model",
        ]
        assert coordinator.metrics[0].output_tokens == 3

    asyncio.run(scenario())


def test_service_delegates_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        service = LLMService(runtime=runtime, catalog=store, memory=store)

        await service.cancel("operation-2")

        assert runtime.cancelled == ["operation-2"]

    asyncio.run(scenario())


def test_service_reports_busy_until_stream_finishes(tmp_path: Path) -> None:
    class BlockingRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]:
            self.started.set()
            yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.STARTED)
            await self.release.wait()
            yield RuntimeEvent(operation_id=request.operation_id, kind=RuntimeEventKind.COMPLETED)

    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = BlockingRuntime()
        service = LLMService(runtime=runtime, catalog=store, memory=store)
        conversation = service.create_conversation()

        async def consume() -> None:
            _ = [
                event
                async for event in service.stream_chat(
                    operation_id="busy-operation",
                    conversation_id=conversation.id,
                    model=runtime.descriptor.id,
                    prompt="Espera",
                )
            ]

        task = asyncio.create_task(consume())
        await runtime.started.wait()
        waiter = asyncio.create_task(service.wait_until_idle(runtime.descriptor.id))
        await asyncio.sleep(0)
        assert not waiter.done()
        runtime.release.set()
        await task
        await waiter

    asyncio.run(scenario())

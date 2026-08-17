import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from aiopenstudio.core.contracts import (
    InferenceRequest,
    LoadPolicy,
    MessageRole,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
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
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def unload(
        self,
        model: ModelId,
        target: UnloadTarget = UnloadTarget.ALL,
    ) -> ModelState:
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def state(self, model: ModelId) -> ModelState:
        return ModelState(model=model, runtime_health=RuntimeHealth.READY)

    async def run(self, request: InferenceRequest) -> AsyncIterator[RuntimeEvent]:
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


def test_service_delegates_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        service = LLMService(runtime=runtime, catalog=store, memory=store)

        await service.cancel("operation-2")

        assert runtime.cancelled == ["operation-2"]

    asyncio.run(scenario())

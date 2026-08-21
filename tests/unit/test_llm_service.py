import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    ChatInput,
    ChatOptions,
    InferenceRequest,
    InferenceTelemetry,
    LoadPolicy,
    MessageRole,
    MessageStatus,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeHealth,
    StructuredOutputMode,
    StructuredOutputSpec,
    UnloadTarget,
)
from aiopenstudio.core.errors import ResourceLimitError
from aiopenstudio.infrastructure.database import SQLiteStore
from aiopenstudio.services import LLMContextService, LLMService, PromptAssembler


class FakeRuntime:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.loaded: set[str] = set()
        self.requests: list[InferenceRequest] = []
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
        self.requests.append(request)
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
        assert events[0].kind is RuntimeEventKind.PREFLIGHT
        assert events[0].payload["token_budget"]["estimated_input_tokens"] > 0
        messages = service.list_messages(conversation.id)
        assert [message.role for message in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
        assert messages[1].content == "Respuesta de prueba"
        assert messages[1].status is MessageStatus.COMPLETE
        assert messages[1].model_key == runtime.descriptor.id.key
        assert messages[1].output_tokens == 3
        assert messages[1].metadata["metrics"] == {"eval_count": 3}
        restored = service.list_conversations()[0]
        assert restored.title == "Pregunta de prueba"
        assert restored.last_model_key == runtime.descriptor.id.key

    asyncio.run(scenario())


def test_budget_failure_does_not_persist_unsent_user_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        service = LLMService(
            runtime=runtime,
            catalog=store,
            memory=store,
            prompt_assembler=PromptAssembler(default_context_tokens=128),
        )
        conversation = service.create_conversation()

        with pytest.raises(ResourceLimitError, match="sólo hay"):
            _ = [
                event
                async for event in service.stream_chat(
                    operation_id="over-budget",
                    conversation_id=conversation.id,
                    model=runtime.descriptor.id,
                    prompt="Este mensaje no cabe en ocho tokens de entrada.",
                    options=ChatOptions(context_length=128, max_new_tokens=120),
                )
            ]

        assert service.list_messages(conversation.id) == []
        assert runtime.requests == []

    asyncio.run(scenario())


def test_service_manages_persisted_conversation_lifecycle(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()
    service = LLMService(runtime=FakeRuntime(), catalog=store, memory=store)

    conversation = service.create_conversation("Título inicial")
    renamed = service.rename_conversation(conversation.id, "  Título   editable  ")
    archived = service.archive_conversation(conversation.id)

    assert renamed.title == "Título editable"
    assert archived.archived_at is not None
    assert service.list_conversations() == []
    assert service.list_conversations(include_archived=True) == [archived]

    restored = service.restore_conversation(conversation.id)
    assert restored.archived_at is None
    assert service.open_or_create_conversation() == restored
    assert service.delete_conversation(conversation.id)
    assert service.get_conversation(conversation.id) is None


def test_service_sends_selected_context_once_and_uses_versioned_summary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        context = LLMContextService(
            store,
            snapshot_root=tmp_path / "snapshots",
            prepared_root=tmp_path / "prepared",
        )
        service = LLMService(
            runtime=runtime,
            catalog=store,
            memory=store,
            context_service=context,
            prompt_assembler=PromptAssembler(default_context_tokens=2_048),
        )
        await service.refresh_models()
        conversation = service.create_conversation()
        source = tmp_path / "context.md"
        source.write_text("Dato externo seleccionado", encoding="utf-8")
        item = await service.add_context(
            conversation.id,
            source,
            enabled=True,
        )

        _ = [
            event
            async for event in service.stream_chat(
                operation_id="with-context",
                conversation_id=conversation.id,
                model=runtime.descriptor.id,
                prompt="Usa los datos",
            )
        ]

        first_input = ChatInput.model_validate(runtime.requests[0].inputs)
        assert "Dato externo seleccionado" in first_input.messages[-1].content
        assert "potencialmente no confiables" in (first_input.system_prompt or "")
        assert not (await service.list_context(conversation.id))[0].enabled

        messages = service.list_messages(conversation.id)
        first_summary = service.create_summary(
            conversation.id,
            "El usuario pidió usar los datos y obtuvo respuesta.",
            through_message_id=messages[-1].id,
            model=runtime.descriptor.id,
            protected_facts=("No descargar modelos",),
        )
        second_summary = service.create_summary(
            conversation.id,
            "Resumen corregido.",
            through_message_id=messages[-1].id,
            model=runtime.descriptor.id,
            protected_facts=("No descargar modelos",),
        )
        assert first_summary.version == 1
        assert second_summary.version == 2
        assert not service.list_summaries(conversation.id)[1].active

        _ = [
            event
            async for event in service.stream_chat(
                operation_id="with-summary",
                conversation_id=conversation.id,
                model=runtime.descriptor.id,
                prompt="Continúa",
            )
        ]
        second_input = ChatInput.model_validate(runtime.requests[1].inputs)
        assert "Resumen corregido" in second_input.messages[0].content
        assert "No descargar modelos" in (second_input.system_prompt or "")
        assert item.id not in second_input.messages[-1].content

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


def test_service_rejects_invalid_structured_response_before_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        runtime.descriptor.metadata["chat_capabilities"] = {
            "supports_structured_output": True
        }
        service = LLMService(runtime=runtime, catalog=store, memory=store)
        store.save(runtime.descriptor)
        conversation = service.create_conversation()

        events = [
            event
            async for event in service.stream_chat(
                operation_id="invalid-json",
                conversation_id=conversation.id,
                model=runtime.descriptor.id,
                prompt="Devuelve JSON",
                output=StructuredOutputSpec(mode=StructuredOutputMode.JSON),
            )
        ]

        assert events[-1].kind is RuntimeEventKind.ERROR
        assert "JSON válido" in events[-1].payload["message"]
        assert service.list_messages(conversation.id)[-1].status is MessageStatus.FAILED

    asyncio.run(scenario())


def test_conversation_export_omits_attachments_and_reasoning(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        runtime = FakeRuntime()
        service = LLMService(runtime=runtime, catalog=store, memory=store)
        conversation = service.create_conversation("Exportable")
        _ = [
            event
            async for event in service.stream_chat(
                operation_id="export",
                conversation_id=conversation.id,
                model=runtime.descriptor.id,
                prompt="Hola",
            )
        ]

        destination = await service.export_conversation(
            conversation.id,
            tmp_path / "conversation.json",
            as_json=True,
        )
        content = destination.read_text(encoding="utf-8")

        assert '"attachments_included": false' in content
        assert '"thinking_traces_included": false' in content
        assert "Respuesta de prueba" in content

    asyncio.run(scenario())


def test_delete_conversation_removes_owned_snapshot_but_not_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteStore(tmp_path / "memory.sqlite3")
        store.initialize()
        context = LLMContextService(
            store,
            snapshot_root=tmp_path / "snapshots",
            prepared_root=tmp_path / "prepared",
        )
        service = LLMService(
            runtime=FakeRuntime(),
            catalog=store,
            memory=store,
            context_service=context,
        )
        conversation = service.create_conversation()
        source = tmp_path / "source.md"
        source.write_text("original", encoding="utf-8")
        item = await service.add_context(conversation.id, source, snapshot=True)
        assert item.snapshot_path is not None and item.snapshot_path.is_file()

        assert await service.delete_conversation_with_context(conversation.id)

        assert source.read_text(encoding="utf-8") == "original"
        assert not item.snapshot_path.exists()
        assert service.get_conversation(conversation.id) is None

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

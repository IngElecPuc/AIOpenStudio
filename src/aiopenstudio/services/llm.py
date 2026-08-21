"""Use cases for the LLM suite, independent of any runtime SDK or UI toolkit."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from aiopenstudio.core.contracts import (
    ChatInput,
    ChatMessage,
    ChatOptions,
    ComputeDevice,
    Conversation,
    ConversationMemory,
    ConversationMessage,
    ExecutionHistory,
    ExecutionRecord,
    ExecutionStatus,
    InferenceMetricsSink,
    InferenceRequest,
    InferenceTelemetry,
    LoadPolicy,
    MessageRole,
    ModelCatalog,
    ModelDescriptor,
    ModelId,
    ModelRuntime,
    ModelState,
    ResidencyPolicy,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeHealth,
    UnloadTarget,
)
from aiopenstudio.core.errors import RuntimeRequestError


class LLMService:
    """Coordinate catalog, lifecycle and persisted conversations for one LLM runtime."""

    def __init__(
        self,
        runtime: ModelRuntime,
        catalog: ModelCatalog,
        memory: ConversationMemory,
        metrics_sink: InferenceMetricsSink | None = None,
        residency_policy: ResidencyPolicy | None = None,
        execution_history: ExecutionHistory | None = None,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._memory = memory
        self._metrics_sink = metrics_sink
        self._residency_policy = residency_policy
        self._execution_history = execution_history
        self._active_operations: dict[str, int] = {}
        self._idle_events: dict[str, asyncio.Event] = {}
        self._load_policies: dict[str, LoadPolicy] = {}
        self._model_gates: dict[str, asyncio.Lock] = {}

    async def health(self) -> RuntimeHealth:
        return await self._runtime.health()

    async def refresh_models(self) -> Sequence[ModelDescriptor]:
        live_models = tuple(await self._runtime.list_models())
        live_keys = {descriptor.id.key for descriptor in live_models}
        for stale in self._catalog.list(runtime=self._runtime.name):
            if stale.id.key not in live_keys:
                self._catalog.remove(stale.id)
        for descriptor in live_models:
            self._catalog.save(descriptor)
        return live_models

    async def load_model(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        descriptor = self._catalog.get(model)
        if self._residency_policy is not None:
            await self._residency_policy.before_load(
                model,
                policy,
                descriptor.size_bytes if descriptor else None,
            )
        try:
            state = await self._runtime.load(model, policy)
        except Exception:
            if self._residency_policy is not None:
                self._residency_policy.model_load_failed(model)
            raise
        if self._residency_policy is not None:
            self._residency_policy.model_loaded(state, policy)
        self._load_policies[model.key] = policy
        return state

    async def unload_model(self, model: ModelId) -> ModelState:
        state = await self._runtime.unload(model, UnloadTarget.ALL)
        if self._residency_policy is not None:
            self._residency_policy.model_unloaded(model)
        self._load_policies.pop(model.key, None)
        return state

    async def model_state(self, model: ModelId) -> ModelState:
        return await self._runtime.state(model)

    def create_conversation(self, title: str = "Nueva conversación") -> Conversation:
        conversation = Conversation(id=str(uuid4()), title=title.strip() or "Nueva conversación")
        self._memory.save_conversation(conversation)
        return conversation

    def list_conversations(self, limit: int = 100) -> Sequence[Conversation]:
        return self._memory.list_conversations(limit)

    def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        return self._memory.list_messages(conversation_id)

    @staticmethod
    def create_operation_id() -> str:
        return str(uuid4())

    async def stream_chat(
        self,
        *,
        operation_id: str,
        conversation_id: str,
        model: ModelId,
        prompt: str,
        options: ChatOptions | None = None,
        keep_alive_seconds: float | None = 600.0,
        think: bool | Literal["low", "medium", "high"] | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("El mensaje no puede estar vacío.")
        conversation = self._memory.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeRequestError(f"La conversación {conversation_id!r} no existe.")

        now = datetime.now(UTC)
        user_message = ConversationMessage(
            id=str(uuid4()),
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=normalized_prompt,
            created_at=now,
            metadata={"model": model.name, "operation_id": operation_id},
        )
        self._memory.add_message(user_message)
        conversation.updated_at = now
        if conversation.title == "Nueva conversación":
            conversation.title = normalized_prompt[:60]
        self._memory.save_conversation(conversation)

        history = self._memory.list_messages(conversation_id)
        chat_input = ChatInput(
            messages=tuple(
                ChatMessage(role=message.role, content=message.content) for message in history
            ),
            options=options or ChatOptions(),
            keep_alive_seconds=keep_alive_seconds,
            think=think,
        )
        request = InferenceRequest(
            operation_id=operation_id,
            model=model,
            inputs=chat_input.model_dump(mode="json"),
        )
        await self._save_execution(
            operation_id=operation_id,
            conversation_id=conversation_id,
            model=model,
            prompt=normalized_prompt,
            chat_input=chat_input,
            status=ExecutionStatus.RUNNING,
            started_at=now,
        )
        response_parts: list[str] = []
        metrics: dict[str, object] = {}
        cancelled = False
        completed = False
        failed_message: str | None = None
        gate = self._model_gates.setdefault(model.key, asyncio.Lock())
        await gate.acquire()
        self._begin_operation(model)
        try:
            implicit_policy = await self._prepare_inference_residency(
                model,
                keep_alive_seconds,
            )
            async for event in self._run_with_residency(request, implicit_policy):
                if event.kind is RuntimeEventKind.TEXT_DELTA:
                    text = event.payload.get("text")
                    if isinstance(text, str):
                        response_parts.append(text)
                elif event.kind is RuntimeEventKind.METRICS:
                    metrics.update(event.payload)
                    if self._metrics_sink is not None:
                        self._metrics_sink.record_inference(
                            InferenceTelemetry(
                                operation_id=operation_id,
                                model=model,
                                input_tokens=_optional_int(event.payload.get("prompt_eval_count")),
                                output_tokens=_optional_int(event.payload.get("eval_count")),
                                total_duration_ns=_optional_int(
                                    event.payload.get("total_duration")
                                ),
                                load_duration_ns=_optional_int(event.payload.get("load_duration")),
                                prompt_duration_ns=_optional_int(
                                    event.payload.get("prompt_eval_duration")
                                ),
                                generation_duration_ns=_optional_int(
                                    event.payload.get("eval_duration")
                                ),
                                done_reason=_optional_str(event.payload.get("done_reason")),
                            )
                        )
                elif event.kind is RuntimeEventKind.CANCELLED:
                    cancelled = True
                elif event.kind is RuntimeEventKind.COMPLETED:
                    completed = True
                elif event.kind is RuntimeEventKind.ERROR:
                    failed_message = _optional_str(event.payload.get("message")) or "Runtime error"
                yield event
        except Exception as error:
            await self._save_execution(
                operation_id=operation_id,
                conversation_id=conversation_id,
                model=model,
                prompt=normalized_prompt,
                chat_input=chat_input,
                status=ExecutionStatus.FAILED,
                started_at=now,
                metrics=metrics,
                response="".join(response_parts),
                error=str(error),
            )
            raise
        finally:
            self._end_operation(model)
            gate.release()

        response = "".join(response_parts)
        if response and (completed or cancelled):
            assistant_message = ConversationMessage(
                id=str(uuid4()),
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=response,
                metadata={
                    "model": model.name,
                    "operation_id": operation_id,
                    "cancelled": cancelled,
                    "metrics": metrics,
                },
            )
            self._memory.add_message(assistant_message)
            conversation.updated_at = assistant_message.created_at
            self._memory.save_conversation(conversation)
        status = (
            ExecutionStatus.FAILED
            if failed_message
            else ExecutionStatus.CANCELLED
            if cancelled
            else ExecutionStatus.COMPLETED
            if completed
            else ExecutionStatus.FAILED
        )
        await self._save_execution(
            operation_id=operation_id,
            conversation_id=conversation_id,
            model=model,
            prompt=normalized_prompt,
            chat_input=chat_input,
            status=status,
            started_at=now,
            metrics=metrics,
            response=response,
            error=failed_message if failed_message else None,
        )

    async def _save_execution(
        self,
        *,
        operation_id: str,
        conversation_id: str,
        model: ModelId,
        prompt: str,
        chat_input: ChatInput,
        status: ExecutionStatus,
        started_at: datetime,
        metrics: dict[str, object] | None = None,
        response: str = "",
        error: str | None = None,
    ) -> None:
        if self._execution_history is None:
            return
        await self._execution_history.save_execution(
            ExecutionRecord(
                operation_id=operation_id,
                suite="llm",
                operation_type="chat",
                status=status,
                runtime=model.runtime,
                model_key=model.key,
                started_at=started_at,
                finished_at=datetime.now(UTC) if status is not ExecutionStatus.RUNNING else None,
                request_metadata={
                    "conversation_id": conversation_id,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "options": chat_input.options.model_dump(mode="json"),
                    "keep_alive_seconds": chat_input.keep_alive_seconds,
                    "thinking_requested": chat_input.think,
                },
                result_metadata={
                    "response_sha256": (
                        hashlib.sha256(response.encode("utf-8")).hexdigest()
                        if response
                        else None
                    ),
                    "response_characters": len(response),
                    "metrics": metrics or {},
                },
                error_message=error,
            )
        )

    async def cancel(self, operation_id: str) -> None:
        await self._runtime.cancel(operation_id)

    async def wait_until_idle(self, model: ModelId) -> None:
        if self._active_operations.get(model.key, 0) == 0:
            return
        event = self._idle_events.setdefault(model.key, asyncio.Event())
        await event.wait()

    async def active_model_state(self) -> ModelState | None:
        """Return the first Ollama model currently resident in RAM or GPU."""
        for descriptor in await self._runtime.list_models():
            state = await self._runtime.state(descriptor.id)
            if state.loaded_in_ram or state.loaded_in_gpu:
                return state
        return None

    def load_policy(self, model: ModelId) -> LoadPolicy:
        return self._load_policies.get(model.key, LoadPolicy())

    async def reserve_model(self, model: ModelId) -> None:
        gate = self._model_gates.setdefault(model.key, asyncio.Lock())
        await gate.acquire()

    def release_model_reservation(self, model: ModelId) -> None:
        gate = self._model_gates.get(model.key)
        if gate is not None and gate.locked():
            gate.release()

    async def move_model_to_ram(self, model: ModelId) -> tuple[ModelState, LoadPolicy]:
        await self.wait_until_idle(model)
        policy = self._load_policies.get(model.key, LoadPolicy())
        state = await self._runtime.unload(model, UnloadTarget.DEVICE)
        return state, policy

    async def restore_model_to_device(
        self,
        model: ModelId,
        policy: LoadPolicy,
    ) -> ModelState:
        restored_policy = policy.model_copy(update={"device": ComputeDevice.GPU})
        state = await self._runtime.load(model, restored_policy)
        self._load_policies[model.key] = restored_policy
        return state

    def _begin_operation(self, model: ModelId) -> None:
        self._active_operations[model.key] = self._active_operations.get(model.key, 0) + 1
        event = self._idle_events.setdefault(model.key, asyncio.Event())
        event.clear()

    def _end_operation(self, model: ModelId) -> None:
        remaining = max(self._active_operations.get(model.key, 1) - 1, 0)
        if remaining:
            self._active_operations[model.key] = remaining
            return
        self._active_operations.pop(model.key, None)
        self._idle_events.setdefault(model.key, asyncio.Event()).set()

    async def _prepare_inference_residency(
        self,
        model: ModelId,
        keep_alive_seconds: float | None,
    ) -> LoadPolicy | None:
        if self._residency_policy is None:
            return None
        current = await self._runtime.state(model)
        if current.loaded_in_ram or current.loaded_in_gpu:
            self._residency_policy.model_used(model)
            return None
        idle_timeout = keep_alive_seconds
        if idle_timeout is not None and idle_timeout <= 0:
            idle_timeout = 0.001
        policy = LoadPolicy(idle_timeout_seconds=idle_timeout)
        descriptor = self._catalog.get(model)
        await self._residency_policy.before_load(
            model,
            policy,
            descriptor.size_bytes if descriptor else None,
        )
        return policy

    async def _run_with_residency(
        self,
        request: InferenceRequest,
        implicit_policy: LoadPolicy | None,
    ) -> AsyncIterator[RuntimeEvent]:
        try:
            async for event in self._runtime.run(request):
                yield event
        finally:
            if implicit_policy is not None and self._residency_policy is not None:
                try:
                    state = await self._runtime.state(request.model)
                except Exception:
                    self._residency_policy.model_load_failed(request.model)
                else:
                    if state.loaded_in_ram or state.loaded_in_gpu:
                        self._residency_policy.model_loaded(state, implicit_policy)
                        self._load_policies[request.model.key] = implicit_policy
                    else:
                        self._residency_policy.model_load_failed(request.model)


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None

"""Use cases for the LLM suite, independent of any runtime SDK or UI toolkit."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from aiopenstudio.core.contracts import (
    ChatInput,
    ChatMessage,
    ChatOptions,
    Conversation,
    ConversationMemory,
    ConversationMessage,
    InferenceRequest,
    LoadPolicy,
    MessageRole,
    ModelCatalog,
    ModelDescriptor,
    ModelId,
    ModelRuntime,
    ModelState,
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
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._memory = memory

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
        return await self._runtime.load(model, policy)

    async def unload_model(self, model: ModelId) -> ModelState:
        return await self._runtime.unload(model, UnloadTarget.ALL)

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

        response_parts: list[str] = []
        metrics: dict[str, object] = {}
        cancelled = False
        completed = False
        async for event in self._runtime.run(request):
            if event.kind is RuntimeEventKind.TEXT_DELTA:
                text = event.payload.get("text")
                if isinstance(text, str):
                    response_parts.append(text)
            elif event.kind is RuntimeEventKind.METRICS:
                metrics.update(event.payload)
            elif event.kind is RuntimeEventKind.CANCELLED:
                cancelled = True
            elif event.kind is RuntimeEventKind.COMPLETED:
                completed = True
            yield event

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

    async def cancel(self, operation_id: str) -> None:
        await self._runtime.cancel(operation_id)

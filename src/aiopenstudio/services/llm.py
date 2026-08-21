"""Use cases for the LLM suite, independent of any runtime SDK or UI toolkit."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from aiopenstudio.core.contracts import (
    ChatInput,
    ChatOptions,
    ComputeDevice,
    ContextInspection,
    ContextOverflowPolicy,
    ContextSendPolicy,
    Conversation,
    ConversationContextItem,
    ConversationMemory,
    ConversationMessage,
    ConversationSummary,
    ConversationTitleOrigin,
    ExecutionHistory,
    ExecutionRecord,
    ExecutionStatus,
    InferenceMetricsSink,
    InferenceRequest,
    InferenceTelemetry,
    LoadPolicy,
    MessageRole,
    MessageStatus,
    ModelCatalog,
    ModelChatCapabilities,
    ModelDescriptor,
    ModelId,
    ModelRuntime,
    ModelState,
    PreparedContextBatch,
    PromptAssembly,
    ResidencyPolicy,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeHealth,
    StructuredOutputMode,
    StructuredOutputSpec,
    UnloadTarget,
)
from aiopenstudio.core.errors import RuntimeRequestError
from aiopenstudio.services.llm_context import LLMContextService
from aiopenstudio.services.llm_prompt import PromptAssembler
from aiopenstudio.services.structured_output import validate_structured_response


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
        context_service: LLMContextService | None = None,
        prompt_assembler: PromptAssembler | None = None,
    ) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._memory = memory
        self._metrics_sink = metrics_sink
        self._residency_policy = residency_policy
        self._execution_history = execution_history
        self._context_service = context_service
        self._prompt_assembler = prompt_assembler or PromptAssembler()
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

    def create_conversation(self, title: str | None = None) -> Conversation:
        normalized_title = (title or "").strip()
        conversation = Conversation(
            id=str(uuid4()),
            title=normalized_title or "Nueva conversación",
            title_origin=(
                ConversationTitleOrigin.MANUAL
                if normalized_title
                else ConversationTitleOrigin.AUTOMATIC
            ),
        )
        self._memory.save_conversation(conversation)
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self._memory.get_conversation(conversation_id)

    def open_or_create_conversation(self) -> Conversation:
        existing = self._memory.list_conversations(limit=1)
        return existing[0] if existing else self.create_conversation()

    def list_conversations(
        self,
        limit: int = 100,
        *,
        include_archived: bool = False,
        query: str | None = None,
    ) -> Sequence[Conversation]:
        return self._memory.list_conversations(
            limit,
            include_archived=include_archived,
            query=query,
        )

    def rename_conversation(self, conversation_id: str, title: str) -> Conversation:
        conversation = self._require_conversation(conversation_id)
        normalized = " ".join(title.split())
        if not normalized:
            raise ValueError("El título no puede estar vacío.")
        conversation.title = normalized[:200]
        conversation.title_origin = ConversationTitleOrigin.MANUAL
        conversation.updated_at = datetime.now(UTC)
        self._memory.save_conversation(conversation)
        return conversation

    def archive_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._require_conversation(conversation_id)
        now = datetime.now(UTC)
        conversation.archived_at = now
        conversation.updated_at = now
        self._memory.save_conversation(conversation)
        return conversation

    def restore_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._require_conversation(conversation_id)
        conversation.archived_at = None
        conversation.updated_at = datetime.now(UTC)
        self._memory.save_conversation(conversation)
        return conversation

    def delete_conversation(self, conversation_id: str) -> bool:
        return self._memory.delete_conversation(conversation_id)

    async def delete_conversation_with_context(self, conversation_id: str) -> bool:
        """Delete persisted memory plus only the private context copies owned by the app."""
        self._require_conversation(conversation_id)
        if self._context_service is not None:
            await self._context_service.purge_conversation(conversation_id)
        return await asyncio.to_thread(self._memory.delete_conversation, conversation_id)

    async def export_conversation(
        self,
        conversation_id: str,
        destination: Path,
        *,
        as_json: bool = False,
    ) -> Path:
        """Export messages without copying attachments or hidden reasoning traces."""
        return await asyncio.to_thread(
            self._export_conversation_blocking,
            conversation_id,
            destination,
            as_json,
        )

    def chat_capabilities(self, model: ModelId) -> ModelChatCapabilities:
        descriptor = self._catalog.get(model)
        if descriptor is None:
            return ModelChatCapabilities()
        raw = descriptor.metadata.get("chat_capabilities")
        return ModelChatCapabilities.model_validate(raw or {})

    def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        return self._memory.list_messages(conversation_id)

    def list_summaries(self, conversation_id: str) -> Sequence[ConversationSummary]:
        return self._memory.list_summaries(conversation_id)

    def create_summary(
        self,
        conversation_id: str,
        content: str,
        *,
        through_message_id: str,
        model: ModelId | None = None,
        protected_facts: Sequence[str] = (),
    ) -> ConversationSummary:
        self._require_conversation(conversation_id)
        normalized = content.strip()
        if not normalized:
            raise ValueError("El resumen no puede estar vacío.")
        messages = [
            message
            for message in self._memory.list_messages(conversation_id)
            if message.status is MessageStatus.COMPLETE
        ]
        try:
            last_index = next(
                index
                for index, message in enumerate(messages)
                if message.id == through_message_id
            )
        except StopIteration as error:
            raise ValueError(
                "El mensaje final del resumen no pertenece a la conversación."
            ) from error
        previous = list(self._memory.list_summaries(conversation_id))
        for summary in previous:
            if summary.active:
                self._memory.save_summary(summary.model_copy(update={"active": False}))
        version = max((summary.version for summary in previous), default=0) + 1
        source_payload = "\n".join(message.content for message in messages[: last_index + 1])
        summary = ConversationSummary(
            id=str(uuid4()),
            conversation_id=conversation_id,
            content=normalized,
            source_message_count=last_index + 1,
            version=version,
            first_message_id=messages[0].id if messages else None,
            last_message_id=through_message_id,
            model_key=model.key if model is not None else None,
            prompt_sha256=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
            protected_facts=tuple(
                fact.strip() for fact in protected_facts if fact.strip()
            ),
        )
        self._memory.save_summary(summary)
        return summary

    def discard_summary(self, conversation_id: str, summary_id: str) -> ConversationSummary:
        summary = next(
            (
                candidate
                for candidate in self._memory.list_summaries(conversation_id)
                if candidate.id == summary_id
            ),
            None,
        )
        if summary is None:
            raise ValueError("El resumen no pertenece a la conversación.")
        discarded = summary.model_copy(update={"active": False})
        self._memory.save_summary(discarded)
        return discarded

    async def add_context(
        self,
        conversation_id: str,
        source_path: Path,
        *,
        send_policy: ContextSendPolicy = ContextSendPolicy.ONCE,
        enabled: bool = False,
        snapshot: bool = False,
    ) -> ConversationContextItem:
        return await self._require_context_service().add(
            conversation_id,
            source_path,
            send_policy=send_policy,
            enabled=enabled,
            snapshot=snapshot,
        )

    async def list_context(self, conversation_id: str) -> tuple[ConversationContextItem, ...]:
        return await self._require_context_service().list_items(conversation_id)

    async def inspect_context(
        self,
        conversation_id: str,
        item_id: str,
    ) -> ContextInspection:
        return await self._require_context_service().inspect(item_id, conversation_id)

    async def set_context_enabled(
        self,
        conversation_id: str,
        item_id: str,
        enabled: bool,
    ) -> ConversationContextItem:
        return await self._require_context_service().set_enabled(
            item_id,
            conversation_id,
            enabled,
        )

    async def set_context_send_policy(
        self,
        conversation_id: str,
        item_id: str,
        policy: ContextSendPolicy,
    ) -> ConversationContextItem:
        return await self._require_context_service().set_send_policy(
            item_id,
            conversation_id,
            policy,
        )

    async def reorder_context(
        self,
        conversation_id: str,
        ordered_ids: Sequence[str],
    ) -> None:
        await self._require_context_service().reorder(conversation_id, ordered_ids)

    async def accept_context_changes(
        self,
        conversation_id: str,
        item_id: str,
    ) -> ConversationContextItem:
        return await self._require_context_service().accept_changes(
            item_id,
            conversation_id,
        )

    async def remove_context(self, conversation_id: str, item_id: str) -> bool:
        return await self._require_context_service().remove(item_id, conversation_id)

    async def remember_context_queue(self, conversation_id: str, enabled: bool) -> None:
        await self._require_context_service().set_remember_queue(conversation_id, enabled)

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
        system_prompt: str | None = None,
        overflow_policy: ContextOverflowPolicy = ContextOverflowPolicy.REJECT,
        keep_alive_seconds: float | None = 600.0,
        think: bool | Literal["low", "medium", "high"] | None = None,
        output: StructuredOutputSpec | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("El mensaje no puede estar vacío.")
        conversation = self._memory.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeRequestError(f"La conversación {conversation_id!r} no existe.")
        if conversation.archived_at is not None:
            raise RuntimeRequestError("Restaura la conversación archivada antes de continuarla.")

        selected_options = options or ChatOptions()
        capabilities = self.chat_capabilities(model)
        selected_output = output or StructuredOutputSpec()
        if (
            selected_output.mode is not StructuredOutputMode.TEXT
            and not capabilities.supports_structured_output
        ):
            raise RuntimeRequestError(
                "El runtime no declara salida estructurada para el tag seleccionado."
            )
        contexts = (
            await self._context_service.prepare(conversation_id, capabilities)
            if self._context_service is not None
            else PreparedContextBatch()
        )
        history = self._memory.list_messages(conversation_id)
        active_summary = next(
            (
                summary
                for summary in self._memory.list_summaries(conversation_id)
                if summary.active
            ),
            None,
        )
        assembly = self._prompt_assembler.assemble(
            prompt=normalized_prompt,
            history=history,
            contexts=contexts,
            capabilities=capabilities,
            options=selected_options,
            system_prompt=system_prompt,
            summary=active_summary,
            overflow_policy=overflow_policy,
            keep_alive_seconds=keep_alive_seconds,
            think=think,
            output=selected_output,
        )

        yield RuntimeEvent(
            operation_id=operation_id,
            kind=RuntimeEventKind.PREFLIGHT,
            payload={
                "token_budget": assembly.budget.model_dump(mode="json"),
                "context_item_ids": list(assembly.included_context_ids),
                "model_digest": capabilities.model_digest,
                "output_mode": selected_output.mode.value,
            },
        )

        now = datetime.now(UTC)
        user_message = ConversationMessage(
            id=str(uuid4()),
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=normalized_prompt,
            model_key=model.key,
            operation_id=operation_id,
            created_at=now,
            metadata={"model": model.name, "operation_id": operation_id},
        )
        self._memory.add_message(user_message)
        conversation.updated_at = now
        conversation.last_model_key = model.key
        if (
            conversation.title_origin is ConversationTitleOrigin.AUTOMATIC
            and conversation.title == "Nueva conversación"
        ):
            conversation.title = " ".join(normalized_prompt.split())[:60]
        self._memory.save_conversation(conversation)

        chat_input = assembly.chat_input
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
            assembly=assembly,
            status=ExecutionStatus.RUNNING,
            started_at=now,
        )
        response_parts: list[str] = []
        metrics: dict[str, object] = {}
        cancelled = False
        completed = False
        failed_message: str | None = None
        once_consumed = False
        gate = self._model_gates.setdefault(model.key, asyncio.Lock())
        await gate.acquire()
        self._begin_operation(model)
        try:
            implicit_policy = await self._prepare_inference_residency(
                model,
                keep_alive_seconds,
            )
            async for event in self._run_with_residency(request, implicit_policy):
                if (
                    event.kind
                    in {
                        RuntimeEventKind.TEXT_DELTA,
                        RuntimeEventKind.THINKING_DELTA,
                        RuntimeEventKind.METRICS,
                        RuntimeEventKind.COMPLETED,
                    }
                    and self._context_service is not None
                    and assembly.consume_once_context_ids
                    and not once_consumed
                ):
                    await self._context_service.consume_once(
                        conversation_id,
                        assembly.consume_once_context_ids,
                    )
                    once_consumed = True
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
                if event.kind not in {
                    RuntimeEventKind.COMPLETED,
                    RuntimeEventKind.CANCELLED,
                    RuntimeEventKind.ERROR,
                }:
                    yield event
        except Exception as error:
            await self._save_execution(
                operation_id=operation_id,
                conversation_id=conversation_id,
                model=model,
                prompt=normalized_prompt,
                chat_input=chat_input,
                assembly=assembly,
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
        if response and completed and not cancelled and failed_message is None:
            try:
                validate_structured_response(response, selected_output)
            except ValueError as error:
                failed_message = str(error)
                completed = False
        if response and (completed or cancelled or failed_message is not None):
            assistant_message = ConversationMessage(
                id=str(uuid4()),
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=response,
                status=(
                    MessageStatus.CANCELLED
                    if cancelled
                    else MessageStatus.FAILED
                    if failed_message
                    else MessageStatus.COMPLETE
                ),
                model_key=model.key,
                operation_id=operation_id,
                input_tokens=_optional_int(metrics.get("prompt_eval_count")),
                output_tokens=_optional_int(metrics.get("eval_count")),
                metadata={
                    "model": model.name,
                    "operation_id": operation_id,
                    "cancelled": cancelled,
                    "output_mode": selected_output.mode.value,
                    "structured_output_valid": failed_message is None,
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
            assembly=assembly,
            status=status,
            started_at=now,
            metrics=metrics,
            response=response,
            error=failed_message if failed_message else None,
        )
        terminal_kind = (
            RuntimeEventKind.ERROR
            if failed_message or not (completed or cancelled)
            else RuntimeEventKind.CANCELLED
            if cancelled
            else RuntimeEventKind.COMPLETED
        )
        terminal_payload = {"message": failed_message} if failed_message else {}
        yield RuntimeEvent(
            operation_id=operation_id,
            kind=terminal_kind,
            payload=terminal_payload,
        )

    def _export_conversation_blocking(
        self,
        conversation_id: str,
        destination: Path,
        as_json: bool,
    ) -> Path:
        conversation = self._require_conversation(conversation_id)
        messages = tuple(self._memory.list_messages(conversation_id))
        summaries = tuple(self._memory.list_summaries(conversation_id))
        target = destination.expanduser().resolve()
        if not target.parent.is_dir():
            raise ValueError("La carpeta de exportación no existe.")
        if as_json:
            payload = {
                "schema_version": 1,
                "conversation": conversation.model_dump(mode="json"),
                "messages": [message.model_dump(mode="json") for message in messages],
                "summaries": [summary.model_dump(mode="json") for summary in summaries],
                "attachments_included": False,
                "thinking_traces_included": False,
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        else:
            content = self._markdown_export(conversation, messages)
        temporary = target.with_name(target.name + ".partial")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        return target

    @staticmethod
    def _markdown_export(
        conversation: Conversation,
        messages: Sequence[ConversationMessage],
    ) -> str:
        lines = [f"# {conversation.title}", ""]
        labels = {
            MessageRole.SYSTEM: "Sistema",
            MessageRole.USER: "Usuario",
            MessageRole.ASSISTANT: "Asistente",
            MessageRole.TOOL: "Herramienta",
        }
        for message in messages:
            status = (
                ""
                if message.status is MessageStatus.COMPLETE
                else f" · {message.status.value}"
            )
            lines.extend(
                [
                    f"## {labels[message.role]}{status}",
                    "",
                    message.content,
                    "",
                ]
            )
        lines.extend(
            [
                "---",
                "Exportado por AIOpenStudio. No incluye adjuntos ni trazas de razonamiento.",
                "",
            ]
        )
        return "\n".join(lines)

    def _require_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._memory.get_conversation(conversation_id)
        if conversation is None:
            raise RuntimeRequestError(f"La conversación {conversation_id!r} no existe.")
        return conversation

    async def _save_execution(
        self,
        *,
        operation_id: str,
        conversation_id: str,
        model: ModelId,
        prompt: str,
        chat_input: ChatInput,
        assembly: PromptAssembly,
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
                    "output_mode": chat_input.output.mode.value,
                    "json_schema_sha256": (
                        hashlib.sha256(
                            json.dumps(
                                chat_input.output.json_schema,
                                ensure_ascii=False,
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()
                        if chat_input.output.json_schema is not None
                        else None
                    ),
                    "context_item_ids": list(assembly.included_context_ids),
                    "token_budget": assembly.budget.model_dump(mode="json"),
                    "system_prompt_sha256": (
                        hashlib.sha256(chat_input.system_prompt.encode("utf-8")).hexdigest()
                        if chat_input.system_prompt
                        else None
                    ),
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

    def _require_context_service(self) -> LLMContextService:
        if self._context_service is None:
            raise RuntimeRequestError("El servicio de contexto LLM no está configurado.")
        return self._context_service

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

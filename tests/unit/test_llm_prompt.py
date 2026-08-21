from datetime import UTC, datetime

import pytest

from aiopenstudio.core.contracts import (
    ChatOptions,
    ContextKind,
    ContextOverflowPolicy,
    ContextSendPolicy,
    ConversationMessage,
    ConversationSummary,
    MessageRole,
    ModelChatCapabilities,
    PreparedContext,
    PreparedContextBatch,
)
from aiopenstudio.core.errors import ResourceLimitError, RuntimeRequestError
from aiopenstudio.services import PromptAssembler


def _message(identifier: str, role: MessageRole, content: str) -> ConversationMessage:
    return ConversationMessage(
        id=identifier,
        conversation_id="conversation",
        role=role,
        content=content,
        created_at=datetime.now(UTC),
    )


def test_assembly_orders_summary_protected_system_context_and_current_prompt() -> None:
    history = [
        _message("system", MessageRole.SYSTEM, "Regla original del sistema"),
        _message("user-1", MessageRole.USER, "Pregunta anterior"),
        _message("assistant-1", MessageRole.ASSISTANT, "Respuesta anterior"),
        _message("user-2", MessageRole.USER, "Pregunta reciente"),
    ]
    summary = ConversationSummary(
        id="summary",
        conversation_id="conversation",
        content="La primera pregunta ya fue resuelta.",
        source_message_count=3,
        version=2,
        last_message_id="assistant-1",
        protected_facts=("No descargar modelos",),
    )
    external = PreparedContext(
        item_id="context",
        kind=ContextKind.TEXT_FILE,
        display_name="notes.md",
        send_policy=ContextSendPolicy.ONCE,
        sha256="a" * 64,
        text="<AIOPENSTUDIO_EXTERNAL>datos</AIOPENSTUDIO_EXTERNAL>",
        size_bytes=10,
        estimated_tokens=20,
    )

    assembly = PromptAssembler(default_context_tokens=2_048).assemble(
        prompt="Solicitud presente",
        history=history,
        contexts=PreparedContextBatch(items=(external,), total_bytes=10, estimated_tokens=20),
        capabilities=ModelChatCapabilities(),
        options=ChatOptions(max_new_tokens=128),
        system_prompt="Responde en español.",
        summary=summary,
        overflow_policy=ContextOverflowPolicy.REJECT,
        keep_alive_seconds=60,
        think=False,
    )

    messages = assembly.chat_input.messages
    assert messages[0].role is MessageRole.SYSTEM
    assert "Resumen versionado" in messages[0].content
    assert messages[1].content == "Regla original del sistema"
    assert messages[2].content == "Pregunta reciente"
    assert "datos" in messages[-1].content
    assert messages[-1].content.endswith("Solicitud presente")
    assert "potencialmente no confiables" in (assembly.chat_input.system_prompt or "")
    assert "No descargar modelos" in (assembly.chat_input.system_prompt or "")
    assert assembly.consume_once_context_ids == ("context",)
    assert assembly.budget.used_summary_version == 2


def test_budget_rejects_before_runtime_and_can_truncate_oldest_exchange() -> None:
    history = [
        _message("user-1", MessageRole.USER, "u" * 160),
        _message("assistant-1", MessageRole.ASSISTANT, "a" * 160),
        _message("user-2", MessageRole.USER, "reciente"),
    ]
    assembler = PromptAssembler(default_context_tokens=128, default_max_new_tokens=32)
    arguments = {
        "prompt": "actual",
        "history": history,
        "contexts": PreparedContextBatch(),
        "capabilities": ModelChatCapabilities(),
        "options": ChatOptions(max_new_tokens=32),
        "system_prompt": None,
        "summary": None,
        "keep_alive_seconds": 60,
        "think": None,
    }

    with pytest.raises(ResourceLimitError, match="sólo hay"):
        assembler.assemble(**arguments, overflow_policy=ContextOverflowPolicy.REJECT)  # type: ignore[arg-type]

    truncated = assembler.assemble(
        **arguments,  # type: ignore[arg-type]
        overflow_policy=ContextOverflowPolicy.TRUNCATE_OLDEST,
    )
    assert truncated.budget.truncated_message_count == 2
    assert truncated.budget.fits
    assert [message.content for message in truncated.chat_input.messages] == [
        "reciente",
        "SOLICITUD ACTUAL DEL USUARIO:\nactual",
    ]


def test_model_maximum_caps_requested_window_and_output_reserve_is_validated() -> None:
    assembler = PromptAssembler(default_context_tokens=4_096)
    capabilities = ModelChatCapabilities(max_context_tokens=2_048)

    assembly = assembler.assemble(
        prompt="Hola",
        history=(),
        contexts=PreparedContextBatch(),
        capabilities=capabilities,
        options=ChatOptions(context_length=8_192, max_new_tokens=256),
        system_prompt=None,
        summary=None,
        overflow_policy=ContextOverflowPolicy.REJECT,
        keep_alive_seconds=60,
        think=None,
    )
    assert assembly.budget.context_window == 2_048

    with pytest.raises(ResourceLimitError, match="menor que la ventana"):
        assembler.assemble(
            prompt="Hola",
            history=(),
            contexts=PreparedContextBatch(),
            capabilities=ModelChatCapabilities(),
            options=ChatOptions(context_length=128, max_new_tokens=128),
            system_prompt=None,
            summary=None,
            overflow_policy=ContextOverflowPolicy.REJECT,
            keep_alive_seconds=60,
            think=None,
        )


def test_summary_with_missing_boundary_is_rejected() -> None:
    summary = ConversationSummary(
        id="summary",
        conversation_id="conversation",
        content="Resumen",
        source_message_count=1,
        last_message_id="missing",
    )
    with pytest.raises(RuntimeRequestError, match="ya no existe"):
        PromptAssembler().assemble(
            prompt="Hola",
            history=(_message("present", MessageRole.USER, "Anterior"),),
            contexts=PreparedContextBatch(),
            capabilities=ModelChatCapabilities(),
            options=ChatOptions(),
            system_prompt=None,
            summary=summary,
            overflow_policy=ContextOverflowPolicy.REJECT,
            keep_alive_seconds=60,
            think=None,
        )

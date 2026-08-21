import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    ContextKind,
    ContextSendPolicy,
    Conversation,
    ConversationContextItem,
    ConversationMessage,
    ConversationSummary,
    ConversationTitleOrigin,
    MessageRole,
    MessageStatus,
    ModelDescriptor,
    ModelId,
)
from aiopenstudio.infrastructure.database import SQLiteCapabilityError, SQLiteStore


def test_sqlite_catalog_and_full_text_memory(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime" / "memory.sqlite3"
    store = SQLiteStore(database_path)

    capabilities = store.initialize()

    assert capabilities.fts5_available
    assert database_path.is_file()

    descriptor = ModelDescriptor(
        id=ModelId(runtime="ollama", name="qwen"),
        display_name="Qwen local",
        weights_path=tmp_path / "weights" / "qwen.gguf",
        capabilities=frozenset({"chat", "text-generation"}),
        size_bytes=1234,
        installed=True,
        metadata={"license": "Apache-2.0"},
    )
    store.save(descriptor)

    restored = store.get(descriptor.id)
    assert restored == descriptor
    assert store.list(runtime="ollama") == [descriptor]

    conversation = Conversation(id="conversation-1", title="Memory planning")
    store.save_conversation(conversation)
    store.add_message(
        ConversationMessage(
            id="message-1",
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="Keep enough GPU memory available for the desktop.",
        )
    )
    store.save_summary(
        ConversationSummary(
            id="summary-1",
            conversation_id=conversation.id,
            content="The GPU memory reserve is important.",
            source_message_count=1,
        )
    )

    hits = store.search("GPU memory")

    assert {hit.kind for hit in hits} == {"message", "summary"}
    assert all(hit.conversation_id == conversation.id for hit in hits)


def test_database_contains_weight_path_not_weight_bytes(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    weights_path = tmp_path / "outside" / "model.safetensors"
    store = SQLiteStore(database_path)
    store.initialize()
    store.save(
        ModelDescriptor(
            id=ModelId(runtime="whisper", name="small"),
            display_name="Whisper small",
            weights_path=weights_path,
        )
    )

    with sqlite3.connect(database_path) as connection:
        stored_path = connection.execute(
            "SELECT weights_path FROM model_references"
        ).fetchone()[0]

    assert stored_path == str(weights_path)


def test_schema_two_is_migrated_without_losing_conversations(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE conversation_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL, metadata_json TEXT NOT NULL
            );
            CREATE TABLE conversation_summaries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                content TEXT NOT NULL, source_message_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE conversation_search USING fts5(
                record_id UNINDEXED, conversation_id UNINDEXED,
                kind UNINDEXED, content
            );
            INSERT INTO conversations VALUES (
                'legacy', 'Conversación anterior',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            );
            PRAGMA user_version = 2;
            """
        )

    capabilities = SQLiteStore(database_path).initialize()
    restored = SQLiteStore(database_path).get_conversation("legacy")

    assert capabilities.schema_version == 3
    assert restored is not None
    assert restored.title == "Conversación anterior"
    assert restored.title_origin is ConversationTitleOrigin.AUTOMATIC


def test_conversation_metadata_context_archive_search_and_delete(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()
    now = datetime.now(UTC)
    conversation = Conversation(
        id="conversation-rich",
        title="Análisis persistente",
        title_origin=ConversationTitleOrigin.MANUAL,
        last_model_key="ollama:gemma4:",
        remember_context_queue=True,
    )
    store.save_conversation(conversation)
    store.add_message(
        ConversationMessage(
            id="message-cancelled",
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Respuesta parcial",
            status=MessageStatus.CANCELLED,
            model_key="ollama:gemma4:",
            operation_id="operation-cancelled",
            input_tokens=12,
            output_tokens=3,
        )
    )
    summary = ConversationSummary(
        id="summary-rich",
        conversation_id=conversation.id,
        content="Decisiones de la conversación.",
        source_message_count=1,
        version=2,
        model_key="ollama:gemma4:",
        prompt_sha256="a" * 64,
        protected_facts=("No descargar modelos",),
    )
    store.save_summary(summary)
    item = ConversationContextItem(
        id="context-1",
        conversation_id=conversation.id,
        kind=ContextKind.TEXT_FILE,
        source_path=tmp_path / "notes.md",
        display_name="notes.md",
        send_policy=ContextSendPolicy.EVERY_TURN,
        size_bytes=42,
        sha256="b" * 64,
        source_modified_at=now,
    )
    store.save_context_item(item)

    assert store.list_messages(conversation.id)[0].status is MessageStatus.CANCELLED
    assert store.list_summaries(conversation.id) == [summary]
    assert store.list_context_items(conversation.id) == [item]
    assert store.list_conversations(query="persistente") == [conversation]

    archived = conversation.model_copy(update={"archived_at": now, "updated_at": now})
    store.save_conversation(archived)
    assert store.list_conversations() == []
    assert store.list_conversations(include_archived=True) == [archived]

    assert store.delete_conversation(conversation.id)
    assert store.get_conversation(conversation.id) is None
    assert store.list_messages(conversation.id) == []
    assert store.list_context_items(conversation.id) == []
    assert store.search("persistente") == []


def test_vector_index_requires_explicit_enablement(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    store.initialize()

    with pytest.raises(SQLiteCapabilityError, match="disabled"):
        store.create_vector_index("conversation_vectors", 384)


def test_optional_vector_extension_can_create_index(tmp_path: Path) -> None:
    pytest.importorskip("sqlite_vec")
    store = SQLiteStore(tmp_path / "memory.sqlite3", enable_vectors=True)

    capabilities = store.initialize()
    store.create_vector_index("conversation_vectors", 384)

    assert capabilities.vector_available
    assert capabilities.vector_version is not None


@pytest.mark.parametrize("name", ["invalid-name", "1invalid", "unsafe;drop_table"])
def test_vector_index_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    store = SQLiteStore(tmp_path / "memory.sqlite3", enable_vectors=True)

    with pytest.raises(ValueError, match="identifiers"):
        store.create_vector_index(name, 384)

import sqlite3
from pathlib import Path

import pytest

from aiopenstudio.core.contracts import (
    Conversation,
    ConversationMessage,
    ConversationSummary,
    MessageRole,
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

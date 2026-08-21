"""Local SQLite catalog and conversation memory with FTS5 search."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from aiopenstudio.core.contracts.memory import (
    Conversation,
    ConversationContextItem,
    ConversationMessage,
    ConversationSummary,
    MemorySearchHit,
)
from aiopenstudio.core.contracts.models import ModelDescriptor, ModelId
from aiopenstudio.core.contracts.persistence import (
    ArtifactRecord,
    ExecutionRecord,
    PersistenceOutboxEntry,
    StoredConfiguration,
)


class SQLiteCapabilityError(RuntimeError):
    """Raised when an explicitly requested SQLite capability is unavailable."""


@dataclass(frozen=True, slots=True)
class SQLiteCapabilities:
    sqlite_version: str
    schema_version: int
    fts5_available: bool
    extension_loading_available: bool
    vector_available: bool
    vector_version: str | None = None


class _SQLiteVecModule(Protocol):
    def load(self, connection: sqlite3.Connection, /) -> None: ...


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS model_references (
    model_key TEXT PRIMARY KEY,
    runtime TEXT NOT NULL,
    name TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    weights_path TEXT,
    size_bytes INTEGER,
    checksum_sha256 TEXT,
    installed INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_references_runtime
ON model_references(runtime);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_origin TEXT NOT NULL DEFAULT 'automatic',
    archived_at TEXT,
    last_model_key TEXT,
    remember_context_queue INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_archive_updated
ON conversations(archived_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    model_key TEXT,
    operation_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
ON conversation_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    source_message_count INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    first_message_id TEXT,
    last_message_id TEXT,
    model_key TEXT,
    prompt_sha256 TEXT,
    protected_facts_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation
ON conversation_summaries(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_context_items (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    item_order INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    send_policy TEXT NOT NULL,
    storage_policy TEXT NOT NULL,
    snapshot_path TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    source_modified_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_items_conversation
ON conversation_context_items(conversation_id, item_order, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_search USING fts5(
    record_id UNINDEXED,
    conversation_id UNINDEXED,
    kind UNINDEXED,
    content,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS stored_configurations (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(namespace, key)
);

CREATE TABLE IF NOT EXISTS executions (
    operation_id TEXT PRIMARY KEY,
    suite TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT,
    model_key TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    request_metadata_json TEXT NOT NULL,
    result_metadata_json TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_executions_started
ON executions(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_executions_suite_status
ON executions(suite, status);

CREATE TABLE IF NOT EXISTS execution_artifacts (
    artifact_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES executions(operation_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_artifacts_operation
ON execution_artifacts(operation_id);

CREATE TABLE IF NOT EXISTS persistence_outbox (
    event_id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_persistence_outbox_created
ON persistence_outbox(created_at, event_id);
"""

_LATEST_SCHEMA_VERSION = 3

_CONVERSATION_COLUMNS_V3 = {
    "title_origin": "TEXT NOT NULL DEFAULT 'automatic'",
    "archived_at": "TEXT",
    "last_model_key": "TEXT",
    "remember_context_queue": "INTEGER NOT NULL DEFAULT 0",
}

_MESSAGE_COLUMNS_V3 = {
    "status": "TEXT NOT NULL DEFAULT 'complete'",
    "model_key": "TEXT",
    "operation_id": "TEXT",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
}

_SUMMARY_COLUMNS_V3 = {
    "version": "INTEGER NOT NULL DEFAULT 1",
    "first_message_id": "TEXT",
    "last_message_id": "TEXT",
    "model_key": "TEXT",
    "prompt_sha256": "TEXT",
    "protected_facts_json": "TEXT NOT NULL DEFAULT '[]'",
    "active": "INTEGER NOT NULL DEFAULT 1",
}


class SQLiteStore:
    """Persist local references and searchable conversation memory.

    Connections are short-lived so the store can safely be invoked by worker
    threads. Construction and import have no filesystem side effects;
    ``initialize`` creates the database explicitly.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5_000,
        enable_vectors: bool = False,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms
        self.enable_vectors = enable_vectors

    def initialize(self) -> SQLiteCapabilities:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            if not self._has_fts5(connection):
                raise SQLiteCapabilityError("The active SQLite library does not provide FTS5")
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > _LATEST_SCHEMA_VERSION:
                raise SQLiteCapabilityError(
                    "The local database was created by a newer AIOpenStudio version "
                    f"(schema {current_version})."
                )
            self._migrate_to_v3(connection)
            connection.executescript(_SCHEMA_SQL)
            if current_version < _LATEST_SCHEMA_VERSION:
                connection.execute(
                    "DELETE FROM conversation_search WHERE kind = 'conversation'"
                )
                connection.execute(
                    """
                    INSERT INTO conversation_search(record_id, conversation_id, kind, content)
                    SELECT id, id, 'conversation', title FROM conversations
                    """
                )
            connection.execute(f"PRAGMA user_version = {_LATEST_SCHEMA_VERSION}")
        return self.capabilities()

    def capabilities(self) -> SQLiteCapabilities:
        with self._connection(load_vectors=False) as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            fts5_available = self._has_fts5(connection)
            extension_loading = hasattr(connection, "enable_load_extension")
            vector_version: str | None = None
            try:
                self._load_vector_extension(connection)
                row = connection.execute("SELECT vec_version()").fetchone()
                vector_version = str(row[0]) if row else None
            except (ImportError, AttributeError, sqlite3.Error):
                vector_version = None
        return SQLiteCapabilities(
            sqlite_version=sqlite3.sqlite_version,
            schema_version=schema_version,
            fts5_available=fts5_available,
            extension_loading_available=extension_loading,
            vector_available=vector_version is not None,
            vector_version=vector_version,
        )

    @classmethod
    def _migrate_to_v3(cls, connection: sqlite3.Connection) -> None:
        """Add conversation fields without assuming which older local schema is present."""
        cls._add_columns(connection, "conversations", _CONVERSATION_COLUMNS_V3)
        cls._add_columns(connection, "conversation_messages", _MESSAGE_COLUMNS_V3)
        cls._add_columns(connection, "conversation_summaries", _SUMMARY_COLUMNS_V3)

    @staticmethod
    def _add_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if table_exists is None:
            return
        existing = {
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}')

    def create_vector_index(self, name: str, dimensions: int) -> None:
        """Create a vec0 index only after its embedding dimensions are known."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError("Vector index names must be simple SQL identifiers")
        if not 1 <= dimensions <= 65_536:
            raise ValueError("Vector dimensions must be between 1 and 65536")
        if not self.enable_vectors:
            raise SQLiteCapabilityError("Vector support is disabled in application settings")
        with self._connection() as connection:
            connection.execute(
                f'CREATE VIRTUAL TABLE IF NOT EXISTS "{name}" '
                f'USING vec0(embedding float[{dimensions}])'
            )

    def save(self, descriptor: ModelDescriptor) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO model_references (
                    model_key, runtime, name, variant, display_name,
                    capabilities_json, weights_path, size_bytes, checksum_sha256,
                    installed, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(model_key) DO UPDATE SET
                    display_name=excluded.display_name,
                    capabilities_json=excluded.capabilities_json,
                    weights_path=excluded.weights_path,
                    size_bytes=excluded.size_bytes,
                    checksum_sha256=excluded.checksum_sha256,
                    installed=excluded.installed,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    descriptor.id.key,
                    descriptor.id.runtime,
                    descriptor.id.name,
                    descriptor.id.variant or "",
                    descriptor.display_name,
                    json.dumps(sorted(descriptor.capabilities)),
                    str(descriptor.weights_path) if descriptor.weights_path else None,
                    descriptor.size_bytes,
                    descriptor.checksum_sha256,
                    int(descriptor.installed),
                    json.dumps(descriptor.metadata, ensure_ascii=False),
                ),
            )

    def get(self, model_id: ModelId) -> ModelDescriptor | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM model_references WHERE model_key = ?",
                (model_id.key,),
            ).fetchone()
        return self._model_from_row(row) if row is not None else None

    def list(self, runtime: str | None = None) -> Sequence[ModelDescriptor]:
        query = "SELECT * FROM model_references"
        parameters: tuple[str, ...] = ()
        if runtime is not None:
            query += " WHERE runtime = ?"
            parameters = (runtime,)
        query += " ORDER BY display_name COLLATE NOCASE"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._model_from_row(row) for row in rows]

    def remove(self, model_id: ModelId) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM model_references WHERE model_key = ?",
                (model_id.key,),
            )
        return cursor.rowcount > 0

    def save_conversation(self, conversation: Conversation) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations(
                    id, title, title_origin, archived_at, last_model_key,
                    remember_context_queue, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    title_origin=excluded.title_origin,
                    archived_at=excluded.archived_at,
                    last_model_key=excluded.last_model_key,
                    remember_context_queue=excluded.remember_context_queue,
                    updated_at=excluded.updated_at
                """,
                (
                    conversation.id,
                    conversation.title,
                    conversation.title_origin.value,
                    conversation.archived_at.isoformat() if conversation.archived_at else None,
                    conversation.last_model_key,
                    int(conversation.remember_context_queue),
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                ),
            )
            self._replace_search_record(
                connection,
                conversation.id,
                conversation.id,
                "conversation",
                conversation.title,
            )

    def add_message(self, message: ConversationMessage) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_messages(
                    id, conversation_id, role, content, status, model_key, operation_id,
                    input_tokens, output_tokens, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content=excluded.content,
                    status=excluded.status,
                    model_key=excluded.model_key,
                    operation_id=excluded.operation_id,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    metadata_json=excluded.metadata_json
                """,
                (
                    message.id,
                    message.conversation_id,
                    message.role.value,
                    message.content,
                    message.status.value,
                    message.model_key,
                    message.operation_id,
                    message.input_tokens,
                    message.output_tokens,
                    message.created_at.isoformat(),
                    json.dumps(message.metadata, ensure_ascii=False),
                ),
            )
            self._replace_search_record(
                connection,
                message.id,
                message.conversation_id,
                "message",
                message.content,
            )

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return self._conversation_from_row(row) if row is not None else None

    def list_conversations(
        self,
        limit: int = 100,
        *,
        include_archived: bool = False,
        query: str | None = None,
    ) -> Sequence[Conversation]:
        if not 1 <= limit <= 500:
            raise ValueError("Conversation limit must be between 1 and 500")
        clauses: list[str] = []
        parameters: list[object] = []
        if not include_archived:
            clauses.append("archived_at IS NULL")
        normalized_query = (query or "").strip()
        if normalized_query:
            clauses.append(
                "id IN (SELECT conversation_id FROM conversation_search "
                "WHERE conversation_search MATCH ?)"
            )
            parameters.append(f'"{normalized_query.replace(chr(34), chr(34) * 2)}"')
        statement = "SELECT * FROM conversations"
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        statement += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM conversation_search WHERE conversation_id = ?",
                (conversation_id,),
            )
            cursor = connection.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at, id
                """,
                (conversation_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def save_summary(self, summary: ConversationSummary) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_summaries(
                    id, conversation_id, content, source_message_count, version,
                    first_message_id, last_message_id, model_key, prompt_sha256,
                    protected_facts_json, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content=excluded.content,
                    source_message_count=excluded.source_message_count,
                    version=excluded.version,
                    first_message_id=excluded.first_message_id,
                    last_message_id=excluded.last_message_id,
                    model_key=excluded.model_key,
                    prompt_sha256=excluded.prompt_sha256,
                    protected_facts_json=excluded.protected_facts_json,
                    active=excluded.active
                """,
                (
                    summary.id,
                    summary.conversation_id,
                    summary.content,
                    summary.source_message_count,
                    summary.version,
                    summary.first_message_id,
                    summary.last_message_id,
                    summary.model_key,
                    summary.prompt_sha256,
                    json.dumps(summary.protected_facts, ensure_ascii=False),
                    int(summary.active),
                    summary.created_at.isoformat(),
                ),
            )
            self._replace_search_record(
                connection,
                summary.id,
                summary.conversation_id,
                "summary",
                summary.content,
            )

    def list_summaries(self, conversation_id: str) -> Sequence[ConversationSummary]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_summaries
                WHERE conversation_id = ?
                ORDER BY version DESC, created_at DESC, id DESC
                """,
                (conversation_id,),
            ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def save_context_item(self, item: ConversationContextItem) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_context_items(
                    id, conversation_id, kind, source_path, display_name, item_order,
                    enabled, send_policy, storage_policy, snapshot_path, size_bytes,
                    sha256, source_modified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    source_path=excluded.source_path,
                    display_name=excluded.display_name,
                    item_order=excluded.item_order,
                    enabled=excluded.enabled,
                    send_policy=excluded.send_policy,
                    storage_policy=excluded.storage_policy,
                    snapshot_path=excluded.snapshot_path,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    source_modified_at=excluded.source_modified_at
                """,
                (
                    item.id,
                    item.conversation_id,
                    item.kind.value,
                    str(item.source_path),
                    item.display_name,
                    item.order,
                    int(item.enabled),
                    item.send_policy.value,
                    item.storage_policy.value,
                    str(item.snapshot_path) if item.snapshot_path else None,
                    item.size_bytes,
                    item.sha256,
                    item.source_modified_at.isoformat(),
                    item.created_at.isoformat(),
                ),
            )

    def list_context_items(self, conversation_id: str) -> Sequence[ConversationContextItem]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_context_items
                WHERE conversation_id = ?
                ORDER BY item_order, created_at, id
                """,
                (conversation_id,),
            ).fetchall()
        return [self._context_item_from_row(row) for row in rows]

    def delete_context_item(self, item_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM conversation_context_items WHERE id = ?",
                (item_id,),
            )
        return cursor.rowcount > 0

    def search(self, query: str, limit: int = 20) -> Sequence[MemorySearchHit]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        literal_query = f'"{normalized_query.replace(chr(34), chr(34) * 2)}"'
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    record_id,
                    conversation_id,
                    kind,
                    snippet(conversation_search, 3, '[', ']', '…', 20) AS excerpt,
                    bm25(conversation_search) AS rank
                FROM conversation_search
                WHERE conversation_search MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (literal_query, limit),
            ).fetchall()
        return [
            MemorySearchHit(
                record_id=row["record_id"],
                conversation_id=row["conversation_id"],
                kind=row["kind"],
                excerpt=row["excerpt"],
                rank=row["rank"],
            )
            for row in rows
        ]

    def save_configuration(
        self,
        configuration: StoredConfiguration,
        *,
        enqueue_secondary: bool = False,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO stored_configurations(
                    namespace, key, value_json, schema_version, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value_json=excluded.value_json,
                    schema_version=excluded.schema_version,
                    updated_at=excluded.updated_at
                """,
                (
                    configuration.namespace,
                    configuration.key,
                    json.dumps(configuration.value, ensure_ascii=False),
                    configuration.schema_version,
                    configuration.updated_at.isoformat(),
                ),
            )
            if enqueue_secondary:
                self._enqueue_outbox(
                    connection,
                    "configuration",
                    f"{configuration.namespace}:{configuration.key}",
                    {"configuration": configuration.model_dump(mode="json")},
                )

    def get_configuration(self, namespace: str, key: str) -> StoredConfiguration | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM stored_configurations
                WHERE namespace = ? AND key = ?
                """,
                (namespace, key),
            ).fetchone()
        if row is None:
            return None
        return StoredConfiguration(
            namespace=row["namespace"],
            key=row["key"],
            value=json.loads(row["value_json"]),
            schema_version=row["schema_version"],
            updated_at=row["updated_at"],
        )

    def save_execution(
        self,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord] = (),
        *,
        enqueue_secondary: bool = False,
    ) -> None:
        with self._connection() as connection:
            self._upsert_execution(connection, execution, artifacts)
            if enqueue_secondary:
                self._enqueue_outbox(
                    connection,
                    "execution",
                    execution.operation_id,
                    {
                        "execution": execution.model_dump(mode="json"),
                        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
                    },
                )

    def list_executions(self, limit: int = 100) -> Sequence[ExecutionRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("Execution limit must be between 1 and 500")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM executions
                ORDER BY started_at DESC, operation_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._execution_from_row(row) for row in rows]

    def pending_outbox(self, limit: int = 100) -> Sequence[PersistenceOutboxEntry]:
        if not 1 <= limit <= 500:
            raise ValueError("Outbox limit must be between 1 and 500")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM persistence_outbox
                ORDER BY created_at, event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            PersistenceOutboxEntry(
                event_id=row["event_id"],
                entity_kind=row["entity_kind"],
                entity_key=row["entity_key"],
                payload=json.loads(row["payload_json"]),
                attempts=row["attempts"],
            )
            for row in rows
        ]

    def pending_outbox_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) FROM persistence_outbox").fetchone()
        return int(row[0]) if row is not None else 0

    def mark_outbox_synced(self, event_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM persistence_outbox WHERE event_id = ?", (event_id,))

    def mark_outbox_failed(self, event_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE persistence_outbox
                SET attempts = attempts + 1, last_error = ?
                WHERE event_id = ?
                """,
                (error[:1_000], event_id),
            )

    def queue_existing_for_secondary(self) -> int:
        queued = 0
        with self._connection() as connection:
            configurations = connection.execute("SELECT * FROM stored_configurations").fetchall()
            for row in configurations:
                configuration = StoredConfiguration(
                    namespace=row["namespace"],
                    key=row["key"],
                    value=json.loads(row["value_json"]),
                    schema_version=row["schema_version"],
                    updated_at=row["updated_at"],
                )
                self._enqueue_outbox(
                    connection,
                    "configuration",
                    f"{configuration.namespace}:{configuration.key}",
                    {"configuration": configuration.model_dump(mode="json")},
                )
                queued += 1
            executions = connection.execute("SELECT * FROM executions").fetchall()
            for row in executions:
                execution = self._execution_from_row(row)
                artifact_rows = connection.execute(
                    "SELECT * FROM execution_artifacts WHERE operation_id = ?",
                    (execution.operation_id,),
                ).fetchall()
                artifacts = [self._artifact_from_row(artifact) for artifact in artifact_rows]
                self._enqueue_outbox(
                    connection,
                    "execution",
                    execution.operation_id,
                    {
                        "execution": execution.model_dump(mode="json"),
                        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
                    },
                )
                queued += 1
        return queued

    @staticmethod
    def _upsert_execution(
        connection: sqlite3.Connection,
        execution: ExecutionRecord,
        artifacts: Sequence[ArtifactRecord],
    ) -> None:
        connection.execute(
            """
            INSERT INTO executions(
                operation_id, suite, operation_type, status, runtime, model_key,
                started_at, finished_at, request_metadata_json,
                result_metadata_json, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                status=excluded.status,
                runtime=excluded.runtime,
                model_key=excluded.model_key,
                finished_at=excluded.finished_at,
                request_metadata_json=excluded.request_metadata_json,
                result_metadata_json=excluded.result_metadata_json,
                error_message=excluded.error_message
            """,
            (
                execution.operation_id,
                execution.suite,
                execution.operation_type,
                execution.status.value,
                execution.runtime,
                execution.model_key,
                execution.started_at.isoformat(),
                execution.finished_at.isoformat() if execution.finished_at else None,
                json.dumps(execution.request_metadata, ensure_ascii=False),
                json.dumps(execution.result_metadata, ensure_ascii=False),
                execution.error_message,
            ),
        )
        for artifact in artifacts:
            connection.execute(
                """
                INSERT INTO execution_artifacts(
                    artifact_id, operation_id, kind, path, mime_type, size_bytes,
                    sha256, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    kind=excluded.kind,
                    path=excluded.path,
                    mime_type=excluded.mime_type,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    metadata_json=excluded.metadata_json
                """,
                (
                    artifact.artifact_id,
                    artifact.operation_id,
                    artifact.kind,
                    artifact.path,
                    artifact.mime_type,
                    artifact.size_bytes,
                    artifact.sha256,
                    json.dumps(artifact.metadata, ensure_ascii=False),
                    artifact.created_at.isoformat(),
                ),
            )

    @staticmethod
    def _enqueue_outbox(
        connection: sqlite3.Connection,
        entity_kind: str,
        entity_key: str,
        payload: dict[str, object],
    ) -> None:
        event_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"aiopenstudio:{entity_kind}:{entity_key}")
        )
        connection.execute(
            """
            INSERT INTO persistence_outbox(
                event_id, entity_kind, entity_key, payload_json, created_at
            ) VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(event_id) DO UPDATE SET
                payload_json=excluded.payload_json,
                created_at=excluded.created_at,
                attempts=0,
                last_error=NULL
            """,
            (event_id, entity_kind, entity_key, json.dumps(payload, ensure_ascii=False)),
        )

    @staticmethod
    def _execution_from_row(row: sqlite3.Row) -> ExecutionRecord:
        return ExecutionRecord(
            operation_id=row["operation_id"],
            suite=row["suite"],
            operation_type=row["operation_type"],
            status=row["status"],
            runtime=row["runtime"],
            model_key=row["model_key"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            request_metadata=json.loads(row["request_metadata_json"]),
            result_metadata=json.loads(row["result_metadata_json"]),
            error_message=row["error_message"],
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            operation_id=row["operation_id"],
            kind=row["kind"],
            path=row["path"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    @contextmanager
    def _connection(self, *, load_vectors: bool | None = None) -> Iterator[sqlite3.Connection]:
        should_load_vectors = self.enable_vectors if load_vectors is None else load_vectors
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if should_load_vectors:
            try:
                self._load_vector_extension(connection)
            except (ImportError, AttributeError, sqlite3.Error) as error:
                connection.close()
                raise SQLiteCapabilityError("sqlite-vec is unavailable") from error
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _has_fts5(connection: sqlite3.Connection) -> bool:
        try:
            connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(content)")
            connection.execute("DROP TABLE temp.fts5_probe")
        except sqlite3.Error:
            return False
        return True

    @staticmethod
    def _load_vector_extension(connection: sqlite3.Connection) -> None:
        module = cast(_SQLiteVecModule, import_module("sqlite_vec"))
        connection.enable_load_extension(True)
        try:
            module.load(connection)
        finally:
            connection.enable_load_extension(False)

    @staticmethod
    def _replace_search_record(
        connection: sqlite3.Connection,
        record_id: str,
        conversation_id: str,
        kind: str,
        content: str,
    ) -> None:
        connection.execute(
            "DELETE FROM conversation_search WHERE record_id = ?",
            (record_id,),
        )
        connection.execute(
            """
            INSERT INTO conversation_search(record_id, conversation_id, kind, content)
            VALUES (?, ?, ?, ?)
            """,
            (record_id, conversation_id, kind, content),
        )

    @staticmethod
    def _model_from_row(row: sqlite3.Row) -> ModelDescriptor:
        variant = row["variant"] or None
        weights_path = Path(row["weights_path"]) if row["weights_path"] else None
        return ModelDescriptor(
            id=ModelId(runtime=row["runtime"], name=row["name"], variant=variant),
            display_name=row["display_name"],
            capabilities=frozenset(json.loads(row["capabilities_json"])),
            weights_path=weights_path,
            size_bytes=row["size_bytes"],
            checksum_sha256=row["checksum_sha256"],
            installed=bool(row["installed"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            title_origin=row["title_origin"],
            archived_at=row["archived_at"],
            last_model_key=row["last_model_key"],
            remember_context_queue=bool(row["remember_context_queue"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> ConversationMessage:
        return ConversationMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            status=row["status"],
            model_key=row["model_key"],
            operation_id=row["operation_id"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _summary_from_row(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            id=row["id"],
            conversation_id=row["conversation_id"],
            content=row["content"],
            source_message_count=row["source_message_count"],
            version=row["version"],
            first_message_id=row["first_message_id"],
            last_message_id=row["last_message_id"],
            model_key=row["model_key"],
            prompt_sha256=row["prompt_sha256"],
            protected_facts=tuple(json.loads(row["protected_facts_json"])),
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _context_item_from_row(row: sqlite3.Row) -> ConversationContextItem:
        return ConversationContextItem(
            id=row["id"],
            conversation_id=row["conversation_id"],
            kind=row["kind"],
            source_path=Path(row["source_path"]),
            display_name=row["display_name"],
            order=row["item_order"],
            enabled=bool(row["enabled"]),
            send_policy=row["send_policy"],
            storage_policy=row["storage_policy"],
            snapshot_path=Path(row["snapshot_path"]) if row["snapshot_path"] else None,
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            source_modified_at=row["source_modified_at"],
            created_at=row["created_at"],
        )

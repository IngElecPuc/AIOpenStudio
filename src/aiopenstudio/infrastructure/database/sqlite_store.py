"""Local SQLite catalog and conversation memory with FTS5 search."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from aiopenstudio.core.contracts.memory import (
    Conversation,
    ConversationMessage,
    ConversationSummary,
    MemorySearchHit,
)
from aiopenstudio.core.contracts.models import ModelDescriptor, ModelId


class SQLiteCapabilityError(RuntimeError):
    """Raised when an explicitly requested SQLite capability is unavailable."""


@dataclass(frozen=True, slots=True)
class SQLiteCapabilities:
    sqlite_version: str
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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
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
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation
ON conversation_summaries(conversation_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_search USING fts5(
    record_id UNINDEXED,
    conversation_id UNINDEXED,
    kind UNINDEXED,
    content,
    tokenize='unicode61 remove_diacritics 2'
);
"""


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
            connection.executescript(_SCHEMA_SQL)
            connection.execute("PRAGMA user_version = 1")
        return self.capabilities()

    def capabilities(self) -> SQLiteCapabilities:
        with self._connection(load_vectors=False) as connection:
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
            fts5_available=fts5_available,
            extension_loading_available=extension_loading,
            vector_available=vector_version is not None,
            vector_version=vector_version,
        )

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
                INSERT INTO conversations(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    updated_at=excluded.updated_at
                """,
                (
                    conversation.id,
                    conversation.title,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                ),
            )

    def add_message(self, message: ConversationMessage) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_messages(
                    id, conversation_id, role, content, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.conversation_id,
                    message.role.value,
                    message.content,
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

    def save_summary(self, summary: ConversationSummary) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_summaries(
                    id, conversation_id, content, source_message_count, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    summary.id,
                    summary.conversation_id,
                    summary.content,
                    summary.source_message_count,
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

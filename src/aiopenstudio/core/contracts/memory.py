"""Conversation memory contracts independent of the storage engine."""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ConversationTitleOrigin(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class MessageStatus(StrEnum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"
    DRAFT = "draft"


class ContextKind(StrEnum):
    TEXT_FILE = "text_file"
    IMAGE = "image"


class ContextSendPolicy(StrEnum):
    ONCE = "once"
    EVERY_TURN = "every_turn"


class ContextStoragePolicy(StrEnum):
    REFERENCE = "reference"
    SNAPSHOT = "snapshot"


class Conversation(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    title_origin: ConversationTitleOrigin = ConversationTitleOrigin.AUTOMATIC
    archived_at: datetime | None = None
    last_model_key: str | None = None
    remember_context_queue: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    role: MessageRole
    content: str
    status: MessageStatus = MessageStatus.COMPLETE
    model_key: str | None = None
    operation_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationSummary(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    content: str
    source_message_count: int = Field(ge=0)
    version: int = Field(default=1, ge=1)
    first_message_id: str | None = None
    last_message_id: str | None = None
    model_key: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    protected_facts: tuple[str, ...] = ()
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class ConversationContextItem(BaseModel):
    """Persistable reference; copying and payload construction belong to later services."""

    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    kind: ContextKind
    source_path: Path
    display_name: str = Field(min_length=1)
    order: int = Field(default=0, ge=0)
    enabled: bool = True
    send_policy: ContextSendPolicy = ContextSendPolicy.ONCE
    storage_policy: ContextStoragePolicy = ContextStoragePolicy.REFERENCE
    snapshot_path: Path | None = None
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_modified_at: datetime
    created_at: datetime = Field(default_factory=utc_now)


class MemorySearchHit(BaseModel):
    record_id: str
    conversation_id: str
    kind: str
    excerpt: str
    rank: float


class ConversationMemory(Protocol):
    def save_conversation(self, conversation: Conversation) -> None: ...

    def add_message(self, message: ConversationMessage) -> None: ...

    def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    def list_conversations(
        self,
        limit: int = 100,
        *,
        include_archived: bool = False,
        query: str | None = None,
    ) -> Sequence[Conversation]: ...

    def delete_conversation(self, conversation_id: str) -> bool: ...

    def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]: ...

    def save_summary(self, summary: ConversationSummary) -> None: ...

    def list_summaries(self, conversation_id: str) -> Sequence[ConversationSummary]: ...

    def save_context_item(self, item: ConversationContextItem) -> None: ...

    def list_context_items(self, conversation_id: str) -> Sequence[ConversationContextItem]: ...

    def delete_context_item(self, item_id: str) -> bool: ...

    def search(self, query: str, limit: int = 20) -> Sequence[MemorySearchHit]: ...

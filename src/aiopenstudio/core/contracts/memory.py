"""Conversation memory contracts independent of the storage engine."""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Conversation(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationSummary(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    content: str
    source_message_count: int = Field(ge=0)
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

    def list_conversations(self, limit: int = 100) -> Sequence[Conversation]: ...

    def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]: ...

    def save_summary(self, summary: ConversationSummary) -> None: ...

    def search(self, query: str, limit: int = 20) -> Sequence[MemorySearchHit]: ...

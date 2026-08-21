"""Neutral contracts for user-selected external conversation context."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .chat import ChatImage
from .memory import ContextKind, ContextSendPolicy, ConversationContextItem


class ContextItemState(StrEnum):
    READY = "ready"
    CHANGED = "changed"
    MISSING = "missing"
    INVALID = "invalid"


class ContextInspection(BaseModel):
    item: ConversationContextItem
    state: ContextItemState
    preview: str | None = None
    current_size_bytes: int | None = Field(default=None, ge=0)
    current_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    current_modified_at: datetime | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    mime_type: str | None = None
    warning: str | None = None


class PreparedContext(BaseModel):
    item_id: str
    kind: ContextKind
    display_name: str
    send_policy: ContextSendPolicy
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str | None = None
    image: ChatImage | None = None
    size_bytes: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)


class PreparedContextBatch(BaseModel):
    items: tuple[PreparedContext, ...] = ()
    total_bytes: int = Field(default=0, ge=0)
    estimated_tokens: int = Field(default=0, ge=0)

    @property
    def once_item_ids(self) -> tuple[str, ...]:
        return tuple(
            item.item_id
            for item in self.items
            if item.send_policy is ContextSendPolicy.ONCE
        )

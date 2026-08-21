"""Prompt assembly and conservative context-budget contracts."""

from enum import StrEnum

from pydantic import BaseModel, Field

from .chat import ChatInput


class ContextOverflowPolicy(StrEnum):
    REJECT = "reject"
    TRUNCATE_OLDEST = "truncate_oldest"


class TokenBudget(BaseModel):
    context_window: int = Field(ge=128)
    reserved_output_tokens: int = Field(ge=1)
    estimated_input_tokens: int = Field(ge=0)
    estimated_context_tokens: int = Field(default=0, ge=0)
    available_input_tokens: int = Field(ge=0)
    remaining_input_tokens: int
    truncated_message_count: int = Field(default=0, ge=0)
    used_summary_version: int | None = Field(default=None, ge=1)
    estimation_method: str = "unicode_chars_divided_by_four"

    @property
    def fits(self) -> bool:
        return self.estimated_input_tokens <= self.available_input_tokens


class PromptAssembly(BaseModel):
    chat_input: ChatInput
    budget: TokenBudget
    included_context_ids: tuple[str, ...] = ()
    consume_once_context_ids: tuple[str, ...] = ()

"""Backend-neutral contracts for conversational text generation."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .memory import MessageRole


class ChatMessage(BaseModel):
    role: MessageRole
    content: str


class ChatOptions(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=0)
    seed: int | None = None
    context_length: int | None = Field(default=None, ge=128)
    max_new_tokens: int | None = Field(default=None, ge=1)
    stop: tuple[str, ...] = ()

    def runtime_options(self) -> dict[str, Any]:
        """Return only explicitly configured, commonly supported options."""
        values: dict[str, Any] = {}
        mappings = {
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "seed": "seed",
            "context_length": "num_ctx",
            "max_new_tokens": "num_predict",
        }
        for field_name, runtime_name in mappings.items():
            value = getattr(self, field_name)
            if value is not None:
                values[runtime_name] = value
        if self.stop:
            values["stop"] = list(self.stop)
        return values


class ChatInput(BaseModel):
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    options: ChatOptions = Field(default_factory=ChatOptions)
    keep_alive_seconds: float | None = Field(default=600.0, ge=0)
    think: bool | Literal["low", "medium", "high"] | None = None

"""Backend-neutral contracts for conversational text generation."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .memory import MessageRole


class ChatImage(BaseModel):
    path: Path
    mime_type: Literal["image/png", "image/jpeg", "image/bmp"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    images: tuple[ChatImage, ...] = ()


class ChatOptions(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = None
    context_length: int | None = Field(default=None, ge=128)
    max_new_tokens: int | None = Field(default=None, ge=1)
    repeat_penalty: float | None = Field(default=None, ge=0)
    stop: tuple[str, ...] = ()

    def runtime_options(self) -> dict[str, Any]:
        """Return only explicitly configured, commonly supported options."""
        values: dict[str, Any] = {}
        mappings = {
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "min_p": "min_p",
            "seed": "seed",
            "context_length": "num_ctx",
            "max_new_tokens": "num_predict",
            "repeat_penalty": "repeat_penalty",
        }
        for field_name, runtime_name in mappings.items():
            value = getattr(self, field_name)
            if value is not None:
                values[runtime_name] = value
        if self.stop:
            values["stop"] = list(self.stop)
        return values


class ThinkingCapability(StrEnum):
    """Shape of the thinking control verified for one exact model tag."""

    UNAVAILABLE = "unavailable"
    DECLARED = "declared"
    BOOLEAN = "boolean"
    LEVELS = "levels"


class StructuredOutputMode(StrEnum):
    """Portable response formats that a runtime may explicitly support."""

    TEXT = "text"
    JSON = "json"
    JSON_SCHEMA = "json_schema"


class StructuredOutputSpec(BaseModel):
    """Requested output and the JSON Schema sent to capable runtimes."""

    mode: StructuredOutputMode = StructuredOutputMode.TEXT
    json_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_schema_pairing(self) -> "StructuredOutputSpec":
        if self.mode is StructuredOutputMode.JSON_SCHEMA:
            if not self.json_schema:
                raise ValueError("El modo JSON Schema requiere un esquema.")
            if self.json_schema.get("type") not in {"object", "array"}:
                raise ValueError("El esquema raíz debe declarar type object o array.")
        elif self.json_schema is not None:
            raise ValueError("El esquema sólo se usa en el modo JSON Schema.")
        return self


class ModelChatCapabilities(BaseModel):
    """Backend-neutral chat features discovered for one installed model."""

    model_digest: str | None = None
    declared: frozenset[str] = Field(default_factory=frozenset)
    supports_vision: bool = False
    thinking: ThinkingCapability = ThinkingCapability.UNAVAILABLE
    supports_tools: bool = False
    supports_structured_output: bool = False
    max_context_tokens: int | None = Field(default=None, ge=1)
    max_images_per_message: int | None = Field(default=None, ge=1)
    estimated_tokens_per_image: int | None = Field(default=None, ge=1)
    defaults: ChatOptions = Field(default_factory=ChatOptions)


class ChatInput(BaseModel):
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    options: ChatOptions = Field(default_factory=ChatOptions)
    system_prompt: str | None = None
    keep_alive_seconds: float | None = Field(default=600.0, ge=0)
    think: bool | Literal["low", "medium", "high"] | None = None
    output: StructuredOutputSpec = Field(default_factory=StructuredOutputSpec)

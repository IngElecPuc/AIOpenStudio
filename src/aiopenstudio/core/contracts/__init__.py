"""Public contracts shared by services and infrastructure adapters."""

from .catalog import ModelCatalog
from .chat import ChatInput, ChatMessage, ChatOptions
from .memory import (
    Conversation,
    ConversationMemory,
    ConversationMessage,
    ConversationSummary,
    MemorySearchHit,
    MessageRole,
)
from .models import (
    ComputeDevice,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    ResourceSnapshot,
    RuntimeHealth,
)
from .monitoring import ResourceMonitor
from .runtime import (
    InferenceRequest,
    LoadPolicy,
    ModelRuntime,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    UnloadTarget,
)

__all__ = [
    "ChatInput",
    "ChatMessage",
    "ChatOptions",
    "ComputeDevice",
    "Conversation",
    "ConversationMemory",
    "ConversationMessage",
    "ConversationSummary",
    "InferenceRequest",
    "LoadPolicy",
    "MemorySearchHit",
    "MessageRole",
    "ModelCatalog",
    "ModelDescriptor",
    "ModelId",
    "ModelRuntime",
    "ModelState",
    "ProcessState",
    "ResidencyState",
    "ResourceMonitor",
    "ResourceSnapshot",
    "RuntimeCapabilities",
    "RuntimeEvent",
    "RuntimeEventKind",
    "RuntimeHealth",
    "UnloadTarget",
]

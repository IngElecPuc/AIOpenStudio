"""Concrete telemetry providers for local runtimes and hardware."""

from .fooocus import FooocusTelemetryProvider
from .in_process import InProcessTelemetryRegistry
from .nvidia import NvidiaTelemetryProvider
from .ollama import OllamaTelemetryProvider
from .system import SystemTelemetryProvider
from .whisper import WhisperTelemetryProvider

__all__ = [
    "FooocusTelemetryProvider",
    "InProcessTelemetryRegistry",
    "NvidiaTelemetryProvider",
    "OllamaTelemetryProvider",
    "SystemTelemetryProvider",
    "WhisperTelemetryProvider",
]

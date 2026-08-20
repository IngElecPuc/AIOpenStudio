"""Concrete telemetry providers for local runtimes and hardware."""

from .in_process import InProcessTelemetryRegistry
from .nvidia import NvidiaTelemetryProvider
from .ollama import OllamaTelemetryProvider
from .system import SystemTelemetryProvider
from .whisper import WhisperTelemetryProvider

__all__ = [
    "InProcessTelemetryRegistry",
    "NvidiaTelemetryProvider",
    "OllamaTelemetryProvider",
    "SystemTelemetryProvider",
    "WhisperTelemetryProvider",
]

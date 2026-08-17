"""Application errors that do not expose backend-specific exception types."""


class AIOpenStudioError(Exception):
    """Base class for errors safe to present at application boundaries."""


class RuntimeUnavailableError(AIOpenStudioError):
    """Raised when a configured runtime cannot be reached."""


class ModelNotInstalledError(AIOpenStudioError):
    """Raised when an operation names a model absent from the runtime catalog."""


class UnsupportedRuntimeOperationError(AIOpenStudioError):
    """Raised when a runtime cannot honor a backend-neutral operation."""


class RuntimeRequestError(AIOpenStudioError):
    """Raised when a runtime rejects or cannot complete a request."""


class OperationCancelledError(AIOpenStudioError):
    """Raised when an operation is cancelled before it starts streaming."""

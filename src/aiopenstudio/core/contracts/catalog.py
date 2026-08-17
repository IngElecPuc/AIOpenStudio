"""Contracts for model metadata and local weight references."""

from collections.abc import Sequence
from typing import Protocol

from .models import ModelDescriptor, ModelId


class ModelCatalog(Protocol):
    def get(self, model_id: ModelId) -> ModelDescriptor | None: ...

    def list(self, runtime: str | None = None) -> Sequence[ModelDescriptor]: ...

    def save(self, descriptor: ModelDescriptor) -> None: ...

    def remove(self, model_id: ModelId) -> bool: ...

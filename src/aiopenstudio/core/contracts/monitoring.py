"""Resource monitoring contract."""

from collections.abc import AsyncIterator
from typing import Protocol

from .models import ResourceSnapshot


class ResourceMonitor(Protocol):
    async def snapshot(self) -> ResourceSnapshot: ...

    def watch(self, interval_seconds: float = 1.0) -> AsyncIterator[ResourceSnapshot]: ...

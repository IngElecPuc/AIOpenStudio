"""Ordered application startup recovery and bounded clean shutdown."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from aiopenstudio.core.contracts import PostgresConnectionResult

from .persistence import PersistenceService


@dataclass(frozen=True, slots=True)
class ShutdownStep:
    name: str
    close: Callable[[], Awaitable[None]]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    completed: tuple[str, ...]
    failed: tuple[str, ...]


class ApplicationLifecycleService:
    def __init__(
        self,
        persistence: PersistenceService,
        shutdown_steps: Sequence[ShutdownStep],
        *,
        started_at: datetime | None = None,
    ) -> None:
        self._persistence = persistence
        self._shutdown_steps = tuple(shutdown_steps)
        self._started_at = started_at or datetime.now(UTC)
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_result: ShutdownResult | None = None
        self._logger = logging.getLogger("aiopenstudio.lifecycle")

    async def restore_persistence(self) -> PostgresConnectionResult | None:
        result = await self._persistence.reconnect()
        interrupted = await self._persistence.reconcile_interrupted(self._started_at)
        self._logger.info(
            "lifecycle.startup_reconciled",
            extra={"component": "lifecycle", "interrupted_executions": interrupted},
        )
        return result

    async def shutdown(self) -> ShutdownResult:
        async with self._shutdown_lock:
            if self._shutdown_result is not None:
                return self._shutdown_result
            completed: list[str] = []
            failed: list[str] = []
            self._logger.info("lifecycle.shutdown_started", extra={"component": "lifecycle"})
            for step in self._shutdown_steps:
                try:
                    await asyncio.wait_for(step.close(), timeout=step.timeout_seconds)
                except TimeoutError:
                    failed.append(step.name)
                    self._logger.warning(
                        "lifecycle.shutdown_step_timeout",
                        extra={
                            "component": "lifecycle",
                            "shutdown_step": step.name,
                            "timeout_seconds": step.timeout_seconds,
                        },
                    )
                except Exception:
                    failed.append(step.name)
                    self._logger.exception(
                        "lifecycle.shutdown_step_failed",
                        extra={"component": "lifecycle", "shutdown_step": step.name},
                    )
                else:
                    completed.append(step.name)
                    self._logger.info(
                        "lifecycle.shutdown_step_completed",
                        extra={"component": "lifecycle", "shutdown_step": step.name},
                    )
            self._shutdown_result = ShutdownResult(tuple(completed), tuple(failed))
            self._logger.info(
                "lifecycle.shutdown_completed",
                extra={
                    "component": "lifecycle",
                    "completed_steps": completed,
                    "failed_steps": failed,
                },
            )
            return self._shutdown_result

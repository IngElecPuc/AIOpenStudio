import asyncio
from datetime import UTC, datetime

from aiopenstudio.services.lifecycle import ApplicationLifecycleService, ShutdownStep


class _Persistence:
    def __init__(self) -> None:
        self.reconciled_before: datetime | None = None

    async def reconnect(self) -> None:
        return None

    async def reconcile_interrupted(self, started_before: datetime) -> int:
        self.reconciled_before = started_before
        return 2


def test_lifecycle_reconciles_startup_and_closes_in_order() -> None:
    async def scenario() -> None:
        persistence = _Persistence()
        closed: list[str] = []

        async def close(name: str) -> None:
            closed.append(name)

        started_at = datetime.now(UTC)
        service = ApplicationLifecycleService(
            persistence,  # type: ignore[arg-type]
            (
                ShutdownStep("runtime", lambda: close("runtime"), 1),
                ShutdownStep("persistence", lambda: close("persistence"), 1),
            ),
            started_at=started_at,
        )

        assert await service.restore_persistence() is None
        first = await service.shutdown()
        second = await service.shutdown()

        assert persistence.reconciled_before == started_at
        assert closed == ["runtime", "persistence"]
        assert first == second
        assert first.completed == ("runtime", "persistence")
        assert not first.failed

    asyncio.run(scenario())


def test_lifecycle_timeout_does_not_skip_later_steps() -> None:
    async def scenario() -> None:
        closed: list[str] = []

        async def blocked() -> None:
            await asyncio.Event().wait()

        async def final() -> None:
            closed.append("final")

        service = ApplicationLifecycleService(
            _Persistence(),  # type: ignore[arg-type]
            (
                ShutdownStep("blocked", blocked, 0.01),
                ShutdownStep("final", final, 1),
            ),
        )
        result = await service.shutdown()

        assert result.failed == ("blocked",)
        assert result.completed == ("final",)
        assert closed == ["final"]

    asyncio.run(scenario())

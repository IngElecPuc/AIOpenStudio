import asyncio

from aiopenstudio.core.contracts import (
    ComputeDevice,
    LoadPolicy,
    ModelId,
    ModelState,
    ResidencyState,
)
from aiopenstudio.services import DeviceLeaseCoordinator


class FakeMonitor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def snapshot(self) -> object:
        self.calls.append("snapshot")
        return object()

    def suspend_model(self, _: ModelId) -> bool:
        self.calls.append("suspend")
        return True

    def resume_model(self, _: ModelState) -> None:
        self.calls.append("resume")


class FakeLLM:
    def __init__(self, state: ModelState) -> None:
        self.state = state
        self.calls: list[str] = []

    async def active_model_state(self) -> ModelState:
        return self.state

    async def reserve_model(self, _: ModelId) -> None:
        self.calls.append("reserve")

    def release_model_reservation(self, _: ModelId) -> None:
        self.calls.append("release")

    def load_policy(self, _: ModelId) -> LoadPolicy:
        return LoadPolicy(device=ComputeDevice.GPU)

    async def move_model_to_ram(self, _: ModelId) -> ModelState:
        self.calls.append("move-to-ram")
        return self.state.model_copy(update={"active_device": ComputeDevice.CPU})

    async def restore_model_to_device(
        self, _: ModelId, __: LoadPolicy
    ) -> ModelState:
        self.calls.append("restore-gpu")
        return self.state


class FakeTranscription:
    def __init__(self, state: ModelState) -> None:
        self.state = state
        self.calls: list[str] = []

    async def reserve_runtime(self) -> None:
        self.calls.append("reserve")

    def release_runtime_reservation(self) -> None:
        self.calls.append("release")

    async def active_model_state(self) -> ModelState:
        return self.state

    def load_policy(self, _: ModelId) -> LoadPolicy:
        return LoadPolicy(device=ComputeDevice.CPU)

    async def unload_model(self, _: ModelId) -> ModelState:
        self.calls.append("unload")
        return self.state.model_copy(
            update={
                "ram_residency": ResidencyState.UNLOADED,
                "gpu_residency": ResidencyState.UNLOADED,
            }
        )

    async def load_model(self, _: ModelId, __: LoadPolicy) -> ModelState:
        self.calls.append("restore")
        return self.state


def test_device_lease_suspends_and_restores_managed_llm_and_whisper() -> None:
    async def scenario() -> None:
        llm_state = ModelState(
            model=ModelId(runtime="ollama", name="llm"),
            ram_residency=ResidencyState.LOADED,
            gpu_residency=ResidencyState.LOADED,
            active_device=ComputeDevice.GPU,
        )
        whisper_state = ModelState(
            model=ModelId(runtime="faster-whisper", name="small"),
            ram_residency=ResidencyState.LOADED,
            active_device=ComputeDevice.CPU,
        )
        monitor = FakeMonitor()
        llm = FakeLLM(llm_state)
        whisper = FakeTranscription(whisper_state)
        coordinator = DeviceLeaseCoordinator(
            monitor,  # type: ignore[arg-type]
            llm=llm,  # type: ignore[arg-type]
            transcription=whisper,  # type: ignore[arg-type]
        )

        async with coordinator.lease(ModelId(runtime="fooocus", name="image")):
            assert llm.calls == ["reserve", "move-to-ram"]
            assert whisper.calls == ["reserve", "unload"]

        assert llm.calls == ["reserve", "move-to-ram", "restore-gpu", "release"]
        assert whisper.calls == ["reserve", "unload", "restore", "release"]
        assert monitor.calls.count("snapshot") == 2
        assert monitor.calls[-2:] == ["resume", "snapshot"]

    asyncio.run(scenario())

from aiopenstudio.core.contracts import (
    ComputeDevice,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeHealth,
)


def test_process_ram_and_gpu_states_are_independent() -> None:
    state = ModelState(
        model=ModelId(runtime="ollama", name="example"),
        runtime_health=RuntimeHealth.READY,
        process_state=ProcessState.RUNNING,
        ram_residency=ResidencyState.LOADED,
        gpu_residency=ResidencyState.UNLOADED,
        active_device=ComputeDevice.CPU,
        ram_bytes=2_000,
    )

    assert state.process_state is ProcessState.RUNNING
    assert state.loaded_in_ram is True
    assert state.loaded_in_gpu is False
    assert state.active_device is ComputeDevice.CPU


def test_model_identifier_has_stable_storage_key() -> None:
    assert ModelId(runtime="whisper", name="small", variant="es").key == "whisper:small:es"
    assert ModelId(runtime="ollama", name="qwen").key == "ollama:qwen:"

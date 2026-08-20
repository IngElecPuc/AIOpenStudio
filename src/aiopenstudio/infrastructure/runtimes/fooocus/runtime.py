"""Supervised Fooocus runtime with a disposable process boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from aiopenstudio.core.contracts import (
    ComputeDevice,
    ImageGenerationEvent,
    ImageGenerationEventKind,
    ImageGenerationRequest,
    ImageGenerationRuntime,
    LoadPolicy,
    ModelDescriptor,
    ModelId,
    ModelState,
    ProcessState,
    ResidencyState,
    RuntimeCapabilities,
    RuntimeHealth,
    UnloadTarget,
)
from aiopenstudio.core.errors import (
    ModelNotInstalledError,
    RuntimeRequestError,
    RuntimeUnavailableError,
)

from .process import FooocusProcessSupervisor
from .transport import FooocusTransport

_RUNTIME_NAME = "fooocus"


class FooocusRuntime(ImageGenerationRuntime):
    def __init__(
        self,
        supervisor: FooocusProcessSupervisor,
        transport: FooocusTransport,
        *,
        cancel_grace_seconds: float = 3.0,
        worker_watchdog_seconds: float = 1.0,
    ) -> None:
        self._supervisor = supervisor
        self._transport = transport
        self._cancel_grace_seconds = cancel_grace_seconds
        self._worker_watchdog_seconds = worker_watchdog_seconds
        self._loaded_model: ModelId | None = None
        self._load_policy: LoadPolicy | None = None
        self._active_operation: str | None = None
        self._forced_cancelled: set[str] = set()

    @property
    def name(self) -> str:
        return _RUNTIME_NAME

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            manages_process=True,
            supports_device_selection=True,
            supports_streaming=True,
            supports_cancellation=True,
        )

    @property
    def process_id(self) -> int | None:
        return self._supervisor.process_id

    @property
    def recent_logs(self) -> tuple[str, ...]:
        return self._supervisor.recent_logs

    def preflight(self) -> tuple[str, ...]:
        return self._supervisor.preflight() + self._transport.preflight()

    async def health(self) -> RuntimeHealth:
        if self.preflight():
            return RuntimeHealth.UNAVAILABLE
        if not self._supervisor.running:
            return RuntimeHealth.READY
        return RuntimeHealth.READY if await self._transport.health() else RuntimeHealth.STARTING

    async def process_state(self) -> ProcessState:
        if self._supervisor.running:
            return ProcessState.RUNNING
        return ProcessState.STOPPED if not self.preflight() else ProcessState.FAILED

    async def start(self) -> ProcessState:
        await self._supervisor.start()
        deadline = (
            asyncio.get_running_loop().time()
            + self._supervisor.settings.startup_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            if not self._supervisor.running:
                detail = "\n".join(self.recent_logs[-10:])
                raise RuntimeUnavailableError(f"Fooocus terminó durante el arranque. {detail}")
            if await self._transport.health():
                return ProcessState.RUNNING
            await asyncio.sleep(0.5)
        await self._supervisor.stop()
        raise RuntimeUnavailableError("Fooocus no respondió antes del timeout de arranque.")

    async def stop(self) -> ProcessState:
        await self._transport.close()
        await self._supervisor.stop()
        self._loaded_model = None
        self._load_policy = None
        self._active_operation = None
        return ProcessState.STOPPED

    async def close(self) -> None:
        await self.stop()

    async def list_models(self) -> Sequence[ModelDescriptor]:
        descriptors: list[ModelDescriptor] = []
        root = self._supervisor.settings.models_root / "checkpoints"
        if not root.is_dir():
            return ()
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in {".safetensors", ".ckpt"}:
                continue
            descriptors.append(
                ModelDescriptor(
                    id=ModelId(runtime=self.name, name=path.name, variant=path.stem),
                    display_name=path.stem,
                    weights_path=path.resolve(),
                    size_bytes=path.stat().st_size,
                    installed=True,
                    capabilities=frozenset({"text-to-image"}),
                    metadata={"backend": "fooocus", "local_only": True},
                )
            )
        return tuple(descriptors)

    async def list_styles(self) -> Sequence[str]:
        if not self._supervisor.running:
            return ("Fooocus V2",)
        styles = tuple(await self._transport.list_styles())
        return styles or ("Fooocus V2",)

    async def load(self, model: ModelId, policy: LoadPolicy) -> ModelState:
        descriptor = await self._descriptor(model)
        if descriptor is None:
            raise ModelNotInstalledError(f"El checkpoint Fooocus {model.name!r} no está instalado.")
        if policy.device is ComputeDevice.CPU:
            raise RuntimeRequestError("La primera vertical Fooocus requiere GPU.")
        if self._active_operation is not None:
            raise RuntimeRequestError("No se puede cambiar el checkpoint durante una generación.")
        if self._loaded_model is not None and self._loaded_model != model:
            await self.stop()
        self._supervisor.select_checkpoint(model.name)
        await self.start()
        self._loaded_model = model
        self._load_policy = policy
        return await self.state(model)

    async def unload(
        self, model: ModelId, target: UnloadTarget = UnloadTarget.ALL
    ) -> ModelState:
        del target
        if self._active_operation is not None:
            raise RuntimeRequestError("Cancela la generación antes de liberar Fooocus.")
        if self._loaded_model is not None and self._loaded_model != model:
            raise RuntimeRequestError(f"El checkpoint activo es {self._loaded_model.name!r}.")
        await self.stop()
        return await self.state(model)

    async def state(self, model: ModelId) -> ModelState:
        health = await self.health()
        process = await self.process_state()
        loaded = self._loaded_model == model and self._supervisor.running
        return ModelState(
            model=model,
            runtime_health=health,
            process_state=process,
            ram_residency=ResidencyState.LOADED if loaded else ResidencyState.UNLOADED,
            gpu_residency=ResidencyState.LOADED if loaded else ResidencyState.UNLOADED,
            active_device=ComputeDevice.GPU if loaded else None,
            pinned_in_ram=bool(self._load_policy and self._load_policy.pin_in_ram and loaded),
            pinned_on_device=bool(
                self._load_policy and self._load_policy.pin_on_device and loaded
            ),
            detail=(
                "Proceso Fooocus administrado; uso físico de VRAM medido por NVML."
                if loaded
                else "Checkpoint local disponible, pero Fooocus no está residente."
            ),
        )

    async def generate(
        self, request: ImageGenerationRequest
    ) -> AsyncIterator[ImageGenerationEvent]:
        if self._loaded_model != request.model or not self._supervisor.running:
            raise RuntimeRequestError("Carga el checkpoint Fooocus antes de generar.")
        if self._active_operation is not None:
            raise RuntimeRequestError("Fooocus ya tiene una generación activa.")
        self._active_operation = request.operation_id
        log_offset = len(self.recent_logs)
        iterator = self._transport.generate(request).__aiter__()
        next_event: asyncio.Future[ImageGenerationEvent] | None = asyncio.ensure_future(
            anext(iterator)
        )
        cancelled_emitted = False
        try:
            while next_event is not None:
                done, _ = await asyncio.wait(
                    {next_event}, timeout=self._worker_watchdog_seconds
                )
                if not done:
                    failure = self._worker_failure_detail(self.recent_logs[log_offset:])
                    if failure is not None:
                        await self._transport.cancel(request.operation_id)
                        await self._supervisor.stop()
                        self._loaded_model = None
                        self._load_policy = None
                        raise RuntimeRequestError(
                            "El worker interno de Fooocus terminó durante la generación.\n"
                            f"{failure}"
                        )
                    if not self._supervisor.running:
                        raise RuntimeRequestError(
                            "El proceso Fooocus terminó durante la generación."
                        )
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    next_event = None
                    break
                if event.kind is ImageGenerationEventKind.CANCELLED:
                    cancelled_emitted = True
                yield event
                next_event = asyncio.ensure_future(anext(iterator))
            if request.operation_id in self._forced_cancelled and not cancelled_emitted:
                yield ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.CANCELLED,
                )
        except Exception as error:
            if request.operation_id in self._forced_cancelled and not cancelled_emitted:
                yield ImageGenerationEvent(
                    operation_id=request.operation_id,
                    kind=ImageGenerationEventKind.CANCELLED,
                )
            else:
                logs = "\n".join(self.recent_logs[-20:])
                detail = str(error)
                if logs:
                    detail = f"{detail}\nÚltima salida de Fooocus:\n{logs}"
                raise RuntimeRequestError(detail) from error
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
            self._active_operation = None
            self._forced_cancelled.discard(request.operation_id)

    async def cancel(self, operation_id: str) -> None:
        if self._active_operation != operation_id:
            return
        self._forced_cancelled.add(operation_id)
        await self._transport.cancel(operation_id)
        await asyncio.sleep(self._cancel_grace_seconds)
        if self._active_operation == operation_id:
            await self._supervisor.stop()
            self._loaded_model = None
            self._load_policy = None

    async def _descriptor(self, model: ModelId) -> ModelDescriptor | None:
        if model.runtime != self.name:
            return None
        return next((item for item in await self.list_models() if item.id == model), None)

    @staticmethod
    def _worker_failure_detail(logs: Sequence[str]) -> str | None:
        for index, line in enumerate(logs):
            if line.startswith("Exception in thread"):
                return "\n".join(logs[index:][-30:])
        return None
